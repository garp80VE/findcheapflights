"""Cazador — multi-airport composite search and hidden-city candidate detection.

These are the techniques deal-hunting communities use that vanilla aggregators
don't surface:

1. composite_search(): try N nearby origin/destination airport combinations in
   parallel, sorted by total price. Often a 200 km drive to a hub saves $300+.

2. hidden_city_candidates(): probe popular "onward" cities past the user's
   destination. If A→C connects at B (the real destination) and is cheaper
   than direct A→B, the user can book A→C and walk off at B (skiplagging).
   We surface the price gap; user verifies the connection on the airline page.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .airports import nearest, lookup as airport_lookup, haversine_km
from .search import cheapest_for

log = logging.getLogger("fcf.hunter")


# Popular "onward" cities from major hubs — used as hidden-city candidates.
# When the user searches A -> {hub in this dict}, we also try A -> {onwards}
# and check if the price is lower (suggesting B is just a layover).
_ONWARD_HUBS = {
    # Europe
    "MAD": ["LIS", "PMI", "OPO", "AGP", "TFS", "VLC"],
    "BCN": ["PMI", "IBZ", "VLC", "AGP", "OPO"],
    "LHR": ["EDI", "GLA", "DUB", "MAN", "BFS"],
    "CDG": ["NCE", "MRS", "TLS", "BOD", "LYS"],
    "AMS": ["EIN", "RTM", "GRQ"],
    "FRA": ["MUC", "HAM", "TXL", "STR"],
    "FCO": ["MXP", "VCE", "NAP", "CTA"],
    "MUC": ["FRA", "HAM", "TXL", "STR"],
    # Americas
    "JFK": ["BOS", "BWI", "DCA", "BUF", "PIT"],
    "LAX": ["LAS", "PHX", "SLC", "SAN", "OAK"],
    "MIA": ["MCO", "TPA", "FLL", "JAX"],
    "ATL": ["BHM", "CHA", "MCO", "MEM"],
    "ORD": ["MKE", "DTW", "IND", "STL"],
    "GRU": ["VCP", "GIG", "BSB", "POA"],
    "MEX": ["GDL", "MTY", "CUN", "MID"],
    "BOG": ["MDE", "CTG", "BAQ", "CLO"],
    "LIM": ["AQP", "CUZ", "TRU", "PIU"],
    "SCL": ["CCP", "PMC", "IPC"],
    # Asia
    "NRT": ["KIX", "ITM", "FUK", "SDJ"],
    "ICN": ["GMP", "PUS", "CJU"],
    "DXB": ["AUH", "DOH", "RUH", "MCT"],
    "SIN": ["KUL", "DPS", "CGK", "BKK"],
}


def composite_search(*, origin: str, destination: str, depart_date: str,
                     return_date: Optional[str], adults: int, children: int,
                     infants: int, max_stops: Optional[int] = None,
                     primary_total_usd: Optional[float] = None,
                     n_neighbors: int = 3,
                     max_km: float = 400.0) -> list[dict]:
    """Probe the N closest origin and destination airports (and combinations)
    in parallel. Return only options strictly cheaper than `primary_total_usd`.

    Each result: {origin, destination, distance_to_origin_km,
                  distance_to_destination_km, price_usd, savings_usd}
    """
    near_o = nearest(origin, n=n_neighbors, max_km=max_km)
    near_d = nearest(destination, n=n_neighbors, max_km=max_km)
    # Add the originals so we get same-origin-different-dest and vice-versa too
    src = airport_lookup(origin) or [origin, "", "", "", 0, 0]
    dst = airport_lookup(destination) or [destination, "", "", "", 0, 0]
    o_options = [{"iata": origin, "city": src[1], "country": src[2],
                  "name": src[3], "distance_km": 0}] + near_o
    d_options = [{"iata": destination, "city": dst[1], "country": dst[2],
                  "name": dst[3], "distance_km": 0}] + near_d

    jobs = []  # (o_dict, d_dict)
    for oa in o_options:
        for da in d_options:
            if oa["iata"] == origin and da["iata"] == destination:
                continue  # already covered by primary
            if oa["iata"] == da["iata"]:
                continue
            jobs.append((oa, da))

    log.info(f"composite search: {len(jobs)} probes around {origin}-{destination}")
    results = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {
            ex.submit(cheapest_for, oa["iata"], da["iata"], depart_date,
                      return_date, adults, children, infants, max_stops):
            (oa, da)
            for oa, da in jobs
        }
        for fut in as_completed(futs):
            oa, da = futs[fut]
            price = fut.result()
            if price is None:
                continue
            if primary_total_usd is not None and price >= primary_total_usd:
                continue  # not actually cheaper
            results.append({
                "origin": oa["iata"], "origin_city": oa["city"],
                "destination": da["iata"], "destination_city": da["city"],
                "distance_to_origin_km": oa["distance_km"],
                "distance_to_destination_km": da["distance_km"],
                "price_usd": price,
                "savings_usd": (primary_total_usd - price) if primary_total_usd else None,
            })
    results.sort(key=lambda r: r["price_usd"])
    return results[:5]


def hidden_city_candidates(*, origin: str, destination: str, depart_date: str,
                           adults: int, children: int, infants: int,
                           primary_one_way_usd: Optional[float] = None,
                           max_stops: Optional[int] = None) -> list[dict]:
    """For each onward city in _ONWARD_HUBS[destination], probe origin -> onward
    and surface candidates cheaper than the direct primary price.

    Caveats are returned verbatim — user MUST verify the connecting itinerary
    actually stops at the original destination on the airline page. Skiplagging
    can violate airline T&Cs (some carriers will cancel the return leg or ban
    the account). We label these clearly.

    NB: hidden-city only works one-way — return tickets are voided once you
    no-show on a leg, so this is intentionally a one-way probe.
    """
    onwards = _ONWARD_HUBS.get(destination)
    if not onwards:
        return []
    candidates = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {
            ex.submit(cheapest_for, origin, target, depart_date, None,
                      adults, children, infants, max_stops): target
            for target in onwards
        }
        for fut in as_completed(futs):
            target = futs[fut]
            price = fut.result()
            if price is None:
                continue
            if primary_one_way_usd is not None and price >= primary_one_way_usd * 0.95:
                continue  # need at least 5% savings to bother
            a = airport_lookup(target)
            if not a:
                continue
            candidates.append({
                "ticketed_to": target,
                "ticketed_city": a[1], "ticketed_country": a[2],
                "ticketed_name": a[3],
                "price_one_way_usd": price,
                "savings_usd": (primary_one_way_usd - price) if primary_one_way_usd else None,
            })
    candidates.sort(key=lambda c: c["price_one_way_usd"])
    return candidates[:3]
