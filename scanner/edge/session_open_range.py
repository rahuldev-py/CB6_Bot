# scanner/edge/session_open_range.py
#
# CB6 Quantum — Session Open Range (OR) Logic for NSE
#
# Computes the 9:15-9:30 and 9:15-9:45 opening ranges.
# Detects OR high break, OR low break, and OR sweep-reversal.
#
# Shadow/context module. Adds tags to trade records — NEVER gates entries.
# Toggle: EDGE_SESSION_OR_ENABLED env var.

from __future__ import annotations

import os
from datetime import time
from typing import Dict, Optional

import pandas as pd

_ENABLED = os.getenv('EDGE_SESSION_OR_ENABLED', 'true').lower() != 'false'

# Opening range windows (IST)
_OR_START   = time(9, 15)
_OR_END_15  = time(9, 30)   # 15-minute OR
_OR_END_30  = time(9, 45)   # 30-minute OR


def compute_open_range(df: pd.DataFrame, index_tz: str = 'Asia/Kolkata') -> Dict:
    """
    Compute the session open range from an OHLCV DataFrame.

    Expects df to have a DatetimeIndex (UTC or IST) and
    standard columns: open, high, low, close.

    Returns:
        or_high_15     — high of 9:15-9:30 candles
        or_low_15      — low  of 9:15-9:30 candles
        or_high_30     — high of 9:15-9:45 candles
        or_low_30      — low  of 9:15-9:45 candles
        or_mid_15      — midpoint of 15-min OR
        or_mid_30      — midpoint of 30-min OR
        or_size_pts    — range size in points (30-min)
        context        — string tag
    """
    empty = _empty_result()
    if not _ENABLED or df is None or len(df) < 4:
        return empty

    try:
        # Ensure IST-localized index
        idx = df.index
        if hasattr(idx, 'tz') and idx.tz is None:
            try:
                import pytz as _pytz
                idx = idx.tz_localize('UTC').tz_convert(index_tz)
            except Exception:
                pass
        elif hasattr(idx, 'tz') and idx.tz is not None:
            try:
                import pytz as _pytz
                idx = idx.tz_convert(index_tz)
            except Exception:
                pass

        df_ist = df.copy()
        df_ist.index = idx

        # Filter to OR windows
        or_mask_15 = [(t.time() >= _OR_START and t.time() < _OR_END_15) for t in df_ist.index]
        or_mask_30 = [(t.time() >= _OR_START and t.time() < _OR_END_30) for t in df_ist.index]

        or_15 = df_ist[or_mask_15]
        or_30 = df_ist[or_mask_30]

        or_high_15 = float(or_15['high'].max()) if len(or_15) > 0 else 0.0
        or_low_15  = float(or_15['low'].min())  if len(or_15) > 0 else 0.0
        or_high_30 = float(or_30['high'].max()) if len(or_30) > 0 else 0.0
        or_low_30  = float(or_30['low'].min())  if len(or_30) > 0 else 0.0

        or_mid_15  = round((or_high_15 + or_low_15) / 2, 2) if or_high_15 else 0.0
        or_mid_30  = round((or_high_30 + or_low_30) / 2, 2) if or_high_30 else 0.0
        or_size    = round(or_high_30 - or_low_30, 2) if or_high_30 and or_low_30 else 0.0

        return {
            'or_high_15'  : or_high_15,
            'or_low_15'   : or_low_15,
            'or_high_30'  : or_high_30,
            'or_low_30'   : or_low_30,
            'or_mid_15'   : or_mid_15,
            'or_mid_30'   : or_mid_30,
            'or_size_pts' : or_size,
            'context'     : f'OR 15min={or_low_15}-{or_high_15} 30min={or_low_30}-{or_high_30}',
        }
    except Exception:
        return empty


def classify_price_vs_or(price: float, or_result: Dict) -> Dict:
    """
    Classify current price relative to the 30-min open range.

    Returns:
        position    — 'above_or' | 'below_or' | 'inside_or'
        breakout    — 'OR_HIGH_BREAK' | 'OR_LOW_BREAK' | None
        distance_pts — points from nearest OR boundary
        context     — string tag
    """
    if not or_result or not or_result.get('or_high_30'):
        return {'position': 'unknown', 'breakout': None, 'distance_pts': 0, 'context': 'no OR data'}

    high = or_result['or_high_30']
    low  = or_result['or_low_30']

    if price > high:
        dist = round(price - high, 2)
        return {'position': 'above_or', 'breakout': 'OR_HIGH_BREAK',
                'distance_pts': dist, 'context': f'Price {dist}pts above OR high {high}'}
    if price < low:
        dist = round(low - price, 2)
        return {'position': 'below_or', 'breakout': 'OR_LOW_BREAK',
                'distance_pts': dist, 'context': f'Price {dist}pts below OR low {low}'}
    dist_h = round(high - price, 2)
    dist_l = round(price - low, 2)
    return {'position': 'inside_or', 'breakout': None,
            'distance_pts': min(dist_h, dist_l),
            'context': f'Price inside OR ({low}-{high})'}


def _empty_result() -> Dict:
    return {
        'or_high_15': 0.0, 'or_low_15': 0.0,
        'or_high_30': 0.0, 'or_low_30': 0.0,
        'or_mid_15': 0.0, 'or_mid_30': 0.0,
        'or_size_pts': 0.0, 'context': 'OR disabled or insufficient data',
    }
