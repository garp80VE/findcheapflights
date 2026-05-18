"""Per-airline ancillary fee lookup (baggage, seat selection)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_FEES_PATH = Path(__file__).resolve().parent.parent / "data" / "fees.json"
_FEES: Optional[dict] = None


def _load() -> dict:
    global _FEES
    if _FEES is None:
        _FEES = json.loads(_FEES_PATH.read_text(encoding="utf-8"))
    return _FEES


def lookup(airline_name: str) -> dict:
    """Return {carry_on, checked, seat} in USD for the given airline.

    Falls back to a default entry when the airline is unknown. Matching is
    case-insensitive substring against the keys so 'Ryanair UK' matches
    'Ryanair'.
    """
    fees = _load()
    name = (airline_name or "").lower()
    for key, val in fees.items():
        if key.startswith("_"):
            continue
        if key.lower() in name or name in key.lower():
            return val
    return fees.get("_default", {"carry_on": 0, "checked": 50, "seat": 12})


def estimate_extras(airline_name: str, *, bring_carry_on: bool = True,
                    checked_bags: int = 0, pick_seat: bool = False) -> dict:
    """Compute estimated extras for one passenger, one-way.

    Returns {total, breakdown: [{label, amount}, ...]}.
    """
    f = lookup(airline_name)
    breakdown = []
    total = 0
    if bring_carry_on and f.get("carry_on", 0) > 0:
        breakdown.append({"label": "Carry-on", "amount": f["carry_on"]})
        total += f["carry_on"]
    if checked_bags > 0:
        amt = f.get("checked", 0) * checked_bags
        breakdown.append({"label": f"Checked bag x{checked_bags}", "amount": amt})
        total += amt
    if pick_seat and f.get("seat", 0) > 0:
        breakdown.append({"label": "Seat selection", "amount": f["seat"]})
        total += f["seat"]
    return {"total": total, "breakdown": breakdown}
