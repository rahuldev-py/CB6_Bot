"""
ml_engine/features/macro_regime_features.py

Enriches the ML training dataset with 20-year macro context features.

For each trade row, looks up the daily NIFTY bar at the trade date and
stamps it with:
  — 20yr structural regime (STRONG_BULL → STRONG_BEAR)
  — RBI monetary stance (easing / tightening / ultra_accommodative / pivot)
  — Active macro shock (GFC, COVID, RUSSIA_UKRAINE, etc.)
  — Election week flag
  — Distance from SMA200, ATR percentile, 1Y/3M return at trade time

These features let the DNN/CNN/RNN learn that the SAME ICT setup
has materially different win rates in different macro environments.

Example signal the model will learn:
  CHoCH + FVG LONG in ultra_accommodative + no shock  → WR ~60%
  CHoCH + FVG LONG in tightening + GFC               → WR ~37%
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("cb6.ml.macro_regime_features")

# ── Encodings ──────────────────────────────────────────────────────────────────

_REGIME_ORD = {
    "STRONG_BULL": 2, "WEAK_BULL": 1, "SIDEWAYS": 0,
    "WEAK_BEAR": -1, "STRONG_BEAR": -2, "UNKNOWN": 0,
}

# Higher = tighter money (bearish for growth assets)
_RBI_ORD = {
    "ultra_accommodative": 0,
    "easing":              1,
    "neutral":             2,
    "pivot":               3,
    "tightening":          4,
    "unknown":             2,   # neutral fallback
}

_SHOCK_BIAS_BIN = {"BEARISH": 1, "BULLISH": 0, "NEUTRAL": 0}


# ── Cached enriched daily lookup table ────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_daily_lookup() -> pd.DataFrame | None:
    """
    Returns an enriched daily DataFrame indexed by date (date only, no time).
    Cached after first load.  Returns None if data not available.
    """
    try:
        from utils.nse_historical_loader import load_nifty_daily
        from utils.nse_event_overlay import enrich

        df = load_nifty_daily()
        if df.empty:
            return None

        df = enrich(df)

        # Rolling SMA / ATR for each date — computed once here
        c = df["close"]
        h = df["high"]
        l = df["low"]

        df["_sma50"]  = c.rolling(50).mean()
        df["_sma200"] = c.rolling(200).mean()

        tr = pd.concat([
            h - l,
            (h - c.shift(1)).abs(),
            (l - c.shift(1)).abs(),
        ], axis=1).max(axis=1)
        df["_atr14"] = tr.rolling(14).mean()

        # ATR percentile rank (252-day rolling)
        def _pct_rank(s: pd.Series, window: int = 252) -> pd.Series:
            return s.rolling(window).apply(
                lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100,
                raw=False,
            )
        df["_atr_pct"] = _pct_rank(df["_atr14"])

        # Returns
        df["_ret_1y"] = c.pct_change(252) * 100
        df["_ret_3m"] = c.pct_change(63)  * 100

        # SMA200 distance
        df["_sma200_dist_pct"] = (c - df["_sma200"]) / df["_sma200"] * 100

        # Regime label from daily context (recompute inline)
        df["_regime_ord"] = df.apply(_row_regime_ord, axis=1)

        # Index by date only for fast lookup
        df.index = df["date"].dt.date
        return df

    except Exception as exc:
        logger.warning(f"macro_regime_features: daily lookup failed — {exc}")
        return None


def _row_regime_ord(row) -> float:
    """Compute regime ordinal for a single daily row."""
    c      = row.get("close", 0)
    s50    = row.get("_sma50",  np.nan)
    s200   = row.get("_sma200", np.nan)
    ret_1y = row.get("_ret_1y", np.nan)

    if pd.isna(s200):
        return 0.0
    above_200  = c > s200
    bull_cross = (not pd.isna(s50)) and (s50 > s200)
    ret        = ret_1y if not pd.isna(ret_1y) else 0.0

    if above_200 and bull_cross and ret > 15:
        return 2.0   # STRONG_BULL
    elif above_200 and (c > (s50 if not pd.isna(s50) else 0) or bull_cross):
        return 1.0   # WEAK_BULL
    elif not above_200 and not bull_cross and ret < -15:
        return -2.0  # STRONG_BEAR
    elif not above_200 or (not bull_cross):
        return -1.0  # WEAK_BEAR
    return 0.0       # SIDEWAYS


def _lookup_row(trade_date: pd.Timestamp | None, lookup: pd.DataFrame) -> dict:
    """Look up macro features for a given trade date from the daily lookup table."""
    empty = {
        "nifty_regime_ord"    : 0.0,
        "nifty_sma200_dist_pct": 0.0,
        "nifty_atr_pct_rank"  : 50.0,
        "nifty_ret_1y_pct"    : 0.0,
        "nifty_ret_3m_pct"    : 0.0,
        "rbi_stance_ord"      : 2.0,   # neutral
        "macro_shock_active"  : 0.0,
        "shock_severity"      : 0.0,
        "shock_bearish"       : 0.0,
        "is_black_swan"       : 0.0,
        "is_election_week"    : 0.0,
    }
    if trade_date is None or lookup is None or pd.isnull(trade_date):
        return empty

    try:
        d = trade_date.date() if hasattr(trade_date, "date") else trade_date
    except Exception:
        return empty

    # Use nearest past trading day if exact date missing
    idx_dates = lookup.index
    past = [x for x in idx_dates if x is not None and x <= d]
    if not past:
        return empty
    row = lookup.loc[max(past)]

    stance = row.get("rbi_stance", "unknown")
    shock  = row.get("macro_shock")

    return {
        "nifty_regime_ord"    : float(row.get("_regime_ord", 0.0) or 0.0),
        "nifty_sma200_dist_pct": float(row.get("_sma200_dist_pct", 0.0) or 0.0),
        "nifty_atr_pct_rank"  : float(row.get("_atr_pct", 50.0) or 50.0),
        "nifty_ret_1y_pct"    : float(row.get("_ret_1y", 0.0) or 0.0),
        "nifty_ret_3m_pct"    : float(row.get("_ret_3m", 0.0) or 0.0),
        "rbi_stance_ord"      : float(_RBI_ORD.get(stance, 2)),
        "macro_shock_active"  : 0.0 if (shock is None or str(shock) == "nan") else 1.0,
        "shock_severity"      : float(row.get("shock_severity", 0) or 0),
        "shock_bearish"       : float(_SHOCK_BIAS_BIN.get(row.get("shock_bias", "NEUTRAL"), 0)),
        "is_black_swan"       : float(bool(row.get("is_black_swan", False))),
        "is_election_week"    : float(bool(row.get("is_election_week", False))),
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def add_macro_regime_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds 11 macro context feature columns to the ML training DataFrame.
    Matches each trade row to its daily NIFTY bar via entry_time / date.
    Gracefully returns all-zero/default values if historical data not loaded.
    """
    out = df.copy()
    lookup = _get_daily_lookup()

    if lookup is None:
        logger.info("macro_regime_features: no daily data — adding default zeros")
        for col in MACRO_REGIME_FEATURE_COLS:
            out[col] = MACRO_REGIME_DEFAULTS.get(col, 0.0)
        return out

    # Parse trade dates from entry_time or date column
    date_col = None
    for c in ("entry_time", "date", "entry_date"):
        if c in out.columns:
            date_col = c
            break

    if date_col is None:
        logger.warning("macro_regime_features: no date column found — using defaults")
        for col in MACRO_REGIME_FEATURE_COLS:
            out[col] = MACRO_REGIME_DEFAULTS.get(col, 0.0)
        return out

    trade_dates = pd.to_datetime(out[date_col], errors="coerce")

    rows = [_lookup_row(d, lookup) for d in trade_dates]
    feat_df = pd.DataFrame(rows, index=out.index)

    for col in feat_df.columns:
        out[col] = feat_df[col]

    logger.debug(
        f"macro_regime_features: enriched {len(out)} rows | "
        f"shocks={int(out['macro_shock_active'].sum())} | "
        f"elections={int(out['is_election_week'].sum())} | "
        f"avg_regime={out['nifty_regime_ord'].mean():.2f}"
    )
    return out


MACRO_REGIME_FEATURE_COLS = [
    "nifty_regime_ord",      # -2 to +2 (bear → bull)
    "nifty_sma200_dist_pct", # % above/below SMA200 (negative = bear)
    "nifty_atr_pct_rank",    # 0-100 (100 = most volatile)
    "nifty_ret_1y_pct",      # 1-year return at trade time
    "nifty_ret_3m_pct",      # 3-month return at trade time
    "rbi_stance_ord",        # 0=ultra_accom → 4=tightening
    "macro_shock_active",    # 0/1 binary
    "shock_severity",        # 0-3
    "shock_bearish",         # 1 if active shock is bearish
    "is_black_swan",         # 0/1 binary
    "is_election_week",      # 0/1 binary
]

MACRO_REGIME_DEFAULTS = {
    "nifty_regime_ord"    : 0.0,
    "nifty_sma200_dist_pct": 0.0,
    "nifty_atr_pct_rank"  : 50.0,
    "nifty_ret_1y_pct"    : 0.0,
    "nifty_ret_3m_pct"    : 0.0,
    "rbi_stance_ord"      : 2.0,
    "macro_shock_active"  : 0.0,
    "shock_severity"      : 0.0,
    "shock_bearish"       : 0.0,
    "is_black_swan"       : 0.0,
    "is_election_week"    : 0.0,
}
