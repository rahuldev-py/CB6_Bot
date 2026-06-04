"""
Conviction Weight Recommender — CB6 Quantum Phase 7
Analyzes live conviction-scored trades and recommends weight adjustments.
DOES NOT auto-adjust. All output is advisory only.
Human approval required before any weight change.

Auto-adjustment gates:
  - 500+ live conviction-scored trades
  - Multiple market cycles observed
  - Stable profitability (WR >= 55%, avg R >= 1.0)

Usage:
    python weight_recommendation.py              # full analysis
    python weight_recommendation.py --account FTMO
    python weight_recommendation.py --export weights.json
"""

import argparse
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "cb6_trades.db"

# Current weights (must match utils/conviction_engine.py WEIGHTS)
CURRENT_WEIGHTS = {
    "technical":   25,
    "regime":      25,
    "session":     15,
    "correlation": 10,
    "oi_flow":     10,
    "macro":       10,
    "sector":       5,
}

# Minimum trades before any recommendation is meaningful
MIN_TRADES_FOR_RECOMMENDATION = 100
MIN_TRADES_FOR_AUTO_GATE      = 500


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ComponentLift:
    component:  str
    current_wt: int
    high_wr:    Optional[float]   # WR when component score >= 70
    low_wr:     Optional[float]   # WR when component score < 70
    high_avg_r: Optional[float]
    low_avg_r:  Optional[float]
    lift_wr:    Optional[float]   # high_wr - low_wr
    lift_r:     Optional[float]   # high_avg_r - low_avg_r
    n_high:     int = 0
    n_low:      int = 0
    verdict:    str = "INSUFFICIENT_DATA"


@dataclass
class WeightRecommendation:
    component:     str
    current_weight: int
    suggested_weight: int
    delta:         int           # suggested - current
    confidence:    str           # HIGH | MODERATE | LOW
    reason:        str
    evidence:      dict


@dataclass
class WeightReport:
    generated_at:       str
    account_filter:     str
    trades_analyzed:    int
    data_quality:       str
    component_lifts:    list[ComponentLift]
    recommendations:    list[WeightRecommendation]
    current_weights:    dict
    suggested_weights:  dict
    auto_adjust_gate:   dict      # gates that must pass before auto-adjust
    overall_verdict:    str


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _connect():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_trades(account: str = None) -> list[dict]:
    with _connect() as conn:
        where_parts = [
            "t.result IS NOT NULL",
            "c.conviction_score IS NOT NULL",
            "c.conviction_components IS NOT NULL",
        ]
        params = []
        if account:
            where_parts.append("t.account = ?")
            params.append(account)

        where = "WHERE " + " AND ".join(where_parts)
        rows = conn.execute(f"""
            SELECT
                t.trade_id, t.result, t.pnl_usd, t.r_multiple,
                t.session, t.mss_type,
                c.conviction_score, c.conviction_grade,
                c.conviction_components, c.regime_4h, c.volatility_at_entry
            FROM trades t
            JOIN trade_context c ON t.trade_id = c.trade_id
            {where}
            ORDER BY t.entry_time ASC
        """, params).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Component lift computation
# ---------------------------------------------------------------------------

def compute_component_lifts(trades: list[dict]) -> list[ComponentLift]:
    """
    For each component, split trades into high (>=70) vs low (<70) score
    and compare WR + avg R.
    """
    lifts = []
    for comp in CURRENT_WEIGHTS:
        high_wins, high_r, high_n = [], [], 0
        low_wins,  low_r,  low_n  = [], [], 0

        for t in trades:
            raw = t.get("conviction_components")
            if not raw:
                continue
            try:
                comps = json.loads(raw)
            except Exception:
                continue
            val   = float(comps.get(comp, 0))
            is_win = 1.0 if t["result"] == "WIN" else 0.0
            r_val  = float(t["r_multiple"] or 0)

            if val >= 70:
                high_wins.append(is_win)
                high_r.append(r_val)
                high_n += 1
            else:
                low_wins.append(is_win)
                low_r.append(r_val)
                low_n += 1

        high_wr = round(sum(high_wins)/high_n*100, 1) if high_n >= 3 else None
        low_wr  = round(sum(low_wins) /low_n *100, 1) if low_n  >= 3 else None
        high_ar = round(sum(high_r)/high_n, 3)         if high_n >= 3 else None
        low_ar  = round(sum(low_r) /low_n,  3)         if low_n  >= 3 else None
        lift_wr = round(high_wr - low_wr, 1)            if (high_wr is not None and low_wr is not None) else None
        lift_r  = round(high_ar - low_ar, 3)            if (high_ar is not None and low_ar is not None) else None

        # Verdict
        if high_n < 3 or low_n < 3:
            verdict = "INSUFFICIENT_DATA"
        elif lift_wr is not None and lift_wr >= 15:
            verdict = "HIGH_IMPACT"
        elif lift_wr is not None and lift_wr >= 7:
            verdict = "MODERATE_IMPACT"
        elif lift_wr is not None and lift_wr <= -5:
            verdict = "NEGATIVE_IMPACT"
        else:
            verdict = "LOW_IMPACT"

        lifts.append(ComponentLift(
            component=comp,
            current_wt=CURRENT_WEIGHTS[comp],
            high_wr=high_wr,
            low_wr=low_wr,
            high_avg_r=high_ar,
            low_avg_r=low_ar,
            lift_wr=lift_wr,
            lift_r=lift_r,
            n_high=high_n,
            n_low=low_n,
            verdict=verdict,
        ))

    return sorted(lifts, key=lambda x: x.lift_wr or -999, reverse=True)


# ---------------------------------------------------------------------------
# Weight recommendation algorithm
# ---------------------------------------------------------------------------

def generate_recommendations(
    lifts: list[ComponentLift],
    n_trades: int,
) -> tuple[list[WeightRecommendation], dict]:
    """
    Generate weight adjustment suggestions.
    Uses proportional rebalancing:
    - HIGH_IMPACT components get +5 weight
    - NEGATIVE_IMPACT components get -3 weight
    - Remaining budget redistributed to LOW_IMPACT
    - Total must sum to 100
    """
    recs: list[WeightRecommendation] = []
    suggested = dict(CURRENT_WEIGHTS)  # copy

    # Confidence based on sample size
    if n_trades >= MIN_TRADES_FOR_AUTO_GATE:
        confidence = "HIGH"
    elif n_trades >= MIN_TRADES_FOR_RECOMMENDATION:
        confidence = "MODERATE"
    else:
        confidence = "LOW"

    delta_map = {}
    for cl in lifts:
        if cl.verdict == "INSUFFICIENT_DATA":
            continue
        if cl.verdict == "HIGH_IMPACT" and n_trades >= 50:
            delta_map[cl.component] = +5
        elif cl.verdict == "NEGATIVE_IMPACT" and n_trades >= 50:
            delta_map[cl.component] = -3
        # LOW_IMPACT and MODERATE_IMPACT: no change yet

    # Apply deltas
    raw_suggested = {}
    for comp, wt in suggested.items():
        delta = delta_map.get(comp, 0)
        raw_suggested[comp] = max(2, wt + delta)   # floor at 2

    # Rescale to sum 100
    total = sum(raw_suggested.values())
    scale = 100.0 / total if total > 0 else 1.0
    for comp in raw_suggested:
        raw_suggested[comp] = max(2, round(raw_suggested[comp] * scale))

    # Final nudge to ensure exactly 100
    diff = 100 - sum(raw_suggested.values())
    if diff != 0:
        biggest = max(raw_suggested, key=raw_suggested.get)
        raw_suggested[biggest] += diff

    suggested = raw_suggested

    # Build recommendation objects
    for cl in lifts:
        delta = suggested[cl.component] - CURRENT_WEIGHTS[cl.component]
        if delta == 0 and cl.verdict == "INSUFFICIENT_DATA":
            continue

        evidence = {
            "lift_wr":   cl.lift_wr,
            "lift_r":    cl.lift_r,
            "n_high":    cl.n_high,
            "n_low":     cl.n_low,
            "high_wr":   cl.high_wr,
            "low_wr":    cl.low_wr,
        }

        if cl.verdict == "INSUFFICIENT_DATA":
            reason = f"Insufficient data (n_high={cl.n_high}, n_low={cl.n_low}) — no change"
        elif cl.verdict == "HIGH_IMPACT":
            reason = f"High lift WR={cl.lift_wr:+.1f}% → increase weight"
        elif cl.verdict == "NEGATIVE_IMPACT":
            reason = f"Negative lift WR={cl.lift_wr:+.1f}% → reduce weight"
        elif cl.verdict == "MODERATE_IMPACT":
            reason = f"Moderate lift WR={cl.lift_wr:+.1f}% → no change yet (need {MIN_TRADES_FOR_RECOMMENDATION} trades)"
        else:
            reason = f"Low impact lift WR={cl.lift_wr:+.1f}% — keep current weight"

        recs.append(WeightRecommendation(
            component=cl.component,
            current_weight=CURRENT_WEIGHTS[cl.component],
            suggested_weight=suggested[cl.component],
            delta=delta,
            confidence=confidence,
            reason=reason,
            evidence=evidence,
        ))

    return recs, suggested


# ---------------------------------------------------------------------------
# Auto-adjust gate check
# ---------------------------------------------------------------------------

def check_auto_adjust_gates(trades: list[dict]) -> dict:
    n    = len(trades)
    wins = sum(1 for t in trades if t["result"] == "WIN")
    wr   = round(wins/n*100, 1) if n else 0.0
    r_vals = [float(t["r_multiple"] or 0) for t in trades if t["r_multiple"]]
    avg_r  = round(sum(r_vals)/len(r_vals), 3) if r_vals else 0.0

    gate_500       = n >= MIN_TRADES_FOR_AUTO_GATE
    gate_wr        = wr >= 55.0
    gate_avg_r     = avg_r >= 1.0
    gate_profitable= sum(float(t["pnl_usd"] or 0) for t in trades) > 0

    all_passed = all([gate_500, gate_wr, gate_avg_r, gate_profitable])

    return {
        "auto_adjust_allowed": all_passed,
        "gates": {
            "500_trades":    {"passed": gate_500,  "value": n, "required": 500},
            "win_rate_55":   {"passed": gate_wr,   "value": wr, "required": 55.0},
            "avg_r_1.0":     {"passed": gate_avg_r,"value": avg_r, "required": 1.0},
            "profitable":    {"passed": gate_profitable, "value": True},
        },
        "message": (
            "ALL GATES PASSED — auto-adjust PERMITTED (still requires human sign-off)"
            if all_passed else
            f"GATES NOT MET — {sum(1 for g in [gate_500,gate_wr,gate_avg_r,gate_profitable] if not g)}/4 failing"
        ),
    }


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------

def build_weight_report(account: str = None) -> WeightReport:
    from datetime import datetime, timezone

    trades = load_trades(account)
    n      = len(trades)

    data_quality = (
        "INSUFFICIENT"  if n < 20    else
        "WEAK"          if n < 100   else
        "MODERATE"      if n < 500   else
        "STRONG"
    )

    lifts    = compute_component_lifts(trades)
    recs, suggested = generate_recommendations(lifts, n)
    gates    = check_auto_adjust_gates(trades)

    if n < MIN_TRADES_FOR_RECOMMENDATION:
        verdict = (f"EARLY STAGE ({n} trades). Collecting data. "
                   f"Need {MIN_TRADES_FOR_RECOMMENDATION} for reliable recommendations.")
    elif not any(r.delta != 0 for r in recs):
        verdict = "WEIGHTS VALIDATED — no changes needed at this time"
    else:
        changes = [r for r in recs if r.delta != 0]
        verdict = (f"{len(changes)} weight adjustment(s) suggested. "
                   f"Review and approve before applying.")

    return WeightReport(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        account_filter=account or "ALL",
        trades_analyzed=n,
        data_quality=data_quality,
        component_lifts=lifts,
        recommendations=recs,
        current_weights=dict(CURRENT_WEIGHTS),
        suggested_weights=suggested,
        auto_adjust_gate=gates,
        overall_verdict=verdict,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_report(rep: WeightReport):
    print(f"\n=== CB6 Quantum — Conviction Weight Recommender ===")
    print(f"Trades analyzed: {rep.trades_analyzed}  |  Data quality: {rep.data_quality}")
    print(f"Account: {rep.account_filter}")
    print(f"\nVERDICT: {rep.overall_verdict}")

    print(f"\n{'─'*70}")
    print("COMPONENT LIFT ANALYSIS")
    print(f"{'Component':<14} {'CurWt':>5} {'HighWR':>7} {'LowWR':>7} "
          f"{'Lift':>6} {'nH':>4} {'nL':>4} {'Impact'}")
    print("─" * 70)
    for cl in rep.component_lifts:
        h_wr  = f"{cl.high_wr:.1f}%" if cl.high_wr is not None else "n/a"
        l_wr  = f"{cl.low_wr:.1f}%"  if cl.low_wr  is not None else "n/a"
        lift  = f"{cl.lift_wr:+.1f}%"if cl.lift_wr is not None else "n/a"
        print(f"{cl.component:<14} {cl.current_wt:>5}  "
              f"{h_wr:>7} {l_wr:>7} {lift:>6}  "
              f"{cl.n_high:>4} {cl.n_low:>4}  {cl.verdict}")

    print(f"\n{'─'*70}")
    print("WEIGHT RECOMMENDATIONS  (advisory — requires human approval)")
    print(f"{'Component':<14} {'Current':>8} {'Suggested':>9} {'Delta':>6}  Reason")
    print("─" * 70)
    for r in rep.recommendations:
        delta_str = f"{r.delta:+d}" if r.delta != 0 else "  0"
        flag      = " ←" if r.delta != 0 else ""
        print(f"{r.component:<14} {r.current_weight:>8}  "
              f"{r.suggested_weight:>9}  {delta_str:>5}{flag}  {r.reason}")

    print(f"\nCurrent total:   {sum(rep.current_weights.values())}")
    print(f"Suggested total: {sum(rep.suggested_weights.values())}")

    print(f"\n{'─'*70}")
    print("AUTO-ADJUST GATES")
    gates = rep.auto_adjust_gate
    for gate, info in gates["gates"].items():
        status = "✓ PASS" if info["passed"] else "✗ FAIL"
        print(f"  {gate:<20} {status}  (value={info['value']}, required={info['required']})")
    print(f"\n  {gates['message']}")

    if not gates["auto_adjust_allowed"]:
        print(f"\n  ⚠ DO NOT apply these weights automatically until all gates pass.")
        print(f"  To apply manually: edit WEIGHTS dict in utils/conviction_engine.py")
        print(f"  and verify sum = 100 before deploying.")


def main():
    parser = argparse.ArgumentParser(description="CB6 Conviction Weight Recommender")
    parser.add_argument("--account", type=str, default=None,
                        help="Filter by account (FTMO, GFT_5K, NSE_LIVE, etc.)")
    parser.add_argument("--export", type=str, default=None,
                        help="Export recommendations to JSON file")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] Database not found: {DB_PATH}")
        print("Run the bot first to generate conviction-scored trade data.")
        return

    report = build_weight_report(account=args.account)
    _print_report(report)

    if args.export:
        out = Path(args.export)
        data = {
            "generated_at":      report.generated_at,
            "account_filter":    report.account_filter,
            "trades_analyzed":   report.trades_analyzed,
            "data_quality":      report.data_quality,
            "verdict":           report.overall_verdict,
            "current_weights":   report.current_weights,
            "suggested_weights": report.suggested_weights,
            "recommendations":   [
                {
                    "component":        r.component,
                    "current_weight":   r.current_weight,
                    "suggested_weight": r.suggested_weight,
                    "delta":            r.delta,
                    "confidence":       r.confidence,
                    "reason":           r.reason,
                    "evidence":         r.evidence,
                }
                for r in report.recommendations
            ],
            "auto_adjust_gate":  report.auto_adjust_gate,
            "component_lifts": [
                {
                    "component":  cl.component,
                    "current_wt": cl.current_wt,
                    "high_wr":    cl.high_wr,
                    "low_wr":     cl.low_wr,
                    "lift_wr":    cl.lift_wr,
                    "lift_r":     cl.lift_r,
                    "n_high":     cl.n_high,
                    "n_low":      cl.n_low,
                    "verdict":    cl.verdict,
                }
                for cl in report.component_lifts
            ],
        }
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"\nReport exported to {out}")


if __name__ == "__main__":
    main()
