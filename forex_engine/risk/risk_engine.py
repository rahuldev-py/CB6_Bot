# forex_engine/risk/risk_engine.py
# Risk engine — aggregates all guard checks into a single risk mode decision.

from forex_engine.risk.daily_loss_guard import check_daily_loss, check_daily_profit
from forex_engine.risk.max_loss_guard import check_total_drawdown
from forex_engine.risk.exposure_guard import check_max_open_positions
from forex_engine.risk.emergency_kill_switch import is_killed
from forex_engine.forex_instruments import FTMO_RISK_GUARD


def get_risk_mode(state: dict) -> tuple[str, str]:
    """
    Master risk gate — returns (mode, reason).
    mode: 'normal' | 'reduced' | 'aplus_only' | 'paused'

    Priority (worst → best):
      paused     → kill switch / DD stop / daily loss stop / profit cap / best-day cap
      aplus_only → A+ gate DD / daily loss A+ tier
      reduced    → 50% lots (DD reduce / daily loss reduce / profit reduce)
      normal     → full operation
    """
    # Kill switch
    if is_killed():
        return ('paused', 'Emergency kill switch active')

    start   = state.get('starting_capital', 10000.0)
    capital = state.get('capital', start)
    daily   = state.get('daily_pnl', 0.0)

    # Daily loss tiers
    mode, reason = check_daily_loss(daily, start)
    if mode != 'normal':
        return mode, reason

    # Total DD tiers
    mode, reason = check_total_drawdown(capital, start)
    if mode != 'normal':
        return mode, reason

    # Daily profit protection
    mode, reason = check_daily_profit(daily, start)
    if mode != 'normal':
        return mode, reason

    # Best Day consistency guard
    from datetime import datetime
    today  = datetime.now().strftime('%Y-%m-%d')
    closed = state.get('closed_trades', [])
    today_profit = sum(
        t.get('pnl_usd', 0) for t in closed
        if (t.get('exit_time') or '')[:10] == today and t.get('pnl_usd', 0) > 0
    )
    total_pos = sum(t.get('pnl_usd', 0) for t in closed if t.get('pnl_usd', 0) > 0)
    if total_pos > 0 and today_profit > 0:
        contribution = today_profit / total_pos * 100
        max_pct      = FTMO_RISK_GUARD.get('best_day_max_pct', 45.0)
        if contribution >= max_pct:
            return ('paused',
                    f"Best Day {contribution:.1f}% ≥ {max_pct:.0f}% limit — maintain consistency")

    return ('normal', 'OK')


def can_open_trade(state: dict, platform: str = 'ftmo') -> tuple[bool, str]:
    """
    Final gate before opening any trade. Platform-aware.
    Returns (allowed, reason).
    """
    if state.get('paused'):
        return False, 'Engine paused'

    # Max open positions
    ok, reason = check_max_open_positions(state, max_positions=1)
    if not ok:
        return False, reason

    risk_mode, risk_reason = get_risk_mode(state)
    if risk_mode == 'paused':
        return False, f"RISK GUARD — {risk_reason}"

    starting = state.get('starting_capital', 10000.0)

    if platform.startswith('gft'):
        return _can_open_gft(state, starting)
    return _can_open_ftmo(state, starting)


def _can_open_ftmo(state: dict, starting: float) -> tuple[bool, str]:
    from forex_engine.forex_instruments import FTMO_RULES
    mode  = state.get('mode', 'free_trial')
    rules = FTMO_RULES.get(mode, FTMO_RULES['free_trial'])

    # Official FTMO daily loss: 3% = $300
    daily_limit = starting * rules['max_daily_loss_pct'] / 100
    if state.get('daily_pnl', 0) <= -daily_limit:
        return False, f"FTMO daily loss limit (${abs(state.get('daily_pnl',0)):.2f} of ${daily_limit:.2f})"

    # FTMO EOD trailing floor
    dd_limit = starting * rules['max_total_dd_pct'] / 100
    eod_peak = state.get('eod_equity_peak', starting)
    dd_floor = eod_peak - dd_limit
    if state.get('capital', starting) <= dd_floor:
        return False, f"FTMO EOD DD floor (equity ≤ ${dd_floor:.2f})"

    # Best Day Rule
    if rules.get('profit_target_pct') and rules.get('best_day_rule_pct'):
        pt_limit = starting * rules['profit_target_pct']  / 100
        bd_limit = pt_limit * rules['best_day_rule_pct'] / 100
        if state.get('best_day_pnl', 0) >= bd_limit:
            return False, f"FTMO Best Day Rule (${state.get('best_day_pnl',0):.2f} of ${bd_limit:.2f})"

    # Daily trade limit
    if state.get('daily_trades', 0) >= FTMO_RULES['max_trades_per_day']:
        return False, f"Daily trade limit ({FTMO_RULES['max_trades_per_day']})"

    return True, 'OK'


def _can_open_gft(state: dict, starting: float) -> tuple[bool, str]:
    from forex_engine.forex_instruments import GFT_RULES
    model = '1_step'
    rules = GFT_RULES[model]

    # GFT daily loss — relative to 5PM EST snapshot
    snapshot  = state.get('gft_daily_snapshot', starting)
    dl_limit  = snapshot * rules['max_daily_loss_pct'] / 100
    daily_loss = snapshot - state.get('capital', starting)
    if daily_loss >= dl_limit:
        return False, f"GFT daily loss limit (${daily_loss:.2f} of ${dl_limit:.2f})"

    # GFT static floor
    floor = starting * (1 - rules['max_total_dd_pct'] / 100)
    if state.get('capital', starting) <= floor:
        return False, f"GFT static floor breached (equity ≤ ${floor:.2f})"

    # GFT daily profit cap
    if state.get('daily_closed_pnl', 0) >= rules.get('daily_profit_cap', 3000):
        return False, f"GFT daily profit cap (${state.get('daily_closed_pnl',0):.2f})"

    # Trade limit
    if state.get('daily_trades', 0) >= GFT_RULES['max_trades_per_day']:
        return False, f"GFT daily trade limit ({GFT_RULES['max_trades_per_day']})"

    return True, 'OK'
