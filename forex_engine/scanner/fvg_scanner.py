# forex_engine/scanner/fvg_scanner.py
# Fair Value Gap detection and price-in-FVG check.

from typing import Optional
import pandas as pd
from utils.logger import logger


def detect_fvg(df: pd.DataFrame, direction: str, lookback: int = 25,
               displacement_mult: float = 1.0) -> Optional[dict]:
    """
    Find the most recent Fair Value Gap in the given direction.
    Returns {'fvg_low', 'fvg_high', 'mid', 'size', 'displacement'} or None.
    """
    try:
        from scanner.silver_bullet import detect_sb_fvg
        return detect_sb_fvg(df, direction, lookback=lookback,
                              displacement_mult=displacement_mult, use_range=True)
    except Exception as e:
        logger.error(f"detect_fvg error: {e}")
        return None


def price_in_fvg(fvg: dict, last_low: float, last_high: float) -> bool:
    """True if current price bar overlaps the FVG zone."""
    return last_low <= fvg['fvg_high'] and last_high >= fvg['fvg_low']


def price_near_fvg(fvg: dict, last_close: float, tolerance_pct: float = 0.005) -> bool:
    """True if price is within tolerance_pct of the FVG midpoint."""
    mid = fvg['mid']
    return abs(last_close - mid) / (mid + 1e-9) <= tolerance_pct


def fvg_size_ok(fvg: dict, min_size: float) -> bool:
    return fvg.get('size', 0) >= min_size
