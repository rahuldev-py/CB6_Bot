# utils/emergency_stop.py
#
# Shared emergency-stop primitive for all CB6 Quantum engines.
#
# Three independent flags:
#   EMERGENCY_STOP.flag       — global: triggered by /stop Telegram, halts ALL engines
#   NSE_EMERGENCY_STOP.flag   — NSE-only: Fyers auth failure, does NOT halt forex
#   FOREX_EMERGENCY_STOP.flag — Forex-only: MT5 connection failure, does NOT halt NSE
#
# Activate:
#   - Telegram /stop command     → EMERGENCY_STOP.flag (global)
#   - Fyers auth failure         → NSE_EMERGENCY_STOP.flag (NSE only)
#   - MT5/forex runtime failure  → FOREX_EMERGENCY_STOP.flag (forex only)
# Deactivate:
#   - Telegram /resume → clears all three
#   - Manual: del data\EMERGENCY_STOP.flag

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_EMERGENCY_STOP_FLAG  = os.path.join(_ROOT, 'data', 'EMERGENCY_STOP.flag')
_NSE_STOP_FLAG        = os.path.join(_ROOT, 'data', 'NSE_EMERGENCY_STOP.flag')
_FOREX_STOP_FLAG      = os.path.join(_ROOT, 'data', 'FOREX_EMERGENCY_STOP.flag')


def is_emergency_stop_active() -> bool:
    """
    Returns True when the EMERGENCY_STOP.flag file exists.

    This is a pure file-system check — O(1), no I/O beyond stat().
    Safe to call on every tick / every candle close.
    """
    return os.path.exists(_EMERGENCY_STOP_FLAG)


def set_emergency_stop(reason: str = 'Manual') -> None:
    """Write the flag file (creates data/ dir if missing)."""
    try:
        os.makedirs(os.path.dirname(_EMERGENCY_STOP_FLAG), exist_ok=True)
        with open(_EMERGENCY_STOP_FLAG, 'w') as f:
            f.write(reason)
    except OSError as e:
        # Import lazily to avoid circular imports
        from utils.logger import logger
        logger.exception(f"CRITICAL: Could not write EMERGENCY_STOP flag — {e}")


def set_nse_emergency_stop(reason: str = 'NSE') -> None:
    """Write NSE-only stop flag. Does NOT halt the Forex engine."""
    try:
        os.makedirs(os.path.dirname(_NSE_STOP_FLAG), exist_ok=True)
        with open(_NSE_STOP_FLAG, 'w') as f:
            f.write(reason)
    except OSError as e:
        from utils.logger import logger
        logger.exception(f"CRITICAL: Could not write NSE_EMERGENCY_STOP flag — {e}")


def is_nse_emergency_stop_active() -> bool:
    return os.path.exists(_NSE_STOP_FLAG) or os.path.exists(_EMERGENCY_STOP_FLAG)


def set_forex_emergency_stop(reason: str = 'Forex') -> None:
    """Write Forex-only stop flag. Does NOT halt the NSE engine."""
    try:
        os.makedirs(os.path.dirname(_FOREX_STOP_FLAG), exist_ok=True)
        with open(_FOREX_STOP_FLAG, 'w') as f:
            f.write(reason)
    except OSError as e:
        from utils.logger import logger
        logger.exception(f"CRITICAL: Could not write FOREX_EMERGENCY_STOP flag — {e}")


def is_forex_emergency_stop_active() -> bool:
    return os.path.exists(_FOREX_STOP_FLAG) or os.path.exists(_EMERGENCY_STOP_FLAG)


def clear_emergency_stop() -> None:
    """Remove all three flag files (no-op if already absent)."""
    try:
        for flag in (_EMERGENCY_STOP_FLAG, _NSE_STOP_FLAG, _FOREX_STOP_FLAG):
            if os.path.exists(flag):
                os.remove(flag)
    except OSError as e:
        from utils.logger import logger
        logger.exception(f"Could not remove EMERGENCY_STOP flag — {e}")
