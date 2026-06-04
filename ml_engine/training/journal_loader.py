"""
ml_engine/training/journal_loader.py

Loads CB6 trade journal CSVs into a normalised DataFrame.
Supports:
  - data/trade_journal.csv      (NSE journal — rich option/Greek fields)
  - data/forex_journal.csv      (Forex journal — candle + execution fields)
  - data/cb6_master_archive.csv (historical merged archive if present)

READ-ONLY. No writes. No live hooks. No execution imports.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("cb6.ml.journal_loader")

_NSE_JOURNAL_PATH   = "data/trade_journal.csv"
_FOREX_JOURNAL_PATH = "data/forex_journal.csv"
_ARCHIVE_PATH       = "data/cb6_master_archive.csv"


def _detect_engine(df: pd.DataFrame) -> str:
    if "pnl_usd" in df.columns or "risk_price" in df.columns:
        return "forex"
    if "delta" in df.columns or "iv" in df.columns or "theta" in df.columns:
        return "nse"
    return "unknown"


def _parse_targets_hit(series: pd.Series) -> pd.Series:
    def _parse(v):
        if pd.isna(v) or v == "" or v == "[]":
            return []
        if isinstance(v, list):
            return v
        try:
            import ast
            return ast.literal_eval(str(v))
        except Exception:
            return [x.strip().strip("'\"") for x in str(v).strip("[]").split(",") if x.strip()]
    return series.apply(_parse)


def _add_outcome_labels(df: pd.DataFrame) -> pd.DataFrame:
    if "targets_hit" in df.columns:
        df["targets_hit"] = _parse_targets_hit(df["targets_hit"])
        df["t1_hit"] = df["targets_hit"].apply(lambda x: "T1" in x)
        df["t2_hit"] = df["targets_hit"].apply(lambda x: "T2" in x)
        df["t3_hit"] = df["targets_hit"].apply(lambda x: "T3" in x)
        df["targets_hit_count"] = df["targets_hit"].apply(len)

    if "exit_reason" in df.columns:
        reason_up = df["exit_reason"].astype(str).str.upper().fillna("")
        df["win"]  = reason_up.str.contains("TARGET|T[123]", regex=True)
        df["loss"] = reason_up.str.contains("SL_HIT|STOP", regex=True)

    r_col = next((c for c in ["r_multiple", "r_mult"] if c in df.columns), None)
    if r_col:
        df["r_multiple"] = pd.to_numeric(df[r_col], errors="coerce")

    return df


def load_journal(path: str | Path, engine: str = "auto") -> Optional[pd.DataFrame]:
    """
    Load a single trade journal CSV.

    Parameters
    ----------
    path   : Path to journal CSV.
    engine : 'nse' | 'forex' | 'auto'

    Returns
    -------
    Normalised DataFrame, or None if file missing / unreadable.
    """
    path = Path(path)
    if not path.exists():
        logger.warning(f"Journal not found: {path}")
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

    detected = _detect_engine(df) if engine == "auto" else engine
    df["engine"] = detected

    for col in ["date", "entry_time", "exit_time"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    df = _add_outcome_labels(df)

    win_rate = df["win"].mean() if "win" in df.columns else float("nan")
    avg_r    = df["r_multiple"].mean() if "r_multiple" in df.columns else float("nan")
    missing  = df.isnull().sum()
    missing_cols = missing[missing > 0].to_dict()

    logger.info(
        f"{path.name} [{detected}]: {len(df)} rows | "
        f"win={win_rate:.1%} | avg_r={avg_r:.2f} | "
        f"missing_cols={missing_cols}"
    )
    return df


def load_nse_journal(base_path: str = "") -> Optional[pd.DataFrame]:
    return load_journal(Path(base_path) / _NSE_JOURNAL_PATH, engine="nse")


def load_forex_journal(base_path: str = "") -> Optional[pd.DataFrame]:
    return load_journal(Path(base_path) / _FOREX_JOURNAL_PATH, engine="forex")


def load_all_journals(base_path: str = "") -> pd.DataFrame:
    """
    Load NSE + Forex journals and optional master archive,
    concatenate into a single DataFrame sorted by entry_time.
    """
    frames = []

    for loader in [load_nse_journal, load_forex_journal]:
        df = loader(base_path=base_path)
        if df is not None and not df.empty:
            frames.append(df)

    archive = Path(base_path) / _ARCHIVE_PATH
    if archive.exists():
        df_arc = load_journal(archive, engine="auto")
        if df_arc is not None and not df_arc.empty:
            df_arc["source_file"] = "cb6_master_archive"
            frames.append(df_arc)

    if not frames:
        logger.warning("No journal data loaded")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)
    if "entry_time" in combined.columns:
        combined = combined.sort_values("entry_time", na_position="last").reset_index(drop=True)

    logger.info(f"Total journal rows: {len(combined)}")
    return combined


def journal_summary(df: pd.DataFrame) -> dict:
    """Return a quick summary dict for logging / Step 3 report."""
    if df.empty:
        return {"rows": 0}
    summary = {"rows": len(df)}
    if "engine" in df.columns:
        summary["by_engine"] = df["engine"].value_counts().to_dict()
    if "win" in df.columns:
        win_col = df["win"].astype(bool)
        summary["win_rate"]   = round(win_col.mean(), 4)
        summary["total_wins"] = int(win_col.sum())
        summary["total_loss"] = int((~win_col).sum())
    if "r_multiple" in df.columns:
        summary["avg_r"]    = round(df["r_multiple"].mean(), 3)
        summary["max_r"]    = round(df["r_multiple"].max(),  3)
        summary["min_r"]    = round(df["r_multiple"].min(),  3)
    if "exit_reason" in df.columns:
        summary["exit_reasons"] = df["exit_reason"].value_counts().to_dict()
    null_counts = df.isnull().sum()
    summary["null_columns"] = null_counts[null_counts > 0].to_dict()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = load_all_journals(base_path="../../")
    summary = journal_summary(df)
    print(f"\nJournal Summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    if not df.empty:
        print(f"\nSample rows:")
        show_cols = [c for c in ["entry_time","engine","symbol","direction","r_multiple","win","exit_reason"] if c in df.columns]
        print(df[show_cols].head(5).to_string())
