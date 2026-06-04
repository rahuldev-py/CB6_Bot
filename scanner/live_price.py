# scanner/live_price.py — Real-time LTP (TrueData primary, Fyers fallback)
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger


def _td_ltp(symbol: str) -> "float | None":
    """Get LTP from TrueData live feed cache. Returns None if feed is not up."""
    try:
        from data.truedata_feed import get_ltp
        return get_ltp(symbol)
    except Exception:
        return None


def get_live_price(fyers, symbol):
    """
    Fetch last traded price. Tries TrueData live feed first, falls back to Fyers.
    Returns float LTP or None on failure.
    """
    ltp = _td_ltp(symbol)
    if ltp is not None:
        return ltp

    try:
        resp = fyers.quotes({"symbols": symbol})
        if resp.get('code') == 200 or resp.get('s') == 'ok':
            items = resp.get('d', [])
            if items:
                ltp = items[0].get('v', {}).get('lp')
                if ltp:
                    return float(ltp)
    except Exception as e:
        logger.debug(f"Fyers live price error {symbol}: {e}")
    return None


def get_live_prices(fyers, symbols):
    """
    Batch fetch LTPs. TrueData fills any symbol it knows; Fyers covers the rest.
    Returns dict {symbol: price}.
    """
    result: dict = {}

    # TrueData pass
    remaining = []
    for sym in symbols:
        ltp = _td_ltp(sym)
        if ltp is not None:
            result[sym] = ltp
        else:
            remaining.append(sym)

    if not remaining:
        return result

    # Fyers fallback for anything TrueData didn't cover
    try:
        chunk_size = 10
        for i in range(0, len(remaining), chunk_size):
            batch   = remaining[i:i + chunk_size]
            sym_str = ','.join(batch)
            resp    = fyers.quotes({"symbols": sym_str})
            if resp.get('code') == 200 or resp.get('s') == 'ok':
                for item in resp.get('d', []):
                    sym = item.get('n', '')
                    ltp = item.get('v', {}).get('lp')
                    if sym and ltp:
                        result[sym] = float(ltp)
    except Exception as e:
        logger.debug(f"Fyers batch live price error: {e}")

    return result
