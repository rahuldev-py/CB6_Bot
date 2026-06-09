# utils/nse_geopolitical_analysis.py
# Calculates NIFTY and BANKNIFTY actual returns, drawdowns, recovery days
# and volatility changes for every geopolitical event in the timeline.
#
# Output: geopolitical_regime_dataset.csv  — ML-ready feature rows
#         geopolitical_report.txt          — Human-readable table

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_EVENTS_FILE  = Path(__file__).parent.parent / "data" / "nse" / "events" / "event_timeline.json"
_OUTPUT_DIR   = Path(__file__).parent.parent / "data" / "nse" / "events"
_HORIZONS     = [5, 10, 30, 60, 90]   # trading days forward


# ── helpers ───────────────────────────────────────────────────────────────────

def _nearest_prior(df: pd.DataFrame, dt: pd.Timestamp) -> Optional[int]:
    """Index of nearest trading day on or before dt."""
    mask = df["date"] <= dt
    if not mask.any():
        return None
    return int(df.index[mask][-1])


def _forward_return(df: pd.DataFrame, start_idx: int, n_days: int) -> Optional[float]:
    """% return from start_idx to start_idx + n_days trading days."""
    end_idx = start_idx + n_days
    if end_idx >= len(df):
        return None
    p0 = df.iloc[start_idx]["close"]
    p1 = df.iloc[end_idx]["close"]
    return round((p1 / p0 - 1) * 100, 2)


def _max_drawdown(df: pd.DataFrame, start_idx: int, window: int = 90) -> float:
    """Max drawdown (%) from start_idx over next `window` trading days."""
    end_idx = min(start_idx + window, len(df))
    closes  = df.iloc[start_idx:end_idx]["close"].values
    if len(closes) < 2:
        return 0.0
    peak   = closes[0]
    max_dd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        dd = (c - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _recovery_days(df: pd.DataFrame, start_idx: int, max_window: int = 500) -> Optional[int]:
    """
    Days to recover to price at start_idx.
    Returns None if not recovered within max_window trading days.
    """
    p0      = df.iloc[start_idx]["close"]
    end_idx = min(start_idx + max_window, len(df))
    closes  = df.iloc[start_idx:end_idx]["close"].values
    trough_done = False
    for i, c in enumerate(closes):
        if c < p0:
            trough_done = True
        if trough_done and c >= p0:
            return i
    return None


def _volatility_change(df: pd.DataFrame, start_idx: int,
                        pre_days: int = 30, post_days: int = 30) -> float:
    """
    % change in daily return std-dev: post-event vs pre-event.
    Positive = volatility increased.
    """
    pre_start  = max(0, start_idx - pre_days)
    post_end   = min(len(df), start_idx + post_days)
    pre_rets   = df.iloc[pre_start:start_idx]["close"].pct_change().dropna() * 100
    post_rets  = df.iloc[start_idx:post_end]["close"].pct_change().dropna() * 100
    if pre_rets.std() == 0 or len(pre_rets) < 5 or len(post_rets) < 5:
        return 0.0
    return round((post_rets.std() / pre_rets.std() - 1) * 100, 1)


# ── core analyser ─────────────────────────────────────────────────────────────

def analyse_event(
    event: dict,
    nifty_df: pd.DataFrame,
    banknifty_df: pd.DataFrame,
) -> dict:
    start_dt = pd.Timestamp(event["start"])

    ni_idx = _nearest_prior(nifty_df,    start_dt)
    bn_idx = _nearest_prior(banknifty_df, start_dt)

    row: dict = {
        "id"          : event["id"],
        "name"        : event["name"],
        "start"       : event["start"],
        "end"         : event.get("end", event["start"]),
        "category"    : event.get("category", ""),
        "severity"    : event.get("severity", 0),
    }

    for label, df, idx in [("nifty", nifty_df, ni_idx),
                            ("banknifty", banknifty_df, bn_idx)]:
        if idx is None:
            for h in _HORIZONS:
                row[f"{label}_ret_{h}d"] = None
            row[f"{label}_max_dd"]        = None
            row[f"{label}_recovery_days"] = None
            row[f"{label}_vol_change_pct"]= None
            row[f"{label}_price_at_event"]= None
            continue

        row[f"{label}_price_at_event"] = round(float(df.iloc[idx]["close"]), 2)
        for h in _HORIZONS:
            row[f"{label}_ret_{h}d"] = _forward_return(df, idx, h)
        row[f"{label}_max_dd"]         = _max_drawdown(df, idx)
        row[f"{label}_recovery_days"]  = _recovery_days(df, idx)
        row[f"{label}_vol_change_pct"] = _volatility_change(df, idx)

    return row


def run_full_analysis() -> pd.DataFrame:
    from utils.nse_historical_loader import load_daily
    nifty_df    = load_daily("nifty50").reset_index(drop=True)
    banknifty_df = load_daily("banknifty").reset_index(drop=True)

    events_raw = json.loads(_EVENTS_FILE.read_text(encoding="utf-8"))
    geo_events = events_raw.get("geopolitical_events", [])

    rows = []
    for ev in geo_events:
        rows.append(analyse_event(ev, nifty_df, banknifty_df))

    df = pd.DataFrame(rows)
    return df


# ── report formatter ──────────────────────────────────────────────────────────

def format_report(df: pd.DataFrame) -> str:
    lines = [
        "=" * 90,
        "CB6 GEOPOLITICAL EVENT IMPACT ANALYSIS — NIFTY & BANKNIFTY (2006-2026)",
        "=" * 90,
        "",
        f"{'Event':<35} {'Cat':<12} {'Sev':>3}  "
        f"{'NF-5d':>6} {'NF-30d':>6} {'NF-90d':>7} {'NF DD':>7} {'NF Rec':>6}  "
        f"{'BN-5d':>6} {'BN-30d':>6} {'BN-90d':>7} {'BN DD':>7} {'BN Rec':>6}",
        "-" * 120,
    ]

    def _fmt(v, pct=True):
        if v is None:
            return "  N/A "
        s = f"{v:+.1f}%" if pct else f"{v:.0f}d"
        return f"{s:>7}"

    for _, r in df.iterrows():
        nf_rec = r["nifty_recovery_days"]
        bn_rec = r["banknifty_recovery_days"]
        lines.append(
            f"{str(r['name'])[:34]:<35} "
            f"{str(r['category'])[:11]:<12} {r['severity']:>3}  "
            f"{_fmt(r['nifty_ret_5d'])} "
            f"{_fmt(r['nifty_ret_30d'])} "
            f"{_fmt(r['nifty_ret_90d'])} "
            f"{_fmt(r['nifty_max_dd'])} "
            f"{'  N/A ' if nf_rec is None else f'{nf_rec:>5}d':>7}  "
            f"{_fmt(r['banknifty_ret_5d'])} "
            f"{_fmt(r['banknifty_ret_30d'])} "
            f"{_fmt(r['banknifty_ret_90d'])} "
            f"{_fmt(r['banknifty_max_dd'])} "
            f"{'  N/A ' if bn_rec is None else f'{bn_rec:>5}d':>7}"
        )

    lines += [
        "",
        "=" * 90,
        "SEVERITY LEGEND: 10=Black Swan  8-9=Major  6-7=Significant  4-5=Notable",
        "Max DD = peak-to-trough during 90d window from event start",
        "Recovery = trading days to return to price at event start",
        "N/A Recovery = not recovered within 500 trading days",
        "",
        "KEY FINDINGS:",
    ]

    # Auto-extract key findings
    worst_nifty = df.nsmallest(3, "nifty_ret_30d")[["name","nifty_ret_30d","nifty_max_dd"]]
    best_nifty  = df.nlargest(3,  "nifty_ret_30d")[["name","nifty_ret_30d"]]
    slowest_rec = df.dropna(subset=["nifty_recovery_days"]).nlargest(3, "nifty_recovery_days")

    lines.append("  Worst NIFTY 30-day reactions:")
    for _, r in worst_nifty.iterrows():
        lines.append(f"    {r['name'][:50]}: {r['nifty_ret_30d']:+.1f}%  DD {r['nifty_max_dd']:.1f}%")

    lines.append("  Best NIFTY 30-day reactions:")
    for _, r in best_nifty.iterrows():
        lines.append(f"    {r['name'][:50]}: {r['nifty_ret_30d']:+.1f}%")

    lines.append("  Slowest recoveries:")
    for _, r in slowest_rec.iterrows():
        lines.append(f"    {r['name'][:50]}: {r['nifty_recovery_days']:.0f} trading days")

    lines.append("")

    # Severity vs return correlation
    sev_rets = df.dropna(subset=["nifty_ret_30d"])
    if len(sev_rets) > 3:
        corr = sev_rets["severity"].corr(sev_rets["nifty_ret_30d"])
        lines.append(f"  Severity vs NIFTY 30d return correlation: {corr:+.3f}")
        lines.append(f"  (negative = higher severity → worse returns, as expected)")

    # NIFTY vs BANKNIFTY comparison
    both = df.dropna(subset=["nifty_ret_30d", "banknifty_ret_30d"])
    if len(both) > 0:
        avg_nf = both["nifty_ret_30d"].mean()
        avg_bn = both["banknifty_ret_30d"].mean()
        lines.append(f"  Avg NIFTY 30d return across all events: {avg_nf:+.2f}%")
        lines.append(f"  Avg BANKNIFTY 30d return across events: {avg_bn:+.2f}%")
        n_bn_worse = (both["banknifty_ret_30d"] < both["nifty_ret_30d"]).sum()
        lines.append(
            f"  BANKNIFTY underperforms NIFTY in {n_bn_worse}/{len(both)} events "
            f"(banks more sensitive to global shocks)"
        )

    lines.append("=" * 90)
    return "\n".join(lines)


# ── save ML dataset ───────────────────────────────────────────────────────────

def save_geopolitical_dataset(df: pd.DataFrame) -> Path:
    """
    Save ML-ready CSV with all event stats.
    Also saves a binary feature version for ML training injection.
    """
    out_csv = _OUTPUT_DIR / "geopolitical_regime_dataset.csv"
    df.to_csv(out_csv, index=False)

    # Binary/ordinal ML version — one row per event, numeric only
    ml_cols = ["id", "severity", "nifty_ret_5d", "nifty_ret_10d", "nifty_ret_30d",
               "nifty_ret_60d", "nifty_ret_90d", "nifty_max_dd", "nifty_recovery_days",
               "nifty_vol_change_pct", "banknifty_ret_5d", "banknifty_ret_10d",
               "banknifty_ret_30d", "banknifty_ret_60d", "banknifty_ret_90d",
               "banknifty_max_dd", "banknifty_recovery_days", "banknifty_vol_change_pct"]
    ml_df = df[[c for c in ml_cols if c in df.columns]].copy()
    ml_out = _OUTPUT_DIR / "geopolitical_ml_features.csv"
    ml_df.to_csv(ml_out, index=False)

    return out_csv


def run_and_report() -> tuple[pd.DataFrame, str]:
    df     = run_full_analysis()
    report = format_report(df)
    save_geopolitical_dataset(df)
    txt_path = _OUTPUT_DIR / "geopolitical_report.txt"
    txt_path.write_text(report, encoding="utf-8")
    return df, report
