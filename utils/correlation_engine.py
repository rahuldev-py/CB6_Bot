"""
Correlation Engine — CB6 Quantum Phase 3
Rolling Pearson correlation between symbol pairs from the OHLCV archive.

Use cases:
- NIFTYBANK vs NIFTY50 (NSE breadth confirmation)
- XAGUSD vs XAUUSD (precious metals co-movement)
- USOIL vs XAUUSD (inflation/risk-off signals)
- NIFTY50 vs EURUSD (global risk appetite)
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np

from utils.ohlcv_archive import get_candles


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CorrelationResult:
    symbol_a:    str
    symbol_b:    str
    timeframe:   str
    window:      int
    correlation: float          # -1.0 to 1.0
    strength:    str            # STRONG | MODERATE | WEAK | NONE
    direction:   str            # POSITIVE | NEGATIVE
    bars_used:   int
    note:        str = ""

    def to_dict(self) -> dict:
        return {
            "symbol_a":    self.symbol_a,
            "symbol_b":    self.symbol_b,
            "timeframe":   self.timeframe,
            "window":      self.window,
            "correlation": round(self.correlation, 3),
            "strength":    self.strength,
            "direction":   self.direction,
            "bars_used":   self.bars_used,
            "note":        self.note,
        }


# ---------------------------------------------------------------------------
# Pair definitions
# ---------------------------------------------------------------------------

# Default pairs to track — (market_a, symbol_a, market_b, symbol_b)
DEFAULT_PAIRS = [
    # NSE internal
    ("NSE", "NSE:NIFTY50-INDEX",    "NSE", "NSE:NIFTYBANK-INDEX"),
    ("NSE", "NSE:NIFTY50-INDEX",    "NSE", "NSE:FINNIFTY-INDEX"),
    ("NSE", "NSE:NIFTY50-INDEX",    "NSE", "NSE:MIDCPNIFTY-INDEX"),
    # Forex internal
    ("FOREX", "XAGUSD", "FOREX", "USOIL"),
    ("FOREX", "XAGUSD", "FOREX", "EURUSD"),
    # Cross-market (when both archived)
    ("NSE", "NSE:NIFTY50-INDEX",    "FOREX", "EURUSD"),
]


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute(market_a: str, symbol_a: str,
            market_b: str, symbol_b: str,
            timeframe: str, window: int = 50,
            limit: int = 300) -> CorrelationResult:
    """
    Compute rolling Pearson correlation between two symbols over `window` bars.
    Uses the most recent `limit` bars from the archive.
    """
    df_a = get_candles(market_a, symbol_a, timeframe, limit=limit)
    df_b = get_candles(market_b, symbol_b, timeframe, limit=limit)

    if df_a.empty or df_b.empty:
        return CorrelationResult(
            symbol_a=symbol_a, symbol_b=symbol_b,
            timeframe=timeframe, window=window,
            correlation=0.0, strength="NONE", direction="NONE",
            bars_used=0, note="insufficient data in archive"
        )

    # Align on timestamp
    df_a = df_a.set_index("timestamp")["close"].rename("a")
    df_b = df_b.set_index("timestamp")["close"].rename("b")
    merged = pd.concat([df_a, df_b], axis=1).dropna()

    if len(merged) < window:
        return CorrelationResult(
            symbol_a=symbol_a, symbol_b=symbol_b,
            timeframe=timeframe, window=window,
            correlation=0.0, strength="NONE", direction="NONE",
            bars_used=len(merged),
            note=f"only {len(merged)} aligned bars, need {window}"
        )

    tail   = merged.tail(window)
    corr   = float(tail["a"].corr(tail["b"]))
    if pd.isna(corr):
        corr = 0.0

    abs_c  = abs(corr)
    if abs_c >= 0.7:
        strength = "STRONG"
    elif abs_c >= 0.4:
        strength = "MODERATE"
    elif abs_c >= 0.2:
        strength = "WEAK"
    else:
        strength = "NONE"

    direction = "POSITIVE" if corr >= 0 else "NEGATIVE"

    return CorrelationResult(
        symbol_a=symbol_a, symbol_b=symbol_b,
        timeframe=timeframe, window=window,
        correlation=corr, strength=strength, direction=direction,
        bars_used=len(merged)
    )


# ---------------------------------------------------------------------------
# Bulk scan
# ---------------------------------------------------------------------------

def scan_all(timeframe: str = "1h", window: int = 50) -> list[dict]:
    """Run compute() for all DEFAULT_PAIRS. Returns list of result dicts."""
    results = []
    for market_a, sym_a, market_b, sym_b in DEFAULT_PAIRS:
        r = compute(market_a, sym_a, market_b, sym_b, timeframe, window)
        results.append(r.to_dict())
    return results


def rolling_correlation(market_a: str, symbol_a: str,
                        market_b: str, symbol_b: str,
                        timeframe: str, window: int = 30,
                        limit: int = 500) -> pd.DataFrame:
    """
    Return a DataFrame of rolling correlations over time.
    Useful for charting how correlation changes — e.g., NIFTY vs NIFTYBANK divergence.
    """
    df_a = get_candles(market_a, symbol_a, timeframe, limit=limit)
    df_b = get_candles(market_b, symbol_b, timeframe, limit=limit)

    if df_a.empty or df_b.empty:
        return pd.DataFrame()

    df_a = df_a.set_index("timestamp")["close"].rename("a")
    df_b = df_b.set_index("timestamp")["close"].rename("b")
    merged = pd.concat([df_a, df_b], axis=1).dropna()

    if len(merged) < window:
        return pd.DataFrame()

    merged["correlation"] = merged["a"].rolling(window).corr(merged["b"])
    return merged.reset_index()[["timestamp", "correlation"]].dropna()
