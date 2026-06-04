"""
Feed health monitor.

Tracks the last tick time per symbol, detects stale feeds during market
hours, and maintains aggregate connection health statistics.

Usage::

    monitor = HealthMonitor()
    monitor.record_tick("NIFTY-I", latency_ms=45.3)
    health = monitor.get_health()
    if monitor.is_stale("NIFTY-I"):
        print("NIFTY-I feed is stale!")
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from provider.truedata.models import ProviderHealth

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")

# Consider feed stale if no tick received for this many seconds during mkt hours
_STALE_THRESHOLD_SECONDS = 30

# NSE market hours (IST)
_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MINUTE = 15
_MARKET_CLOSE_HOUR = 15
_MARKET_CLOSE_MINUTE = 30


class HealthMonitor:
    """
    Monitors real-time feed health for multiple symbols.

    Parameters
    ----------
    provider_name:
        Name of the data provider (used in health reports).
    stale_threshold_seconds:
        Number of seconds without a tick before a feed is flagged stale.
    """

    def __init__(
        self,
        provider_name: str = "truedata",
        stale_threshold_seconds: int = _STALE_THRESHOLD_SECONDS,
    ) -> None:
        self._provider_name = provider_name
        self._stale_threshold = stale_threshold_seconds
        self._lock = threading.Lock()

        # Per-symbol state
        self._last_tick_time: dict[str, datetime] = {}
        self._tick_counts: dict[str, int] = {}
        self._latency_samples: dict[str, list[float]] = {}

        # Connection state
        self._connected = False
        self._last_heartbeat: Optional[datetime] = None
        self._reconnect_count = 0
        self._error_count = 0
        self._connect_time: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_tick(self, symbol: str, latency_ms: Optional[float] = None) -> None:
        """
        Record that a tick was received for ``symbol``.

        Parameters
        ----------
        symbol:
            Symbol that sent the tick.
        latency_ms:
            Tick latency in milliseconds (exchange time vs receive time).
        """
        now = datetime.now(tz=_IST)
        with self._lock:
            self._last_tick_time[symbol] = now
            self._tick_counts[symbol] = self._tick_counts.get(symbol, 0) + 1

            if latency_ms is not None and 0 < latency_ms < 60_000:
                if symbol not in self._latency_samples:
                    self._latency_samples[symbol] = []
                self._latency_samples[symbol].append(latency_ms)
                # Keep last 500 samples per symbol
                if len(self._latency_samples[symbol]) > 500:
                    self._latency_samples[symbol] = self._latency_samples[symbol][-500:]

    def record_heartbeat(self) -> None:
        """Record receipt of a heartbeat from the data provider."""
        with self._lock:
            self._last_heartbeat = datetime.now(tz=_IST)

    def record_connect(self) -> None:
        """Record a successful connection event."""
        with self._lock:
            self._connected = True
            self._connect_time = datetime.now(tz=_IST)
        logger.info("HealthMonitor: provider '%s' connected", self._provider_name)

    def record_disconnect(self) -> None:
        """Record a disconnection event."""
        with self._lock:
            self._connected = False
        logger.warning(
            "HealthMonitor: provider '%s' disconnected", self._provider_name
        )

    def record_reconnect(self) -> None:
        """Record a reconnection attempt."""
        with self._lock:
            self._reconnect_count += 1
        logger.info(
            "HealthMonitor: reconnect #%d for '%s'",
            self._reconnect_count, self._provider_name,
        )

    def record_error(self) -> None:
        """Increment the error counter."""
        with self._lock:
            self._error_count += 1

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def is_stale(self, symbol: str) -> bool:
        """
        Return True if no tick has been received for ``symbol`` within
        the stale threshold during market hours.

        Outside market hours always returns False.

        Parameters
        ----------
        symbol:
            Symbol to check.

        Returns
        -------
        bool
        """
        if not _is_market_hours():
            return False

        with self._lock:
            last = self._last_tick_time.get(symbol)

        if last is None:
            return True  # Never received a tick

        now = datetime.now(tz=_IST)
        elapsed = (now - last).total_seconds()
        return elapsed > self._stale_threshold

    def get_stale_symbols(self) -> list[str]:
        """Return a list of symbols currently flagged as stale."""
        with self._lock:
            symbols = list(self._last_tick_time.keys())
        return [s for s in symbols if self.is_stale(s)]

    def get_health(self) -> ProviderHealth:
        """
        Return a current :class:`ProviderHealth` snapshot.

        Returns
        -------
        ProviderHealth
        """
        with self._lock:
            connected = self._connected
            last_heartbeat = self._last_heartbeat
            reconnect_count = self._reconnect_count
            error_count = self._error_count

            # Most recent tick across all symbols
            last_tick_time: Optional[datetime] = None
            if self._last_tick_time:
                last_tick_time = max(self._last_tick_time.values())

            # Average latency across all symbols (last samples)
            all_samples: list[float] = []
            for samples in self._latency_samples.values():
                all_samples.extend(samples[-20:])
            latency_ms = (
                sum(all_samples) / len(all_samples) if all_samples else None
            )

        stale = self.get_stale_symbols()
        if not connected:
            status = "disconnected"
        elif stale:
            status = f"degraded (stale: {', '.join(stale[:3])})"
        elif reconnect_count > 0:
            status = f"connected (reconnects={reconnect_count})"
        else:
            status = "connected"

        return ProviderHealth(
            provider=self._provider_name,
            connected=connected,
            last_heartbeat=last_heartbeat,
            last_tick_time=last_tick_time,
            reconnect_count=reconnect_count,
            error_count=error_count,
            latency_ms=latency_ms,
            status=status,
        )

    def get_symbol_tick_count(self, symbol: str) -> int:
        """Return the total tick count received for a symbol."""
        with self._lock:
            return self._tick_counts.get(symbol, 0)

    def get_last_tick_time(self, symbol: str) -> Optional[datetime]:
        """Return the last tick time for a symbol, or None."""
        with self._lock:
            return self._last_tick_time.get(symbol)

    def get_latency_summary(self, symbol: str) -> dict:
        """
        Return a latency summary dict for a symbol.

        Returns
        -------
        dict with keys: count, mean_ms, min_ms, max_ms
        """
        with self._lock:
            samples = list(self._latency_samples.get(symbol, []))
        if not samples:
            return {"count": 0, "mean_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}
        return {
            "count": len(samples),
            "mean_ms": sum(samples) / len(samples),
            "min_ms": min(samples),
            "max_ms": max(samples),
        }

    def reset(self) -> None:
        """Clear all health state (for testing)."""
        with self._lock:
            self._last_tick_time.clear()
            self._tick_counts.clear()
            self._latency_samples.clear()
            self._connected = False
            self._last_heartbeat = None
            self._reconnect_count = 0
            self._error_count = 0
            self._connect_time = None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _is_market_hours() -> bool:
    """Return True if the current IST time is within NSE trading hours."""
    now = datetime.now(tz=_IST)
    # Weekdays only (Mon=0 … Fri=4)
    if now.weekday() >= 5:
        return False
    open_minutes = _MARKET_OPEN_HOUR * 60 + _MARKET_OPEN_MINUTE
    close_minutes = _MARKET_CLOSE_HOUR * 60 + _MARKET_CLOSE_MINUTE
    current_minutes = now.hour * 60 + now.minute
    return open_minutes <= current_minutes <= close_minutes
