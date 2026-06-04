# forex_engine/mt5/mt5_symbol_info.py
# Symbol specification queries — contract size, pip value, margin requirements.

from utils.logger import logger
from forex_engine.forex_instruments import INSTRUMENTS

try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False


def get_symbol_info(symbol: str) -> dict:
    """
    Return instrument config from INSTRUMENTS dict.
    Falls back to MT5 symbol_info if available.
    """
    cfg = INSTRUMENTS.get(symbol, {})
    if not cfg and _MT5_AVAILABLE:
        try:
            info = mt5.symbol_info(cfg.get('mt5_symbol', symbol))
            if info:
                return {
                    'contract_size': info.trade_contract_size,
                    'min_lot'      : info.volume_min,
                    'max_lot'      : info.volume_max,
                    'lot_step'     : info.volume_step,
                    'point_size'   : info.point,
                }
        except Exception:
            pass
    return cfg


def pip_value(symbol: str, lots: float) -> float:
    """Dollar value of 1 pip for given lots."""
    cfg           = INSTRUMENTS.get(symbol, {})
    contract_size = cfg.get('contract_size', 100000)
    pip_size      = cfg.get('pip_size', 0.0001)
    return round(lots * contract_size * pip_size, 4)


def point_to_price(symbol: str, points: float) -> float:
    """Convert raw MT5 points to price units."""
    cfg = INSTRUMENTS.get(symbol, {})
    return points * cfg.get('point_size', 0.00001)


def margin_required(symbol: str, lots: float, price: float, leverage: int = 100) -> float:
    """Margin needed to hold a position at given leverage."""
    cfg = INSTRUMENTS.get(symbol, {})
    return round(lots * cfg.get('contract_size', 100000) * price / leverage, 2)
