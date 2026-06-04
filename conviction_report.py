"""
Conviction Validation Report — CB6 Quantum Phase 7
Answers: Does higher conviction produce higher win rate and avg R?

Generates milestone reports at 50 / 100 / 250 / 500 conviction-scored trades.
Reads live data from cb6_trades.db (trade_context.conviction_score).

Usage:
    python conviction_report.py                  # auto-detect milestone
    python conviction_report.py --milestone 50   # force 50-trade report
    python conviction_report.py --account FTMO   # filter by account
    python conviction_report.py --export report.json
"""

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "cb6_trades.db"

MILESTONES = [50, 100, 250, 500]

# Conviction band boundaries  [min, max)
BANDS = [
    ("A+ (85-100)", 85.0, 100.1),
    ("A  (70-84)",  70.0,  85.0),
    ("B  (55-69)",  55.0,  70.0),
    ("C  (40-54)",  40.0,  55.0),
    ("D  (<40)",     0.0,  40.0),
]

COMPONENTS = ["technical", "regime", "session", "correlation", "oi_flow", "macro", "sector"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_conviction_trades(account: str = None, limit: int = 0) -> list[dict]:
    """
    Load all closed trades that have a conviction_score recorded.
    Joins trades + trade_context.
    """
    with _connect() as conn:
        where_parts = [
            "t.result IS NOT NULL",
            "c.conviction_score IS NOT NULL",
        ]
        params: list = []

        if account:
            where_parts.append("t.account = ?")
            params.append(account)

        where = "WHERE " + " AND ".join(where_parts)
        limit_clause = f"LIMIT {limit}" if limit > 0 else ""

        rows = conn.execute(f"""
            SELECT
                t.trade_id, t.account, t.market, t.symbol, t.direction,
                t.entry_time, t.session, t.mss_type, t.score AS ict_score,
                t.result, t.pnl_usd, t.r_multiple,
                t.sim_ratio, t.is_aplus,
                c.conviction_score, c.conviction_grade, c.conviction_risk_mult,
                c.conviction_components, c.conviction_reasons,
                c.regime_4h, c.volatility_at_entry, c.oi_bias,
                c.conviction_hard_block
            FROM trades t
            JOIN trade_context c ON t.trade_id = c.trade_id
            {where}
            ORDER BY t.entry_time ASC
            {limit_clause}
        """, params).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Band analysis
# ---------------------------------------------------------------------------

@dataclass
class BandStats:
    label:     str
    min_score: float
    max_score: float
    total:     int = 0
    wins:      int = 0
    losses:    int = 0
    be:        int = 0
    win_rate:  float = 0.0
    avg_r:     float = 0.0
    avg_pnl:   float = 0.0
    total_pnl: float = 0.0
    verdict:   str = "SAMPLE_TOO_SMALL"

    def compute(self, trades: list[dict]):
        band_trades = [
            t for t in trades
            if t["conviction_score"] is not None
            and self.min_score <= float(t["conviction_score"]) < self.max_score
        ]
        self.total  = len(band_trades)
        self.wins   = sum(1 for t in band_trades if t["result"] == "WIN")
        self.losses = sum(1 for t in band_trades if t["result"] == "LOSS")
        self.be     = sum(1 for t in band_trades if t["result"] == "BE")

        if self.total == 0:
            return

        self.win_rate  = round(self.wins / self.total * 100, 1)
        r_vals  = [float(t["r_multiple"] or 0) for t in band_trades]
        pnl_vals= [float(t["pnl_usd"]   or 0) for t in band_trades]
        self.avg_r    = round(sum(r_vals)   / self.total, 3)
        self.avg_pnl  = round(sum(pnl_vals) / self.total, 2)
        self.total_pnl= round(sum(pnl_vals), 2)

        if self.total < 5:
            self.verdict = "SAMPLE_TOO_SMALL"
        elif self.win_rate >= 60 and self.avg_r >= 1.0:
            self.verdict = "STRONG_EDGE"
        elif self.win_rate >= 55:
            self.verdict = "EDGE"
        elif self.win_rate <= 35:
            self.verdict = "AVOID"
        else:
            self.verdict = "NEUTRAL"

    def to_dict(self) -> dict:
        return {
            "band":      self.label,
            "score_range": f"{self.min_score:.0f}-{self.max_score:.0f}",
            "total":     self.total,
            "wins":      self.wins,
            "losses":    self.losses,
            "win_rate":  self.win_rate,
            "avg_r":     self.avg_r,
            "avg_pnl":   self.avg_pnl,
            "total_pnl": self.total_pnl,
            "verdict":   self.verdict,
        }


# ---------------------------------------------------------------------------
# Component contribution analysis
# ---------------------------------------------------------------------------

def component_win_correlation(trades: list[dict]) -> dict:
    """
    For each conviction component, split trades into high/low score and compare WR.
    High = component score >= 70.  Low = component score < 70.
    """
    result = {}
    for comp in COMPONENTS:
        high_wins, high_total = 0, 0
        low_wins,  low_total  = 0, 0

        for t in trades:
            raw = t.get("conviction_components")
            if not raw:
                continue
            try:
                comps = json.loads(raw)
            except Exception:
                continue
            val = float(comps.get(comp, 0))
            is_win = t["result"] == "WIN"
            if val >= 70:
                high_total += 1
                if is_win: high_wins += 1
            else:
                low_total += 1
                if is_win: low_wins += 1

        high_wr = round(high_wins / high_total * 100, 1) if high_total else None
        low_wr  = round(low_wins  / low_total  * 100, 1) if low_total  else None
        lift    = round(high_wr - low_wr, 1) if (high_wr is not None and low_wr is not None) else None

        result[comp] = {
            "high_score_wr": high_wr,
            "high_n":        high_total,
            "low_score_wr":  low_wr,
            "low_n":         low_total,
            "lift":          lift,
            "most_valuable": (lift is not None and lift >= 10),
        }

    # Sort by lift descending
    return dict(sorted(result.items(), key=lambda x: x[1]["lift"] or -999, reverse=True))


# ---------------------------------------------------------------------------
# Main report builder
# ---------------------------------------------------------------------------

def build_report(
    trades: list[dict],
    milestone: int,
    account: str = None,
) -> dict:
    """Build the full conviction validation report."""

    total = len(trades)

    if total == 0:
        return {"error": "No conviction-scored trades found", "milestone": milestone}

    wins  = sum(1 for t in trades if t["result"] == "WIN")
    losses= sum(1 for t in trades if t["result"] == "LOSS")
    be    = sum(1 for t in trades if t["result"] == "BE")

    overall_wr  = round(wins / total * 100, 1) if total else 0.0
    r_vals      = [float(t["r_multiple"] or 0) for t in trades]
    pnl_vals    = [float(t["pnl_usd"]   or 0) for t in trades]
    avg_r       = round(sum(r_vals)   / total, 3) if total else 0.0
    avg_pnl     = round(sum(pnl_vals) / total, 2) if total else 0.0
    total_pnl   = round(sum(pnl_vals), 2)

    # Conviction stats
    scores = [float(t["conviction_score"]) for t in trades if t["conviction_score"] is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    # Band breakdown
    band_stats = []
    for label, lo, hi in BANDS:
        bs = BandStats(label=label, min_score=lo, max_score=hi)
        bs.compute(trades)
        band_stats.append(bs.to_dict())

    # Monotonicity check: does higher conviction → higher WR?
    wrs_by_band = [b["win_rate"] for b in band_stats if b["total"] >= 3]
    monotonic = all(
        wrs_by_band[i] >= wrs_by_band[i+1]
        for i in range(len(wrs_by_band)-1)
    ) if len(wrs_by_band) >= 2 else None

    # Component contribution
    component_stats = component_win_correlation(trades)

    # Conviction vs outcome correlation (Pearson-ish: rank correlation)
    paired = [
        (float(t["conviction_score"]), 1.0 if t["result"] == "WIN" else 0.0)
        for t in trades
        if t["conviction_score"] is not None
    ]
    conviction_wr_corr = None
    if len(paired) >= 10:
        import statistics
        xs = [p[0] for p in paired]
        ys = [p[1] for p in paired]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        try:
            cov = sum((x-mx)*(y-my) for x,y in zip(xs,ys)) / len(xs)
            sx  = statistics.stdev(xs)
            sy  = statistics.stdev(ys)
            if sx > 0 and sy > 0:
                conviction_wr_corr = round(cov / (sx * sy), 3)
        except Exception:
            pass

    # Grade distribution
    grade_dist: dict[str, int] = {}
    for t in trades:
        g = t.get("conviction_grade") or "?"
        grade_dist[g] = grade_dist.get(g, 0) + 1

    # Top performing regime × conviction combos
    regime_bands: dict[str, dict] = {}
    for t in trades:
        regime = t.get("regime_4h") or "UNKNOWN"
        score  = float(t.get("conviction_score") or 0)
        band   = "HIGH" if score >= 70 else ("MID" if score >= 55 else "LOW")
        key    = f"{regime}:{band}"
        if key not in regime_bands:
            regime_bands[key] = {"total": 0, "wins": 0}
        regime_bands[key]["total"] += 1
        if t["result"] == "WIN":
            regime_bands[key]["wins"] += 1

    regime_analysis = []
    for key, stats in sorted(regime_bands.items(),
                              key=lambda x: x[1]["wins"] / max(x[1]["total"],1),
                              reverse=True):
        n   = stats["total"]
        w   = stats["wins"]
        wr  = round(w / n * 100, 1)
        regime_analysis.append({
            "regime_band": key,
            "total": n,
            "win_rate": wr,
            "verdict": "EDGE" if wr >= 60 and n >= 5 else
                       ("AVOID" if wr <= 35 and n >= 5 else "NEUTRAL"),
        })

    # Key questions answered
    qa = {
        "q1_higher_conviction_higher_wr": {
            "answer": "YES — conviction correlates with WR" if monotonic else
                      ("PARTIALLY" if monotonic is None else "NO — conviction does not linearly predict WR"),
            "monotonic_wr_bands": monotonic,
            "pearson_corr": conviction_wr_corr,
        },
        "q2_most_profitable_band": max(
            [b for b in band_stats if b["total"] >= 3],
            key=lambda x: x["avg_r"],
            default=None
        ),
        "q3_most_predictive_component": next(
            (k for k, v in component_stats.items() if v.get("most_valuable")), "insufficient_data"
        ),
        "q4_recommendation": _overall_recommendation(band_stats, conviction_wr_corr, total),
    }

    # Data sufficiency
    data_quality = "INSUFFICIENT" if total < 20 else (
                   "WEAK"    if total < milestone else
                   "USABLE"  if total < milestone * 2 else
                   "STRONG")

    return {
        "report_type":     f"CONVICTION_{milestone}_TRADE",
        "account_filter":  account or "ALL",
        "generated_at":    _now_iso(),
        "milestone":       milestone,
        "trades_available": total,
        "data_quality":    data_quality,
        "overview": {
            "total_trades":  total,
            "wins":          wins,
            "losses":        losses,
            "be":            be,
            "win_rate":      overall_wr,
            "avg_r":         avg_r,
            "avg_pnl":       avg_pnl,
            "total_pnl":     total_pnl,
            "avg_conviction_score": avg_score,
            "grade_distribution": grade_dist,
        },
        "band_analysis":       band_stats,
        "component_contribution": component_stats,
        "regime_conviction_matrix": regime_analysis,
        "key_questions":       qa,
    }


def _overall_recommendation(band_stats: list[dict], corr: Optional[float], total: int) -> str:
    if total < 20:
        return f"INSUFFICIENT DATA ({total} trades). Need 50+ conviction-scored trades."

    high_band = next((b for b in band_stats if "85" in b["band"]), None)
    if high_band and high_band["total"] >= 5:
        if high_band["win_rate"] >= 65:
            return (f"CONVICTION VALIDATED: A+ band WR={high_band['win_rate']}% "
                    f"— use 1.5× sizing for A+ trades")
        if high_band["win_rate"] <= 45:
            return (f"CONVICTION NOT VALIDATED: A+ band underperforming "
                    f"(WR={high_band['win_rate']}%) — review component weights")

    if corr and corr >= 0.2:
        return f"WEAK POSITIVE CORRELATION (r={corr}) — continue collecting data"
    if corr and corr < 0:
        return f"NEGATIVE CORRELATION (r={corr}) — conviction formula needs revision"

    return "NEUTRAL — 50+ A+/A grade trades needed for reliable verdict"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CB6 Conviction Validation Report")
    parser.add_argument("--milestone", type=int, choices=MILESTONES,
                        help="Force specific milestone (default: auto-detect)")
    parser.add_argument("--account", type=str, default=None,
                        help="Filter by account (FTMO, GFT_5K, NSE_LIVE, etc.)")
    parser.add_argument("--export", type=str, default=None,
                        help="Export report JSON to file path")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] Database not found: {DB_PATH}")
        print("Run the bot first to generate trade data.")
        return

    trades = load_conviction_trades(account=args.account)
    n      = len(trades)

    print(f"\n=== CB6 Quantum — Conviction Validation Report ===")
    print(f"Conviction-scored closed trades: {n}")
    if args.account:
        print(f"Account filter: {args.account}")

    if n == 0:
        print("\nNo conviction-scored trades yet. Trades need `setup` passed to")
        print("capture_entry_context() to enable conviction recording.")
        return

    # Auto-detect milestone
    milestone = args.milestone
    if milestone is None:
        milestone = next((m for m in MILESTONES if n >= m), MILESTONES[0])

    report = build_report(trades, milestone, account=args.account)

    # Print summary
    ov = report["overview"]
    print(f"\n{'─'*60}")
    print(f"OVERVIEW  ({ov['total_trades']} trades, WR={ov['win_rate']}%, "
          f"avgR={ov['avg_r']}, totalPnL=${ov['total_pnl']:+.2f})")
    print(f"Avg conviction score: {ov['avg_conviction_score']}")
    print(f"Grade distribution:   {ov['grade_distribution']}")

    print(f"\n{'─'*60}")
    print("BAND ANALYSIS")
    print(f"{'Band':<18} {'N':>4} {'WR%':>6} {'AvgR':>6} {'Verdict'}")
    print("─" * 56)
    for b in report["band_analysis"]:
        if b["total"] == 0:
            continue
        print(f"{b['band']:<18} {b['total']:>4} {b['win_rate']:>5.1f}% "
              f"{b['avg_r']:>6.3f} {b['verdict']}")

    print(f"\n{'─'*60}")
    print("COMPONENT CONTRIBUTION (lift = high_score_WR - low_score_WR)")
    for comp, stats in report["component_contribution"].items():
        lift = stats.get("lift")
        lift_str = f"{lift:+.1f}%" if lift is not None else "n/a"
        flag = " ★" if stats.get("most_valuable") else ""
        print(f"  {comp:<14} lift={lift_str:>7}{flag}")

    print(f"\n{'─'*60}")
    print("KEY QUESTIONS")
    qa = report["key_questions"]
    print(f"  Q1 Higher conviction → higher WR? {qa['q1_higher_conviction_higher_wr']['answer']}")
    q2 = qa.get("q2_most_profitable_band")
    if q2:
        print(f"  Q2 Most profitable band: {q2['band']} (avgR={q2['avg_r']}, WR={q2['win_rate']}%)")
    print(f"  Q3 Most predictive component: {qa['q3_most_predictive_component']}")
    print(f"\n  RECOMMENDATION: {qa['q4_recommendation']}")

    if args.export:
        out = Path(args.export)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nReport exported to {out}")


if __name__ == "__main__":
    main()
