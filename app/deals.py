"""Mistake-fare deal feeds (Secret Flying, The Flight Deal, etc.).

Pulls RSS, extracts price + origin city + destination city via regex on the
title (these sites have consistent title patterns like "[Boston to Tokyo from
$398 roundtrip]"). Falls back to gracefully empty results if a feed is down.

Cached in memory for `_CACHE_TTL` seconds — we don't need fresh-per-second
data, deals are posted at most every few hours.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import feedparser
import httpx

from .airports import lookup as airport_lookup

log = logging.getLogger("fcf.deals")

_AIRPORTS_PATH = Path(__file__).resolve().parent.parent / "data" / "airports.json"
_AIRPORTS_BY_CITY: dict[str, list] | None = None  # city.lower() -> [airport, ...]
_CACHE: list[dict] | None = None
_CACHE_TS: float = 0.0
_CACHE_TTL = 30 * 60  # 30 min
_LOCK = threading.Lock()

FEEDS = [
    ("Secret Flying", "https://www.secretflying.com/feed/"),
    ("The Flight Deal", "https://www.theflightdeal.com/feed/"),
    ("Going (Scott's)", "https://www.going.com/feed"),  # may 404, falls through
]

# Title patterns seen in the wild:
#   "Cathay Pacific: San Francisco – Haikou, China. $853. Roundtrip..."
#   "American: Los Angeles – Key West, Florida (and vice versa). $276..."
#   "Boston to Tokyo, Japan from $398 roundtrip"
#   "Cheap flights from NYC to Paris from $250"
# Separator can be: en/em-dash, hyphen, arrow, "to". Optional airline prefix
# "<Airline>:" is stripped first.
_AIRLINE_PREFIX_RE = re.compile(r"^[A-Z][\w &.'/-]+:\s*")
_TITLE_RE = re.compile(
    r"(?:cheap\s+flights?\s+from\s+)?"
    r"(?P<orig>[A-Z][A-Za-z .'’-]+(?:\s*,\s*[A-Z][A-Za-z .'’-]+)?)\s*"
    r"(?:–|—|->|→|-|\bto\b)\s*"
    r"(?P<dest>[A-Z][A-Za-z .'’-]+(?:\s*,\s*[A-Z][A-Za-z .'’-]+)?)"
    r".*?\$\s*(?P<price>[\d,]+)",
    re.IGNORECASE | re.DOTALL,
)


def _build_city_index():
    global _AIRPORTS_BY_CITY
    if _AIRPORTS_BY_CITY is not None:
        return
    idx: dict[str, list] = {}
    rows = json.loads(_AIRPORTS_PATH.read_text(encoding="utf-8"))
    for a in rows:
        idx.setdefault(a[1].lower(), []).append(a)
        # secondary index by name fragments so "NYC" matches "John F Kennedy"
        for tok in a[3].lower().split():
            if len(tok) > 3:
                idx.setdefault(tok, []).append(a)
    _AIRPORTS_BY_CITY = idx


def _city_to_iata(city_text: str) -> Optional[dict]:
    """Best-effort: map a city/region string from a deal title to an airport.

    Handles "Boston", "New York City", "NYC", "London, UK", "Paris, France".
    """
    _build_city_index()
    s = city_text.strip().lower()
    # quick aliases for common shorthand
    aliases = {
        "nyc": "new york", "lax": "los angeles", "sfo": "san francisco",
        "dc": "washington", "uk": "london", "us": "new york",
    }
    s = aliases.get(s, s)
    # strip ", country" tail
    head = s.split(",")[0].strip()
    for key in (s, head):
        hits = _AIRPORTS_BY_CITY.get(key)  # type: ignore
        if hits:
            # prefer the entry whose airport name contains "International"
            big = [h for h in hits if "international" in h[3].lower()]
            chosen = (big or hits)[0]
            return {"iata": chosen[0], "city": chosen[1],
                    "country": chosen[2], "name": chosen[3]}
    return None


@dataclass
class Deal:
    source: str
    title: str
    link: str
    published: str
    price_usd: Optional[float]
    origin_text: Optional[str]
    destination_text: Optional[str]
    origin_iata: Optional[str]
    destination_iata: Optional[str]


def _parse_entry(source: str, entry) -> Optional[Deal]:
    title = (entry.get("title") or "").strip()
    link = entry.get("link") or ""
    published = entry.get("published") or entry.get("updated") or ""
    if not title:
        return None
    # Strip a leading "<Airline>:" if present.
    clean = _AIRLINE_PREFIX_RE.sub("", title)
    m = _TITLE_RE.search(clean)
    if not m:
        return Deal(source=source, title=title, link=link, published=published,
                    price_usd=None, origin_text=None, destination_text=None,
                    origin_iata=None, destination_iata=None)
    origin_text = m.group("orig").strip()
    destination_text = m.group("dest").strip()
    try:
        price = float(m.group("price").replace(",", ""))
    except ValueError:
        price = None
    o = _city_to_iata(origin_text)
    d = _city_to_iata(destination_text)
    return Deal(
        source=source, title=title, link=link, published=published,
        price_usd=price,
        origin_text=origin_text, destination_text=destination_text,
        origin_iata=o["iata"] if o else None,
        destination_iata=d["iata"] if d else None,
    )


def _fetch_all() -> list[dict]:
    out: list[dict] = []
    for source, url in FEEDS:
        try:
            r = httpx.get(url, timeout=10.0,
                          headers={"User-Agent": "Mozilla/5.0 FindCheapFlights"},
                          follow_redirects=True)
            if r.status_code != 200:
                log.info(f"deal feed {source} returned {r.status_code}")
                continue
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:30]:
                d = _parse_entry(source, entry)
                if d:
                    out.append(asdict(d))
        except Exception as e:
            log.warning(f"deal feed {source} failed: {e}")
    return out


def fetch_deals(force: bool = False) -> list[dict]:
    """Return cached deals (or refresh if expired)."""
    global _CACHE, _CACHE_TS
    with _LOCK:
        now = time.time()
        if not force and _CACHE is not None and (now - _CACHE_TS) < _CACHE_TTL:
            return _CACHE
        _CACHE = _fetch_all()
        _CACHE_TS = now
        log.info(f"refreshed deals cache: {len(_CACHE)} items")
        return _CACHE


def filter_deals(deals: list[dict], *, origin: Optional[str] = None,
                 destination: Optional[str] = None,
                 origin_country: Optional[str] = None) -> list[dict]:
    """Filter deals by origin/destination IATA or country."""
    if not (origin or destination or origin_country):
        return deals
    out = []
    for d in deals:
        if origin and d.get("origin_iata") != origin:
            continue
        if destination and d.get("destination_iata") != destination:
            continue
        if origin_country:
            iata = d.get("origin_iata")
            if not iata:
                continue
            a = airport_lookup(iata)
            if not a or a[2] != origin_country:
                continue
        out.append(d)
    return out
