# forex_engine/risk/exposure_guard.py
# Exposure guard — max open positions, per-symbol position limits.


def check_max_open_positions(state: dict, max_positions: int = 1) -> tuple[bool, str]:
    """Block if too many positions are already open."""
    open_count = len(state.get('open_trades', []))
    if open_count >= max_positions:
        return False, f"Max open positions reached ({open_count}/{max_positions})"
    return True, 'OK'


def check_position_exists(state: dict, symbol: str) -> tuple[bool, str]:
    """Block if a position for this symbol is already open."""
    for t in state.get('open_trades', []):
        if t.get('symbol') == symbol:
            return False, f"Position already open for {symbol}"
    return True, 'OK'


def total_exposure_usd(state: dict) -> float:
    """Sum of risk_usd across all open trades."""
    return round(
        sum(t.get('risk_usd', 0) for t in state.get('open_trades', [])), 2
    )


def max_exposure_pct(state: dict, limit_pct: float = 3.0) -> tuple[bool, str]:
    """Block if total open exposure exceeds limit_pct of capital."""
    capital  = state.get('capital', 1.0)
    exposure = total_exposure_usd(state)
    limit    = capital * limit_pct / 100
    if exposure >= limit:
        return False, (
            f"Total exposure ${exposure:.2f} ≥ limit ${limit:.2f} "
            f"({limit_pct}% of ${capital:.2f})"
        )
    return True, 'OK'
