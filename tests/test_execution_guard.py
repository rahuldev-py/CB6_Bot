# tests/test_execution_guard.py — ExecutionGuard wiring and behavior tests
#
# Tests Task 2 requirements:
#   1. Valid bot trade passes ExecutionGuard
#   2. Valid ML trade passes ExecutionGuard (same path as bot trade)
#   3. Invalid symbol is blocked
#   4. XAUUSD block works if configured
#   5. Bad account/magic mismatch does not affect guard (schema-level)
#   6. Daily loss limit blocks trade
#   7. Dashboard still imports (no regression)

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch


class TestGuardDictEntry(unittest.TestCase):
    """Test the dict-based guard used by existing order_manager + live_trader."""

    def setUp(self):
        self.clean_state = {
            'daily_trades': 0,
            'daily_losses': 0,
            'closed_trades': [],
            'available_capital': 200000,
        }
        self.capital = 200000

    def _call(self, state=None, capital=None, symbol="", blocked=None):
        from core.execution_guard import guard_dict_entry
        return guard_dict_entry(
            state or self.clean_state,
            capital or self.capital,
            symbol=symbol,
            blocked_symbols=blocked,
        )

    # ── 1. Valid bot trade ────────────────────────────────────────────────────

    def test_clean_state_allowed(self):
        ok, reason = self._call()
        self.assertTrue(ok)
        self.assertEqual(reason, "OK")

    # ── 2. Valid ML trade — same path, not blocked because source is ML ───────

    def test_ml_trade_not_blocked_by_source(self):
        """ML trades must pass the same guard as bot trades when risk is valid."""
        ml_state = {**self.clean_state, 'signal_source': 'ML'}
        ok, reason = self._call(state=ml_state)
        self.assertTrue(ok, f"ML trade was unexpectedly blocked: {reason}")

    # ── 3. Invalid symbol blocked ─────────────────────────────────────────────

    def test_blocked_symbol_rejected(self):
        ok, reason = self._call(symbol="XAUUSD", blocked={"XAUUSD", "EURUSD"})
        self.assertFalse(ok)
        self.assertIn("permanently blocked", reason)

    # ── 4. XAUUSD block ───────────────────────────────────────────────────────

    def test_xauusd_blocked_on_gft(self):
        ok, reason = self._call(symbol="XAUUSD", blocked={"XAUUSD"})
        self.assertFalse(ok)
        self.assertIn("XAUUSD", reason)

    def test_xagusd_allowed_on_gft(self):
        ok, reason = self._call(symbol="XAGUSD", blocked={"XAUUSD"})
        self.assertTrue(ok)

    # ── 5. Daily loss limit ───────────────────────────────────────────────────

    def test_dd_limit_blocks_trade(self):
        today = datetime.now().strftime('%Y-%m-%d')
        state = {
            **self.clean_state,
            'closed_trades': [{'pnl': -5000, 'exit_time': f'{today} 10:00:00'}],
        }
        ok, reason = self._call(state=state)
        self.assertFalse(ok)
        # can_enter() applies an absolute Rs 1,000 hard cap before the
        # percentage-based "DD limit" check.  A -5000 loss hits the hard
        # cap first, so we assert on the generic "Daily loss" prefix which
        # both messages share.
        self.assertIn("Daily loss", reason)

    def test_dd_within_limit_passes(self):
        today = datetime.now().strftime('%Y-%m-%d')
        state = {
            **self.clean_state,
            'closed_trades': [{'pnl': -100, 'exit_time': f'{today} 10:00:00'}],
        }
        ok, _ = self._call(state=state)
        self.assertTrue(ok)

    # ── 6. Guard errors — default (PAPER/DEV) fail open ──────────────────────

    def test_guard_error_fails_open_default(self):
        """Default mode (no mode arg): guard error fails open so PAPER/DEV trades proceed."""
        with patch('core.risk.can_enter', side_effect=RuntimeError("unexpected")):
            ok, reason = self._call()
        self.assertTrue(ok, "Default mode guard error should fail open")
        self.assertIn("guard_error", reason)

    def test_guard_error_fails_open(self):
        """Explicit PAPER mode: guard error still fails open."""
        with patch('core.risk.can_enter', side_effect=RuntimeError("unexpected")):
            from core.execution_guard import guard_dict_entry
            ok, reason = guard_dict_entry(
                self.clean_state, self.capital, symbol="NIFTY",
                mode="PAPER", intent_type="ENTRY",
            )
        self.assertTrue(ok, "PAPER mode guard error should fail open")
        self.assertIn("guard_error", reason)


class TestFailClosedBehavior(unittest.TestCase):
    """Task 2: Fail-closed live entry / fail-open exit behavior."""

    def setUp(self):
        self.clean_state = {
            'daily_trades': 0,
            'daily_losses': 0,
            'closed_trades': [],
            'available_capital': 200000,
        }
        self.capital = 200000

    def _call(self, mode="", intent_type="ENTRY"):
        from core.execution_guard import guard_dict_entry
        return guard_dict_entry(
            self.clean_state, self.capital,
            symbol="NIFTY", mode=mode, intent_type=intent_type,
        )

    # ── 1. LIVE ENTRY + guard exception = blocked ─────────────────────────────

    def test_live_entry_guard_exception_blocked(self):
        with patch('core.risk.can_enter', side_effect=RuntimeError("db down")):
            ok, reason = self._call(mode="LIVE", intent_type="ENTRY")
        self.assertFalse(ok, "LIVE ENTRY guard error must be blocked (fail closed)")
        self.assertIn("GUARD_INTERNAL_ERROR_FAIL_CLOSED", reason)

    # ── 2. PAPER ENTRY + guard exception = allowed ────────────────────────────

    def test_paper_entry_guard_exception_allowed(self):
        with patch('core.risk.can_enter', side_effect=RuntimeError("test error")):
            ok, reason = self._call(mode="PAPER", intent_type="ENTRY")
        self.assertTrue(ok, "PAPER ENTRY guard error must fail open")
        self.assertIn("guard_error", reason)

    # ── 3. DEV ENTRY + guard exception = allowed ──────────────────────────────

    def test_dev_entry_guard_exception_allowed(self):
        with patch('core.risk.can_enter', side_effect=RuntimeError("test error")):
            ok, reason = self._call(mode="DEV", intent_type="ENTRY")
        self.assertTrue(ok, "DEV ENTRY guard error must fail open")

    # ── 4. LIVE EXIT + guard exception = allowed ──────────────────────────────

    def test_live_exit_guard_exception_allowed(self):
        with patch('core.risk.can_enter', side_effect=RuntimeError("test error")):
            ok, reason = self._call(mode="LIVE", intent_type="CLOSE_SL")
        self.assertTrue(ok, "LIVE EXIT guard error must NOT block exit")

    def test_live_exit_close_intent_allowed(self):
        with patch('core.risk.can_enter', side_effect=RuntimeError("test error")):
            ok, reason = self._call(mode="LIVE", intent_type="EXIT")
        self.assertTrue(ok, "LIVE EXIT intent guard error must NOT block exit")

    # ── 5. LIVE valid ML trade still passes ───────────────────────────────────

    def test_live_valid_ml_trade_passes(self):
        """An ML-sourced trade with valid risk state must still be allowed in LIVE mode."""
        from core.execution_guard import guard_dict_entry
        ml_state = {**self.clean_state, 'signal_source': 'ML'}
        ok, reason = guard_dict_entry(ml_state, self.capital, symbol="NIFTY", mode="LIVE")
        self.assertTrue(ok, f"Valid LIVE ML trade was unexpectedly blocked: {reason}")

    # ── 6. LIVE valid bot trade still passes ──────────────────────────────────

    def test_live_valid_bot_trade_passes(self):
        ok, reason = self._call(mode="LIVE", intent_type="ENTRY")
        self.assertTrue(ok, f"Valid LIVE bot trade was unexpectedly blocked: {reason}")

    # ── 7. Blocked trade includes GUARD_INTERNAL_ERROR_FAIL_CLOSED ───────────

    def test_blocked_reason_code_present(self):
        with patch('core.risk.can_enter', side_effect=RuntimeError("timeout")):
            ok, reason = self._call(mode="LIVE", intent_type="ENTRY")
        self.assertFalse(ok)
        self.assertIn("GUARD_INTERNAL_ERROR_FAIL_CLOSED", reason)
        self.assertIn("timeout", reason)

    # ── 8. Audit log receives fail-open/fail-closed status ────────────────────

    def test_live_entry_error_logged_as_fail_closed(self):
        import logging
        with patch('core.risk.can_enter', side_effect=RuntimeError("timeout")):
            with self.assertLogs(level="ERROR") as cm:
                self._call(mode="LIVE", intent_type="ENTRY")
        self.assertTrue(
            any("FAIL CLOSED" in line for line in cm.output),
            f"Expected 'FAIL CLOSED' in ERROR log. Got: {cm.output}"
        )

    def test_paper_entry_error_logged_as_fail_open(self):
        import logging
        with patch('core.risk.can_enter', side_effect=RuntimeError("timeout")):
            with self.assertLogs(level="WARNING") as cm:
                self._call(mode="PAPER", intent_type="ENTRY")
        self.assertTrue(
            any("fail open" in line for line in cm.output),
            f"Expected 'fail open' in WARNING log. Got: {cm.output}"
        )

    # ── should_fail_closed helper directly ────────────────────────────────────

    def test_should_fail_closed_live_entry(self):
        from core.execution_guard import should_fail_closed
        self.assertTrue(should_fail_closed({"mode": "LIVE", "intent_type": "ENTRY"}))

    def test_should_fail_closed_live_sell_entry(self):
        from core.execution_guard import should_fail_closed
        self.assertTrue(should_fail_closed({"mode": "LIVE", "intent_type": "SELL"}))

    def test_should_fail_closed_live_exit_false(self):
        from core.execution_guard import should_fail_closed
        self.assertFalse(should_fail_closed({"mode": "LIVE", "intent_type": "CLOSE_SL"}))

    def test_should_fail_closed_paper_false(self):
        from core.execution_guard import should_fail_closed
        self.assertFalse(should_fail_closed({"mode": "PAPER", "intent_type": "ENTRY"}))

    def test_should_fail_closed_backtest_false(self):
        from core.execution_guard import should_fail_closed
        self.assertFalse(should_fail_closed({"mode": "BACKTEST", "intent_type": "ENTRY"}))


class TestForexGuardedOrder(unittest.TestCase):
    """Task 4: execute_forex_guarded_order validation."""

    def test_direct_order_without_account_blocked(self):
        """Entry without account_id must be blocked."""
        from core.execution_guard import execute_forex_guarded_order
        mock_fn = MagicMock(return_value={"ticket": 1})
        with self.assertRaises(ValueError) as ctx:
            execute_forex_guarded_order(
                {}, mock_fn,
                symbol="XAGUSD", intent="ENTRY",
                account_id="",      # missing account
            )
        self.assertIn("account_id", str(ctx.exception))
        mock_fn.assert_not_called()

    def test_validated_order_reaches_broker(self):
        """Valid ENTRY with proper account+magic reaches broker call."""
        from core.execution_guard import execute_forex_guarded_order
        mock_fn = MagicMock(return_value={"ticket": 42})
        result = execute_forex_guarded_order(
            {}, mock_fn,
            symbol="XAGUSD", intent="ENTRY",
            account_id="FTMO_10K", magic=62002, expected_magic=62002,
        )
        mock_fn.assert_called_once()
        self.assertEqual(result["ticket"], 42)

    def test_magic_mismatch_blocked(self):
        """Magic number mismatch must block ENTRY to prevent cross-account pollution."""
        from core.execution_guard import execute_forex_guarded_order
        mock_fn = MagicMock(return_value={"ticket": 1})
        with self.assertRaises(ValueError) as ctx:
            execute_forex_guarded_order(
                {}, mock_fn,
                symbol="XAGUSD", intent="ENTRY",
                account_id="FTMO_10K", magic=99999, expected_magic=62002,
            )
        self.assertIn("magic", str(ctx.exception))
        mock_fn.assert_not_called()

    def test_exit_order_allowed_without_account(self):
        """EXIT/CLOSE orders must always pass even if account_id is missing."""
        from core.execution_guard import execute_forex_guarded_order
        mock_fn = MagicMock(return_value=True)
        result = execute_forex_guarded_order(
            {}, mock_fn,
            symbol="XAGUSD", intent="CLOSE_SL",
            account_id="",    # missing — but exit must still be allowed
        )
        mock_fn.assert_called_once()
        self.assertTrue(result)


class TestExecuteGuardedOrder(unittest.TestCase):
    """Test the execute_guarded_order() broker-call wrapper."""

    def test_wraps_and_returns_result(self):
        from core.execution_guard import execute_guarded_order
        mock_fn = MagicMock(return_value={"id": "ORD123", "code": 200})
        result = execute_guarded_order(mock_fn, {"symbol": "NIFTY"}, symbol="NIFTY", intent="ENTRY")
        mock_fn.assert_called_once_with({"symbol": "NIFTY"})
        self.assertEqual(result["id"], "ORD123")

    def test_propagates_exception(self):
        from core.execution_guard import execute_guarded_order
        def bad_fn(*a, **kw): raise ConnectionError("broker down")
        with self.assertRaises(ConnectionError):
            execute_guarded_order(bad_fn, symbol="NIFTY", intent="ENTRY")

    def test_logs_both_entry_and_exit_intents(self):
        from core.execution_guard import execute_guarded_order
        import logging
        mock_fn = MagicMock(return_value=None)
        # Capture all loggers — execute_guarded_order logs at INFO level
        with self.assertLogs(level="INFO") as cm:
            execute_guarded_order(mock_fn, symbol="XAGUSD", intent="CLOSE_SL")
        self.assertTrue(
            any("CLOSE_SL" in line for line in cm.output),
            f"Expected CLOSE_SL in logs. Got: {cm.output}"
        )


class TestSchemaGuard(unittest.TestCase):
    """Test the schema-based ExecutionGuard with Signal objects."""

    def _make_signal(self, symbol="NSE:NIFTY50-FUT", score=75.0, direction="LONG",
                     entry=24000.0, stop_loss=23950.0, target2=24150.0):
        import uuid
        from core.schemas import Signal, Direction, Market, Engine
        return Signal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            market=Market.NSE,
            engine=Engine.NSE_PAPER,
            direction=Direction[direction],
            strategy="ICT_SILVER_BULLET",
            entry=entry,
            stop_loss=stop_loss,
            target1=entry + (target2 - entry) * 0.5,
            target2=target2,
            target3=target2 + (target2 - entry),
            timeframe="15",
            score=score,
            timestamp=datetime.utcnow(),
            window="Morning Silver Bullet",
        )

    def _make_guard(self, capital=200000, blocked=None):
        from core.execution_guard import ExecutionGuard, ExecutionGuardConfig
        from core.schemas import Engine
        cfg = ExecutionGuardConfig(
            engine=Engine.NSE_PAPER,
            capital=capital,
            max_daily_loss_pct=2.0,
            blocked_symbols=blocked or set(),
        )
        return ExecutionGuard(cfg)

    def test_valid_long_signal_passes(self):
        guard = self._make_guard()
        sig = self._make_signal()
        decision = guard.check(sig, daily_loss=0, open_trade_count=0, trades_today=0)
        self.assertTrue(decision.allowed)

    def test_blocked_symbol_denied(self):
        guard = self._make_guard(blocked={"XAUUSD"})
        sig = self._make_signal(symbol="XAUUSD")
        decision = guard.check(sig, daily_loss=0, open_trade_count=0, trades_today=0)
        self.assertFalse(decision.allowed)
        self.assertIn("permanently blocked", decision.reason)

    def test_daily_loss_at_limit_denied(self):
        guard = self._make_guard(capital=10000)
        sig = self._make_signal()
        decision = guard.check(sig, daily_loss=300.0, open_trade_count=0, trades_today=0)
        self.assertFalse(decision.allowed)
        self.assertIn("Daily loss", decision.reason)

    def test_kill_switch_blocks_all(self):
        guard = self._make_guard()
        sig = self._make_signal(score=99)
        decision = guard.check(sig, daily_loss=0, open_trade_count=0,
                                trades_today=0, kill_switch=True)
        self.assertFalse(decision.allowed)
        self.assertIn("Kill switch", decision.reason)

    def test_invalid_long_sl_blocked(self):
        guard = self._make_guard()
        sig = self._make_signal(entry=24000, stop_loss=24050)  # SL above entry = invalid LONG
        decision = guard.check(sig, daily_loss=0, open_trade_count=0, trades_today=0)
        self.assertFalse(decision.allowed)
        self.assertIn("stop_loss >= entry", decision.reason)

    def test_build_intent_requires_allowed_decision(self):
        from core.execution_guard import ExecutionGuardConfig, ExecutionGuard
        from core.schemas import Engine, RiskDecision
        guard = self._make_guard()
        sig = self._make_signal()
        denied = guard.check(sig, daily_loss=999999, open_trade_count=0, trades_today=0)
        self.assertFalse(denied.allowed)
        with self.assertRaises(ValueError):
            guard.build_intent(sig, denied, account_id="TEST", quantity=1)


class TestTrueDataDeprecation(unittest.TestCase):
    """data.truedata_feed is the primary active TrueData feed (rewritten 2026-05-30).
    It is NOT deprecated — the original test was wrong. These tests verify
    the module imports cleanly and exposes its public API."""

    def test_import_warns(self):
        # data/truedata_feed.py is the live primary feed — it must NOT raise
        # on import and must NOT emit DeprecationWarning (it is not a shim).
        import warnings
        import importlib
        import sys
        sys.modules.pop("data.truedata_feed", None)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.import_module("data.truedata_feed")
        deprecations = [x for x in w if issubclass(x.category, DeprecationWarning)]
        self.assertEqual(
            deprecations, [],
            "data.truedata_feed is the primary active feed and must not emit DeprecationWarning"
        )

    def test_functions_still_accessible(self):
        """Shim must not break callers even after deprecation warning."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from data.truedata_feed import fyers_to_td_symbol, tf_to_bar_size
        self.assertEqual(fyers_to_td_symbol("NSE:NIFTY50-FUT"), "NIFTY-I")
        self.assertEqual(tf_to_bar_size("15"), "15min")
        self.assertEqual(tf_to_bar_size("D"), "eod")


class TestDashboardImport(unittest.TestCase):
    """Task 5: dashboard still imports and generate_dashboard is callable."""

    def test_dashboard_module_importable(self):
        import importlib
        try:
            mod = importlib.import_module("dashboard")
            self.assertTrue(hasattr(mod, "start_dashboard"))
            self.assertTrue(hasattr(mod, "archive_trades"))
            self.assertTrue(hasattr(mod, "generate_dashboard"))
        except Exception as e:
            self.fail(f"dashboard import failed: {e}")

    def test_generate_dashboard_is_callable(self):
        import dashboard as dash
        self.assertTrue(callable(dash.generate_dashboard))

    def test_dashboard_state_reader_importable(self):
        """dashboard/state_reader.py is a standalone utility."""
        import importlib, sys
        sys.path.insert(0, ".")
        try:
            spec = importlib.util.spec_from_file_location(
                "dashboard_state_reader", "dashboard/state_reader.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertTrue(hasattr(mod, "load_nse_state"))
            self.assertTrue(hasattr(mod, "load_market_context"))
        except Exception as e:
            self.fail(f"dashboard/state_reader.py failed to load: {e}")


if __name__ == "__main__":
    unittest.main()
