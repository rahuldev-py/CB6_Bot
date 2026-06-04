# forex_engine/prop_firms/gft/gft_objectives.py
# GFT 2-Step objective tracking — phase progress, completion detection.

from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE


def phase_progress(state: dict) -> dict:
    """Return current phase progress metrics."""
    profile  = GFT_2STEP_PROFILE
    phase    = state.get('current_phase', 'phase_1')
    start    = state.get('phase_start_capital', profile['account_size'])
    capital  = state.get('capital', start)
    total    = round(capital - start, 2)

    # 'funded' is a terminal state — no further target
    if phase == 'funded':
        return {
            'phase'          : 'funded',
            'phase_label'    : 'Funded — evaluation complete',
            'start_capital'  : start,
            'current_capital': capital,
            'profit_earned'  : total,
            'target_usd'     : 0.0,
            'progress_pct'   : 100.0,
            'remaining'      : 0.0,
            'is_passed'      : True,
            'next_phase'     : None,
        }

    target  = profile[phase]['target_usd']
    pct     = round(total / target * 100, 1) if target > 0 else 0.0
    is_done = total >= target

    return {
        'phase'          : phase,
        'phase_label'    : profile[phase]['label'],
        'start_capital'  : start,
        'current_capital': capital,
        'profit_earned'  : total,
        'target_usd'     : target,
        'progress_pct'   : pct,
        'remaining'      : round(target - total, 2),
        'is_passed'      : is_done,
        'next_phase'     : 'phase_2' if phase == 'phase_1' else 'funded',
    }


def check_phase_completion(state: dict) -> tuple[bool, str]:
    """
    Returns (completed, message).
    True only when BOTH conditions are met:
      1. Profit target reached (≥ phase target USD)
      2. Minimum trading days logged (GFT requirement: 3 days per phase)
    """
    prog          = phase_progress(state)
    min_days      = GFT_2STEP_PROFILE.get('min_trading_days', 3)
    active_days   = state.get('trading_days_active', 0)
    profit_ok     = prog['is_passed']
    days_ok       = active_days >= min_days

    if profit_ok and days_ok:
        return True, (
            f"{prog['phase_label']} COMPLETED — "
            f"earned ${prog['profit_earned']:.2f} ≥ target ${prog['target_usd']:.2f} "
            f"over {active_days} trading days"
        )

    if profit_ok and not days_ok:
        return False, (
            f"Target reached (${prog['profit_earned']:.2f}) but only {active_days}/{min_days} "
            f"trading days logged — GFT requires {min_days} minimum. Keep trading."
        )

    return False, (
        f"Progress: ${prog['profit_earned']:.2f} / ${prog['target_usd']:.2f} "
        f"({prog['progress_pct']:.1f}%) | Days: {active_days}/{min_days}"
    )


def advance_phase(state: dict) -> dict:
    """
    Move to next phase when current phase is complete.
    Resets phase_start_capital and trading_days_active counter for the new phase.
    """
    current = state.get('current_phase', 'phase_1')
    if current == 'phase_1':
        state['current_phase']       = 'phase_2'
        state['phase_start_capital'] = state.get('capital', GFT_2STEP_PROFILE['account_size'])
        state['phase_1_passed']      = True
        state['phase_1_passed_at']   = __import__('datetime').datetime.now().isoformat()
        state['trading_days_active'] = 0   # reset for Phase 2 day count
    elif current == 'phase_2':
        state['current_phase']       = 'funded'
        state['phase_start_capital'] = state.get('capital', GFT_2STEP_PROFILE['account_size'])
        state['phase_2_passed']      = True
        state['phase_2_passed_at']   = __import__('datetime').datetime.now().isoformat()
        state['trading_days_active'] = 0   # reset on funded
    return state
