"""
ml_engine/monitoring/performance_tracker.py

PerformanceTracker: rolling accuracy and bucket-level stats from
audited shadow predictions.

Reads shadow_predictions.jsonl (only rows with actual_outcome filled).

Computes:
    - Rolling accuracy (last N predictions)
    - AUC over audited window
    - Per-bucket accuracy and expectancy
    - Calibration: does 0.7 win_prob really win 70% of the time?

Usage:
    from ml_engine.monitoring.performance_tracker import PerformanceTracker
    tracker = PerformanceTracker(engine="nse")
    stats = tracker.compute(window=100)
    print(stats)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("cb6.ml.performance_tracker")

SHADOW_LOG = Path("ml_engine/logs/shadow_predictions.jsonl")

# Calibration bins (win_prob ranges)
CAL_BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]


class PerformanceTracker:

    def __init__(self, engine: str = "nse"):
        self.engine = engine
        self._df: Optional[pd.DataFrame] = None

    def _load(self) -> pd.DataFrame:
        if not SHADOW_LOG.exists():
            return pd.DataFrame()
        rows = []
        with open(SHADOW_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        if "engine" in df.columns:
            df = df[df["engine"] == self.engine].copy()
        return df

    def compute(self, window: int = 100) -> dict:
        """
        Compute performance stats over the last `window` audited predictions.

        Returns
        -------
        dict with:
            n_total            total shadow predictions for this engine
            n_audited          predictions with known outcomes
            n_window           window used for stats (min of window, n_audited)
            accuracy           rolling accuracy
            auc                AUC over audited window
            brier              Brier score
            bucket_stats       per-bucket accuracy and expectancy
            calibration        calibration by win_prob bin
            shadow_ready       bool — True if n_audited >= 100 (Step 11 gate)
        """
        df = self._load()
        self._df = df

        n_total   = len(df)
        audited   = df[df["actual_outcome"].notna()].copy() if "actual_outcome" in df.columns else pd.DataFrame()
        n_audited = len(audited)

        if n_audited == 0:
            return {
                "engine"      : self.engine,
                "n_total"     : n_total,
                "n_audited"   : 0,
                "n_window"    : 0,
                "accuracy"    : None,
                "auc"         : None,
                "brier"       : None,
                "bucket_stats": {},
                "calibration" : [],
                "shadow_ready": False,
                "message"     : "no audited predictions yet",
            }

        # Restrict to rolling window
        win_df = audited.tail(window).copy()
        n_win  = len(win_df)

        y_true = win_df["actual_outcome"].astype(float).values
        y_pred = win_df["win_probability"].astype(float).values

        # Accuracy
        accuracy = float(((y_pred >= 0.5) == y_true.astype(bool)).mean())

        # AUC
        try:
            from sklearn.metrics import roc_auc_score
            auc = float(roc_auc_score(y_true, y_pred)) if len(np.unique(y_true)) > 1 else float("nan")
        except Exception:
            auc = float("nan")

        # Brier
        brier = float(np.mean((y_pred - y_true) ** 2))

        # Per-bucket stats
        bucket_stats = {}
        for bucket in ["A+", "A", "B", "C"]:
            col = "final_bucket" if "final_bucket" in win_df.columns else "ml_bucket"
            bmask = win_df.get(col, pd.Series(dtype=str)) == bucket
            bdf   = win_df[bmask]
            if len(bdf) == 0:
                bucket_stats[bucket] = {"n": 0, "accuracy": None, "win_rate": None, "avg_r": None}
                continue
            bt = bdf["actual_outcome"].astype(float)
            br = bdf["actual_r"].astype(float) if "actual_r" in bdf.columns else pd.Series([float("nan")] * len(bdf))
            bp = bdf["win_probability"].astype(float)
            bucket_stats[bucket] = {
                "n"       : int(len(bdf)),
                "accuracy": round(float(((bp >= 0.5) == bt.astype(bool)).mean()), 4),
                "win_rate": round(float(bt.mean()), 4),
                "avg_r"   : round(float(br.mean()), 4) if br.notna().any() else None,
            }

        # Calibration: does win_prob 0.7 → 70% win rate?
        calibration = []
        for lo, hi in CAL_BINS:
            mask = (y_pred >= lo) & (y_pred < hi)
            if mask.sum() == 0:
                calibration.append({
                    "bin"          : f"{lo:.1f}-{hi:.1f}",
                    "n"            : 0,
                    "mean_pred"    : None,
                    "actual_wr"    : None,
                    "calibration_err": None,
                })
                continue
            mean_pred  = float(y_pred[mask].mean())
            actual_wr  = float(y_true[mask].mean())
            cal_err    = round(abs(mean_pred - actual_wr), 4)
            calibration.append({
                "bin"            : f"{lo:.1f}-{hi:.1f}",
                "n"              : int(mask.sum()),
                "mean_pred"      : round(mean_pred, 4),
                "actual_wr"      : round(actual_wr, 4),
                "calibration_err": cal_err,
            })

        return {
            "engine"      : self.engine,
            "n_total"     : n_total,
            "n_audited"   : n_audited,
            "n_window"    : n_win,
            "accuracy"    : round(accuracy, 4),
            "auc"         : round(auc, 4) if not np.isnan(auc) else None,
            "brier"       : round(brier, 4),
            "bucket_stats": bucket_stats,
            "calibration" : calibration,
            "shadow_ready": n_audited >= 100,
        }

    def bucket_monotonic(self) -> bool:
        """Check if live win rates follow A+ > A > B > C order."""
        if self._df is None:
            self.compute()
        stats = self.compute()
        wr = [
            stats["bucket_stats"].get(b, {}).get("win_rate")
            for b in ["A+", "A", "B", "C"]
        ]
        valid = [w for w in wr if w is not None]
        return all(valid[i] >= valid[i + 1] for i in range(len(valid) - 1)) if len(valid) >= 2 else False
