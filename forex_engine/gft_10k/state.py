import os
import threading
from datetime import datetime, timezone, timedelta

from utils.state_io import load_json_locked, save_json_locked
from forex_engine.gft_10k.config import GFT_10K_PROFILE

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STATE_DIR = os.path.join(_ROOT, GFT_10K_PROFILE["state_dir"].replace("/", os.sep))

STATE_FILE          = os.path.join(_STATE_DIR, "state.json")
STARTUP_ERROR_LOG   = os.path.join(_STATE_DIR, "startup_error.log")
HEARTBEAT_FILE      = os.path.join(_STATE_DIR, "heartbeat.txt")
DEDUP_FILE          = os.path.join(_STATE_DIR, "dedup.json")
LOCK_STATE_FILE     = os.path.join(_STATE_DIR, "lock_state.json")

_LOCK = threading.Lock()

DEFAULT_STATE = {
    "account_namespace" : "GFT_10K",
    "broker"            : "gft_10k",
    "capital"           : GFT_10K_PROFILE["account_size"],
    "starting_capital"  : GFT_10K_PROFILE["account_size"],
    "available_capital" : GFT_10K_PROFILE["account_size"],
    "daily_snapshot"    : GFT_10K_PROFILE["account_size"],
    "peak_capital"      : GFT_10K_PROFILE["account_size"],
    "open_trades"       : [],
    "closed_trades"     : [],
    "daily_trades"      : 0,
    "daily_pnl"         : 0.0,
    "last_reset_date"   : "",
    "paused"            : False,
}


def ensure_state_dir() -> str:
    os.makedirs(_STATE_DIR, exist_ok=True)
    return _STATE_DIR


def load_state() -> dict:
    ensure_state_dir()
    with _LOCK:
        state = load_json_locked(STATE_FILE, DEFAULT_STATE.copy())
    for key, value in DEFAULT_STATE.items():
        state.setdefault(key, value)
    return state


def save_state(state: dict) -> None:
    ensure_state_dir()
    with _LOCK:
        save_json_locked(STATE_FILE, state)


def reset_daily_if_needed(state: dict) -> dict:
    utc_now = datetime.now(timezone.utc)
    day_key = (utc_now - timedelta(hours=22)).strftime("%Y-%m-%d")
    if state.get("last_reset_date") != day_key:
        state["daily_snapshot"] = state.get("capital", GFT_10K_PROFILE["account_size"])
        state["daily_trades"]   = 0
        state["daily_pnl"]      = 0.0
        state["last_reset_date"] = day_key
        save_state(state)
    return state


def load_lock_state() -> dict:
    ensure_state_dir()
    default = {"locked": False, "dry_run": True, "updated_at": None, "reason": None}
    state = load_json_locked(LOCK_STATE_FILE, default.copy())
    for key, value in default.items():
        state.setdefault(key, value)
    return state


def save_lock_state(state: dict) -> None:
    ensure_state_dir()
    save_json_locked(LOCK_STATE_FILE, state)
