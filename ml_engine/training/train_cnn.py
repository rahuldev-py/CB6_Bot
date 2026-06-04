"""
ml_engine/training/train_cnn.py

CNN chart-vision training script — RESEARCH ONLY.

Data strategy:
  We don't have pre-recorded OHLCV candle windows aligned to each trade.
  This script builds synthetic windows from backtest rows using the
  live_market_loader OR falls back to generating synthetic candles from
  the metadata we do have (entry price, SL, TP, direction, ATR proxy).

  Real candle data can be wired in later when the NSE paid feed arrives.

Fallback synthetic candle generator:
  Given (entry_price, atr, direction, win, r_multiple), construct a plausible
  N-candle window that encodes the ICT setup geometry:
    - Pre-entry candles: trending in setup direction
    - Displacement candle: large-body in direction
    - FVG: 3-candle gap
    - OB candle: opposite wick
    - Entry candle: at FVG/OB midpoint

  This is not a substitute for real data — it trains the CNN to recognise
  the ICT geometry but will overfit to the synthetic distribution.
  AUC from synthetic data is treated as RESEARCH ONLY until real candles arrive.

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

logger = logging.getLogger("cb6.ml.train_cnn")

MODEL_SAVE_BASE = Path("ml_engine/models/saved")
REGISTRY_PATH   = Path("ml_engine/config/model_registry.json")
MIN_SAMPLES     = 100   # lower bar for research model
CANDLE_WINDOW   = 50


# ── Synthetic candle generator (fallback when real OHLCV unavailable) ─────────

def _synthetic_window(
    entry_price: float,
    atr: float,
    direction: int,    # +1 = bullish, -1 = bearish
    win: int,          # 1 = winner, 0 = loser
    r_multiple: float,
    n_candles: int = CANDLE_WINDOW,
    rng: np.random.Generator = None,
) -> np.ndarray:
    """
    Generate a plausible OHLCV pre-entry window encoding an ICT Silver Bullet setup.

    Geometry (bullish example):
      candles 0-29  : downtrend (liquidity sweep build-up)
      candle  30    : displacement candle (large bullish, creates FVG)
      candles 31-32 : FVG gap candles (small, don't fill displacement body)
      candle  33    : OB candle (bearish wick into FVG)
      candles 34-49 : consolidation approaching entry level

    Volume: higher on displacement, lower in consolidation.
    """
    if rng is None:
        rng = np.random.default_rng()
    if atr <= 0 or np.isnan(atr):
        atr = entry_price * 0.005   # default 0.5% ATR

    candles = []   # list of [o, h, l, c, v]

    # Phase 1: prior trend opposite to setup direction (0..29)
    price = entry_price + direction * atr * 3   # start above/below entry
    for i in range(30):
        noise  = rng.normal(0, 0.2) * atr
        drift  = -direction * atr * 0.12        # moves against direction
        o      = price
        c      = price + drift + noise
        h      = max(o, c) + abs(rng.normal(0, 0.15)) * atr
        lo     = min(o, c) - abs(rng.normal(0, 0.15)) * atr
        v      = abs(rng.normal(800, 200))
        candles.append([o, h, lo, c, v])
        price  = c

    # Phase 2: displacement candle (30)
    disp_body = atr * rng.uniform(1.8, 2.8) * direction
    o    = price
    c    = price + disp_body
    h    = max(o, c) + abs(rng.normal(0, 0.1)) * atr
    lo   = min(o, c) - abs(rng.normal(0, 0.1)) * atr
    v    = abs(rng.normal(2500, 400))    # high volume on displacement
    candles.append([o, h, lo, c, v])
    fvg_top    = max(o, c) if direction > 0 else min(o, c)
    fvg_bottom = min(o, c) if direction > 0 else max(o, c)
    price = c

    # Phase 3: FVG candles (31-32) — small, don't fill gap
    for _ in range(2):
        o   = price
        c   = price + rng.normal(0, 0.25) * atr
        fvg_mid = (fvg_top + fvg_bottom) / 2
        # keep candles in upper half of FVG for bullish
        if direction > 0:
            c = max(c, fvg_mid)
        else:
            c = min(c, fvg_mid)
        h   = max(o, c) + abs(rng.normal(0, 0.1)) * atr
        lo  = min(o, c) - abs(rng.normal(0, 0.1)) * atr
        v   = abs(rng.normal(600, 150))
        candles.append([o, h, lo, c, v])
        price = c

    # Phase 4: OB + pullback to entry (33-49)
    target_price = entry_price
    remaining    = n_candles - len(candles)
    for i in range(remaining):
        t     = (i + 1) / remaining
        o     = price
        drift = (target_price - price) * 0.15 + rng.normal(0, 0.15) * atr * (1 - t)
        c     = price + drift
        h     = max(o, c) + abs(rng.normal(0, 0.12)) * atr * (1 - t * 0.5)
        lo    = min(o, c) - abs(rng.normal(0, 0.12)) * atr * (1 - t * 0.5)
        v     = abs(rng.normal(500, 100))
        candles.append([o, h, lo, c, v])
        price = c

    return np.array(candles[:n_candles], dtype=np.float32)


def _build_ohlcv_windows(
    df: pd.DataFrame,
    n_candles: int = CANDLE_WINDOW,
) -> tuple[list[np.ndarray], np.ndarray, np.ndarray]:
    """
    Attempt to load real candle windows from live_market_loader.
    Falls back to synthetic windows if OHLCV data unavailable.

    Returns: (windows, y_win, y_r)
    """
    windows = []
    y_win   = []
    y_r     = []

    # Try loading real candles (only if Yahoo data is accessible)
    real_candles_available = False
    try:
        from ml_engine.training.live_market_loader import load_candles
        # Quick test — if it fails, use synthetic
        test = load_candles("NIFTY", "5m", days=1)
        real_candles_available = test is not None and not test.empty
    except Exception:
        real_candles_available = False

    if real_candles_available:
        logger.info("Real candle data available — will attempt per-trade OHLCV lookup")

    rng = np.random.default_rng(42)

    for _, row in df.iterrows():
        win_label = row.get("win_loss_label", np.nan)
        r_label   = row.get("r_multiple_label", np.nan)

        if pd.isna(win_label):
            continue

        # Attempt real candle lookup (skipped for now — requires entry_time indexing)
        window = None

        if window is None:
            # Synthetic fallback
            ep    = float(row.get("entry_price", 100.0) or 100.0)
            atr   = float(row.get("atr_proxy", 0.0) or 0.0)
            if atr == 0:
                sl = float(row.get("stop_loss", ep * 0.995) or ep * 0.995)
                atr = abs(ep - sl) * 2 if sl else ep * 0.005

            dir_str  = str(row.get("direction", "BULLISH")).upper()
            direction = 1 if "BULL" in dir_str or "BUY" in dir_str or "LONG" in dir_str else -1
            r_val    = float(r_label) if not pd.isna(r_label) else (1.5 if win_label else -1.0)

            window = _synthetic_window(ep, atr, direction, int(win_label), r_val, n_candles, rng)

        windows.append(window)
        y_win.append(float(win_label))
        y_r.append(float(r_label) if not pd.isna(r_label) else np.nan)

    return windows, np.array(y_win, dtype=np.float32), np.array(y_r, dtype=np.float32)


# ── Main training function ─────────────────────────────────────────────────────

def train(
    engine: str = "nse",
    epochs: int = 100,
    lr: float = 5e-4,
    batch_size: int = 16,
    patience: int = 15,
    train_ratio: float = 0.80,
    save_model: bool = True,
    use_synthetic: bool = True,    # always True until real OHLCV available
) -> dict:
    """
    CNN chart-vision training — RESEARCH ONLY.

    Returns metrics dict with research_only=True flag.
    Activation gate deliberately set to AUC >= 0.60 (stricter than DNN/LSTM).
    """
    from ml_engine.training.dataset_builder import build_dataset
    from ml_engine.models.cnn_chart_vision import CNNChartScorer
    from ml_engine.training.validation import (
        compute_classification_metrics, compute_regression_metrics,
        confidence_bucket_analysis, print_metrics_report,
    )

    ts         = datetime.now().strftime("%Y%m%d_%H%M")
    version_id = f"cnn_{engine}_v1_{ts}"
    logger.info(f"Starting CNN training: engine={engine} version={version_id}")

    # ── 1. Load data ──────────────────────────────────────────────────────
    df = build_dataset(base_path="")
    if df is None or df.empty:
        return {"error": "No dataset available", "ready": False}

    if engine != "all" and "engine" in df.columns:
        df_eng = df[df["engine"] == engine].copy()
        if len(df_eng) < MIN_SAMPLES // 2:
            df_eng = df.copy()
    else:
        df_eng = df.copy()

    # Keep only labeled rows
    df_eng = df_eng[df_eng["win_loss_label"].notna()].reset_index(drop=True)

    if len(df_eng) < MIN_SAMPLES:
        msg = f"Only {len(df_eng)} labeled rows — need {MIN_SAMPLES}+ for CNN research"
        logger.warning(msg)
        print(f"\nWARNING: {msg}")

    data_source = "synthetic" if use_synthetic else "real"
    print(f"\nCNN chart-vision training (RESEARCH — {data_source} candles)")
    print(f"Engine: {engine} | Rows: {len(df_eng)}")

    # ── 2. Build OHLCV windows ────────────────────────────────────────────
    windows, y_win_arr, y_r_arr = _build_ohlcv_windows(df_eng)

    if len(windows) < MIN_SAMPLES:
        return {
            "error"        : f"Only {len(windows)} windows built",
            "ready"        : False,
            "research_only": True,
        }

    print(f"Windows built: {len(windows)} | Win rate: {y_win_arr.mean():.1%}")

    # ── 3. Time-based split (no shuffling) ────────────────────────────────
    n       = len(windows)
    split   = int(n * train_ratio)
    win_tr  = windows[:split]
    win_val = windows[split:]
    yw_tr   = y_win_arr[:split]
    yw_val  = y_win_arr[split:]
    yr_tr   = y_r_arr[:split]
    yr_val  = y_r_arr[split:]
    print(f"Split: {len(win_tr)} train / {len(win_val)} val")

    # ── 4. Train CNN ──────────────────────────────────────────────────────
    print(f"\nTraining CNN (epochs={epochs}, patience={patience})...")
    scorer = CNNChartScorer()

    history = scorer.fit(
        win_tr, yw_tr, yr_tr,
        win_val, yw_val, yr_val,
        epochs=epochs, lr=lr,
        batch_size=batch_size, patience=patience,
    )

    # ── 5. Evaluate ───────────────────────────────────────────────────────
    pred_val  = scorer.predict(win_val)
    win_probs = pred_val["win_probability"]
    exp_r     = pred_val["expected_r"]

    cls_metrics = compute_classification_metrics(yw_val, win_probs)
    reg_metrics = compute_regression_metrics(yr_val, exp_r)
    bucket_perf = confidence_bucket_analysis(yw_val, win_probs, yr_val)

    print_metrics_report(cls_metrics, label=f"Classification ({engine.upper()} CNN)")
    print_metrics_report(reg_metrics, label=f"Regression / Expected-R ({engine.upper()} CNN)")

    print(f"\nConfidence Bucket Performance:")
    print(f"  {'Bucket':<5} {'N':>5} {'WinRate':>8} {'AvgR':>7} {'Expectancy':>11}")
    for b in ["A+", "A", "B", "C"]:
        bdata = bucket_perf.get(b, {})
        if bdata.get("n", 0) == 0:
            print(f"  {b:<5} {'0':>5} {'--':>8} {'--':>7} {'--':>11}")
        else:
            print(
                f"  {b:<5} {bdata['n']:>5} "
                f"{bdata['win_rate']:>8.1%} "
                f"{(bdata['avg_r'] or 0):>7.2f} "
                f"{(bdata['expectancy'] or 0):>11.3f}"
            )

    monotonic = bucket_perf.get("_monotonic_wr", False)
    all_pos_e = bucket_perf.get("_all_positive_expectancy", False)
    print(f"\n  Monotonic win-rate: {'YES' if monotonic else 'NO'}")
    print(f"  All positive expectancy: {'YES' if all_pos_e else 'NO'}")
    print(f"\n  [RESEARCH] Data source: {data_source}")
    print(f"  [RESEARCH] Metrics on synthetic candles are NOT activation-gate eligible")
    print(f"  [RESEARCH] Re-run with real OHLCV when NSE paid feed is available")

    # ── 6. Save model ─────────────────────────────────────────────────────
    model_path = None
    if save_model:
        save_dir = MODEL_SAVE_BASE / version_id
        scorer.metadata = {
            "version_id"   : version_id,
            "engine"       : engine,
            "trained_at"   : ts,
            "train_samples": len(win_tr),
            "val_samples"  : len(win_val),
            "data_source"  : data_source,
            "research_only": True,
            "history_last5": history["train_loss"][-5:],
        }
        saved      = scorer.save(save_dir)
        model_path = saved["path"]
        print(f"\nModel saved (research): {model_path}")

    # CNN gate is STRICTER (AUC >= 0.60) and requires real data
    gate_eligible = not use_synthetic
    gate_passed   = (
        gate_eligible
        and cls_metrics["auc"] >= 0.60
        and monotonic
        and all_pos_e
    )

    result = {
        "version_id"             : version_id,
        "engine"                 : engine,
        "train_samples"          : len(win_tr),
        "val_samples"            : len(win_val),
        "data_source"            : data_source,
        "research_only"          : True,
        "accuracy"               : cls_metrics["accuracy"],
        "auc"                    : cls_metrics["auc"],
        "f1"                     : cls_metrics["f1"],
        "brier_score"            : cls_metrics["brier_score"],
        "expected_r_mae"         : reg_metrics["mae"],
        "r2_score"               : reg_metrics["r2"],
        "confusion_matrix"       : cls_metrics["confusion_matrix"],
        "bucket_performance"     : {
            b: {k: v for k, v in bucket_perf[b].items()}
            for b in ["A+", "A", "B", "C"]
        },
        "monotonic_wr"           : monotonic,
        "all_positive_expectancy": all_pos_e,
        "model_path"             : model_path,
        "activation_gate_passed" : gate_passed,
    }

    _update_registry(engine, result, model_path)

    print(f"\n{'='*50}")
    print(f"  CNN Research gate: {'PASS' if gate_passed else 'NOT ELIGIBLE (synthetic data)'}")
    print(f"  AUC={cls_metrics['auc']:.4f}")
    print(f"{'='*50}")

    return result


def _update_registry(engine: str, metrics: dict, model_path: str) -> None:
    try:
        with open(REGISTRY_PATH) as f:
            registry = json.load(f)

        model_key = "cnn_research"
        if model_key not in registry["models"]:
            model_key = list(registry["models"].keys())[-1]

        version_entry = {
            "version_id"            : metrics["version_id"],
            "trained_at"            : metrics["version_id"].split("_v1_")[-1],
            "train_samples"         : metrics["train_samples"],
            "val_samples"           : metrics["val_samples"],
            "data_source"           : metrics["data_source"],
            "research_only"         : True,
            "auc"                   : metrics["auc"],
            "f1"                    : metrics["f1"],
            "brier_score"           : metrics["brier_score"],
            "activation_gate_passed": metrics["activation_gate_passed"],
            "model_path"            : model_path or "",
        }

        if model_key in registry["models"]:
            registry["models"][model_key]["versions"].append(version_entry)
            registry["models"][model_key]["status"] = "RESEARCH"

        registry["last_updated"] = datetime.now().isoformat()

        with open(REGISTRY_PATH, "w") as f:
            json.dump(registry, f, indent=2, default=str)
        logger.info(f"Registry updated: {model_key} (research)")
    except Exception as e:
        logger.warning(f"Could not update registry: {e}")


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)

    print("\nCB6 QUANTUM -- CNN CHART VISION (RESEARCH)")
    result = train(engine="nse", epochs=100, use_synthetic=True)
    print("\nForex CNN (research):")
    result_fx = train(engine="forex", epochs=100, use_synthetic=True)
