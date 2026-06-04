# forex_engine/scanner/choch_bos.py
# CHoCH / BOS detection — wraps scanner.silver_bullet.detect_sb_mss.

from typing import Optional
import pandas as pd
from utils.logger import logger


def detect_mss(df: pd.DataFrame, lookback: int = 40) -> Optional[dict]:
    """
    Detect Market Structure Shift (CHoCH or BOS) on the given DataFrame.
    Returns {'direction', 'type', 'level', ...} or None.

    CHoCH = Change of Character (reversal — higher-conviction)
    BOS   = Break of Structure (continuation)
    """
    try:
        from scanner.silver_bullet import detect_sb_mss
        return detect_sb_mss(df, lookback=lookback)
    except Exception as e:
        logger.error(f"detect_mss error: {e}")
        return None


def is_choch(mss: Optional[dict]) -> bool:
    return mss is not None and mss.get('type') == 'CHOCH'


def is_bos(mss: Optional[dict]) -> bool:
    return mss is not None and mss.get('type') == 'BOS'
