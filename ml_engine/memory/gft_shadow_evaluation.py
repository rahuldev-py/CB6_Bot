from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


RECO_PATH = Path("gft_shadow_recommendations.jsonl")
FOREX_MEMORY_PATH = Path("memory") / "forex" / "memory_v1.json"


@dataclass
class EvalConfig:
    min_samples: int = 20
    starting_equity: float = 5000.0
    daily_loss_limit_abs: float = 200.0
    max_drawdown_abs: float = 500.0
    match_window_minutes: int = 360


def _parse_dt(s: Any) -> Optional[datetime]:
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


def _load_recommendations() -> List[Dict[str, Any]]:
    if not RECO_PATH.exists():
        return []
    out = []
    with open(RECO_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("market", "")).lower() != "forex":
                continue
            row["_ts"] = _parse_dt(row.get("ts_utc"))
            out.append(row)
    out.sort(key=lambda x: x.get("_ts") or datetime.min.replace(tzinfo=timezone.utc))
    return out


def _load_forex_closed_trades() -> List[Dict[str, Any]]:
    if not FOREX_MEMORY_PATH.exists():
        return []
    try:
        data = json.loads(FOREX_MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    events = data.get("events", [])
    out = []
    for ev in events if isinstance(events, list) else []:
        meta = ev.get("metadata") or {}
        if str(meta.get("event_type", "")).lower() != "trade_closed":
            continue
        result = str(ev.get("result", "")).upper()
        if result not in {"WIN", "LOSS", "BREAKEVEN"}:
            continue
        entry_t = _parse_dt(ev.get("entry_time")) or _parse_dt(meta.get("entry_time"))
        exit_t = _parse_dt(ev.get("exit_time")) or _parse_dt(meta.get("exit_time")) or _parse_dt(ev.get("created_at_utc"))
        if exit_t is None:
            continue
        rr = _safe_float(ev.get("rr_achieved"), 0.0)
        pnl = _safe_float(meta.get("pnl_usd"), 0.0)
        if abs(pnl) < 1e-12:
            pnl = rr * 100.0
        out.append(
            {
                "event": ev,
                "trade_id": str(ev.get("trade_id") or ev.get("event_id") or ""),
                "symbol": str(ev.get("symbol") or "UNKNOWN"),
                "setup_key": "|".join(
                    [
                        f"regime={(ev.get('setup_dna') or {}).get('regime', 'UNKNOWN')}",
                        f"session={(ev.get('setup_dna') or {}).get('session', 'UNKNOWN')}",
                        f"sweep={(ev.get('setup_dna') or {}).get('sweep_type', 'UNKNOWN')}",
                        f"fvg={(ev.get('setup_dna') or {}).get('fvg_bucket', 'UNKNOWN')}",
                        f"htf={(ev.get('setup_dna') or {}).get('htf_bias', 'UNKNOWN')}",
                    ]
                ),
                "result": result,
                "rr": rr,
                "pnl": pnl,
                "entry_time": entry_t,
                "exit_time": exit_t,
            }
        )
    out.sort(key=lambda x: x["exit_time"])
    return out


def _time_distance_minutes(a: Optional[datetime], b: Optional[datetime]) -> float:
    if a is None or b is None:
        return 10**9
    return abs((a - b).total_seconds()) / 60.0


def _join_reco_to_trade(
    recos: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    *,
    match_window_minutes: int,
) -> List[Dict[str, Any]]:
    used = set()
    joined = []
    for r in recos:
        r_sym = str(r.get("symbol") or "UNKNOWN")
        r_key = str(r.get("setup_key") or "")
        r_ts = r.get("_ts")
        best_idx = None
        best_score = 10**9
        for i, t in enumerate(trades):
            if i in used:
                continue
            if t["symbol"] != r_sym:
                continue
            # Prefer setup key match when available.
            key_penalty = 0 if (r_key and t.get("setup_key") == r_key) else 1000
            d = _time_distance_minutes(r_ts, t.get("entry_time") or t.get("exit_time"))
            score = key_penalty + d
            if score < best_score and d <= match_window_minutes:
                best_score = score
                best_idx = i
        if best_idx is None:
            continue
        used.add(best_idx)
        t = trades[best_idx]
        joined.append(
            {
                "recommendation": str(r.get("recommendation", "UNKNOWN")).upper(),
                "confidence": _safe_float(r.get("confidence"), 0.0),
                "reason_codes": r.get("reason_codes") or [],
                "risk_notes": r.get("risk_notes") or [],
                "symbol": t["symbol"],
                "result": t["result"],
                "rr": t["rr"],
                "pnl": t["pnl"],
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "setup_key": t.get("setup_key"),
            }
        )
    joined.sort(key=lambda x: x.get("exit_time") or datetime.min.replace(tzinfo=timezone.utc))
    return joined


def _winrate(rows: List[Dict[str, Any]]) -> float:
    wins = sum(1 for x in rows if x["result"] == "WIN")
    losses = sum(1 for x in rows if x["result"] == "LOSS")
    return round((wins / (wins + losses) * 100.0), 2) if (wins + losses) > 0 else 0.0


def _avg_rr(rows: List[Dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    vals = [_safe_float(x.get("rr"), 0.0) for x in rows]
    return round(sum(vals) / len(vals), 3)


def _curve_metrics(rows: List[Dict[str, Any]], cfg: EvalConfig) -> Dict[str, Any]:
    equity = cfg.starting_equity
    peak = equity
    max_dd = 0.0
    daily: Dict[str, float] = {}
    daily_loss_breaches = 0
    for x in rows:
        pnl = _safe_float(x.get("pnl"), 0.0)
        dt = x.get("exit_time")
        day = dt.date().isoformat() if isinstance(dt, datetime) else "UNKNOWN"
        daily[day] = daily.get(day, 0.0) + pnl
        if daily[day] < -abs(cfg.daily_loss_limit_abs):
            daily_loss_breaches += 1
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "ending_equity": round(equity, 2),
        "net_profit": round(equity - cfg.starting_equity, 2),
        "max_drawdown": round(max_dd, 2),
        "daily_loss_breaches": daily_loss_breaches,
        "drawdown_limit_breached": max_dd > abs(cfg.max_drawdown_abs),
    }


def _metrics_by_recommendation(joined: List[Dict[str, Any]], cfg: EvalConfig) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for rec in ("ALLOW", "CAUTION", "REJECT"):
        rows = [x for x in joined if x["recommendation"] == rec]
        out[rec] = {
            "sample_size": len(rows),
            "win_rate": _winrate(rows),
            "avg_rr": _avg_rr(rows),
            **_curve_metrics(rows, cfg),
        }
    return out


def evaluate_shadow_recommendations(cfg: EvalConfig) -> Dict[str, Any]:
    recos = _load_recommendations()
    trades = _load_forex_closed_trades()
    joined = _join_reco_to_trade(recos, trades, match_window_minutes=cfg.match_window_minutes)

    baseline = joined
    skip_reject = [x for x in joined if x["recommendation"] != "REJECT"]
    allow_only = [x for x in joined if x["recommendation"] == "ALLOW"]

    false_rejects = [x for x in joined if x["recommendation"] == "REJECT" and x["result"] == "WIN"]
    false_allows = [x for x in joined if x["recommendation"] == "ALLOW" and x["result"] == "LOSS"]

    by_rec = _metrics_by_recommendation(joined, cfg)
    base_m = {
        "sample_size": len(baseline),
        "win_rate": _winrate(baseline),
        "avg_rr": _avg_rr(baseline),
        **_curve_metrics(baseline, cfg),
    }
    skip_m = {
        "sample_size": len(skip_reject),
        "win_rate": _winrate(skip_reject),
        "avg_rr": _avg_rr(skip_reject),
        **_curve_metrics(skip_reject, cfg),
    }
    allow_m = {
        "sample_size": len(allow_only),
        "win_rate": _winrate(allow_only),
        "avg_rr": _avg_rr(allow_only),
        **_curve_metrics(allow_only, cfg),
    }

    sample_ok = len(joined) >= cfg.min_samples
    improved = (
        sample_ok
        and skip_m["win_rate"] >= base_m["win_rate"]
        and skip_m["max_drawdown"] <= base_m["max_drawdown"]
    )
    verdict = (
        "LIKELY_IMPROVING_GFT_PASS_PROBABILITY"
        if improved
        else "NOT_YET_PROVEN_TO_IMPROVE_GFT_PASS_PROBABILITY"
    )
    if not sample_ok:
        verdict = "INSUFFICIENT_SAMPLE_TO_ASSESS_GFT_PASS_IMPROVEMENT"

    return {
        "focus_market": "forex",
        "input_counts": {
            "recommendations_total": len(recos),
            "closed_trades_total": len(trades),
            "joined_pairs": len(joined),
            "unmatched_recommendations": max(0, len(recos) - len(joined)),
        },
        "minimum_sample_guard": {
            "required": cfg.min_samples,
            "actual": len(joined),
            "passed": sample_ok,
        },
        "metrics_by_recommendation": by_rec,
        "scenario_baseline_all_joined": base_m,
        "scenario_skip_reject": skip_m,
        "scenario_allow_only": allow_m,
        "false_rejects_count": len(false_rejects),
        "false_allows_count": len(false_allows),
        "false_rejects_examples": false_rejects[:20],
        "false_allows_examples": false_allows[:20],
        "answer": {
            "question": "Is the shadow recommendation engine actually improving GFT pass probability?",
            "verdict": verdict,
            "notes": [
                f"Baseline vs skip-REJECT win rate: {base_m['win_rate']}% -> {skip_m['win_rate']}%",
                f"Baseline vs skip-REJECT max drawdown: {base_m['max_drawdown']} -> {skip_m['max_drawdown']}",
                f"ALLOW-only sample size: {allow_m['sample_size']}",
            ],
        },
    }

