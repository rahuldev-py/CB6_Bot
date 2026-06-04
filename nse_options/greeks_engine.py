from __future__ import annotations

from typing import Any


def normalize_greeks(rows: list[dict[str, Any]], symbol: str, expiry: str, atm: int | None) -> dict[str, Any]:
    strikes: list[int] = []
    ce: dict[int, dict[str, Any]] = {}
    pe: dict[int, dict[str, Any]] = {}
    for row in rows:
        strike = _to_int(row.get("strike"))
        if strike is None:
            continue
        strikes.append(strike)
        ce[strike] = _normalize_side(row.get("CE") or {}, row.get("CE.tradingsymbol"))
        pe[strike] = _normalize_side(row.get("PE") or {}, row.get("PE.tradingsymbol"))
    return {
        "symbol": symbol,
        "expiry": expiry,
        "atm": atm,
        "strikes": sorted(set(strikes)),
        "ce": ce,
        "pe": pe,
        "summary": {},
    }


def _normalize_side(data: dict[str, Any], tradingsymbol: str | None) -> dict[str, Any]:
    return {
        "tradingsymbol": tradingsymbol,
        "ltp": _to_float(data.get("ltp") or data.get("last_price")),
        "iv": _to_float(data.get("iv") or data.get("implied_volatility")),
        "delta": _to_float(data.get("delta")),
        "gamma": _to_float(data.get("gamma")),
        "theta": _to_float(data.get("theta")),
        "vega": _to_float(data.get("vega")),
        "oi": _to_float(data.get("oi") or data.get("open_interest")),
        "volume": _to_float(data.get("volume") or data.get("traded_volume")),
    }


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except Exception:
        return None
