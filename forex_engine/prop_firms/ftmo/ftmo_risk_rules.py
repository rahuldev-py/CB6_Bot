# forex_engine/prop_firms/ftmo/ftmo_risk_rules.py
# FTMO-specific risk rule checks — news block, rollover, best-day rule.

from datetime import datetime, timezone
from forex_engine.forex_instruments import FTMO_RULES, FTMO_RISK_GUARD


def check_best_day_rule(state: dict, starting: float = 10000.0,
                        mode: str = 'free_trial') -> tuple[bool, str]:
    """
    FTMO Best Day Rule: single day profit ≤ 50% of profit target.
    Returns (allowed, reason).
    """
    rules          = FTMO_RULES.get(mode, FTMO_RULES['free_trial'])
    profit_target  = starting * rules['profit_target_pct']  / 100
    best_day_limit = profit_target * rules['best_day_rule_pct'] / 100
    best_day_pnl   = state.get('best_day_pnl', 0.0)

    if best_day_pnl >= best_day_limit:
        return False, (
            f"FTMO Best Day Rule hit "
            f"(${best_day_pnl:.2f} of ${best_day_limit:.2f} max)"
        )
    return True, 'OK'


def check_daily_trade_limit(state: dict) -> tuple[bool, str]:
    """Max trades per day per FTMO_RULES."""
    if state.get('daily_trades', 0) >= FTMO_RULES['max_trades_per_day']:
        return False, f"Daily trade limit ({FTMO_RULES['max_trades_per_day']})"
    return True, 'OK'


def effective_risk_pct(base_pct: float, risk_mode: str,
                       boost_factor: float = 1.0) -> float:
    """
    Resolve the actual risk pct accounting for mode and A+ boost.
    Risk guard modes override boost — only normal mode can boost.
    """
    reduction = FTMO_RISK_GUARD.get('risk_reduction_factor', 0.5)
    if risk_mode in ('reduced', 'aplus_only'):
        return round(base_pct * reduction, 4)
    if boost_factor > 1.0 and risk_mode == 'normal':
        return round(base_pct * boost_factor, 4)
    return base_pct
