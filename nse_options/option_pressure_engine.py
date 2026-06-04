from __future__ import annotations

from typing import Any


def calculate_option_pressure(context: dict[str, Any]) -> dict[str, Any]:
    ce = context.get("ce", {})
    pe = context.get("pe", {})
    strikes = context.get("strikes", [])
    ce_oi = sum(_num(ce.get(s, {}).get("oi")) for s in strikes)
    pe_oi = sum(_num(pe.get(s, {}).get("oi")) for s in strikes)
    ce_vol = sum(_num(ce.get(s, {}).get("volume")) for s in strikes)
    pe_vol = sum(_num(pe.get(s, {}).get("volume")) for s in strikes)

    pcr_oi = pe_oi / ce_oi if ce_oi > 0 else None
    pcr_vol = pe_vol / ce_vol if ce_vol > 0 else None

    score = 0
    if pcr_oi is not None:
        if pcr_oi >= 1.15:
            score += 1
        elif pcr_oi <= 0.85:
            score -= 1
    if pcr_vol is not None:
        if pcr_vol >= 1.20:
            score += 1
        elif pcr_vol <= 0.80:
            score -= 1

    bias = "NEUTRAL"
    if score >= 1:
        bias = "BULLISH"
    elif score <= -1:
        bias = "BEARISH"

    return {
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
        "ce_volume": ce_vol,
        "pe_volume": pe_vol,
        "pcr_oi": round(pcr_oi, 3) if pcr_oi is not None else None,
        "pcr_volume": round(pcr_vol, 3) if pcr_vol is not None else None,
        "pressure_score": score,
        "option_bias": bias,
    }


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0
