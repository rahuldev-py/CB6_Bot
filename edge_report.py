"""
CB6 Quantum — Edge Attribution Report
Answers: which conditions make CB6 win, and which make it lose?

Run from project root:
  python edge_report.py                    # all sources, full report
  python edge_report.py --source live      # live trades only
  python edge_report.py --source backtest  # backtest data only
  python edge_report.py --dim regime_4h    # single dimension deep-dive
  python edge_report.py --rules            # rule suggestions only
  python edge_report.py --backfill         # compute MFE/MAE for existing trades first
  python edge_report.py --save             # save to reports/edge_YYYYMMDD.txt
"""

import sys
import os
import argparse
from datetime import datetime
from pathlib import Path
import pytz

sys.path.insert(0, str(Path(__file__).parent))

IST = pytz.timezone("Asia/Kolkata")
NOW = datetime.now(IST)
SEP  = "─" * 72
SEP2 = "═" * 72

VERDICT_ICONS = {
    "EDGE":              "✓ EDGE",
    "AVOID":             "✗ AVOID",
    "NEUTRAL":           "~ NEUTRAL",
    "SAMPLE_TOO_SMALL":  "? SMALL",
}

ACTION_ICONS = {
    "BLOCK":       "[BLOCK]",
    "REDUCE_LOT":  "[REDUCE]",
    "ALLOW":       "[ALLOW]",
    "WATCH":       "[WATCH]",
}


def _conf_color(conf: str) -> str:
    return {"HIGH": "HIGH ", "MEDIUM": "MED  ", "LOW": "LOW  ", "INSUFFICIENT": "INSUF"}.get(conf, conf[:5])


def _fmt_stats_row(s) -> str:
    v = VERDICT_ICONS.get(s.verdict, s.verdict)
    return (
        f"  {s.value:<22} {s.total:>6}  "
        f"{s.win_rate:>6.1f}%  {s.avg_r:>+7.3f}R  "
        f"{_conf_color(s.confidence)}  {v}"
    )


def _section_summary(engine) -> list[str]:
    lines = []
    lines.append(f"\n{'TRADE UNIVERSE SUMMARY':^72}")
    lines.append(SEP)
    for src_filter, label in [
        ("live",        "Live trades (real money)"),
        ("backtest_nse","Backtest NSE"),
        ("backtest_forex","Backtest Forex"),
    ]:
        s = engine.summary(src_filter)
        if s["total"] == 0:
            continue
        lines.append(
            f"  {label:<24}  n={s['total']:>5}  WR={s['win_rate']:>5.1f}%  "
            f"avgR={s['avg_r']:>+.3f}R"
        )
    total = engine.summary()
    lines.append(f"  {'─'*60}")
    lines.append(
        f"  {'ALL SOURCES':<24}  n={total['total']:>5}  WR={total['win_rate']:>5.1f}%  "
        f"avgR={total['avg_r']:>+.3f}R"
    )
    return lines


def _section_dimension(engine, dimension: str, source_filter: str = None) -> list[str]:
    lines = []
    label = dimension.replace("_", " ").upper()
    src_label = f" ({source_filter})" if source_filter else " (all sources)"
    lines.append(f"\n  {label}{src_label}")
    lines.append(f"  {'─'*68}")
    lines.append(f"  {'Condition':<22} {'Trades':>6}  {'WR':>7}  {'AvgR':>8}  {'Conf':>5}  {'Verdict'}")
    lines.append(f"  {'─'*22} {'─'*6}  {'─'*7}  {'─'*8}  {'─'*5}  {'─'*12}")

    stats = engine.attribute_by(dimension, source_filter=source_filter, min_n=1)
    if not stats:
        lines.append("  (no data)")
        return lines
    for s in stats:
        lines.append(_fmt_stats_row(s))
    return lines


def _section_all_dimensions(engine, source_filter: str = None) -> list[str]:
    lines = []
    lines.append(f"\n{'EDGE ATTRIBUTION BY DIMENSION':^72}")
    lines.append(SEP)

    priority_dims = ["regime_4h", "session", "mss_type", "symbol",
                     "direction", "exit_type", "volatility_at_entry", "oi_bias"]
    for dim in priority_dims:
        lines += _section_dimension(engine, dim, source_filter)
    return lines


def _section_rules(engine, source_filter: str = None) -> list[str]:
    lines = []
    lines.append(f"\n{'RULE SUGGESTION ENGINE':^72}")
    lines.append(SEP)

    rules = engine.suggest_rules(source_filter)
    if not rules:
        lines.append("  No rules generated yet — need more trade data.")
        return lines

    lines.append(f"  {'Action':<10} {'Conf':>5}  {'Rule'}")
    lines.append(f"  {'─'*9} {'─'*5}  {'─'*52}")

    for r in rules:
        icon   = ACTION_ICONS.get(r.action, r.action)
        conf_s = _conf_color(r.confidence)
        lines.append(f"  {icon:<10} {conf_s}  {r.rule}")
        lines.append(f"             {'─'*5}  Evidence: {r.evidence}")

    return lines


def _section_exit_analysis(engine) -> list[str]:
    """Show how different exit types affect performance."""
    lines = []
    lines.append(f"\n{'EXIT TYPE ANALYSIS':^72}")
    lines.append(SEP)

    for src_filter, label in [("live", "Live"), ("backtest", "Backtest")]:
        stats = engine.attribute_by("exit_type", source_filter=src_filter, min_n=1)
        if not stats:
            continue
        lines.append(f"\n  {label} trades")
        lines.append(f"  {'Exit Type':<22} {'n':>6}  {'WR':>7}  {'AvgR':>8}")
        lines.append(f"  {'─'*22} {'─'*6}  {'─'*7}  {'─'*8}")
        for s in sorted(stats, key=lambda x: -x.total):
            lines.append(f"  {s.value:<22} {s.total:>6}  {s.win_rate:>6.1f}%  {s.avg_r:>+7.3f}R")
    return lines


def _section_score_analysis(engine) -> list[str]:
    """Bin trades by score and show WR per bin."""
    lines = []
    lines.append(f"\n{'SCORE / CONFLUENCE ANALYSIS':^72}")
    lines.append(SEP)

    # Bin into score ranges
    bins = {"≥14": [], "13": [], "12": [], "11": [], "≤10": []}
    for t in engine._trades:
        sc = t.get("score")
        if sc is None:
            continue
        sc = float(sc)
        if sc >= 14:   bins["≥14"].append(t)
        elif sc == 13: bins["13"].append(t)
        elif sc == 12: bins["12"].append(t)
        elif sc == 11: bins["11"].append(t)
        else:          bins["≤10"].append(t)

    lines.append(f"  {'Score':<10} {'n':>6}  {'WR':>7}  {'AvgR':>8}")
    lines.append(f"  {'─'*10} {'─'*6}  {'─'*7}  {'─'*8}")
    for label, trades in bins.items():
        if not trades:
            continue
        total = len(trades)
        wins  = sum(1 for t in trades if t.get("result") == "WIN")
        r_vals = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None]
        wr    = wins / total * 100 if total else 0
        avg_r = sum(r_vals) / len(r_vals) if r_vals else 0.0
        lines.append(f"  {label:<10} {total:>6}  {wr:>6.1f}%  {avg_r:>+7.3f}R")
    return lines


def main():
    parser = argparse.ArgumentParser(description="CB6 Edge Attribution Report")
    parser.add_argument("--source",  choices=["live", "backtest", "all"], default="all")
    parser.add_argument("--dim",     help="Single dimension to deep-dive")
    parser.add_argument("--rules",   action="store_true", help="Show rule suggestions only")
    parser.add_argument("--backfill", action="store_true", help="Run outcome backfill first")
    parser.add_argument("--save",    action="store_true", help="Save to reports/")
    args = parser.parse_args()

    # Backfill outcome fields if requested
    if args.backfill:
        from utils.outcome_tagger import backfill_outcomes
        from utils.trade_db import init_db
        init_db()   # runs migrations including new columns
        print("Running outcome backfill...")
        result = backfill_outcomes()
        print(f"  Updated: {result['updated']}  Errors: {result['errors']}")
        print()

    # Load data
    from utils.edge_engine import EdgeEngine
    engine = EdgeEngine()
    n_live = engine.load_live()
    n_bt   = engine.load_backtest()
    src_filter = None if args.source == "all" else args.source

    # Build report
    all_lines = []
    all_lines.append("")
    all_lines.append(SEP2)
    all_lines.append(f"{'CB6 QUANTUM — EDGE ATTRIBUTION REPORT':^72}")
    all_lines.append(f"{'Generated: ' + NOW.strftime('%Y-%m-%d %H:%M IST'):^72}")
    all_lines.append(f"{'Live trades: %d  |  Backtest: %d' % (n_live, n_bt):^72}")
    all_lines.append(SEP2)

    if args.dim:
        all_lines += _section_dimension(engine, args.dim, src_filter)
    elif args.rules:
        all_lines += _section_rules(engine, src_filter)
    else:
        all_lines += _section_summary(engine)
        all_lines += _section_all_dimensions(engine, src_filter)
        all_lines += _section_score_analysis(engine)
        all_lines += _section_exit_analysis(engine)
        all_lines += _section_rules(engine, src_filter)

    all_lines.append("")
    all_lines.append(SEP)
    all_lines.append("  Verdict guide: EDGE=60%+ WR | AVOID=35%- WR | NEUTRAL=35-60%")
    all_lines.append("  Confidence:    HIGH=30+ trades | MEDIUM=10-29 | LOW=3-9 | INSUFFICIENT=<3")
    all_lines.append("")

    report_text = "\n".join(all_lines)
    print(report_text)

    if args.save:
        Path("reports").mkdir(exist_ok=True)
        fname = Path("reports") / f"edge_{NOW.strftime('%Y%m%d')}.txt"
        fname.write_text(report_text, encoding="utf-8")
        print(f"Report saved to {fname}")


if __name__ == "__main__":
    main()
