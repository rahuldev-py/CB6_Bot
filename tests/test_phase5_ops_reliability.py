# tests/test_phase5_ops_reliability.py
#
# CB6 Quantum — Phase 5 Ops Reliability Tests
# Run: python -m pytest tests/test_phase5_ops_reliability.py -v

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── HeartbeatMonitor ──────────────────────────────────────────────────────────

class TestHeartbeatMonitor(unittest.TestCase):

    def setUp(self):
        # Use a temp dir so we don't touch real data/heartbeat/
        self._tmpdir = tempfile.mkdtemp()
        self._orig_hb_dir = None

    def _patch_hb_dir(self, module):
        module._HB_DIR = self._tmpdir

    def test_beat_writes_file(self):
        from utils import heartbeat_monitor as hm
        orig = hm._HB_DIR
        hm._HB_DIR = self._tmpdir
        try:
            hm.beat('test_engine', status='ok')
            path = os.path.join(self._tmpdir, 'test_engine.json')
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data['engine'], 'test_engine')
            self.assertEqual(data['status'], 'ok')
            self.assertIn('ts', data)
        finally:
            hm._HB_DIR = orig

    def test_read_heartbeat_returns_none_for_missing(self):
        from utils import heartbeat_monitor as hm
        orig = hm._HB_DIR
        hm._HB_DIR = self._tmpdir
        try:
            result = hm.read_heartbeat('nonexistent_engine')
            self.assertIsNone(result)
        finally:
            hm._HB_DIR = orig

    def test_check_all_marks_stale_when_no_file(self):
        from utils import heartbeat_monitor as hm
        orig_dir     = hm._HB_DIR
        orig_engines = hm._MONITORED_ENGINES
        hm._HB_DIR = self._tmpdir
        hm._MONITORED_ENGINES = ['test_stale_engine']
        try:
            results = hm.check_all(stale_after=180)
            self.assertTrue(results['test_stale_engine']['stale'])
            self.assertEqual(results['test_stale_engine']['status'], 'NO_HEARTBEAT')
        finally:
            hm._HB_DIR = orig_dir
            hm._MONITORED_ENGINES = orig_engines

    def test_check_all_marks_fresh_when_recent(self):
        from utils import heartbeat_monitor as hm
        orig_dir     = hm._HB_DIR
        orig_engines = hm._MONITORED_ENGINES
        hm._HB_DIR = self._tmpdir
        hm._MONITORED_ENGINES = ['fresh_engine']
        try:
            hm.beat('fresh_engine')
            results = hm.check_all(stale_after=180)
            self.assertFalse(results['fresh_engine']['stale'])
        finally:
            hm._HB_DIR = orig_dir
            hm._MONITORED_ENGINES = orig_engines

    def test_check_all_marks_stale_when_old(self):
        from utils import heartbeat_monitor as hm
        orig_dir     = hm._HB_DIR
        orig_engines = hm._MONITORED_ENGINES
        hm._HB_DIR = self._tmpdir
        hm._MONITORED_ENGINES = ['old_engine']
        try:
            # Write a heartbeat with a very old timestamp
            old_ts = int(time.time()) - 300
            path = os.path.join(self._tmpdir, 'old_engine.json')
            with open(path, 'w') as f:
                json.dump({'ts': old_ts, 'status': 'ok', 'engine': 'old_engine'}, f)
            results = hm.check_all(stale_after=60)
            self.assertTrue(results['old_engine']['stale'])
        finally:
            hm._HB_DIR = orig_dir
            hm._MONITORED_ENGINES = orig_engines

    def test_monitor_fires_alert_for_stale(self):
        from utils import heartbeat_monitor as hm
        orig_dir     = hm._HB_DIR
        orig_engines = hm._MONITORED_ENGINES
        hm._HB_DIR = self._tmpdir
        hm._MONITORED_ENGINES = ['stale_target']
        try:
            alerts = []
            mon = hm.HeartbeatMonitor(
                telegram_fn=alerts.append,
                stale_after=0,    # treat everything as stale immediately
                check_interval=1,
                engines=['stale_target'],
            )
            mon._check()
            self.assertEqual(len(alerts), 1)
            self.assertIn('stale_target', alerts[0])
        finally:
            hm._HB_DIR = orig_dir
            hm._MONITORED_ENGINES = orig_engines

    def test_monitor_clears_alert_after_recovery(self):
        from utils import heartbeat_monitor as hm
        orig_dir     = hm._HB_DIR
        orig_engines = hm._MONITORED_ENGINES
        hm._HB_DIR = self._tmpdir
        hm._MONITORED_ENGINES = ['recovering_engine']
        try:
            alerts = []
            mon = hm.HeartbeatMonitor(
                telegram_fn=alerts.append,
                stale_after=0,
                check_interval=1,
                engines=['recovering_engine'],
            )
            mon._check()   # fires alert — no file
            self.assertEqual(len(alerts), 1)
            # Write a fresh heartbeat
            hm.beat('recovering_engine')
            mon._check()   # should clear alert, no second Telegram message
            self.assertEqual(len(alerts), 1)   # still just 1 alert
        finally:
            hm._HB_DIR = orig_dir
            hm._MONITORED_ENGINES = orig_engines

    def test_beat_does_not_raise_on_bad_path(self):
        from utils import heartbeat_monitor as hm
        orig = hm._HB_DIR
        hm._HB_DIR = '/nonexistent/path/that/cannot/be/created'
        try:
            hm.beat('engine')  # must not raise
        finally:
            hm._HB_DIR = orig


# ── EngineWatchdog ────────────────────────────────────────────────────────────

class TestEngineWatchdog(unittest.TestCase):

    def test_register_and_track(self):
        from utils.engine_watchdog import EngineWatchdog
        wd = EngineWatchdog(max_restarts=3)
        wd.register('TEST_ENGINE', [sys.executable, '-c', 'import sys; sys.exit(0)'])
        self.assertIn('TEST_ENGINE', wd._specs)
        self.assertEqual(wd._restarts['TEST_ENGINE'], 0)

    def test_stop_without_start_safe(self):
        from utils.engine_watchdog import EngineWatchdog
        wd = EngineWatchdog()
        wd.stop()  # should not raise even with no procs

    def test_restart_on_crash(self):
        """Watchdog should detect process exit and relaunch it."""
        from utils.engine_watchdog import EngineWatchdog
        restarts = []

        wd = EngineWatchdog(
            max_restarts=2,
            on_restart=lambda name, cnt: restarts.append((name, cnt)),
        )
        # Command that exits immediately with code 1
        wd.register('CRASH_ENGINE', [sys.executable, '-c', 'import sys; sys.exit(1)'])
        wd.start_all()

        # Give watchdog loop time to detect and restart
        t = wd.run(background=True)
        deadline = time.time() + 8
        while time.time() < deadline and not restarts:
            time.sleep(0.2)
        wd.stop()

        self.assertGreater(len(restarts), 0)
        self.assertEqual(restarts[0][0], 'CRASH_ENGINE')

    def test_max_restarts_honoured(self):
        """Watchdog must stop restarting after max_restarts exceeded."""
        from utils.engine_watchdog import EngineWatchdog
        restarts = []
        max_r = 2

        wd = EngineWatchdog(
            max_restarts=max_r,
            on_restart=lambda name, cnt: restarts.append(cnt),
        )
        wd.register('EXHAUST_ENGINE', [sys.executable, '-c', 'import sys; sys.exit(1)'])
        wd.start_all()
        t = wd.run(background=True)
        deadline = time.time() + 20
        while time.time() < deadline and len(restarts) < max_r:
            time.sleep(0.3)
        wd.stop()

        self.assertLessEqual(len(restarts), max_r)


if __name__ == '__main__':
    unittest.main(verbosity=2)
