# utils/engine_watchdog.py
#
# CB6 Quantum — Windows-Compatible Engine Watchdog
#
# Monitors child subprocesses launched by forex_main.py and restarts them if
# they crash. Also writes a watchdog heartbeat file so HeartbeatMonitor can
# detect if the watchdog itself dies.
#
# Design:
#   - Works with subprocess.Popen objects (Windows-compatible, no signal tricks).
#   - Exponential backoff between restarts (5 → 15 → 30 → 60 → 120 → 300s).
#   - Hard max restarts per engine per session (default 10).
#   - Writes its own heartbeat via utils.heartbeat_monitor.beat('watchdog').
#   - Logs every restart event to audit_log.
#
# Usage:
#   wd = EngineWatchdog(max_restarts=10)
#   wd.register('GFT_5K', cmd=[sys.executable, '-m', 'forex_engine.forex_main', ...])
#   wd.start()   # blocks or runs in background thread

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

from utils.logger import logger

_ROOT = os.path.dirname(os.path.dirname(__file__))

_BACKOFF_STEPS = [5, 15, 30, 60, 120, 300]   # seconds


class EngineWatchdog:
    """
    Watches a set of named subprocess specs. Restarts any that exit unexpectedly.
    Writes heartbeat every 60s so HeartbeatMonitor can detect watchdog death.
    """

    def __init__(
        self,
        max_restarts: int = 10,
        env: Optional[dict] = None,
        heartbeat_interval: int = 60,
        on_restart: Optional[Callable[[str, int], None]] = None,
    ):
        self._max_restarts = max_restarts
        self._env          = env or os.environ.copy()
        self._hb_interval  = heartbeat_interval
        self._on_restart   = on_restart   # optional callback(engine_name, restart_count)

        self._specs: Dict[str, List[str]] = {}        # name → cmd list
        self._procs: Dict[str, subprocess.Popen] = {} # name → process
        self._restarts: Dict[str, int] = {}
        self._next_delay: Dict[str, int] = {}
        self._stopped = threading.Event()

    def register(self, name: str, cmd: List[str]) -> None:
        """Register an engine subprocess spec."""
        self._specs[name]      = cmd
        self._restarts[name]   = 0
        self._next_delay[name] = _BACKOFF_STEPS[0]

    def start_all(self) -> None:
        """Launch all registered engines."""
        for name, cmd in self._specs.items():
            self._launch(name, cmd)
            time.sleep(2)   # stagger launches

    def _launch(self, name: str, cmd: List[str]) -> None:
        try:
            logger.info(f"[Watchdog] starting {name}: {' '.join(cmd)}")
            self._procs[name] = subprocess.Popen(cmd, cwd=_ROOT, env=self._env)
            logger.info(f"[Watchdog] {name} PID={self._procs[name].pid}")
        except Exception as e:
            logger.error(f"[Watchdog] failed to launch {name}: {e}")

    def run(self, background: bool = False) -> Optional[threading.Thread]:
        """
        Start monitoring loop.
        - background=False: blocks the calling thread.
        - background=True : runs in a daemon thread; returns the thread.
        """
        if background:
            t = threading.Thread(target=self._loop, name='engine-watchdog', daemon=True)
            t.start()
            return t
        self._loop()
        return None

    def stop(self) -> None:
        """Signal watchdog to stop and terminate all children."""
        self._stopped.set()
        for name, proc in self._procs.items():
            if proc.poll() is None:
                logger.info(f"[Watchdog] terminating {name} PID={proc.pid}")
                proc.terminate()

    def _loop(self) -> None:
        _last_hb = 0.0
        while not self._stopped.is_set():
            # Write watchdog heartbeat
            now = time.time()
            if now - _last_hb > self._hb_interval:
                try:
                    from utils.heartbeat_monitor import beat
                    beat('watchdog', status='ok', extra={'engines': list(self._procs)})
                except Exception:
                    pass
                _last_hb = now

            for name in list(self._procs):
                proc = self._procs.get(name)
                if proc is None:
                    continue
                code = proc.poll()
                if code is None:
                    continue   # still running

                logger.warning(f"[Watchdog] {name} exited with code {code}")
                self._procs.pop(name, None)

                if self._restarts[name] >= self._max_restarts:
                    logger.error(
                        f"[Watchdog] {name} exceeded {self._max_restarts} "
                        "restarts — leaving stopped"
                    )
                    self._audit(name, code, exhausted=True)
                    continue

                delay = self._next_delay[name]
                self._restarts[name] += 1
                idx = min(self._restarts[name], len(_BACKOFF_STEPS) - 1)
                self._next_delay[name] = _BACKOFF_STEPS[idx]

                logger.info(
                    f"[Watchdog] restarting {name} "
                    f"#{self._restarts[name]}/{self._max_restarts} "
                    f"in {delay}s (next backoff {self._next_delay[name]}s)"
                )
                self._audit(name, code)

                if self._on_restart:
                    try:
                        self._on_restart(name, self._restarts[name])
                    except Exception:
                        pass

                time.sleep(delay)
                if not self._stopped.is_set():
                    self._launch(name, self._specs[name])

            time.sleep(5)

    def _audit(self, name: str, exit_code: int, exhausted: bool = False) -> None:
        try:
            from utils.audit_log import append as _audit
            _audit(
                'WATCHDOG_RESTART' if not exhausted else 'WATCHDOG_EXHAUSTED',
                name,
                'forex',
                exit_code=exit_code,
                restart_count=self._restarts.get(name, 0),
            )
        except Exception:
            pass
