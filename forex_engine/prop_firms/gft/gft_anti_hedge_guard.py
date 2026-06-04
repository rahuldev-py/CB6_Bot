# forex_engine/prop_firms/gft/gft_anti_hedge_guard.py
# GFT Anti-Hedge guard — no opposite positions on same symbol in same account.

from utils.logger import logger


def check_no_hedge(state: dict, symbol: str, direction: str) -> tuple[bool, str]:
    """
    Block if there's an open position in the opposite direction for this symbol.
    Returns (allowed, reason).
    """
    for trade in state.get('open_trades', []):
        if trade.get('symbol') != symbol:
            continue
        existing_dir = trade.get('direction', '')
        if existing_dir and existing_dir != direction:
            return False, (
                f"ANTI-HEDGE BLOCK — {symbol} already has open {existing_dir} position. "
                f"Cannot open {direction} on same symbol."
            )
    return True, 'OK'


def check_no_same_symbol(state: dict, symbol: str,
                          max_positions: int = 1) -> tuple[bool, str]:
    """
    Block if max_positions are already open for this symbol.
    """
    count = sum(1 for t in state.get('open_trades', []) if t.get('symbol') == symbol)
    if count >= max_positions:
        return False, (
            f"SYMBOL LIMIT — {count} open position(s) for {symbol} "
            f"(max {max_positions})"
        )
    return True, 'OK'
