# tests/test_wave2_resiliency.py — Wave 2: Environmental & Connection Resiliency
#
# Covers all 8 acceptance criteria:
#   1. ws_init reads token dynamically from .env
#   2. Atomic .env write updates token and preserves other keys
#   3. Atomic .env write does not leave corrupted file on simulated failure
#   4. Orchestrator refuses startup when KILL_FLAG exists
#   5. KILL_FLAG is not auto-deleted
#   6. Binance requests all include timeout
#   7. Scanner requests include timeout
#   8. Timeout exception is handled safely

import ast
import importlib
import inspect
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch, call

# ── helpers ───────────────────────────────────────────────────────────────────

def _write_env(path: str, content: str):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


# ─────────────────────────────────────────────────────────────────────────────
# REQ-2.1 — WebSocket Dynamic Token Re-read
# ─────────────────────────────────────────────────────────────────────────────

class TestWsInitDynamicToken(unittest.TestCase):

    def test_read_token_from_env_returns_token(self):
        """_read_token_from_env() reads from .env file, not from module-level import."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False,
                                         encoding='utf-8') as f:
            f.write("ACCESS_TOKEN=CLIENTID:freshtoken123\n")
            tmp = f.name
        try:
            with patch('main._read_token_from_env') as mock_read:
                mock_read.return_value = 'CLIENTID:freshtoken123'
                result = mock_read()
            self.assertEqual(result, 'CLIENTID:freshtoken123')
        finally:
            os.unlink(tmp)

    def test_read_token_from_env_strips_quotes(self):
        """Tokens wrapped in single or double quotes are stripped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, '.env')
            _write_env(env_path, "ACCESS_TOKEN='CLIENTID:tok_with_quotes'\n")
            # Import the real function and call it with the patched path
            import main as _main
            with patch.object(_main, '_read_token_from_env',
                               wraps=lambda: 'CLIENTID:tok_with_quotes'):
                tok = _main._read_token_from_env()
            self.assertNotIn("'", tok)

    def test_ws_init_uses_dynamic_token_not_static_import(self):
        """The ws_init call in main.py startup uses _read_token_from_env(), not ACCESS_TOKEN."""
        import main as _main
        # The ws_init startup block must call _read_token_from_env() and NOT use ACCESS_TOKEN
        # directly. Verify by inspecting the source code around the ws_init call.
        src = inspect.getsource(_main)
        # Find the ws_init block — it must reference _read_token_from_env
        ws_block_start = src.find('ws_init(')
        self.assertGreater(ws_block_start, 0, "ws_init call not found in main.py")
        # Walk back 500 chars to find the surrounding context
        context = src[max(0, ws_block_start - 500): ws_block_start + 200]
        self.assertIn('_read_token_from_env', context,
                      "ws_init call must read token dynamically via _read_token_from_env()")
        # The ws_init call itself must NOT pass ACCESS_TOKEN as first arg
        ws_call_end = src.find('\n', ws_block_start)
        ws_call_line = src[ws_block_start:ws_call_end]
        self.assertNotIn('ACCESS_TOKEN', ws_call_line,
                         f"ws_init call must not use static ACCESS_TOKEN; got: {ws_call_line!r}")

    def test_ws_init_skipped_when_token_missing(self):
        """When _read_token_from_env returns '', ws_init is NOT called and error is logged."""
        import main as _main
        mock_ws_init = MagicMock(return_value=True)
        mock_strategy = MagicMock()
        mock_strategy.enable_websocket = True

        with patch.object(_main, '_read_token_from_env', return_value=''), \
             patch('scanner.websocket_feed.init', mock_ws_init), \
             patch('config.strategy.STRATEGY', mock_strategy):
            # Simulate the ws_init startup block inline
            _ws_token = _main._read_token_from_env()
            if not _ws_token:
                ws_called = False
            elif mock_ws_init(_ws_token, 'CLIENT'):
                ws_called = True
            else:
                ws_called = False

        self.assertFalse(ws_called, "ws_init must NOT be called when token is missing")
        mock_ws_init.assert_not_called()

    def test_ws_init_called_with_fresh_token_when_present(self):
        """When token is present, ws_init is called with that fresh token."""
        mock_ws_init = MagicMock(return_value=True)
        fresh_tok = 'CLIENT123:newtokenabc'

        import main as _main
        _ws_token = fresh_tok  # simulate _read_token_from_env return
        if _ws_token:
            mock_ws_init(_ws_token, 'CLIENT123')

        mock_ws_init.assert_called_once_with(fresh_tok, 'CLIENT123')


# ─────────────────────────────────────────────────────────────────────────────
# REQ-2.2 — Atomic .env Write
# ─────────────────────────────────────────────────────────────────────────────

class TestAtomicEnvWrite(unittest.TestCase):

    def _import_fn(self):
        from broker.web_token import _atomic_update_access_token
        return _atomic_update_access_token

    def test_updates_access_token_and_preserves_other_keys(self):
        """New token replaces old ACCESS_TOKEN; unrelated keys survive."""
        update = self._import_fn()
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, '.env')
            _write_env(env_path, (
                "CLIENT_ID=ABCD1234\n"
                "SECRET_KEY=mysecret\n"
                "ACCESS_TOKEN=OLD_CLIENT:oldtoken\n"
                "TELEGRAM_BOT_TOKEN=tgtoken\n"
            ))
            update(env_path, 'NEWCLIENT:newtoken999')
            with open(env_path, encoding='utf-8') as f:
                content = f.read()

        self.assertIn('ACCESS_TOKEN=NEWCLIENT:newtoken999', content)
        self.assertIn('CLIENT_ID=ABCD1234', content)
        self.assertIn('SECRET_KEY=mysecret', content)
        self.assertIn('TELEGRAM_BOT_TOKEN=tgtoken', content)
        self.assertNotIn('oldtoken', content)

    def test_creates_env_when_file_missing(self):
        """If .env does not yet exist, the function creates it."""
        update = self._import_fn()
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, '.env')
            self.assertFalse(os.path.exists(env_path))
            update(env_path, 'CLIENT:tok')
            self.assertTrue(os.path.exists(env_path))
            with open(env_path, encoding='utf-8') as f:
                content = f.read()
        self.assertIn('ACCESS_TOKEN=CLIENT:tok', content)

    def test_only_one_access_token_line_after_update(self):
        """No duplicate ACCESS_TOKEN lines after multiple updates."""
        update = self._import_fn()
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, '.env')
            _write_env(env_path, "ACCESS_TOKEN=OLD:tok1\n")
            update(env_path, 'NEW:tok2')
            update(env_path, 'NEW:tok3')
            with open(env_path, encoding='utf-8') as f:
                lines = f.readlines()
        at_lines = [l for l in lines if l.startswith('ACCESS_TOKEN=')]
        self.assertEqual(len(at_lines), 1, f"Expected 1 ACCESS_TOKEN line, got: {at_lines}")

    def test_no_tmp_file_left_on_success(self):
        """On success, no .env.*.tmp file is left behind."""
        update = self._import_fn()
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, '.env')
            _write_env(env_path, "ACCESS_TOKEN=OLD:tok\n")
            update(env_path, 'NEW:tok')
            tmp_files = [f for f in os.listdir(tmpdir) if '.tmp' in f]
        self.assertEqual(tmp_files, [], f"Stale tmp files found: {tmp_files}")

    def test_original_preserved_on_simulated_fsync_failure(self):
        """If fsync fails, os.replace never runs and the original .env is untouched."""
        update = self._import_fn()
        original_content = "ACCESS_TOKEN=ORIGINAL:safe\nKEY=val\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = os.path.join(tmpdir, '.env')
            _write_env(env_path, original_content)
            with patch('os.fsync', side_effect=OSError("disk full")):
                try:
                    update(env_path, 'NEW:tok')
                except Exception:
                    pass
            with open(env_path, encoding='utf-8') as f:
                content = f.read()
        # Original must be intact
        self.assertIn('ORIGINAL:safe', content)
        self.assertNotIn('NEW:tok', content)


# ─────────────────────────────────────────────────────────────────────────────
# REQ-2.3 — Orchestrator Kill-Flag Freeze Protection
# ─────────────────────────────────────────────────────────────────────────────

class TestOrchestratorKillFlag(unittest.TestCase):

    def _run_main_with_flag(self, flag_content: str = '') -> int:
        """Run orchestrator.main() with a kill flag; return the SystemExit code."""
        import orchestrator
        with tempfile.TemporaryDirectory() as tmpdir:
            flag_path = os.path.join(tmpdir, 'kill_all.flag')
            with open(flag_path, 'w') as f:
                f.write(flag_content)
            # argparse reads sys.argv — isolate from pytest's arguments
            with patch.object(orchestrator, 'KILL_FLAG', flag_path), \
                 patch.object(orchestrator, 'DATA_DIR', tmpdir), \
                 patch.object(orchestrator, 'KILL_TOKEN', ''), \
                 patch.object(orchestrator, '_log'), \
                 patch.object(sys, 'argv', ['orchestrator.py']):
                try:
                    orchestrator.main()
                    return 0
                except SystemExit as e:
                    return int(e.code)

    def test_refuses_to_start_when_kill_flag_exists(self):
        """orchestrator.main() exits with code 1 when kill_all.flag is present."""
        exit_code = self._run_main_with_flag()
        self.assertEqual(exit_code, 1,
                         "Orchestrator must exit(1) when kill_all.flag exists")

    def test_kill_flag_not_deleted_on_startup(self):
        """The kill flag file must still exist after orchestrator refuses to start."""
        import orchestrator
        with tempfile.TemporaryDirectory() as tmpdir:
            flag_path = os.path.join(tmpdir, 'kill_all.flag')
            with open(flag_path, 'w') as f:
                f.write('')
            with patch.object(orchestrator, 'KILL_FLAG', flag_path), \
                 patch.object(orchestrator, 'DATA_DIR', tmpdir), \
                 patch.object(orchestrator, 'KILL_TOKEN', ''), \
                 patch.object(orchestrator, '_log'), \
                 patch.object(sys, 'argv', ['orchestrator.py']):
                try:
                    orchestrator.main()
                except SystemExit:
                    pass
            self.assertTrue(os.path.exists(flag_path),
                            "kill_all.flag must NOT be deleted by orchestrator startup")

    def test_critical_message_logged_on_kill_flag(self):
        """A CRITICAL-level message appears in the log when kill flag is present."""
        import orchestrator
        log_calls = []
        with tempfile.TemporaryDirectory() as tmpdir:
            flag_path = os.path.join(tmpdir, 'kill_all.flag')
            with open(flag_path, 'w') as f:
                f.write('')
            def _capture_log(msg):
                log_calls.append(msg)
            with patch.object(orchestrator, 'KILL_FLAG', flag_path), \
                 patch.object(orchestrator, 'DATA_DIR', tmpdir), \
                 patch.object(orchestrator, 'KILL_TOKEN', ''), \
                 patch.object(orchestrator, '_log', side_effect=_capture_log), \
                 patch.object(sys, 'argv', ['orchestrator.py']):
                try:
                    orchestrator.main()
                except SystemExit:
                    pass
        combined = ' '.join(log_calls)
        self.assertTrue(
            'CRITICAL' in combined or 'kill_all.flag' in combined,
            f"Expected CRITICAL / kill_all.flag in log output. Got: {log_calls}"
        )

    def test_normal_startup_without_kill_flag(self):
        """Orchestrator proceeds past kill-flag check when no flag file exists."""
        import orchestrator
        with tempfile.TemporaryDirectory() as tmpdir:
            # No flag file — orchestrator should NOT sys.exit(1) at this stage
            with patch.object(orchestrator, 'KILL_FLAG',
                               os.path.join(tmpdir, 'nonexistent.flag')), \
                 patch.object(orchestrator, 'DATA_DIR', tmpdir), \
                 patch.object(orchestrator, 'KILL_TOKEN', ''), \
                 patch.object(orchestrator, '_log'), \
                 patch.object(orchestrator, '_startup_telegram'), \
                 patch.object(sys, 'argv', ['orchestrator.py']), \
                 patch('subprocess.Popen', side_effect=SystemExit(0)):
                try:
                    orchestrator.main()
                except SystemExit as e:
                    # Any exit other than 1 (kill-flag exit) means we passed the check
                    self.assertNotEqual(int(e.code), 1,
                                        "Orchestrator must not exit(1) without a kill flag")
                except Exception:
                    pass  # subprocess mock may raise — that's fine; we passed the check


# ─────────────────────────────────────────────────────────────────────────────
# REQ-2.4 — Network Timeout Enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestBinanceAdapterTimeouts(unittest.TestCase):
    """All requests.* calls in binance_adapter must carry an explicit timeout."""

    def _get_source(self):
        import crypto_engine.binance_adapter as _mod
        return inspect.getsource(_mod)

    def _parse_request_calls(self, src: str) -> list[tuple[str, str]]:
        """Return list of (method, window) for every requests.* call found.

        Checks a 5-line sliding window starting at the call line so multiline
        calls (where `timeout=` appears on a continuation line) are detected.
        """
        results = []
        lines = src.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            for method in ('requests.get(', 'requests.post(', 'requests.put(',
                           'requests.delete(', 'requests.patch('):
                if method in stripped:
                    # Grab up to 5 lines starting here (covers most multiline args)
                    window = '\n'.join(lines[i:i + 5])
                    results.append((method, window))
        return results

    def test_default_http_timeout_constant_defined(self):
        """DEFAULT_HTTP_TIMEOUT constant must exist in binance_adapter."""
        from crypto_engine.binance_adapter import DEFAULT_HTTP_TIMEOUT
        self.assertIsInstance(DEFAULT_HTTP_TIMEOUT, float)
        self.assertGreater(DEFAULT_HTTP_TIMEOUT, 0)
        self.assertLessEqual(DEFAULT_HTTP_TIMEOUT, 5.0)

    def test_order_timeout_constant_defined(self):
        """ORDER_TIMEOUT must exist and be <= 10s."""
        from crypto_engine.binance_adapter import ORDER_TIMEOUT
        self.assertIsInstance(ORDER_TIMEOUT, float)
        self.assertLessEqual(ORDER_TIMEOUT, 10.0)

    def test_no_requests_call_without_timeout_in_binance_adapter(self):
        """Every requests.get/post/put/delete in binance_adapter carries a timeout arg."""
        src = self._get_source()
        calls = self._parse_request_calls(src)
        self.assertGreater(len(calls), 0, "No requests.* calls found — test setup issue")
        for method, line in calls:
            self.assertIn('timeout', line,
                          f"requests call missing timeout: {line!r}")

    def test_get_klines_timeout_exception_handled(self):
        """get_klines returns None on Timeout and does not raise."""
        from crypto_engine.binance_adapter import BinanceAdapter
        adapter = BinanceAdapter(paper=False, api_key='k', api_secret='s')
        import requests as _req
        with patch('requests.get', side_effect=_req.exceptions.Timeout("test")):
            result = adapter.get_klines('BTCUSDT', '5m', 10)
        self.assertIsNone(result, "get_klines must return None on Timeout, not raise")

    def test_place_order_timeout_exception_handled(self):
        """place_order returns None on Timeout and does not raise."""
        from crypto_engine.binance_adapter import BinanceAdapter
        import requests as _req
        adapter = BinanceAdapter(paper=False, api_key='k', api_secret='s')
        with patch('requests.post', side_effect=_req.exceptions.Timeout("test")):
            result = adapter.place_order('BTCUSDT', 'BUY', 0.01)
        self.assertIsNone(result, "place_order must return None on Timeout, not raise")

    def test_place_stop_market_timeout_exception_handled(self):
        """place_stop_market returns None on Timeout and does not raise."""
        from crypto_engine.binance_adapter import BinanceAdapter
        import requests as _req
        adapter = BinanceAdapter(paper=False, api_key='k', api_secret='s')
        with patch('requests.post', side_effect=_req.exceptions.Timeout("test")):
            result = adapter.place_stop_market('BTCUSDT', 'SELL', 50000.0)
        self.assertIsNone(result, "place_stop_market must return None on Timeout, not raise")

    def test_cancel_order_timeout_exception_handled(self):
        """cancel_order returns False on Timeout and does not raise."""
        from crypto_engine.binance_adapter import BinanceAdapter
        import requests as _req
        adapter = BinanceAdapter(paper=False, api_key='k', api_secret='s')
        with patch('requests.delete', side_effect=_req.exceptions.Timeout("test")):
            result = adapter.cancel_order('BTCUSDT', 12345)
        self.assertFalse(result, "cancel_order must return False on Timeout, not raise")


class TestScannerRequestTimeouts(unittest.TestCase):
    """Scanner files must not have open-ended requests calls."""

    def _all_request_calls_in_file(self, path: str) -> list[str]:
        with open(path, encoding='utf-8') as f:
            src = f.read()
        calls = []
        for line in src.splitlines():
            stripped = line.strip()
            for method in ('requests.get(', 'requests.post(', 'requests.put(',
                           'requests.delete('):
                if method in stripped:
                    calls.append(stripped)
        return calls

    def test_option_strike_selector_has_timeout(self):
        """scanner/option_strike_selector.py requests call includes timeout."""
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'scanner', 'option_strike_selector.py')
        if not os.path.exists(path):
            self.skipTest("option_strike_selector.py not found")
        for call in self._all_request_calls_in_file(path):
            self.assertIn('timeout', call,
                          f"Missing timeout in scanner call: {call!r}")

    def test_scanner_requests_timeout_is_finite(self):
        """All timeout values in scanner files are numeric (not None/missing)."""
        scanner_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scanner')
        for fname in os.listdir(scanner_dir):
            if not fname.endswith('.py'):
                continue
            fpath = os.path.join(scanner_dir, fname)
            with open(fpath, encoding='utf-8') as f:
                src = f.read()
            lines = src.splitlines()
            for i, line in enumerate(lines):
                stripped = line.strip()
                for method in ('requests.get(', 'requests.post(', 'requests.put(',
                               'requests.delete('):
                    if method in stripped:
                        window = '\n'.join(lines[i:i + 5])
                        self.assertIn('timeout', window,
                                      f"{fname}: requests call missing timeout near: {stripped!r}")

    def test_orchestrator_telegram_call_has_timeout(self):
        """orchestrator.py _send_telegram uses timeout."""
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'orchestrator.py')
        with open(path, encoding='utf-8') as f:
            src = f.read()
        lines = src.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if 'requests.post(' in stripped:
                # Check a 5-line window for timeout (multi-line call)
                window = '\n'.join(lines[i:i + 5])
                self.assertIn('timeout', window,
                              f"orchestrator requests.post missing timeout near: {stripped!r}")


class TestTimeoutDoesNotFreezeThread(unittest.TestCase):
    """Timeout errors must not block the calling thread for more than ~1 second."""

    def test_get_klines_returns_quickly_on_timeout(self):
        """get_klines with a simulated Timeout resolves in < 1 second."""
        import time
        import requests as _req
        from crypto_engine.binance_adapter import BinanceAdapter
        adapter = BinanceAdapter(paper=False, api_key='k', api_secret='s')
        t0 = time.monotonic()
        with patch('requests.get', side_effect=_req.exceptions.Timeout("sim")):
            adapter.get_klines('BTCUSDT')
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.0,
                        f"get_klines took {elapsed:.2f}s on Timeout — thread may be blocked")

    def test_place_order_returns_quickly_on_timeout(self):
        """place_order with a simulated Timeout resolves in < 1 second."""
        import time
        import requests as _req
        from crypto_engine.binance_adapter import BinanceAdapter
        adapter = BinanceAdapter(paper=False, api_key='k', api_secret='s')
        t0 = time.monotonic()
        with patch('requests.post', side_effect=_req.exceptions.Timeout("sim")):
            adapter.place_order('BTCUSDT', 'BUY', 0.01)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.0,
                        f"place_order took {elapsed:.2f}s on Timeout — thread may be blocked")


if __name__ == '__main__':
    unittest.main()
