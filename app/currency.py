"""Lightweight currency conversion using frankfurter.app (ECB rates, no key)."""
from __future__ import annotations

import time
from typing import Dict, Optional
import httpx

_CACHE: Dict[str, float] = {}
_CACHE_TS: float = 0.0
_TTL_SECONDS = 60 * 60 * 6  # 6h


def _refresh(base: str = "USD") -> None:
    global _CACHE, _CACHE_TS
    try:
        r = httpx.get(f"https://api.frankfurter.app/latest?from={base}", timeout=8.0)
        r.raise_for_status()
        rates = r.json().get("rates", {})
        rates[base] = 1.0
        _CACHE = rates
        _CACHE_TS = time.time()
    except Exception:
        # On failure, populate a coarse fallback so the app still works offline.
        _CACHE = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "MXN": 17.0, "BRL": 5.0,
                  "INR": 83.0, "ARS": 900.0, "JPY": 155.0, "CAD": 1.36, "AUD": 1.52,
                  "CHF": 0.88, "CNY": 7.2, "COP": 4000.0, "CLP": 950.0, "PEN": 3.75,
                  "TRY": 32.0, "SGD": 1.34}
        _CACHE_TS = time.time()


def to_usd(amount: float, currency: str) -> float:
    """Convert `amount` in `currency` to USD."""
    if not currency or currency.upper() == "USD":
        return amount
    if not _CACHE or (time.time() - _CACHE_TS) > _TTL_SECONDS:
        _refresh("USD")
    rate = _CACHE.get(currency.upper())
    if not rate:
        return amount  # unknown -> pass through
    return amount / rate


_CURRENCY_SYMBOLS = {
    "$": "USD", "US$": "USD", "USD": "USD",
    "€": "EUR", "EUR": "EUR",
    "£": "GBP", "GBP": "GBP",
    "¥": "JPY", "JPY": "JPY", "CN¥": "CNY", "CNY": "CNY",
    "₹": "INR", "INR": "INR",
    "MX$": "MXN", "MXN": "MXN",
    "R$": "BRL", "BRL": "BRL",
    "CA$": "CAD", "CAD": "CAD",
    "A$": "AUD", "AUD": "AUD",
    "S$": "SGD", "SGD": "SGD",
    "CHF": "CHF",
    "TRY": "TRY",
    "ARS": "ARS",
    "COP": "COP",
    "CLP": "CLP",
    "PEN": "PEN",
}


def parse_price(price_str: str, hint_currency: Optional[str] = None) -> tuple[float, str]:
    """Parse '€123', 'US$456', '1,234' -> (amount_float, currency_code).

    If hint_currency is given and no symbol is found, use the hint.
    """
    s = (price_str or "").strip()
    if not s or s == "0":
        return 0.0, hint_currency or "USD"
    currency = hint_currency or "USD"
    # try multi-char prefixes first
    for sym in sorted(_CURRENCY_SYMBOLS.keys(), key=len, reverse=True):
        if s.startswith(sym):
            currency = _CURRENCY_SYMBOLS[sym]
            s = s[len(sym):]
            break
    # strip everything except digits/dot/comma
    digits = "".join(ch for ch in s if ch.isdigit() or ch in ".,")
    # european format: 1.234,56 -> 1234.56
    if "," in digits and "." in digits:
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "," in digits:
        # ambiguous: if exactly 3 digits after the last comma assume thousands sep
        tail = digits.split(",")[-1]
        if len(tail) == 3:
            digits = digits.replace(",", "")
        else:
            digits = digits.replace(",", ".")
    try:
        return float(digits), currency
    except ValueError:
        return 0.0, currency
