from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


MARKETS = ("nse", "forex", "futures", "crypto")


@dataclass
class GFTChallengeRules:
    starting_equity: float = 5000.0
    daily_loss_limit_abs: float = 200.0
    max_drawdown_abs: float = 500.0
    profit_target_abs: float = 400.0
    max_trades_per_day: int = 5
    min_quality_score: float = 0.0
    min_memory_score: float = 0.0
    allowed_regimes: Optional[List[str]] = None
    allowed_setup_keys: Optional[List[str]] = None
    min_trades_required: int = 20


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _market_events(market: str) -> List[Dict[str, Any]]:
    m = str(market).strip().lower()
    if m not in MARKETS:
        raise ValueError(f"Unsupported market '{market}'. Expected one of: {', '.join(MARKETS)}")
    path = Path("memory") / m / "memory_v1.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    events = data.get("events", [])
    return events if isinstance(events, list) else []


def _setup_key(ev: Dict[str, Any]) -> str:
    dna = ev.get("setup_dna") or {}
    return "|".join(
        [
            f"regime={dna.get('regime', 'UNKNOWN')}",
            f"session={dna.get('session', 'UNKNOWN')}",
            f"sweep={dna.get('sweep_type', 'UNKNOWN')}",
            f"fvg={dna.get('fvg_bucket', 'UNKNOWN')}",
            f"htf={dna.get('htf_bias', 'UNKNOWN')}",
        ]
    )


def _extract_closed_trades(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for ev in events:
        meta = ev.get("metadata") or {}
        if str(meta.get("event_type", "")).lower() != "trade_closed":
            continue
        result = str(ev.get("result", "")).upper()
        if result not in {"WIN", "LOSS", "BREAKEVEN"}:
            continue
        t_exit = _parse_dt(str(ev.get("exit_time", ""))) or _parse_dt(str(ev.get("created_at_utc", "")))
        if t_exit is None:
            continue

        rr = ev.get("rr_achieved")
        rr_f = _safe_float(rr, 0.0)
        pnl = _safe_float(meta.get("pnl_usd"), 0.0)
        if abs(pnl) < 1e-12:
            # Fallback synthetic PnL proxy when currency pnl is unavailable.
            # Keeps simulation usable without touching live systems.
            pnl = rr_f * 100.0

        out.append(
            {
                "event": ev,
                "exit_dt": t_exit,
                "exit_day": t_exit.date().isoformat(),
                "result": result,
                "rr_achieved": rr_f,
                "pnl": pnl,
                "quality_score": _safe_float(ev.get("ml_score"), 0.0),
                "memory_score": _safe_float(meta.get("memory_score"), 0.0),
                "regime": str((ev.get("setup_dna") or {}).get("regime", "UNKNOWN")).upper(),
                "setup_key": _setup_key(ev),
                "symbol": str(ev.get("symbol") or "UNKNOWN"),
                "session": str(ev.get("session") or "UNKNOWN"),
            }
        )
    out.sort(key=lambda x: x["exit_dt"])
    return out


def simulate_gft_challenge(
    market: str,
    rules: GFTChallengeRules,
) -> Dict[str, Any]:
    events = _market_events(market)
    trades = _extract_closed_trades(events)

    violations: List[Dict[str, Any]] = []
    skip_reasons: Dict[str, int] = {}
    accepted: List[Dict[str, Any]] = []

    for t in trades:
        if t["quality_score"] < rules.min_quality_score:
            skip_reasons["quality_threshold"] = skip_reasons.get("quality_threshold", 0) + 1
            continue
        if t["memory_score"] < rules.min_memory_score:
            skip_reasons["memory_score_threshold"] = skip_reasons.get("memory_score_threshold", 0) + 1
            continue
        if rules.allowed_regimes and t["regime"] not in {x.upper() for x in rules.allowed_regimes}:
            skip_reasons["regime_filter"] = skip_reasons.get("regime_filter", 0) + 1
            continue
        if rules.allowed_setup_keys and t["setup_key"] not in set(rules.allowed_setup_keys):
            skip_reasons["setup_dna_filter"] = skip_reasons.get("setup_dna_filter", 0) + 1
            continue
        accepted.append(t)

    equity = rules.starting_equity
    peak_equity = equity
    worst_drawdown = 0.0
    pnl_by_day: Dict[str, float] = {}
    trades_by_day: Dict[str, int] = {}

    for t in accepted:
        d = t["exit_day"]
        trades_by_day[d] = trades_by_day.get(d, 0) + 1
        pnl_by_day[d] = pnl_by_day.get(d, 0.0) + t["pnl"]

        if trades_by_day[d] > rules.max_trades_per_day:
            violations.append(
                {
                    "rule": "max_trades_per_day",
                    "day": d,
                    "value": trades_by_day[d],
                    "limit": rules.max_trades_per_day,
                    "severity": trades_by_day[d] - rules.max_trades_per_day,
                    "symbol": t["symbol"],
                }
            )

        if pnl_by_day[d] < -abs(rules.daily_loss_limit_abs):
            breach = abs(pnl_by_day[d]) - abs(rules.daily_loss_limit_abs)
            violations.append(
                {
                    "rule": "daily_loss_limit",
                    "day": d,
                    "value": pnl_by_day[d],
                    "limit": -abs(rules.daily_loss_limit_abs),
                    "severity": breach,
                    "symbol": t["symbol"],
                }
            )

        equity += t["pnl"]
        peak_equity = max(peak_equity, equity)
        dd = peak_equity - equity
        worst_drawdown = max(worst_drawdown, dd)
        if dd > abs(rules.max_drawdown_abs):
            violations.append(
                {
                    "rule": "max_drawdown_limit",
                    "day": d,
                    "value": dd,
                    "limit": abs(rules.max_drawdown_abs),
                    "severity": dd - abs(rules.max_drawdown_abs),
                    "symbol": t["symbol"],
                }
            )

    net_profit = equity - rules.starting_equity
    target_met = net_profit >= abs(rules.profit_target_abs)
    if not target_met:
        violations.append(
            {
                "rule": "profit_target",
                "day": None,
                "value": net_profit,
                "limit": abs(rules.profit_target_abs),
                "severity": abs(rules.profit_target_abs) - net_profit,
                "symbol": None,
            }
        )

    hard_fail = any(v["rule"] in {"daily_loss_limit", "max_drawdown_limit", "max_trades_per_day"} for v in violations)
    low_sample = len(accepted) < max(1, rules.min_trades_required)

    if hard_fail:
        verdict = "FAIL"
        fail_reason = "Hard rule violation(s) occurred"
    elif not target_met:
        verdict = "AT_RISK"
        fail_reason = "Profit target not reached"
    elif low_sample:
        verdict = "AT_RISK"
        fail_reason = "Insufficient accepted sample size"
    else:
        verdict = "PASS"
        fail_reason = ""

    by_rule: Dict[str, Dict[str, Any]] = {}
    for v in violations:
        r = v["rule"]
        cur = by_rule.setdefault(r, {"count": 0, "max_severity": 0.0})
        cur["count"] += 1
        cur["max_severity"] = max(cur["max_severity"], _safe_float(v.get("severity"), 0.0))

    ranked = sorted(
        [{"rule": r, **meta} for r, meta in by_rule.items()],
        key=lambda x: (-x["count"], -x["max_severity"], x["rule"]),
    )

    return {
        "market": market,
        "priority_market": market,
        "rules": rules.__dict__,
        "summary": {
            "total_closed_trades": len(trades),
            "accepted_trades": len(accepted),
            "skipped_trades": len(trades) - len(accepted),
            "skip_reasons": skip_reasons,
            "starting_equity": rules.starting_equity,
            "ending_equity": round(equity, 2),
            "net_profit": round(net_profit, 2),
            "worst_drawdown": round(worst_drawdown, 2),
            "target_met": target_met,
            "verdict": verdict,
            "fail_reason": fail_reason,
            "low_sample": low_sample,
        },
        "best_rule_compliance": ranked[-3:] if ranked else [],
        "worst_rule_violations": ranked[:5],
        "violations": violations[:200],
    }

