"""FastAPI app exposing the flight search engine."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from concurrent.futures import ThreadPoolExecutor, as_completed

from . import amadeus, deals as deals_mod, tracker
from .airports import nearest
from .hunter import composite_search, hidden_city_candidates
from .notifier import is_configured as smtp_is_configured
from .recommendations import analyze as analyze_recommendations
from .local_carriers import offgds_for_route
from .search import cheapest_for, search, weekday_analysis
from .self_transfer import find_self_transfer

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("fcf.api")

BASE = Path(__file__).resolve().parent
DATA = BASE.parent / "data"
INDEX_PATH = BASE / "templates" / "index.html"
app = FastAPI(title="FindCheapFlights", version="0.1.0")


@app.on_event("startup")
async def _startup():
    tracker.start_worker()
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
_AIRPORTS_JSON = (DATA / "airports.json").read_text(encoding="utf-8")


class SearchPayload(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3,
                        description="IATA airport code, e.g. MAD")
    destination: str = Field(..., min_length=3, max_length=3)
    depart_date: str  # YYYY-MM-DD
    return_date: Optional[str] = None
    adults: int = Field(1, ge=1, le=9)
    children: int = Field(0, ge=0, le=9)  # 2-11 yrs
    infants: int = Field(0, ge=0, le=9)   # under 2
    seniors: int = Field(0, ge=0, le=9)   # treated as adults for fare
    checked_bags: int = Field(0, ge=0, le=4)
    pick_seat: bool = False
    flex_days: int = Field(0, ge=0, le=30)
    probe_pos: bool = True
    max_stops: Optional[int] = Field(None, ge=0, le=3)
    composite: bool = False        # probe nearby airport pairs
    hidden_city: bool = False      # probe skiplagging candidates
    use_amadeus: bool = False      # query Amadeus too (needs API key)
    weekday_analysis: bool = False # probe 7 consecutive days, group by weekday
    self_transfer: bool = False    # virtual interlining through hubs


def _asset_version(name: str) -> str:
    """mtime-based version tag so changes to static files bust browser cache."""
    try:
        return str(int((BASE / "static" / name).stat().st_mtime))
    except OSError:
        return "0"


@app.get("/", response_class=HTMLResponse)
async def home():
    # Read on every request so template edits don't require a restart.
    html = INDEX_PATH.read_text(encoding="utf-8")
    html = html.replace("/static/app.js",
                        f"/static/app.js?v={_asset_version('app.js')}")
    html = html.replace("/static/style.css",
                        f"/static/style.css?v={_asset_version('style.css')}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.post("/api/search")
async def api_search(payload: SearchPayload):
    try:
        from datetime import date as _date
        dep = _date.fromisoformat(payload.depart_date)
        if dep < _date.today():
            raise HTTPException(status_code=400,
                                detail="La fecha de ida no puede estar en el pasado.")
        if payload.return_date:
            ret = _date.fromisoformat(payload.return_date)
            if ret < dep:
                raise HTTPException(status_code=400,
                                    detail="La fecha de vuelta no puede ser anterior a la de ida.")
        if payload.origin == payload.destination:
            raise HTTPException(status_code=400,
                                detail="Origen y destino no pueden ser el mismo aeropuerto.")
        # seniors fly on adult fares — fold them into the adult count.
        adults = payload.adults + payload.seniors
        result = await asyncio.to_thread(
            search,
            origin=payload.origin, destination=payload.destination,
            depart_date=payload.depart_date, return_date=payload.return_date,
            adults=adults, children=payload.children, infants=payload.infants,
            checked_bags=payload.checked_bags, pick_seat=payload.pick_seat,
            flex_days=payload.flex_days, probe_pos=payload.probe_pos,
            max_stops=payload.max_stops,
        )
        body = result.to_dict()
        body["recommendations"] = analyze_recommendations(
            origin=payload.origin, destination=payload.destination,
            depart_date=payload.depart_date, return_date=payload.return_date,
        )
        body["smtp_configured"] = smtp_is_configured()
        body["amadeus_configured"] = amadeus.is_configured()
        body["offgds_carriers"] = offgds_for_route(
            origin=payload.origin, destination=payload.destination,
            depart_date=payload.depart_date, return_date=payload.return_date,
            adults=adults, children=payload.children, infants=payload.infants,
        )
        primary_total = body["flights"][0]["total_usd"] if body["flights"] else None

        # Hunter add-ons. Each is gated and runs in a thread so the request
        # stays responsive — they probe in parallel as well.
        if payload.composite:
            body["composite"] = await asyncio.to_thread(
                composite_search,
                origin=payload.origin, destination=payload.destination,
                depart_date=payload.depart_date, return_date=payload.return_date,
                adults=adults, children=payload.children, infants=payload.infants,
                max_stops=payload.max_stops,
                primary_total_usd=primary_total,
            )
        if payload.hidden_city:
            # Hidden-city only makes sense for one-way travel — comparing it
            # against the round-trip primary is apples-to-oranges. Probe the
            # one-way price separately when the user asked for round-trip.
            if payload.return_date:
                ow_primary = await asyncio.to_thread(
                    cheapest_for, payload.origin, payload.destination,
                    payload.depart_date, None,
                    adults, payload.children, payload.infants, payload.max_stops,
                )
            else:
                ow_primary = primary_total
            body["primary_one_way_usd"] = ow_primary
            body["hidden_city"] = await asyncio.to_thread(
                hidden_city_candidates,
                origin=payload.origin, destination=payload.destination,
                depart_date=payload.depart_date,
                adults=adults, children=payload.children, infants=payload.infants,
                primary_one_way_usd=ow_primary,
                max_stops=payload.max_stops,
            )
        if payload.use_amadeus and amadeus.is_configured():
            body["amadeus"] = await asyncio.to_thread(
                amadeus.search_flights,
                origin=payload.origin, destination=payload.destination,
                depart_date=payload.depart_date, return_date=payload.return_date,
                adults=adults, children=payload.children, infants=payload.infants,
            )
        if payload.weekday_analysis:
            body["weekday_analysis"] = await asyncio.to_thread(
                weekday_analysis,
                origin=payload.origin, destination=payload.destination,
                depart_date=payload.depart_date, return_date=payload.return_date,
                adults=adults, children=payload.children, infants=payload.infants,
                checked_bags=payload.checked_bags, pick_seat=payload.pick_seat,
                max_stops=payload.max_stops,
            )
        if payload.self_transfer:
            body["self_transfer"] = await asyncio.to_thread(
                find_self_transfer,
                origin=payload.origin, destination=payload.destination,
                depart_date=payload.depart_date, return_date=payload.return_date,
                adults=adults, children=payload.children, infants=payload.infants,
                max_stops=payload.max_stops,
            )

        return JSONResponse(body)
    except HTTPException:
        raise
    except RuntimeError as e:
        msg = str(e)
        if "No flights parsed" in msg:
            # No single-ticket itinerary. Run BOTH fallbacks in parallel:
            # nearby airports + self-transfer (virtual interlining) — the
            # latter directly answers "why no connections for VLC->CCS".
            alt_task = asyncio.to_thread(_find_alternatives, payload, adults)
            st_task = asyncio.to_thread(
                find_self_transfer,
                origin=payload.origin, destination=payload.destination,
                depart_date=payload.depart_date, return_date=payload.return_date,
                adults=adults, children=payload.children, infants=payload.infants,
                max_stops=payload.max_stops,
            )
            (alt_origin, alt_dest), self_transfer = await asyncio.gather(
                alt_task, st_task,
            )
            offgds = offgds_for_route(
                origin=payload.origin, destination=payload.destination,
                depart_date=payload.depart_date, return_date=payload.return_date,
                adults=adults, children=payload.children, infants=payload.infants,
            )
            raise HTTPException(
                status_code=404,
                detail={
                    "message": ("No hay vuelos de billete único para esa "
                                "ruta/fecha (origen pequeño sin itinerario "
                                "directo publicado). Mira self-transfer, "
                                "aeropuertos cercanos y aerolíneas fuera de "
                                "Google Flights abajo."),
                    "origin": payload.origin,
                    "destination": payload.destination,
                    "alternative_origins": alt_origin,
                    "alternative_destinations": alt_dest,
                    "self_transfer": self_transfer,
                    "offgds_carriers": offgds,
                },
            )
        log.exception("search failed")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    except Exception as e:
        log.exception("search failed")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


def _find_alternatives(payload: "SearchPayload", adults: int):
    """For each side (origin/destination), probe the 6 nearest airports in
    parallel and return the 2 cheapest that actually have flights.

    Returns (alt_origins, alt_destinations).
    """
    # Dense-airport regions (Europe, Caribbean) have many small fields between
    # the user's airport and the next real hub — Spain has ~12 GA airfields
    # within 300 km of VLC before you hit MAD. n=20 catches the hubs anyway.
    cand_o = nearest(payload.origin, n=20, max_km=1500)
    cand_d = nearest(payload.destination, n=20, max_km=1500)

    jobs = []  # (side, candidate, origin_for_probe, dest_for_probe)
    for c in cand_o:
        jobs.append(("origin", c, c["iata"], payload.destination))
    for c in cand_d:
        jobs.append(("destination", c, payload.origin, c["iata"]))

    results = {"origin": [], "destination": []}
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {}
        for side, c, o, d in jobs:
            futs[ex.submit(
                cheapest_for, o, d, payload.depart_date, payload.return_date,
                adults, payload.children, payload.infants, payload.max_stops,
            )] = (side, c)
        for fut in as_completed(futs):
            side, c = futs[fut]
            price = fut.result()
            if price is not None:
                c["best_price_usd"] = price
                results[side].append(c)

    results["origin"].sort(key=lambda r: r["best_price_usd"])
    results["destination"].sort(key=lambda r: r["best_price_usd"])
    return results["origin"][:2], results["destination"][:2]


@app.get("/healthz")
async def healthz():
    return {"ok": True,
            "smtp_configured": smtp_is_configured(),
            "amadeus_configured": amadeus.is_configured()}


@app.get("/api/deals")
async def get_deals(origin: Optional[str] = None,
                    destination: Optional[str] = None,
                    origin_country: Optional[str] = None):
    """Mistake-fare RSS feeds, refreshed in memory every 30 min.

    Pass ?origin=XXX or ?destination=XXX (IATA, uppercase) to filter, or
    ?origin_country=Spain to filter by country.
    """
    ds = await asyncio.to_thread(deals_mod.fetch_deals)
    if origin or destination or origin_country:
        ds = deals_mod.filter_deals(
            ds, origin=origin.upper() if origin else None,
            destination=destination.upper() if destination else None,
            origin_country=origin_country,
        )
    return {"deals": ds, "count": len(ds)}


class TrackPayload(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    depart_date: str
    return_date: Optional[str] = None
    adults: int = Field(1, ge=1, le=9)
    children: int = Field(0, ge=0, le=9)
    infants: int = Field(0, ge=0, le=9)
    max_stops: Optional[int] = Field(None, ge=0, le=3)
    email: Optional[str] = None
    threshold_usd: Optional[float] = Field(None, gt=0)


@app.post("/api/tracks")
async def create_track(payload: TrackPayload):
    if payload.origin == payload.destination:
        raise HTTPException(400, "Origen y destino no pueden ser iguales.")
    tid = tracker.create_track(
        origin=payload.origin.upper(), destination=payload.destination.upper(),
        depart_date=payload.depart_date, return_date=payload.return_date,
        adults=payload.adults, children=payload.children, infants=payload.infants,
        max_stops=payload.max_stops,
        email=payload.email, threshold_usd=payload.threshold_usd,
    )
    return {"id": tid, "smtp_configured": smtp_is_configured()}


@app.get("/api/tracks")
async def list_tracks():
    return {"tracks": tracker.list_tracks(),
            "smtp_configured": smtp_is_configured(),
            "poll_interval_s": tracker.POLL_INTERVAL}


@app.delete("/api/tracks/{track_id}")
async def remove_track(track_id: int):
    if not tracker.delete_track(track_id):
        raise HTTPException(404, "Track no encontrado")
    return {"ok": True}


@app.get("/api/airports")
async def airports():
    """Return the bundled list of airports as compact JSON arrays.

    Format: [[iata, city, country, name], ...]  — ~6000 entries, ~340 KB.
    Served once and cached client-side; all filtering happens in the browser.
    """
    from fastapi.responses import Response
    return Response(
        content=_AIRPORTS_JSON,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=86400"},
    )
