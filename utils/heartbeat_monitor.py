# utils/heartbeat_monitor.py
#
# CB6 Quantum — Engine Heartbeat Monitor
#
# Each engine writes a JSON heartbeat file every 60s:
#   data/heartbeat/{engine_name}.json  →  {"ts": <unix_epoch>, "status": "ok"}
#
# This monitor reads those files and fires a Telegram alert if any engine
# goes silent for > STALE_THRESHOLD_SECS (default 180s).
#
# Monitored engines:
#   nse_engine, gft_5k, gft_1k_instant, gft_10k,
#   telegram_nse, telegram_gft, db_writer, data_feed
#
# Usage (background thread from main launchers):
#   from utils.heartbeat_monitor import HeartbeatMonitor
#   mon = HeartbeatMonitor(telegram_fn=send_alert)
#   mon.start()   # daemon thread
#
# Engines write their heartbeat via:
#   from utils.heartbeat_monitor import beat
#   beat('gft_5k')

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from utils.logger import logger

_HB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'heartbeat')
STALE_THRESHOLD_SECS = 180    # alert after this many seconds of silence
CHECK_INTERVAL_SECS  = 60     # how often the monitor polls

_MONITORED_ENGINES = [
    'nse_engine',
    'gft_5k',
    'gft_1k_instant',
    'gft_10k',
    'telegram_nse',
    'telegram_gft',
    'db_writer',
    'data_feed',
]


# ─── Engine-side: write heartbeat ─────────────────────────────────────────────

def beat(engine_name: str, status: str = 'ok', extra: Optional[dict] = None) -> None:
    """
    Write a heartbeat file for `engine_name`.
    Call every ~60s from within each engine's main loop.
    Fails silently — never raises.
    """
    try:
        os.makedirs(_HB_DIR, exist_ok=True)
        payload = {
            'ts'    : int(time.time()),
            'status': status,
            'engine': engine_name,
        }
        if extra:
            payload.update(extra)
        path = os.path.join(_HB_DIR, f'{engine_name}.json')
        tmp  = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        os.replace(tmp, path)   # atomic write on Windows
    except Exception:
        pass


# ─── Monitor-side: read + alert ───────────────────────────────────────────────

def read_heartbeat(engine_name: str) -> Optional[dict]:
    """Read and return the last heartbeat dict for `engine_name`, or None."""
    try:
        path = os.path.join(_HB_DIR, f'{engine_name}.json')
        if not os.path.exists(path):
            return None
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def check_all(stale_after: int = STALE_THRESHOLD_SECS) -> Dict[str, dict]:
    """
    Check all monitored engines and return a status dict.

    Returns:
        {engine_name: {stale: bool, last_ts: int, age_secs: int, status: str}}
    """
    now = int(time.time())
    results = {}
    for name in _MONITORED_ENGINES:
        hb = read_heartbeat(name)
        if hb is None:
            results[name] = {
                'stale'   : True,
                'last_ts' : 0,
                'age_secs': stale_after + 1,   # treat missing as stale
                'status'  : 'NO_HEARTBEAT',
            }
        else:
            age = now - int(hb.get('ts', 0))
            results[name] = {
                'stale'   : age > stale_after,
                'last_ts' : hb.get('ts', 0),
                'age_secs': age,
                'status'  : hb.get('status', 'unknown'),
            }
    return results


class HeartbeatMonitor:
    """
    Background daemon thread that polls engine heartbeat files and fires
    Telegram alerts when any engine goes stale.
    """

    def __init__(
        self,
        telegram_fn: Optional[Callable[[str], None]] = None,
        stale_after: int = STALE_THRESHOLD_SECS,
        check_interval: int = CHECK_INTERVAL_SECS,
        engines: Optional[List[str]] = None,
    ):
        self._telegram_fn    = telegram_fn
        self._stale_after    = stale_after
        self._check_interval = check_interval
        self._engines        = engines or _MONITORED_ENGINES
        self._alerted: set   = set()   # engines that already sent an alert this session
        self._thread         = None

    def start(self) -> None:
        """Start background monitoring thread."""
        self._thread = threading.Thread(
            target=self._loop,
            name='heartbeat-monitor',
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"[HeartbeatMonitor] started — "
            f"stale_after={self._stale_after}s  check_every={self._check_interval}s  "
            f"engines={self._engines}"
        )

    def _loop(self) -> None:
        while True:
            try:
                self._check()
            except Exception as e:
                logger.warning(f"[HeartbeatMonitor] check error: {e}")
            time.sleep(self._check_interval)

    def _check(self) -> None:
        now    = int(time.time())
        stale_engines = []

        for name in self._engines:
            hb = read_heartbeat(name)
            if hb is None:
                age = self._stale_after + 1
            else:
                age = now - int(hb.get('ts', 0))

            if age > self._stale_after:
                stale_engines.append((name, age))
                if name not in self._alerted:
                    self._alerted.add(name)
                    logger.warning(
                        f"[HeartbeatMonitor] STALE: {name} "
                        f"(last beat {age}s ago, threshold {self._stale_after}s)"
                    )
            else:
                # Engine recovered — clear alert so it can fire again if it goes stale again
                self._alerted.discard(name)

        if stale_engines and self._telegram_fn:
            self._send_alert(stale_engines)

    def _send_alert(self, stale_engines: list) -> None:
        try:
            lines = []
            for name, age_secs in stale_engines:
                lines.append(f"  ❌ {name} — last beat {age_secs}s ago")
            msg = (
                "<b>CB6 QUANTUM — ENGINE STALE ALERT</b>\n\n"
                + '\n'.join(lines)
                + f"\n\nThreshold: {self._stale_after}s. Check logs."
            )
            self._telegram_fn(msg)
        except Exception as e:
            logger.error(f"[HeartbeatMonitor] Telegram alert failed: {e}")

    def status(self) -> Dict[str, dict]:
        """Return current heartbeat status for all monitored engines."""
        return check_all(stale_after=self._stale_after)
