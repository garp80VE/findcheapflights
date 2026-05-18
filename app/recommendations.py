"""Heuristic booking-timing & season recommendations.

Rules based on airline pricing patterns documented by Hopper / Skyscanner /
Google Flights' own "best time to book" data. Not predictive — directional.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional

from .airports import lookup as airport_lookup


@dataclass
class Recommendation:
    severity: str  # "info" | "good" | "warn"
    title: str
    detail: str


# Rough route classification by haversine — short = domestic-ish, long = intl.
def _route_distance_km(origin: str, destination: str) -> Optional[float]:
    o = airport_lookup(origin)
    d = airport_lookup(destination)
    if not o or not d:
        return None
    from .airports import haversine_km
    return haversine_km(o[4], o[5], d[4], d[5])


def _is_intercontinental(origin: str, destination: str) -> bool:
    o = airport_lookup(origin)
    d = airport_lookup(destination)
    if not o or not d:
        return False
    # rough continental grouping by country
    return o[2] != d[2] and (_route_distance_km(origin, destination) or 0) > 3000


def _is_intl(origin: str, destination: str) -> bool:
    o = airport_lookup(origin)
    d = airport_lookup(destination)
    return bool(o and d and o[2] != d[2])


def _peak_season(d: date, destination_country: Optional[str]) -> Optional[str]:
    """Return a human label if d falls in a known peak window."""
    m, day = d.month, d.day
    # Christmas / New Year (universal)
    if (m == 12 and day >= 18) or (m == 1 and day <= 7):
        return "Navidad / Año Nuevo"
    # Easter window (approximate — mid-March to mid-April)
    if (m == 3 and day >= 20) or (m == 4 and day <= 15):
        return "Semana Santa"
    # Summer (June-Aug) for Northern Hemisphere destinations
    if m in (7, 8):
        return "Verano (alta demanda)"
    return None


def analyze(*, origin: str, destination: str, depart_date: str,
            return_date: Optional[str] = None) -> list[dict]:
    """Return a list of recommendation dicts (severity, title, detail)."""
    out: list[Recommendation] = []
    try:
        dep = date.fromisoformat(depart_date)
    except ValueError:
        return []
    today = date.today()
    days_out = (dep - today).days
    intl = _is_intl(origin, destination)
    intercontinental = _is_intercontinental(origin, destination)

    # 1) Booking-window timing
    if days_out < 14:
        out.append(Recommendation(
            "warn", "Reserva de última hora",
            f"Faltan {days_out} días. Las tarifas suelen estar en su pico — "
            f"compra cuanto antes si necesitas este vuelo. No esperes a que bajen."
        ))
    elif days_out <= 30:
        out.append(Recommendation(
            "info", f"{days_out} días antes",
            "Estás por debajo del sweet spot. Las tarifas siguen altas; revisa cada 1-2 días."
        ))
    elif intercontinental:
        if 60 <= days_out <= 180:
            out.append(Recommendation(
                "good", f"Buen momento para reservar ({days_out} días antes)",
                "Sweet spot transatlántico/transpacífico es 60-180 días. "
                "Si ves un precio que te gusta, considera asegurarlo."
            ))
        elif days_out > 180:
            out.append(Recommendation(
                "info", f"Reserva muy anticipada ({days_out} días antes)",
                f"Quedan más de 6 meses. Las aerolíneas suelen abrir tarifas a precio "
                f"alto y las van bajando con promociones. Vuelve a revisar en "
                f"{max(1, (days_out - 180) // 30)} mes(es). Vale la pena trackear esta ruta."
            ))
    elif intl:
        if 30 <= days_out <= 90:
            out.append(Recommendation(
                "good", f"Buen momento para reservar ({days_out} días antes)",
                "Sweet spot para internacional regional es 30-90 días."
            ))
        elif days_out > 90:
            out.append(Recommendation(
                "info", f"Reserva anticipada ({days_out} días antes)",
                "Considera esperar — las tarifas suelen bajar en las próximas semanas."
            ))
    else:  # domestic
        if 14 <= days_out <= 60:
            out.append(Recommendation(
                "good", f"Buen momento para reservar ({days_out} días antes)",
                "Sweet spot doméstico es 14-60 días."
            ))
        elif days_out > 90:
            out.append(Recommendation(
                "info", f"Reserva anticipada ({days_out} días antes)",
                "Para vuelos domésticos, las mejores tarifas suelen aparecer en las últimas 4-8 semanas."
            ))

    # 2) Peak season warning
    dest_country = (airport_lookup(destination) or [None, None, None])[2]
    season = _peak_season(dep, dest_country)
    if season:
        out.append(Recommendation(
            "warn", f"Fecha en temporada alta: {season}",
            "Los precios pueden estar 30-80% por encima del promedio. "
            "Si tu fecha es flexible, prueba ±1-2 semanas y compara."
        ))

    # 3) Return-trip duration tip (for intercontinental)
    if return_date and intercontinental:
        try:
            ret = date.fromisoformat(return_date)
            stay = (ret - dep).days
            if 7 <= stay <= 21:
                out.append(Recommendation(
                    "info", "Duración óptima de viaje",
                    f"Estancia de {stay} días — las aerolíneas suelen ofrecer mejores "
                    "tarifas en este rango (1-3 semanas) que en estancias muy cortas."
                ))
            elif stay < 4:
                out.append(Recommendation(
                    "info", "Estancia muy corta",
                    f"Solo {stay} días. Las tarifas \"weekend\" son a veces más caras "
                    "que estancias de 7-14 días por la misma ruta."
                ))
        except ValueError:
            pass

    # 4) Tracker hint
    if days_out > 90 or season:
        out.append(Recommendation(
            "info", "Trackea esta ruta",
            "Activa una alerta de precio para esta búsqueda; el sistema revisa "
            "cada 6 horas y te avisa por email si el precio baja."
        ))

    return [asdict(r) for r in out]
