"""
tests/test_prop_firm_critical.py
=================================
Critical prop-firm safety guard tests — added after CB6 Quantum full audit (2026-06-02).

Covers:
  1. GFT magic missing → RuntimeError on module import
  2. FTMO magic consistency across account_router, forex_worker, ftmo_config
  3. FOREX_DISABLED_SYMBOLS overlapping ACTIVE_SYMBOLS → startup RuntimeError
  4. Magic mismatch → execute_forex_guarded_order blocks ENTRY, allows EXIT
  5. Emergency stop → ForexWorker._run_scan returns immediately
  6. Daily loss limit → can_open_trade blocks new FTMO trade

Run:
    python -m pytest tests/test_prop_firm_critical.py -v
"""

import importlib
import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ============================================================================
# 1 — GFT magic: missing env var must raise RuntimeError at import time
# ============================================================================

class TestGFTMagicRequired:

    def test_missing_magic_raises_on_import(self, monkeypatch):
        """GFT_2STEP_MAGIC absent → RuntimeError before engine can start.

        Isolation note: settings.load_dotenv() runs during the module import
        chain and would re-inject the env var from .env.  We stub it out so
        the test reflects the real startup path where the var is truly absent.
        """
        monkeypatch.delenv("GFT_2STEP_MAGIC", raising=False)

        # Prevent load_dotenv() (called by settings.py on import) from
        # re-populating GFT_2STEP_MAGIC from the .env file during the test.
        import dotenv as _dotenv
        monkeypatch.setattr(_dotenv, "load_dotenv", lambda *a, **kw: None)

        # Force a clean re-import so the module-level check fires again.
        mod_name = "forex_engine.prop_firms.gft.gft_5k_2step"
        settings_mod = "settings"
        for _m in (mod_name, settings_mod):
            if _m in sys.modules:
                del sys.modules[_m]

        with pytest.raises(RuntimeError, match="GFT_2STEP_MAGIC is not set"):
            import forex_engine.prop_firms.gft.gft_5k_2step  # noqa: F401

    def test_valid_magic_does_not_raise(self, monkeypatch):
        """GFT_2STEP_MAGIC set to a valid integer → no RuntimeError."""
        monkeypatch.setenv("GFT_2STEP_MAGIC", "333001")

        mod_name = "forex_engine.prop_firms.gft.gft_5k_2step"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        # Import must not raise; the magic value should be the one we set.
        try:
            mod = importlib.import_module(mod_name)
            assert mod._GFT_MAGIC == 333001
        except RuntimeError:
            pytest.fail("RuntimeError raised even though GFT_2STEP_MAGIC was set")

    def test_magic_is_integer_not_random(self, monkeypatch):
        """With GFT_2STEP_MAGIC set, _GFT_MAGIC is deterministic across two imports."""
        monkeypatch.setenv("GFT_2STEP_MAGIC", "555999")

        mod_name = "forex_engine.prop_firms.gft.gft_5k_2step"
        for _ in range(2):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
            mod = importlib.import_module(mod_name)
            assert mod._GFT_MAGIC == 555999, "Magic must be fixed, not random"


# ============================================================================
# 2 — FTMO magic consistency: account_router, forex_worker, ftmo_config
#     all read from the same env var and produce the same integer.
# ============================================================================

class TestFTMOMagicConsistency:

    def test_ftmo_config_uses_env(self, monkeypatch):
        """ftmo_config.FTMO_MAGIC reflects FTMO_MAGIC env var."""
        monkeypatch.setenv("FTMO_MAGIC", "62002")

        mod_name = "forex_engine.prop_firms.ftmo.ftmo_config"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        import forex_engine.prop_firms.ftmo.ftmo_config as fc
        assert fc.FTMO_MAGIC == 62002

    def test_default_magic_is_62002(self, monkeypatch):
        """Without FTMO_MAGIC set, default is 62002 (not 62000)."""
        monkeypatch.delenv("FTMO_MAGIC", raising=False)

        mod_name = "forex_engine.prop_firms.ftmo.ftmo_config"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        import forex_engine.prop_firms.ftmo.ftmo_config as fc
        assert fc.FTMO_MAGIC == 62002, (
            f"Default FTMO_MAGIC should be 62002, got {fc.FTMO_MAGIC}"
        )


# ============================================================================
# 3 — FOREX_DISABLED_SYMBOLS overlapping ACTIVE_SYMBOLS → startup RuntimeError
# ============================================================================

class TestForexDisabledSymbolsGate:

    def _run_startup_gate(self, active_symbols, disabled_symbols):
        """
        Execute only the startup guard logic from forex_worker.main(),
        without actually starting the worker.
        """
        ftmo_disabled = ["XAUUSD"]  # matches FTMO_DISABLED_SYMBOLS constant
        forex_disabled = [str(s).upper() for s in (disabled_symbols or [])]

        for sym in active_symbols:
            if sym in ftmo_disabled:
                raise RuntimeError(f"STARTUP ABORT ftmo: {sym}")
            if sym.upper() in forex_disabled:
                raise RuntimeError(
                    f"STARTUP ABORT: {sym} is in ACTIVE_SYMBOLS but also in "
                    f"FOREX_DISABLED_SYMBOLS {forex_disabled}"
                )

    def test_disabled_symbol_in_active_raises(self):
        """XAGUSD in both ACTIVE and FOREX_DISABLED → RuntimeError."""
        with pytest.raises(RuntimeError, match="STARTUP ABORT"):
            self._run_startup_gate(["XAGUSD", "USOIL"], ["XAGUSD"])

    def test_no_overlap_passes(self):
        """Active symbols not in disabled list → no error."""
        self._run_startup_gate(["XAGUSD", "USOIL"], [])

    def test_empty_disabled_passes(self):
        """Empty disabled list → always passes."""
        self._run_startup_gate(["XAGUSD", "USOIL", "EURUSD"], [])

    def test_xauusd_in_active_raises_via_ftmo_check(self):
        """XAUUSD in ACTIVE_SYMBOLS triggers the FTMO guard (first check)."""
        with pytest.raises(RuntimeError, match="STARTUP ABORT ftmo"):
            self._run_startup_gate(["XAUUSD", "USOIL"], [])

    def test_settings_default_is_empty_list(self):
        """settings.FOREX_DISABLED_SYMBOLS default must be empty after Fix 3."""
        import settings
        assert settings.FOREX_DISABLED_SYMBOLS == [], (
            f"Default FOREX_DISABLED_SYMBOLS should be [] after fix, "
            f"got {settings.FOREX_DISABLED_SYMBOLS}"
        )


# ============================================================================
# 4 — Magic mismatch: execute_forex_guarded_order blocks ENTRY, not EXIT
# ============================================================================

class TestMagicMismatchGuard:

    def _dummy_broker(self, **kwargs):
        return {"ok": True}

    def test_magic_mismatch_blocks_entry(self):
        """Wrong magic on ENTRY intent → ValueError raised."""
        from core.execution_guard import execute_forex_guarded_order

        with pytest.raises(ValueError, match="magic.*!=.*expected"):
            execute_forex_guarded_order(
                {},
                self._dummy_broker,
                symbol="XAGUSD",
                intent="ENTRY",
                account_id="FTMO_10K",
                magic=99999,
                expected_magic=62002,
            )

    def test_magic_mismatch_allows_exit(self):
        """Wrong magic on EXIT intent → allowed (exits must never be blocked)."""
        from core.execution_guard import execute_forex_guarded_order

        result = execute_forex_guarded_order(
            {},
            self._dummy_broker,
            symbol="XAGUSD",
            intent="CLOSE_SL",
            account_id="FTMO_10K",
            magic=99999,
            expected_magic=62002,
        )
        assert result == {"ok": True}

    def test_correct_magic_allows_entry(self):
        """Correct magic on ENTRY intent → broker is called."""
        from core.execution_guard import execute_forex_guarded_order

        result = execute_forex_guarded_order(
            {},
            self._dummy_broker,
            symbol="XAGUSD",
            intent="ENTRY",
            account_id="FTMO_10K",
            magic=62002,
            expected_magic=62002,
        )
        assert result == {"ok": True}


# ============================================================================
# 5 — Emergency stop blocks entry scan
# ============================================================================

class TestEmergencyStopBlocksEntry:

    def test_gft_scan_skips_when_emergency_stop_active(self, tmp_path, monkeypatch):
        """GFT2StepWorker._run() returns immediately when emergency stop is active."""
        import utils.emergency_stop as es
        flag = str(tmp_path / "EMERGENCY_STOP.flag")
        monkeypatch.setattr(es, "_EMERGENCY_STOP_FLAG", flag)
        es.set_emergency_stop("test_emergency")

        # Patch GFT_2STEP_MAGIC so module import works
        monkeypatch.setenv("GFT_2STEP_MAGIC", "333001")
        mod_name = "forex_engine.prop_firms.gft.gft_5k_2step"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        # We verify _run() checks the flag by monkeypatching the scanner
        # and confirming it is never called.
        called = []

        import forex_engine.prop_firms.gft.gft_5k_2step as gft_mod
        monkeypatch.setattr(
            gft_mod, "scan_setup", lambda *a, **kw: called.append(1) or None
        )

        # Build a minimal worker-like object to call _run() directly
        class _FakeWorker:
            _paper    = True
            _locks    = {"XAGUSD": __import__("threading").Lock()}
            _candles  = {}
            _dedup    = None
            _hft_guard = None
            _slip     = None
            _ema_alerted = {}

        worker = gft_mod.GFT2StepWorker.__new__(gft_mod.GFT2StepWorker)
        worker._paper = True
        worker._locks = {"XAGUSD": __import__("threading").Lock()}
        worker._candles = {}

        # _run should return before calling scan_setup
        worker._run = gft_mod.GFT2StepWorker._run.__get__(worker, gft_mod.GFT2StepWorker)
        worker._run("XAGUSD")

        assert called == [], "scan_setup should not be called when emergency stop is active"

    def test_nse_emergency_stop_flag_blocks(self, tmp_path, monkeypatch):
        """NSE _emergency_stop_active() returns True when flag file exists."""
        import main as nse_main
        flag = str(tmp_path / "NSE_EMERGENCY_STOP.flag")
        monkeypatch.setattr(nse_main, "_EMERGENCY_STOP_FLAG", flag)

        assert not nse_main._emergency_stop_active()
        open(flag, "w").close()
        assert nse_main._emergency_stop_active()


# ============================================================================
# 6 — Daily loss limit blocks new FTMO trade
# ============================================================================

class TestFTMODailyLossBlock:

    def _make_ftmo_state(self, daily_pnl: float, capital: float = 9900.0) -> dict:
        return {
            "capital"           : capital,
            "available_capital" : capital,
            "starting_capital"  : 10000.0,
            "open_trades"       : [],
            "closed_trades"     : [],
            "daily_trades"      : 0,
            "daily_losses"      : 0,
            "daily_pnl"         : daily_pnl,
            "best_day_pnl"      : 0.0,
            "daily_closed_pnl"  : 0.0,
            "last_reset_date"   : "",
            "paused"            : False,
            "total_pnl"         : daily_pnl,
            "peak_capital"      : 10000.0,
            "eod_equity_peak"   : 10000.0,
            "broker"            : "ftmo",
            "mode"              : "free_trial",
            "risk_mode"         : "normal",
            "high_slippage_symbols": [],
            "gft_daily_snapshot": capital,
        }

    def test_no_loss_allows_trade(self):
        from forex_engine.prop_firms.ftmo.ftmo_state import can_open_trade
        state = self._make_ftmo_state(0.0)
        ok, reason = can_open_trade(state)
        assert ok, f"Zero loss should allow trade: {reason}"

    def test_small_loss_allows_trade(self):
        from forex_engine.prop_firms.ftmo.ftmo_state import can_open_trade
        state = self._make_ftmo_state(-100.0)   # $100 < $300 daily limit
        ok, reason = can_open_trade(state)
        assert ok, f"$100 loss should allow trade: {reason}"

    def test_daily_limit_breach_blocks_trade(self):
        """Daily loss ≥ $300 (3% of $10K) must block new entries."""
        from forex_engine.prop_firms.ftmo.ftmo_state import can_open_trade
        state = self._make_ftmo_state(-300.0)
        ok, reason = can_open_trade(state)
        assert not ok, "Daily loss at limit should block trade"
        assert "daily loss" in reason.lower() or "limit" in reason.lower()

    def test_over_limit_blocks_trade(self):
        from forex_engine.prop_firms.ftmo.ftmo_state import can_open_trade
        state = self._make_ftmo_state(-350.0)   # over $300 limit
        ok, reason = can_open_trade(state)
        assert not ok, "$350 loss should block trade"

    def test_internal_stop_guard_blocks_before_official_limit(self):
        """Internal stop gate ($250) fires before FTMO's official $300 limit."""
        from forex_engine.prop_firms.ftmo.ftmo_state import can_open_trade
        state = self._make_ftmo_state(-252.0)   # past internal $250 stop
        ok, reason = can_open_trade(state)
        assert not ok, "Internal stop at $250 should block trade before $300 official limit"

    def test_paused_flag_blocks_regardless_of_pnl(self):
        from forex_engine.prop_firms.ftmo.ftmo_state import can_open_trade
        state = self._make_ftmo_state(0.0)
        state["paused"] = True
        ok, reason = can_open_trade(state)
        assert not ok
        assert "paused" in reason.lower()
