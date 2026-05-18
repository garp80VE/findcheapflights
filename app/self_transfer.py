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
    """Hubs to try as the connecting point. Real global gateways FIRST (these
    actually have intercontinental service) then airports geographically near
    the origin. De-duplicated, excludes origin/destination themselves.

    Putting gateways first matters: small origins (e.g. VLC) have only GA
    airfields nearby with zero commercial service — those would otherwise eat
    the probe budget and return nothing."""
    near = [a["iata"] for a in nearest(origin, n=4, max_km=700)]
    seen, out = set(), []
    for code in _GLOBAL_GATEWAYS + near:
        if code in (origin, destination) or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out[:14]  # bound the probe budget


def _best_through_hub(a_to_hub: Optional[float],
                       hub_to_b_same: Optional[float],
                       hub_to_b_next: Optional[float],
                       d1: str, d_next: str):
    """Given leg1 (A->hub on d1) and leg2 (hub->B same/next day) prices,
    return (leg1_price, leg2_price, leg2_date, when) or None if incomplete."""
    if a_to_hub is None:
        return None
    opts = []
    if hub_to_b_same is not None:
        opts.append(("mismo día", hub_to_b_same, d1))
    if hub_to_b_next is not None:
        opts.append(("día siguiente (escala nocturna)", hub_to_b_next, d_next))
    if not opts:
        return None
    when, leg2_price, leg2_date = min(opts, key=lambda x: x[1])
    return a_to_hub, leg2_price, leg2_date, when


def _journey_options(a: str, b: str, d: str, adults: int, children: int,
                     infants: int, max_stops: Optional[int],
                     max_results: int) -> list[dict]:
    """Self-transfer options for a single A->B journey on date d.

    Each option = A->hub (leg1, day d) + hub->B (leg2, day d or d+1). Returns
    independent options sorted by total. Hubs are gateways near A's region.
    """
    hubs = _hub_candidates(a, b)
    if not hubs:
        return []
    d_next = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    res: dict[str, dict[str, Optional[float]]] = {h: {} for h in hubs}
    jobs = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for hub in hubs:
            jobs[ex.submit(cheapest_for, a, hub, d, None,
                           adults, children, infants, max_stops)] = (hub, "l1")
            jobs[ex.submit(cheapest_for, hub, b, d, None,
                           adults, children, infants, max_stops)] = (hub, "l2s")
            jobs[ex.submit(cheapest_for, hub, b, d_next, None,
                           adults, children, infants, max_stops)] = (hub, "l2n")
        for fut in as_completed(jobs):
            hub, key = jobs[fut]
            res[hub][key] = fut.result()

    out = []
    for hub in hubs:
        r = res[hub]
        best = _best_through_hub(r.get("l1"), r.get("l2s"), r.get("l2n"),
                                 d, d_next)
        if best is None:
            continue
        l1_p, l2_p, l2_date, l2_when = best
        ap = airport_lookup(hub)
        out.append({
            "hub": hub,
            "hub_city": ap[1] if ap else hub,
            "hub_country": ap[2] if ap else "",
            "leg1": f"{a}-{hub}", "leg1_usd": round(l1_p, 2),
            "leg1_date": d,
            "leg1_link": google_flights_market_url(
                a, hub, d, None, adults, children, infants),
            "leg2": f"{hub}-{b}", "leg2_usd": round(l2_p, 2),
            "leg2_date": l2_date, "leg2_when": l2_when,
            "leg2_link": google_flights_market_url(
                hub, b, l2_date, None, adults, children, infants),
            "total_usd": round(l1_p + l2_p, 2),
        })
    out.sort(key=lambda x: x["total_usd"])
    return out[:max_results]


def find_self_transfer(*, origin: str, destination: str, depart_date: str,
                       return_date: Optional[str], adults: int, children: int,
                       infants: int, max_stops: Optional[int] = None,
                       max_results: int = 4) -> dict:
    """Self-transfer for the requested trip.

    Returns {is_round_trip, outbound[], return[], best_combined_usd}.

    outbound = self-transfer options for origin->destination on depart_date.
    return   = self-transfer options for destination->origin on return_date
               (only when a return date is given). Outbound and return are
               INDEPENDENT journeys (days apart) and may route through
               different hubs — we don't force the same one. best_combined_usd
               is cheapest outbound + cheapest return when both exist.
    """
    is_rt = bool(return_date)
    outbound = _journey_options(origin, destination, depart_date, adults,
                                children, infants, max_stops, max_results)
    ret: list[dict] = []
    if is_rt:
        ret = _journey_options(destination, origin, return_date, adults,
                               children, infants, max_stops, max_results)
    best_combined = None
    if outbound and (ret or not is_rt):
        best_combined = round(
            outbound[0]["total_usd"] + (ret[0]["total_usd"] if ret else 0), 2)
    return {
        "is_round_trip": is_rt,
        "outbound": outbound,
        "return": ret,
        "best_combined_usd": best_combined,
    }
