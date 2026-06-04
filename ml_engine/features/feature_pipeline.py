"""
ml_engine/features/feature_pipeline.py

Master feature pipeline. Runs all feature modules in order and returns
a clean feature matrix (X) + target vectors (y_win, y_r) ready for DNN training.

Usage:
    from ml_engine.features.feature_pipeline import build_features
    X, y_win, y_r, feature_names = build_features(df_labeled)

READ-ONLY input. No live hooks. No execution imports.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from ml_engine.features.ict_features         import add_ict_features,         ICT_FEATURE_COLS
from ml_engine.features.market_features       import add_market_features,       MARKET_FEATURE_COLS
from ml_engine.features.session_features      import add_session_features,      SESSION_FEATURE_COLS
from ml_engine.features.risk_features         import add_risk_features,         RISK_FEATURE_COLS
from ml_engine.features.execution_features    import add_execution_features,    EXECUTION_FEATURE_COLS
from ml_engine.features.news_features         import add_news_features,         NEWS_FEATURE_COLS
from ml_engine.features.silver_bullet_features import add_silver_bullet_features, SILVER_BULLET_FEATURE_COLS

logger = logging.getLogger("cb6.ml.feature_pipeline")

# ── Master feature list (order matters for DNN input) ────────────────────────
ALL_FEATURE_COLS = (
    ICT_FEATURE_COLS +
    MARKET_FEATURE_COLS +
    SESSION_FEATURE_COLS +
    RISK_FEATURE_COLS +
    EXECUTION_FEATURE_COLS +
    NEWS_FEATURE_COLS +
    SILVER_BULLET_FEATURE_COLS
)

# Target columns
TARGET_WIN  = "win_loss_label"    # binary: 1=win, 0=loss
TARGET_R    = "r_multiple_label"  # continuous: R achieved
TARGET_GRADE = "trade_grade"      # categorical: A+/A/B/C

# Imputation defaults (used when column is NaN)
IMPUTE_DEFAULTS = {
    "liquidity_sweep"     : 0.0,
    "sweep_depth_pct"     : 0.0,
    "sweep_candles_ago"   : 0.0,
    "ob_size"             : 0.0,
    "ob_agrees_dol"       : 0.0,
    "dol_agrees_bin"      : 0.5,   # unknown → neutral
    "dol_distance_r"      : 3.0,   # assume DOL at T3
    "ut_aligned_bin"      : 0.5,
    "atr_proxy"           : 0.0,
    "risk_atr_ratio"      : 1.0,
    "momentum_3c"         : 0.0,
    "in_sb_window"        : 0.5,
    "minutes_into_window" : 30.0,
    "in_forex_kz"         : 0.5,
    "in_prime_kz"         : 0.5,
    "session_ord"         : 6.0,
    "dte_num"             : 7.0,
    "dte_bucket"          : 2.0,
    "win_streak"          : 0.0,
    "consec_wins"         : 0.0,
    "consec_losses"       : 0.0,
    "wr_last_10"          : 0.5,
    "avg_r_last_10"       : 0.0,
    "cumulative_r"        : 0.0,
    "drawdown_r"          : 0.0,
    "confluence_zscore"   : 0.0,
    "lots_num"            : 0.0,
    "leverage_num"        : 100.0,
    "risk_usd_num"        : 0.0,
    "news_blackout"       : 0.0,
    "news_impact"         : 0.0,
    "mode_live"           : 0.0,
    "opt_delta"           : 0.5,
    "opt_gamma"           : 0.0,
    "opt_theta"           : 0.0,
    "opt_iv"              : 0.2,
    "dol_rr_ratio"        : 1.0,
}


def run_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply all feature modules to a labeled DataFrame.
    Returns enriched DataFrame with all feature columns added.
    """
    logger.info(f"Feature pipeline start: {len(df)} rows, {len(df.columns)} cols")

    df = add_ict_features(df)
    logger.debug("ICT features done")

    df = add_market_features(df)
    logger.debug("Market features done")

    df = add_session_features(df)
    logger.debug("Session features done")

    df = add_risk_features(df)
    logger.debug("Risk features done")

    df = add_execution_features(df)
    logger.debug("Execution features done")

    df = add_news_features(df)
    logger.debug("News features done")

    df = add_silver_bullet_features(df)
    logger.debug("Silver Bullet features done")

    logger.info(f"Feature pipeline complete: {len(df.columns)} total cols")
    return df


def build_features(
    df: pd.DataFrame,
    feature_cols: Optional[list] = None,
    impute: bool = True,
    drop_missing_targets: bool = True,
    min_null_threshold: float = 0.95,
) -> tuple[pd.DataFrame, pd.Series, pd.Series, list]:
    """
    Full pipeline: enrich → select features → impute → return X, y_win, y_r.

    Parameters
    ----------
    df                   : Labeled DataFrame from dataset_builder.
    feature_cols         : Feature columns to use (default: ALL_FEATURE_COLS).
    impute               : Fill NaN with sensible defaults.
    drop_missing_targets : Drop rows where win_loss_label is NaN.
    min_null_threshold   : Drop feature columns > this fraction null.

    Returns
    -------
    X            : Feature DataFrame (rows × features)
    y_win        : Binary win/loss Series
    y_r          : R-multiple Series
    feature_names: List of feature column names used
    """
    # Sort by time first (critical for risk features + leakage prevention)
    if "entry_time" in df.columns:
        df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")
        df = df.sort_values("entry_time", na_position="last").reset_index(drop=True)

    # Run pipeline
    df = run_pipeline(df)

    # Select feature columns
    cols = feature_cols or ALL_FEATURE_COLS
    available = [c for c in cols if c in df.columns]
    missing   = [c for c in cols if c not in df.columns]
    if missing:
        logger.warning(f"{len(missing)} requested feature cols not in DataFrame: {missing}")

    X = df[available].copy()

    # Drop columns with too many nulls
    null_frac = X.isnull().mean()
    drop_cols = null_frac[null_frac > min_null_threshold].index.tolist()
    if drop_cols:
        logger.info(f"Dropping {len(drop_cols)} feature cols > {min_null_threshold:.0%} null: {drop_cols}")
        X = X.drop(columns=drop_cols)
        available = [c for c in available if c not in drop_cols]

    # Ensure all numeric first, then impute
    X = X.apply(pd.to_numeric, errors="coerce")

    if impute:
        null_mask = X.isnull().any()  # Series: col -> bool
        for col in X.columns[null_mask]:
            if col in IMPUTE_DEFAULTS:
                default = float(IMPUTE_DEFAULTS[col])
            else:
                med_val = X[col].median()
                med_scalar = float(med_val) if isinstance(med_val, (int, float, np.floating)) else 0.0
                default = med_scalar if not np.isnan(med_scalar) else 0.0
            X[col] = X[col].fillna(default)

    X = X.fillna(0.0)

    # Targets
    y_win = df[TARGET_WIN].copy() if TARGET_WIN in df.columns else pd.Series(np.nan, index=df.index)
    y_r   = df[TARGET_R].copy()   if TARGET_R   in df.columns else pd.Series(np.nan, index=df.index)

    # Drop rows with no win/loss label
    if drop_missing_targets:
        labeled_mask = y_win.notna()
        n_before = len(X)
        X     = X[labeled_mask].reset_index(drop=True)
        y_win = y_win[labeled_mask].reset_index(drop=True)
        y_r   = y_r[labeled_mask].reset_index(drop=True)
        if n_before != len(X):
            logger.info(f"Dropped {n_before - len(X)} rows with no outcome label")

    logger.info(
        f"Feature matrix: {X.shape} | "
        f"features={len(available)} | "
        f"win_rate={y_win.mean():.1%} | "
        f"avg_r={y_r.mean():.2f}"
    )
    return X, y_win, y_r, available


def get_feature_summary(X: pd.DataFrame) -> pd.DataFrame:
    """Return a summary DataFrame of feature statistics."""
    summary = pd.DataFrame({
        "feature"   : X.columns,
        "dtype"     : X.dtypes.values,
        "null_pct"  : X.isnull().mean().values,
        "mean"      : X.mean().values,
        "std"       : X.std().values,
        "min"       : X.min().values,
        "max"       : X.max().values,
        "zero_pct"  : (X == 0).mean().values,
    }).set_index("feature")
    return summary


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)

    from ml_engine.training.dataset_builder import build_dataset
    df_labeled = build_dataset(base_path="")

    if df_labeled is not None:
        X, y_win, y_r, feat_names = build_features(df_labeled)
        print(f"\nFeature matrix shape : {X.shape}")
        print(f"Feature count        : {len(feat_names)}")
        print(f"Win rate             : {y_win.mean():.1%}")
        print(f"Avg R                : {y_r.mean():.2f}")
        print(f"\nFeature list:")
        for i, f in enumerate(feat_names):
            print(f"  {i+1:2d}. {f}")
        print(f"\nSample feature row:")
        print(X.iloc[0].to_string())
        summary = get_feature_summary(X)
        print(f"\nNull % by feature (top 10):")
        print(summary["null_pct"].sort_values(ascending=False).head(10).to_string())
