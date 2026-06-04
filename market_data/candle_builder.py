"""
Real-time candle (OHLCV bar) builder from raw ticks.

Aggregates incoming :class:`MarketTick` objects into completed
:class:`MarketBar` objects at configurable time intervals.

Usage::

    builder = CandleBuilder(interval_minutes=1)
    builder.on_bar = lambda bar: print(bar)

    for tick in ticks:
        completed_bar = builder.add_tick(tick)
        if completed_bar:
            process_bar(completed_bar)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from provider.truedata.models import MarketBar, MarketTick

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")

# Supported intervals (minutes)
SUPPORTED_INTERVALS: tuple[int, ...] = (1, 3, 5, 10, 15, 30, 60)


class _BarState:
    """Mutable state for a single in-progress bar."""

    __slots__ = (
        "symbol", "exchange", "interval_minutes",
        "bar_open_time", "open", "high", "low", "close",
        "volume", "oi", "tick_count",
    )

    def __init__(
        self,
        symbol: str,
        exchange: str,
        interval_minutes: int,
        bar_open_time: datetime,
        first_price: float,
        volume: int,
        oi: Optional[int],
    ) -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.interval_minutes = interval_minutes
        self.bar_open_time = bar_open_time
        self.open = first_price
        self.high = first_price
        self.low = first_price
        self.close = first_price
        self.volume = volume
        self.oi = oi
        self.tick_count = 1

    def update(self, tick: MarketTick) -> None:
        """Incorporate a new tick into the running bar."""
        price = tick.ltp
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.close = price
        if tick.volume is not None:
            self.volume += tick.volume
        if tick.oi is not None:
            self.oi = tick.oi
        self.tick_count += 1

    def to_bar(self, interval_str: str) -> MarketBar:
        """Convert to an immutable MarketBar."""
        return MarketBar(
            symbol=self.symbol,
            exchange=self.exchange,
            timestamp=self.bar_open_time,
            bar_time=self.bar_open_time,
            interval=interval_str,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            oi=self.oi,
        )


class CandleBuilder:
    """
    Builds OHLCV bars from a stream of real-time ticks.

    One instance per (symbol, interval) pair is recommended.  A single
    instance handles multiple symbols if needed — each symbol maintains
    independent state.

    Parameters
    ----------
    interval_minutes:
        Bar interval in minutes.  Must be one of
        ``(1, 3, 5, 10, 15, 30, 60)``.
    on_bar:
        Optional callback invoked each time a bar completes.  Receives
        the completed :class:`MarketBar`.

    Raises
    ------
    ValueError
        If ``interval_minutes`` is not in ``SUPPORTED_INTERVALS``.
    """

    def __init__(
        self,
        interval_minutes: int = 1,
        on_bar: Optional[Callable[[MarketBar], None]] = None,
    ) -> None:
        if interval_minutes not in SUPPORTED_INTERVALS:
            raise ValueError(
                f"interval_minutes={interval_minutes} not supported. "
                f"Valid: {SUPPORTED_INTERVALS}"
            )
        self._interval_minutes = interval_minutes
        self._interval_str = (
            f"{interval_minutes}min" if interval_minutes < 60 else "60min"
        )
        self._interval_td = timedelta(minutes=interval_minutes)
        self.on_bar = on_bar

        # Per-symbol state: symbol -> _BarState
        self._states: dict[str, _BarState] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def interval_minutes(self) -> int:
        """Bar interval in minutes."""
        return self._interval_minutes

    @property
    def interval_str(self) -> str:
        """Bar interval as TrueData interval string, e.g. ``'5min'``."""
        return self._interval_str

    def add_tick(self, tick: MarketTick) -> Optional[MarketBar]:
        """
        Process a tick and return a completed bar if an interval boundary
        has been crossed.

        Parameters
        ----------
        tick:
            The incoming :class:`MarketTick`.

        Returns
        -------
        MarketBar or None
            The completed bar, or None if the current bar is still open.
        """
        symbol = tick.symbol
        tick_bar_open = _bar_open_time(tick.timestamp, self._interval_minutes)

        state = self._states.get(symbol)

        if state is None:
            # First tick for this symbol — start a new bar
            self._states[symbol] = _BarState(
                symbol=symbol,
                exchange=tick.exchange,
                interval_minutes=self._interval_minutes,
                bar_open_time=tick_bar_open,
                first_price=tick.ltp,
                volume=tick.volume or 0,
                oi=tick.oi,
            )
            return None

        if tick_bar_open == state.bar_open_time:
            # Same bar — update running OHLCV
            state.update(tick)
            return None

        if tick_bar_open > state.bar_open_time:
            # New bar interval — close the old bar and emit it
            completed_bar = state.to_bar(self._interval_str)

            # Fill gap bars if there are missing intervals
            gap_bars = self._fill_gaps(state, tick_bar_open, completed_bar.close)

            # Start new bar
            self._states[symbol] = _BarState(
                symbol=symbol,
                exchange=tick.exchange,
                interval_minutes=self._interval_minutes,
                bar_open_time=tick_bar_open,
                first_price=tick.ltp,
                volume=tick.volume or 0,
                oi=tick.oi,
            )

            # Emit completed bar(s) via callback
            for gap_bar in gap_bars:
                logger.debug(
                    "Gap bar emitted for %s at %s",
                    symbol, gap_bar.timestamp.isoformat(),
                )
                if self.on_bar:
                    try:
                        self.on_bar(gap_bar)
                    except Exception as exc:
                        logger.warning("on_bar callback raised: %s", exc)

            if self.on_bar:
                try:
                    self.on_bar(completed_bar)
                except Exception as exc:
                    logger.warning("on_bar callback raised: %s", exc)

            return completed_bar

        # Tick is older than current bar open — discard as late tick
        logger.debug(
            "Late tick discarded for %s: tick_bar=%s current_bar=%s",
            symbol,
            tick_bar_open.isoformat(),
            state.bar_open_time.isoformat(),
        )
        return None

    def flush(self, symbol: str) -> Optional[MarketBar]:
        """
        Emit the current partial bar for a symbol (e.g. at EOD).

        The state for the symbol is cleared after flush.

        Parameters
        ----------
        symbol:
            Symbol string.

        Returns
        -------
        MarketBar or None
            The partial bar, or None if no ticks have been received.
        """
        state = self._states.pop(symbol, None)
        if state is None:
            return None

        bar = state.to_bar(self._interval_str)
        if self.on_bar:
            try:
                self.on_bar(bar)
            except Exception as exc:
                logger.warning("on_bar callback raised during flush: %s", exc)
        return bar

    def flush_all(self) -> list[MarketBar]:
        """
        Flush all symbols and return their partial bars.

        Returns
        -------
        list[MarketBar]
        """
        symbols = list(self._states.keys())
        return [b for s in symbols if (b := self.flush(s)) is not None]

    def get_open_bar(self, symbol: str) -> Optional[MarketBar]:
        """
        Return the current (incomplete) bar for a symbol without closing it.

        Returns
        -------
        MarketBar or None
        """
        state = self._states.get(symbol)
        if state is None:
            return None
        return state.to_bar(self._interval_str)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fill_gaps(
        self,
        state: _BarState,
        new_bar_open: datetime,
        last_close: float,
    ) -> list[MarketBar]:
        """
        Generate phantom bars for intervals with no ticks.

        Each gap bar has OHLCV = (last_close, last_close, last_close,
        last_close, 0) so downstream code sees a flat bar rather than
        a hole in the series.
        """
        gap_bars: list[MarketBar] = []
        next_open = state.bar_open_time + self._interval_td

        # Cap gap filling to avoid huge loops on long gaps
        max_gaps = 20
        gap_count = 0

        while next_open < new_bar_open and gap_count < max_gaps:
            gap_bars.append(
                MarketBar(
                    symbol=state.symbol,
                    exchange=state.exchange,
                    timestamp=next_open,
                    bar_time=next_open,
                    interval=self._interval_str,
                    open=last_close,
                    high=last_close,
                    low=last_close,
                    close=last_close,
                    volume=0,
                    oi=state.oi,
                )
            )
            next_open += self._interval_td
            gap_count += 1

        if gap_count == max_gaps and next_open < new_bar_open:
            logger.info(
                "Large gap for %s: capped gap fill at %d bars",
                state.symbol, max_gaps,
            )

        return gap_bars


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _bar_open_time(ts: datetime, interval_minutes: int) -> datetime:
    """
    Return the open time of the bar that contains ``ts``.

    Truncates the timestamp to the nearest ``interval_minutes`` boundary.
    """
    minute = ts.minute
    bar_minute = (minute // interval_minutes) * interval_minutes
    return ts.replace(minute=bar_minute, second=0, microsecond=0)
