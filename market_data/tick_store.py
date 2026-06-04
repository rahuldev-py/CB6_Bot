"""
Local tick persistence and quality statistics.

Buffers incoming :class:`MarketTick` objects in memory per symbol,
periodically flushes to CSV, detects missing sequence numbers, and
tracks duplicate ticks.

Usage::

    store = TickStore(output_dir=Path("data/ticks"), max_buffer=10_000)
    store.add(tick)
    store.flush_all()
    stats = store.get_stats("NIFTY-I")
"""

from __future__ import annotations

import csv
import logging
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from provider.truedata.models import FeedLatencyStats, MarketTick

logger = logging.getLogger(__name__)


class TickStore:
    """
    In-memory tick buffer with CSV flush and quality statistics.

    Parameters
    ----------
    output_dir:
        Directory where CSV files are written.  Created automatically.
    max_buffer:
        Maximum ticks to hold per symbol before auto-flushing.
        Default 10,000.
    """

    def __init__(
        self,
        output_dir: Path,
        max_buffer: int = 10_000,
    ) -> None:
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._max_buffer = max_buffer
        self._lock = threading.Lock()

        # symbol -> list of ticks
        self._buffers: dict[str, list[MarketTick]] = defaultdict(list)

        # Quality tracking: symbol -> set of seen seqs
        self._seen_seqs: dict[str, set[int]] = defaultdict(set)
        self._duplicate_counts: dict[str, int] = defaultdict(int)
        self._gap_counts: dict[str, int] = defaultdict(int)

        # Latency tracking: symbol -> list of ms values
        self._latencies: dict[str, list[float]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, tick: MarketTick, receive_time_ms: Optional[float] = None) -> None:
        """
        Buffer a tick and check for duplicates / sequence gaps.

        Parameters
        ----------
        tick:
            The :class:`MarketTick` to store.
        receive_time_ms:
            Wall-clock receive time in milliseconds since epoch.
            Used to compute latency if provided.
        """
        symbol = tick.symbol

        with self._lock:
            # Duplicate check via seq number
            if tick.seq is not None:
                if tick.seq in self._seen_seqs[symbol]:
                    self._duplicate_counts[symbol] += 1
                    logger.debug("Duplicate tick seq=%d for %s", tick.seq, symbol)
                    return  # discard duplicate
                self._seen_seqs[symbol].add(tick.seq)

            # Gap detection
            if tick.seq is not None and self._seen_seqs[symbol]:
                sorted_seqs = sorted(self._seen_seqs[symbol])
                if len(sorted_seqs) >= 2:
                    expected = sorted_seqs[-2] + 1
                    if tick.seq > expected:
                        gap = tick.seq - expected
                        self._gap_counts[symbol] += gap
                        logger.debug(
                            "Sequence gap for %s: expected %d got %d",
                            symbol, expected, tick.seq,
                        )

            # Latency tracking
            if receive_time_ms is not None:
                exchange_ms = tick.timestamp.timestamp() * 1000.0
                latency_ms = receive_time_ms - exchange_ms
                if 0 < latency_ms < 60_000:
                    self._latencies[symbol].append(latency_ms)
                    # Keep last 10k
                    if len(self._latencies[symbol]) > 10_000:
                        self._latencies[symbol] = self._latencies[symbol][-10_000:]

            self._buffers[symbol].append(tick)

            # Auto-flush if buffer is full
            if len(self._buffers[symbol]) >= self._max_buffer:
                logger.info(
                    "Auto-flushing %s: buffer reached %d ticks",
                    symbol, self._max_buffer,
                )
                self._flush_symbol(symbol)

    def flush(self, symbol: str) -> int:
        """
        Write buffered ticks for ``symbol`` to CSV and clear the buffer.

        Parameters
        ----------
        symbol:
            Symbol string.

        Returns
        -------
        int
            Number of ticks flushed.
        """
        with self._lock:
            return self._flush_symbol(symbol)

    def flush_all(self) -> dict[str, int]:
        """
        Flush all buffered symbols to CSV.

        Returns
        -------
        dict[str, int]
            Mapping of symbol → number of ticks flushed.
        """
        with self._lock:
            symbols = list(self._buffers.keys())
            return {sym: self._flush_symbol(sym) for sym in symbols}

    def get_stats(self, symbol: str) -> FeedLatencyStats:
        """
        Compute and return feed quality statistics for a symbol.

        Parameters
        ----------
        symbol:
            Symbol string.

        Returns
        -------
        FeedLatencyStats
        """
        with self._lock:
            latencies = list(self._latencies.get(symbol, []))
            tick_count = len(self._buffers.get(symbol, [])) + len(
                self._seen_seqs.get(symbol, set())
            )
            duplicates = self._duplicate_counts.get(symbol, 0)
            gaps = self._gap_counts.get(symbol, 0)

        if latencies:
            arr = np.array(latencies, dtype=float)
            mean_ms = float(np.mean(arr))
            min_ms = float(np.min(arr))
            max_ms = float(np.max(arr))
            p50_ms = float(np.percentile(arr, 50))
            p95_ms = float(np.percentile(arr, 95))
            p99_ms = float(np.percentile(arr, 99))
        else:
            mean_ms = min_ms = max_ms = p50_ms = p95_ms = p99_ms = 0.0

        return FeedLatencyStats(
            symbol=symbol,
            count=len(latencies) if latencies else tick_count,
            mean_ms=mean_ms,
            min_ms=min_ms,
            max_ms=max_ms,
            p50_ms=p50_ms,
            p95_ms=p95_ms,
            p99_ms=p99_ms,
            missing_ticks=gaps,
            duplicate_ticks=duplicates,
        )

    def buffer_size(self, symbol: str) -> int:
        """Return the number of ticks currently buffered for a symbol."""
        with self._lock:
            return len(self._buffers.get(symbol, []))

    def all_symbols(self) -> list[str]:
        """Return all symbols with buffered ticks."""
        with self._lock:
            return list(self._buffers.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _flush_symbol(self, symbol: str) -> int:
        """
        Write buffered ticks to CSV and clear buffer.

        Must be called with ``self._lock`` held.
        """
        ticks = self._buffers.get(symbol, [])
        if not ticks:
            return 0

        # Build safe filename: replace special chars
        safe_name = symbol.replace("/", "-").replace("\\", "-")
        date_str = datetime.now().strftime("%Y%m%d")
        csv_path = self._output_dir / f"{safe_name}_{date_str}.csv"

        write_header = not csv_path.exists()

        try:
            with open(csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow([
                        "symbol", "exchange", "timestamp", "ltp",
                        "open", "high", "low", "close",
                        "volume", "oi", "bid", "ask",
                        "bid_qty", "ask_qty", "seq",
                    ])
                for tick in ticks:
                    writer.writerow([
                        tick.symbol,
                        tick.exchange,
                        tick.timestamp.isoformat(),
                        tick.ltp,
                        tick.open,
                        tick.high,
                        tick.low,
                        tick.close,
                        tick.volume,
                        tick.oi,
                        tick.bid,
                        tick.ask,
                        tick.bid_qty,
                        tick.ask_qty,
                        tick.seq,
                    ])
        except OSError as exc:
            logger.error("Failed to flush ticks for %s to %s: %s", symbol, csv_path, exc)
            return 0

        count = len(ticks)
        self._buffers[symbol] = []
        logger.debug("Flushed %d ticks for %s → %s", count, symbol, csv_path)
        return count
