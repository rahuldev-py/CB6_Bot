# forex_engine/prop_firms/ftmo/ftmo_daily_loss.py
# FTMO daily loss — official limit check and internal guard tiers.

from forex_engine.forex_instruments import FTMO_RULES, FTMO_RISK_GUARD


def official_daily_loss_ok(daily_pnl: float, starting: float = 10000.0,
                            mode: str = 'free_trial') -> tuple[bool, str]:
    """
    Check against official FTMO 3% daily loss limit ($300 on $10K).
    This is the hard stop — account would be terminated if breached.
    """
    rules = FTMO_RULES.get(mode, FTMO_RULES['free_trial'])
    limit = starting * rules['max_daily_loss_pct'] / 100
    if daily_pnl <= -limit:
        return False, (
            f"FTMO official daily loss limit hit "
            f"(${abs(daily_pnl):.2f} of ${limit:.2f})"
        )
    return True, 'OK'


def daily_loss_remaining(daily_pnl: float, starting: float = 10000.0,
                          mode: str = 'free_trial') -> float:
    """How much daily loss budget remains (positive = still have room)."""
    rules = FTMO_RULES.get(mode, FTMO_RULES['free_trial'])
    limit = starting * rules['max_daily_loss_pct'] / 100
    return round(limit + daily_pnl, 2)   # daily_pnl is negative on loss


def internal_daily_stop_amount(starting: float = 10000.0) -> float:
    """Internal daily stop threshold (before official FTMO limit)."""
    return round(starting * FTMO_RISK_GUARD['daily_loss_stop_pct'] / 100, 2)
