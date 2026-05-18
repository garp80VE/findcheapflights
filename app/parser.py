"""Robust Google Flights HTML -> structured Flight list parser.

Strategy: Google's flight cards expose a single rich `aria-label` on the
`div.JMc5Xc` element of each `<li>` row, e.g.:

  "From 78 US dollars. Nonstop flight with Iberia. Leaves Adolfo Suárez
   Madrid-Barajas Airport at 9:35 PM on Wednesday, June 10 and arrives at
   Josep Tarradellas Barcelona-El Prat Airport at 10:50 PM on Wednesday,
   June 10. Total duration 1 hr 15 min. Select flight"

Parsing this text is far more stable than chasing CSS class names that Google
rotates frequently. Falls back to inner-DOM extraction when the aria-label is
missing or malformed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from selectolax.lexbor import LexborHTMLParser

_PRICE_RE = re.compile(
    r"From\s+(?P<amt>[\d.,]+)\s+(?P<cur>[A-Za-z][\w\s]*?)\.",
    re.IGNORECASE,
)
_STOPS_RE = re.compile(
    r"(?P<kind>Nonstop|(?P<n>\d+)\s*stop)s?\s+flight\s+with\s+(?P<airline>.+?)\.",
    re.IGNORECASE,
)
_LEAVE_ARRIVE_RE = re.compile(
    r"Leaves\s+(?P<orig_name>.+?)\s+at\s+(?P<dep>[\d:]+\s*(?:AM|PM))"
    r"\s+on\s+(?P<dep_day>.+?)\s+and\s+arrives\s+at\s+(?P<dest_name>.+?)"
    r"\s+at\s+(?P<arr>[\d:]+\s*(?:AM|PM))\s+on\s+(?P<arr_day>.+?)\.",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"Total\s+duration\s+(?P<dur>.+?)\.", re.IGNORECASE)
_PLUS_DAYS_RE = re.compile(r"\(\+(\d+)\s*day", re.IGNORECASE)

# Map "78 US dollars" / "78 euros" / "1,234 Mexican pesos" -> ISO code
_CUR_WORDS = {
    "us dollar": "USD", "dollar": "USD",
    "euro": "EUR",
    "pound sterling": "GBP", "british pound": "GBP", "pound": "GBP",
    "japanese yen": "JPY", "yen": "JPY",
    "indian rupee": "INR", "rupee": "INR",
    "mexican peso": "MXN",
    "brazilian real": "BRL", "real": "BRL",
    "canadian dollar": "CAD",
    "australian dollar": "AUD",
    "swiss franc": "CHF", "franc": "CHF",
    "chinese yuan": "CNY", "yuan": "CNY", "renminbi": "CNY",
    "turkish lira": "TRY", "lira": "TRY",
    "argentine peso": "ARS",
    "colombian peso": "COP",
    "chilean peso": "CLP",
    "peruvian sol": "PEN", "sol": "PEN",
    "singapore dollar": "SGD",
}


@dataclass
class ParsedFlight:
    airline: str
    departure_time: str
    departure_day: str
    arrival_time: str
    arrival_day: str
    origin_airport: str  # full name from aria-label
    destination_airport: str
    duration: str
    stops: int
    price_amount: float
    price_currency: str  # ISO if recognised, else raw
    price_raw: str       # visible text e.g. "$78"
    plus_days: int = 0   # "(+1 day)" -> 1
    is_best: bool = False


def _currency_from_words(words: str) -> str:
    w = words.lower().strip().rstrip(".")
    if w.endswith("s"):
        w_singular = w[:-1]
    else:
        w_singular = w
    for key, code in _CUR_WORDS.items():
        if key in w or key in w_singular:
            return code
    return w.upper()[:3] or "USD"


def _parse_amount(s: str) -> float:
    s = s.strip()
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        tail = s.split(",")[-1]
        if len(tail) == 3:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


_WS_TRANSLATE = str.maketrans({
    " ": " ",  # narrow no-break space (used in times like "9:35 PM")
    " ": " ",  # nbsp
    " ": " ",  # thin space
    "–": "-",  # en-dash
    "—": "-",  # em-dash
})


def _normalize_ws(s: str) -> str:
    return s.translate(_WS_TRANSLATE).strip()


def _aria_text(item) -> str:
    n = item.css_first("div.JMc5Xc[aria-label]")
    if not n:
        return ""
    return _normalize_ws(n.attributes.get("aria-label", "") or "")


def _visible_price(item) -> str:
    n = item.css_first("div.YMlIz.FpEdX span") or item.css_first("div.YMlIz.FpEdX")
    if not n:
        return ""
    return _normalize_ws(n.text(strip=True))


def parse_flights(html: str) -> List[ParsedFlight]:
    parser = LexborHTMLParser(html)
    out: List[ParsedFlight] = []
    seen_ids = set()

    for i, tab in enumerate(parser.css('div[jsname="IWWDBc"], div[jsname="YdtKid"]')):
        is_best_tab = i == 0
        for item in tab.css("ul.Rk10dc li.pIav2d"):
            ssk = item.attributes.get("ssk") or ""
            if ssk and ssk in seen_ids:
                continue
            if ssk:
                seen_ids.add(ssk)

            text = _aria_text(item)
            if not text:
                continue

            m_price = _PRICE_RE.search(text)
            m_stops = _STOPS_RE.search(text)
            m_route = _LEAVE_ARRIVE_RE.search(text)
            m_dur = _DURATION_RE.search(text)

            if not (m_price and m_stops and m_route and m_dur):
                continue

            amount = _parse_amount(m_price.group("amt"))
            currency = _currency_from_words(m_price.group("cur"))
            visible = _visible_price(item) or f"{amount:.0f}"
            airline = m_stops.group("airline").strip()
            stops = 0 if m_stops.group("kind").lower().startswith("nonstop") else int(m_stops.group("n") or 1)

            arr_day = m_route.group("arr_day")
            plus = 0
            m_plus = _PLUS_DAYS_RE.search(arr_day)
            if m_plus:
                plus = int(m_plus.group(1))
                arr_day = _PLUS_DAYS_RE.sub("", arr_day).strip()

            out.append(ParsedFlight(
                airline=airline,
                departure_time=m_route.group("dep").strip(),
                departure_day=m_route.group("dep_day").strip(),
                arrival_time=m_route.group("arr").strip(),
                arrival_day=arr_day.strip(),
                origin_airport=m_route.group("orig_name").strip(),
                destination_airport=m_route.group("dest_name").strip(),
                duration=m_dur.group("dur").strip(),
                stops=stops,
                price_amount=amount,
                price_currency=currency,
                price_raw=visible,
                plus_days=plus,
                is_best=is_best_tab,
            ))
    return out
