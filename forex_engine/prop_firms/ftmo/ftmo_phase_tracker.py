# forex_engine/prop_firms/ftmo/ftmo_phase_tracker.py
# FTMO phase/mode tracking — free_trial vs challenge, phase summary.

import json
import os
from datetime import datetime
from utils.logger import logger
from utils.state_io import save_json_locked

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    'data', 'forex_paper_state.json'
)


def get_current_mode() -> str:
    """Read current FTMO mode from state file."""
    try:
        if not os.path.exists(_STATE_FILE):
            return 'free_trial'
        with open(_STATE_FILE) as f:
            state = json.load(f)
        return state.get('mode', 'free_trial')
    except Exception:
        return 'free_trial'


def set_mode(mode: str):
    """Update FTMO mode in state file (free_trial → challenge after passing)."""
    valid = ('free_trial', 'challenge')
    if mode not in valid:
        raise ValueError(f"Invalid FTMO mode: {mode}. Must be one of {valid}")
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE) as f:
                state = json.load(f)
        else:
            state = {}
        state['mode'] = mode
        save_json_locked(_STATE_FILE, state)
        logger.info(f"FTMO mode set to: {mode}")
    except Exception as e:
        logger.error(f"ftmo_phase_tracker.set_mode error: {e}")


def phase_summary(state: dict) -> dict:
    """Return human-readable phase progress summary."""
    from forex_engine.prop_firms.ftmo.ftmo_objectives import objective_progress
    mode    = state.get('mode', 'free_trial')
    start   = state.get('starting_capital', 10000.0)
    capital = state.get('capital', start)
    total   = state.get('total_pnl', 0.0)
    prog    = objective_progress(capital, total, mode, start)

    return {
        'mode'         : mode,
        'capital'      : capital,
        'total_pnl'    : total,
        'target_pnl'   : prog['profit_target'],
        'progress_pct' : prog['progress_pct'],
        'remaining'    : prog['remaining'],
        'is_passed'    : prog['is_passed'],
        'daily_pnl'    : state.get('daily_pnl', 0.0),
        'eod_peak'     : state.get('eod_equity_peak', start),
    }
