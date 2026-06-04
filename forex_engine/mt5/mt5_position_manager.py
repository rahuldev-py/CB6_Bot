# forex_engine/mt5/mt5_position_manager.py
# Position lifecycle: track open positions, partial closes, SL trailing.

from utils.logger import logger
from forex_engine.mt5.mt5_account import get_open_positions


def get_position(connector, symbol: str, magic: int = None) -> dict | None:
    """
    Return the first open MT5 position for this symbol (optionally filtered by magic).
    Returns position dict or None.
    """
    positions = get_open_positions(symbol)
    if not positions:
        return None
    if magic is not None:
        positions = [p for p in positions if p.get('magic') == magic]
    return positions[0] if positions else None


def has_open_position(connector, symbol: str, magic: int = None) -> bool:
    return get_position(connector, symbol, magic) is not None


def trail_sl_to_breakeven(connector, symbol: str, ticket: int, entry_price: float) -> bool:
    """Move SL to entry (breakeven) for an open position."""
    return connector.modify_sl(symbol, ticket, entry_price)


def partial_close(connector, symbol: str, ticket: int, close_lots: float,
                  direction: str) -> bool:
    """Close a portion of an open position."""
    return connector.close_position(symbol, ticket, close_lots, direction)
