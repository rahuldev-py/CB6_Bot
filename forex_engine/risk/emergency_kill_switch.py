# forex_engine/risk/emergency_kill_switch.py
# Emergency kill switch — manually pause trading, circuit breaker.

import os
import json
from datetime import datetime
from utils.logger import logger

_KILL_FLAG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'forex_kill_switch.json'
)


def is_killed() -> bool:
    """True if manual kill switch is active."""
    try:
        if not os.path.exists(_KILL_FLAG_FILE):
            return False
        with open(_KILL_FLAG_FILE) as f:
            data = json.load(f)
        return bool(data.get('killed', False))
    except Exception:
        # REQ-5: Log read failures — fail-safe (return False = assume not killed)
        logger.warning(
            f"kill_switch.is_killed: could not read flag file {_KILL_FLAG_FILE} "
            "— assuming NOT killed (fail-safe)", exc_info=True
        )
        return False


def activate(reason: str = 'Manual kill switch'):
    """Activate the kill switch — stops all new entries."""
    try:
        os.makedirs(os.path.dirname(_KILL_FLAG_FILE), exist_ok=True)
        with open(_KILL_FLAG_FILE, 'w') as f:
            json.dump({
                'killed'    : True,
                'reason'    : reason,
                'activated' : datetime.now().isoformat(),
            }, f, indent=2)
        logger.warning(f"KILL SWITCH ACTIVATED: {reason}")
    except Exception as e:
        logger.error(f"Kill switch activate error: {e}")


def deactivate():
    """Deactivate the kill switch — resume trading."""
    try:
        if os.path.exists(_KILL_FLAG_FILE):
            os.remove(_KILL_FLAG_FILE)
        logger.info("Kill switch deactivated — trading resumed")
    except Exception as e:
        logger.error(f"Kill switch deactivate error: {e}")


def get_status() -> dict:
    try:
        if not os.path.exists(_KILL_FLAG_FILE):
            return {'killed': False}
        with open(_KILL_FLAG_FILE) as f:
            return json.load(f)
    except Exception:
        # REQ-5: Log read failures — return safe default
        logger.warning(
            f"kill_switch.get_status: could not read {_KILL_FLAG_FILE}",
            exc_info=True
        )
        return {'killed': False}
