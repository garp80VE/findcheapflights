"""Registry of airlines that do NOT appear in Google Flights / GDS.

These carriers sell only on their own sites (sanctions, ULCC model, or pure
regional). We can't reliably scrape their fares (anti-bot, local payment
rails), so instead we surface a one-click deep-link to their own search for
the requested route — the user checks the price there directly.

`url` is a function (origin, dest, depart, ret, adults, children, infants) ->
str. When the airline's site can't be pre-filled reliably, the function just
returns the booking landing page (honest: a working link beats a broken
pre-filled one).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from .airports import lookup as airport_lookup


def _home(url: str):
    return lambda o, d, dep, ret, a, c, i: url


def _southwest(o, d, dep, ret, a, c, i):
    base = ("https://www.southwest.com/air/booking/select.html"
            f"?originationAirportCode={o}&destinationAirportCode={d}"
            f"&departureDate={dep}&adultPassengersCount={a}")
    if ret:
        return base + f"&returnDate={ret}&tripType=roundtrip"
    return base + "&tripType=oneway"


def _flybondi(o, d, dep, ret, a, c, i):
    # FlyBondi accepts query params on its search route.
    base = (f"https://flybondi.com/ar/search?adults={a}&children={c}&infants={i}"
            f"&origin={o}&destination={d}&departure={dep}")
    return base + (f"&return={ret}" if ret else "")


def _jetsmart(o, d, dep, ret, a, c, i):
    return (f"https://jetsmart.com/?origin={o}&destination={d}"
            f"&departureDate={dep}{'&returnDate=' + ret if ret else ''}"
            f"&adults={a}&children={c}&infants={i}")


def _wingo(o, d, dep, ret, a, c, i):
    return (f"https://www.wingo.com/en/flights/search?origin={o}"
            f"&destination={d}&departureDate={dep}"
            f"{'&returnDate=' + ret if ret else ''}&adults={a}&children={c}")


# country (as it appears in airports.json, OpenFlights naming) -> carriers
CARRIERS_BY_COUNTRY: dict[str, list[dict]] = {
    "Venezuela": [
        {"name": "Conviasa", "note": "Estatal. Doméstico + Bogotá, Panamá, La Habana, Madrid (vía CCS).",
         "url": _home("https://www.conviasa.aero/")},
        {"name": "Laser Airlines", "note": "Doméstico + Panamá, Bogotá, Madrid.",
         "url": _home("https://www.laser.com.ve/")},
        {"name": "Estelar Latinoamérica", "note": "Doméstico + Bogotá, Panamá.",
         "url": _home("https://www.estelar.com.ve/")},
        {"name": "Avior Airlines", "note": "Doméstico + Panamá, Medellín.",
         "url": _home("https://www.aviorair.com/")},
        {"name": "Rutaca Airlines", "note": "Doméstico (Caracas, Pto. Ordaz, Maturín…).",
         "url": _home("https://www.rutaca.com.ve/")},
        {"name": "Albatros Airlines", "note": "Doméstico + Curazao.",
         "url": _home("https://www.albatros.aero/")},
    ],
    "Panama": [
        {"name": "Air Panama", "note": "Doméstico Panamá + regional (San José, David, Bocas).",
         "url": _home("https://www.airpanama.com/")},
    ],
    "Colombia": [
        {"name": "Satena", "note": "Estatal. Rutas a regiones remotas (Amazonía, Pacífico).",
         "url": _home("https://www.satena.com/")},
        {"name": "Clic (ex EasyFly)", "note": "Regional doméstico Colombia.",
         "url": _home("https://www.clic.com.co/")},
        {"name": "Wingo", "note": "Low-cost. Solo parcial en GDS — revisa directo.",
         "url": _wingo},
    ],
    "Argentina": [
        {"name": "FlyBondi", "note": "Ultra low-cost. Casi nunca en GDS.",
         "url": _flybondi},
        {"name": "JetSMART Argentina", "note": "Low-cost. Tarifas promo no siempre en GDS.",
         "url": _jetsmart},
    ],
    "Brazil": [
        {"name": "VoePass (ex MAP/Passaredo)", "note": "Regional brasileño.",
         "url": _home("https://www.voepass.com.br/")},
    ],
    "Chile": [
        {"name": "JetSMART Chile", "note": "Low-cost. Tarifas promo no siempre en GDS.",
         "url": _jetsmart},
        {"name": "SKY Airline", "note": "En GDS pero promos a veces solo en su web.",
         "url": _home("https://www.skyairline.com/")},
    ],
    "United States": [
        {"name": "Southwest", "note": "NO aparece en NINGÚN agregador. Siempre revísala directo.",
         "url": _southwest},
        {"name": "Allegiant Air", "note": "ULCC. Cobertura parcial en agregadores.",
         "url": _home("https://www.allegiantair.com/")},
    ],
}


def carriers_for_country(country: Optional[str]) -> list[dict]:
    if not country:
        return []
    return CARRIERS_BY_COUNTRY.get(country, [])


def offgds_for_route(*, origin: str, destination: str, depart_date: str,
                     return_date: Optional[str], adults: int, children: int,
                     infants: int) -> list[dict]:
    """Return off-GDS carriers relevant to either endpoint country, each with
    a ready deep-link for this exact route."""
    o = airport_lookup(origin)
    d = airport_lookup(destination)
    countries = []
    if o and o[2] not in countries:
        countries.append(o[2])
    if d and d[2] not in countries:
        countries.append(d[2])

    out = []
    seen = set()
    for ctry in countries:
        for c in carriers_for_country(ctry):
            key = c["name"]
            if key in seen:
                continue
            seen.add(key)
            try:
                link = c["url"](origin, destination, depart_date,
                                return_date, adults, children, infants)
            except Exception:
                link = "#"
            out.append({
                "name": c["name"],
                "country": ctry,
                "note": c["note"],
                "link": link,
            })
    return out
