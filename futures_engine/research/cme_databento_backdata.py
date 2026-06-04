"""CB6 futures Databento historical CSV loader.

Offline research only. This loader reads CB6-normalized Databento files:
data/futures/historical/{SYMBOL}_{TIMEFRAME}_2021_present.csv
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd

from futures_engine.core.futures_contract_manager import ContractManager
from futures_engine.core.futures_data_feed import FuturesBar, FuturesDataFeed

logger = logging.getLogger("cb6.futures.research.databento_loader")

DATA_DIR = Path("data") / "futures" / "historical"
REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "symbol", "source", "timeframe"]
SUPPORTED_SYMBOLS = {"MES", "MNQ", "MGC", "CL"}
SUPPORTED_TIMEFRAMES = {"1m", "3m"}


def databento_csv_path(symbol: str, timeframe: str, data_dir: str | Path = DATA_DIR) -> Path:
    return Path(data_dir) / f"{symbol.upper()}_{timeframe}_2021_present.csv"


def _coerce_utc(value: datetime | str | None) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Invalid timestamp boundary: {value!r}")
    return ts


def load_databento_futures_csv(
    symbol: str,
    timeframe: str,
    start: datetime | str | None = None,
    end: datetime | str | None = None,
    data_dir: str | Path = DATA_DIR,
) -> pd.DataFrame:
    """Load and clean a CB6 Databento futures CSV as a pandas DataFrame."""
    symbol = symbol.upper()
    timeframe = timeframe.lower()
    if symbol not in SUPPORTED_SYMBOLS:
        raise ValueError(f"Unsupported Databento futures symbol {symbol!r}; expected {sorted(SUPPORTED_SYMBOLS)}")
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"Unsupported Databento timeframe {timeframe!r}; expected 1m or 3m")

    path = databento_csv_path(symbol, timeframe, data_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"Databento backdata file not found: {path}. "
            "Run: python tools/download_cme_databento.py --start 2021-01-01 --end today "
            "--symbols MES MNQ MGC CL"
        )

    df = pd.read_csv(path)
    if list(df.columns) != REQUIRED_COLUMNS:
        raise ValueError(f"{path} columns mismatch. Expected {REQUIRED_COLUMNS}, got {list(df.columns)}")

    clean = df.copy()
    clean["timestamp"] = pd.to_datetime(clean["timestamp"], utc=True, errors="coerce")
    bad_ts = int(clean["timestamp"].isna().sum())
    if bad_ts:
        raise ValueError(f"{path} has {bad_ts} invalid timestamps")

    for col in ["open", "high", "low", "close", "volume"]:
        clean[col] = pd.to_numeric(clean[col], errors="coerce")
    if clean[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError(f"{path} contains null/non-numeric OHLC values")
    if clean["volume"].isna().any():
        raise ValueError(f"{path} contains null/non-numeric volume values")

    clean["symbol"] = clean["symbol"].astype(str).str.upper()
    clean["source"] = clean["source"].astype(str).str.lower()
    clean["timeframe"] = clean["timeframe"].astype(str).str.lower()
    clean = clean[(clean["symbol"] == symbol) & (clean["timeframe"] == timeframe)]
    clean = clean.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")

    start_ts = _coerce_utc(start)
    end_ts = _coerce_utc(end)
    if start_ts is not None:
        clean = clean[clean["timestamp"] >= start_ts]
    if end_ts is not None:
        clean = clean[clean["timestamp"] <= end_ts]

    clean["volume"] = clean["volume"].clip(lower=0).astype("int64")
    return clean.reset_index(drop=True)[REQUIRED_COLUMNS]


class DatabentoCSVDataFeed(FuturesDataFeed):
    """FuturesDataFeed adapter over CB6-normalized Databento CSV files."""

    def __init__(self, data_dir: str | Path = DATA_DIR):
        self._dir = Path(data_dir)
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}

    def _load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol.upper(), timeframe.lower())
        if key not in self._cache:
            self._cache[key] = load_databento_futures_csv(symbol, timeframe, data_dir=self._dir)
        return self._cache[key]

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        contract: Optional[str] = None,
    ) -> list[FuturesBar]:
        df = self._load(symbol, timeframe)
        start_ts = _coerce_utc(start)
        end_ts = _coerce_utc(end)
        if start_ts is not None:
            df = df[df["timestamp"] >= start_ts]
        if end_ts is not None:
            df = df[df["timestamp"] <= end_ts]
        if df.empty:
            return []

        contract_mgr = ContractManager(symbol)
        bars: list[FuturesBar] = []
        for row in df.itertuples(index=False):
            ts = row.timestamp.to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            active_contract = contract_mgr.active_contract(ts.date())
            if contract is not None and active_contract != contract:
                continue
            bars.append(
                FuturesBar(
                    symbol=symbol.upper(),
                    contract=active_contract,
                    timestamp=ts,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=int(row.volume),
                    timeframe=timeframe.lower(),
                )
            )
        return bars

    def get_latest_bar(self, symbol: str, timeframe: str) -> Optional[FuturesBar]:
        bars = self.get_bars(symbol, timeframe, datetime(1970, 1, 1, tzinfo=timezone.utc), datetime.now(timezone.utc))
        return bars[-1] if bars else None

    def stream_bars(self, symbol: str, timeframe: str) -> Iterator[FuturesBar]:
        for bar in self.get_bars(symbol, timeframe, datetime(1970, 1, 1, tzinfo=timezone.utc), datetime.now(timezone.utc)):
            yield bar

