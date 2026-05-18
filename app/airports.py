"""Airport coordinates lookup and nearest-airport queries."""
from __future__ import annotations

import json
from math import radians, sin, cos, asin, sqrt
from pathlib import Path
from typing import Optional

_DATA = Path(__file__).resolve().parent.parent / "data" / "airports.json"
_AIRPORTS: list | None = None
_BY_IATA: dict | None = None


def _load() -> tuple[list, dict]:
    global _AIRPORTS, _BY_IATA
    if _AIRPORTS is None:
        _AIRPORTS = json.loads(_DATA.read_text(encoding="utf-8"))
        _BY_IATA = {a[0]: a for a in _AIRPORTS}
    return _AIRPORTS, _BY_IATA  # type: ignore


def lookup(iata: str) -> Optional[list]:
    """Return [iata, city, country, name, lat, lon] or None."""
    _, by = _load()
    return by.get(iata.upper())


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in km."""
    R = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * asin(sqrt(a))


def nearest(iata: str, *, n: int = 2, max_km: float = 500.0,
            min_km: float = 25.0) -> list[dict]:
    """N nearest airports to `iata` (excluding itself).

    Returns list of {iata, city, country, name, distance_km} sorted by distance.
    `min_km` filters out same-city secondary airports (e.g. JFK vs LGA — JFK is
    fine, but JFK vs satellite GA strips); set to 0 to include them.
    """
    src = lookup(iata)
    if not src:
        return []
    _, lat, lon = src[0], src[4], src[5]
    out = []
    airports, _ = _load()
    for a in airports:
        if a[0] == src[0]:
            continue
        d = haversine_km(lat, lon, a[4], a[5])
        if d < min_km or d > max_km:
            continue
        out.append((d, a))
    out.sort(key=lambda x: x[0])
    return [
        {
            "iata": a[0], "city": a[1], "country": a[2], "name": a[3],
            "distance_km": round(d, 0),
        }
        for d, a in out[:n]
    ]
