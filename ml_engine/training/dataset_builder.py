"""
ml_engine/training/dataset_builder.py

Combines all data sources (backtest CSVs, journal CSVs, paper state)
with auto-generated labels to build a unified ML training dataset.

Output: labeled CSV + Parquet in ml_engine/data/labeled/

READ-ONLY on source data. No writes to live files. No execution imports.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logger = logging.getLogger("cb6.ml.dataset_builder")

OUTPUT_DIR = Path("ml_engine/data/labeled")

# Columns kept in final dataset (drops raw broker-specific fields)
CORE_FEATURE_COLS = [
    # Identity
    "trade_id", "engine", "source_file", "date", "entry_time",
    # Trade params
    "symbol", "direction", "entry", "stop_loss", "target1", "target2",
    "risk", "rr_ratio", "confluence",
    # ICT labels
    "market_regime", "liquidity_sweep", "sweep_type", "sweep_depth_pct",
    "fvg_present", "fvg_quality", "fvg_size", "fvg_displacement",
    "order_block_present", "ob_type",
    "mss_confirmed", "mss_type", "choch_confirmed", "bos_confirmed",
    # Outcome labels
    "win_loss_label", "r_multiple_label", "trade_grade",
    # Derived
    "session", "regime",
]


def _coerce_entry_col(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise entry price column (called 'entry' or 'entry_price')."""
    if "entry" not in df.columns and "entry_price" in df.columns:
        df["entry"] = df["entry_price"]
    return df


def _coerce_risk_col(df: pd.DataFrame) -> pd.DataFrame:
    """Derive risk from stop_loss if not present."""
    if "risk" not in df.columns or df["risk"].isna().all():
        entry = pd.to_numeric(df.get("entry", df.get("entry_price")), errors="coerce")
        sl    = pd.to_numeric(df.get("stop_loss"), errors="coerce")
        df["risk"] = (entry - sl).abs()
    return df


def _add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add simple derived columns useful for ML."""
    entry = pd.to_numeric(df.get("entry", df.get("entry_price")), errors="coerce")
    risk  = pd.to_numeric(df.get("risk"), errors="coerce")

    if "rr_ratio" not in df.columns or df["rr_ratio"].isna().all():
        t2 = pd.to_numeric(df.get("target2"), errors="coerce")
        df["rr_ratio"] = ((t2 - entry).abs() / risk.replace(0, np.nan)).round(2)

    # Encode direction as binary
    if "direction" in df.columns:
        df["direction_bin"] = (
            df["direction"].str.upper().isin(["BULLISH", "BUY", "LONG"])
        ).astype(int)

    # Encode mss_type as binary (CHoCH=1, BOS=0)
    if "mss_type" in df.columns:
        df["choch_bin"] = df["mss_type"].str.upper().eq("CHOCH").astype(int)

    # Encode fvg_quality as ordinal
    quality_map = {"NONE": 0, "WEAK": 1, "STRONG": 2}
    if "fvg_quality" in df.columns:
        df["fvg_quality_ord"] = df["fvg_quality"].map(quality_map).fillna(0).astype(int)

    # Regime as ordinal
    regime_map = {"CHOPPY": 0, "NEUTRAL": 1, "TRENDING": 2}
    if "market_regime" in df.columns:
        df["regime_ord"] = df["market_regime"].map(regime_map).fillna(1).astype(int)

    return df


def _drop_future_leakage(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove columns that contain post-entry information (forward-looking).
    These must never be used as features — only as labels.
    """
    leakage_cols = [
        "exit_price", "exit_time", "exit_reason", "realized_pnl",
        "pnl", "pnl_usd", "pnl_pts", "targets_hit", "result",
        "t1_hit", "t2_hit", "t3_hit", "targets_hit_count",
        "capital_after", "daily_pnl_after", "daily_pnl_before",
    ]
    drop = [c for c in leakage_cols if c in df.columns]
    if drop:
        logger.info(f"Dropping {len(drop)} post-entry columns (leakage guard): {drop}")
        df = df.drop(columns=drop)
    return df


def build_from_backtests(base_path: str = "") -> Optional[pd.DataFrame]:
    """Load + label backtest CSVs."""
    from ml_engine.training.backtest_loader import load_all_backtests
    from ml_engine.training.label_builder import label_from_existing

    df = load_all_backtests(Path(base_path) / "data/")
    if df is None or df.empty:
        logger.warning("No backtest data loaded")
        return None

    df = _coerce_entry_col(df)
    df = _coerce_risk_col(df)
    df = label_from_existing(df)
    df = _add_derived_features(df)
    logger.info(f"Backtest rows labeled: {len(df)}")
    return df


def build_from_journal(base_path: str = "") -> Optional[pd.DataFrame]:
    """Load + label journal CSVs."""
    from ml_engine.training.journal_loader import load_all_journals
    from ml_engine.training.label_builder import label_from_existing

    df = load_all_journals(base_path=base_path)
    if df is None or df.empty:
        logger.warning("No journal data loaded")
        return None

    df = _coerce_entry_col(df)
    df = _coerce_risk_col(df)
    df = label_from_existing(df)
    df = _add_derived_features(df)
    logger.info(f"Journal rows labeled: {len(df)}")
    return df


def build_from_trade_history(base_path: str = "") -> Optional[pd.DataFrame]:
    """Load + label closed trades from paper state JSON files."""
    from ml_engine.training.trade_history_loader import load_trade_history
    from ml_engine.training.label_builder import label_from_existing

    df = load_trade_history(engine="all", base_path=base_path)
    if df is None or df.empty:
        logger.warning("No trade history loaded")
        return None

    df = _coerce_entry_col(df)
    df = _coerce_risk_col(df)
    df = label_from_existing(df)
    df = _add_derived_features(df)
    logger.info(f"Trade history rows labeled: {len(df)}")
    return df


def build_dataset(
    base_path: str = "",
    include_backtests: bool = True,
    include_journal: bool = True,
    include_trade_history: bool = True,
    apply_leakage_guard: bool = True,
    min_rows: int = 50,
) -> Optional[pd.DataFrame]:
    """
    Master builder — combines all sources, deduplicates, and returns
    a clean labeled DataFrame ready for Step 5 feature engineering.

    Parameters
    ----------
    base_path            : Root path prefix ('' when running from c:/cb6_bot/).
    include_backtests    : Load backtest CSVs.
    include_journal      : Load journal CSVs.
    include_trade_history: Load paper state JSON closed trades.
    apply_leakage_guard  : Drop post-entry columns.
    min_rows             : Minimum rows required (warns if below).

    Returns
    -------
    Combined labeled DataFrame, or None if no data loaded.
    """
    frames = []

    if include_backtests:
        df = build_from_backtests(base_path)
        if df is not None:
            frames.append(df)

    if include_journal:
        df = build_from_journal(base_path)
        if df is not None:
            frames.append(df)

    if include_trade_history:
        df = build_from_trade_history(base_path)
        if df is not None:
            frames.append(df)

    if not frames:
        logger.error("No data available from any source")
        return None

    combined = pd.concat(frames, ignore_index=True, sort=False)

    # Deduplicate on entry_time + symbol + entry (same trade across sources)
    dedup_cols = [c for c in ["entry_time", "symbol", "entry"] if c in combined.columns]
    if dedup_cols:
        before = len(combined)
        combined = combined.drop_duplicates(subset=dedup_cols, keep="first")
        after = len(combined)
        if before != after:
            logger.info(f"Deduplication: {before} → {after} rows ({before - after} duplicates removed)")

    if apply_leakage_guard:
        combined = _drop_future_leakage(combined)

    if len(combined) < min_rows:
        logger.warning(
            f"Dataset has only {len(combined)} rows (min recommended: {min_rows}). "
            "DNN training may produce unreliable results."
        )

    logger.info(
        f"Dataset built: {len(combined)} rows | "
        f"win_rate={combined['win_loss_label'].mean():.1%} | "
        f"labeled_outcomes={combined['win_loss_label'].notna().sum()} / {len(combined)}"
    )
    return combined


def save_dataset(
    df: pd.DataFrame,
    name: str = "cb6_labeled",
    output_dir: Path = OUTPUT_DIR,
) -> dict:
    """
    Save labeled dataset as CSV and Parquet (if pyarrow available).
    Returns dict of saved file paths.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    saved = {}

    # CSV — always
    csv_path = output_dir / f"{name}_{ts}.csv"
    df.to_csv(csv_path, index=False)
    saved["csv"] = str(csv_path)
    logger.info(f"Saved CSV: {csv_path} ({len(df)} rows)")

    # Latest symlink (just overwrite the latest file for convenience)
    latest_csv = output_dir / f"{name}_latest.csv"
    df.to_csv(latest_csv, index=False)
    saved["csv_latest"] = str(latest_csv)

    # Parquet — optional
    try:
        parquet_path = output_dir / f"{name}_{ts}.parquet"
        df.to_parquet(parquet_path, index=False, compression="snappy")
        saved["parquet"] = str(parquet_path)
        logger.info(f"Saved Parquet: {parquet_path}")
    except ImportError:
        logger.info("pyarrow not installed — Parquet skipped (CSV only)")
    except Exception as e:
        logger.warning(f"Parquet save failed: {e}")

    return saved


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)

    df = build_dataset(base_path="")
    if df is not None:
        paths = save_dataset(df, name="cb6_labeled")
        print(f"\nDataset: {len(df)} rows")
        print(f"Saved: {paths}")
        print(f"Columns: {list(df.columns)}")
        print(df[["engine", "direction", "mss_type", "fvg_present",
                   "win_loss_label", "r_multiple_label", "trade_grade"]].head(8).to_string())
