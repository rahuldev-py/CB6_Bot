"""
ml_engine/features/execution_features.py

Execution quality features: spread, slippage, risk sizing.
Most are available in the Forex journal; NSE rows may have NaN.
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def add_execution_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    entry = pd.to_numeric(out.get("entry", out.get("entry_price")), errors="coerce")
    risk  = pd.to_numeric(out.get("risk",  out.get("risk_price")),  errors="coerce").replace(0, np.nan)

    # Risk as % of entry price
    out["risk_pct"] = (risk / entry.replace(0, np.nan)).clip(0, 0.1)

    # RR ratio (already computed in market_features, keep here as backup)
    if "rr_ratio" not in out.columns or out["rr_ratio"].isna().all():
        t2 = pd.to_numeric(out.get("target2"), errors="coerce")
        out["rr_ratio"] = ((t2 - entry).abs() / risk).round(2)
    else:
        out["rr_ratio"] = pd.to_numeric(out["rr_ratio"], errors="coerce")

    # Forex-specific: lots, leverage, margin
    if "lots" in out.columns:
        out["lots_num"] = pd.to_numeric(out["lots"], errors="coerce")
    else:
        out["lots_num"] = np.nan

    if "leverage" in out.columns:
        out["leverage_num"] = pd.to_numeric(out["leverage"], errors="coerce")
    else:
        out["leverage_num"] = np.nan

    # Risk in account currency (Forex rows)
    for src in ["risk_usd", "risk_price"]:
        if src in out.columns:
            out["risk_usd_num"] = pd.to_numeric(out[src], errors="coerce")
            break
    else:
        out["risk_usd_num"] = np.nan

    return out


EXECUTION_FEATURE_COLS = [
    "risk_pct", "rr_ratio", "lots_num", "leverage_num", "risk_usd_num",
]
