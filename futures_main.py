"""
CB6 Futures Core — Entry Point
Isolated futures engine. Zero connection to NSE, forex, or crypto engines.

Usage:
    python futures_main.py [--mode MODE] [--symbols SYM1,SYM2] [--backtest]

Modes:
    paper           (default) — paper trading simulation
    backtest        — historical backtest only
    manual_monitor  — track manual trades only, no signal execution
    semi_auto       — signals queued for manual approval before execution
    off             — no operation

LIVE_AUTO is permanently disabled in this entry point.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

# ── Logging ────────────────────────────────────────────────────────────────────
os.makedirs("logs/futures", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            f"logs/futures/futures_{__import__('datetime').date.today().isoformat()}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("cb6.futures_main")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CB6 Futures Core")
    p.add_argument(
        "--mode",
        default="paper",
        choices=["off", "paper", "backtest", "manual_monitor", "semi_auto"],
        help="Operating mode (default: paper)",
    )
    p.add_argument(
        "--symbols",
        default="MES,MNQ,MGC,MCL",
        help="Comma-separated symbol list (default: MES,MNQ,MGC,MCL)",
    )
    p.add_argument(
        "--poll",
        type=float,
        default=60.0,
        help="Poll interval in seconds for paper/semi_auto modes (default: 60)",
    )
    p.add_argument(
        "--timeframe",
        default="1m",
        choices=["1m", "3m"],
        help="Backtest/input timeframe for futures historical data (default: 1m)",
    )
    p.add_argument(
        "--htf-timeframe",
        default=None,
        choices=["1m", "3m", "4h"],
        help="Higher-timeframe bias series. Defaults to 4h, or input timeframe for Databento.",
    )
    p.add_argument(
        "--source",
        default="csv",
        choices=["csv", "databento"],
        help="Historical data source for backtest/paper feed (default: csv)",
    )
    p.add_argument(
        "--start",
        default=None,
        help="Backtest start date YYYY-MM-DD. Databento default: 2021-01-01.",
    )
    p.add_argument(
        "--end",
        default=None,
        help="Backtest end date YYYY-MM-DD or 'today'. Default: now UTC.",
    )
    p.add_argument(
        "--status",
        action="store_true",
        help="Print current account status and exit",
    )
    return p.parse_args()


def _parse_utc_date(raw: str | None, end_of_day: bool = False):
    if raw is None:
        return None
    if raw.strip().lower() == "today":
        return datetime.now(timezone.utc)
    dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def _validate_databento_backtest_inputs(symbols: list[str], timeframe: str, start, end) -> None:
    from futures_engine.research.cme_databento_backdata import load_databento_futures_csv

    for symbol in symbols:
        frame = load_databento_futures_csv(symbol, timeframe, start=start, end=end)
        if frame.empty:
            raise RuntimeError(f"No Databento rows available for {symbol} {timeframe} in requested date range")
        logger.info(
            "Databento input OK: %s %s rows=%d first=%s last=%s",
            symbol,
            timeframe,
            len(frame),
            frame["timestamp"].iloc[0],
            frame["timestamp"].iloc[-1],
        )


def main() -> None:
    args = parse_args()
    mode = args.mode.upper()
    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    source = args.source.lower()
    timeframe = args.timeframe.lower()
    htf_timeframe = (args.htf_timeframe or (timeframe if source == "databento" else "4h")).lower()
    start = _parse_utc_date(args.start)
    end = _parse_utc_date(args.end, end_of_day=True)
    if source == "databento" and start is None:
        start = datetime(2021, 1, 1, tzinfo=timezone.utc)

    logger.info("=" * 60)
    logger.info("CB6 Futures Core — starting")
    logger.info("Mode: %s | Symbols: %s | Source: %s | TF: %s | HTF: %s",
                mode, symbols, source, timeframe, htf_timeframe)
    logger.info("=" * 60)

    from futures_engine.mff_flex_25k.mff_flex_runner import MFFFlexRunner
    feed = None
    if source == "databento":
        from futures_engine.research.cme_databento_backdata import DatabentoCSVDataFeed
        if mode == "BACKTEST":
            try:
                _validate_databento_backtest_inputs(symbols, timeframe, start, end)
            except Exception as exc:
                logger.error("Databento backtest input validation failed: %s", exc)
                print(f"FAILED: {exc}")
                sys.exit(1)
        feed = DatabentoCSVDataFeed("data/futures/historical")

    runner = MFFFlexRunner(
        mode=mode,
        feed=feed,
        symbols=symbols,
        poll_interval=args.poll,
        timeframe=timeframe,
        htf_timeframe=htf_timeframe,
        data_source=source,
        backtest_start=start,
        backtest_end=end,
    )

    if args.status:
        import json
        print(json.dumps(runner.status(), indent=2))
        return

    runner.start()


if __name__ == "__main__":
    main()
