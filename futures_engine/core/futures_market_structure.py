"""
CB6 Futures Core — Market Structure
CHoCH, BOS, MSS detection on bar series.
No dependency on forex or NSE signal engines.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from futures_engine.core.futures_data_feed import FuturesBar


class Bias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL  = "NEUTRAL"


class StructureEvent(str, Enum):
    BOS_UP   = "BOS_UP"    # Break of Structure — bullish
    BOS_DOWN = "BOS_DOWN"  # Break of Structure — bearish
    CHOCH_UP = "CHOCH_UP"  # Change of Character — bullish reversal
    CHOCH_DOWN = "CHOCH_DOWN"  # Change of Character — bearish reversal
    MSS_UP   = "MSS_UP"    # Market Structure Shift — bullish
    MSS_DOWN = "MSS_DOWN"  # Market Structure Shift — bearish


@dataclass
class SwingPoint:
    index: int
    price: float
    is_high: bool          # True = swing high, False = swing low
    bar: FuturesBar


@dataclass
class StructureSignal:
    event: StructureEvent
    bar: FuturesBar
    broken_level: float    # price level that was breached
    swing_from: SwingPoint
    confirmed: bool = False


def find_swing_highs_lows(
    bars: List[FuturesBar],
    lookback: int = 3,
) -> tuple[List[SwingPoint], List[SwingPoint]]:
    """
    Identify swing highs and lows using a simple n-bar lookback on each side.
    Returns (swing_highs, swing_lows).
    """
    swing_highs: List[SwingPoint] = []
    swing_lows: List[SwingPoint] = []
    n = lookback

    for i in range(n, len(bars) - n):
        bar = bars[i]
        left_highs  = [bars[j].high for j in range(i - n, i)]
        right_highs = [bars[j].high for j in range(i + 1, i + n + 1)]
        if bar.high > max(left_highs) and bar.high > max(right_highs):
            swing_highs.append(SwingPoint(i, bar.high, True, bar))

        left_lows  = [bars[j].low for j in range(i - n, i)]
        right_lows = [bars[j].low for j in range(i + 1, i + n + 1)]
        if bar.low < min(left_lows) and bar.low < min(right_lows):
            swing_lows.append(SwingPoint(i, bar.low, False, bar))

    return swing_highs, swing_lows


def detect_structure_events(
    bars: List[FuturesBar],
    lookback: int = 3,
) -> List[StructureSignal]:
    """
    Detect BOS and CHoCH events.
    BOS: price closes beyond same-bias swing (continuation).
    CHoCH: price closes beyond opposite-bias swing (reversal).
    """
    if len(bars) < lookback * 2 + 2:
        return []

    highs, lows = find_swing_highs_lows(bars, lookback)
    if not highs or not lows:
        return []

    signals: List[StructureSignal] = []
    bias = Bias.NEUTRAL

    # Determine initial bias from first two swings
    if highs and lows:
        last_high = highs[-1]
        last_low  = lows[-1]
        if last_high.index > last_low.index:
            bias = Bias.BULLISH
        else:
            bias = Bias.BEARISH

    # Walk bars after enough structure forms
    start_idx = max(highs[0].index, lows[0].index) + 1 if highs and lows else lookback + 1

    for i in range(start_idx, len(bars)):
        bar = bars[i]
        close = bar.close

        prev_highs = [h for h in highs if h.index < i]
        prev_lows  = [l for l in lows  if l.index < i]
        if not prev_highs or not prev_lows:
            continue

        last_sh = prev_highs[-1]
        last_sl = prev_lows[-1]

        if bias in (Bias.BULLISH, Bias.NEUTRAL) and close > last_sh.price:
            signals.append(StructureSignal(
                event=StructureEvent.BOS_UP if bias == Bias.BULLISH else StructureEvent.CHOCH_UP,
                bar=bar, broken_level=last_sh.price, swing_from=last_sh,
            ))
            bias = Bias.BULLISH

        elif bias in (Bias.BEARISH, Bias.NEUTRAL) and close < last_sl.price:
            signals.append(StructureSignal(
                event=StructureEvent.BOS_DOWN if bias == Bias.BEARISH else StructureEvent.CHOCH_DOWN,
                bar=bar, broken_level=last_sl.price, swing_from=last_sl,
            ))
            bias = Bias.BEARISH

        elif bias == Bias.BULLISH and close < last_sl.price:
            signals.append(StructureSignal(
                event=StructureEvent.CHOCH_DOWN,
                bar=bar, broken_level=last_sl.price, swing_from=last_sl,
            ))
            bias = Bias.BEARISH

        elif bias == Bias.BEARISH and close > last_sh.price:
            signals.append(StructureSignal(
                event=StructureEvent.CHOCH_UP,
                bar=bar, broken_level=last_sh.price, swing_from=last_sh,
            ))
            bias = Bias.BULLISH

    return signals


def get_htf_bias(bars: List[FuturesBar], lookback: int = 3) -> Bias:
    """
    Determine higher-timeframe bias from last two confirmed swing points.
    Used as mandatory H4 bias check before entry.
    """
    if len(bars) < lookback * 2 + 4:
        return Bias.NEUTRAL
    highs, lows = find_swing_highs_lows(bars, lookback)
    if not highs or not lows:
        return Bias.NEUTRAL
    last_high = highs[-1]
    last_low  = lows[-1]
    if last_high.index > last_low.index:
        # most recent swing was a high — price making higher highs
        if len(highs) >= 2 and highs[-1].price > highs[-2].price:
            return Bias.BULLISH
        if len(lows) >= 2 and lows[-1].price > lows[-2].price:
            return Bias.BULLISH
    else:
        if len(lows) >= 2 and lows[-1].price < lows[-2].price:
            return Bias.BEARISH
        if len(highs) >= 2 and highs[-1].price < highs[-2].price:
            return Bias.BEARISH
    return Bias.NEUTRAL
