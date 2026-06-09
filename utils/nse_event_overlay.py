# utils/nse_event_overlay.py
# Overlays the event timeline onto a NIFTY daily OHLCV DataFrame.
#
# Adds these columns to any daily DataFrame:
#
#   macro_shock      str | None   — active shock id (e.g. 'GFC', 'COVID_CRASH')
#   shock_severity   int  0-3     — 0=none, 1=low, 2=medium, 3=black_swan
#   shock_bias       str          — 'BEARISH' | 'BULLISH' | 'NEUTRAL'
#   is_black_swan    bool         — severity == 3
#   is_election_week bool         — within election window
#   election_id      str | None   — e.g. 'GE_2024'
#   rbi_stance       str          — 'easing'|'tightening'|'ultra_accommodative'|
#                                   'neutral'|'pivot'|'unknown'
#   sector_cycle     str | None   — active sector cycle id
#   sector_leaders   str          — comma-joined leader sectors
#   combined_regime  str          — composite tag for ML categorical encoding
#
# Usage:
#   from utils.nse_event_overlay import enrich
#   df = enrich(load_nifty_daily())   # adds all flag columns in-place

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

_EVENTS_FILE = Path(__file__).parent.parent / "data" / "nse" / "events" / "event_timeline.json"


@lru_cache(maxsize=1)
def _load_events() -> dict:
    return json.loads(_EVENTS_FILE.read_text(encoding="utf-8"))


# ── interval helpers ──────────────────────────────────────────────────────────

def _interval_series(df: pd.DataFrame, start: str, end: str) -> pd.Series:
    """Boolean mask for rows where date falls in [start, end]."""
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    return (df["date"] >= s) & (df["date"] <= e)


# ── per-category overlays ─────────────────────────────────────────────────────

def _apply_macro_shocks(df: pd.DataFrame, events: dict) -> pd.DataFrame:
    df["macro_shock"]    = None
    df["shock_severity"] = 0
    df["shock_bias"]     = "NEUTRAL"
    df["is_black_swan"]  = False

    for shock in events["macro_shocks"]:
        mask = _interval_series(df, shock["start"], shock["end"])
        df.loc[mask, "macro_shock"]    = shock["id"]
        df.loc[mask, "shock_severity"] = shock["severity"]
        df.loc[mask, "shock_bias"]     = shock["direction_bias"]
        df.loc[mask, "is_black_swan"]  = shock["severity"] == 3

    return df


def _apply_elections(df: pd.DataFrame, events: dict) -> pd.DataFrame:
    df["is_election_week"] = False
    df["election_id"]      = None

    for el in events["elections"]:
        mask = _interval_series(df, el["window_start"], el["window_end"])
        df.loc[mask, "is_election_week"] = True
        df.loc[mask, "election_id"]      = el["id"]

    return df


def _apply_rbi_regimes(df: pd.DataFrame, events: dict) -> pd.DataFrame:
    df["rbi_stance"] = "unknown"

    for rr in events["rbi_regimes"]:
        mask = _interval_series(df, rr["start"], rr["end"])
        df.loc[mask, "rbi_stance"] = rr["stance"]

    return df


def _apply_sector_cycles(df: pd.DataFrame, events: dict) -> pd.DataFrame:
    df["sector_cycle"]   = None
    df["sector_leaders"] = ""

    for sc in events["sector_cycles"]:
        mask = _interval_series(df, sc["start"], sc["end"])
        df.loc[mask, "sector_cycle"]   = sc["id"]
        df.loc[mask, "sector_leaders"] = ", ".join(sc["leaders"])

    return df


def _add_combined_regime(df: pd.DataFrame) -> pd.DataFrame:
    """
    Composite regime tag for ML categorical encoding.
    Format: {rbi_stance}__{shock_or_normal}__{election_or_normal}
    Example: 'tightening__GFC__normal'  or  'easing__normal__GE_2014'
    """
    shock_col  = df["macro_shock"].fillna("normal")
    elect_col  = df["election_id"].fillna("normal")
    df["combined_regime"] = (
        df["rbi_stance"] + "__" + shock_col + "__" + elect_col
    )
    return df


# ── public API ────────────────────────────────────────────────────────────────

def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all event overlay columns to a daily OHLCV DataFrame.
    The input must have a 'date' column (pd.Timestamp).
    Returns a new DataFrame (does not modify in-place).
    """
    if df.empty:
        return df

    events = _load_events()
    out = df.copy()
    out = _apply_macro_shocks(out, events)
    out = _apply_elections(out, events)
    out = _apply_rbi_regimes(out, events)
    out = _apply_sector_cycles(out, events)
    out = _add_combined_regime(out)
    return out


def get_active_events(as_of: str | None = None) -> dict:
    """
    Return all events active on a given date (default: today).
    Useful for live scanner context — 'what macro regime am I trading in right now?'
    """
    from datetime import date
    dt = pd.Timestamp(as_of) if as_of else pd.Timestamp(date.today())
    events = _load_events()

    active: dict = {
        "macro_shock"   : None,
        "shock_severity": 0,
        "shock_bias"    : "NEUTRAL",
        "is_black_swan" : False,
        "is_election_week": False,
        "election_id"   : None,
        "rbi_stance"    : "unknown",
        "sector_cycle"  : None,
        "sector_leaders": [],
    }

    for shock in events["macro_shocks"]:
        if pd.Timestamp(shock["start"]) <= dt <= pd.Timestamp(shock["end"]):
            active["macro_shock"]    = shock["id"]
            active["shock_severity"] = shock["severity"]
            active["shock_bias"]     = shock["direction_bias"]
            active["is_black_swan"]  = shock["severity"] == 3

    for el in events["elections"]:
        if pd.Timestamp(el["window_start"]) <= dt <= pd.Timestamp(el["window_end"]):
            active["is_election_week"] = True
            active["election_id"]      = el["id"]

    for rr in events["rbi_regimes"]:
        if pd.Timestamp(rr["start"]) <= dt <= pd.Timestamp(rr["end"]):
            active["rbi_stance"] = rr["stance"]

    for sc in events["sector_cycles"]:
        if pd.Timestamp(sc["start"]) <= dt <= pd.Timestamp(sc["end"]):
            active["sector_cycle"]   = sc["id"]
            active["sector_leaders"] = sc["leaders"]

    return active


def format_active_events(as_of: str | None = None) -> str:
    """Telegram-ready summary of currently active macro context."""
    a = get_active_events(as_of)
    lines = ["--- MACRO CONTEXT ---"]

    rbi_icons = {
        "easing": "↓ Easing", "tightening": "↑ Tightening",
        "ultra_accommodative": "↓↓ Ultra-Accommodative",
        "neutral": "→ Neutral", "pivot": "↻ Pivot", "unknown": "? Unknown",
    }
    lines.append(f"RBI Stance  : {rbi_icons.get(a['rbi_stance'], a['rbi_stance'])}")

    if a["macro_shock"]:
        sev = "🔴 BLACK SWAN" if a["is_black_swan"] else ("🟠 MAJOR" if a["shock_severity"] >= 2 else "🟡 MINOR")
        lines.append(f"Active Shock: {sev} — {a['macro_shock']} ({a['shock_bias']})")
    else:
        lines.append(f"Active Shock: None")

    if a["is_election_week"]:
        lines.append(f"Election    : ⚠️ ACTIVE — {a['election_id']} (IV spike likely)")

    if a["sector_cycle"]:
        leaders = ", ".join(a["sector_leaders"][:3])
        lines.append(f"Sector Cycle: {a['sector_cycle']} | Leaders: {leaders}")

    return "\n".join(lines)


# ── regime stats for a given event filter ────────────────────────────────────

def regime_performance_stats(df_enriched: pd.DataFrame,
                              filter_col: str,
                              filter_val,
                              return_col: str = "daily_return") -> dict:
    """
    Given an enriched DataFrame with a 'close' column, compute basic stats
    for rows matching filter_col == filter_val.

    Example:
        stats = regime_performance_stats(df, 'rbi_stance', 'tightening')
    """
    if "daily_return" not in df_enriched.columns:
        df_enriched = df_enriched.copy()
        df_enriched["daily_return"] = df_enriched["close"].pct_change() * 100

    subset = df_enriched[df_enriched[filter_col] == filter_val]["daily_return"].dropna()
    if subset.empty:
        return {"rows": 0}

    pos = (subset > 0).sum()
    total = len(subset)
    return {
        "filter"       : f"{filter_col}={filter_val}",
        "rows"         : total,
        "positive_days": int(pos),
        "win_rate_pct" : round(pos / total * 100, 1),
        "avg_return"   : round(subset.mean(), 3),
        "std_return"   : round(subset.std(), 3),
        "max_return"   : round(subset.max(), 2),
        "min_return"   : round(subset.min(), 2),
        "sharpe_proxy" : round(subset.mean() / subset.std(), 3) if subset.std() > 0 else 0,
    }
