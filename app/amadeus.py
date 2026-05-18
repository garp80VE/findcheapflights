"""Amadeus Self-Service API as an optional secondary source.

Enable by setting AMADEUS_CLIENT_ID + AMADEUS_CLIENT_SECRET env vars (free tier
gives 2000 calls/month — sandbox: test.api.amadeus.com).
Get keys at https://developers.amadeus.com/

When enabled, the search endpoint also queries Amadeus and merges results.
Amadeus often returns slightly different inventory than Google's aggregator,
especially for partner B2B fares.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import httpx

log = logging.getLogger("fcf.amadeus")

# test.api = sandbox (free), api = production (paid)
_BASE = os.environ.get("AMADEUS_BASE", "https://test.api.amadeus.com")
_TOKEN: Optional[str] = None
_TOKEN_EXP: float = 0.0
_LOCK = threading.Lock()


def is_configured() -> bool:
    return bool(os.environ.get("AMADEUS_CLIENT_ID")
                and os.environ.get("AMADEUS_CLIENT_SECRET"))


def _get_token() -> Optional[str]:
    """Cache the OAuth token until 60s before expiry."""
    global _TOKEN, _TOKEN_EXP
    if not is_configured():
        return None
    with _LOCK:
        if _TOKEN and time.time() < _TOKEN_EXP - 60:
            return _TOKEN
        try:
            r = httpx.post(
                f"{_BASE}/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": os.environ["AMADEUS_CLIENT_ID"],
                    "client_secret": os.environ["AMADEUS_CLIENT_SECRET"],
                },
                timeout=10.0,
            )
            r.raise_for_status()
            data = r.json()
            _TOKEN = data["access_token"]
            _TOKEN_EXP = time.time() + int(data.get("expires_in", 1799))
            return _TOKEN
        except Exception as e:
            log.warning(f"Amadeus token fetch failed: {e}")
            return None


def search_flights(*, origin: str, destination: str, depart_date: str,
                   return_date: Optional[str] = None,
                   adults: int = 1, children: int = 0, infants: int = 0,
                   max_results: int = 10) -> list[dict]:
    """Search Amadeus and return a list of simplified flight options.

    Each: {airline, price_usd, currency, depart_at, arrive_at, duration, stops,
           ticketing_carriers}.
    Returns [] if not configured, on error, or no results.
    """
    token = _get_token()
    if not token:
        return []
    params = {
        "originLocationCode": origin,
        "destinationLocationCode": destination,
        "departureDate": depart_date,
        "adults": adults,
        "currencyCode": "USD",
        "max": max_results,
    }
    if return_date:
        params["returnDate"] = return_date
    if children:
        params["children"] = children
    if infants:
        params["infants"] = infants
    try:
        r = httpx.get(
            f"{_BASE}/v2/shopping/flight-offers",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20.0,
        )
        if r.status_code != 200:
            log.info(f"Amadeus returned {r.status_code}: {r.text[:200]}")
            return []
        body = r.json()
    except Exception as e:
        log.warning(f"Amadeus search failed: {e}")
        return []

    dict_carriers = (body.get("dictionaries") or {}).get("carriers") or {}
    out = []
    for offer in body.get("data", []):
        try:
            price = float(offer["price"]["grandTotal"])
            currency = offer["price"]["currency"]
            itin = offer["itineraries"][0]
            segs = itin["segments"]
            first = segs[0]
            last = segs[-1]
            carrier_code = first.get("carrierCode") or ""
            airline_name = dict_carriers.get(carrier_code, carrier_code)
            out.append({
                "airline": airline_name,
                "price_usd": price if currency == "USD" else price,  # currencyCode=USD already
                "currency": currency,
                "depart_at": first["departure"]["at"],
                "arrive_at": last["arrival"]["at"],
                "duration": itin.get("duration", ""),
                "stops": max(0, len(segs) - 1),
                "ticketing_carriers": [
                    dict_carriers.get(c, c)
                    for c in offer.get("validatingAirlineCodes", [])
                ],
                "deep_link": (
                    f"https://www.google.com/travel/flights?q=Flights%20from%20"
                    f"{origin}%20to%20{destination}%20on%20{depart_date}"
                ),
            })
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    out.sort(key=lambda x: x["price_usd"])
    return out
