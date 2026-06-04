# forex_engine/prop_firms/gft/gft_risk_rules.py
# GFT 2-Step risk rule aggregator — combines all guard results into a single mode.

from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE
from forex_engine.prop_firms.gft.gft_daily_loss_guard import (
    official_daily_loss_ok, get_internal_risk_mode as daily_mode
)
from forex_engine.prop_firms.gft.gft_max_loss_guard import (
    official_max_loss_ok, get_internal_risk_mode as total_mode
)
from forex_engine.prop_firms.gft.gft_symbol_guard import is_allowed
from forex_engine.prop_firms.gft.gft_anti_hedge_guard import check_no_hedge, check_no_same_symbol

_P = GFT_2STEP_PROFILE


def get_risk_mode(state: dict) -> tuple[str, str]:
    """
    Aggregate risk mode for GFT 2-Step.
    Returns (mode, reason). mode: 'normal' | 'reduced' | 'warning' | 'paused'
    """
    # Official limits first (hard stops)
    ok, reason = official_daily_loss_ok(state)
    if not ok:
        return ('paused', reason)

    ok, reason = official_max_loss_ok(state)
    if not ok:
        return ('paused', reason)

    # Daily profit cap
    daily_profit = state.get('daily_closed_pnl', 0.0)
    profit_cap   = 3000.0  # GFT daily profit cap
    if daily_profit >= profit_cap:
        return ('paused', f"Daily profit cap ${daily_profit:.2f} ≥ ${profit_cap:.0f}")

    # Internal daily tiers
    mode, reason = daily_mode(state)
    if mode in ('paused', 'reduced', 'warning'):
        return mode, reason

    # Internal total DD tiers
    mode, reason = total_mode(state)
    if mode in ('paused', 'reduced', 'warning'):
        return mode, reason

    return ('normal', 'OK')


def can_open_trade(state: dict, symbol: str, direction: str) -> tuple[bool, str]:
    """
    Full pre-trade gate for GFT 2-Step.
    Returns (allowed, reason).
    """
    if state.get('paused'):
        return False, 'Engine paused'

    # Phase must not be 'funded' (funded accounts use different rules)
    if state.get('current_phase') == 'funded':
        pass   # funded accounts can still trade — no evaluation rules

    # Max open positions
    open_count = len(state.get('open_trades', []))
    if open_count >= _P['max_open_positions']:
        return False, f"Max open positions ({open_count}/{_P['max_open_positions']})"

    # Daily trade limit
    if state.get('daily_trades', 0) >= _P['max_trades_per_day']:
        return False, f"Daily trade limit ({_P['max_trades_per_day']})"

    # Symbol guard
    ok, reason = is_allowed(symbol)
    if not ok:
        return False, reason

    # Anti-hedge
    ok, reason = check_no_hedge(state, symbol, direction)
    if not ok:
        return False, reason

    # Anti-same-symbol overload
    ok, reason = check_no_same_symbol(state, symbol, max_positions=1)
    if not ok:
        return False, reason

    # Official limits + internal guards
    mode, reason = get_risk_mode(state)
    if mode == 'paused':
        return False, reason

    return True, 'OK'
