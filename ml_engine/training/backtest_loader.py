"""
ml_engine/training/backtest_loader.py

Loads CB6 backtest result CSVs into a clean, normalised DataFrame.
Supports:
  - data/backtest_30day_report.csv    (NSE 30-day backtest)
  - data/backtest_silver_bullet_nifty.csv  (NSE Silver Bullet backtest)
  - data/forex_journal.csv            (Forex backtest / live journal)
  - any custom backtest CSV with the required columns

READ-ONLY. No writes. No live hooks. No execution imports.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("cb6.ml.backtest_loader")

# ── Column normalization maps ────────────────────────────────────────────────

# NSE 30-day report columns → standard names
_NSE_30DAY_MAP = {
    "market"      : "symbol",
    "time"        : "entry_time_str",
    "direction"   : "direction",
    "score"       : "confluence",
    "mss_type"    : "mss_type",
    "regime"      : "regime",
    "in_fvg"      : "in_fvg",
    "entry"       : "entry",
    "sl"          : "stop_loss",
    "risk_pts"    : "risk",
    "t1"          : "target1",
    "t2"          : "target2",
    "t3"          : "target3",
    "result"      : "exit_reason",
    "exit_price"  : "exit_price",
    "targets_hit" : "targets_hit",
    "pnl_pts"     : "pnl",
    "r_multiple"  : "r_multiple",
    "is_win"      : "win",
}

# NSE Silver Bullet backtest columns → standard names
_NSE_SB_MAP = {
    "direction"      : "direction",
    "entry"          : "entry",
    "stop_loss"      : "stop_loss",
    "risk_pts"       : "risk",
    "target1"        : "target1",
    "target2"        : "target2",
    "target3"        : "target3",
    "score"          : "confluence",
    "fvg_displacement": "fvg_displacement",
    "result"         : "exit_reason",
    "exit_price"     : "exit_price",
    "targets_hit"    : "targets_hit",
    "pnl_pts"        : "pnl",
    "r_multiple"     : "r_multiple",
    "is_win"         : "win",
}

# Forex journal columns → standard names
_FOREX_MAP = {
    "symbol"         : "symbol",
    "direction"      : "direction",
    "session"        : "session",
    "mss_type"       : "mss_type",
    "score"          : "confluence",
    "fvg_size"       : "fvg_size",
    "fvg_displacement": "fvg_displacement",
    "price_in_fvg"   : "in_fvg",
    "ob_present"     : "ob_present",
    "ut_bot_aligned" : "ut_aligned",
    "entry"          : "entry",
    "stop_loss"      : "stop_loss",
    "target1"        : "target1",
    "target2"        : "target2",
    "target3"        : "target3",
    "risk_price"     : "risk",
    "rr_ratio"       : "rr_ratio",
    "result"         : "exit_reason",
    "targets_hit"    : "targets_hit",
    "exit_price"     : "exit_price",
    "pnl_usd"        : "pnl",
    "r_multiple"     : "r_multiple",
    "win"            : "win",
}


def _detect_format(df: pd.DataFrame) -> str:
    cols = set(df.columns)
    if "pnl_usd" in cols or "risk_price" in cols:
        return "forex"
    if "market" in cols:
        return "nse_30day"
    if "strategy" in cols:
        return "nse_sb"
    return "unknown"


def _normalise(df: pd.DataFrame, col_map: dict) -> pd.DataFrame:
    rename = {k: v for k, v in col_map.items() if k in df.columns}
    df = df.rename(columns=rename)
    return df


def _add_outcome_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "win" not in df.columns and "exit_reason" in df.columns:
        df["win"] = df["exit_reason"].str.upper().str.contains("TARGET|T[123]", na=False)
    if "win" in df.columns:
        df["win"] = df["win"].astype(bool)
    if "r_multiple" in df.columns:
        df["r_multiple"] = pd.to_numeric(df["r_multiple"], errors="coerce")
    return df


def load_backtest(path: str | Path, engine: str = "auto") -> Optional[pd.DataFrame]:
    """
    Load a backtest CSV into a normalised DataFrame.

    Parameters
    ----------
    path   : Path to CSV file.
    engine : 'nse' | 'forex' | 'auto' (auto-detected from columns).

    Returns
    -------
    DataFrame with standard columns, or None if load fails.

    Standard columns always present after load:
        date, symbol, direction, confluence, mss_type, entry, stop_loss,
        target1, target2, target3, risk, exit_reason, exit_price,
        r_multiple, win, pnl, engine
    """
    path = Path(path)
    if not path.exists():
        logger.warning(f"Backtest file not found: {path}")
        return None

    try:
        df = pd.read_csv(path, low_memory=False)
        logger.info(f"Loaded {len(df)} rows from {path.name}")
    except Exception as e:
        logger.error(f"Failed to read {path}: {e}")
        return None

    if df.empty:
        logger.warning(f"{path.name} is empty")
        return None

    fmt = _detect_format(df) if engine == "auto" else engine
    logger.info(f"Detected format: {fmt}")

    if fmt == "forex":
        df = _normalise(df, _FOREX_MAP)
        df["engine"] = "forex"
    elif fmt == "nse_30day":
        df = _normalise(df, _NSE_30DAY_MAP)
        df["engine"] = "nse"
        if "date" in df.columns and "entry_time_str" in df.columns:
            df["entry_time"] = pd.to_datetime(
                df["date"].astype(str) + " " + df["entry_time_str"].astype(str),
                errors="coerce",
            )
    elif fmt == "nse_sb":
        df = _normalise(df, _NSE_SB_MAP)
        df["engine"] = "nse"
        if "date" in df.columns and "time" in df.columns:
            df["entry_time"] = pd.to_datetime(
                df["date"].astype(str) + " " + df["time"].astype(str),
                errors="coerce",
            )
    else:
        logger.warning(f"Unknown format for {path.name} — returning raw with engine tag")
        df["engine"] = engine if engine != "auto" else "unknown"

    df = _add_outcome_columns(df)

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    logger.info(
        f"{path.name}: {len(df)} rows | wins={df['win'].sum() if 'win' in df.columns else 'n/a'} "
        f"| cols={list(df.columns)}"
    )
    return df


def load_all_backtests(base_path: str | Path = "data/") -> pd.DataFrame:
    """
    Load all known backtest CSVs and concatenate into a single DataFrame.
    Adds 'source_file' column for traceability.
    """
    base = Path(base_path)
    targets = [
        (base / "backtest_30day_report.csv",        "auto"),
        (base / "backtest_silver_bullet_nifty.csv", "auto"),
        (base / "forex_journal.csv",                "forex"),
    ]

    frames = []
    for fpath, engine in targets:
        df = load_backtest(fpath, engine=engine)
        if df is not None and not df.empty:
            df["source_file"] = fpath.name
            frames.append(df)

    if not frames:
        logger.warning("No backtest files loaded — check data/ directory")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)
    logger.info(f"Total backtest rows loaded: {len(combined)}")
    return combined


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = load_all_backtests("../../data/")
    print(f"\nTotal rows: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    if "win" in df.columns:
        print(f"Win rate: {df['win'].mean():.1%}")
    if "r_multiple" in df.columns:
        print(f"Avg R: {df['r_multiple'].mean():.2f}")
    print(df.head(3).to_string())
