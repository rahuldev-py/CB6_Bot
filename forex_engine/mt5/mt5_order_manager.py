# forex_engine/mt5/mt5_order_manager.py
# High-level order management — place, close, modify with validation.

from utils.logger import logger

try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False


def place_order(connector, symbol: str, direction: str, lots: float,
                sl: float, tp: float = 0.0, magic: int = 62002,
                comment: str = 'CB6_Quantum') -> dict | None:
    """
    Place a market order via the connector.
    Returns {'ticket', 'price', 'symbol', 'direction', 'lots'} or None.
    """
    return connector.place_market_order(symbol, direction, lots, sl, tp, magic)


def close_order(connector, symbol: str, ticket: int, lots: float,
                direction: str) -> bool:
    """Close an open position."""
    return connector.close_position(symbol, ticket, lots, direction)


def modify_stop_loss(connector, symbol: str, ticket: int, new_sl: float) -> bool:
    """Trail stop loss to a new level."""
    return connector.modify_sl(symbol, ticket, new_sl)


def close_all_positions(connector, symbol: str = None) -> int:
    """
    Emergency: close all open positions (optionally filtered by symbol).
    Returns count of positions closed.
    """
    from forex_engine.mt5.mt5_account import get_open_positions
    positions = get_open_positions(symbol)
    closed = 0
    for pos in positions:
        ok = connector.close_position(
            pos['symbol'], pos['ticket'], pos['volume'], pos['type']
        )
        if ok:
            closed += 1
            logger.info(f"Emergency close: {pos['symbol']} ticket={pos['ticket']}")
        else:
            logger.error(f"Emergency close FAILED: {pos['symbol']} ticket={pos['ticket']}")
    return closed
