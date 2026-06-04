# forex_engine/scanner/ut_confirmation.py
# UT Bot signal — higher-timeframe trend confirmation.

from typing import Optional
import pandas as pd
from utils.logger import logger


def get_ut_signal(df: pd.DataFrame) -> dict:
    """
    Get UT Bot trend signal from the scanner module.
    Returns {'trend', 'stop', 'signal', 'bars_in_trend', 'aligned'}.
    'aligned' is set externally by comparing trend vs trade direction.
    """
    try:
        from scanner.ut_bot import get_ut_signal as _get
        result = _get(df)
        result.setdefault('aligned', None)
        return result
    except Exception as e:
        logger.error(f"get_ut_signal error: {e}")
        return {'trend': None, 'stop': None, 'signal': None,
                'bars_in_trend': 0, 'aligned': None}


def ut_aligned(df: pd.DataFrame, direction: str) -> bool:
    """True if UT Bot trend aligns with trade direction."""
    ut = get_ut_signal(df)
    return ut.get('trend') == direction
