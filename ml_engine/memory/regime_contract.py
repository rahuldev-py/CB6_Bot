from __future__ import annotations

from typing import Dict


REGIME_V1_ALLOWED = {
    "TREND",
    "RANGE",
    "EXPANSION",
    "REVERSAL",
    "NEWS_CHAOS",
    "UNKNOWN",
}


# Backward-compatible normalization from current labels into the phase-1 contract.
REGIME_ALIAS_MAP: Dict[str, str] = {
    "TRENDING": "TREND",
    "NEUTRAL": "RANGE",
    "CHOPPY": "RANGE",
    "TREND": "TREND",
    "RANGE": "RANGE",
    "EXPANSION": "EXPANSION",
    "REVERSAL": "REVERSAL",
    "NEWS_CHAOS": "NEWS_CHAOS",
    "UNKNOWN": "UNKNOWN",
}


def normalize_regime(value: str) -> str:
    if not value:
        return "UNKNOWN"
    key = str(value).strip().upper()
    normalized = REGIME_ALIAS_MAP.get(key, "UNKNOWN")
    return normalized if normalized in REGIME_V1_ALLOWED else "UNKNOWN"

