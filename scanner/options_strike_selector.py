# scanner/options_strike_selector.py
# Selects ATM strike from live spot price and builds the Fyers options symbol.

import logging
from utils.options_expiry import (
    atm_strike, build_fyers_option_symbol, direction_to_option_type,
    get_next_expiry, UNDERLYING_SYMBOL, LOT_SIZE,
)

logger = logging.getLogger(__name__)


def get_spot_price(fyers, index: str) -> float | None:
    """Fetch current spot price for the index from Fyers quotes API."""
    ul_sym = UNDERLYING_SYMBOL.get(index.upper())
    if not ul_sym:
        logger.error(f"No underlying symbol for {index}")
        return None

    try:
        resp = fyers.quotes({"symbols": ul_sym})
        if resp and resp.get('code') == 200:
            data = resp.get('d', [])
            if data and data[0].get('v'):
                lp = data[0]['v'].get('lp') or data[0]['v'].get('cmd', {}).get('c')
                if lp:
                    return float(lp)
        logger.warning(f"Fyers quotes returned unexpected response for {ul_sym}: {resp}")
    except Exception as e:
        logger.error(f"get_spot_price({index}) failed: {e}")
    return None


def select_atm_option(fyers, index: str, direction: str) -> dict | None:
    """
    Given a Silver Bullet signal (index + direction), return a dict with:
        symbol      : Fyers option symbol
        index       : e.g. 'NIFTY'
        strike      : ATM strike price (int)
        option_type : 'CE' or 'PE'
        expiry      : date object of next weekly expiry
        lot_size    : number of units per 1 lot
        spot        : live spot price used
    Returns None if spot fetch fails.
    """
    spot = get_spot_price(fyers, index)
    if spot is None:
        logger.warning(f"Cannot select ATM for {index} — spot unavailable")
        return None

    option_type = direction_to_option_type(direction)
    strike      = atm_strike(spot, index)
    expiry      = get_next_expiry(index)
    symbol      = build_fyers_option_symbol(index, expiry, strike, option_type)
    lot         = LOT_SIZE[index.upper()]

    logger.info(
        f"[OPTIONS] {index} {direction} → {option_type} ATM strike={strike} "
        f"expiry={expiry} symbol={symbol} lot={lot} spot={spot:.2f}"
    )

    return {
        'symbol'     : symbol,
        'index'      : index.upper(),
        'strike'     : strike,
        'option_type': option_type,
        'expiry'     : expiry,
        'lot_size'   : lot,
        'spot'       : spot,
    }


def get_option_ltp(fyers, symbol: str) -> float | None:
    """Fetch current LTP (Last Traded Price) for an options symbol."""
    try:
        resp = fyers.quotes({"symbols": symbol})
        if resp and resp.get('code') == 200:
            data = resp.get('d', [])
            if data and data[0].get('v'):
                lp = data[0]['v'].get('lp') or data[0]['v'].get('cmd', {}).get('c')
                if lp:
                    return float(lp)
        logger.warning(f"LTP fetch failed for {symbol}: {resp}")
    except Exception as e:
        logger.error(f"get_option_ltp({symbol}) failed: {e}")
    return None
