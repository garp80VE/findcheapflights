"""Self-transfer (virtual interlining) search.

When no single-ticket itinerary exists for A->B (typical for small origin
airports like VLC->CCS), combine two SEPARATELY-ticketed legs through a hub:

    A -> HUB   (leg 1, one-way)
    HUB -> B   (leg 2, one-way, same day or +1 day)

This is what Kiwi.com sells as "self-transfer". It's often dramatically
cheaper and opens routes that don't exist as a single fare — but the legs are
independent tickets: if leg 1 is delayed/cancelled you miss leg 2 with no
airline protection and no automatic rebooking. We label this loudly.

We don't have precise per-flight times from the cheap-probe, so we surface the
cheapest leg-1 + cheapest leg-2 (same-day and +1-day variants) and tell the
user to verify the layover is feasible themselves. A self-transfer with a
generous overnight layover is the safe way to use this.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional

from .airlines import google_flights_market_url
from .airports import nearest, lookup as airport_lookup
from .search import cheapest_for

log = logging.getLogger("fcf.selftransfer")

# Curated global gateways that interline most long-haul markets. Used in
# addition to airports geographically near the origin.
_GLOBAL_GATEWAYS = [
    "MAD", "BCN", "LIS", "CDG", "AMS", "FRA", "LHR", "MUC", "IST",
    "MIA", "JFK", "ATL", "BOG", "PTY", "GRU", "MEX", "LIM",
    "DXB", "DOH", "IST", "CCS",
]


def _hub_candidates(origin: str, destination: str) -> list[str]:
    """Hubs to try as the connecting point: airports near the origin (so the
    first leg is a short hop) plus global gateways. De-duplicated, excludes
    origin/destination themselves."""
    near = [a["iata"] for a in nearest(origin, n=4, max_km=700)]
    seen, out = set(), []
    for code in near + _GLOBAL_GATEWAYS:
        if code in (origin, destination) or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out[:14]  # bound the probe budget


def find_self_transfer(*, origin: str, destination: str, depart_date: str,
                       return_date: Optional[str], adults: int, children: int,
                       infants: int, max_stops: Optional[int] = None,
                       max_results: int = 5) -> list[dict]:
    """Return self-transfer itineraries sorted by total one-way price.

    Each: {hub, hub_city, leg1_usd, leg2_usd, leg2_when, total_usd}.
    Round-trip is intentionally probed one-way only — self-transfer return
    tickets compound the missed-connection risk; we surface the outbound and
    let the user mirror it manually for the return.
    """
    hubs = _hub_candidates(origin, destination)
    if not hubs:
        return []

    base_dep = date.fromisoformat(depart_date)
    next_day = (base_dep + timedelta(days=1)).isoformat()

    # Probe leg 1 (origin->hub, depart day) and leg 2 (hub->dest, depart day
    # AND next day) for every hub, all in parallel.
    jobs = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for hub in hubs:
            jobs[ex.submit(cheapest_for, origin, hub, depart_date, None,
                           adults, children, infants, max_stops)] = ("L1", hub)
            jobs[ex.submit(cheapest_for, hub, destination, depart_date, None,
                           adults, children, infants, max_stops)] = ("L2same", hub)
            jobs[ex.submit(cheapest_for, hub, destination, next_day, None,
                           adults, children, infants, max_stops)] = ("L2next", hub)

        leg1: dict[str, float] = {}
        leg2_same: dict[str, float] = {}
        leg2_next: dict[str, float] = {}
        for fut in as_completed(jobs):
            kind, hub = jobs[fut]
            price = fut.result()
            if price is None:
                continue
            if kind == "L1":
                leg1[hub] = price
            elif kind == "L2same":
                leg2_same[hub] = price
            else:
                leg2_next[hub] = price

    out = []
    for hub in hubs:
        if hub not in leg1:
            continue
        opts = []
        if hub in leg2_same:
            opts.append(("mismo día", leg2_same[hub]))
        if hub in leg2_next:
            opts.append(("día siguiente (escala nocturna)", leg2_next[hub]))
        if not opts:
            continue
        when, leg2_price = min(opts, key=lambda x: x[1])
        leg2_date = depart_date if when == "mismo día" else next_day
        a = airport_lookup(hub)
        out.append({
            "hub": hub,
            "hub_city": a[1] if a else hub,
            "hub_country": a[2] if a else "",
            "leg1": f"{origin}-{hub}",
            "leg1_usd": round(leg1[hub], 2),
            "leg1_date": depart_date,
            "leg1_link": google_flights_market_url(
                origin, hub, depart_date, None, adults, children, infants),
            "leg2": f"{hub}-{destination}",
            "leg2_usd": round(leg2_price, 2),
            "leg2_date": leg2_date,
            "leg2_when": when,
            "leg2_link": google_flights_market_url(
                hub, destination, leg2_date, None, adults, children, infants),
            "total_usd": round(leg1[hub] + leg2_price, 2),
        })
    out.sort(key=lambda x: x["total_usd"])
    return out[:max_results]
