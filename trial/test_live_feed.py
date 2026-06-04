"""
Trial test: Live WebSocket feed quality.

Subscribes to the four index continuous contracts, collects ticks for
``duration_minutes``, saves to CSV, computes latency statistics, and
returns a :class:`TrialResult` with a quality score.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from provider.truedata import (
    TrueDataAuth,
    TrueDataConfig,
    TrueDataRestClient,
    TrueDataWebSocketClient,
    TrialResult,
)
from provider.truedata.models import MarketTick

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")

# Maximum score contribution from this test
_MAX_SCORE = 35  # auth(10) + ws_stability(20) + tick_quality(5 of 15 here)


async def run_live_feed_test(
    config: TrueDataConfig,
    duration_minutes: Optional[int] = None,
) -> TrialResult:
    """
    Subscribe to NIFTY-I, BANKNIFTY-I, FINNIFTY-I, MIDCPNIFTY-I via
    WebSocket, collect ticks for ``duration_minutes``, save to CSV, and
    compute latency statistics.

    Parameters
    ----------
    config:
        TrueData configuration.
    duration_minutes:
        Override from config if provided.

    Returns
    -------
    TrialResult
        Contains score, latency stats, and per-symbol tick counts.
    """
    started_at = datetime.now(tz=_IST)
    errors: list[str] = []
    details: dict = {}

    duration = duration_minutes or config.trial_duration_minutes
    symbols = list(config.trial_symbols)

    logger.info(
        "Starting live feed test: symbols=%s duration=%dm", symbols, duration
    )

    # ---- Auth ----
    auth = TrueDataAuth(config)
    try:
        auth.login()
        details["auth_ok"] = True
    except Exception as exc:
        errors.append(f"Auth failed: {exc}")
        details["auth_ok"] = False
        return _build_result(started_at, False, 0, details, errors)

    # ---- Per-symbol state ----
    tick_counts: dict[str, int] = {s: 0 for s in symbols}
    latency_samples: dict[str, list[float]] = {s: [] for s in symbols}
    seq_gaps: dict[str, int] = {s: 0 for s in symbols}
    last_seq: dict[str, Optional[int]] = {s: None for s in symbols}
    connect_events = 0
    disconnect_events = 0
    reconnect_events = 0

    # Output directory
    output_dir = config.data_dir / "trial_ticks"
    output_dir.mkdir(parents=True, exist_ok=True)

    # CSV writers per symbol
    csv_files: dict[str, object] = {}
    csv_writers: dict[str, csv.writer] = {}

    def _open_csv(symbol: str) -> csv.writer:
        safe = symbol.replace("/", "-")
        path = output_dir / f"{safe}_live_feed_trial.csv"
        f = open(path, "w", newline="", encoding="utf-8")
        csv_files[symbol] = f
        writer = csv.writer(f)
        writer.writerow([
            "symbol", "timestamp", "ltp", "volume", "oi",
            "bid", "ask", "seq", "latency_ms",
        ])
        return writer

    for sym in symbols:
        csv_writers[sym] = _open_csv(sym)

    # ---- Callbacks ----
    receive_times: dict[str, float] = {}

    def on_tick(tick: MarketTick) -> None:
        recv = time.time()
        sym = tick.symbol
        if sym not in tick_counts:
            return

        tick_counts[sym] += 1

        # Latency
        exchange_ts = tick.timestamp.timestamp()
        latency_ms = (recv - exchange_ts) * 1000.0
        if 0 < latency_ms < 60_000:
            latency_samples[sym].append(latency_ms)

        # Sequence gap
        if tick.seq is not None:
            prev = last_seq.get(sym)
            if prev is not None and tick.seq > prev + 1:
                seq_gaps[sym] += tick.seq - prev - 1
            last_seq[sym] = tick.seq

        # Write CSV row
        writer = csv_writers.get(sym)
        if writer:
            writer.writerow([
                tick.symbol,
                tick.timestamp.isoformat(),
                tick.ltp,
                tick.volume,
                tick.oi,
                tick.bid,
                tick.ask,
                tick.seq,
                round(latency_ms, 2) if 0 < latency_ms < 60_000 else "",
            ])

    def on_connect() -> None:
        nonlocal connect_events
        connect_events += 1
        logger.info("WebSocket connected (event #%d)", connect_events)

    def on_disconnect() -> None:
        nonlocal disconnect_events
        disconnect_events += 1
        logger.warning("WebSocket disconnected (event #%d)", disconnect_events)

    # ---- WebSocket client ----
    rest = TrueDataRestClient(config, auth)
    ws = TrueDataWebSocketClient(config, auth)
    ws.on_tick = on_tick
    ws.on_connect = on_connect
    ws.on_disconnect = on_disconnect

    # ---- Run ----
    try:
        await ws.connect()
        await ws.subscribe(symbols)

        logger.info(
            "Collecting ticks for %d minutes...", duration
        )
        await asyncio.sleep(duration * 60)

    except Exception as exc:
        errors.append(f"WebSocket error: {exc}")
        logger.error("Live feed test error: %s", exc)
    finally:
        await ws.disconnect()

        # Close CSV files
        for f in csv_files.values():
            try:
                f.close()  # type: ignore[attr-defined]
            except Exception:
                pass

    # ---- Scoring ----
    total_ticks = sum(tick_counts.values())
    symbols_with_ticks = sum(1 for c in tick_counts.values() if c > 0)
    total_gaps = sum(seq_gaps.values())

    all_latencies: list[float] = []
    for samples in latency_samples.values():
        all_latencies.extend(samples)

    p95_ms = _percentile(all_latencies, 95) if all_latencies else 9999
    mean_ms = sum(all_latencies) / len(all_latencies) if all_latencies else 0.0

    details.update({
        "duration_minutes": duration,
        "symbols": symbols,
        "tick_counts": tick_counts,
        "total_ticks": total_ticks,
        "symbols_with_ticks": symbols_with_ticks,
        "total_seq_gaps": total_gaps,
        "connect_events": connect_events,
        "disconnect_events": disconnect_events,
        "reconnect_events": ws._reconnect_count,
        "latency_p95_ms": round(p95_ms, 2),
        "latency_mean_ms": round(mean_ms, 2),
        "output_dir": str(output_dir),
        "per_symbol_latency": {
            sym: {
                "count": len(latency_samples[sym]),
                "mean_ms": round(
                    sum(latency_samples[sym]) / len(latency_samples[sym]), 2
                ) if latency_samples[sym] else 0.0,
                "p95_ms": round(_percentile(latency_samples[sym], 95), 2)
                if latency_samples[sym]
                else 0.0,
            }
            for sym in symbols
        },
    })

    # Auth score: 10 pts
    score = 10 if details["auth_ok"] else 0

    # WS stability: 20 pts
    if symbols_with_ticks == len(symbols) and disconnect_events == 0:
        score += 20
    elif symbols_with_ticks >= 2:
        score += 10
    elif symbols_with_ticks >= 1:
        score += 5

    # Tick quality — partial (rest in test_historical)
    # Latency p95 < 500ms = 5 pts
    if p95_ms < 500:
        score += 5
    elif p95_ms < 1000:
        score += 2

    passed = symbols_with_ticks == len(symbols) and total_ticks > 0 and not errors

    return _build_result(started_at, passed, score, details, errors)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _percentile(data: list[float], pct: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def _build_result(
    started_at: datetime,
    passed: bool,
    score: int,
    details: dict,
    errors: list[str],
) -> TrialResult:
    ended_at = datetime.now(tz=_IST)
    duration_s = (ended_at - started_at).total_seconds()
    return TrialResult(
        test_name="Live WebSocket Feed",
        passed=passed,
        score=score,
        details=details,
        errors=errors,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
    )
