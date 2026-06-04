"""
CB6 Futures Core — Liquidity
Buy-side / sell-side liquidity pools, EQH/EQL, PDH/PDL, DOL identification.
ICT liquidity concepts applied to futures bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from futures_engine.core.futures_data_feed import FuturesBar


@dataclass
class LiquidityPool:
    level: float
    side: str            # "BSL" (buy-side) | "SSL" (sell-side)
    bar: FuturesBar      # bar where the pool was identified
    touches: int = 1
    swept: bool = False
    sweep_bar: Optional[FuturesBar] = None


@dataclass
class EqualLevel:
    price: float
    kind: str            # "EQH" | "EQL"
    bars: list           # bars forming the equal level
    swept: bool = False


def find_liquidity_pools(
    bars: List[FuturesBar],
    lookback: int = 20,
    tolerance: float = 0.001,  # 0.1% price tolerance for "equal" levels
) -> List[LiquidityPool]:
    """
    Identify BSL/SSL pools from equal highs and equal lows.
    BSL = resting buy stops above swing highs.
    SSL = resting sell stops below swing lows.
    """
    pools: List[LiquidityPool] = []
    if len(bars) < lookback:
        return pools

    window = bars[-lookback:]
    highs = [b.high for b in window]
    lows  = [b.low  for b in window]

    # Group equal highs → BSL above them
    visited_h: set = set()
    for i, b in enumerate(window):
        if i in visited_h:
            continue
        cluster = [j for j in range(i, len(window))
                   if abs(window[j].high - b.high) / b.high <= tolerance]
        if len(cluster) >= 2:
            for j in cluster:
                visited_h.add(j)
            pools.append(LiquidityPool(
                level=max(window[j].high for j in cluster),
                side="BSL", bar=b,
                touches=len(cluster),
            ))

    # Group equal lows → SSL below them
    visited_l: set = set()
    for i, b in enumerate(window):
        if i in visited_l:
            continue
        cluster = [j for j in range(i, len(window))
                   if abs(window[j].low - b.low) / b.low <= tolerance]
        if len(cluster) >= 2:
            for j in cluster:
                visited_l.add(j)
            pools.append(LiquidityPool(
                level=min(window[j].low for j in cluster),
                side="SSL", bar=b,
                touches=len(cluster),
            ))

    return pools


def check_sweeps(
    pools: List[LiquidityPool],
    bars: List[FuturesBar],
) -> List[LiquidityPool]:
    """
    Mark pools as swept when a subsequent bar trades through the level
    but closes back on the other side (stop hunt / liquidity grab).
    """
    for pool in pools:
        if pool.swept:
            continue
        after = [b for b in bars if b.timestamp > pool.bar.timestamp]
        for b in after:
            if pool.side == "BSL":
                if b.high > pool.level and b.close < pool.level:
                    pool.swept = True
                    pool.sweep_bar = b
                    break
            else:  # SSL
                if b.low < pool.level and b.close > pool.level:
                    pool.swept = True
                    pool.sweep_bar = b
                    break
    return pools


def get_pdh_pdl(bars: List[FuturesBar]) -> tuple[Optional[float], Optional[float]]:
    """
    Return (previous_day_high, previous_day_low) from bar series.
    Assumes bars sorted ascending by timestamp.
    """
    if not bars:
        return None, None
    today = bars[-1].timestamp.date()
    prev_bars = [b for b in bars if b.timestamp.date() < today]
    if not prev_bars:
        return None, None
    last_date = max(b.timestamp.date() for b in prev_bars)
    day_bars = [b for b in prev_bars if b.timestamp.date() == last_date]
    return max(b.high for b in day_bars), min(b.low for b in day_bars)


def identify_dol(
    bars: List[FuturesBar],
    pools: List[LiquidityPool],
    htf_bias: str,
) -> Optional[LiquidityPool]:
    """
    Identify the most probable Draw on Liquidity given HTF bias.
    BULLISH bias → target BSL above current price.
    BEARISH bias → target SSL below current price.
    """
    if not bars or not pools:
        return None
    current_price = bars[-1].close
    candidates = [
        p for p in pools if not p.swept and (
            (htf_bias == "BULLISH" and p.side == "BSL" and p.level > current_price) or
            (htf_bias == "BEARISH" and p.side == "SSL" and p.level < current_price)
        )
    ]
    if not candidates:
        return None
    if htf_bias == "BULLISH":
        return min(candidates, key=lambda p: p.level)   # nearest BSL above
    return max(candidates, key=lambda p: p.level)         # nearest SSL below


def find_fvg(
    bars: List[FuturesBar],
    min_size_points: float = 0.0,
) -> list[dict]:
    """
    Detect Fair Value Gaps (3-candle imbalance).
    Returns list of {"type": "bull|bear", "top": float, "bottom": float, "bar": bar}
    """
    fvgs = []
    for i in range(2, len(bars)):
        b0, b1, b2 = bars[i-2], bars[i-1], bars[i]
        # Bullish FVG: b2.low > b0.high
        if b2.low > b0.high and (b2.low - b0.high) >= min_size_points:
            fvgs.append({"type": "bull", "top": b2.low, "bottom": b0.high, "bar": b1})
        # Bearish FVG: b2.high < b0.low
        if b2.high < b0.low and (b0.low - b2.high) >= min_size_points:
            fvgs.append({"type": "bear", "top": b0.low, "bottom": b2.high, "bar": b1})
    return fvgs
