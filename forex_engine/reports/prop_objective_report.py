# forex_engine/reports/prop_objective_report.py
# Progress report toward FTMO and GFT prop-firm objectives.

from datetime import datetime


def ftmo_report(state: dict) -> dict:
    """Progress toward FTMO free trial $500 target."""
    from forex_engine.forex_instruments import FTMO_RULES
    from forex_engine.prop_firms.ftmo.ftmo_max_loss import official_max_loss_ok, max_loss_remaining
    from forex_engine.prop_firms.ftmo.ftmo_daily_loss import official_daily_loss_ok, daily_loss_remaining

    starting    = state.get('starting_capital', 10000.0)
    capital     = state.get('capital', starting)
    daily_pnl   = state.get('daily_pnl', 0.0)
    total_pnl   = state.get('total_pnl', 0.0)
    eod_peak    = state.get('eod_equity_peak', starting)
    mode        = state.get('mode', 'free_trial')
    daily_trades= state.get('daily_trades', 0)

    rules  = FTMO_RULES.get(mode, FTMO_RULES['free_trial'])
    target = starting * rules['profit_target_pct'] / 100

    max_ok, max_reason     = official_max_loss_ok(capital, eod_peak, starting, mode)
    daily_ok, daily_reason = official_daily_loss_ok(daily_pnl, starting, mode)

    return {
        'platform'        : 'FTMO',
        'profit_target'   : round(target, 2),
        'total_pnl'       : round(total_pnl, 2),
        'progress_pct'    : round(total_pnl / target * 100, 1) if target > 0 else 0,
        'remaining_usd'   : round(max(0, target - total_pnl), 2),
        'capital'         : round(capital, 2),
        'daily_pnl'       : round(daily_pnl, 2),
        'daily_trades'    : daily_trades,
        'max_loss_ok'     : max_ok,
        'max_loss_reason' : max_reason,
        'daily_loss_ok'   : daily_ok,
        'daily_loss_reason': daily_reason,
        'max_loss_room'   : round(max_loss_remaining(capital, eod_peak, starting, mode), 2),
        'daily_loss_room' : round(daily_loss_remaining(daily_pnl, starting, mode), 2),
        'risk_mode'       : state.get('risk_mode', 'normal'),
    }


def gft_report(state: dict) -> dict:
    """Progress toward GFT 2-Step phase targets."""
    from forex_engine.prop_firms.gft.gft_objectives import phase_progress
    from forex_engine.prop_firms.gft.gft_max_loss_guard import official_max_loss_ok, max_loss_remaining
    from forex_engine.prop_firms.gft.gft_daily_loss_guard import official_daily_loss_ok, daily_loss_remaining
    from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE

    _P = GFT_2STEP_PROFILE
    phase       = state.get('current_phase', 'phase_1')
    capital     = state.get('capital', _P['account_size'])
    daily_pnl   = state.get('daily_pnl', 0.0)
    total_pnl   = state.get('total_pnl', 0.0)
    prog        = phase_progress(state)

    max_ok, max_reason   = official_max_loss_ok(state)
    daily_ok, daily_reason = official_daily_loss_ok(state)

    return {
        'platform'        : 'GFT-2STEP',
        'current_phase'   : phase,
        'phase_1_passed'  : state.get('phase_1_passed', False),
        'phase_2_passed'  : state.get('phase_2_passed', False),
        'phase_target_usd': prog.get('target_usd', 0),
        'phase_profit'    : round(prog.get('profit_earned', 0), 2),
        'phase_progress_pct': round(prog.get('progress_pct', 0), 1),
        'phase_remaining_usd': round(prog.get('remaining', 0), 2),
        'capital'         : round(capital, 2),
        'total_pnl'       : round(total_pnl, 2),
        'daily_pnl'       : round(daily_pnl, 2),
        'max_loss_ok'     : max_ok,
        'max_loss_reason' : max_reason,
        'daily_loss_ok'   : daily_ok,
        'daily_loss_reason': daily_reason,
        'max_loss_room'   : round(max_loss_remaining(state), 2),
        'daily_loss_room' : round(daily_loss_remaining(state), 2),
        'risk_mode'       : state.get('risk_mode', 'normal'),
        'open_trades'     : len(state.get('open_trades', [])),
        'daily_trades'    : state.get('daily_trades', 0),
    }


def print_ftmo_report(state: dict = None):
    if state is None:
        from forex_engine.forex_paper_trader import load_state
        state = load_state()
    r = ftmo_report(state)
    print(f"\n{'='*50}")
    print(f"FTMO FREE TRIAL PROGRESS")
    print(f"{'='*50}")
    print(f"Capital     : ${r['capital']:.2f}")
    print(f"Total PnL   : ${r['total_pnl']:+.2f}  ({r['progress_pct']}% of ${r['profit_target']})")
    print(f"Remaining   : ${r['remaining_usd']:.2f} to target")
    print(f"Daily PnL   : ${r['daily_pnl']:+.2f}  |  Trades: {r['daily_trades']}")
    print(f"Max Loss Room : ${r['max_loss_room']:.2f}")
    print(f"Daily Loss Room: ${r['daily_loss_room']:.2f}")
    print(f"Risk Mode   : {r['risk_mode'].upper()}")
    if not r['max_loss_ok']:
        print(f"  ⚠️  {r['max_loss_reason']}")
    if not r['daily_loss_ok']:
        print(f"  ⚠️  {r['daily_loss_reason']}")
    print(f"{'='*50}")


def print_gft_report(state: dict = None):
    if state is None:
        from forex_engine.prop_firms.gft.gft_phase_tracker import load_state
        state = load_state()
    r = gft_report(state)
    print(f"\n{'='*50}")
    print(f"GFT 2-STEP GOAT PROGRESS")
    print(f"{'='*50}")
    print(f"Phase       : {r['current_phase'].upper()}")
    print(f"Capital     : ${r['capital']:.2f}")
    print(f"Phase Profit: ${r['phase_profit']:+.2f} / ${r['phase_target_usd']:.2f}  ({r['phase_progress_pct']}%)")
    print(f"Remaining   : ${r['phase_remaining_usd']:.2f} to pass phase")
    print(f"Phase 1     : {'✅ PASSED' if r['phase_1_passed'] else '⏳ pending'}")
    print(f"Phase 2     : {'✅ PASSED' if r['phase_2_passed'] else '⏳ pending'}")
    print(f"Daily PnL   : ${r['daily_pnl']:+.2f}  |  Trades today: {r['daily_trades']}")
    print(f"Open Trades : {r['open_trades']}")
    print(f"Max Loss Room : ${r['max_loss_room']:.2f}")
    print(f"Daily Loss Room: ${r['daily_loss_room']:.2f}")
    print(f"Risk Mode   : {r['risk_mode'].upper()}")
    if not r['max_loss_ok']:
        print(f"  ⚠️  {r['max_loss_reason']}")
    if not r['daily_loss_ok']:
        print(f"  ⚠️  {r['daily_loss_reason']}")
    print(f"{'='*50}")
