"""
Market Regime Detector — CB6 Quantum Phase 3
Classifies market state from OHLCV data. No API calls, pure pandas/numpy.

Regimes:
  TRENDING_UP    — EMA structure bullish, ADX > 25
  TRENDING_DOWN  — EMA structure bearish, ADX > 25
  RANGING        — ADX 15–25, price oscillating between levels
  CHOPPY         — ADX < 15, high noise relative to range

Volatility:
  HIGH   — ATR(14) > 1.5× its 50-period average
  NORMAL — 0.7× – 1.5× average
  LOW    — < 0.7× average

Trend strength:
  STRONG  — ADX > 35
  MODERATE — ADX 25–35
  WEAK    — ADX 15–25
  NONE    — ADX < 15
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class RegimeResult:
    symbol:          str
    timeframe:       str
    regime:          str            # TRENDING_UP | TRENDING_DOWN | RANGING | CHOPPY
    trend_strength:  str            # STRONG | MODERATE | WEAK | NONE
    volatility:      str            # HIGH | NORMAL | LOW
    adx:             float = 0.0
    atr:             float = 0.0
    atr_ratio:       float = 0.0    # current ATR / avg ATR
    ema20:           float = 0.0
    ema50:           float = 0.0
    ema200:          float = 0.0
    price:           float = 0.0
    bars_used:       int   = 0
    note:            str   = ""

    def to_dict(self) -> dict:
        return {
            "symbol":         self.symbol,
            "timeframe":      self.timeframe,
            "regime":         self.regime,
            "trend_strength": self.trend_strength,
            "volatility":     self.volatility,
            "adx":            round(self.adx, 2),
            "atr":            round(self.atr, 4),
            "atr_ratio":      round(self.atr_ratio, 3),
            "ema20":          round(self.ema20, 4),
            "ema50":          round(self.ema50, 4),
            "ema200":         round(self.ema200, 4),
            "price":          round(self.price, 4),
            "bars_used":      self.bars_used,
            "note":           self.note,
        }


# ---------------------------------------------------------------------------
# Indicator calculations
# ---------------------------------------------------------------------------

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    prev  = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev).abs(),
        (low  - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Welles Wilder ADX."""
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    plus_dm  = (high - prev_high).clip(lower=0)
    minus_dm = (prev_low - low).clip(lower=0)
    # Zero out where the other direction is larger
    mask = plus_dm < minus_dm
    plus_dm[mask] = 0
    mask2 = minus_dm < plus_dm
    minus_dm[mask2] = 0

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_s    = tr.ewm(span=period, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(span=period, adjust=False).mean()  / atr_s
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr_s

    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
    return dx.ewm(span=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

def detect(df: pd.DataFrame, symbol: str = "", timeframe: str = "") -> RegimeResult:
    """
    Classify market regime from an OHLCV DataFrame.
    df must have columns: open, high, low, close (+ optional volume).
    Returns RegimeResult.
    """
    MIN_BARS = 60
    if df is None or len(df) < MIN_BARS:
        return RegimeResult(
            symbol=symbol, timeframe=timeframe,
            regime="UNKNOWN", trend_strength="NONE", volatility="UNKNOWN",
            note=f"insufficient data ({len(df) if df is not None else 0} bars, need {MIN_BARS})"
        )

    df = df.copy().reset_index(drop=True)
    df.columns = [c.lower() for c in df.columns]

    # Indicators
    df["ema20"]  = _ema(df["close"], 20)
    df["ema50"]  = _ema(df["close"], 50)
    df["ema200"] = _ema(df["close"], 200) if len(df) >= 200 else _ema(df["close"], len(df) // 2)
    df["atr"]    = _atr(df)
    df["adx"]    = _adx(df)

    last = df.iloc[-1]
    price   = float(last["close"])
    ema20   = float(last["ema20"])
    ema50   = float(last["ema50"])
    ema200  = float(last["ema200"])
    adx_val = float(last["adx"])
    atr_val = float(last["atr"])

    # ATR ratio: current vs 50-bar average
    atr_avg   = float(df["atr"].tail(50).mean())
    atr_ratio = atr_val / atr_avg if atr_avg > 0 else 1.0

    # Trend strength from ADX
    if adx_val >= 35:
        strength = "STRONG"
    elif adx_val >= 25:
        strength = "MODERATE"
    elif adx_val >= 15:
        strength = "WEAK"
    else:
        strength = "NONE"

    # Volatility
    if atr_ratio >= 1.5:
        volatility = "HIGH"
    elif atr_ratio <= 0.7:
        volatility = "LOW"
    else:
        volatility = "NORMAL"

    # Regime classification
    bullish_structure = ema20 > ema50
    price_above_ema20 = price > ema20

    if adx_val >= 25:
        if bullish_structure:
            regime = "TRENDING_UP"
        else:
            regime = "TRENDING_DOWN"
    elif adx_val >= 15:
        regime = "RANGING"
    else:
        regime = "CHOPPY"

    return RegimeResult(
        symbol=symbol,
        timeframe=timeframe,
        regime=regime,
        trend_strength=strength,
        volatility=volatility,
        adx=adx_val,
        atr=atr_val,
        atr_ratio=atr_ratio,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        price=price,
        bars_used=len(df),
    )


# ---------------------------------------------------------------------------
# Historical regime scan (for analysis / Phase 3 feed)
# ---------------------------------------------------------------------------

def scan_history(df: pd.DataFrame, symbol: str = "", timeframe: str = "",
                 step: int = 1) -> list[dict]:
    """
    Run detect() at each bar (rolling window). Useful for building a regime history.
    step: evaluate every Nth bar (1 = every bar, 4 = every 4th bar for speed).
    Returns list of dicts with timestamp + regime fields.
    """
    MIN_BARS = 60
    if df is None or len(df) < MIN_BARS:
        return []

    df = df.copy().reset_index(drop=True)
    df.columns = [c.lower() for c in df.columns]

    # Pre-compute all indicators on full series (much faster than rolling detect)
    df["ema20"]  = _ema(df["close"], 20)
    df["ema50"]  = _ema(df["close"], 50)
    df["atr"]    = _atr(df)
    df["adx"]    = _adx(df)

    atr_roll_avg = df["atr"].rolling(50, min_periods=10).mean()

    results = []
    ts_col = "timestamp" if "timestamp" in df.columns else None

    for i in range(MIN_BARS - 1, len(df), step):
        row = df.iloc[i]
        adx_v = float(row["adx"])
        atr_v = float(row["atr"])
        atr_a = float(atr_roll_avg.iloc[i]) if not pd.isna(atr_roll_avg.iloc[i]) else atr_v
        ratio = atr_v / atr_a if atr_a > 0 else 1.0
        bull  = row["ema20"] > row["ema50"]

        if adx_v >= 25:
            regime = "TRENDING_UP" if bull else "TRENDING_DOWN"
        elif adx_v >= 15:
            regime = "RANGING"
        else:
            regime = "CHOPPY"

        entry = {
            "symbol":    symbol,
            "timeframe": timeframe,
            "regime":    regime,
            "adx":       round(adx_v, 2),
            "atr_ratio": round(ratio, 3),
        }
        if ts_col:
            entry["ts"] = str(row[ts_col])
        results.append(entry)

    return results
