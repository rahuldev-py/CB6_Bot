"""
ml_engine/features/market_features.py

Market/price structure features derived from embedded candle data
or trade-level fields in the labeled dataset.

Candle data is available for Forex rows (ec, c1, c2, c3 columns).
For NSE rows lacking candle data, features fall back to trade-level fields.
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def _candle_body(o, h, l, c) -> pd.Series:
    return (pd.to_numeric(c, errors="coerce") - pd.to_numeric(o, errors="coerce")).abs()


def _candle_range(h, l) -> pd.Series:
    return (pd.to_numeric(h, errors="coerce") - pd.to_numeric(l, errors="coerce")).abs()


def _upper_wick(h, o, c) -> pd.Series:
    hi    = pd.to_numeric(h, errors="coerce")
    body_top = pd.concat([pd.to_numeric(o, errors="coerce"),
                          pd.to_numeric(c, errors="coerce")], axis=1).max(axis=1)
    return (hi - body_top).clip(lower=0)


def _lower_wick(l, o, c) -> pd.Series:
    lo   = pd.to_numeric(l, errors="coerce")
    body_bot = pd.concat([pd.to_numeric(o, errors="coerce"),
                          pd.to_numeric(c, errors="coerce")], axis=1).min(axis=1)
    return (body_bot - lo).clip(lower=0)


def add_market_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    n   = len(out)

    # ── Entry candle features (ec = entry candle) ─────────────────────────
    has_ec = all(c in out.columns for c in ["ec_open", "ec_high", "ec_low", "ec_close"])
    if has_ec:
        ec_body  = _candle_body(out["ec_open"], out["ec_high"], out["ec_low"], out["ec_close"])
        ec_range = _candle_range(out["ec_high"], out["ec_low"])
        ec_uw    = _upper_wick(out["ec_high"], out["ec_open"], out["ec_close"])
        ec_lw    = _lower_wick(out["ec_low"],  out["ec_open"], out["ec_close"])

        out["ec_body_ratio"]  = (ec_body  / ec_range.replace(0, np.nan)).clip(0, 1)
        out["ec_uw_ratio"]    = (ec_uw    / ec_range.replace(0, np.nan)).clip(0, 1)
        out["ec_lw_ratio"]    = (ec_lw    / ec_range.replace(0, np.nan)).clip(0, 1)
        out["ec_range"]       = ec_range
        out["ec_is_bull"]     = (
            pd.to_numeric(out["ec_close"], errors="coerce") >
            pd.to_numeric(out["ec_open"],  errors="coerce")
        ).astype(float)
    else:
        for col in ["ec_body_ratio", "ec_uw_ratio", "ec_lw_ratio", "ec_range", "ec_is_bull"]:
            out[col] = np.nan

    # ── Multi-candle ATR proxy (c1, c2, c3 are pre-entry candles) ────────
    candle_ranges = []
    for tag in ["c1", "c2", "c3"]:
        h_col, l_col = f"{tag}_high", f"{tag}_low"
        if h_col in out.columns and l_col in out.columns:
            candle_ranges.append(_candle_range(out[h_col], out[l_col]))

    if candle_ranges:
        atr_proxy = pd.concat(candle_ranges, axis=1).mean(axis=1)
        out["atr_proxy"] = atr_proxy
        # Relative ATR: risk / ATR — how many ATRs wide the SL is
        risk = pd.to_numeric(out.get("risk"), errors="coerce").replace(0, np.nan) \
            if "risk" in out.columns else pd.Series(np.nan, index=out.index)
        out["risk_atr_ratio"] = risk / atr_proxy.replace(0, np.nan)
    else:
        out["atr_proxy"]     = np.nan
        out["risk_atr_ratio"] = np.nan

    # ── Candle momentum: c3→ec direction ─────────────────────────────────
    if "c3_close" in out.columns and "ec_close" in out.columns:
        c3c = pd.to_numeric(out["c3_close"], errors="coerce")
        ecc = pd.to_numeric(out["ec_close"], errors="coerce")
        out["momentum_3c"] = (ecc - c3c) / (c3c.replace(0, np.nan))
    else:
        out["momentum_3c"] = np.nan

    # ── Market regime ─────────────────────────────────────────────────────
    if "regime_ord" not in out.columns:
        rmap = {"CHOPPY": 0, "NEUTRAL": 1, "TRENDING": 2}
        src = next((c for c in ["market_regime", "regime"] if c in out.columns), None)
        if src:
            out["regime_ord"] = out[src].map(rmap).fillna(1).astype(float)
        else:
            out["regime_ord"] = 1.0

    # ── RR ratio ─────────────────────────────────────────────────────────
    if "rr_ratio" not in out.columns or out["rr_ratio"].isna().all():
        entry = pd.to_numeric(out.get("entry", out.get("entry_price")), errors="coerce")
        t2    = pd.to_numeric(out.get("target2"), errors="coerce")
        risk  = pd.to_numeric(out.get("risk"), errors="coerce").replace(0, np.nan)
        out["rr_ratio"] = ((t2 - entry).abs() / risk).round(2)
    else:
        out["rr_ratio"] = pd.to_numeric(out["rr_ratio"], errors="coerce")

    # ── Greeks (NSE options rows) ──────────────────────────────────────────
    for col in ["delta", "gamma", "theta", "vega"]:
        if col in out.columns:
            out[f"opt_{col}"] = pd.to_numeric(out[col], errors="coerce")
        else:
            out[f"opt_{col}"] = np.nan

    # IV as float (strip % if present)
    if "iv" in out.columns:
        out["opt_iv"] = pd.to_numeric(
            out["iv"].astype(str).str.replace("%", "", regex=False), errors="coerce"
        ) / 100.0
    else:
        out["opt_iv"] = np.nan

    return out


MARKET_FEATURE_COLS = [
    "ec_body_ratio", "ec_uw_ratio", "ec_lw_ratio", "ec_range", "ec_is_bull",
    "atr_proxy", "risk_atr_ratio", "momentum_3c",
    "regime_ord", "rr_ratio",
    "opt_delta", "opt_gamma", "opt_theta", "opt_iv",
]
