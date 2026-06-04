# forex_engine/prop_firms/gft/gft_phase_tracker.py
# GFT 2-Step phase lifecycle management — state persistence and phase transitions.

import json
import os
import shutil
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional
from utils.logger import logger
from utils.state_io import load_json_locked, save_json_locked
from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE

_P    = GFT_2STEP_PROFILE
_LOCK = threading.Lock()

# gft_phase_tracker.py is 4 directories deep from project root:
#   C:\cb6_bot\forex_engine\prop_firms\gft\gft_phase_tracker.py
#   4x dirname  → C:\cb6_bot
_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_STATE_FILE = os.path.join(_ROOT, _P['state_file'].replace('/', os.sep))

# ── One-time migration from legacy paths ────────────────────────────────────────
# Check multiple possible legacy locations (original code had a path bug that
# stored state at C:\data\ instead of C:\cb6_bot\data\)
_LEGACY_CANDIDATES = [
    os.path.join(_ROOT, _P.get('legacy_state_file', 'data/gft_2step_state.json').replace('/', os.sep)),
    os.path.join('C:\\', 'data', 'gft_5k', 'state.json'),        # from old wrong-root migration
    os.path.join('C:\\', 'data', 'gft_2step_state.json'),         # original pre-existing wrong path
]
_LEGACY_STATE_FILE = next((p for p in _LEGACY_CANDIDATES if os.path.exists(p)), None)
if not os.path.exists(_STATE_FILE) and _LEGACY_STATE_FILE:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        shutil.copy2(_LEGACY_STATE_FILE, _STATE_FILE)
        logger.info(f"GFT state migrated: {_LEGACY_STATE_FILE} → {_STATE_FILE}")
    except Exception as _e:
        logger.warning(f"GFT state migration failed (non-fatal): {_e}")

_DEFAULT_STATE = {
    'capital'            : _P['account_size'],
    'available_capital'  : _P['account_size'],
    'starting_capital'   : _P['account_size'],
    'current_phase'      : 'phase_1',
    'phase_start_capital': _P['account_size'],
    'phase_1_passed'     : False,
    'phase_1_passed_at'  : None,
    'phase_2_passed'     : False,
    'phase_2_passed_at'  : None,
    'open_trades'        : [],
    'closed_trades'      : [],
    'daily_trades'       : 0,
    'daily_losses'       : 0,
    'daily_pnl'          : 0.0,
    'daily_closed_pnl'   : 0.0,
    'best_day_pnl'       : 0.0,
    'gft_daily_snapshot' : _P['account_size'],  # 5PM EST equity snapshot
    'last_reset_date'    : '',
    'total_pnl'          : 0.0,
    'peak_capital'       : _P['account_size'],
    'paused'             : False,
    'risk_mode'          : 'normal',
    'broker'             : 'gft_2step',
    # ── GFT evaluation requirement: min 3 trading days per phase ──────────────
    # Incremented at daily reset when daily_trades > 0 (meaning a trade happened today).
    # Resets to 0 when phase advances.
    'trading_days_active': 0,
}


def load_state() -> dict:
    try:
        state = load_json_locked(_STATE_FILE, _DEFAULT_STATE.copy())
        for k, v in _DEFAULT_STATE.items():
            if k not in state:
                state[k] = v
        return state
    except Exception:
        return _DEFAULT_STATE.copy()


def _save(state: dict):
    try:
        save_json_locked(_STATE_FILE, state)
    except Exception as e:
        logger.error(f"GFT 2-Step state save error: {e}")


def reset_daily_if_needed(state: dict) -> dict:
    """
    GFT resets daily counters at 5PM EST = 22:00 UTC.
    Also snapshots the equity at that time as the new daily baseline.
    """
    utc_now = datetime.now(timezone.utc)
    day_key = (utc_now - timedelta(hours=22)).strftime('%Y-%m-%d')

    if state.get('last_reset_date') != day_key:
        # Count this as an active trading day if at least 1 trade happened
        if state.get('daily_trades', 0) > 0:
            state['trading_days_active'] = state.get('trading_days_active', 0) + 1
            logger.info(
                f"GFT: trading day counted — "
                f"total active days = {state['trading_days_active']} "
                f"(need {_P.get('min_trading_days', 3)} for phase completion)"
            )

        state['gft_daily_snapshot'] = state.get('capital', _P['account_size'])
        state['daily_trades']       = 0
        state['daily_losses']       = 0
        state['daily_pnl']          = 0.0
        state['daily_closed_pnl']   = 0.0
        state['best_day_pnl']       = 0.0
        state['last_reset_date']    = day_key
        _save(state)
    return state


def advance_phase_if_complete(state: dict) -> tuple[dict, bool, str]:
    """
    Check if current phase is complete and advance if so.
    Returns (updated_state, advanced, message).
    """
    from forex_engine.prop_firms.gft.gft_objectives import check_phase_completion, advance_phase
    completed, msg = check_phase_completion(state)
    if completed:
        state = advance_phase(state)
        _save(state)
        return state, True, msg
    return state, False, msg


def get_summary(state: dict = None) -> dict:
    """Return a summary dict for display/alerts."""
    if state is None:
        state = load_state()
    from forex_engine.prop_firms.gft.gft_objectives import phase_progress
    prog = phase_progress(state)
    return {
        'capital'       : state.get('capital', _P['account_size']),
        'phase'         : state.get('current_phase', 'phase_1'),
        'progress'      : prog,
        'daily_pnl'     : state.get('daily_pnl', 0.0),
        'total_pnl'     : state.get('total_pnl', 0.0),
        'open_trades'   : len(state.get('open_trades', [])),
        'daily_trades'  : state.get('daily_trades', 0),
        'risk_mode'     : state.get('risk_mode', 'normal'),
    }
