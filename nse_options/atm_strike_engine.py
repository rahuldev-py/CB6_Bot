from __future__ import annotations

from math import floor
from typing import Optional

STRIKE_GAPS = {"NIFTY": 50, "FINNIFTY": 50, "MIDCPNIFTY": 25, "BANKNIFTY": 100}


def normalize_underlying(symbol: str) -> str:
    return str(symbol).upper().replace("NSE:", "").replace("-INDEX", "").replace("-EQ", "")


def strike_gap(symbol: str) -> int:
    return STRIKE_GAPS.get(normalize_underlying(symbol), 50)


def nearest_atm_from_price(symbol: str, spot: float) -> Optional[int]:
    if not spot or spot <= 0:
        return None
    gap = strike_gap(symbol)
    return int(round(float(spot) / gap) * gap)


def nearby_strikes(atm: int, gap: int, count: int) -> list[int]:
    strikes = [atm]
    for i in range(1, count + 1):
        strikes.extend([atm - gap * i, atm + gap * i])
    return sorted(s for s in strikes if s > 0)
