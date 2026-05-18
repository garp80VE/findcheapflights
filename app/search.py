"""Core search engine: Google Flights scrape + flex dates + multi-POS arbitrage."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import List, Optional

from fast_flights import FlightData, Passengers
from fast_flights.filter import TFSData
from fast_flights.primp import Client

from .airlines import build_booking_links, google_flights_market_url
from .currency import to_usd
from .fees import estimate_extras
from .parser import parse_flights, ParsedFlight

log = logging.getLogger("fcf.search")

_CONSENT_COOKIES = {
    "CONSENT": "YES+cb.20231231-00-p0.en+FX+000",
    "SOCS": "CAESHAgBEhJnd3NfMjAyNDAxMDQtMF9SQzIaAmVuIAEaBgiA4N6sBg",
}

# (gl, hl, currency) probes for point-of-sale arbitrage.
# hl is forced to 'en' so the aria-label parser (English-only) works regardless
# of POS country. gl + curr drive the actual price/market.
POS_PROBES = [
    ("us", "en", "USD"),
    ("gb", "en", "GBP"),
    ("es", "en", "EUR"),
    ("de", "en", "EUR"),
    ("mx", "en", "MXN"),
    ("br", "en", "BRL"),
    ("in", "en", "INR"),
    ("jp", "en", "JPY"),
    ("tr", "en", "TRY"),
    ("ar", "en", "ARS"),
    ("ca", "en", "CAD"),
    ("au", "en", "AUD"),
]


@dataclass
class FlightOption:
    airline: str
    departure: str
    arrival: str
    origin_airport: str
    destination_airport: str
    duration: str
    stops: int
    plus_days: int
    base_price_usd: float
    raw_price: str
    raw_currency: str
    pos: str
    extras_usd: float = 0.0
    extras_breakdown: list = field(default_factory=list)
    total_usd: float = 0.0
    booking_links: dict = field(default_factory=dict)
    is_best: bool = False


@dataclass
class POSResult:
    pos: str
    currency: str
    best_price_usd: Optional[float]
    best_price_raw: Optional[str]
    n_flights: int
    error: Optional[str] = None
    google_link: Optional[str] = None    # opens Google Flights in this market


@dataclass
class SearchResult:
    origin: str
    destination: str
    depart_date: str
    return_date: Optional[str]
    adults: int
    children: int
    infants: int
    flights: List[FlightOption]
    arbitrage: List[POSResult]
    cheapest_pos: Optional[str]
    cheapest_pos_savings_usd: float
    flex_calendar: List[dict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    rerun_pos: Optional[str] = None             # if not None, flights are from this market
    rerun_savings_usd: Optional[float] = None   # vs the originally-displayed cheapest
    split_vs_block: Optional[dict] = None        # round-trip block vs 2 one-ways

    def to_dict(self):
        return {
            "origin": self.origin,
            "destination": self.destination,
            "depart_date": self.depart_date,
            "return_date": self.return_date,
            "adults": self.adults,
            "children": self.children,
            "infants": self.infants,
            "flights": [asdict(f) for f in self.flights],
            "arbitrage": [asdict(p) for p in self.arbitrage],
            "cheapest_pos": self.cheapest_pos,
            "cheapest_pos_savings_usd": self.cheapest_pos_savings_usd,
            "flex_calendar": self.flex_calendar,
            "notes": self.notes,
            "rerun_pos": self.rerun_pos,
            "rerun_savings_usd": self.rerun_savings_usd,
            "split_vs_block": self.split_vs_block,
        }


def _fetch_one(origin: str, destination: str, depart_iso: str,
               return_iso: Optional[str], adults: int, children: int, infants: int,
               gl: str, hl: str, curr: str,
               max_stops: Optional[int] = None) -> List[ParsedFlight]:
    """One Google Flights request -> parsed ParsedFlight list."""
    fd = [FlightData(date=depart_iso, from_airport=origin, to_airport=destination)]
    if return_iso:
        fd.append(FlightData(date=return_iso, from_airport=destination, to_airport=origin))
    trip = "round-trip" if return_iso else "one-way"
    tfs = TFSData.from_interface(
        flight_data=fd, trip=trip, seat="economy",
        passengers=Passengers(adults=adults, children=children,
                              infants_in_seat=0, infants_on_lap=infants),
        max_stops=max_stops,
    )
    params = {
        "tfs": tfs.as_b64().decode("utf-8"),
        "hl": hl, "gl": gl, "curr": curr,
        "tfu": "EgQIABABIgA",
    }
    client = Client(impersonate="chrome_126", verify=False, cookies=_CONSENT_COOKIES)
    res = client.get("https://www.google.com/travel/flights", params=params)
    if res.status_code != 200:
        raise RuntimeError(f"Google Flights returned {res.status_code}")
    flights = parse_flights(res.text)
    if not flights:
        raise RuntimeError("No flights parsed (page structure may have changed)")
    return flights


_WEEKDAY_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def weekday_analysis(*, origin: str, destination: str, depart_date: str,
                     return_date: Optional[str], adults: int, children: int,
                     infants: int, checked_bags: int, pick_seat: bool,
                     max_stops: Optional[int]) -> list[dict]:
    """Probe the 7 calendar days starting at depart_date (one per weekday),
    keeping trip duration constant. Returns one row per weekday sorted Mon-Sun.

    Caveat: only one sample per weekday — directional, not statistical.
    Ideal for a quick "Tuesday vs Saturday" comparison without massive flex.
    """
    base_dep = date.fromisoformat(depart_date)
    base_ret = date.fromisoformat(return_date) if return_date else None

    pairs = []
    for i in range(7):
        d = base_dep + timedelta(days=i)
        if d < date.today():
            continue
        r = (base_ret + timedelta(days=i)) if base_ret else None
        pairs.append((d, r))

    raw = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {
            ex.submit(_fetch_one, origin, destination, d.isoformat(),
                      r.isoformat() if r else None,
                      adults, children, infants, "us", "en", "USD", max_stops):
            (d, r)
            for d, r in pairs
        }
        for fut in as_completed(futs):
            d, r = futs[fut]
            try:
                flights = fut.result()
                best_total = None
                best_airline = None
                for pf in flights:
                    usd = to_usd(pf.price_amount, pf.price_currency)
                    extras = estimate_extras(pf.airline, bring_carry_on=True,
                                             checked_bags=checked_bags,
                                             pick_seat=pick_seat)
                    tot = usd + extras["total"] * (adults + children)
                    if best_total is None or tot < best_total:
                        best_total, best_airline = tot, pf.airline
                raw.append({
                    "weekday_idx": d.weekday(),
                    "weekday": _WEEKDAY_ES[d.weekday()],
                    "depart_date": d.isoformat(),
                    "return_date": r.isoformat() if r else None,
                    "total_usd": round(best_total, 2) if best_total else None,
                    "airline": best_airline,
                })
            except Exception as e:
                raw.append({
                    "weekday_idx": d.weekday(),
                    "weekday": _WEEKDAY_ES[d.weekday()],
                    "depart_date": d.isoformat(),
                    "return_date": r.isoformat() if r else None,
                    "total_usd": None, "airline": None,
                    "error": str(e)[:60],
                })
    raw.sort(key=lambda x: x["weekday_idx"])
    return raw


def cheapest_for(origin: str, destination: str, depart_date: str,
                 return_date: Optional[str], adults: int, children: int,
                 infants: int, max_stops: Optional[int] = None) -> Optional[float]:
    """Quick probe: cheapest USD price for this route on this date, or None.

    Used by the "alternative airports" suggester so we only surface candidates
    that actually have flights, ordered by price.
    """
    try:
        flights = _fetch_one(origin, destination, depart_date, return_date,
                             adults, children, infants, "us", "en", "USD",
                             max_stops)
    except Exception:
        return None
    if not flights:
        return None
    best = None
    for pf in flights:
        usd = to_usd(pf.price_amount, pf.price_currency)
        if usd <= 0:
            continue
        if best is None or usd < best:
            best = usd
    return round(best, 2) if best else None


def _enrich(pf: ParsedFlight, *, pos_label: str,
            origin: str, destination: str, depart_iso: str, return_iso: Optional[str],
            adults: int, children: int, infants: int,
            checked_bags: int, pick_seat: bool) -> FlightOption:
    base_usd = to_usd(pf.price_amount, pf.price_currency)
    extras = estimate_extras(pf.airline, bring_carry_on=True,
                             checked_bags=checked_bags, pick_seat=pick_seat)
    n_pax = adults + children
    extras_usd = extras["total"] * n_pax
    return FlightOption(
        airline=pf.airline,
        departure=f"{pf.departure_time} ({pf.departure_day})",
        arrival=f"{pf.arrival_time} ({pf.arrival_day})",
        origin_airport=pf.origin_airport,
        destination_airport=pf.destination_airport,
        duration=pf.duration, stops=pf.stops, plus_days=pf.plus_days,
        base_price_usd=round(base_usd, 2),
        raw_price=pf.price_raw, raw_currency=pf.price_currency,
        pos=pos_label, extras_usd=round(extras_usd, 2),
        extras_breakdown=extras["breakdown"],
        total_usd=round(base_usd + extras_usd, 2),
        booking_links=build_booking_links(pf.airline, origin, destination,
                                          depart_iso, return_iso,
                                          adults, children, infants),
        is_best=pf.is_best,
    )


def search(*, origin: str, destination: str, depart_date: str,
           return_date: Optional[str] = None,
           adults: int = 1, children: int = 0, infants: int = 0,
           checked_bags: int = 0, pick_seat: bool = False,
           flex_days: int = 0, probe_pos: bool = True,
           max_stops: Optional[int] = None) -> SearchResult:
    origin, destination = origin.upper(), destination.upper()
    notes: List[str] = []

    primary_pos = ("us", "en", "USD")
    primary_parsed = _fetch_one(origin, destination, depart_date, return_date,
                                adults, children, infants, *primary_pos,
                                max_stops=max_stops)
    flights = [
        _enrich(pf, pos_label=f"{primary_pos[0]}/{primary_pos[2]}",
                origin=origin, destination=destination,
                depart_iso=depart_date, return_iso=return_date,
                adults=adults, children=children, infants=infants,
                checked_bags=checked_bags, pick_seat=pick_seat)
        for pf in primary_parsed
    ]

    # Dedupe: same (airline, departure, arrival, raw_price) appearing in both
    # the "Best" and "All" tabs.
    seen = set()
    unique = []
    for f in flights:
        key = (f.airline, f.departure, f.arrival, f.raw_price, f.stops)
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    flights = sorted(unique, key=lambda f: f.total_usd)

    arbitrage: List[POSResult] = []
    cheapest_pos: Optional[str] = None
    cheapest_pos_savings = 0.0
    if probe_pos and flights:
        primary_best = flights[0].base_price_usd
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {}
            for gl, hl, curr in POS_PROBES:
                if (gl, hl, curr) == primary_pos:
                    continue
                futs[ex.submit(_fetch_one, origin, destination, depart_date,
                               return_date, adults, children, infants,
                               gl, hl, curr, max_stops)] = (gl, hl, curr)
            for fut in as_completed(futs):
                gl, hl, curr = futs[fut]
                try:
                    rows = fut.result()
                    best, best_raw = None, None
                    for pf in rows:
                        usd = to_usd(pf.price_amount, pf.price_currency)
                        if best is None or usd < best:
                            best, best_raw = usd, pf.price_raw
                    arbitrage.append(POSResult(
                        pos=f"{gl}/{curr}", currency=curr,
                        best_price_usd=round(best, 2) if best else None,
                        best_price_raw=best_raw, n_flights=len(rows),
                        google_link=google_flights_market_url(
                            origin, destination, depart_date, return_date,
                            adults, children, infants, gl=gl, curr=curr),
                    ))
                except Exception as e:
                    arbitrage.append(POSResult(pos=f"{gl}/{curr}", currency=curr,
                                               best_price_usd=None,
                                               best_price_raw=None,
                                               n_flights=0, error=str(e)[:120],
                                               google_link=google_flights_market_url(
                                                   origin, destination, depart_date,
                                                   return_date, adults, children,
                                                   infants, gl=gl, curr=curr)))
        arbitrage.append(POSResult(
            pos=f"{primary_pos[0]}/{primary_pos[2]}",
            currency=primary_pos[2],
            best_price_usd=round(primary_best, 2),
            best_price_raw=flights[0].raw_price,
            n_flights=len(flights),
            google_link=google_flights_market_url(
                origin, destination, depart_date, return_date,
                adults, children, infants,
                gl=primary_pos[0], curr=primary_pos[2]),
        ))
        arbitrage.sort(key=lambda p: (p.best_price_usd is None, p.best_price_usd or 0))
        valid = [p for p in arbitrage if p.best_price_usd is not None]
        if valid:
            top = valid[0]
            if top.pos != f"{primary_pos[0]}/{primary_pos[2]}" and top.best_price_usd < primary_best:
                cheapest_pos = top.pos
                cheapest_pos_savings = round(primary_best - top.best_price_usd, 2)
                notes.append(
                    f"Posible ahorro de ${cheapest_pos_savings:.2f} comprando desde "
                    f"el mercado {top.pos} (verifica T&Cs de la aerolínea, "
                    "algunas anulan tickets con POS distinto al país de origen)."
                )

    # If a different market is materially cheaper, re-run the primary search
    # there and surface those flights as the main list. Threshold: savings of
    # at least $20 AND at least 3% of the original primary's price — avoids
    # rerunning for noise (fx fluctuations between probes).
    rerun_pos = None
    rerun_savings = None
    if (probe_pos and flights and cheapest_pos
            and cheapest_pos_savings >= 20
            and cheapest_pos_savings >= flights[0].base_price_usd * 0.03):
        gl, curr = cheapest_pos.split("/")
        try:
            retry_parsed = _fetch_one(origin, destination, depart_date,
                                      return_date, adults, children, infants,
                                      gl, "en", curr, max_stops)
            retry_flights = [
                _enrich(pf, pos_label=cheapest_pos,
                        origin=origin, destination=destination,
                        depart_iso=depart_date, return_iso=return_date,
                        adults=adults, children=children, infants=infants,
                        checked_bags=checked_bags, pick_seat=pick_seat)
                for pf in retry_parsed
            ]
            seen2 = set()
            unique2 = []
            for f in retry_flights:
                k = (f.airline, f.departure, f.arrival, f.raw_price, f.stops)
                if k in seen2:
                    continue
                seen2.add(k)
                unique2.append(f)
            retry_flights = sorted(unique2, key=lambda f: f.total_usd)
            if retry_flights and retry_flights[0].total_usd < flights[0].total_usd:
                rerun_savings = round(flights[0].total_usd - retry_flights[0].total_usd, 2)
                notes.insert(0,
                    f"🎯 Búsqueda re-ejecutada automáticamente en el mercado "
                    f"{cheapest_pos}: el más barato es ahora "
                    f"${retry_flights[0].total_usd:.2f} con {retry_flights[0].airline} "
                    f"(ahorras ${rerun_savings:.2f} vs el mercado US/USD). "
                    f"⚠ Para comprarlo, usa una VPN o tarjeta del país {gl.upper()} — "
                    f"verifica T&Cs antes."
                )
                flights = retry_flights
                rerun_pos = cheapest_pos
        except Exception as e:
            log.warning(f"rerun in {cheapest_pos} failed: {e}")

    flex_calendar = []
    if flex_days and flex_days > 0:
        base_dep = date.fromisoformat(depart_date)
        base_ret = date.fromisoformat(return_date) if return_date else None

        # Shift depart AND return by the same delta so the trip duration stays
        # constant — the user wants their N-day trip, cheaper, in a similar
        # window. This makes flex prices apples-to-apples with the primary
        # search instead of comparing one-way vs round-trip.
        pairs_to_probe = []
        for off in range(-flex_days, flex_days + 1):
            d = base_dep + timedelta(days=off)
            if d < date.today():
                continue
            r = (base_ret + timedelta(days=off)) if base_ret else None
            pairs_to_probe.append((d.isoformat(), r.isoformat() if r else None))

        workers = min(max(6, len(pairs_to_probe) // 4), 12)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(_fetch_one, origin, destination, dep_iso, ret_iso,
                          adults, children, infants, "us", "en", "USD", max_stops):
                (dep_iso, ret_iso)
                for dep_iso, ret_iso in pairs_to_probe
            }
            for fut in as_completed(futs):
                dep_iso, ret_iso = futs[fut]
                try:
                    rows = fut.result()
                    best_total = None
                    best_airline = None
                    for pf in rows:
                        usd = to_usd(pf.price_amount, pf.price_currency)
                        extras = estimate_extras(pf.airline, bring_carry_on=True,
                                                 checked_bags=checked_bags,
                                                 pick_seat=pick_seat)
                        tot = usd + extras["total"] * (adults + children)
                        if best_total is None or tot < best_total:
                            best_total, best_airline = tot, pf.airline
                    flex_calendar.append({
                        "depart_date": dep_iso,
                        "return_date": ret_iso,
                        "total_usd": round(best_total, 2) if best_total else None,
                        "airline": best_airline,
                    })
                except Exception as e:
                    flex_calendar.append({"depart_date": dep_iso,
                                          "return_date": ret_iso,
                                          "total_usd": None, "airline": None,
                                          "error": str(e)[:80]})
        flex_calendar.sort(key=lambda x: x["depart_date"])
        valid_dates = [d for d in flex_calendar if d["total_usd"] is not None]
        if valid_dates and flights:
            cheapest = min(valid_dates, key=lambda d: d["total_usd"])
            if (cheapest["depart_date"] != depart_date
                    and cheapest["total_usd"] < flights[0].total_usd):
                savings = flights[0].total_usd - cheapest["total_usd"]
                if return_date:
                    notes.append(
                        f"Moviendo el viaje a {cheapest['depart_date']} ↔ "
                        f"{cheapest['return_date']} con {cheapest['airline']}, "
                        f"el total baja a ${cheapest['total_usd']:.2f} — "
                        f"ahorras ${savings:.2f} manteniendo la duración del viaje."
                    )
                else:
                    notes.append(
                        f"Volando el {cheapest['depart_date']} con "
                        f"{cheapest['airline']} el total bajaría a "
                        f"${cheapest['total_usd']:.2f} (vs ${flights[0].total_usd:.2f})."
                    )

    # Split vs block: is it cheaper to buy the round-trip as ONE combined
    # ticket (block) or as TWO independent one-ways (ida suelta + vuelta
    # suelta, possibly different airlines)? Compared on base fare — separate
    # tickets each charge their own bags/seat, noted to the user.
    split_vs_block = None
    if return_date and flights:
        block_base = flights[0].base_price_usd
        block_airline = flights[0].airline
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_out = ex.submit(cheapest_for, origin, destination, depart_date,
                              None, adults, children, infants, max_stops)
            f_ret = ex.submit(cheapest_for, destination, origin, return_date,
                              None, adults, children, infants, max_stops)
            out_p = f_out.result()
            ret_p = f_ret.result()
        if out_p is not None and ret_p is not None:
            split_total = round(out_p + ret_p, 2)
            cheaper = "split" if split_total < block_base else "block"
            savings = round(abs(block_base - split_total), 2)
            split_vs_block = {
                "block_base_usd": round(block_base, 2),
                "block_airline": block_airline,
                "split_out_usd": round(out_p, 2),
                "split_return_usd": round(ret_p, 2),
                "split_total_usd": split_total,
                "cheaper": cheaper,
                "savings_usd": savings,
            }
            if cheaper == "split" and savings >= 1:
                notes.append(
                    f"💡 Comprar por tramos separados (ida ${out_p:.0f} + "
                    f"vuelta ${ret_p:.0f} = ${split_total:.0f}) es "
                    f"${savings:.2f} más barato que el billete ida-y-vuelta "
                    f"combinado (${block_base:.0f}). Ojo: son 2 reservas "
                    f"independientes, el equipaje se cobra aparte en cada una."
                )
            elif cheaper == "block":
                notes.append(
                    f"💡 El billete ida-y-vuelta combinado "
                    f"(${block_base:.0f}, {block_airline}) es ${savings:.2f} "
                    f"más barato que comprar los tramos por separado "
                    f"(${split_total:.0f}). Cómpralo como bloque único."
                )

    return SearchResult(
        origin=origin, destination=destination,
        depart_date=depart_date, return_date=return_date,
        adults=adults, children=children, infants=infants,
        flights=flights, arbitrage=arbitrage,
        cheapest_pos=cheapest_pos,
        cheapest_pos_savings_usd=cheapest_pos_savings,
        flex_calendar=flex_calendar, notes=notes,
        rerun_pos=rerun_pos, rerun_savings_usd=rerun_savings,
        split_vs_block=split_vs_block,
    )
