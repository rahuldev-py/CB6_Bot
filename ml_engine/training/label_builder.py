"""
ml_engine/training/label_builder.py

Auto-labels OHLCV data and backtest rows using CB6's existing rule detectors.

Two labeling modes:
  1. label_from_existing(df)  — extracts labels already in backtest/journal CSVs
  2. label_from_ohlcv(df)     — runs CB6 detectors on each candle window

IMPORTANT:
  - Rule detectors are called READ-ONLY for labeling historical data.
  - These detectors are NOT replaced. Live scanner continues to use them unchanged.
  - No execution imports. No write-back to scanner state.
"""

from __future__ import annotations

import logging
import sys
import os
from typing import Optional

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logger = logging.getLogger("cb6.ml.label_builder")

# ── Minimum candle window required to run detectors ──────────────────────────
MIN_CANDLES = 40

# ── Label schema — all output column names ───────────────────────────────────
LABEL_COLUMNS = [
    "market_regime",          # TRENDING / NEUTRAL / CHOPPY
    "liquidity_sweep",        # bool — sweep detected
    "sweep_type",             # BSL / SSL / None
    "sweep_depth_pct",        # float — how deep was the sweep
    "sweep_candles_ago",      # int
    "fvg_present",            # bool
    "fvg_quality",            # STRONG / WEAK / NONE
    "fvg_size",               # float
    "fvg_displacement",       # bool
    "fvg_body_ratio",         # float
    "order_block_present",    # bool
    "ob_type",                # BULL_OB / BEAR_OB / None
    "mss_confirmed",          # bool
    "mss_type",               # CHOCH / BOS / None
    "mss_candles_ago",        # int
    "choch_confirmed",        # bool
    "bos_confirmed",          # bool
    "direction",              # BULLISH / BEARISH (from MSS)
    "win_loss_label",         # 1 = win, 0 = loss, NaN = unknown
    "r_multiple_label",       # float R achieved, NaN = unknown
    "trade_grade",            # A+ / A / B / C — based on score + R
]


# ── Mode 1: Extract labels from existing backtest / journal columns ───────────

_DIRECTION_MAP = {
    "buy": "BULLISH", "bullish": "BULLISH", "long": "BULLISH",
    "sell": "BEARISH", "bearish": "BEARISH", "short": "BEARISH",
}

_REGIME_MAP = {
    "trending": "TRENDING", "neutral": "NEUTRAL",
    "choppy": "CHOPPY", "ranging": "NEUTRAL",
}


def _norm_direction(val) -> Optional[str]:
    if pd.isna(val):
        return None
    return _DIRECTION_MAP.get(str(val).lower().strip())


def _norm_regime(val) -> Optional[str]:
    if pd.isna(val):
        return None
    return _REGIME_MAP.get(str(val).lower().strip())


def _parse_bool(val) -> Optional[bool]:
    if pd.isna(val):
        return None
    if isinstance(val, bool):
        return val
    s = str(val).lower().strip()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def _fvg_quality(fvg_present, fvg_displacement, fvg_size, atr) -> str:
    if not fvg_present:
        return "NONE"
    size_ratio = (fvg_size / atr) if (atr and atr > 0) else 0
    if fvg_displacement and size_ratio >= 0.3:
        return "STRONG"
    return "WEAK"


def _trade_grade(r_multiple, confluence, mss_type) -> str:
    if pd.isna(r_multiple):
        return "UNKNOWN"
    r = float(r_multiple)
    score = int(confluence) if not pd.isna(confluence) else 0
    choch = str(mss_type).upper() == "CHOCH" if not pd.isna(mss_type) else False
    if r >= 3.0 and score >= 12 and choch:
        return "A+"
    if r >= 2.0 and score >= 10:
        return "A"
    if r >= 1.0:
        return "B"
    return "C"


def label_from_existing(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map backtest/journal columns to the standard label schema.
    Rows without candle data are labeled from what's available.
    Unknown labels are left as NaN (handled by data_validator).

    Does NOT require OHLCV candle history — works on CSV rows directly.
    """
    out = df.copy()

    # direction
    if "direction" in out.columns:
        out["direction"] = out["direction"].apply(_norm_direction)

    # market_regime
    for src in ["regime", "market_regime"]:
        if src in out.columns:
            out["market_regime"] = out[src].apply(_norm_regime)
            break

    # liquidity_sweep — no source column in most CSVs, leave NaN
    if "liquidity_sweep" not in out.columns:
        out["liquidity_sweep"] = np.nan

    # sweep_type
    if "sweep_type" not in out.columns:
        out["sweep_type"] = np.nan

    # fvg_present
    for src in ["fvg_present", "in_fvg", "price_in_fvg"]:
        if src in out.columns:
            out["fvg_present"] = out[src].apply(_parse_bool)
            break

    # fvg_displacement
    for src in ["fvg_displacement", "displacement"]:
        if src in out.columns:
            out["fvg_displacement"] = out[src].apply(_parse_bool)
            break

    # fvg_size
    for src in ["fvg_size", "risk_pts", "risk"]:
        if src in out.columns:
            out["fvg_size"] = pd.to_numeric(out[src], errors="coerce")
            break

    # fvg_quality — derive if we have fvg_present + displacement + size
    if all(c in out.columns for c in ["fvg_present", "fvg_displacement", "fvg_size"]):
        atr_median = out["fvg_size"].median() if "fvg_size" in out.columns else 10.0
        out["fvg_quality"] = out.apply(
            lambda r: _fvg_quality(
                bool(r.get("fvg_present")),
                bool(r.get("fvg_displacement")),
                float(r.get("fvg_size", 0) or 0),
                atr_median,
            ),
            axis=1,
        )
    else:
        out["fvg_quality"] = np.nan

    # order_block_present
    for src in ["ob_present", "order_block_present", "ob_confluence"]:
        if src in out.columns:
            if src == "ob_confluence":
                out["order_block_present"] = out[src].apply(
                    lambda v: bool(v) if not pd.isna(v) else False
                )
            else:
                out["order_block_present"] = out[src].apply(_parse_bool)
            break

    # mss_type, mss_confirmed, choch_confirmed, bos_confirmed
    for src in ["mss_type"]:
        if src in out.columns:
            out["mss_type"] = out[src].str.upper().where(out[src].notna())
            out["mss_confirmed"]  = out["mss_type"].notna()
            out["choch_confirmed"] = out["mss_type"].str.upper().eq("CHOCH")
            out["bos_confirmed"]   = out["mss_type"].str.upper().eq("BOS")
            break

    # win_loss_label
    for src in ["win", "is_win"]:
        if src in out.columns:
            out["win_loss_label"] = out[src].apply(
                lambda v: 1 if _parse_bool(v) else (0 if _parse_bool(v) is False else np.nan)
            )
            break

    # r_multiple_label
    for src in ["r_multiple", "r_mult"]:
        if src in out.columns:
            out["r_multiple_label"] = pd.to_numeric(out[src], errors="coerce")
            break

    # trade_grade
    out["trade_grade"] = out.apply(
        lambda r: _trade_grade(
            r.get("r_multiple_label"),
            r.get("confluence") or r.get("score"),
            r.get("mss_type"),
        ),
        axis=1,
    )

    return out


# ── Mode 2: Run CB6 detectors on OHLCV windows ───────────────────────────────

def _safe_call(fn, *args, **kwargs):
    """Call a CB6 detector safely — returns None on any error."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.debug(f"Detector call failed: {fn.__name__} — {e}")
        return None


def label_window(df_window: pd.DataFrame) -> dict:
    """
    Run all CB6 detectors on one OHLCV window (>= MIN_CANDLES rows).
    Returns a flat dict of labels for the last candle in the window.
    Called once per candle when building a rolling-window labeled dataset.
    """
    from scanner.silver_bullet import (
        market_regime,
        detect_liquidity_sweep,
        detect_sb_mss,
        detect_sb_fvg,
        detect_order_block,
    )

    labels: dict = {col: np.nan for col in LABEL_COLUMNS}

    if len(df_window) < MIN_CANDLES:
        return labels

    # Market regime
    regime = _safe_call(market_regime, df_window)
    labels["market_regime"] = regime

    # Liquidity sweep
    sweep = _safe_call(detect_liquidity_sweep, df_window, lookback=60, sweep_window=20)
    if sweep:
        labels["liquidity_sweep"]   = True
        labels["sweep_type"]        = sweep.get("sweep_type")
        labels["sweep_candles_ago"] = sweep.get("candles_ago")
        swept = sweep.get("swept_level", 0)
        last  = float(df_window["close"].iloc[-1])
        labels["sweep_depth_pct"]   = abs(last - swept) / (swept + 1e-9)
    else:
        labels["liquidity_sweep"] = False
        labels["sweep_type"]      = None

    # MSS
    mss = _safe_call(detect_sb_mss, df_window, lookback=30)
    if mss:
        labels["mss_confirmed"]   = True
        labels["mss_type"]        = mss.get("type", "BOS")
        labels["mss_candles_ago"] = mss.get("candles_ago")
        labels["direction"]       = mss.get("direction")
        labels["choch_confirmed"] = mss.get("type") == "CHOCH"
        labels["bos_confirmed"]   = mss.get("type") == "BOS"
        direction = mss.get("direction", "BULLISH")
    else:
        labels["mss_confirmed"] = False
        labels["mss_type"]      = None
        direction = "BULLISH"  # fallback for FVG/OB scan

    # FVG
    fvg = _safe_call(detect_sb_fvg, df_window, direction, lookback=25, displacement_mult=1.0, use_range=False)
    if fvg:
        labels["fvg_present"]     = True
        labels["fvg_size"]        = fvg.get("size", 0)
        labels["fvg_displacement"] = bool(fvg.get("displacement"))
        labels["fvg_body_ratio"]  = fvg.get("body_ratio", 0)
        atr_median = float((df_window["high"] - df_window["low"]).median())
        size = fvg.get("size", 0) or 0
        labels["fvg_quality"] = _fvg_quality(True, bool(fvg.get("displacement")), size, atr_median)
    else:
        labels["fvg_present"] = False
        labels["fvg_quality"] = "NONE"

    # Order block
    ob = _safe_call(detect_order_block, df_window, direction, lookback=40)
    if ob:
        labels["order_block_present"] = True
        labels["ob_type"] = ob.get("type")
    else:
        labels["order_block_present"] = False
        labels["ob_type"] = None

    return labels


def label_from_ohlcv(
    df_ohlcv: pd.DataFrame,
    window: int = 60,
    step: int = 1,
) -> pd.DataFrame:
    """
    Slide a window over OHLCV data and label each position.
    Returns a DataFrame aligned to df_ohlcv.index with one label row per candle.

    Parameters
    ----------
    df_ohlcv : OHLCV DataFrame with columns [open, high, low, close, volume].
    window   : Lookback window size in candles.
    step     : Step between windows (1 = label every candle, 5 = every 5th).

    Returns
    -------
    DataFrame of labels, same length as df_ohlcv.
    """
    n = len(df_ohlcv)
    if n < window:
        logger.warning(f"OHLCV too short ({n} < {window}) — no labels generated")
        return pd.DataFrame()

    rows = []
    indices = []

    for end in range(window, n + 1, step):
        window_df = df_ohlcv.iloc[end - window: end].reset_index(drop=True)
        lbl = label_window(window_df)
        lbl["candle_idx"] = end - 1
        rows.append(lbl)
        indices.append(df_ohlcv.index[end - 1] if hasattr(df_ohlcv.index, '__len__') else end - 1)

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows, index=indices)
    logger.info(
        f"label_from_ohlcv: {len(result)} windows labeled | "
        f"mss={result['mss_confirmed'].sum()} "
        f"fvg={result['fvg_present'].sum()} "
        f"sweep={result['liquidity_sweep'].sum()}"
    )
    return result


def compute_win_loss_from_ohlcv(
    entry_price: float,
    direction: str,
    stop_loss: float,
    target2: float,
    df_future: pd.DataFrame,
    max_bars: int = 20,
) -> dict:
    """
    Forward-looking outcome computation for labeled candle windows.
    Simulates whether price hits T2 (2R) or SL within max_bars candles.

    LEAKAGE GUARD: df_future must be strictly AFTER the entry candle.
    The caller is responsible for passing only future candles.

    Returns {'win': bool, 'r_multiple': float, 'bars_to_exit': int}
    """
    if df_future is None or df_future.empty:
        return {"win": None, "r_multiple": None, "bars_to_exit": None}

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return {"win": None, "r_multiple": None, "bars_to_exit": None}

    future = df_future.head(max_bars)
    is_bull = direction.upper() in ("BULLISH", "BUY", "LONG")

    for i, (_, row) in enumerate(future.iterrows()):
        hi = float(row["high"])
        lo = float(row["low"])

        if is_bull:
            if lo <= stop_loss:
                return {"win": False, "r_multiple": -1.0, "bars_to_exit": i + 1}
            if hi >= target2:
                r = (target2 - entry_price) / risk
                return {"win": True, "r_multiple": round(r, 2), "bars_to_exit": i + 1}
        else:
            if hi >= stop_loss:
                return {"win": False, "r_multiple": -1.0, "bars_to_exit": i + 1}
            if lo <= target2:
                r = (entry_price - target2) / risk
                return {"win": True, "r_multiple": round(r, 2), "bars_to_exit": i + 1}

    # Max bars reached — compute unrealized R at last close
    last_close = float(future["close"].iloc[-1])
    r = (last_close - entry_price) / risk if is_bull else (entry_price - last_close) / risk
    return {"win": r > 0, "r_multiple": round(r, 2), "bars_to_exit": max_bars}
