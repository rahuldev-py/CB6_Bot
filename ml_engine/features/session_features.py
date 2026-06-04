"""
ml_engine/features/session_features.py

Session and time-of-day features derived from entry_time.
Encodes kill zones, Silver Bullet windows, day-of-week, and DTE.
"""

from __future__ import annotations
import numpy as np
import pandas as pd


# NSE Silver Bullet windows (IST minutes from midnight)
_SB_WINDOWS = [(10 * 60, 11 * 60), (13 * 60 + 30, 14 * 60 + 30)]

# Forex kill zone windows (UTC hours)
_FOREX_KZ = [(7, 12), (16, 20)]
_FOREX_PRIME_KZ = set(range(7, 10)) | set(range(16, 18))

_SESSION_MAP = {
    "morning silver bullet"  : 0,
    "morning sb"             : 0,
    "afternoon silver bullet": 1,
    "afternoon sb"           : 1,
    "london"                 : 2,
    "london sb"              : 2,
    "ny"                     : 3,
    "ny am sb"               : 3,
    "ny pm sb"               : 4,
    "london_ny_overlap"      : 5,
    "all hours"              : 6,
}


def _in_sb_window(minute_of_day: pd.Series) -> pd.Series:
    result = pd.Series(False, index=minute_of_day.index)
    for start, end in _SB_WINDOWS:
        result |= (minute_of_day >= start) & (minute_of_day < end)
    return result.astype(float)


def _is_prime_kz(utc_hour: pd.Series) -> pd.Series:
    return utc_hour.isin(_FOREX_PRIME_KZ).astype(float)


def add_session_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # Parse entry_time
    time_col = next((c for c in ["entry_time", "date"] if c in out.columns), None)
    if time_col:
        ts = pd.to_datetime(out[time_col], errors="coerce")
    else:
        ts = pd.Series(pd.NaT, index=out.index)

    # Hour and minute
    out["hour_of_day"]   = ts.dt.hour.astype(float)
    out["minute_of_day"] = (ts.dt.hour * 60 + ts.dt.minute).astype(float)
    out["day_of_week"]   = ts.dt.dayofweek.astype(float)  # 0=Mon, 4=Fri

    # Sin/cos encoding for hour (cyclical)
    out["hour_sin"] = np.sin(2 * np.pi * out["hour_of_day"] / 24)
    out["hour_cos"] = np.cos(2 * np.pi * out["hour_of_day"] / 24)
    out["dow_sin"]  = np.sin(2 * np.pi * out["day_of_week"] / 5)
    out["dow_cos"]  = np.cos(2 * np.pi * out["day_of_week"] / 5)

    # NSE Silver Bullet window (IST)
    is_nse = out["engine"].str.lower().eq("nse") if "engine" in out.columns else pd.Series(False, index=out.index)
    out["in_sb_window"] = np.where(
        is_nse, _in_sb_window(out["minute_of_day"]), np.nan
    )
    out["minutes_into_window"] = np.where(
        (out["in_sb_window"] == 1),
        out["minute_of_day"] - _SB_WINDOWS[0][0],
        np.nan
    )

    # Forex kill zone (UTC — approximation, entry_time may be UTC or local)
    is_forex = ~is_nse
    hour = out["hour_of_day"]
    forex_kz = pd.Series(False, index=out.index)
    for s, e in _FOREX_KZ:
        forex_kz |= (hour >= s) & (hour < e)
    out["in_forex_kz"]    = np.where(is_forex, forex_kz.astype(float), np.nan)
    out["in_prime_kz"]    = np.where(is_forex, _is_prime_kz(hour.fillna(0).astype(int)), np.nan)

    # Session label as ordinal
    if "session" in out.columns:
        out["session_ord"] = out["session"].str.lower().map(_SESSION_MAP).fillna(6).astype(float)
    elif "window" in out.columns:
        out["session_ord"] = out["window"].str.lower().map(_SESSION_MAP).fillna(6).astype(float)
    else:
        out["session_ord"] = np.nan

    # Days-to-expiry (NSE options)
    if "dte" in out.columns:
        out["dte_num"] = pd.to_numeric(out["dte"], errors="coerce")
        # DTE bucket: 0=expiry, 1=1DTE, 2+=normal
        out["dte_bucket"] = out["dte_num"].clip(upper=2).fillna(2).astype(float)
    else:
        out["dte_num"]    = np.nan
        out["dte_bucket"] = 2.0

    # Is Friday (expiry risk elevated)
    out["is_friday"] = (out["day_of_week"] == 4).astype(float)

    return out


SESSION_FEATURE_COLS = [
    "hour_of_day", "minute_of_day", "day_of_week",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "in_sb_window", "minutes_into_window",
    "in_forex_kz", "in_prime_kz",
    "session_ord", "dte_num", "dte_bucket", "is_friday",
]
