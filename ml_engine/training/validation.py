"""
ml_engine/training/validation.py

Shared validation utilities for all CB6 ML models.
- Time-based train/val split (no shuffling)
- Walk-forward cross-validation folds
- Metric computation (accuracy, precision, recall, F1, AUC, Brier)
- Confidence bucket performance analysis
- Expectancy and profit factor by bucket
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("cb6.ml.validation")


def time_split(
    X: pd.DataFrame | np.ndarray,
    y: pd.Series | np.ndarray,
    y_r: Optional[pd.Series | np.ndarray] = None,
    train_ratio: float = 0.80,
) -> tuple:
    """
    Chronological train/val split. No shuffling — preserves temporal order.

    Returns (X_train, X_val, y_train, y_val, y_r_train, y_r_val)
    y_r is optional regression target — pass None if unused.
    """
    n       = len(X)
    split   = int(n * train_ratio)

    if isinstance(X, pd.DataFrame):
        X_train, X_val = X.iloc[:split].values, X.iloc[split:].values
    else:
        X_train, X_val = X[:split], X[split:]

    if isinstance(y, pd.Series):
        y_train, y_val = y.iloc[:split].values, y.iloc[split:].values
    else:
        y_train, y_val = y[:split], y[split:]

    if y_r is not None:
        if isinstance(y_r, pd.Series):
            yr_train, yr_val = y_r.iloc[:split].values, y_r.iloc[split:].values
        else:
            yr_train, yr_val = y_r[:split], y_r[split:]
    else:
        yr_train = yr_val = None

    logger.info(f"Time split: {split} train / {n - split} val (ratio={train_ratio})")
    return X_train, X_val, y_train, y_val, yr_train, yr_val


def walk_forward_folds(
    X: np.ndarray,
    y: np.ndarray,
    n_folds: int = 5,
    min_train_size: float = 0.4,
) -> list[tuple]:
    """
    Walk-forward cross-validation: expanding window.
    Each fold adds more training data, tests on the next unseen block.

    Returns list of (train_idx, val_idx) tuples.
    """
    n        = len(X)
    fold_size = int(n * (1 - min_train_size) / n_folds)
    base     = int(n * min_train_size)

    folds = []
    for i in range(n_folds):
        train_end = base + i * fold_size
        val_end   = min(train_end + fold_size, n)
        if val_end <= train_end:
            break
        folds.append((np.arange(train_end), np.arange(train_end, val_end)))
        logger.debug(f"Fold {i+1}: train[0:{train_end}] val[{train_end}:{val_end}]")

    logger.info(f"Walk-forward: {len(folds)} folds from {n} samples")
    return folds


def compute_classification_metrics(
    y_true: np.ndarray,
    y_pred_prob: np.ndarray,
    threshold: float = 0.5,
) -> dict:
    """Compute standard binary classification metrics."""
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score,
        f1_score, roc_auc_score, brier_score_loss,
        confusion_matrix,
    )

    y_pred = (y_pred_prob >= threshold).astype(int)
    y_true = y_true.astype(int)

    metrics = {
        "accuracy"   : round(float(accuracy_score(y_true, y_pred)), 4),
        "precision"  : round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall"     : round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1"         : round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "brier_score": round(float(brier_score_loss(y_true, y_pred_prob)), 4),
        "threshold"  : threshold,
        "n_samples"  : len(y_true),
    }

    try:
        metrics["auc"] = round(float(roc_auc_score(y_true, y_pred_prob)), 4)
    except Exception:
        metrics["auc"] = float("nan")

    cm = confusion_matrix(y_true, y_pred)
    metrics["confusion_matrix"] = cm.tolist()
    if cm.shape == (2, 2):
        tn, fp, fn, tp = cm.ravel()
        metrics["true_positives"]  = int(tp)
        metrics["false_positives"] = int(fp)
        metrics["true_negatives"]  = int(tn)
        metrics["false_negatives"] = int(fn)

    return metrics


def compute_regression_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> dict:
    """Compute regression metrics for expected_r prediction."""
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    # Drop rows where y_true is NaN (R-multiple not recorded)
    mask = ~np.isnan(y_true.astype(float))
    if mask.sum() < 2:
        return {"mae": float("nan"), "rmse": float("nan"), "r2": float("nan"),
                "mean_err": float("nan"), "n_samples": 0, "note": "no r_multiple labels"}

    yt = y_true[mask].astype(float)
    yp = y_pred[mask].astype(float)

    return {
        "mae"     : round(float(mean_absolute_error(yt, yp)), 4),
        "rmse"    : round(float(np.sqrt(mean_squared_error(yt, yp))), 4),
        "r2"      : round(float(r2_score(yt, yp)), 4),
        "mean_err": round(float(np.mean(yp - yt)), 4),
        "n_samples": int(mask.sum()),
    }


def confidence_bucket_analysis(
    y_true_win: np.ndarray,
    y_pred_prob: np.ndarray,
    y_true_r: Optional[np.ndarray] = None,
) -> dict:
    """
    Break down model performance by confidence bucket.
    This is the KEY metric for CB6: A+ bucket must outperform A, A > B, B > C.

    confidence = abs(win_prob - 0.5) * 2
    A+: ≥ 0.60  (win_prob ≥ 0.80 or ≤ 0.20)
    A : ≥ 0.40  (win_prob ≥ 0.70 or ≤ 0.30)
    B : ≥ 0.20  (win_prob ≥ 0.60 or ≤ 0.40)
    C : < 0.20  (near 50/50)
    """
    confidence = np.abs(y_pred_prob - 0.5) * 2.0

    bucket_thresholds = [("A+", 0.60), ("A", 0.40), ("B", 0.20), ("C", 0.00)]
    result = {}

    for bucket, threshold in bucket_thresholds:
        if bucket == "C":
            mask = confidence < 0.20
        else:
            next_t = dict(bucket_thresholds).get(
                {"A+": "A", "A": "B", "B": "C"}.get(bucket, "C"), 0.0
            )
            mask = (confidence >= threshold)

        n = int(mask.sum())
        if n == 0:
            result[bucket] = {"n": 0, "win_rate": None, "expectancy": None,
                               "profit_factor": None, "avg_conf": None}
            continue

        wins    = y_true_win[mask].astype(float)
        win_rate = float(wins.mean())

        if y_true_r is not None:
            r_vals = y_true_r[mask].astype(float)
            pos_r  = r_vals[r_vals > 0]
            neg_r  = np.abs(r_vals[r_vals < 0])
            expectancy    = float(r_vals.mean())
            profit_factor = float(pos_r.sum() / neg_r.sum()) if neg_r.sum() > 0 else float("inf")
            avg_r         = float(r_vals.mean())
        else:
            expectancy    = None
            profit_factor = None
            avg_r         = None

        result[bucket] = {
            "n"            : n,
            "win_rate"     : round(win_rate, 4),
            "expectancy"   : round(expectancy, 3) if expectancy is not None else None,
            "profit_factor": round(profit_factor, 3) if profit_factor is not None and profit_factor != float("inf") else profit_factor,
            "avg_r"        : round(avg_r, 3) if avg_r is not None else None,
            "avg_conf"     : round(float(confidence[mask].mean()), 4),
        }

    # Check monotonic: A+ > A > B > C (win_rate should decrease)
    wr_list = [result[b]["win_rate"] for b in ["A+", "A", "B", "C"] if result[b]["win_rate"] is not None]
    result["_monotonic_wr"] = all(wr_list[i] >= wr_list[i+1] for i in range(len(wr_list)-1))
    result["_all_positive_expectancy"] = all(
        result[b]["expectancy"] is not None and result[b]["expectancy"] > 0
        for b in ["A+", "A", "B"]
        if result[b]["n"] > 0
    )

    return result


def feature_importance_permutation(
    model,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list[str],
    n_repeats: int = 5,
    _metric: str = "auc",
) -> pd.DataFrame:
    """
    Permutation feature importance. Shuffles each feature and measures AUC drop.
    Slow but model-agnostic — works with any sklearn-compatible predictor.
    """
    from sklearn.metrics import roc_auc_score

    base_pred = model.predict(X_val)
    if isinstance(base_pred, dict):
        base_pred = base_pred["win_probability"]

    try:
        base_score = roc_auc_score(y_val, base_pred)
    except Exception:
        base_score = 0.5

    importances = []
    rng = np.random.default_rng(42)

    for j, fname in enumerate(feature_names):
        drops = []
        for _ in range(n_repeats):
            X_perm = X_val.copy()
            X_perm[:, j] = rng.permutation(X_perm[:, j])
            pred_perm = model.predict(X_perm)
            if isinstance(pred_perm, dict):
                pred_perm = pred_perm["win_probability"]
            try:
                score = roc_auc_score(y_val, pred_perm)
            except Exception:
                score = base_score
            drops.append(base_score - score)
        importances.append({"feature": fname, "importance": round(float(np.mean(drops)), 5)})

    df_imp = pd.DataFrame(importances).sort_values("importance", ascending=False).reset_index(drop=True)
    return df_imp


def print_metrics_report(metrics: dict, label: str = "") -> None:
    header = f"Metrics - {label}" if label else "Metrics"
    print(f"\n{'-'*50}")
    print(f"  {header}")
    print(f"{'-'*50}")
    for k, v in metrics.items():
        if k == "confusion_matrix":
            print(f"  confusion_matrix:")
            for row in v:
                print(f"    {row}")
        elif isinstance(v, float):
            print(f"  {k:<25}: {v:.4f}")
        else:
            print(f"  {k:<25}: {v}")
