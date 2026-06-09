# utils/nse_historical_loader.py
# Loads and merges 20-year daily OHLCV CSVs for NSE indices.
#
# Directory layout:
#   data/nse/historical/nifty50/      — NIFTY 50 files
#   data/nse/historical/banknifty/    — NIFTY BANK files
#   data/nse/historical/finnifty/     — NIFTY FIN SERVICE (when available)
#   data/nse/historical/midcpnifty/   — NIFTY MIDCAP (when available)
#
# Date format in source files: DD-MMM-YYYY (e.g. 05-JUN-2007)

from __future__ import annotations

import warnings
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pandas as pd

_BASE_DIR = Path(__file__).parent.parent / "data" / "nse" / "historical"

IndexKey = Literal["nifty50", "banknifty", "finnifty", "midcpnifty"]

_INDEX_DIRS: dict[str, str] = {
    "nifty50"   : "nifty50",
    "banknifty" : "banknifty",
    "finnifty"  : "finnifty",
    "midcpnifty": "midcpnifty",
}


def _find_csvs(index: str) -> list[Path]:
    d = _BASE_DIR / _INDEX_DIRS.get(index, index)
    if not d.exists():
        return []
    return sorted(d.glob("*.csv"))


def _parse_one(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8")
    df.columns = [
        c.strip().lower()
         .replace(" ", "_").replace("(", "").replace(")", "")
         .replace("₹", "rs").replace("/", "_")
        for c in df.columns
    ]
    renames = {
        "date": "date", "open": "open", "high": "high", "low": "low", "close": "close",
        "shares_traded": "volume", "turnover_rs_cr": "turnover_cr",
        "turnover_₹_cr": "turnover_cr", "turnover": "turnover_cr", "volume": "volume",
    }
    df = df.rename(columns={c: renames.get(c, c) for c in df.columns})
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y", errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ""), errors="coerce"
            )
    for col in ("volume", "turnover_cr"):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ""), errors="coerce"
            ).fillna(0)
    keep = [c for c in ("date", "open", "high", "low", "close", "volume", "turnover_cr")
            if c in df.columns]
    return df[keep].copy()


@lru_cache(maxsize=8)
def load_daily(index: str = "nifty50") -> pd.DataFrame:
    """
    Returns a deduplicated daily OHLCV DataFrame for the given index,
    sorted oldest → newest.  Cached per index key.

    index: 'nifty50' | 'banknifty' | 'finnifty' | 'midcpnifty'
    """
    files = _find_csvs(index)
    if not files:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    frames = []
    for f in files:
        try:
            frames.append(_parse_one(f))
        except Exception as exc:
            warnings.warn(f"nse_historical_loader: skipped {f.name} — {exc}")

    if not frames:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = (pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["date"])
            .sort_values("date")
            .reset_index(drop=True))
    return df


# Convenience aliases
def load_nifty_daily()     -> pd.DataFrame: return load_daily("nifty50")
def load_banknifty_daily() -> pd.DataFrame: return load_daily("banknifty")


def get_recent(index: str = "nifty50", n: int = 252) -> pd.DataFrame:
    return load_daily(index).tail(n).reset_index(drop=True)


def available_years(index: str = "nifty50") -> list[int]:
    df = load_daily(index)
    if df.empty:
        return []
    return sorted(df["date"].dt.year.unique().tolist())


def data_status(index: str = "nifty50") -> dict:
    df = load_daily(index)
    if df.empty:
        return {"loaded": False, "rows": 0, "files": 0, "date_range": "none", "index": index}
    return {
        "loaded"    : True,
        "index"     : index,
        "rows"      : len(df),
        "files"     : len(_find_csvs(index)),
        "date_range": f"{df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}",
        "years"     : available_years(index),
    }


def all_status() -> dict:
    """Summary for all indices that have data loaded."""
    return {idx: data_status(idx) for idx in _INDEX_DIRS if _find_csvs(idx)}
