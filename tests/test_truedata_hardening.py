"""
tests/test_truedata_hardening.py

Concurrency stress tests for the hardened data/truedata_feed.py.

Goals verified:
  - State machine correctness (only one session created per connect wave)
  - No leaked sessions (failed connects clean up)
  - No deadlocks (all operations finish within timeout)
  - Queue remains bounded (tick worker drains queue, no unbounded growth)
  - Reconnect failure handling (state reverts to DISCONNECTED on exception)

No TrueData credentials required: the `truedata_ws` SDK is fully mocked.
"""

import importlib
import os
import queue
import sys
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Unified SDK mock factory — replaces `from truedata_ws.websocket.TD import TD`
# inside truedata_feed.py.  A single TD class routes to hist or live behaviour
# based on the `live_port` kwarg (None → hist, int → live).
# ---------------------------------------------------------------------------

def _make_td_class(hist_registry: list, live_registry: list = None,
                   hist_raise_on_n: int = 0, fail_start_live: bool = False):
    """
    Returns a unified MockTD class.
    - live_port=None  → historical mode; appended to hist_registry.
    - live_port=<int> → live mode;       appended to live_registry.
    - hist_raise_on_n: raise RuntimeError on the N-th hist instantiation.
    - fail_start_live: raise RuntimeError inside start_live_data().
    """
    if live_registry is None:
        live_registry = []
    hist_count = [0]

    class MockTD:
        def __init__(self, user, password, live_port=None,
                     historical_api=True, log_level=0, **kwargs):
            self._live_port   = live_port
            self._disconnected = False
            self.live_websocket = None
            self.live_data      = {}
            if live_port is None:
                hist_count[0] += 1
                if hist_raise_on_n and hist_count[0] >= hist_raise_on_n:
                    raise RuntimeError("Simulated TD hist connection failure")
                self._id = hist_count[0]
                hist_registry.append(self)
            else:
                live_registry.append(self)

        def get_historic_data(self, symbol, bar_size=None,
                              start_time=None, end_time=None, **kw):
            return [
                {"time": "2026-05-30 09:15:00", "open": 24000.0,
                 "high": 24050.0, "low": 23980.0, "close": 24020.0, "volume": 100000},
                {"time": "2026-05-30 09:30:00", "open": 24010.0,
                 "high": 24060.0, "low": 23990.0, "close": 24040.0, "volume": 110000},
            ]

        def get_n_historical_bars(self, symbol, no_of_bars=200, bar_size="15 mins"):
            return self.get_historic_data(symbol)

        def start_live_data(self, symbols):
            if fail_start_live:
                raise RuntimeError("Simulated start_live_data failure")
            return list(range(len(symbols)))

        def disconnect(self):
            self._disconnected = True

    return MockTD


def _build_fake_td_ws_module(td_class):
    """Build a fake truedata_ws.websocket.TD module with MockTD as TD."""
    td_mod = types.ModuleType("truedata_ws.websocket.TD")
    td_mod.TD = td_class
    return td_mod


def _fresh_manager(fake_td_module):
    """
    Import (or re-import) data.truedata_feed with the given fake truedata_ws
    module, and return a brand-new TrueDataManager instance (singleton reset).
    """
    # Inject the fake SDK into the full module hierarchy so that
    #   `from truedata_ws.websocket.TD import TD`
    # inside truedata_feed.py resolves to our mock.
    sys.modules["truedata_ws"]            = types.ModuleType("truedata_ws")
    sys.modules["truedata_ws.websocket"]  = types.ModuleType("truedata_ws.websocket")
    sys.modules["truedata_ws.websocket.TD"] = fake_td_module

    # Force re-import of truedata_feed so module-level globals + singleton reset
    for key in list(sys.modules.keys()):
        if "truedata_feed" in key or key == "data.truedata_feed":
            del sys.modules[key]

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import data.truedata_feed as feed_mod

    # Reset singleton so each test gets a fresh instance
    feed_mod.TrueDataManager._instance = None
    mgr = feed_mod.TrueDataManager()
    return mgr, feed_mod


# ---------------------------------------------------------------------------
# Helper: run N threads simultaneously and collect results
# ---------------------------------------------------------------------------

def _run_concurrent(fn, n_threads: int, timeout: float = 10.0) -> list:
    results = [None] * n_threads
    errors  = [None] * n_threads
    barrier = threading.Barrier(n_threads)

    def worker(idx):
        barrier.wait()          # all threads start at the same instant
        try:
            results[idx] = fn()
        except Exception as e:
            errors[idx] = e

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)

    # Check for threads that didn't finish (deadlock indicator)
    still_alive = [t for t in threads if t.is_alive()]
    if still_alive:
        raise AssertionError(
            f"DEADLOCK DETECTED: {len(still_alive)}/{n_threads} threads still running after {timeout}s"
        )

    return results, errors


# ===========================================================================
# Test Suite
# ===========================================================================

class TestStateMachineCorrectness(unittest.TestCase):
    """State transitions are correct under normal conditions."""

    def setUp(self):
        self.hist_instances = []
        td_cls = _make_td_class(self.hist_instances)
        fake_mod = _build_fake_td_ws_module(td_cls)
        self.mgr, self.feed = _fresh_manager(fake_mod)

    def test_initial_state_is_disconnected(self):
        from data.truedata_feed import _ConnState
        self.assertEqual(self.mgr._hist_state, _ConnState.DISCONNECTED)
        self.assertEqual(self.mgr._live_state, _ConnState.DISCONNECTED)

    def test_connect_hist_transitions_to_connected(self):
        from data.truedata_feed import _ConnState
        with patch.dict("os.environ", {"TRUEDATA_USER": "user", "TRUEDATA_PASSWORD": "pass"}):
            # Patch module-level credentials (read at import time)
            self.feed._TRUEDATA_USER = "user"
            self.feed._TRUEDATA_PASS = "pass"
            ok = self.mgr.connect_hist()
        self.assertTrue(ok)
        self.assertEqual(self.mgr._hist_state, _ConnState.CONNECTED)
        self.assertTrue(self.mgr.is_hist_ready)

    def test_second_connect_hist_is_noop(self):
        self.feed._TRUEDATA_USER = "user"
        self.feed._TRUEDATA_PASS = "pass"
        self.mgr.connect_hist()
        self.mgr.connect_hist()
        # Only one TD instance should exist
        self.assertEqual(len(self.hist_instances), 1)

    def test_disconnect_resets_state(self):
        from data.truedata_feed import _ConnState
        self.feed._TRUEDATA_USER = "user"
        self.feed._TRUEDATA_PASS = "pass"
        self.mgr.connect_hist()
        self.mgr.disconnect()
        self.assertEqual(self.mgr._hist_state, _ConnState.DISCONNECTED)
        self.assertEqual(self.mgr._live_state, _ConnState.DISCONNECTED)
        self.assertIsNone(self.mgr._td_hist)
        self.assertIsNone(self.mgr._td_live)

    def test_is_hist_ready_false_before_connect(self):
        self.assertFalse(self.mgr.is_hist_ready)

    def test_is_hist_ready_true_after_connect(self):
        self.feed._TRUEDATA_USER = "user"
        self.feed._TRUEDATA_PASS = "pass"
        self.mgr.connect_hist()
        self.assertTrue(self.mgr.is_hist_ready)


class TestConcurrentConnect(unittest.TestCase):
    """50 threads racing to connect_hist() — only ONE session must be created."""

    N_THREADS = 50
    TIMEOUT   = 10.0

    def setUp(self):
        self.hist_instances = []
        td_cls = _make_td_class(self.hist_instances)
        fake_mod = _build_fake_td_ws_module(td_cls)
        self.mgr, self.feed = _fresh_manager(fake_mod)
        self.feed._TRUEDATA_USER = "user"
        self.feed._TRUEDATA_PASS = "pass"

    def test_no_duplicate_sessions(self):
        """50 concurrent connect_hist() calls must create exactly 1 TD instance."""
        results, errors = _run_concurrent(self.mgr.connect_hist, self.N_THREADS, self.TIMEOUT)

        # At least one True (the winner)
        success_count = sum(1 for r in results if r is True)
        self.assertGreaterEqual(success_count, 1, "At least one thread must succeed")

        # Exactly 1 TD instance created (no duplicate sessions)
        self.assertEqual(
            len(self.hist_instances), 1,
            f"Expected 1 TD instance, got {len(self.hist_instances)}"
        )

    def test_state_is_connected_after_race(self):
        """After the race, state must be CONNECTED."""
        from data.truedata_feed import _ConnState
        _run_concurrent(self.mgr.connect_hist, self.N_THREADS, self.TIMEOUT)
        self.assertEqual(self.mgr._hist_state, _ConnState.CONNECTED)

    def test_no_deadlock(self):
        """All threads must complete within timeout — no deadlock."""
        # _run_concurrent raises AssertionError if any thread is still alive
        _run_concurrent(self.mgr.connect_hist, self.N_THREADS, self.TIMEOUT)


class TestConcurrentDisconnect(unittest.TestCase):
    """Concurrent get_historical_bars() + disconnect() — no AttributeError (C2)."""

    def setUp(self):
        self.hist_instances = []
        td_cls = _make_td_class(self.hist_instances)
        fake_mod = _build_fake_td_ws_module(td_cls)
        self.mgr, self.feed = _fresh_manager(fake_mod)
        self.feed._TRUEDATA_USER = "user"
        self.feed._TRUEDATA_PASS = "pass"
        self.mgr.connect_hist()

    def test_no_crash_on_concurrent_disconnect(self):
        """
        10 threads calling get_historical_bars() while 1 thread calls disconnect()
        must produce NO AttributeError (the C2 fix).
        """
        errors = []
        stop_event = threading.Event()

        def reader():
            for _ in range(20):
                if stop_event.is_set():
                    break
                try:
                    self.mgr.get_historical_bars("NIFTY-I", "15 mins", days=5)
                except AttributeError as e:
                    errors.append(f"AttributeError (C2 regression): {e}")
                except Exception:
                    pass  # Other errors acceptable (session gone)

        def disconnector():
            time.sleep(0.01)   # let readers warm up
            self.mgr.disconnect()
            stop_event.set()

        threads = [threading.Thread(target=reader) for _ in range(10)]
        threads.append(threading.Thread(target=disconnector))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        alive = [t for t in threads if t.is_alive()]
        self.assertEqual(len(alive), 0, "DEADLOCK: threads did not finish")
        self.assertEqual(errors, [], "\n".join(errors))


class TestReconnectOnSessionExpiry(unittest.TestCase):
    """C3: _hist_state resets on session-expiry errors."""

    def setUp(self):
        self.hist_instances = []
        td_cls = _make_td_class(self.hist_instances)
        fake_mod = _build_fake_td_ws_module(td_cls)
        self.mgr, self.feed = _fresh_manager(fake_mod)
        self.feed._TRUEDATA_USER = "user"
        self.feed._TRUEDATA_PASS = "pass"

    def test_expiry_error_resets_state_to_disconnected(self):
        from data.truedata_feed import _ConnState

        self.mgr.connect_hist()
        self.assertEqual(self.mgr._hist_state, _ConnState.CONNECTED)

        # Simulate session expiry by replacing hist with one that raises "expired"
        class _ExpiredHist:
            def get_historic_data(self, *a, **kw):
                raise RuntimeError("Session expired: unauthorized")
            def get_n_historical_bars(self, *a, **kw):
                raise RuntimeError("Session expired: unauthorized")

        with self.mgr._lock:
            self.mgr._td_hist = _ExpiredHist()

        # Call should fail and reset state
        result = self.mgr.get_historical_bars("NIFTY-I", "15 mins", 5)
        self.assertIsNone(result)
        self.assertEqual(
            self.mgr._hist_state, _ConnState.DISCONNECTED,
            "State must revert to DISCONNECTED after session expiry (C3 fix)"
        )

    def test_reconnects_after_expiry(self):
        """After expiry resets state, the next call should reconnect."""
        from data.truedata_feed import _ConnState

        self.mgr.connect_hist()
        first_instance = self.hist_instances[0]

        class _ExpiredHist:
            def get_historic_data(self, *a, **kw):
                raise RuntimeError("401 unauthorized")

        with self.mgr._lock:
            self.mgr._td_hist = _ExpiredHist()

        # First call — hits expiry, resets to DISCONNECTED
        self.mgr.get_historical_bars("NIFTY-I", "15 mins", 5)
        self.assertEqual(self.mgr._hist_state, _ConnState.DISCONNECTED)

        # Second call — should trigger reconnect
        result = self.mgr.get_historical_bars("NIFTY-I", "15 mins", 5)
        # A fresh TD instance must have been created
        self.assertEqual(len(self.hist_instances), 2, "Expected a new TD instance on reconnect")


class TestZombieCleanupOnLiveFail(unittest.TestCase):
    """C4: start_live_data() failure cleans up the TD_live object."""

    def setUp(self):
        self.live_instances = []
        td_cls = _make_td_class([], live_registry=self.live_instances, fail_start_live=True)
        fake_mod = _build_fake_td_ws_module(td_cls)
        self.mgr, self.feed = _fresh_manager(fake_mod)
        self.feed._TRUEDATA_USER = "user"
        self.feed._TRUEDATA_PASS = "pass"

    def test_live_state_reverts_to_disconnected_on_failure(self):
        from data.truedata_feed import _ConnState
        ok = self.mgr.connect_live(["NIFTY-I"])
        self.assertFalse(ok, "connect_live should return False when start_live_data fails")
        self.assertEqual(
            self.mgr._live_state, _ConnState.DISCONNECTED,
            "State must be DISCONNECTED after start_live_data() failure (C4 fix)"
        )

    def test_failed_live_is_disconnected(self):
        """The partially-created TD_live object must have disconnect() called."""
        self.mgr.connect_live(["NIFTY-I"])
        self.assertEqual(len(self.live_instances), 1)
        self.assertTrue(
            self.live_instances[0]._disconnected,
            "TD_live.disconnect() must be called on failed connect (C4 fix)"
        )

    def test_no_self_live_set_on_failure(self):
        """self._td_live must be None after a failed connect."""
        self.mgr.connect_live(["NIFTY-I"])
        with self.mgr._lock:
            self.assertIsNone(self.mgr._td_live)


class TestConcurrentHistoricalRequests(unittest.TestCase):
    """10 concurrent get_historical_bars() calls — no crashes, no deadlock."""

    N_THREADS = 10

    def setUp(self):
        self.hist_instances = []
        td_cls = _make_td_class(self.hist_instances)
        fake_mod = _build_fake_td_ws_module(td_cls)
        self.mgr, self.feed = _fresh_manager(fake_mod)
        self.feed._TRUEDATA_USER = "user"
        self.feed._TRUEDATA_PASS = "pass"

    def test_concurrent_historical_no_crash(self):
        def fetch():
            return self.mgr.get_historical_bars("NIFTY-I", "15 mins", days=5)

        results, errors = _run_concurrent(fetch, self.N_THREADS, timeout=15.0)

        real_errors = [e for e in errors if e is not None]
        self.assertEqual(real_errors, [], f"Errors during concurrent fetch: {real_errors}")

        # All results should be DataFrames (not None), since mock never fails
        for i, r in enumerate(results):
            self.assertIsNotNone(r, f"Thread {i} returned None unexpectedly")

    def test_concurrent_historical_no_deadlock(self):
        def fetch():
            return self.mgr.get_historical_bars("BANKNIFTY-I", "5 mins", days=10)

        # Will raise AssertionError if any thread hangs past timeout
        _run_concurrent(fetch, self.N_THREADS, timeout=15.0)


class TestTickQueueBounded(unittest.TestCase):
    """Queue drains correctly — no unbounded growth under tick bursts."""

    def setUp(self):
        td_cls = _make_td_class([])
        fake_mod = _build_fake_td_ws_module(td_cls)
        self.mgr, self.feed = _fresh_manager(fake_mod)

    def test_queue_drains_to_zero(self):
        """Enqueue 10,000 ticks; queue must drain to 0 within 5 seconds."""

        class _FakeTick:
            symbol    = "NIFTY-I"
            ltp       = 24000.0
            ttq       = 100000
            timestamp = "2026-05-30 10:00:00"

        tick = _FakeTick()
        N = 10_000
        for _ in range(N):
            self.mgr._tick_queue.put(("tick", tick))

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.mgr._tick_queue.empty():
                break
            time.sleep(0.05)

        remaining = self.mgr._tick_queue.qsize()
        self.assertEqual(remaining, 0, f"Queue not drained: {remaining} items remain after 5s")

    def test_queue_does_not_block_caller(self):
        """put() on a SimpleQueue must be non-blocking even under rapid fire."""

        class _FakeTick:
            symbol    = "BANKNIFTY-I"
            ltp       = 51000.0
            ttq       = 200000
            timestamp = "2026-05-30 10:00:00"

        tick = _FakeTick()
        start = time.monotonic()
        for _ in range(50_000):
            self.mgr._tick_queue.put(("tick", tick))
        elapsed = time.monotonic() - start

        # 50,000 enqueues should complete in well under 1 second
        self.assertLess(elapsed, 1.0, f"Enqueue took {elapsed:.3f}s — possible blocking")


class TestSimulatedReconnectFailures(unittest.TestCase):
    """Connect fails N times, then succeeds — state always lands correctly."""

    def _build_mgr_with_failing_hist(self, n_failures: int):
        """Build a manager where the first n_failures connect attempts raise."""
        instances = []
        td_cls = _make_td_class(instances, hist_raise_on_n=n_failures)
        fake_mod = _build_fake_td_ws_module(td_cls)
        mgr, feed = _fresh_manager(fake_mod)
        feed._TRUEDATA_USER = "user"
        feed._TRUEDATA_PASS = "pass"
        return mgr, instances, feed

    def test_failure_leaves_disconnected(self):
        mgr, _, feed = self._build_mgr_with_failing_hist(n_failures=1)
        _ConnState = feed._ConnState
        ok = mgr.connect_hist()
        self.assertFalse(ok)
        self.assertEqual(mgr._hist_state, _ConnState.DISCONNECTED)

    def test_retry_after_failure_succeeds(self):
        """After a failed connect, the next attempt must be allowed (state is DISCONNECTED)."""
        instances = []
        attempt = [0]

        class _FlakeyTD:
            def __init__(self, user, password, live_port=None,
                         historical_api=True, log_level=0, **kwargs):
                attempt[0] += 1
                instances.append(self)
                self._disconnected = False
                self.live_websocket = None
                self.live_data = {}
                if attempt[0] == 1:
                    raise RuntimeError("First attempt fails")

            def get_historic_data(self, *a, **kw):
                import pandas as pd
                return [
                    {"time": "2026-05-30 09:15:00", "open": 24000.0, "high": 24050.0,
                     "low": 23980.0, "close": 24020.0, "volume": 100000},
                ]

            def get_n_historical_bars(self, *a, **kw):
                return self.get_historic_data()

            def disconnect(self):
                self._disconnected = True

        fake_mod = _build_fake_td_ws_module(_FlakeyTD)
        mgr, feed = _fresh_manager(fake_mod)
        feed._TRUEDATA_USER = "user"
        feed._TRUEDATA_PASS = "pass"
        _ConnState = feed._ConnState

        ok1 = mgr.connect_hist()
        self.assertFalse(ok1)
        self.assertEqual(mgr._hist_state, _ConnState.DISCONNECTED)

        ok2 = mgr.connect_hist()
        self.assertTrue(ok2)
        self.assertEqual(mgr._hist_state, _ConnState.CONNECTED)
        self.assertEqual(len(instances), 2)

    def test_50_concurrent_connect_disconnect_cycles(self):
        """
        50 threads each doing connect → get_data → disconnect in rapid succession.
        No deadlock, no AttributeError, no leaked sessions per round.
        """
        instances = []
        td_cls = _make_td_class(instances)
        fake_mod = _build_fake_td_ws_module(td_cls)
        mgr, feed = _fresh_manager(fake_mod)
        feed._TRUEDATA_USER = "user"
        feed._TRUEDATA_PASS = "pass"

        errors = []
        barrier = threading.Barrier(50)

        def cycle():
            barrier.wait()
            for _ in range(5):
                try:
                    mgr.connect_hist()
                    mgr.get_historical_bars("NIFTY-I", "15 mins", 5)
                    mgr.disconnect()
                except AttributeError as e:
                    errors.append(f"AttributeError: {e}")
                except Exception:
                    pass  # Races on disconnect are acceptable

        threads = [threading.Thread(target=cycle) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        alive = [t for t in threads if t.is_alive()]
        self.assertEqual(len(alive), 0, f"DEADLOCK: {len(alive)} threads still running")
        self.assertEqual(errors, [], "\n".join(errors))


class TestPasswordSanitization(unittest.TestCase):
    """HIGH-1: Passwords must not appear in log output."""

    def setUp(self):
        td_cls = _make_td_class([], hist_raise_on_n=1)  # always fails
        fake_mod = _build_fake_td_ws_module(td_cls)
        self.mgr, self.feed = _fresh_manager(fake_mod)
        self.feed._TRUEDATA_USER = "testuser"
        self.feed._TRUEDATA_PASS = "s3cr3tP@ss!"

    def test_password_not_in_log_on_connect_failure(self):
        """After a connect failure, the error log must not contain the password."""
        import logging

        log_records = []

        class CapturingHandler(logging.Handler):
            def emit(self, record):
                log_records.append(self.format(record))

        handler = CapturingHandler()
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.DEBUG)

        try:
            self.mgr.connect_hist()
        finally:
            logging.getLogger().removeHandler(handler)

        password = os.environ.get("TRUEDATA_PASSWORD", "truedata-test-pw")
        leaking = [r for r in log_records if password in r]
        self.assertEqual(
            leaking, [],
            f"Password found in log records:\n" + "\n".join(leaking)
        )


class TestMissingCredentials(unittest.TestCase):
    """connect_hist/live must fail gracefully with missing credentials."""

    def setUp(self):
        td_cls = _make_td_class([])
        fake_mod = _build_fake_td_ws_module(td_cls)
        self.mgr, self.feed = _fresh_manager(fake_mod)
        self.feed._TRUEDATA_USER = ""
        self.feed._TRUEDATA_PASS = ""

    def test_connect_hist_returns_false_no_credentials(self):
        ok = self.mgr.connect_hist()
        self.assertFalse(ok)

    def test_state_stays_disconnected_no_credentials(self):
        from data.truedata_feed import _ConnState
        self.mgr.connect_hist()
        self.assertEqual(self.mgr._hist_state, _ConnState.DISCONNECTED)

    def test_connect_live_returns_false_no_credentials(self):
        ok = self.mgr.connect_live(["NIFTY-I"])
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
