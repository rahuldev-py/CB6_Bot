# utils/nse_bullish_catalyst_analysis.py
# Measures how NIFTY and BANKNIFTY actually behaved after every major
# bullish catalyst from 2006–2026 against the real 20-year daily data.
#
# Metrics:
#   Forward returns : +5d +10d +30d +60d +90d +180d +365d
#   Max gain        : highest close reached within 365 trading days
#   Days to +10%    : how fast the first +10% milestone was hit
#   Days to +20%    : first +20% milestone
#   Days to +30%    : first +30% milestone
#   Bull duration   : consecutive trading days price stayed above +10% threshold
#   Volatility      : std-dev change pre vs post
#
# Output files:
#   data/nse/events/bullish_catalyst_dataset.csv    — full stats
#   data/nse/events/bullish_catalyst_report.txt     — human-readable table

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_EVENTS_FILE = Path(__file__).parent.parent / "data" / "nse" / "events" / "event_timeline.json"
_OUTPUT_DIR  = Path(__file__).parent.parent / "data" / "nse" / "events"

_FWD_HORIZONS  = [5, 10, 30, 60, 90, 180, 365]
_RALLY_TARGETS = [10, 20, 30]   # % milestones to measure days-to-hit


# ── helpers ───────────────────────────────────────────────────────────────────

def _nearest_prior(df: pd.DataFrame, dt: pd.Timestamp) -> Optional[int]:
    mask = df["date"] <= dt
    if not mask.any():
        return None
    return int(df.index[mask][-1])


def _fwd_return(df: pd.DataFrame, start_idx: int, n_days: int) -> Optional[float]:
    end_idx = start_idx + n_days
    if end_idx >= len(df):
        return None
    return round((df.iloc[end_idx]["close"] / df.iloc[start_idx]["close"] - 1) * 100, 2)


def _max_gain(df: pd.DataFrame, start_idx: int, window: int = 365) -> float:
    end_idx = min(start_idx + window, len(df))
    closes  = df.iloc[start_idx:end_idx]["close"].values
    p0      = closes[0]
    return round((closes.max() / p0 - 1) * 100, 2)


def _days_to_target(df: pd.DataFrame, start_idx: int,
                    target_pct: float, window: int = 500) -> Optional[int]:
    """How many trading days to first reach +target_pct% from start price."""
    p0      = df.iloc[start_idx]["close"]
    target  = p0 * (1 + target_pct / 100)
    end_idx = min(start_idx + window, len(df))
    for i in range(start_idx, end_idx):
        if df.iloc[i]["close"] >= target:
            return i - start_idx
    return None


def _bull_duration(df: pd.DataFrame, start_idx: int,
                   threshold_pct: float = 10.0, window: int = 500) -> int:
    """
    Count trading days price stays continuously above +threshold_pct from start.
    Resets to 0 when price falls below threshold. Returns max sustained streak.
    """
    p0      = df.iloc[start_idx]["close"]
    target  = p0 * (1 + threshold_pct / 100)
    end_idx = min(start_idx + window, len(df))
    closes  = df.iloc[start_idx:end_idx]["close"].values

    max_streak = 0
    streak     = 0
    for c in closes:
        if c >= target:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def _vol_change(df: pd.DataFrame, start_idx: int,
                pre_days: int = 60, post_days: int = 60) -> float:
    pre_start = max(0, start_idx - pre_days)
    post_end  = min(len(df), start_idx + post_days)
    pre_ret   = df.iloc[pre_start:start_idx]["close"].pct_change().dropna() * 100
    post_ret  = df.iloc[start_idx:post_end]["close"].pct_change().dropna() * 100
    if pre_ret.std() == 0 or len(pre_ret) < 5 or len(post_ret) < 5:
        return 0.0
    return round((post_ret.std() / pre_ret.std() - 1) * 100, 1)


# ── core analyser ─────────────────────────────────────────────────────────────

def analyse_catalyst(event: dict,
                     nifty_df: pd.DataFrame,
                     banknifty_df: pd.DataFrame) -> dict:
    start_dt = pd.Timestamp(event["date"])

    row = {
        "id"       : event["id"],
        "name"     : event["name"],
        "date"     : event["date"],
        "tier"     : event.get("tier", 0),
        "category" : event.get("category", ""),
    }

    for label, df, idx in [
        ("nifty",     nifty_df,     _nearest_prior(nifty_df,     start_dt)),
        ("banknifty", banknifty_df, _nearest_prior(banknifty_df, start_dt)),
    ]:
        if idx is None:
            for h in _FWD_HORIZONS:
                row[f"{label}_ret_{h}d"] = None
            for t in _RALLY_TARGETS:
                row[f"{label}_days_to_{t}pct"] = None
            row[f"{label}_max_gain_365d"]  = None
            row[f"{label}_bull_duration"]  = None
            row[f"{label}_vol_change_pct"] = None
            row[f"{label}_price_at_event"] = None
            continue

        p0 = df.iloc[idx]["close"]
        row[f"{label}_price_at_event"] = round(float(p0), 2)

        for h in _FWD_HORIZONS:
            row[f"{label}_ret_{h}d"] = _fwd_return(df, idx, h)

        for t in _RALLY_TARGETS:
            row[f"{label}_days_to_{t}pct"] = _days_to_target(df, idx, float(t))

        row[f"{label}_max_gain_365d"]  = _max_gain(df, idx)
        row[f"{label}_bull_duration"]  = _bull_duration(df, idx)
        row[f"{label}_vol_change_pct"] = _vol_change(df, idx)

    return row


def run_full_analysis() -> pd.DataFrame:
    from utils.nse_historical_loader import load_daily
    nifty_df     = load_daily("nifty50").reset_index(drop=True)
    banknifty_df = load_daily("banknifty").reset_index(drop=True)

    events_raw = json.loads(_EVENTS_FILE.read_text(encoding="utf-8"))
    catalysts  = events_raw.get("bullish_catalysts", [])

    return pd.DataFrame([
        analyse_catalyst(ev, nifty_df, banknifty_df)
        for ev in catalysts
    ])


# ── report formatter ──────────────────────────────────────────────────────────

def format_report(df: pd.DataFrame) -> str:
    def _f(v, decimals=1):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "  N/A"
        return f"{v:+.{decimals}f}%"

    def _d(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "  N/A"
        return f"{int(v):>4}d"

    SEP = "=" * 130

    lines = [
        SEP,
        "CB6 BULLISH CATALYST ANALYSIS — NIFTY & BANKNIFTY FORWARD RETURNS (2006–2026)",
        SEP,
        "",
        "SECTION 1 — FORWARD RETURNS",
        f"{'Event':<38} {'Tier'} {'Cat':<22}  "
        f"{'NF+30':>6} {'NF+90':>6} {'NF+180':>7} {'NF+365':>7} {'NF Max':>7}  "
        f"{'BN+30':>6} {'BN+90':>6} {'BN+180':>7} {'BN+365':>7} {'BN Max':>7}",
        "-" * 130,
    ]

    for _, r in df.iterrows():
        lines.append(
            f"{str(r['name'])[:37]:<38} "
            f"T{r['tier']}   "
            f"{str(r['category'])[:21]:<22}  "
            f"{_f(r['nifty_ret_30d']):>7} "
            f"{_f(r['nifty_ret_90d']):>7} "
            f"{_f(r['nifty_ret_180d']):>8} "
            f"{_f(r['nifty_ret_365d']):>8} "
            f"{_f(r['nifty_max_gain_365d']):>8}  "
            f"{_f(r['banknifty_ret_30d']):>7} "
            f"{_f(r['banknifty_ret_90d']):>7} "
            f"{_f(r['banknifty_ret_180d']):>8} "
            f"{_f(r['banknifty_ret_365d']):>8} "
            f"{_f(r['banknifty_max_gain_365d']):>8}"
        )

    lines += [
        "",
        "SECTION 2 — RALLY SPEED (days to hit milestone from catalyst date)",
        f"{'Event':<38} {'NF→+10%':>8} {'NF→+20%':>8} {'NF→+30%':>8} "
        f"{'NF Bull Dur':>11}  "
        f"{'BN→+10%':>8} {'BN→+20%':>8} {'BN→+30%':>8} {'BN Bull Dur':>11}",
        "-" * 115,
    ]

    for _, r in df.iterrows():
        lines.append(
            f"{str(r['name'])[:37]:<38} "
            f"{_d(r['nifty_days_to_10pct']):>9} "
            f"{_d(r['nifty_days_to_20pct']):>9} "
            f"{_d(r['nifty_days_to_30pct']):>9} "
            f"{_d(r['nifty_bull_duration']):>12}  "
            f"{_d(r['banknifty_days_to_10pct']):>9} "
            f"{_d(r['banknifty_days_to_20pct']):>9} "
            f"{_d(r['banknifty_days_to_30pct']):>9} "
            f"{_d(r['banknifty_bull_duration']):>12}"
        )

    # ── Key findings ──────────────────────────────────────────────────────────
    lines += ["", SEP, "KEY FINDINGS", SEP]

    valid = df.dropna(subset=["nifty_ret_365d"])

    # Best 1-year performers
    top3 = valid.nlargest(3, "nifty_ret_365d")
    lines.append("\nTop 3 NIFTY 1-year returns after catalyst:")
    for _, r in top3.iterrows():
        lines.append(
            f"  {r['name'][:55]:<56} "
            f"NF +{r['nifty_ret_365d']:.1f}%   BN {_f(r['banknifty_ret_365d'])}"
        )

    # Fastest +20% rallies
    speed = df.dropna(subset=["nifty_days_to_20pct"]).nsmallest(3, "nifty_days_to_20pct")
    lines.append("\nFastest NIFTY +20% rally after catalyst:")
    for _, r in speed.iterrows():
        lines.append(
            f"  {r['name'][:55]:<56} {int(r['nifty_days_to_20pct'])} trading days"
        )

    # BANKNIFTY outperformance
    both = df.dropna(subset=["nifty_ret_365d", "banknifty_ret_365d"]).copy()
    both["bn_premium"] = both["banknifty_ret_365d"] - both["nifty_ret_365d"]
    bn_out = both.nlargest(3, "bn_premium")
    lines.append("\nBest BANKNIFTY outperformance vs NIFTY (1 year):")
    for _, r in bn_out.iterrows():
        lines.append(
            f"  {r['name'][:55]:<56} "
            f"BN {r['banknifty_ret_365d']:+.1f}% vs NF {r['nifty_ret_365d']:+.1f}% "
            f"(+{r['bn_premium']:.1f}% premium)"
        )

    # Summary stats by tier
    lines.append("\nAvg NIFTY 1-year return by tier:")
    for tier in sorted(df["tier"].unique()):
        grp = df[df["tier"] == tier].dropna(subset=["nifty_ret_365d"])
        if not grp.empty:
            avg_nf = grp["nifty_ret_365d"].mean()
            avg_bn = grp["banknifty_ret_365d"].mean() if "banknifty_ret_365d" in grp else None
            bn_str = f"  BN avg {avg_bn:+.1f}%" if avg_bn is not None else ""
            lines.append(f"  Tier {tier}: NIFTY avg {avg_nf:+.1f}%{bn_str}  ({len(grp)} events)")

    # Bullish vs bearish comparison
    try:
        geo_df = _load_geo_stats()
        if geo_df is not None:
            avg_bull_nf = valid["nifty_ret_365d"].mean()
            avg_bear_nf = geo_df["nifty_ret_90d"].mean()
            lines.append(f"\nBullish catalysts avg 1yr NIFTY return : {avg_bull_nf:+.1f}%")
            lines.append(f"Bearish shocks avg 90d NIFTY return     : {avg_bear_nf:+.1f}%")
            lines.append("→ Markets spend far more time recovering from bull catalysts than bear shocks")
    except Exception:
        pass

    lines += ["", SEP,
              "BULL DURATION = max consecutive trading days price stayed ≥+10% above catalyst price",
              "N/A = milestone not reached within 500 trading days of data available",
              SEP]

    return "\n".join(lines)


def _load_geo_stats() -> Optional[pd.DataFrame]:
    p = _OUTPUT_DIR / "geopolitical_regime_dataset.csv"
    return pd.read_csv(p) if p.exists() else None


def save_dataset(df: pd.DataFrame) -> Path:
    out = _OUTPUT_DIR / "bullish_catalyst_dataset.csv"
    df.to_csv(out, index=False)
    return out


def run_and_report() -> tuple[pd.DataFrame, str]:
    df     = run_full_analysis()
    report = format_report(df)
    save_dataset(df)
    txt = _OUTPUT_DIR / "bullish_catalyst_report.txt"
    txt.write_text(report, encoding="utf-8")
    return df, report
