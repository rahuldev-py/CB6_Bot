from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from settings import CB6_GFT_SOFT_GATE_ENABLED
from utils.state_io import file_lock

from ml_engine.memory.analytics import (
    query_best_worst_setup_dna,
    query_win_rate_by_regime,
    query_win_rate_by_session,
    query_win_rate_by_symbol,
)


OUT_PATH = Path("gft_soft_gate_decisions.jsonl")

# Analytics results are stable between trade closes (every few hours at most).
# Cache them for 60 s to avoid repeated file reads on every 15-s poll cycle.
_analytics_cache: Dict[str, Any] = {}
_ANALYTICS_TTL: float = 60.0


def _cached_analytics(key: str, fn, *args, **kwargs) -> Any:
    now = time.monotonic()
    entry = _analytics_cache.get(key)
    if entry is not None:
        val, ts = entry
        if now - ts < _ANALYTICS_TTL:
            return val
    result = fn(*args, **kwargs)
    _analytics_cache[key] = (result, now)
    return result


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _setup_key(setup: Dict[str, Any]) -> str:
    fvg = setup.get("fvg") or {}
    pd = setup.get("premium_discount") or {}
    return "|".join(
        [
            f"regime={str(setup.get('regime', 'UNKNOWN')).upper()}",
            f"session={setup.get('window') or setup.get('session') or 'UNKNOWN'}",
            f"sweep={((setup.get('liq_sweep') or {}).get('sweep_type') or 'UNKNOWN')}",
            f"fvg={fvg.get('bucket') or ('DISPLACED' if fvg.get('displacement') else 'PLAIN')}",
            f"htf={pd.get('zone') or 'UNKNOWN'}",
        ]
    )


def _quality_score(setup: Dict[str, Any]) -> float:
    sweep_q = _safe_float(setup.get("sweep_confidence"), 0.0)
    mss_q = _safe_float((setup.get("mss") or {}).get("strength"), 0.0)
    fvg_q = 100.0 if (setup.get("fvg") or {}).get("displacement") else 50.0
    base = _safe_float(setup.get("confluence"), 0.0) * 6.5
    return round((base + sweep_q + mss_q + fvg_q) / 4.0, 2)


def _estimate_memory_score(market: str, setup_key: str, min_samples: int = 10, _prof=None) -> float:
    prof = _prof if _prof is not None else query_best_worst_setup_dna(market, min_samples=min_samples, top_n=300)
    for row in prof.get("best", []) + prof.get("worst", []):
        if row.get("setup_key") == setup_key:
            wr = _safe_float(row.get("win_rate"), 50.0)
            n = _safe_float(row.get("sample_size"), 0.0)
            return round(max(0.0, min(100.0, wr + min(20.0, n / 5.0) - 30.0)), 2)
    return 50.0


def _find_wr(table: list, key: str) -> Optional[float]:
    for row in table:
        if str(row.get("key")) == str(key):
            return _safe_float(row.get("win_rate"))
    return None


def _daily_loss_remaining(state: Dict[str, Any], daily_loss_limit_abs: float) -> float:
    daily_pnl = _safe_float(state.get("daily_pnl"), 0.0)
    used = max(0.0, -daily_pnl)
    return round(max(0.0, daily_loss_limit_abs - used), 2)


def _max_dd_remaining(state: Dict[str, Any], max_drawdown_abs: float) -> float:
    start = _safe_float(state.get("starting_capital"), 5000.0)
    cap = _safe_float(state.get("capital"), start)
    used = max(0.0, start - cap)
    return round(max(0.0, max_drawdown_abs - used), 2)


def evaluate_soft_gate_and_log(
    *,
    setup: Dict[str, Any],
    state: Dict[str, Any],
    engine: str,
    market: str = "forex",
    daily_loss_limit_abs: float = 200.0,
    max_drawdown_abs: float = 500.0,
    max_trades_per_day: int = 6,
    projected_risk_usd: float = 0.0,
) -> Optional[Dict[str, Any]]:
    if not CB6_GFT_SOFT_GATE_ENABLED:
        return None
    try:
        symbol = str(setup.get("symbol") or "UNKNOWN")
        session = str(setup.get("window") or setup.get("session") or "UNKNOWN")
        regime = str(setup.get("regime") or "UNKNOWN").upper()
        setup_key = _setup_key(setup)

        _prof = _cached_analytics(f"bw_{market}", query_best_worst_setup_dna, market, min_samples=10, top_n=300)
        memory_score = _estimate_memory_score(market, setup_key, _prof=_prof)
        wr_symbol = _find_wr(_cached_analytics(f"wrs_{market}", query_win_rate_by_symbol, market, min_samples=10), symbol)
        wr_session = _find_wr(_cached_analytics(f"wse_{market}", query_win_rate_by_session, market, min_samples=10), session)
        wr_regime = _find_wr(_cached_analytics(f"wrr_{market}", query_win_rate_by_regime, market, min_samples=10), regime)

        regime_score = 50.0 if wr_regime is None else wr_regime
        setup_dna_score = memory_score
        quality_score = _quality_score(setup)

        daily_remaining = _daily_loss_remaining(state, daily_loss_limit_abs)
        dd_remaining = _max_dd_remaining(state, max_drawdown_abs)
        trade_count_today = int(state.get("daily_trades", 0) or 0)
        # Use caller-supplied projected risk when available (accurate).
        # Fallback to setup extraction only if the caller didn't compute it
        # (risk_usd in entry_signal is set after lot calc, so is 0 in shadow paths).
        projected_risk = projected_risk_usd if projected_risk_usd > 0.0 else _safe_float(
            (setup.get("entry_signal") or {}).get("risk_usd"), 0.0
        )

        reason_codes = []
        gate_score = 0.0

        if daily_remaining <= projected_risk:
            reason_codes.append("DAILY_LOSS_BUFFER_INSUFFICIENT")
            gate_score -= 45
        elif daily_remaining <= daily_loss_limit_abs * 0.2:
            reason_codes.append("DAILY_LOSS_BUFFER_TIGHT")
            gate_score -= 20

        if dd_remaining <= projected_risk:
            reason_codes.append("DRAWDOWN_BUFFER_INSUFFICIENT")
            gate_score -= 40
        elif dd_remaining <= max_drawdown_abs * 0.2:
            reason_codes.append("DRAWDOWN_BUFFER_TIGHT")
            gate_score -= 18

        if trade_count_today >= max_trades_per_day:
            reason_codes.append("TRADE_COUNT_LIMIT_REACHED")
            gate_score -= 35
        elif trade_count_today >= max_trades_per_day - 1:
            reason_codes.append("TRADE_COUNT_NEAR_LIMIT")
            gate_score -= 12

        if quality_score >= 70:
            gate_score += 10
        else:
            reason_codes.append("LOW_STRUCTURE_QUALITY")
            gate_score -= 8

        if memory_score >= 65:
            gate_score += 8
        else:
            reason_codes.append("LOW_MEMORY_SCORE")
            gate_score -= 8

        for code, wr in (("WEAK_SYMBOL_EDGE", wr_symbol), ("WEAK_SESSION_EDGE", wr_session), ("WEAK_REGIME_EDGE", wr_regime)):
            if wr is None:
                reason_codes.append(code.replace("WEAK", "UNKNOWN"))
                gate_score -= 2
            elif wr < 45:
                reason_codes.append(code)
                gate_score -= 8
            elif wr >= 60:
                gate_score += 5
            else:
                gate_score -= 1

        if gate_score >= 12:
            gate_decision = "ALLOW"
            would_allow = True
        elif gate_score >= -12:
            gate_decision = "CAUTION"
            would_allow = True
        else:
            gate_decision = "BLOCK"
            would_allow = False

        confidence = round(max(5.0, min(95.0, 50.0 + abs(gate_score))), 1)

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "market": market,
            "engine": engine,
            "symbol": symbol,
            "would_allow": would_allow,
            "gate_decision": gate_decision,
            "confidence": confidence,
            "reason_codes": sorted(set(reason_codes)),
            "rule_snapshot": {
                "daily_loss_limit_abs": daily_loss_limit_abs,
                "max_drawdown_abs": max_drawdown_abs,
                "max_trades_per_day": max_trades_per_day,
            },
            "memory_score": memory_score,
            "regime_score": round(regime_score, 2),
            "setup_dna_score": round(setup_dna_score, 2),
            "daily_loss_remaining": daily_remaining,
            "max_drawdown_remaining": dd_remaining,
            "trade_count_today": trade_count_today,
            "projected_risk": projected_risk,
            "quality_scores": {
                "overall_quality": quality_score,
                "sweep_quality": _safe_float(setup.get("sweep_confidence"), 0.0),
                "mss_quality": _safe_float((setup.get("mss") or {}).get("strength"), 0.0),
                "fvg_quality": 100.0 if (setup.get("fvg") or {}).get("displacement") else 50.0,
            },
            "what_would_happen_under_challenge_mode": (
                "WOULD_ALLOW" if would_allow else "WOULD_BLOCK"
            ),
        }

        with file_lock(str(OUT_PATH), timeout=5.0):
            with open(OUT_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str) + "\n")
                f.flush()
        return payload
    except Exception:
        return None

