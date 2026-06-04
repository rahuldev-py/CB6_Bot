# forex_engine/scanner/structure_scanner.py
# Higher-timeframe bias — H1 and H4 EMA trend detection.

from typing import Optional
from utils.logger import logger


def get_htf_bias(connector, symbol: str, interval: str,
                 fast_span: int = 3, slow_span: int = 8,
                 band_pct: float = 0.0002) -> str:
    """
    Generic HTF EMA bias on any timeframe.
    Returns 'BULLISH', 'BEARISH', or 'RANGING'.
    """
    try:
        df = connector.get_klines(symbol, interval, 20)
        if df is None or len(df) < 10:
            return 'RANGING'
        c    = df['close']
        fast = c.ewm(span=fast_span, adjust=False).mean().iloc[-1]
        slow = c.ewm(span=slow_span, adjust=False).mean().iloc[-1]
        if fast > slow * (1 + band_pct):
            return 'BULLISH'
        if fast < slow * (1 - band_pct):
            return 'BEARISH'
        return 'RANGING'
    except Exception:
        return 'RANGING'


def get_h1_bias(connector, symbol: str) -> str:
    """
    H1 trend bias via EMA(3) vs EMA(8).
    Band 0.02% — filters noise, avoids ranging misclassification.
    """
    return get_htf_bias(connector, symbol, '1h',
                        fast_span=3, slow_span=8, band_pct=0.0002)


def get_h4_bias(connector, symbol: str) -> str:
    """
    H4 multi-day bias via EMA(3) vs EMA(8).
    Wider band (0.03%) — H4 has more noise than H1.
    """
    return get_htf_bias(connector, symbol, '4h',
                        fast_span=3, slow_span=8, band_pct=0.0003)
