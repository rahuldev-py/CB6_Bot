# forex_engine/mt5/mt5_error_handler.py
# MT5 error codes and retry utilities.

import time
import functools
from utils.logger import logger


class MT5ConnectionError(Exception):
    pass


class MT5OrderError(Exception):
    pass


# Common MT5 retcodes that indicate a retryable condition
RETRYABLE_RETCODES = {
    10004,  # TRADE_RETCODE_REQUOTE
    10006,  # TRADE_RETCODE_REJECT
    10014,  # TRADE_RETCODE_INVALID_VOLUME
    10021,  # TRADE_RETCODE_NO_MONEY — temporary (margin)
    10024,  # TRADE_RETCODE_TOO_MANY_REQUESTS
    10025,  # TRADE_RETCODE_NO_CHANGES
    10030,  # TRADE_RETCODE_SERVER_DISABLES_AT
}

MT5_RETCODE_MESSAGES = {
    10004: 'Requote',
    10006: 'Request rejected',
    10007: 'Request canceled by trader',
    10008: 'Order placed',
    10009: 'Request completed',
    10010: 'Only part of the request was completed',
    10013: 'Invalid request',
    10014: 'Invalid volume in the request',
    10015: 'Invalid price in the request',
    10016: 'Invalid stops in the request',
    10017: 'Trade is disabled',
    10018: 'Market is closed',
    10019: 'Not enough money to complete the request',
    10020: 'Prices changed',
    10021: 'No quotes to process the request',
    10024: 'Too many requests',
    10025: 'No changes in request',
    10027: 'AutoTrading disabled by server',
    10028: 'AutoTrading disabled by client terminal',
    10029: 'Request locked for processing',
    10030: 'Order or position frozen',
}


def describe_retcode(retcode: int) -> str:
    return MT5_RETCODE_MESSAGES.get(retcode, f'Unknown retcode {retcode}')


def with_retry(max_attempts: int = 3, delay_secs: float = 2.0):
    """Decorator: retry a function on MT5ConnectionError, with exponential backoff."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except MT5ConnectionError as e:
                    if attempt == max_attempts:
                        raise
                    wait = delay_secs * attempt
                    logger.warning(f"MT5 retry {attempt}/{max_attempts} for {fn.__name__}: {e} — waiting {wait}s")
                    time.sleep(wait)
            return None
        return wrapper
    return decorator
