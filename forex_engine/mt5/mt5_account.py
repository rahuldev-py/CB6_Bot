# forex_engine/mt5/mt5_account.py
# MT5 account info helpers — balance, equity, margin, account details.

from utils.logger import logger

try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False


def get_account_info() -> dict:
    if not _MT5_AVAILABLE:
        return {}
    try:
        info = mt5.account_info()
        if not info:
            return {}
        return {
            'login'     : info.login,
            'balance'   : float(info.balance),
            'equity'    : float(info.equity),
            'margin'    : float(info.margin),
            'free_margin': float(info.margin_free),
            'margin_level': float(info.margin_level),
            'profit'    : float(info.profit),
            'server'    : info.server,
            'currency'  : info.currency,
            'leverage'  : info.leverage,
            'name'      : info.name,
        }
    except Exception as e:
        logger.error(f"MT5 account_info error: {e}")
        return {}


def get_balance() -> float:
    info = get_account_info()
    return info.get('balance', 0.0)


def get_equity() -> float:
    info = get_account_info()
    return info.get('equity', 0.0)


def get_free_margin() -> float:
    info = get_account_info()
    return info.get('free_margin', 0.0)


def get_open_positions(symbol: str = None) -> list:
    """Return list of open MT5 positions, optionally filtered by symbol."""
    if not _MT5_AVAILABLE:
        return []
    try:
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not positions:
            return []
        result = []
        for p in positions:
            result.append({
                'ticket'    : p.ticket,
                'symbol'    : p.symbol,
                'type'      : 'BUY' if p.type == 0 else 'SELL',
                'volume'    : p.volume,
                'open_price': p.price_open,
                'sl'        : p.sl,
                'tp'        : p.tp,
                'profit'    : p.profit,
                'magic'     : p.magic,
                'comment'   : p.comment,
            })
        return result
    except Exception as e:
        logger.error(f"MT5 positions_get error: {e}")
        return []
