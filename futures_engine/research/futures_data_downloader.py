"""
CB6 Futures Core — Historical Data Downloader
Downloads 1m and 4h OHLCV data for MES, MNQ, MGC, MCL from free sources.

Supported sources:
  1. Yahoo Finance  (yfinance) — free, limited tick/minute history
  2. CSV import     — paste data from TradingView, NinjaTrader, Sierra Chart exports
  3. Dukascopy      — for tick data (reuses pattern from forex_engine)

For research-quality backtesting, CSV import from a quality source
(Norgate, TickData, Kinetick, CQG) is strongly preferred over Yahoo.

Usage:
    python -m futures_engine.research.futures_data_downloader --symbol MES --source csv --file path/to/data.csv
    python -m futures_engine.research.futures_data_downloader --symbol MES --source yahoo --start 2024-01-01
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from futures_engine.core.futures_data_feed import FuturesBar, CSVDataFeed
from futures_engine.core.futures_contract_manager import ContractManager
from futures_engine.core.futures_contract_rollover_validator import FuturesRolloverValidator
from futures_engine.core.futures_symbol_registry import get_symbol

logger = logging.getLogger("cb6.futures.research.downloader")

DATA_DIR = "data/futures/historical"

YAHOO_SYMBOL_MAP = {
    "MES":  "ES=F",   # Yahoo doesn't serve hourly for micros — use standard as proxy
    "MNQ":  "NQ=F",
    "MGC":  "GC=F",
    "MCL":  "CL=F",
    "ES":   "ES=F",
    "NQ":   "NQ=F",
    "GC":   "GC=F",
    "CL":   "CL=F",
    "SI":   "SI=F",
    "ZN":   "ZN=F",
    "ZB":   "ZB=F",
}

# Maximum history Yahoo provides per timeframe (use conservative limits)
YAHOO_MAX_HISTORY = {
    "1m":  7,     # days
    "5m":  60,    # days
    "1h":  720,   # days (Yahoo caps at 730; use 720 to avoid boundary rejections)
    "4h":  720,   # days (served as 60m and resampled)
    "1d":  9999,  # days (full history)
}


class FuturesDataDownloader:

    def __init__(self, output_dir: str = DATA_DIR):
        self._dir = output_dir
        os.makedirs(self._dir, exist_ok=True)
        self._feed = CSVDataFeed(self._dir)

    # ── Yahoo Finance ──────────────────────────────────────────────────────

    def download_yahoo(
        self,
        symbol: str,
        timeframe: str = "1h",
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[FuturesBar]:
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed. Run: pip install yfinance")
            return []

        ticker = YAHOO_SYMBOL_MAP.get(symbol.upper(), symbol + "=F")
        max_days = YAHOO_MAX_HISTORY.get(timeframe, 60)

        if start is None:
            start = datetime.now(timezone.utc) - timedelta(days=min(max_days, 730))
        if end is None:
            end = datetime.now(timezone.utc)

        # Yahoo timeframe codes — 4h is not native, download as 1h then resample
        resample_4h = (timeframe == "4h")
        tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "1h", "1d": "1d"}
        yf_interval = tf_map.get(timeframe, "1h")

        logger.info("Yahoo: %s %s%s %s → %s",
                    ticker, yf_interval,
                    " (resample→4h)" if resample_4h else "",
                    start.date(), end.date())

        try:
            data = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=yf_interval,
                progress=False,
                auto_adjust=True,
            )
        except Exception as e:
            logger.error("Yahoo download failed for %s: %s", symbol, e)
            return []

        if data.empty:
            logger.warning("Yahoo returned no data for %s %s", symbol, timeframe)
            return []

        # Flatten multi-level columns FIRST (yfinance >= 0.2.x returns (field, ticker) tuples)
        if hasattr(data.columns, "levels"):
            data.columns = [col[0] if isinstance(col, tuple) else col for col in data.columns]
        # Some versions return lowercase column names
        data.columns = [c.capitalize() if isinstance(c, str) else c for c in data.columns]

        # Resample 1h → 4h after flattening
        if resample_4h:
            import pandas as pd
            agg_cols = {c: ("first" if c == "Open" else "max" if c == "High"
                            else "min" if c == "Low" else "last" if c == "Close"
                            else "sum")
                        for c in ["Open", "High", "Low", "Close", "Volume"]
                        if c in data.columns}
            data = data.resample("4h").agg(agg_cols).dropna(subset=["Open"])
            timeframe = "4h"

        # Determine contract for each bar
        contract_mgr = ContractManager(symbol)
        bars: List[FuturesBar] = []

        for ts_idx, row in data.iterrows():
            try:
                ts = ts_idx.to_pydatetime() if hasattr(ts_idx, "to_pydatetime") else ts_idx
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                else:
                    ts = ts.astimezone(timezone.utc)

                contract = contract_mgr.active_contract(ts.date())

                def _v(key: str) -> float:
                    val = row.get(key, row.get(key.lower(), None))
                    if val is None:
                        return 0.0
                    # handle Series (multi-level residue) or scalar
                    import pandas as pd
                    return float(val.iloc[0] if isinstance(val, pd.Series) else val)

                open_  = _v("Open")
                high   = _v("High")
                low    = _v("Low")
                close  = _v("Close")
                volume = int(_v("Volume"))

                if open_ == 0 or high == 0:
                    continue

                bars.append(FuturesBar(
                    symbol=symbol.upper(),
                    contract=contract,
                    timestamp=ts,
                    open=open_, high=high, low=low, close=close,
                    volume=volume,
                    timeframe=timeframe,
                ))
            except Exception as row_err:
                logger.debug("Row parse skip: %s", row_err)

        logger.info("Yahoo: %d bars downloaded for %s %s", len(bars), symbol, timeframe)
        self._feed.save_bars(bars)
        self._run_rollover_validation(symbol, bars)
        return bars

    # ── CSV import ─────────────────────────────────────────────────────────

    def import_csv(
        self,
        symbol: str,
        timeframe: str,
        filepath: str,
        date_col: str = "Date",
        time_col: Optional[str] = "Time",
        open_col: str = "Open",
        high_col: str = "High",
        low_col: str = "Low",
        close_col: str = "Close",
        volume_col: str = "Volume",
        date_format: str = "%Y-%m-%d",
        time_format: str = "%H:%M:%S",
    ) -> List[FuturesBar]:
        """
        Import bars from a CSV file.
        Supports TradingView, NinjaTrader, Sierra Chart, and generic formats.
        """
        if not os.path.exists(filepath):
            logger.error("CSV not found: %s", filepath)
            return []

        contract_mgr = ContractManager(symbol)
        bars: List[FuturesBar] = []

        with open(filepath, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    date_str = row.get(date_col, row.get("timestamp", ""))
                    time_str = row.get(time_col, "") if time_col else ""

                    if time_str:
                        dt_str = f"{date_str} {time_str}"
                        fmt = f"{date_format} {time_format}"
                    else:
                        dt_str = date_str
                        fmt = date_format

                    ts = datetime.strptime(dt_str.strip(), fmt).replace(tzinfo=timezone.utc)
                    contract = contract_mgr.active_contract(ts.date())

                    bars.append(FuturesBar(
                        symbol=symbol.upper(),
                        contract=contract,
                        timestamp=ts,
                        open=float(row[open_col]),
                        high=float(row[high_col]),
                        low=float(row[low_col]),
                        close=float(row[close_col]),
                        volume=int(float(row.get(volume_col, 0))),
                        timeframe=timeframe,
                    ))
                except (ValueError, KeyError) as e:
                    logger.debug("Row parse error: %s — %s", row, e)

        logger.info("CSV import: %d bars for %s %s from %s",
                    len(bars), symbol, timeframe, filepath)
        self._feed.save_bars(bars)
        self._run_rollover_validation(symbol, bars)
        return bars

    # ── Batch download ─────────────────────────────────────────────────────

    def download_all_phase1(
        self,
        timeframes: Optional[List[str]] = None,
        years_back: int = 3,
    ) -> dict:
        """
        Download all Phase 1 symbols via Yahoo.
        Respects Yahoo's per-timeframe history limits:
          1h  → max 730 days (Yahoo hard cap)
          1d  → full history available
        """
        from futures_engine.core.futures_symbol_registry import PHASE1_SYMBOLS
        tfs = timeframes or ["1h", "4h", "1d"]
        results = {}
        now = datetime.now(timezone.utc)

        for sym in PHASE1_SYMBOLS:
            results[sym] = {}
            for tf in tfs:
                max_days = YAHOO_MAX_HISTORY.get(tf, 60)
                # Don't request more history than Yahoo will return
                requested_days = min(365 * years_back, max_days)
                start = now - timedelta(days=requested_days)
                bars = self.download_yahoo(sym, tf, start=start, end=now)
                results[sym][tf] = len(bars)
                logger.info("%s %s: %d bars saved", sym, tf, len(bars))

        return results

    def _run_rollover_validation(self, symbol: str, bars: List[FuturesBar]) -> None:
        if not bars:
            return
        validator = FuturesRolloverValidator(symbol)
        report = validator.validate(bars)
        if not report.passed:
            logger.warning("Rollover validation warnings for %s:", symbol)
            for w in report.warnings:
                logger.warning("  %s", w)
        else:
            logger.info("Rollover validation passed for %s (%d rollovers detected)",
                        symbol, report.rollover_count)

    def data_inventory(self) -> dict:
        """Report what data is currently stored."""
        inventory = {}
        if not os.path.exists(self._dir):
            return inventory
        for fname in os.listdir(self._dir):
            if not fname.endswith(".csv"):
                continue
            parts = fname.replace(".csv", "").split("_")
            if len(parts) >= 2:
                sym = parts[0]
                tf  = "_".join(parts[1:])
                path = os.path.join(self._dir, fname)
                # Count rows quickly
                with open(path, encoding="utf-8") as f:
                    rows = sum(1 for _ in f) - 1  # subtract header
                inventory[f"{sym}_{tf}"] = {
                    "symbol": sym, "timeframe": tf,
                    "bars": rows, "path": path,
                }
        return inventory


# ── CLI ────────────────────────────────────────────────────────────────────────

def _cli() -> None:
    p = argparse.ArgumentParser(description="CB6 Futures Data Downloader")
    p.add_argument("--symbol", default="MES", help="Symbol (MES, MNQ, MGC, MCL)")
    p.add_argument("--source", choices=["yahoo", "csv", "inventory"], default="yahoo")
    p.add_argument("--timeframe", default="1h", help="Timeframe (1m, 5m, 15m, 1h, 4h, 1d)")
    p.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    p.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    p.add_argument("--file", default=None, help="CSV file path for --source csv")
    p.add_argument("--all", action="store_true", help="Download all Phase 1 symbols")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    dl = FuturesDataDownloader()

    if args.source == "inventory":
        import json
        print(json.dumps(dl.data_inventory(), indent=2))
        return

    if args.all:
        results = dl.download_all_phase1(years_back=3)
        import json
        print(json.dumps(results, indent=2))
        return

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.start else None
    end   = datetime.strptime(args.end,   "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.end   else None

    if args.source == "yahoo":
        bars = dl.download_yahoo(args.symbol, args.timeframe, start, end)
        print(f"Downloaded {len(bars)} bars for {args.symbol} {args.timeframe}")
    elif args.source == "csv":
        if not args.file:
            print("--file is required for --source csv")
            sys.exit(1)
        bars = dl.import_csv(args.symbol, args.timeframe, args.file)
        print(f"Imported {len(bars)} bars for {args.symbol} {args.timeframe}")


if __name__ == "__main__":
    _cli()
