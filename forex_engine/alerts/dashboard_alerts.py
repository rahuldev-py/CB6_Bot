# forex_engine/alerts/dashboard_alerts.py
# Dashboard / terminal notifications for CB6 Quantum forex engine.
# These write to log and optionally to a shared status file for dashboard reads.

import json
import os
from datetime import datetime, timezone
from utils.logger import logger

_STATUS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'forex_dashboard_status.json'
)


def _write_status(payload: dict):
    try:
        os.makedirs(os.path.dirname(_STATUS_FILE), exist_ok=True)
        with open(_STATUS_FILE, 'w') as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"[DASHBOARD] Status write failed: {e}")


def notify_entry(setup: dict, lots: float, risk_usd: float, platform: str, ticket: int = 0):
    sym  = setup.get('symbol', '?')
    dire = setup.get('direction', '?')
    conf = setup.get('confluence', 0)
    sig  = setup.get('entry_signal', {})

    logger.info(
        f"[{platform}] ENTRY {sym} {dire} | lots={lots} risk=${risk_usd:.2f} "
        f"conf={conf}/15 entry={sig.get('entry','?')} sl={sig.get('stop_loss','?')} "
        f"ticket={ticket}"
    )
    _write_status({
        'event'    : 'entry',
        'platform' : platform,
        'symbol'   : sym,
        'direction': dire,
        'lots'     : lots,
        'risk_usd' : risk_usd,
        'confluence': conf,
        'ticket'   : ticket,
        'ts'       : datetime.now(timezone.utc).isoformat(),
    })


def notify_exit(event: dict, platform: str):
    t    = event.get('trade', {})
    sym  = t.get('symbol', '?')
    pnl  = event.get('pnl', 0.0)
    etype = event.get('type', '?')

    sign = '+' if pnl >= 0 else ''
    logger.info(f"[{platform}] EXIT {sym} {etype} | pnl={sign}${pnl:.2f}")
    _write_status({
        'event'    : 'exit',
        'platform' : platform,
        'symbol'   : sym,
        'exit_type': etype,
        'pnl'      : pnl,
        'ts'       : datetime.now(timezone.utc).isoformat(),
    })


def notify_risk_mode(mode: str, reason: str, platform: str):
    logger.warning(f"[{platform}] RISK MODE → {mode.upper()} | {reason}")
    _write_status({
        'event'    : 'risk_mode',
        'platform' : platform,
        'mode'     : mode,
        'reason'   : reason,
        'ts'       : datetime.now(timezone.utc).isoformat(),
    })


def notify_phase(phase: str, capital: float, profit: float, platform: str):
    logger.info(f"[{platform}] PHASE ADVANCE → {phase} | capital=${capital:.2f} profit=+${profit:.2f}")
    _write_status({
        'event'    : 'phase_advance',
        'platform' : platform,
        'phase'    : phase,
        'capital'  : capital,
        'profit'   : profit,
        'ts'       : datetime.now(timezone.utc).isoformat(),
    })


def notify_blocked(reason: str, symbol: str, platform: str):
    logger.debug(f"[{platform}] BLOCKED {symbol} — {reason}")


def notify_kill_switch(activated: bool, platform: str):
    state = 'ACTIVATED' if activated else 'DEACTIVATED'
    logger.critical(f"[{platform}] KILL SWITCH {state}")
    _write_status({
        'event'    : 'kill_switch',
        'platform' : platform,
        'state'    : state,
        'ts'       : datetime.now(timezone.utc).isoformat(),
    })
