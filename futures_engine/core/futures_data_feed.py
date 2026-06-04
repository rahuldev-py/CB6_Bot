"""
CB6 Futures Core — Data Feed
Abstract interface + CSV/file-based implementation for paper/backtest.
No live broker dependency in this layer.
"""
from __future__ import annotations

import csv
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, List, Optional


@dataclass
class FuturesBar:
    symbol: str
    contract: str          # e.g. "MESH25"
    timestamp: datetime    # UTC-aware
    open: float
    high: float
    low: float
    close: float
    volume: int
    timeframe: str         # "1m", "5m", "15m", "1h", "4h", "1d"


@dataclass
class FuturesTick:
    symbol: str
    contract: str
    timestamp: datetime
    price: float
    size: int
    side: str              # "bid" | "ask" | "trade"


class FuturesDataFeed(ABC):
    """Base contract for all futures data providers."""

    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        contract: Optional[str] = None,
    ) -> List[FuturesBar]:
        ...

    @abstractmethod
    def get_latest_bar(self, symbol: str, timeframe: str) -> Optional[FuturesBar]:
        ...

    @abstractmethod
    def stream_bars(
        self, symbol: str, timeframe: str
    ) -> Iterator[FuturesBar]:
        ...

    def name(self) -> str:
        return self.__class__.__name__


class CSVDataFeed(FuturesDataFeed):
    """
    Reads OHLCV bars from CSV files stored under data/futures/historical/.
    Expected filename pattern: {SYMBOL}_{TIMEFRAME}.csv
    Expected columns: timestamp,open,high,low,close,volume
    Timestamp format: ISO-8601 or Unix epoch seconds.
    """

    def __init__(self, data_dir: str = "data/futures/historical"):
        self._dir = data_dir
        os.makedirs(self._dir, exist_ok=True)

    def _csv_path(self, symbol: str, timeframe: str) -> str:
        return os.path.join(self._dir, f"{symbol.upper()}_{timeframe}.csv")

    def _parse_ts(self, raw: str) -> datetime:
        raw = raw.strip()
        if raw.isdigit():
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        for fmt in [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%Y%m%d %H%M%S",   # NinjaTrader YYYYMMDD HHMMSS
            "%Y%m%d %H%M",
            "%Y%m%d",
        ]:
            try:
                dt = datetime.strptime(raw.replace("Z", "+00:00"), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        raise ValueError(f"Cannot parse timestamp: {raw!r}")

    def _load_csv(self, symbol: str, timeframe: str) -> List[FuturesBar]:
        path = self._csv_path(symbol, timeframe)
        if not os.path.exists(path):
            return []
        bars: List[FuturesBar] = []

        with open(path, encoding="utf-8") as f:
            # Detect delimiter (comma vs semicolon — NinjaTrader uses semicolons)
            sample = f.read(4096)
            f.seek(0)
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
            reader = csv.DictReader(f, delimiter=delimiter)

            for row in reader:
                try:
                    # Normalize all column names to lowercase for uniform lookup
                    r = {k.lower().strip().lstrip("﻿"): v.strip() for k, v in row.items() if k}

                    # Timestamp — handle all common column naming conventions:
                    # "timestamp" (CB6 native), "time" (TradingView), "date" (Sierra Chart / NinjaTrader)
                    ts_raw = (r.get("timestamp") or r.get("time") or r.get("date") or "")

                    # NinjaTrader: separate Date (YYYYMMDD) + Time (HHMMSS) columns
                    if not ts_raw and "date" in r:
                        ts_raw = r["date"].strip()
                        if "time" in r and r["time"].strip():
                            ts_raw = ts_raw + " " + r["time"].strip()

                    if not ts_raw:
                        continue

                    ts = self._parse_ts(ts_raw)

                    bars.append(FuturesBar(
                        symbol=symbol.upper(),
                        contract=r.get("contract", ""),
                        timestamp=ts,
                        open=float(r.get("open", 0)),
                        high=float(r.get("high", 0)),
                        low=float(r.get("low", 0)),
                        close=float(r.get("close", 0)),
                        volume=int(float(r.get("volume", r.get("totalvolume", 0)))),
                        timeframe=timeframe,
                    ))
                except (ValueError, TypeError, KeyError):
                    continue  # skip unparseable rows silently

        return bars

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        contract: Optional[str] = None,
    ) -> List[FuturesBar]:
        all_bars = self._load_csv(symbol, timeframe)
        filtered = [
            b for b in all_bars
            if start <= b.timestamp <= end and
               (contract is None or b.contract == contract)
        ]
        return sorted(filtered, key=lambda b: b.timestamp)

    def get_latest_bar(self, symbol: str, timeframe: str) -> Optional[FuturesBar]:
        bars = self._load_csv(symbol, timeframe)
        return max(bars, key=lambda b: b.timestamp) if bars else None

    def stream_bars(self, symbol: str, timeframe: str) -> Iterator[FuturesBar]:
        for bar in self._load_csv(symbol, timeframe):
            yield bar

    def save_bars(self, bars: List[FuturesBar]) -> None:
        """Append bars to the appropriate CSV file, deduplicating by timestamp."""
        if not bars:
            return
        sym = bars[0].symbol
        tf = bars[0].timeframe
        path = self._csv_path(sym, tf)
        existing_ts: set = set()
        existing_rows: list = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    existing_ts.add(row["timestamp"])
                    existing_rows.append(row)
        new_rows = [b for b in bars if b.timestamp.isoformat() not in existing_ts]
        if not new_rows:
            return
        write_header = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8") as f:
            fieldnames = ["timestamp", "contract", "open", "high", "low", "close", "volume"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            for b in new_rows:
                writer.writerow({
                    "timestamp": b.timestamp.isoformat(),
                    "contract": b.contract,
                    "open": b.open, "high": b.high,
                    "low": b.low, "close": b.close,
                    "volume": b.volume,
                })


class PaperDataFeed(CSVDataFeed):
    """
    Paper-mode feed: serves historical CSV data + accepts injected ticks
    to simulate live streaming without a broker connection.
    """

    def __init__(self, data_dir: str = "data/futures/historical"):
        super().__init__(data_dir)
        self._injected: list[FuturesBar] = []

    def inject_bar(self, bar: FuturesBar) -> None:
        self._injected.append(bar)

    def get_latest_bar(self, symbol: str, timeframe: str) -> Optional[FuturesBar]:
        injected = [b for b in self._injected
                    if b.symbol == symbol and b.timeframe == timeframe]
        if injected:
            return max(injected, key=lambda b: b.timestamp)
        return super().get_latest_bar(symbol, timeframe)

    def stream_bars(self, symbol: str, timeframe: str) -> Iterator[FuturesBar]:
        for bar in super().stream_bars(symbol, timeframe):
            yield bar
        for bar in self._injected:
            if bar.symbol == symbol and bar.timeframe == timeframe:
                yield bar
