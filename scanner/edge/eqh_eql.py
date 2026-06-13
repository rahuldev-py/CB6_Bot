# scanner/edge/eqh_eql.py
#
# CB6 Quantum — Equal Highs / Equal Lows (EQH/EQL) Detector
#
# Shadow/context module. Enriches trade context — NEVER gates entries.
# Config toggles via EDGE_EQH_EQL_ENABLED env var (default True).
#
# EQH: two or more swing highs within tolerance_pct of each other
# EQL: two or more swing lows within tolerance_pct of each other

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Config
_ENABLED        = os.getenv('EDGE_EQH_EQL_ENABLED', 'true').lower() != 'false'
_DEFAULT_LOOKBACK  = 40   # candles to scan
_DEFAULT_TOLERANCE = 0.0015  # 0.15% tolerance for "equal" levels


def detect_eqh_eql(
    df: pd.DataFrame,
    lookback: int   = _DEFAULT_LOOKBACK,
    tolerance: float = _DEFAULT_TOLERANCE,
) -> Dict:
    """
    Detect equal highs and equal lows in OHLCV data.

    Args:
        df        : DataFrame with columns ['open','high','low','close','volume']
        lookback  : number of recent candles to scan
        tolerance : fractional price tolerance (0.0015 = 0.15%)

    Returns dict:
        has_eqh          — bool
        has_eql          — bool
        eqh_levels       — list of price levels identified as equal highs
        eql_levels       — list of price levels identified as equal lows
        eqh_count        — how many EQH clusters found
        eql_count        — how many EQL clusters found
        sweep_quality    — float 0-1 (higher = more significant liquidity pool)
        context          — natural-language summary for Hermes
    """
    empty = _empty_result()
    if not _ENABLED or df is None or len(df) < 10:
        return empty

    try:
        recent  = df.tail(lookback).copy().reset_index(drop=True)
        highs   = _find_swing_highs(recent)
        lows    = _find_swing_lows(recent)

        eqh_clusters = _cluster_levels(highs, tolerance)
        eql_clusters = _cluster_levels(lows,  tolerance)

        has_eqh = any(len(c) >= 2 for c in eqh_clusters)
        has_eql = any(len(c) >= 2 for c in eql_clusters)

        eqh_levels = [round(sum(c) / len(c), 2) for c in eqh_clusters if len(c) >= 2]
        eql_levels = [round(sum(c) / len(c), 2) for c in eql_clusters if len(c) >= 2]

        sweep_quality = _sweep_quality(eqh_levels, eql_levels, recent)

        context_parts = []
        if eqh_levels:
            context_parts.append(f"EQH at {','.join(str(v) for v in eqh_levels)}")
        if eql_levels:
            context_parts.append(f"EQL at {','.join(str(v) for v in eql_levels)}")

        return {
            'has_eqh'        : has_eqh,
            'has_eql'        : has_eql,
            'eqh_levels'     : eqh_levels,
            'eql_levels'     : eql_levels,
            'eqh_count'      : len(eqh_levels),
            'eql_count'      : len(eql_levels),
            'sweep_quality'  : sweep_quality,
            'context'        : ' | '.join(context_parts) if context_parts else 'No EQH/EQL',
        }
    except Exception:
        return empty


def _find_swing_highs(df: pd.DataFrame, n: int = 2) -> List[float]:
    """Return list of swing high prices (local maxima with n bars on each side)."""
    highs = []
    for i in range(n, len(df) - n):
        val = df['high'].iloc[i]
        if all(val >= df['high'].iloc[i - j] for j in range(1, n + 1)) and \
           all(val >= df['high'].iloc[i + j] for j in range(1, n + 1)):
            highs.append(float(val))
    return highs


def _find_swing_lows(df: pd.DataFrame, n: int = 2) -> List[float]:
    """Return list of swing low prices (local minima with n bars on each side)."""
    lows = []
    for i in range(n, len(df) - n):
        val = df['low'].iloc[i]
        if all(val <= df['low'].iloc[i - j] for j in range(1, n + 1)) and \
           all(val <= df['low'].iloc[i + j] for j in range(1, n + 1)):
            lows.append(float(val))
    return lows


def _cluster_levels(prices: List[float], tolerance: float) -> List[List[float]]:
    """Group prices within tolerance of each other into clusters."""
    if not prices:
        return []
    sorted_p = sorted(prices)
    clusters = [[sorted_p[0]]]
    for p in sorted_p[1:]:
        ref = clusters[-1][0]
        if abs(p - ref) / max(ref, 1) <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return clusters


def _sweep_quality(eqh_levels: List[float], eql_levels: List[float],
                   df: pd.DataFrame) -> float:
    """
    Compute a sweep quality score 0-1.
    More equal levels = higher score (more liquidity trapped).
    """
    total = len(eqh_levels) + len(eql_levels)
    if total == 0:
        return 0.0
    return round(min(1.0, total / 4.0), 2)


def _empty_result() -> Dict:
    return {
        'has_eqh': False, 'has_eql': False,
        'eqh_levels': [], 'eql_levels': [],
        'eqh_count': 0, 'eql_count': 0,
        'sweep_quality': 0.0, 'context': 'EQH/EQL disabled or insufficient data',
    }
