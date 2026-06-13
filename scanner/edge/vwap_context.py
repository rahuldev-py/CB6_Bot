# scanner/edge/vwap_context.py
#
# CB6 Quantum — VWAP Context Module
#
# Computes intraday VWAP and classifies price relative to it.
# Shadow/context only — NEVER used as a hard filter.
# Toggle: EDGE_VWAP_ENABLED env var.

from __future__ import annotations

import os
from typing import Dict, Optional

import pandas as pd

_ENABLED = os.getenv('EDGE_VWAP_ENABLED', 'true').lower() != 'false'


def compute_vwap(df: pd.DataFrame) -> Optional[float]:
    """
    Compute cumulative intraday VWAP from DataFrame.
    Requires columns: high, low, close, volume.
    Returns VWAP float or None if computation fails.
    """
    if not _ENABLED or df is None or len(df) < 2:
        return None
    try:
        tp  = (df['high'] + df['low'] + df['close']) / 3
        vol = df['volume'].fillna(0)
        if vol.sum() <= 0:
            return None
        vwap = (tp * vol).cumsum() / vol.cumsum()
        return round(float(vwap.iloc[-1]), 2)
    except Exception:
        return None


def get_vwap_context(df: pd.DataFrame, current_price: float) -> Dict:
    """
    Compute VWAP and classify price position.

    Returns:
        vwap         — float or None
        position     — 'above_vwap' | 'below_vwap' | 'at_vwap' | 'unknown'
        distance_pts — points from VWAP
        distance_pct — % from VWAP
        signal       — 'reclaim' | 'reject' | 'at' | 'above' | 'below' | 'unknown'
        context      — string summary for Hermes
    """
    vwap = compute_vwap(df)
    if vwap is None or not current_price:
        return _empty_result()

    diff_pts = round(current_price - vwap, 2)
    diff_pct = round(diff_pts / vwap * 100, 3) if vwap else 0.0

    if abs(diff_pts) < vwap * 0.001:
        position = 'at_vwap'
        signal   = 'at'
    elif current_price > vwap:
        position = 'above_vwap'
        signal   = _classify_above(df, vwap)
    else:
        position = 'below_vwap'
        signal   = _classify_below(df, vwap)

    return {
        'vwap'         : vwap,
        'position'     : position,
        'distance_pts' : abs(diff_pts),
        'distance_pct' : abs(diff_pct),
        'signal'       : signal,
        'context'      : (
            f"VWAP={vwap} price={current_price} "
            f"({diff_pts:+.0f}pts {diff_pct:+.2f}%) → {signal}"
        ),
    }


def _classify_above(df: pd.DataFrame, vwap: float) -> str:
    """Detect VWAP reclaim (was below, now above) vs sustained above."""
    try:
        lows = df['low'].tail(5)
        if (lows < vwap).any() and float(df['close'].iloc[-1]) > vwap:
            return 'reclaim'
        return 'above'
    except Exception:
        return 'above'


def _classify_below(df: pd.DataFrame, vwap: float) -> str:
    """Detect VWAP reject (was above, now below) vs sustained below."""
    try:
        highs = df['high'].tail(5)
        if (highs > vwap).any() and float(df['close'].iloc[-1]) < vwap:
            return 'reject'
        return 'below'
    except Exception:
        return 'below'


def _empty_result() -> Dict:
    return {
        'vwap': None, 'position': 'unknown',
        'distance_pts': 0, 'distance_pct': 0,
        'signal': 'unknown', 'context': 'VWAP disabled or insufficient data',
    }
