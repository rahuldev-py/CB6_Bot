"""
ml_engine/training/trade_history_loader.py

Loads closed trade history directly from CB6 paper state JSON files.
Supports:
  - data/paper_state.json          (NSE paper trades)
  - data/forex_paper_state.json    (Forex paper trades)
  - data/gft_paper_state_1.json    (GFT instance 1)

READ-ONLY. No writes. No live hooks. No execution imports.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("cb6.ml.trade_history_loader")

_STATE_FILES = {
    "nse"   : "data/paper_state.json",
    "forex" : "data/forex_paper_state.json",
    "gft1"  : "data/gft_paper_state_1.json",
}


def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        logger.warning(f"State file not found: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read {path}: {e}")
        return None


def _closed_trades_to_df(state: dict, engine: str) -> pd.DataFrame:
    trades = state.get("closed_trades", [])
    if not trades:
        logger.info(f"No closed trades in {engine} state")
        return pd.DataFrame()

    rows = []
    for t in trades:
        rows.append({
            "trade_id"        : t.get("id"),
            "engine"          : engine,
            "symbol"          : t.get("symbol"),
            "direction"       : t.get("direction"),
            "instrument_type" : t.get("instrument_type"),
            "timeframe"       : t.get("timeframe"),
            "entry_time"      : t.get("entry_time"),
            "exit_time"       : t.get("exit_time"),
            "entry_price"     : t.get("entry_price"),
            "exit_price"      : t.get("exit_price"),
            "stop_loss"       : t.get("stop_loss"),
            "target1"         : t.get("target1"),
            "target2"         : t.get("target2"),
            "target3"         : t.get("target3"),
            "quantity"        : t.get("quantity"),
            "risk"            : t.get("risk"),
            "rr_ratio"        : t.get("rr_ratio"),
            "confluence"      : t.get("confluence"),
            "mss_type"        : t.get("mss_type"),
            "in_fvg"          : t.get("in_fvg"),
            "fvg_low"         : t.get("fvg_low"),
            "fvg_high"        : t.get("fvg_high"),
            "ob_present"      : t.get("ob_present"),
            "regime"          : t.get("regime"),
            "dte"             : t.get("dte"),
            "targets_hit"     : t.get("targets_hit", []),
            "exit_reason"     : t.get("exit_reason"),
            "realized_pnl"    : t.get("realized_pnl") or t.get("pnl_usd") or t.get("pnl"),
            "r_multiple"      : t.get("r_multiple"),
            # Forex-specific
            "lots"            : t.get("lots"),
            "ticket"          : t.get("ticket"),
        })

    df = pd.DataFrame(rows)
    df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")
    df["exit_time"]  = pd.to_datetime(df["exit_time"],  errors="coerce")

    if "realized_pnl" in df.columns:
        df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce")

    if "r_multiple" not in df.columns or df["r_multiple"].isna().all():
        entry = pd.to_numeric(df.get("entry_price"), errors="coerce")
        exit_ = pd.to_numeric(df.get("exit_price"),  errors="coerce")
        sl    = pd.to_numeric(df.get("stop_loss"),   errors="coerce")
        risk  = pd.to_numeric(df.get("risk"),        errors="coerce")
        # Derive risk from SL distance where explicit risk field is missing
        risk_derived = (entry - sl).abs().where(risk.isna(), risk)
        with_risk = risk_derived.replace(0, float("nan"))
        pnl_pts = exit_ - entry
        if "direction" in df.columns:
            bearish_mask = df["direction"].str.upper().isin(["BEARISH", "SELL", "SHORT"])
            pnl_pts = pnl_pts.where(~bearish_mask, entry - exit_)
        df["r_multiple"] = pnl_pts / with_risk

    df["win"] = df["r_multiple"].gt(0)

    logger.info(
        f"[{engine}] {len(df)} closed trades loaded | "
        f"win_rate={df['win'].mean():.1%} | "
        f"avg_r={df['r_multiple'].mean():.2f}"
    )
    return df


def load_trade_history(engine: str = "all", base_path: str = "") -> pd.DataFrame:
    """
    Load closed trade history from paper state JSON files.

    Parameters
    ----------
    engine    : 'nse' | 'forex' | 'gft1' | 'all'
    base_path : root directory prefix (e.g. '../../' when running from ml_engine/)

    Returns
    -------
    DataFrame of closed trades with standardised columns.
    """
    engines_to_load = list(_STATE_FILES.keys()) if engine == "all" else [engine]
    frames = []

    for eng in engines_to_load:
        if eng not in _STATE_FILES:
            logger.warning(f"Unknown engine key: {eng}")
            continue
        path = Path(base_path) / _STATE_FILES[eng]
        state = _load_json(path)
        if state is None:
            continue
        df = _closed_trades_to_df(state, eng)
        if not df.empty:
            frames.append(df)

    if not frames:
        logger.warning("No closed trade history found in any state file")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined = combined.sort_values("entry_time", na_position="last").reset_index(drop=True)
    logger.info(f"Total closed trades across all engines: {len(combined)}")
    return combined


def get_open_trades(engine: str = "nse", base_path: str = "") -> pd.DataFrame:
    """
    Load open trades for validation/monitoring purposes only.
    Never used for training — no outcomes known yet.
    """
    key = engine if engine in _STATE_FILES else "nse"
    path = Path(base_path) / _STATE_FILES[key]
    state = _load_json(path)
    if state is None:
        return pd.DataFrame()

    trades = state.get("open_trades", [])
    if not trades:
        return pd.DataFrame()

    df = pd.DataFrame(trades)
    df["engine"] = engine
    logger.info(f"[{engine}] {len(df)} open trades (validation only — no outcome labels)")
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = load_trade_history(engine="all", base_path="../../")
    print(f"\nTotal closed trades: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    if not df.empty:
        print(f"Win rate: {df['win'].mean():.1%}")
        print(f"Avg R: {df['r_multiple'].mean():.2f}")
        print(df[["trade_id","engine","symbol","direction","r_multiple","win"]].head(5).to_string())
