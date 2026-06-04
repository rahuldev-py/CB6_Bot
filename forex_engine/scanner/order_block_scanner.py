# forex_engine/scanner/order_block_scanner.py
# Order block detection — institutional supply/demand zones.

from typing import Optional
import pandas as pd
from utils.logger import logger


def detect_ob(df: pd.DataFrame, direction: str, lookback: int = 40) -> Optional[dict]:
    """
    Find the last opposing candle before the displacement (institutional OB).
    Returns {'type', 'ob_low', 'ob_high', 'ob_mid'} or None.

    BULL_OB = demand zone (entry on pullback to OB for longs)
    BEAR_OB = supply zone (entry on rally to OB for shorts)
    """
    try:
        from scanner.silver_bullet import detect_order_block
        return detect_order_block(df, direction, lookback=lookback)
    except Exception as e:
        logger.error(f"detect_ob error: {e}")
        return None


def price_in_ob(ob: dict, current_price: float) -> bool:
    """True if current price is inside the order block zone."""
    return ob['ob_low'] <= current_price <= ob['ob_high']
