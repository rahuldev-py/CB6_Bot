# utils/nse_structural_levels.py
# Extracts key structural levels from 20-year NIFTY daily data.
#
# These levels are institutional DOL (Draw on Liquidity) anchors —
# major swing highs/lows that price repeatedly revisits over years.
# Used by the scanner to identify magnet prices and validate setup quality.

from __future__ import annotations

from functools import lru_cache
from typing import NamedTuple

import pandas as pd

from utils.nse_historical_loader import load_daily


class Level(NamedTuple):
    price:    float
    kind:     str       # 'HIGH' or 'LOW'
    date:     str       # YYYY-MM-DD
    age_days: int       # days since this level formed
    label:    str       # human-readable label e.g. "2024 ATH"


def _swing_highs_lows(
    df: pd.DataFrame,
    window: int = 20,          # bars on each side to qualify as a swing
    lookback_days: int = 365 * 10,
) -> tuple[list[Level], list[Level]]:
    """
    Find significant swing highs and lows in the last `lookback_days` of daily data.
    window=20 means the candle must be highest/lowest among 41 consecutive days.
    """
    if df.empty:
        return [], []

    from datetime import date
    today = pd.Timestamp(date.today())
    cutoff = today - pd.Timedelta(days=lookback_days)
    df = df[df["date"] >= cutoff].reset_index(drop=True)
    if len(df) < window * 2 + 1:
        return [], []

    highs, lows = [], []
    today_str = today.date().isoformat()

    for i in range(window, len(df) - window):
        row       = df.iloc[i]
        hi_window = df.iloc[i - window: i + window + 1]["high"]
        lo_window = df.iloc[i - window: i + window + 1]["low"]

        if row["high"] == hi_window.max():
            age = (today - row["date"]).days
            highs.append(Level(
                price=round(float(row["high"]), 2),
                kind="HIGH",
                date=row["date"].date().isoformat(),
                age_days=age,
                label=f"{row['date'].year} swing high",
            ))

        if row["low"] == lo_window.min():
            age = (today - row["date"]).days
            lows.append(Level(
                price=round(float(row["low"]), 2),
                kind="LOW",
                date=row["date"].date().isoformat(),
                age_days=age,
                label=f"{row['date'].year} swing low",
            ))

    return highs, lows


def _annual_extremes(df: pd.DataFrame) -> list[Level]:
    """
    Year-by-year high and low — the strongest DOL anchors in ICT theory.
    Price always tries to raid these at some point.
    """
    if df.empty:
        return []
    from datetime import date
    today = date.today()
    levels = []
    for year, grp in df.groupby(df["date"].dt.year):
        if grp.empty:
            continue
        yr_high = round(float(grp["high"].max()), 2)
        yr_low  = round(float(grp["low"].min()),  2)
        hi_date = grp.loc[grp["high"].idxmax(), "date"].date().isoformat()
        lo_date = grp.loc[grp["low"].idxmin(),  "date"].date().isoformat()
        age_h   = (pd.Timestamp(today) - grp.loc[grp["high"].idxmax(), "date"]).days
        age_l   = (pd.Timestamp(today) - grp.loc[grp["low"].idxmin(),  "date"]).days
        levels.append(Level(yr_high, "HIGH", hi_date, age_h, f"{year} annual high"))
        levels.append(Level(yr_low,  "LOW",  lo_date, age_l, f"{year} annual low"))
    return levels


@lru_cache(maxsize=8)
def get_structural_levels(index: str = "nifty50") -> dict:
    """
    Returns all significant structural levels from 20yr data for the given index.
    index: 'nifty50' | 'banknifty' | 'finnifty' | 'midcpnifty'
    """
    df = load_daily(index)
    if df.empty:
        return {"annual_extremes": [], "swing_highs": [], "swing_lows": [],
                "ath": None, "atl": None}

    annual = _annual_extremes(df)
    sh, sl = _swing_highs_lows(df)
    ath    = round(float(df["high"].max()), 2)
    atl    = round(float(df["low"].min()),  2)

    return {"annual_extremes": annual, "swing_highs": sh, "swing_lows": sl,
            "ath": ath, "atl": atl}


def nearest_levels(ltp: float, n: int = 5, index: str = "nifty50") -> dict:
    """
    Return n nearest structural levels above and below current price.
    Useful for the scanner to identify DOL targets and support/resistance.
    """
    levels = get_structural_levels(index)
    all_levels = levels["annual_extremes"] + levels["swing_highs"] + levels["swing_lows"]

    above = sorted([l for l in all_levels if l.price > ltp], key=lambda x: x.price)[:n]
    below = sorted([l for l in all_levels if l.price < ltp], key=lambda x: -x.price)[:n]

    return {
        "above": above,   # nearest resistance / buy-side DOL targets
        "below": below,   # nearest support / sell-side DOL targets
        "ath":   levels["ath"],
        "atl":   levels["atl"],
    }


def format_structural_levels(ltp: float, n: int = 5, index: str = "nifty50") -> str:
    """Telegram-ready formatted structural levels."""
    r = nearest_levels(ltp, n, index)
    lines = ["--- 20yr STRUCTURAL LEVELS ---"]

    if not r["above"] and not r["below"]:
        return "20yr structural levels: not available (load CSV files)"

    if r.get("ath"):
        ath_dist = round(r["ath"] - ltp, 1)
        lines.append(f"ATH : {r['ath']}  ({ath_dist:+.1f})")

    lines.append("Resistance (BSL above):")
    for lv in reversed(r["above"]):
        dist = round(lv.price - ltp, 1)
        lines.append(f"  {lv.price}  ({dist:+.1f})  [{lv.label}]")

    lines.append(f"  ← LTP {ltp}")

    lines.append("Support (SSL below):")
    for lv in r["below"]:
        dist = round(lv.price - ltp, 1)
        lines.append(f"  {lv.price}  ({dist:+.1f})  [{lv.label}]")

    if r.get("atl"):
        atl_dist = round(ltp - r["atl"], 1)
        lines.append(f"ATL : {r['atl']}  (-{atl_dist:.1f})")

    return "\n".join(lines)
