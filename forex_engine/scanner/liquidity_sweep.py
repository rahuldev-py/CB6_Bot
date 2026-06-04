"""Stateful liquidity level and sweep detection for CB6 Forex.

This module is a safety/scoring layer around the existing Silver Bullet flow:
it does not create entries by itself.  It identifies active buy-side/sell-side
liquidity, marks levels as swept/violated/expired, scores sweep quality, and
logs each unique sweep for later ML review.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from utils.logger import logger


STATE_ACTIVE = "ACTIVE"
STATE_SWEPT = "SWEPT"
STATE_VIOLATED = "VIOLATED"
STATE_EXPIRED = "EXPIRED"

BUY_SIDE = "BUY_SIDE"
SELL_SIDE = "SELL_SIDE"

SWEEP_LOG_FILE = os.path.join("data", "ml", "forex", "sweep_events.jsonl")

_LEVEL_STATE: dict[str, dict[str, dict]] = {}
_LOGGED_SWEEPS: set[str] = set()


def _num(row, key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default))
    except Exception:
        return default


def _row_time(row, fallback_index: int) -> str:
    for key in ("time", "datetime", "timestamp", "date"):
        val = row.get(key) if hasattr(row, "get") else None
        if val is not None and str(val) not in ("", "nan", "NaT"):
            return str(val)
    return str(fallback_index)


def _level_id(symbol: str, timeframe: str, side: str, level: float) -> str:
    return f"{symbol or 'UNKNOWN'}:{timeframe}:{side}:{round(level, 5)}"


def _state_bucket(symbol: str, timeframe: str) -> dict[str, dict]:
    key = f"{symbol or 'UNKNOWN'}:{timeframe}"
    return _LEVEL_STATE.setdefault(key, {})


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.fillna(df["high"] - df["low"])


def _session_score(hour_utc: int) -> int:
    if 7 <= hour_utc < 10 or 16 <= hour_utc < 18:
        return 100
    if 10 <= hour_utc < 12 or 18 <= hour_utc < 20:
        return 70
    if 12 <= hour_utc < 16:
        return 35
    return 10


def _candle_hour_utc(row) -> int:
    raw = None
    for key in ("time", "datetime", "timestamp", "date"):
        raw = row.get(key) if hasattr(row, "get") else None
        if raw is not None and str(raw) not in ("", "nan", "NaT"):
            break
    if raw is None:
        return datetime.now(timezone.utc).hour
    try:
        ts = pd.to_datetime(raw, utc=True)
        return int(ts.hour)
    except Exception:
        return datetime.now(timezone.utc).hour


def detect_liquidity_levels(
    df: pd.DataFrame,
    symbol: str = "",
    timeframe: str = "15m",
    lookback: int = 80,
    sweep_window: int = 20,
    swing_window: int = 3,
) -> list[dict]:
    """Return current internal/external buy-side and sell-side liquidity levels."""
    if df is None or len(df) < max(20, swing_window * 2 + 3):
        return []

    recent = df.tail(lookback).reset_index(drop=True)
    structure_end = max(len(recent) - sweep_window, swing_window * 2 + 1)
    structure = recent.iloc[:structure_end].reset_index(drop=True)
    if len(structure) < swing_window * 2 + 3:
        return []

    highest = float(structure["high"].max())
    lowest = float(structure["low"].min())
    levels: list[dict] = []

    for i in range(swing_window, len(structure) - swing_window):
        window = structure.iloc[i - swing_window : i + swing_window + 1]
        row = structure.iloc[i]
        high = _num(row, "high")
        low = _num(row, "low")
        candles_ago = len(recent) - 1 - i

        if high == float(window["high"].max()):
            levels.append(
                {
                    "id": _level_id(symbol, timeframe, BUY_SIDE, high),
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "side": BUY_SIDE,
                    "level_type": "EXTERNAL" if high == highest else "INTERNAL",
                    "level": high,
                    "source_index": i,
                    "source_time": _row_time(row, i),
                    "candles_ago": candles_ago,
                    "state": STATE_ACTIVE,
                }
            )

        if low == float(window["low"].min()):
            levels.append(
                {
                    "id": _level_id(symbol, timeframe, SELL_SIDE, low),
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "side": SELL_SIDE,
                    "level_type": "EXTERNAL" if low == lowest else "INTERNAL",
                    "level": low,
                    "source_index": i,
                    "source_time": _row_time(row, i),
                    "candles_ago": candles_ago,
                    "state": STATE_ACTIVE,
                }
            )

    deduped: dict[str, dict] = {}
    for level in levels:
        prev = deduped.get(level["id"])
        if prev is None or level["candles_ago"] < prev["candles_ago"]:
            deduped[level["id"]] = level
    return list(deduped.values())


def _score_sweep(
    recent: pd.DataFrame,
    sweep_pos: int,
    level: dict,
    direction: str,
    wick_extension: float,
) -> dict:
    row = recent.iloc[sweep_pos]
    high = _num(row, "high")
    low = _num(row, "low")
    opn = _num(row, "open")
    cls = _num(row, "close")
    vol = _num(row, "volume")

    candle_range = max(high - low, 1e-9)
    body = abs(cls - opn)
    upper_wick = max(high - max(opn, cls), 0.0)
    lower_wick = max(min(opn, cls) - low, 0.0)
    sweep_wick = upper_wick if level["side"] == BUY_SIDE else lower_wick
    wick_ratio = max(wick_extension, sweep_wick) / candle_range
    body_rejection = 1.0 - min(body / candle_range, 1.0)

    avg_vol = float(recent["volume"].iloc[:sweep_pos].tail(20).mean()) if "volume" in recent else 0.0
    volume_spike = (vol / avg_vol) if avg_vol > 0 else 1.0

    tr = _true_range(recent)
    atr = float(tr.iloc[:sweep_pos].tail(14).mean()) if sweep_pos > 1 else float(tr.tail(14).mean())
    atr_expansion = candle_range / atr if atr > 0 else 1.0

    follow = recent.iloc[sweep_pos + 1 : min(len(recent), sweep_pos + 4)]
    if len(follow) and direction == "BEARISH":
        displacement = max(float(row["close"]) - float(follow["low"].min()), 0.0)
    elif len(follow):
        displacement = max(float(follow["high"].max()) - float(row["close"]), 0.0)
    else:
        displacement = 0.0
    displacement_ratio = displacement / candle_range

    hour = _candle_hour_utc(row)
    scores = {
        "wick_ratio_score": min(100, int(wick_ratio * 160)),
        "body_rejection_score": min(100, int(body_rejection * 100)),
        "volume_spike_score": min(100, int(volume_spike * 45)),
        "atr_expansion_score": min(100, int(atr_expansion * 55)),
        "session_score": _session_score(hour),
        "displacement_score": min(100, int(displacement_ratio * 80)),
    }
    confidence = int(round(
        scores["wick_ratio_score"] * 0.25
        + scores["body_rejection_score"] * 0.15
        + scores["volume_spike_score"] * 0.15
        + scores["atr_expansion_score"] * 0.15
        + scores["session_score"] * 0.10
        + scores["displacement_score"] * 0.20
    ))
    scores.update(
        {
            "confidence": max(0, min(100, confidence)),
            "wick_ratio": round(float(wick_ratio), 4),
            "volume_spike": round(float(volume_spike), 4),
            "atr_expansion": round(float(atr_expansion), 4),
            "displacement_ratio": round(float(displacement_ratio), 4),
            "session_hour_utc": hour,
        }
    )
    return scores


def _mark_level(level: dict, state: str, reason: str, candle: Optional[dict] = None) -> dict:
    updated = dict(level)
    updated["state"] = state
    updated["state_reason"] = reason
    updated["state_time"] = (
        _row_time(candle, updated.get("source_index", 0)) if candle is not None else datetime.now(timezone.utc).isoformat()
    )
    return updated


def _log_sweep_event(event: dict) -> None:
    event_id = event.get("event_id")
    if not event_id or event_id in _LOGGED_SWEEPS:
        return
    try:
        os.makedirs(os.path.dirname(SWEEP_LOG_FILE), exist_ok=True)
        with open(SWEEP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
        _LOGGED_SWEEPS.add(event_id)
    except Exception as exc:
        logger.debug(f"sweep event log skipped: {exc}")


def analyze_liquidity_state(
    df: pd.DataFrame,
    symbol: str = "",
    timeframe: str = "15m",
    lookback: int = 80,
    sweep_window: int = 20,
    min_wick_ratio: float = 0.12,
    expiry_candles: int = 160,
) -> dict:
    """Build/update liquidity state and return active targets plus latest sweep."""
    levels = detect_liquidity_levels(df, symbol, timeframe, lookback, sweep_window)
    bucket = _state_bucket(symbol, timeframe)
    recent = df.tail(lookback).reset_index(drop=True)
    sweep_start = max(len(recent) - sweep_window, 0)

    current_close = float(recent["close"].iloc[-1]) if len(recent) else 0.0
    events: list[dict] = []
    invalidated: list[dict] = []

    for raw_level in levels:
        level = dict(bucket.get(raw_level["id"], raw_level))
        if level.get("state") == STATE_SWEPT:
            bucket[level["id"]] = level
            continue
        if int(raw_level.get("candles_ago", 0)) > expiry_candles:
            level = _mark_level(raw_level, STATE_EXPIRED, "LEVEL_TOO_OLD")
            bucket[level["id"]] = level
            invalidated.append(level)
            continue

        violated = False
        for i in range(sweep_start, len(recent)):
            row = recent.iloc[i]
            cls = _num(row, "close")
            prev_cls = _num(recent.iloc[i - 1], "close") if i > 0 else cls
            level_price = float(raw_level["level"])
            if raw_level["side"] == BUY_SIDE and cls > level_price and prev_cls > level_price:
                violated = True
                level = _mark_level(raw_level, STATE_VIOLATED, "TRUE_BREAKOUT_ABOVE_BUY_SIDE", row)
                break
            if raw_level["side"] == SELL_SIDE and cls < level_price and prev_cls < level_price:
                violated = True
                level = _mark_level(raw_level, STATE_VIOLATED, "TRUE_BREAKDOWN_BELOW_SELL_SIDE", row)
                break
        if violated:
            bucket[level["id"]] = level
            invalidated.append(level)
            continue

        for i in range(sweep_start, len(recent)):
            row = recent.iloc[i]
            high = _num(row, "high")
            low = _num(row, "low")
            cls = _num(row, "close")
            level_price = float(raw_level["level"])
            candles_ago = len(recent) - 1 - i

            if raw_level["side"] == BUY_SIDE and high > level_price and cls < level_price:
                wick_ratio = (high - level_price) / max(high - low, 1e-9)
                if wick_ratio < min_wick_ratio:
                    continue
                scores = _score_sweep(recent, i, raw_level, "BEARISH", high - level_price)
                event = {
                    **_mark_level(raw_level, STATE_SWEPT, "WICK_SWEEP_CLOSE_BACK_INSIDE", row),
                    "event_id": f"{raw_level['id']}:{_row_time(row, i)}:HIGH_SWEEP",
                    "direction": "BEARISH",
                    "sweep_type": "HIGH_SWEEP",
                    "swept_level": level_price,
                    "candles_ago": candles_ago,
                    "wick_extreme": high,
                    "close_back_inside": True,
                    "wick_ratio_valid": True,
                    "quality": scores,
                }
                level = event
                events.append(event)
                _log_sweep_event(event)
                break

            if raw_level["side"] == SELL_SIDE and low < level_price and cls > level_price:
                wick_ratio = (level_price - low) / max(high - low, 1e-9)
                if wick_ratio < min_wick_ratio:
                    continue
                scores = _score_sweep(recent, i, raw_level, "BULLISH", level_price - low)
                event = {
                    **_mark_level(raw_level, STATE_SWEPT, "WICK_SWEEP_CLOSE_BACK_INSIDE", row),
                    "event_id": f"{raw_level['id']}:{_row_time(row, i)}:LOW_SWEEP",
                    "direction": "BULLISH",
                    "sweep_type": "LOW_SWEEP",
                    "swept_level": level_price,
                    "candles_ago": candles_ago,
                    "wick_extreme": low,
                    "close_back_inside": True,
                    "wick_ratio_valid": True,
                    "quality": scores,
                }
                level = event
                events.append(event)
                _log_sweep_event(event)
                break

        bucket[level["id"]] = level

    active = [v for v in bucket.values() if v.get("state") == STATE_ACTIVE]
    swept = [v for v in bucket.values() if v.get("state") == STATE_SWEPT]
    violated = [v for v in bucket.values() if v.get("state") == STATE_VIOLATED]
    expired = [v for v in bucket.values() if v.get("state") == STATE_EXPIRED]

    buy_side = sorted(
        [v for v in active if v.get("side") == BUY_SIDE and float(v.get("level", 0)) >= current_close],
        key=lambda x: abs(float(x.get("level", 0)) - current_close),
    )
    sell_side = sorted(
        [v for v in active if v.get("side") == SELL_SIDE and float(v.get("level", 0)) <= current_close],
        key=lambda x: abs(float(x.get("level", 0)) - current_close),
    )

    latest = sorted(
        events or swept,
        key=lambda x: int(x.get("candles_ago", 999999)),
    )
    return {
        "active_levels": active,
        "swept_levels": swept,
        "violated_levels": violated,
        "expired_levels": expired,
        "invalidated_levels": invalidated,
        "active_buy_side_liquidity": buy_side[0] if buy_side else None,
        "active_sell_side_liquidity": sell_side[0] if sell_side else None,
        "next_buy_side_liquidity": buy_side[1] if len(buy_side) > 1 else None,
        "next_sell_side_liquidity": sell_side[1] if len(sell_side) > 1 else None,
        "last_sweep": latest[0] if latest else None,
    }


def detect_sweep(
    df: pd.DataFrame,
    lookback: int = 80,
    sweep_window: int = 20,
    symbol: str = "",
    timeframe: str = "15m",
    min_confidence: int = 0,
) -> Optional[dict]:
    """Detect the latest valid sweep and return the legacy-compatible sweep dict."""
    try:
        state = analyze_liquidity_state(
            df,
            symbol=symbol,
            timeframe=timeframe,
            lookback=lookback,
            sweep_window=sweep_window,
        )
        sweep = state.get("last_sweep")
        if not sweep:
            return None
        quality = sweep.get("quality", {})
        if int(quality.get("confidence", 0)) < min_confidence:
            return None
        return {
            "direction": sweep.get("direction"),
            "swept_level": sweep.get("swept_level", sweep.get("level")),
            "candles_ago": sweep.get("candles_ago", 999),
            "wick_extreme": sweep.get("wick_extreme"),
            "sweep_type": sweep.get("sweep_type"),
            "level_id": sweep.get("id"),
            "level_side": sweep.get("side"),
            "level_state": sweep.get("state"),
            "level_type": sweep.get("level_type"),
            "event_id": sweep.get("event_id"),
            "quality": quality,
            "confidence": quality.get("confidence", 0),
            "wick_ratio": quality.get("wick_ratio", 0.0),
            "volume_spike": quality.get("volume_spike", 0.0),
            "atr_expansion": quality.get("atr_expansion", 0.0),
            "displacement_ratio": quality.get("displacement_ratio", 0.0),
            "close_back_inside": sweep.get("close_back_inside", False),
        }
    except Exception as e:
        logger.error(f"detect_sweep error: {e}")
        return None


def sweep_confirmed(
    sweep: Optional[dict],
    direction: str,
    max_candles_ago: int = 15,
    min_confidence: int = 0,
) -> bool:
    """True if a fresh, one-level sweep aligns with trade direction."""
    if sweep is None:
        return False
    if sweep.get("level_state") not in (None, STATE_SWEPT):
        return False
    return (
        sweep.get("direction") == direction
        and sweep.get("candles_ago", 999) <= max_candles_ago
        and int(sweep.get("confidence", 0)) >= min_confidence
    )
