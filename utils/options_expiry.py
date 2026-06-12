# utils/options_expiry.py
# NSE weekly options expiry calculator.
#
# Expiry days (NSE schedule as of 2026):
#   NIFTY       : Thursday
#   BANKNIFTY   : Wednesday
#   FINNIFTY    : Tuesday
#   MIDCPNIFTY  : Monday

from datetime import datetime, date, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')

# Weekday index: Monday=0 ... Sunday=6
EXPIRY_WEEKDAY = {
    'NIFTY'     : 3,  # Thursday
    'BANKNIFTY' : 2,  # Wednesday
    'FINNIFTY'  : 1,  # Tuesday
    'MIDCPNIFTY': 0,  # Monday
}

# Strike intervals (points)
STRIKE_STEP = {
    'NIFTY'     : 50,
    'BANKNIFTY' : 100,
    'FINNIFTY'  : 50,
    'MIDCPNIFTY': 25,
}

# Lot sizes
LOT_SIZE = {
    'NIFTY'     : 25,
    'BANKNIFTY' : 15,
    'FINNIFTY'  : 40,
    'MIDCPNIFTY': 75,
}

# Fyers underlying index symbols
UNDERLYING_SYMBOL = {
    'NIFTY'     : 'NSE:NIFTY50-INDEX',
    'BANKNIFTY' : 'NSE:NIFTYBANK-INDEX',
    'FINNIFTY'  : 'NSE:FINNIFTY-INDEX',
    'MIDCPNIFTY': 'NSE:NIFTYMIDCAP150-INDEX',
}


def get_next_expiry(index: str, as_of: date = None) -> date:
    """
    Return the next (or same-day) weekly expiry date for the given index.
    If today IS the expiry day and it's before 15:15 IST → return today.
    If today IS the expiry day and it's after 15:15 IST → return next cycle.
    """
    target_wd = EXPIRY_WEEKDAY.get(index.upper(), 3)  # default Thursday

    now_ist = datetime.now(IST)
    today   = as_of or now_ist.date()

    # Walk forward from today to find next occurrence of target weekday
    for delta in range(8):
        candidate = today + timedelta(days=delta)
        if candidate.weekday() == target_wd:
            # If it's today: check if market already closed (after 15:15 IST)
            if delta == 0 and now_ist.hour * 60 + now_ist.minute >= 15 * 60 + 15:
                continue  # skip today's expired expiry — look next week
            return candidate

    # Fallback: shouldn't reach here
    return today + timedelta(days=7)


def build_fyers_option_symbol(index: str, expiry: date,
                               strike: int, option_type: str) -> str:
    """
    Build Fyers symbol string for a weekly option.
    Format: NSE:{INDEX}{YY}{MM}{DD}{STRIKE}{CE/PE}
    Example: NSE:NIFTY260612{strike}PE
    """
    idx = index.upper()
    yy  = expiry.strftime('%y')   # '26'
    mm  = expiry.strftime('%m')   # '06'
    dd  = expiry.strftime('%d')   # '12'
    ot  = option_type.upper()     # 'CE' or 'PE'
    return f"NSE:{idx}{yy}{mm}{dd}{strike}{ot}"


def atm_strike(spot: float, index: str) -> int:
    """Round spot price to nearest ATM strike for the given index."""
    step = STRIKE_STEP.get(index.upper(), 50)
    return int(round(spot / step) * step)


def direction_to_option_type(direction: str) -> str:
    """BULLISH → CE (Call) | BEARISH → PE (Put)."""
    return 'CE' if direction == 'BULLISH' else 'PE'
