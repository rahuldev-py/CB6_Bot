"""
tests/test_data_health.py
==========================
TrueData health monitor + Fyers fallback tests — added 2026-06-02.

Verifies:
  1. Stale tick blocks trading (no tick > STALE_TICK_SECS)
  2. Fresh tick allows scan
  3. Reconnect storm → provider marked unhealthy → Fyers fallback active
  4. Recovery resets health state after stable tick flow
  5. Fyers fallback: get_historical_data skips TrueData when unhealthy
  6. Both stale: get_historical_data returns None (scanner blocked)
  7. Bar freshness gate: old last-candle timestamp rejected during market hours
  8. Bar freshness gate: old bars allowed outside market hours

Run:
    python -m pytest tests/test_data_health.py -v
"""

import os
import sys
import time
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _fresh_monitor():
    """Return a brand-new DataHealthMonitor with cleared singleton state."""
    import data.data_health as dh
    # Reset singleton so each test starts clean
    dh.DataHealthMonitor._instance = None
    monitor = dh.DataHealthMonitor()
    return monitor


# ============================================================================
# 1 & 2. Tick staleness
# ============================================================================

class TestTickStaleness:

    def test_no_tick_ever_is_stale(self):
        """Symbol with no tick yet is stale during market hours."""
        monitor = _fresh_monitor()
        with patch("data.data_health._is_market_hours", return_value=True):
            assert monitor.is_tick_stale("NIFTY-I") is True

    def test_fresh_tick_not_stale(self):
        """Tick received just now is not stale."""
        monitor = _fresh_monitor()
        monitor.record_tick("NIFTY-I")
        with patch("data.data_health._is_market_hours", return_value=True):
            assert monitor.is_tick_stale("NIFTY-I") is False

    def test_old_tick_is_stale(self):
        """Tick older than STALE_TICK_SECS is stale."""
        from data.data_health import STALE_TICK_SECS
        monitor = _fresh_monitor()
        # Backdate the last tick
        with monitor._lock:
            monitor._last_tick["NIFTY-I"] = time.monotonic() - STALE_TICK_SECS - 1
        with patch("data.data_health._is_market_hours", return_value=True):
            assert monitor.is_tick_stale("NIFTY-I") is True

    def test_stale_check_skipped_outside_market_hours(self):
        """Outside market hours, staleness check always returns False."""
        monitor = _fresh_monitor()
        # Never got a tick
        with patch("data.data_health._is_market_hours", return_value=False):
            assert monitor.is_tick_stale("NIFTY-I") is False

    def test_is_trading_safe_blocked_by_stale_tick(self):
        """is_trading_safe returns False when a tracked symbol is stale."""
        from data.data_health import STALE_TICK_SECS
        monitor = _fresh_monitor()
        with monitor._lock:
            monitor._last_tick["NIFTY-I"] = time.monotonic() - STALE_TICK_SECS - 5

        with patch("data.data_health._is_market_hours", return_value=True):
            safe, reason = monitor.is_trading_safe(truedata_symbols=["NIFTY-I"])

        assert safe is False
        assert "stale" in reason.lower() or "Tick" in reason

    def test_is_trading_safe_ok_with_fresh_tick(self):
        """is_trading_safe returns True when tick is fresh."""
        monitor = _fresh_monitor()
        monitor.record_tick("NIFTY-I")
        monitor.record_tick("BANKNIFTY-I")

        with patch("data.data_health._is_market_hours", return_value=True):
            safe, reason = monitor.is_trading_safe(
                truedata_symbols=["NIFTY-I", "BANKNIFTY-I"]
            )

        assert safe is True
        assert reason == "OK"


# ============================================================================
# 3. Reconnect storm → provider unhealthy
# ============================================================================

class TestReconnectStorm:

    def test_reconnect_storm_marks_unhealthy(self):
        """5 gap events in 60s marks TrueData as unhealthy."""
        from data.data_health import (
            RECONNECT_GAP_SECS, UNHEALTHY_RECONNECT_THRESHOLD
        )
        monitor = _fresh_monitor()

        # Simulate UNHEALTHY_RECONNECT_THRESHOLD gap events by recording ticks
        # with simulated silence gaps between them
        base = time.monotonic() - (RECONNECT_GAP_SECS + 2) * (UNHEALTHY_RECONNECT_THRESHOLD + 1)

        for i in range(UNHEALTHY_RECONNECT_THRESHOLD + 1):
            # Simulate gap: previous tick was > RECONNECT_GAP_SECS ago
            with monitor._lock:
                monitor._last_tick["NIFTY-I"] = base + i * (RECONNECT_GAP_SECS + 5)
            # Record fresh tick after the gap — this registers a gap event
            monitor.record_tick("NIFTY-I")

        assert monitor.is_healthy() is False, \
            "TrueData should be unhealthy after reconnect storm"

    def test_unhealthy_activates_fyers_fallback(self):
        """When unhealthy, is_fyers_active returns True."""
        from data.data_health import RECONNECT_GAP_SECS, UNHEALTHY_RECONNECT_THRESHOLD
        monitor = _fresh_monitor()

        base = time.monotonic() - (RECONNECT_GAP_SECS + 2) * (UNHEALTHY_RECONNECT_THRESHOLD + 1)
        for i in range(UNHEALTHY_RECONNECT_THRESHOLD + 1):
            with monitor._lock:
                monitor._last_tick["NIFTY-I"] = base + i * (RECONNECT_GAP_SECS + 5)
            monitor.record_tick("NIFTY-I")

        assert monitor.is_fyers_active() is True

    def test_unhealthy_blocks_is_trading_safe(self):
        """is_trading_safe returns False when provider is unhealthy."""
        monitor = _fresh_monitor()
        with monitor._lock:
            monitor._healthy = False

        safe, reason = monitor.is_trading_safe()
        assert safe is False
        assert "unhealthy" in reason.lower()

    def test_single_gap_does_not_mark_unhealthy(self):
        """A single gap event does not trigger unhealthy state."""
        from data.data_health import RECONNECT_GAP_SECS
        monitor = _fresh_monitor()

        # One gap
        with monitor._lock:
            monitor._last_tick["NIFTY-I"] = time.monotonic() - RECONNECT_GAP_SECS - 5
        monitor.record_tick("NIFTY-I")

        assert monitor.is_healthy() is True


# ============================================================================
# 4. Recovery resets health state
# ============================================================================

class TestRecovery:

    def _make_unhealthy(self, monitor):
        """Force monitor into unhealthy state."""
        with monitor._lock:
            monitor._healthy      = False
            monitor._fyers_active = True
            monitor._gap_events   = []   # clear so recovery can proceed

    def test_recovery_after_stable_ticks(self):
        """After RECOVERY_TICKS_NEEDED × 2 fresh ticks on 2+ symbols, health restores."""
        from data.data_health import RECOVERY_TICKS_NEEDED
        monitor = _fresh_monitor()
        self._make_unhealthy(monitor)

        # Feed fresh ticks on two symbols
        needed = RECOVERY_TICKS_NEEDED * 2
        for i in range(needed + 1):
            sym = "NIFTY-I" if i % 2 == 0 else "BANKNIFTY-I"
            monitor.record_tick(sym)

        assert monitor.is_healthy() is True, "Should have recovered after stable ticks"
        assert monitor.is_fyers_active() is False

    def test_recovery_resets_fyers_fallback(self):
        """After recovery, Fyers fallback is no longer active."""
        from data.data_health import RECOVERY_TICKS_NEEDED
        monitor = _fresh_monitor()
        self._make_unhealthy(monitor)

        for i in range(RECOVERY_TICKS_NEEDED * 2 + 2):
            monitor.record_tick("NIFTY-I" if i % 2 == 0 else "BANKNIFTY-I")

        assert monitor.is_fyers_active() is False

    def test_partial_recovery_not_enough(self):
        """Only 1 symbol ticking is not enough for recovery."""
        monitor = _fresh_monitor()
        self._make_unhealthy(monitor)

        # Only feed one symbol — not enough for multi-symbol confirmation
        for _ in range(20):
            monitor.record_tick("NIFTY-I")

        # Should still be unhealthy (only 1 symbol)
        assert monitor.is_healthy() is False


# ============================================================================
# 5. Fyers fallback: get_historical_data skips TrueData when unhealthy
# ============================================================================

class TestFyersFallback:

    def test_unhealthy_truedata_skipped_in_fetcher(self):
        """When DataHealthMonitor is unhealthy, _get_historical_data_truedata returns None."""
        import data.data_health as dh
        dh.DataHealthMonitor._instance = None
        monitor = dh.DataHealthMonitor()
        with monitor._lock:
            monitor._healthy = False

        with patch("data.data_health.get_monitor", return_value=monitor):
            from scanner import data_fetcher
            result = data_fetcher._get_historical_data_truedata(
                "NSE:NIFTY26JUNFUT", "3", 3
            )

        assert result is None, "Unhealthy TrueData should return None"

    def test_healthy_truedata_proceeds_to_fetch(self):
        """When healthy, _get_historical_data_truedata attempts the real fetch."""
        import data.data_health as dh
        dh.DataHealthMonitor._instance = None
        monitor = dh.DataHealthMonitor()
        # Healthy by default

        fake_df = pd.DataFrame({
            "timestamp": pd.date_range("2026-06-02 09:15", periods=30, freq="3min"),
            "open": [24000] * 30, "high": [24050] * 30,
            "low": [23950] * 30,  "close": [24020] * 30, "volume": [1000] * 30,
        })

        mock_td = MagicMock()
        mock_td.is_hist_ready = True
        mock_td.get_historical_bars = MagicMock(return_value=fake_df)

        with patch("data.data_health.get_monitor", return_value=monitor), \
             patch("data.truedata_feed.get_manager", return_value=mock_td), \
             patch("data.data_health._is_market_hours", return_value=False), \
             patch.dict("os.environ", {"TRUEDATA_LIVE_ENABLED": "true"}):
            from scanner import data_fetcher
            data_fetcher.clear_cache()
            result = data_fetcher._get_historical_data_truedata(
                "NSE:NIFTY26JUNFUT", "3", 3
            )

        assert result is not None, "Healthy TrueData should return data"


# ============================================================================
# 6. Both stale: get_historical_data returns None
# ============================================================================

class TestBothStale:

    def test_both_providers_fail_returns_none(self):
        """When TrueData unhealthy AND Fyers returns None, result is None."""
        import data.data_health as dh
        dh.DataHealthMonitor._instance = None
        monitor = dh.DataHealthMonitor()
        with monitor._lock:
            monitor._healthy = False

        mock_fyers = MagicMock()
        mock_fyers.history = MagicMock(return_value={"code": 500, "s": "error"})

        with patch("data.data_health.get_monitor", return_value=monitor), \
             patch("data.data_health._is_market_hours", return_value=False):
            from scanner import data_fetcher
            data_fetcher.clear_cache()
            data_fetcher._CONSECUTIVE_FAILS.clear()
            result = data_fetcher.get_historical_data(
                mock_fyers, "NSE:NIFTY26JUNFUT", "3", days=3
            )

        assert result is None, "Both providers failing must return None"

    def test_both_stale_triggers_telegram_during_market_hours(self):
        """When both providers return None during market hours, Telegram alert fires after threshold."""
        import data.data_health as dh
        dh.DataHealthMonitor._instance = None
        monitor = dh.DataHealthMonitor()
        with monitor._lock:
            monitor._healthy = False

        mock_fyers = MagicMock()
        mock_fyers.history = MagicMock(return_value={"code": 500, "s": "error"})

        with patch("data.data_health.get_monitor", return_value=monitor), \
             patch("data.data_health._is_market_hours", return_value=True), \
             patch.object(monitor, "send_both_stale_alert") as mock_alert:
            from scanner import data_fetcher
            data_fetcher.clear_cache()
            data_fetcher._CONSECUTIVE_FAILS.clear()
            for _ in range(data_fetcher._STALE_ALERT_THRESHOLD):
                data_fetcher.get_historical_data(
                    mock_fyers, "NSE:NIFTY26JUNFUT", "3", days=3
                )

        mock_alert.assert_called()


# ============================================================================
# 7 & 8. Bar freshness gate
# ============================================================================

class TestBarFreshness:

    def _make_df(self, last_candle_minutes_ago: float) -> pd.DataFrame:
        """Build a DataFrame whose last candle is N minutes in the past (IST-naive)."""
        import pytz
        IST = pytz.timezone("Asia/Kolkata")
        last_ts = datetime.now(IST) - timedelta(minutes=last_candle_minutes_ago)
        # Make timezone-naive for simplicity (like TrueData returns)
        last_ts_naive = last_ts.replace(tzinfo=None)
        return pd.DataFrame({
            "timestamp": [last_ts_naive],
            "open": [24000], "high": [24050], "low": [23950], "close": [24020],
            "volume": [1000],
        })

    def test_fresh_bars_accepted_during_market_hours(self):
        """Bars with last candle 5 min ago are accepted."""
        from data.data_health import DataHealthMonitor
        df = self._make_df(5)
        with patch("data.data_health._is_market_hours", return_value=True):
            assert DataHealthMonitor.is_bar_fresh(df, max_age_mins=15) is True

    def test_stale_bars_rejected_during_market_hours(self):
        """Bars with last candle 20 min ago are rejected during market hours."""
        from data.data_health import DataHealthMonitor
        df = self._make_df(20)
        with patch("data.data_health._is_market_hours", return_value=True):
            assert DataHealthMonitor.is_bar_fresh(df, max_age_mins=15) is False

    def test_stale_bars_accepted_outside_market_hours(self):
        """Old bars are accepted outside market hours (no trading concern)."""
        from data.data_health import DataHealthMonitor
        df = self._make_df(60)  # 60 min old
        with patch("data.data_health._is_market_hours", return_value=False):
            assert DataHealthMonitor.is_bar_fresh(df, max_age_mins=15) is True

    def test_empty_df_rejected(self):
        """Empty DataFrame is not fresh."""
        from data.data_health import DataHealthMonitor
        with patch("data.data_health._is_market_hours", return_value=True):
            assert DataHealthMonitor.is_bar_fresh(pd.DataFrame(), max_age_mins=15) is False

    def test_none_df_rejected(self):
        """None is not fresh."""
        from data.data_health import DataHealthMonitor
        with patch("data.data_health._is_market_hours", return_value=True):
            assert DataHealthMonitor.is_bar_fresh(None, max_age_mins=15) is False

    def test_cached_stale_bars_discarded_in_fetcher(self):
        """Cached bars with old last candle are discarded and re-fetch is attempted."""
        import data.data_health as dh
        dh.DataHealthMonitor._instance = None
        monitor = dh.DataHealthMonitor()

        stale_df = self._make_df(20)   # 20-min-old last candle

        mock_fyers = MagicMock()
        mock_fyers.history = MagicMock(return_value={"code": 500, "s": "error"})

        with patch("data.data_health.get_monitor", return_value=monitor), \
             patch("data.data_health._is_market_hours", return_value=True):
            from scanner import data_fetcher
            data_fetcher.clear_cache()
            # Seed the cache with stale bars
            data_fetcher._cache_put("NSE:NIFTY26JUNFUT", "3", 3, stale_df)

            result = data_fetcher.get_historical_data(
                mock_fyers, "NSE:NIFTY26JUNFUT", "3", days=3
            )

        # Cache had stale data → discarded → TrueData (healthy) tried →
        # Fyers fallback → Fyers returns error → None
        assert result is None, "Stale cached bars must be discarded, not returned"
