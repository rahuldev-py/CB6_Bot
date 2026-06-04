# forex_engine/risk/max_loss_guard.py
# Total max drawdown guard — trailing EOD floor (FTMO) or static floor (GFT).

from forex_engine.forex_instruments import FTMO_RISK_GUARD


def check_total_drawdown(capital: float, starting_capital: float) -> tuple[str, str]:
    """
    Returns (mode, reason) based on total DD from starting capital.
    FTMO internal guard (not the official EOD trailing floor — that's in can_open_trade).
    """
    g        = FTMO_RISK_GUARD
    total_dd = round(starting_capital - capital, 2)

    dd_stop = round(starting_capital * g['total_dd_stop_pct']  / 100, 2)
    dd_ap   = round(starting_capital * g['total_dd_aplus_pct'] / 100, 2)
    dd_red  = round(starting_capital * g['total_dd_reduce_pct']/ 100, 2)

    if total_dd >= dd_stop:
        return ('paused',
                f"Total DD ${total_dd:.2f} ≥ stop gate ${dd_stop:.2f} — trading halted")
    if total_dd >= dd_ap:
        return ('aplus_only',
                f"Total DD ${total_dd:.2f} ≥ A+ gate ${dd_ap:.2f} — elite setups only")
    if total_dd >= dd_red:
        return ('reduced',
                f"Total DD ${total_dd:.2f} ≥ reduce gate ${dd_red:.2f} — 50% lots")
    return ('normal', 'OK')


def check_ftmo_eod_floor(capital: float, eod_peak: float, starting: float,
                          dd_pct: float = 10.0) -> tuple[bool, str]:
    """
    True if equity is above the FTMO EOD trailing DD floor.
    Returns (allowed, reason).
    """
    limit    = starting * dd_pct / 100
    dd_floor = eod_peak - limit
    if capital <= dd_floor:
        return False, (
            f"FTMO EOD trailing DD limit hit "
            f"(equity ${capital:.2f} ≤ floor ${dd_floor:.2f} "
            f"= peak ${eod_peak:.2f} − ${limit:.0f})"
        )
    return True, 'OK'


def check_gft_static_floor(capital: float, starting: float,
                            dd_pct: float = 10.0) -> tuple[bool, str]:
    """
    GFT uses a static (never-moves) loss floor.
    Returns (allowed, reason).
    """
    floor = starting * (1 - dd_pct / 100)
    if capital <= floor:
        return False, (
            f"GFT static DD floor breached "
            f"(equity ${capital:.2f} ≤ floor ${floor:.2f})"
        )
    return True, 'OK'
