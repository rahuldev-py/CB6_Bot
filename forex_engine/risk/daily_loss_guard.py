# forex_engine/risk/daily_loss_guard.py
# Daily loss guard — tiered response to daily PnL drawdown.

from forex_engine.forex_instruments import FTMO_RISK_GUARD


def check_daily_loss(daily_pnl: float, starting_capital: float) -> tuple[str, str]:
    """
    Returns (mode, reason) based on daily_pnl.
    mode: 'normal' | 'reduced' | 'aplus_only' | 'paused'
    """
    g       = FTMO_RISK_GUARD
    start   = starting_capital
    dl_stop = round(start * g['daily_loss_stop_pct']  / 100, 2)
    dl_ap   = round(start * g['daily_loss_aplus_pct'] / 100, 2)
    dl_red  = round(start * g['daily_loss_reduce_pct']/ 100, 2)

    if daily_pnl <= -dl_stop:
        return ('paused',
                f"Daily loss ${abs(daily_pnl):.2f} ≥ stop gate ${dl_stop:.2f} — no more entries today")
    if daily_pnl <= -dl_ap:
        return ('aplus_only',
                f"Daily loss ${abs(daily_pnl):.2f} ≥ A+ gate ${dl_ap:.2f} — elite setups only")
    if daily_pnl <= -dl_red:
        return ('reduced',
                f"Daily loss ${abs(daily_pnl):.2f} ≥ reduce gate ${dl_red:.2f} — 50% lots")
    return ('normal', 'OK')


def check_daily_profit(daily_pnl: float, starting_capital: float) -> tuple[str, str]:
    """
    Profit protection — slow down / stop after good days to protect gains.
    """
    g      = FTMO_RISK_GUARD
    start  = starting_capital
    pp_stop = round(start * g['daily_profit_stop_pct']  / 100, 2)
    pp_red  = round(start * g['daily_profit_reduce_pct']/ 100, 2)

    if daily_pnl >= pp_stop:
        return ('paused',
                f"Daily profit ${daily_pnl:.2f} ≥ protect stop ${pp_stop:.2f} — locking in gains")
    if daily_pnl >= pp_red:
        return ('reduced',
                f"Daily profit ${daily_pnl:.2f} ≥ protect reduce ${pp_red:.2f} — 50% lots")
    return ('normal', 'OK')


def daily_loss_used_pct(daily_pnl: float, starting_capital: float) -> float:
    """What % of the daily loss limit has been consumed."""
    if daily_pnl >= 0 or starting_capital <= 0:
        return 0.0
    limit = starting_capital * FTMO_RISK_GUARD['daily_loss_stop_pct'] / 100
    return round(abs(daily_pnl) / limit * 100, 1)
