from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from settings import CB6_GFT_SHADOW_RECOMMENDATION_ENABLED
from utils.state_io import file_lock

from ml_engine.memory.analytics import (
    query_best_worst_setup_dna,
    query_win_rate_by_regime,
    query_win_rate_by_session,
    query_win_rate_by_symbol,
)
from ml_engine.memory.gft_challenge_simulator import (
    GFTChallengeRules,
    simulate_gft_challenge,
)


OUT_PATH = Path("gft_shadow_recommendations.jsonl")


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


def _estimate_memory_score(market: str, setup_key: str, min_samples: int = 10) -> float:
    prof = query_best_worst_setup_dna(market, min_samples=min_samples, top_n=200)
    for row in prof.get("best", []) + prof.get("worst", []):
        if row.get("setup_key") == setup_key:
            wr = _safe_float(row.get("win_rate"), 50.0)
            n = _safe_float(row.get("sample_size"), 0.0)
            confidence_bonus = min(20.0, n / 5.0)
            return round(max(0.0, min(100.0, wr + confidence_bonus - 30.0)), 2)
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


def recommend_shadow_for_candidate(
    *,
    setup: Dict[str, Any],
    state: Dict[str, Any],
    engine: str,
    market: str = "forex",
    daily_loss_limit_abs: float = 200.0,
    max_drawdown_abs: float = 500.0,
    profit_target_abs: float = 400.0,
    max_trades_per_day: int = 5,
) -> Optional[Dict[str, Any]]:
    if not CB6_GFT_SHADOW_RECOMMENDATION_ENABLED:
        return None

    try:
        symbol = str(setup.get("symbol") or "UNKNOWN")
        session = str(setup.get("window") or setup.get("session") or "UNKNOWN")
        regime = str(setup.get("regime") or ((setup.get("setup_dna") or {}).get("regime")) or "UNKNOWN").upper()
        setup_key = _setup_key(setup)
        quality = _quality_score(setup)
        memory_score = _estimate_memory_score(market, setup_key)

        wr_symbol = _find_wr(query_win_rate_by_symbol(market, min_samples=10), symbol)
        wr_session = _find_wr(query_win_rate_by_session(market, min_samples=10), session)
        wr_regime = _find_wr(query_win_rate_by_regime(market, min_samples=10), regime)

        rules = GFTChallengeRules(
            starting_equity=_safe_float(state.get("starting_capital"), 10000.0),
            daily_loss_limit_abs=daily_loss_limit_abs,
            max_drawdown_abs=max_drawdown_abs,
            profit_target_abs=profit_target_abs,
            max_trades_per_day=max(1, int(max_trades_per_day)),
            min_quality_score=0.0,
            min_memory_score=0.0,
            min_trades_required=20,
        )
        sim = simulate_gft_challenge(market, rules)

        daily_remaining = _daily_loss_remaining(state, daily_loss_limit_abs)
        dd_remaining = _max_dd_remaining(state, max_drawdown_abs)
        trade_count_today = int(state.get("daily_trades", 0) or 0)

        reason_codes = []
        risk_notes = []
        score = 0.0

        if sim.get("summary", {}).get("verdict") == "FAIL":
            reason_codes.append("SIM_FAIL")
            score -= 45
        elif sim.get("summary", {}).get("verdict") == "AT_RISK":
            reason_codes.append("SIM_AT_RISK")
            score -= 20
        else:
            reason_codes.append("SIM_PASS")
            score += 15

        if daily_remaining <= daily_loss_limit_abs * 0.15:
            reason_codes.append("LOW_DAILY_LOSS_REMAINING")
            risk_notes.append("Very low daily loss buffer remaining.")
            score -= 35
        elif daily_remaining <= daily_loss_limit_abs * 0.30:
            reason_codes.append("TIGHT_DAILY_LOSS_REMAINING")
            score -= 15

        if dd_remaining <= max_drawdown_abs * 0.15:
            reason_codes.append("LOW_DD_REMAINING")
            risk_notes.append("Very low max drawdown buffer remaining.")
            score -= 30
        elif dd_remaining <= max_drawdown_abs * 0.30:
            reason_codes.append("TIGHT_DD_REMAINING")
            score -= 10

        if trade_count_today >= max_trades_per_day:
            reason_codes.append("TRADE_COUNT_LIMIT_REACHED")
            score -= 25
        elif trade_count_today >= max_trades_per_day - 1:
            reason_codes.append("TRADE_COUNT_NEAR_LIMIT")
            score -= 10

        if quality >= 70:
            score += 10
        else:
            reason_codes.append("LOW_QUALITY_SCORE")
            score -= 10

        if memory_score >= 65:
            score += 10
        else:
            reason_codes.append("LOW_MEMORY_SCORE")
            score -= 8

        for label, wr in (("SYMBOL_WR", wr_symbol), ("SESSION_WR", wr_session), ("REGIME_WR", wr_regime)):
            if wr is None:
                reason_codes.append(f"{label}_UNKNOWN")
                score -= 2
            elif wr >= 60:
                score += 6
            elif wr < 45:
                reason_codes.append(f"{label}_WEAK")
                score -= 8
            else:
                score -= 2

        if score >= 15:
            rec = "ALLOW"
        elif score >= -10:
            rec = "CAUTION"
        else:
            rec = "REJECT"

        confidence = round(max(5.0, min(95.0, 50.0 + abs(score))), 1)

        payload = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "market": market,
            "engine": engine,
            "symbol": symbol,
            "session": session,
            "regime": regime,
            "setup_key": setup_key,
            "quality_score": quality,
            "quality_components": {
                "sweep_quality": _safe_float(setup.get("sweep_confidence"), 0.0),
                "mss_quality": _safe_float((setup.get("mss") or {}).get("strength"), 0.0),
                "fvg_quality": 100.0 if (setup.get("fvg") or {}).get("displacement") else 50.0,
            },
            "memory_score": memory_score,
            "win_rate_context": {
                "symbol": wr_symbol,
                "session": wr_session,
                "regime": wr_regime,
            },
            "challenge_context": {
                "sim_verdict": sim.get("summary", {}).get("verdict"),
                "sim_fail_reason": sim.get("summary", {}).get("fail_reason"),
                "daily_loss_remaining": daily_remaining,
                "max_drawdown_remaining": dd_remaining,
                "trade_count_today": trade_count_today,
                "max_trades_per_day": max_trades_per_day,
                "what_would_happen_under_challenge_mode": (
                    f"{sim.get('summary', {}).get('verdict')} | "
                    f"{sim.get('summary', {}).get('fail_reason') or 'No hard-block reason'}"
                ),
            },
            "recommendation": rec,
            "confidence": confidence,
            "reason_codes": sorted(set(reason_codes)),
            "risk_notes": risk_notes,
        }

        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(str(OUT_PATH), timeout=5.0):
            with open(OUT_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str) + "\n")
                f.flush()
        return payload
    except Exception:
        return None
