"""
ml_engine/training/train_rnn.py

LSTM training script for CB6 trade scorer.

Pipeline:
  1. Load labeled dataset (same as DNN)
  2. Run feature pipeline
  3. Time-based split (80/20)
  4. Walk-forward validation (5 folds)
  5. Train RNNTradeScorer (LSTM with attention)
  6. Evaluate: classification + regression + confidence bucket analysis
  7. Save model to ml_engine/models/saved/

Returns metrics dict. Never touches live trading code.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logger = logging.getLogger("cb6.ml.train_rnn")

MODEL_SAVE_BASE = Path("ml_engine/models/saved")
REGISTRY_PATH   = Path("ml_engine/config/model_registry.json")
MIN_SAMPLES     = 200


def _drop_zero_variance(X: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    std  = X.std()
    drop = std[std == 0].index.tolist()
    if drop:
        logger.info(f"Dropping {len(drop)} zero-variance features: {drop}")
        X = X.drop(columns=drop)
    return X, drop


def _deduplicate_columns(X: pd.DataFrame) -> pd.DataFrame:
    seen = set()
    keep = []
    for col in X.columns:
        if col not in seen:
            keep.append(col)
            seen.add(col)
    return X[keep]


def train(
    engine: str = "nse",
    epochs: int = 150,
    lr: float = 1e-3,
    batch_size: int = 32,
    patience: int = 20,
    train_ratio: float = 0.80,
    n_folds: int = 5,
    hidden_dim: int = 64,
    n_layers: int = 2,
    dropout: float = 0.3,
    seq_len: int = 10,
    run_walk_forward: bool = True,
    save_model: bool = True,
) -> dict:
    """
    Full LSTM training run for one engine.

    Parameters
    ----------
    engine    : 'nse' | 'forex' | 'all'
    seq_len   : Number of prior trades used as context window
    save_model: Save weights + scaler to ml_engine/models/saved/

    Returns
    -------
    Dict with all metrics, model path, and activation gate status.
    """
    from ml_engine.training.dataset_builder import build_dataset
    from ml_engine.features.feature_pipeline import build_features
    from ml_engine.models.rnn_sequence_model import RNNTradeScorer
    from ml_engine.training.validation import (
        time_split, walk_forward_folds,
        compute_classification_metrics, compute_regression_metrics,
        confidence_bucket_analysis, print_metrics_report,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    version_id = f"rnn_{engine}_v1_{ts}"
    logger.info(f"Starting LSTM training: engine={engine} version={version_id}")

    # ── 1. Load data ──────────────────────────────────────────────────────
    df = build_dataset(base_path="")
    if df is None or df.empty:
        return {"error": "No dataset available", "ready": False}

    if engine != "all" and "engine" in df.columns:
        df_eng = df[df["engine"] == engine].copy()
        if len(df_eng) < MIN_SAMPLES // 2:
            logger.warning(f"Only {len(df_eng)} rows for engine={engine}, using all engines")
            df_eng = df.copy()
    else:
        df_eng = df.copy()

    # ── 2. Feature pipeline ───────────────────────────────────────────────
    X, y_win, y_r, feat_names = build_features(df_eng)

    if len(X) < MIN_SAMPLES:
        msg = f"Only {len(X)} labeled rows — need {MIN_SAMPLES}+ for reliable training"
        logger.warning(msg)
        print(f"\nWARNING: {msg}")

    X = _deduplicate_columns(X)
    X, dropped_zv = _drop_zero_variance(X)
    feat_names = X.columns.tolist()

    X_arr     = X.values.astype(np.float32)
    y_win_arr = y_win.values.astype(np.float32)
    y_r_arr   = y_r.values.astype(np.float32)

    print(f"\nTraining dataset: {X_arr.shape[0]} rows x {X_arr.shape[1]} features")
    print(f"Engine: {engine} | Win rate: {y_win_arr.mean():.1%} | Avg R: {np.nanmean(y_r_arr):.2f}")
    print(f"Sequence length (context window): {seq_len} trades")

    # ── 3. Time-based split ───────────────────────────────────────────────
    X_train, X_val, y_win_tr, y_win_val, y_r_tr, y_r_val = time_split(
        X_arr, y_win_arr, y_r_arr, train_ratio=train_ratio
    )
    print(f"Split: {len(X_train)} train / {len(X_val)} val")

    # ── 4. Walk-forward validation ────────────────────────────────────────
    wf_results = []
    if run_walk_forward and len(X_arr) >= MIN_SAMPLES:
        folds = walk_forward_folds(X_arr, y_win_arr, n_folds=n_folds)
        print(f"\nWalk-forward validation ({len(folds)} folds):")

        for i, (tr_idx, val_idx) in enumerate(folds):
            wf_scorer = RNNTradeScorer(
                input_dim=X_arr.shape[1],
                hidden_dim=hidden_dim,
                n_layers=n_layers,
                dropout=dropout,
                seq_len=seq_len,
            )
            wf_scorer.fit(
                X_arr[tr_idx], y_win_arr[tr_idx], y_r_arr[tr_idx],
                X_arr[val_idx], y_win_arr[val_idx], y_r_arr[val_idx],
                epochs=min(epochs, 80), lr=lr,
                batch_size=batch_size, patience=12,
            )
            pred = wf_scorer.predict(X_arr[val_idx])
            fold_metrics = compute_classification_metrics(
                y_win_arr[val_idx], pred["win_probability"]
            )
            wf_results.append({
                "fold" : i + 1,
                "n_train": len(tr_idx),
                "n_val"  : len(val_idx),
                "auc"    : fold_metrics["auc"],
                "f1"     : fold_metrics["f1"],
                "brier"  : fold_metrics["brier_score"],
            })
            print(f"  Fold {i+1}: auc={fold_metrics['auc']:.4f} f1={fold_metrics['f1']:.4f} brier={fold_metrics['brier_score']:.4f}")

    # ── 5. Train final model ──────────────────────────────────────────────
    print(f"\nTraining final LSTM model (epochs={epochs}, patience={patience}, seq_len={seq_len})...")
    scorer = RNNTradeScorer(
        input_dim=X_arr.shape[1],
        hidden_dim=hidden_dim,
        n_layers=n_layers,
        dropout=dropout,
        seq_len=seq_len,
    )
    scorer.feature_names = feat_names

    history = scorer.fit(
        X_train, y_win_tr, y_r_tr,
        X_val,   y_win_val, y_r_val,
        epochs=epochs, lr=lr,
        batch_size=batch_size, patience=patience,
    )

    # ── 6. Evaluate ───────────────────────────────────────────────────────
    pred_val  = scorer.predict(X_val)
    win_probs = pred_val["win_probability"]
    exp_r     = pred_val["expected_r"]

    cls_metrics = compute_classification_metrics(y_win_val, win_probs)
    reg_metrics = compute_regression_metrics(y_r_val, exp_r)
    bucket_perf = confidence_bucket_analysis(y_win_val, win_probs, y_r_val)

    print_metrics_report(cls_metrics, label=f"Classification ({engine.upper()} LSTM)")
    print_metrics_report(reg_metrics, label=f"Regression / Expected-R ({engine.upper()} LSTM)")

    print(f"\nConfidence Bucket Performance:")
    print(f"  {'Bucket':<5} {'N':>5} {'WinRate':>8} {'AvgR':>7} {'Expectancy':>11} {'ProfitFactor':>13}")
    for b in ["A+", "A", "B", "C"]:
        bdata = bucket_perf.get(b, {})
        if bdata.get("n", 0) == 0:
            print(f"  {b:<5} {'0':>5} {'--':>8} {'--':>7} {'--':>11} {'--':>13}")
        else:
            pf = bdata.get("profit_factor")
            pf_str = f"{pf:.3f}" if isinstance(pf, float) and pf != float("inf") else "inf"
            print(
                f"  {b:<5} {bdata['n']:>5} "
                f"{bdata['win_rate']:>8.1%} "
                f"{(bdata['avg_r'] or 0):>7.2f} "
                f"{(bdata['expectancy'] or 0):>11.3f} "
                f"{pf_str:>13}"
            )

    monotonic = bucket_perf.get("_monotonic_wr", False)
    all_pos_e = bucket_perf.get("_all_positive_expectancy", False)
    print(f"\n  Monotonic win-rate (A+ > A > B > C): {'YES' if monotonic else 'NO'}")
    print(f"  All buckets positive expectancy:      {'YES' if all_pos_e else 'NO'}")

    # ── 7. Save model ─────────────────────────────────────────────────────
    model_path = None
    if save_model:
        save_dir = MODEL_SAVE_BASE / version_id
        scorer.metadata = {
            "version_id"   : version_id,
            "engine"       : engine,
            "trained_at"   : ts,
            "train_samples": len(X_train),
            "val_samples"  : len(X_val),
            "seq_len"      : seq_len,
            "dropped_zv"   : dropped_zv,
            "history_last5": history["train_loss"][-5:],
        }
        saved = scorer.save(save_dir)
        model_path = saved["path"]
        print(f"\nModel saved: {model_path}")

    # ── Build result dict ─────────────────────────────────────────────────
    result = {
        "version_id"      : version_id,
        "engine"          : engine,
        "train_samples"   : len(X_train),
        "val_samples"     : len(X_val),
        "features"        : len(feat_names),
        "seq_len"         : seq_len,
        "accuracy"        : cls_metrics["accuracy"],
        "precision"       : cls_metrics["precision"],
        "recall"          : cls_metrics["recall"],
        "f1"              : cls_metrics["f1"],
        "auc"             : cls_metrics["auc"],
        "brier_score"     : cls_metrics["brier_score"],
        "expected_r_mae"  : reg_metrics["mae"],
        "r2_score"        : reg_metrics["r2"],
        "confusion_matrix": cls_metrics["confusion_matrix"],
        "bucket_performance": {
            b: {k: v for k, v in bucket_perf[b].items()}
            for b in ["A+", "A", "B", "C"]
        },
        "monotonic_wr"           : monotonic,
        "all_positive_expectancy": all_pos_e,
        "walk_forward"           : wf_results,
        "model_path"             : model_path,
        "activation_gate_passed" : (
            cls_metrics["auc"] >= 0.55
            and monotonic
            and all_pos_e
        ),
    }

    _update_registry(engine, result, model_path)

    # Log training event for gate checks
    try:
        from ml_engine.monitoring.ml_logger import MLLogger
        MLLogger.log_train(engine, result, model_type="lstm")
        MLLogger.log_gate(
            engine=engine, model_type="lstm",
            passed=result["activation_gate_passed"],
            reason=f"AUC={cls_metrics['auc']:.4f} monotonic={monotonic} pos_exp={all_pos_e}",
            metrics=result,
        )
    except Exception:
        pass

    gate = result["activation_gate_passed"]
    print(f"\n{'='*50}")
    print(f"  Activation gate: {'PASS' if gate else 'NOT YET'}")
    print(f"  AUC={cls_metrics['auc']:.4f} | Monotonic={monotonic} | PosExpectancy={all_pos_e}")
    print(f"{'='*50}")

    return result


def _update_registry(engine: str, metrics: dict, model_path: str) -> None:
    """Append LSTM version to model_registry.json under rnn_nse / rnn_forex."""
    try:
        with open(REGISTRY_PATH) as f:
            registry = json.load(f)

        model_key = f"rnn_trade_scorer_{engine}"
        if model_key not in registry["models"]:
            # Fall back to first available rnn slot
            rnn_keys = [k for k in registry["models"] if "rnn" in k]
            model_key = rnn_keys[0] if rnn_keys else list(registry["models"].keys())[0]

        version_entry = {
            "version_id"            : metrics["version_id"],
            "trained_at"            : metrics["version_id"].split("_v1_")[-1],
            "train_samples"         : metrics["train_samples"],
            "val_samples"           : metrics["val_samples"],
            "seq_len"               : metrics["seq_len"],
            "accuracy"              : metrics["accuracy"],
            "auc"                   : metrics["auc"],
            "f1"                    : metrics["f1"],
            "brier_score"           : metrics["brier_score"],
            "expected_r_mae"        : metrics["expected_r_mae"],
            "activation_gate_passed": metrics["activation_gate_passed"],
            "model_path"            : model_path or "",
            "shadow_predictions"    : 0,
            "shadow_accuracy"       : None,
        }

        if model_key in registry["models"]:
            registry["models"][model_key]["versions"].append(version_entry)
            registry["models"][model_key]["status"] = "TRAINED"
            if metrics["activation_gate_passed"]:
                registry["models"][model_key]["active_version"] = metrics["version_id"]
                registry["models"][model_key]["activation_gate_passed"] = True

        registry["last_updated"] = datetime.now().isoformat()

        with open(REGISTRY_PATH, "w") as f:
            json.dump(registry, f, indent=2, default=str)
        logger.info(f"Registry updated: {model_key}")
    except Exception as e:
        logger.warning(f"Could not update registry: {e}")


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)

    print("\nCB6 QUANTUM -- LSTM TRADE SCORER TRAINING")
    print("Engine: NSE")
    result = train(engine="nse", epochs=150, run_walk_forward=True)

    print("\nForex LSTM model:")
    result_fx = train(engine="forex", epochs=150, run_walk_forward=True)
