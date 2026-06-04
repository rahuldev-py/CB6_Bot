# forex_engine/risk/drawdown_guard.py
# Drawdown tracking — EOD peak advancement, DD percentage display.

from datetime import datetime, timezone, timedelta


def advance_eod_peak(state: dict) -> dict:
    """
    Advance FTMO EOD equity peak at end of trading day.
    Called during daily reset. Updates eod_equity_peak if capital grew.
    """
    current = state.get('capital', state.get('starting_capital', 10000.0))
    old_peak = state.get('eod_equity_peak', state.get('starting_capital', 10000.0))
    state['eod_equity_peak'] = max(old_peak, current)
    return state


def current_drawdown_pct(capital: float, peak: float) -> float:
    """Drawdown as percentage of peak capital."""
    if peak <= 0:
        return 0.0
    return round((peak - capital) / peak * 100, 2)


def dd_floor_remaining_pct(capital: float, eod_peak: float,
                           dd_limit_pct: float = 10.0, starting: float = 10000.0) -> float:
    """
    How much % of the DD limit is still available (0% = at floor, 100% = full room).
    """
    dd_limit_usd = starting * dd_limit_pct / 100
    dd_floor     = eod_peak - dd_limit_usd
    if dd_limit_usd <= 0:
        return 100.0
    dd_used = max(0.0, dd_floor - capital) + max(0.0, capital - dd_floor)
    room    = capital - dd_floor
    return round(max(0.0, room / dd_limit_usd * 100), 1)


def reset_daily_if_needed(state: dict, broker: str = 'ftmo') -> dict:
    """
    Reset daily counters when the date (or GFT day) changes.
    Advances the FTMO EOD peak and GFT snapshot on reset.
    """
    if broker.startswith('gft'):
        utc_now = datetime.now(timezone.utc)
        day_key = (utc_now - timedelta(hours=22)).strftime('%Y-%m-%d')
    else:
        day_key = datetime.now().strftime('%Y-%m-%d')

    if state.get('last_reset_date') != day_key:
        advance_eod_peak(state)
        state['gft_daily_snapshot'] = state.get('capital', 10000.0)
        state['daily_trades']       = 0
        state['daily_losses']       = 0
        state['daily_pnl']          = 0.0
        state['daily_closed_pnl']   = 0.0
        state['best_day_pnl']       = 0.0
        state['last_reset_date']    = day_key
    return state
