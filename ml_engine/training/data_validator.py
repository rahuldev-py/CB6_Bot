"""
ml_engine/training/data_validator.py

Validates the labeled dataset before training:
  - Leakage check   — no post-entry columns in feature set
  - Label distribution — class balance, label coverage
  - Null/NaN audit   — which features are missing and how much
  - Temporal order   — data is time-sorted (no look-ahead from shuffling)
  - Minimum size     — enough rows for reliable model training
  - Sample rows      — quick sanity check output

READ-ONLY. No model training. No live hooks.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger("cb6.ml.data_validator")

# ── Columns that must NEVER appear in the feature set ────────────────────────
LEAKAGE_COLUMNS = {
    "exit_price", "exit_time", "exit_reason", "realized_pnl",
    "pnl", "pnl_usd", "pnl_pts", "result", "targets_hit",
    "t1_hit", "t2_hit", "t3_hit", "targets_hit_count",
    "capital_after", "daily_pnl_after", "daily_pnl_before",
}

# ── Columns required in every training dataset ───────────────────────────────
REQUIRED_COLUMNS = [
    "direction", "mss_confirmed", "fvg_present",
    "win_loss_label", "engine",
]

# ── Minimum dataset sizes ─────────────────────────────────────────────────────
MIN_ROWS_WARN  = 200
MIN_ROWS_TRAIN = 500


def check_leakage(df: pd.DataFrame) -> dict:
    """Check for any post-entry (future-looking) columns in the DataFrame."""
    found = [c for c in LEAKAGE_COLUMNS if c in df.columns]
    return {
        "pass": len(found) == 0,
        "leakage_columns_found": found,
        "message": (
            "PASS — No leakage columns found"
            if not found
            else f"FAIL — {len(found)} leakage columns present: {found}"
        ),
    }


def check_required_columns(df: pd.DataFrame) -> dict:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    return {
        "pass": len(missing) == 0,
        "missing": missing,
        "message": (
            "PASS — All required columns present"
            if not missing
            else f"FAIL — Missing required columns: {missing}"
        ),
    }


def check_size(df: pd.DataFrame) -> dict:
    n = len(df)
    labeled = int(df["win_loss_label"].notna().sum()) if "win_loss_label" in df.columns else 0
    status = "PASS" if n >= MIN_ROWS_TRAIN else ("WARN" if n >= MIN_ROWS_WARN else "FAIL")
    return {
        "pass": status == "PASS",
        "status": status,
        "total_rows": n,
        "labeled_rows": labeled,
        "unlabeled_rows": n - labeled,
        "min_recommended": MIN_ROWS_TRAIN,
        "message": (
            f"{status} — {n} rows total, {labeled} labeled. "
            f"{'Sufficient for training.' if n >= MIN_ROWS_TRAIN else f'Need {MIN_ROWS_TRAIN - n} more for reliable DNN.'}"
        ),
    }


def check_label_distribution(df: pd.DataFrame) -> dict:
    result = {}

    if "win_loss_label" in df.columns:
        labeled = df["win_loss_label"].dropna()
        win_rate = labeled.mean() if len(labeled) > 0 else float("nan")
        result["win_loss"] = {
            "total_labeled": len(labeled),
            "wins": int(labeled.sum()),
            "losses": int((labeled == 0).sum()),
            "win_rate": round(win_rate, 4),
            "imbalanced": bool(win_rate > 0.80 or win_rate < 0.20),
        }

    if "r_multiple_label" in df.columns:
        r = df["r_multiple_label"].dropna()
        result["r_multiple"] = {
            "count": len(r),
            "mean": round(r.mean(), 3) if len(r) else float("nan"),
            "median": round(r.median(), 3) if len(r) else float("nan"),
            "std": round(r.std(), 3) if len(r) else float("nan"),
            "min": round(r.min(), 3) if len(r) else float("nan"),
            "max": round(r.max(), 3) if len(r) else float("nan"),
        }

    if "trade_grade" in df.columns:
        result["trade_grade"] = df["trade_grade"].value_counts().to_dict()

    if "market_regime" in df.columns:
        result["market_regime"] = df["market_regime"].value_counts().to_dict()

    if "mss_type" in df.columns:
        result["mss_type"] = df["mss_type"].value_counts().to_dict()

    if "engine" in df.columns:
        result["by_engine"] = df["engine"].value_counts().to_dict()

    return result


def check_nulls(df: pd.DataFrame, threshold: float = 0.50) -> dict:
    """
    Audit null values. Flags columns with > threshold null ratio.
    """
    null_pct = df.isnull().mean()
    high_null = null_pct[null_pct > threshold].sort_values(ascending=False)
    ok_cols   = null_pct[null_pct <= threshold]

    return {
        "pass": len(high_null) == 0,
        "high_null_cols": high_null.round(3).to_dict(),
        "ok_cols_count": len(ok_cols),
        "threshold": threshold,
        "message": (
            f"PASS — All columns < {threshold:.0%} null"
            if len(high_null) == 0
            else f"WARN — {len(high_null)} columns > {threshold:.0%} null (will need imputation)"
        ),
    }


def check_temporal_order(df: pd.DataFrame) -> dict:
    """Verify data is sorted by time (no accidental shuffle causing look-ahead)."""
    time_col = next((c for c in ["entry_time", "date"] if c in df.columns), None)
    if time_col is None:
        return {"pass": None, "message": "SKIP — No time column found"}

    ts = pd.to_datetime(df[time_col], errors="coerce").dropna()
    if len(ts) < 2:
        return {"pass": None, "message": "SKIP — Insufficient time data"}

    is_sorted = ts.is_monotonic_increasing
    pct_sorted = (ts.diff().dropna() >= pd.Timedelta(0)).mean()
    date_range = f"{ts.min().date()} → {ts.max().date()}"

    return {
        "pass": is_sorted,
        "is_sorted": is_sorted,
        "pct_non_decreasing": round(float(pct_sorted), 4),
        "date_range": date_range,
        "message": (
            f"PASS — Data sorted chronologically ({date_range})"
            if is_sorted
            else f"WARN — Data NOT sorted ({pct_sorted:.1%} non-decreasing). Sort before training."
        ),
    }


def validate(df: pd.DataFrame, verbose: bool = True) -> dict:
    """
    Run all validation checks on a labeled dataset.
    Returns a summary dict with pass/fail per check.
    """
    checks = {
        "leakage"       : check_leakage(df),
        "required_cols" : check_required_columns(df),
        "size"          : check_size(df),
        "nulls"         : check_nulls(df),
        "label_dist"    : check_label_distribution(df),
        "temporal_order": check_temporal_order(df),
    }

    all_pass = all(
        v.get("pass", True) is not False
        for k, v in checks.items()
        if isinstance(v, dict) and k != "label_dist"
    )

    checks["overall_pass"] = all_pass
    checks["ready_for_training"] = (
        all_pass
        and len(df) >= MIN_ROWS_TRAIN
        and df.get("win_loss_label", pd.Series()).notna().sum() >= MIN_ROWS_WARN
    )

    if verbose:
        print("\n" + "=" * 60)
        print("DATA VALIDATION REPORT")
        print("=" * 60)
        print(f"  Rows total       : {len(df)}")
        print(f"  Columns          : {len(df.columns)}")
        print(f"  Leakage check    : {checks['leakage']['message']}")
        print(f"  Required cols    : {checks['required_cols']['message']}")
        print(f"  Size check       : {checks['size']['message']}")
        print(f"  Null check       : {checks['nulls']['message']}")
        print(f"  Temporal order   : {checks['temporal_order']['message']}")
        print()
        print("  Label Distribution:")
        dist = checks["label_dist"]
        if "win_loss" in dist:
            wl = dist["win_loss"]
            print(f"    Win/Loss       : {wl['wins']}W / {wl['losses']}L = {wl['win_rate']:.1%} WR")
            if wl["imbalanced"]:
                print("    WARNING: Class imbalance detected — use class weights in training")
        if "r_multiple" in dist:
            rm = dist["r_multiple"]
            print(f"    R-Multiple     : mean={rm['mean']:.2f} | median={rm['median']:.2f} | min={rm['min']:.2f} | max={rm['max']:.2f}")
        if "mss_type" in dist:
            print(f"    MSS Types      : {dist['mss_type']}")
        if "market_regime" in dist:
            print(f"    Regimes        : {dist['market_regime']}")
        if "trade_grade" in dist:
            print(f"    Trade Grades   : {dist['trade_grade']}")
        if "by_engine" in dist:
            print(f"    By Engine      : {dist['by_engine']}")
        if "high_null_cols" in checks["nulls"] and checks["nulls"]["high_null_cols"]:
            print(f"\n  High-Null Cols (>{checks['nulls']['threshold']:.0%}):")
            for col, pct in list(checks["nulls"]["high_null_cols"].items())[:10]:
                print(f"    {col:<30}: {pct:.1%} null")
        print()
        status = "READY FOR TRAINING" if checks["ready_for_training"] else "NOT READY — see issues above"
        print(f"  Overall: {status}")
        print("=" * 60)

    return checks


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.WARNING)

    from ml_engine.training.dataset_builder import build_dataset
    df = build_dataset(base_path="")
    if df is not None:
        result = validate(df, verbose=True)
        print(f"\nready_for_training: {result['ready_for_training']}")
