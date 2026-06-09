# utils/nse_regime.py
# Market regime detector using 20-year NSE index daily history.
#
# Supports: nifty50, banknifty, finnifty, midcpnifty
#
# Regime feeds into:
#   1. setup_scorer / silver_bullet — ±1 score on regime-aligned/opposing setups
#   2. nifty_levels Telegram report — full regime block with SMA / ATR / returns
#
# Cached per index per calendar day.

from __future__ import annotations

from datetime import date
from typing import Literal

import pandas as pd

from utils.nse_historical_loader import load_daily

RegimeLabel = Literal["STRONG_BULL", "WEAK_BULL", "SIDEWAYS", "WEAK_BEAR", "STRONG_BEAR", "UNKNOWN"]
VolLabel    = Literal["HIGH_VOL", "NORMAL_VOL", "LOW_VOL"]

# ── per-index cache ────────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}        # index → result
_cache_dates: dict[str, str] = {}   # index → YYYY-MM-DD


def _compute_regime(df: pd.DataFrame) -> dict:
    if len(df) < 50:
        return _unknown()

    df = df.copy().reset_index(drop=True)
    c  = df["close"]

    sma50  = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()

    latest   = c.iloc[-1]
    s50_now  = sma50.iloc[-1]
    s200_now = sma200.iloc[-1] if len(df) >= 200 else None

    ret_1y = (latest / c.iloc[-252] - 1) * 100 if len(df) >= 252 else None
    ret_3m = (latest / c.iloc[-63]  - 1) * 100 if len(df) >= 63  else None

    h, l = df["high"], df["low"]
    tr = pd.concat([
        h - l,
        (h - c.shift(1)).abs(),
        (l - c.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr14       = tr.rolling(14).mean()
    atr_now     = atr14.iloc[-1]
    atr_pct_rank = (atr14.iloc[-252:] < atr_now).mean() * 100 if len(df) >= 252 else 50.0

    above_50  = latest > s50_now  if pd.notna(s50_now)  else None
    above_200 = (latest > s200_now) if (s200_now is not None and pd.notna(s200_now)) else None
    bull_cross = (s50_now > s200_now) if (
        s200_now is not None and pd.notna(s50_now) and pd.notna(s200_now)
    ) else None

    if above_200 is None:
        regime: RegimeLabel = "UNKNOWN"
    elif above_200 and bull_cross and (ret_1y or 0) > 15:
        regime = "STRONG_BULL"
    elif above_200 and (above_50 or bull_cross):
        regime = "WEAK_BULL"
    elif not above_200 and not bull_cross and (ret_1y or 0) < -15:
        regime = "STRONG_BEAR"
    elif not above_200 or (not above_50 and not bull_cross):
        regime = "WEAK_BEAR"
    else:
        regime = "SIDEWAYS"

    vol: VolLabel = (
        "HIGH_VOL"   if atr_pct_rank >= 75 else
        "LOW_VOL"    if atr_pct_rank <= 25 else
        "NORMAL_VOL"
    )

    current_year = df["date"].iloc[-1].year
    ytd_df  = df[df["date"].dt.year == current_year]
    ytd_ret = ((latest / ytd_df["close"].iloc[0] - 1) * 100) if len(ytd_df) > 1 else 0.0

    recent_252 = df.tail(252)
    year_high  = round(float(recent_252["high"].max()), 2)
    year_low   = round(float(recent_252["low"].min()),  2)

    recent_5y = df.tail(252 * 5) if len(df) >= 252 * 5 else df
    ath        = round(float(recent_5y["high"].max()), 2)
    atl        = round(float(recent_5y["low"].min()),  2)

    bias_score = {
        "STRONG_BULL": +1, "WEAK_BULL": +1,
        "SIDEWAYS": 0,
        "WEAK_BEAR": -1, "STRONG_BEAR": -1,
        "UNKNOWN": 0,
    }[regime]

    return {
        "regime"       : regime,
        "vol"          : vol,
        "bias_score"   : bias_score,
        "latest_close" : round(latest, 2),
        "sma50"        : round(s50_now,  2) if pd.notna(s50_now)  else None,
        "sma200"       : round(s200_now, 2) if s200_now is not None and pd.notna(s200_now) else None,
        "atr14"        : round(atr_now,  2) if pd.notna(atr_now)  else None,
        "atr_pct_rank" : round(atr_pct_rank, 1),
        "ret_1y_pct"   : round(ret_1y, 1) if ret_1y is not None else None,
        "ret_3m_pct"   : round(ret_3m, 1) if ret_3m is not None else None,
        "ytd_ret_pct"  : round(ytd_ret, 1),
        "year_high"    : year_high,
        "year_low"     : year_low,
        "ath_5y"       : ath,
        "atl_5y"       : atl,
        "data_rows"    : len(df),
    }


def _unknown() -> dict:
    return {
        "regime": "UNKNOWN", "vol": "NORMAL_VOL", "bias_score": 0,
        "latest_close": None, "sma50": None, "sma200": None,
        "atr14": None, "atr_pct_rank": 50.0,
        "ret_1y_pct": None, "ret_3m_pct": None, "ytd_ret_pct": 0.0,
        "year_high": None, "year_low": None, "ath_5y": None, "atl_5y": None,
        "data_rows": 0,
    }


# ── symbol → index key mapping ────────────────────────────────────────────────
_SYMBOL_TO_INDEX: dict[str, str] = {
    "NIFTY"      : "nifty50",
    "BANKNIFTY"  : "banknifty",
    "FINNIFTY"   : "finnifty",
    "MIDCPNIFTY" : "midcpnifty",
}

def _index_for_symbol(symbol: str) -> str:
    sym = (symbol or "").upper().replace("NSE:", "").replace("-INDEX", "")
    # strip futures suffix e.g. NIFTY26JUNFUT → NIFTY
    import re
    sym = re.sub(r"\d{2}[A-Z]{3}FUT$", "", sym)
    sym = re.sub(r"\d{2}[A-Z]{3}\d+[CP]E$", "", sym)
    for k, v in _SYMBOL_TO_INDEX.items():
        if k in sym:
            return v
    return "nifty50"


def get_regime(index: str = "nifty50") -> dict:
    """
    Returns current regime dict for the given index. Cached per calendar day.
    Gracefully returns UNKNOWN if CSV data is not loaded yet.
    """
    today = date.today().isoformat()
    if _cache_dates.get(index) == today and index in _cache:
        return _cache[index]

    try:
        df = load_daily(index)
        result = _compute_regime(df) if not df.empty else _unknown()
    except Exception:
        result = _unknown()

    _cache[index]       = result
    _cache_dates[index] = today
    return result


def get_regime_for_symbol(symbol: str) -> dict:
    """Convenience: look up regime by trading symbol (e.g. 'BANKNIFTY26JUNFUT')."""
    return get_regime(_index_for_symbol(symbol))


def regime_score_adjustment(direction: str, index: str = "nifty50") -> int:
    """
    Score delta (+1, 0, -1) for a setup based on regime alignment.
    direction: 'BULLISH' or 'BEARISH'
    """
    bias = get_regime(index).get("bias_score", 0)
    if direction == "BULLISH":
        return bias
    elif direction == "BEARISH":
        return -bias
    return 0


def regime_score_adjustment_for_symbol(direction: str, symbol: str) -> int:
    """Convenience wrapper using trading symbol instead of index key."""
    return regime_score_adjustment(direction, _index_for_symbol(symbol))


def format_regime_summary(index: str = "nifty50") -> str:
    r = get_regime(index)
    label = index.upper()
    if r["regime"] == "UNKNOWN":
        return f"{label} Regime: UNKNOWN (no historical data loaded)"

    arrow = {"STRONG_BULL": "↑↑", "WEAK_BULL": "↑", "SIDEWAYS": "→",
             "WEAK_BEAR": "↓", "STRONG_BEAR": "↓↓"}.get(r["regime"], "?")
    vol_tag = " ⚡HIGH-VOL" if r["vol"] == "HIGH_VOL" else (" 😴LOW-VOL" if r["vol"] == "LOW_VOL" else "")

    ret_1y = f"1Y {r['ret_1y_pct']:+.1f}%" if r["ret_1y_pct"] is not None else ""
    ytd    = f"YTD {r['ytd_ret_pct']:+.1f}%"
    sma200 = f"SMA200 {r['sma200']}" if r["sma200"] else ""
    atr    = f"ATR14 {r['atr14']} (pct {r['atr_pct_rank']:.0f})" if r["atr14"] else ""

    parts = [p for p in [ret_1y, ytd, sma200, atr] if p]
    return f"{label} Regime: {r['regime']} {arrow}{vol_tag} | {' | '.join(parts)}"


def all_regime_summary() -> str:
    """One-liner per index for all loaded indices."""
    lines = []
    for idx in ("nifty50", "banknifty", "finnifty", "midcpnifty"):
        r = get_regime(idx)
        if r["regime"] != "UNKNOWN":
            lines.append(format_regime_summary(idx))
    return "\n".join(lines) if lines else "No historical data loaded for any index."
