"""
ml_engine/features/risk_features.py

Risk-state features: rolling win/loss streaks, drawdown, daily PnL context.
Computed from the sequential order of trades — must be called AFTER temporal sort.

LEAKAGE GUARD: all rolling features look BACKWARD only (shift(1) applied).
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def add_risk_features(df: pd.DataFrame, group_by_engine: bool = True) -> pd.DataFrame:
    """
    Add rolling risk-context features.
    All features use .shift(1) so the current trade cannot see its own outcome.

    Parameters
    ----------
    df              : Labeled dataset sorted by entry_time.
    group_by_engine : Compute rolling stats per engine (nse / forex separately).
    """
    out = df.copy()

    # Ensure sorted
    if "entry_time" in out.columns:
        out = out.sort_values("entry_time", na_position="last").reset_index(drop=True)

    win  = pd.to_numeric(out.get("win_loss_label"), errors="coerce")
    r_m  = pd.to_numeric(out.get("r_multiple_label"), errors="coerce")
    conf = pd.to_numeric(out.get("confluence_score", out.get("confluence")), errors="coerce")

    def _rolling_stats(series: pd.Series, win_series: pd.Series, grp_col=None) -> pd.DataFrame:
        """Compute rolling streak + win-rate for a series, grouped optionally."""
        result = pd.DataFrame(index=series.index)

        if grp_col is not None and grp_col in out.columns:
            groups = out[grp_col]
        else:
            groups = pd.Series("all", index=out.index)

        streak        = pd.Series(0.0, index=series.index)
        consec_wins   = pd.Series(0.0, index=series.index)
        consec_losses = pd.Series(0.0, index=series.index)
        wr_10         = pd.Series(np.nan, index=series.index)
        avg_r_10      = pd.Series(np.nan, index=series.index)

        for g in groups.unique():
            mask = groups == g
            idx  = series.index[mask]
            w    = win_series.loc[idx]
            r    = series.loc[idx]

            _streak = 0.0
            _cw = _cl = 0.0

            for i, (ix, wi) in enumerate(zip(idx, w)):
                streak.at[ix]        = _streak
                consec_wins.at[ix]   = _cw
                consec_losses.at[ix] = _cl
                if i >= 10:
                    wr_10.at[ix]    = w.iloc[max(0, i-10):i].mean()
                    avg_r_10.at[ix] = r.iloc[max(0, i-10):i].mean()
                if not np.isnan(wi):
                    if wi == 1:
                        _streak = max(0, _streak) + 1
                        _cw += 1
                        _cl  = 0
                    else:
                        _streak = min(0, _streak) - 1
                        _cl += 1
                        _cw  = 0

        result["streak"]        = streak
        result["consec_wins"]   = consec_wins
        result["consec_losses"] = consec_losses
        result["wr_last_10"]    = wr_10
        result["avg_r_last_10"] = avg_r_10
        return result

    grp = "engine" if group_by_engine else None
    rolling = _rolling_stats(r_m, win, grp_col=grp)

    out["win_streak"]      = rolling["streak"]
    out["consec_wins"]     = rolling["consec_wins"]
    out["consec_losses"]   = rolling["consec_losses"]
    out["wr_last_10"]      = rolling["wr_last_10"]
    out["avg_r_last_10"]   = rolling["avg_r_last_10"]

    # Cumulative PnL proxy (R-based, not dollar)
    if r_m.notna().any():
        out["cumulative_r"] = r_m.fillna(0).cumsum().shift(1)
        # Rolling max for drawdown
        cum = out["cumulative_r"].fillna(0)
        out["rolling_peak"] = cum.cummax()
        out["drawdown_r"]   = (cum - out["rolling_peak"]).clip(upper=0)
    else:
        out["cumulative_r"] = np.nan
        out["drawdown_r"]   = np.nan

    # Confluence z-score (how unusual is this setup's score)
    if conf.notna().sum() > 10:
        mu  = conf.mean()
        std = conf.std()
        out["confluence_zscore"] = ((conf - mu) / (std + 1e-9)).clip(-3, 3)
    else:
        out["confluence_zscore"] = np.nan

    return out


RISK_FEATURE_COLS = [
    "win_streak", "consec_wins", "consec_losses",
    "wr_last_10", "avg_r_last_10",
    "cumulative_r", "drawdown_r",
    "confluence_zscore",
]
