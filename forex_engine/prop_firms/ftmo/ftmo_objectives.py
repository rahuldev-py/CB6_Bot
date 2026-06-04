# forex_engine/prop_firms/ftmo/ftmo_objectives.py
# FTMO objective tracking — progress toward profit target.

from forex_engine.forex_instruments import FTMO_RULES


def get_objectives(mode: str = 'free_trial', starting: float = 10000.0) -> dict:
    """Return objective thresholds for a given FTMO mode."""
    rules = FTMO_RULES.get(mode, FTMO_RULES['free_trial'])
    profit_target    = starting * rules['profit_target_pct']  / 100
    max_daily_loss   = starting * rules['max_daily_loss_pct'] / 100
    max_total_dd     = starting * rules['max_total_dd_pct']   / 100
    best_day_limit   = profit_target * rules['best_day_rule_pct'] / 100

    return {
        'mode'           : mode,
        'account_size'   : starting,
        'profit_target'  : profit_target,
        'max_daily_loss' : max_daily_loss,
        'max_total_dd'   : max_total_dd,
        'best_day_limit' : best_day_limit,
        'trading_days'   : rules.get('trading_days'),
    }


def objective_progress(capital: float, total_pnl: float,
                        mode: str = 'free_trial',
                        starting: float = 10000.0) -> dict:
    """Return progress metrics vs FTMO objectives."""
    obj    = get_objectives(mode, starting)
    profit = round(total_pnl, 2)
    pct    = round(profit / obj['profit_target'] * 100, 1) if obj['profit_target'] > 0 else 0.0

    return {
        'profit_earned'   : profit,
        'profit_target'   : obj['profit_target'],
        'progress_pct'    : pct,
        'remaining'       : round(obj['profit_target'] - profit, 2),
        'is_passed'       : profit >= obj['profit_target'],
        'daily_loss_limit': obj['max_daily_loss'],
        'total_dd_limit'  : obj['max_total_dd'],
        'best_day_limit'  : obj['best_day_limit'],
    }
