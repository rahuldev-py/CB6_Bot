"""Statistics-only shadow audit for the adaptive trade-gate policy.

This tool reads an existing backtest JSON artifact. It does not scan markets,
connect to brokers, or alter execution behavior.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


DECISIONS = ("BLOCKED", "CAUTION", "T1_ONLY", "REDUCED_SIZE", "FULL_SIZE")


def _h4_relation(trade: dict[str, Any]) -> str:
    h4 = str(trade.get("h4_bias", "")).upper()
    direction = str(trade.get("direction", "")).upper()
    if h4 not in {"BULLISH", "BEARISH"} or direction not in {"BULLISH", "BEARISH"}:
        return "UNKNOWN"
    return "ALIGNED" if h4 == direction else "COUNTER"


def _t1_only_r(trade: dict[str, Any]) -> float:
    targets = str(trade.get("targets", "")).upper()
    if "T1" in targets:
        return 1.0
    return min(0.0, float(trade.get("total_r", 0.0)))


def classify_trade(trade: dict[str, Any]) -> dict[str, Any]:
    """Classify one frozen historical signal without using execution state."""
    score = float(trade.get("score", 0.0))
    mss = str(trade.get("mss_type", "UNKNOWN")).upper()
    relation = _h4_relation(trade)
    reasons: list[str] = []
    unavailable = ["rr", "session_quality", "liquidity_sweep_quality", "fvg_quality"]

    decision = "FULL_SIZE"
    trade_allowed = True
    size = 1.0
    t1_only = False

    # The frozen artifact has no hard-risk snapshots, so this audit cannot
    # classify any signal as BLOCKED. That limitation is surfaced in the report.
    if relation == "COUNTER":
        if score >= 16 and "CHOCH" in mss:
            decision = "CAUTION"
            size = 0.5
            t1_only = True
            reasons.append("counter-H4 score and CHoCH qualify")
            reasons.append("strong sweep/FVG and RR>=2.0 assumed for proxy audit")
        else:
            decision = "CAUTION"
            trade_allowed = False
            size = 0.0
            reasons.append("counter-H4 minimum score/CHoCH requirements not met")
    elif "BOS" in mss:
        if score >= 15:
            decision = "REDUCED_SIZE"
            size = 0.75
            reasons.append("BOS-only score >=15")
        elif score >= 12:
            decision = "T1_ONLY"
            size = 0.5
            t1_only = True
            reasons.append("BOS-only score 12-14")
        else:
            decision = "CAUTION"
            trade_allowed = False
            size = 0.0
            reasons.append("BOS-only score <12")
    elif score >= 16:
        reasons.append("aligned/high-score CHoCH")
    elif score >= 14:
        decision = "REDUCED_SIZE"
        size = 0.75
        reasons.append("score slightly below A+ proxy threshold")
    else:
        decision = "CAUTION"
        trade_allowed = False
        size = 0.0
        reasons.append("score below proxy quality floor")

    source = "CASCADE" if bool(trade.get("cascade")) else "PRIMARY"
    if source == "PRIMARY":
        reasons.append("primary source recorded; no source-only penalty")

    original_r = float(trade.get("total_r", 0.0))
    base_adaptive_r = _t1_only_r(trade) if t1_only else original_r
    adaptive_r = round(base_adaptive_r * size, 4) if trade_allowed else 0.0

    return {
        **trade,
        "shadow_decision": decision,
        "trade_allowed": trade_allowed,
        "size_multiplier": size,
        "t1_only": t1_only,
        "h4_relation": relation,
        "source": source,
        "shadow_reasons": reasons,
        "unavailable_evidence": unavailable,
        "original_r": original_r,
        "adaptive_r": adaptive_r,
    }


def _stats(rows: list[dict[str, Any]], r_key: str) -> dict[str, Any]:
    values = [float(row.get(r_key, 0.0)) for row in rows]
    count = len(values)
    wins = sum(value > 0 for value in values)
    return {
        "count": count,
        "win_rate_pct": round((wins / count * 100.0), 2) if count else 0.0,
        "average_r": round(sum(values) / count, 4) if count else 0.0,
        "total_r": round(sum(values), 4),
    }


def build_report(payload: dict[str, Any], input_path: Path) -> dict[str, Any]:
    rows = [classify_trade(trade) for trade in payload.get("trades", [])]
    by_decision: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_decision[row["shadow_decision"]].append(row)

    original = _stats(rows, "original_r")
    adaptive = _stats(rows, "adaptive_r")
    expectancy_improved = adaptive["average_r"] > original["average_r"]
    total_edge_preserved = adaptive["total_r"] >= original["total_r"]
    return {
        "generated_at": datetime.now().isoformat(),
        "mode": "SHADOW_STATISTICS_ONLY",
        "input": str(input_path),
        "window": payload.get("window"),
        "methodology": {
            "identical_signal_stream": True,
            "execution_changed": False,
            "t1_only_model": "T1 reached => +1.0R before size; otherwise non-positive original R",
            "counter_h4_proxy": (
                "Score>=16 plus CHoCH is treated as proxy-qualified because sweep quality, "
                "FVG quality, and RR are absent from the frozen artifact."
            ),
            "blocked_limitation": (
                "No hard-risk snapshots exist in the artifact; BLOCKED count is therefore zero."
            ),
        },
        "comparison": {
            "original_scanner": original,
            "adaptive_gate_scanner": adaptive,
            "total_r_delta": round(adaptive["total_r"] - original["total_r"], 4),
            "average_r_delta": round(adaptive["average_r"] - original["average_r"], 4),
            "edge_retention_pct": round(
                adaptive["total_r"] / original["total_r"] * 100.0, 2
            )
            if original["total_r"]
            else None,
        },
        "verdict": {
            "expectancy_improved": expectancy_improved,
            "total_edge_preserved": total_edge_preserved,
            "activation_supported": expectancy_improved and total_edge_preserved,
            "summary": (
                "PASS: adaptive gates improved expectancy without reducing total edge."
                if expectancy_improved and total_edge_preserved
                else "NOT VERIFIED: adaptive gates did not improve expectancy without reducing total edge."
            ),
        },
        "categories": {
            decision: {
                "original_outcomes": _stats(by_decision.get(decision, []), "original_r"),
                "adaptive_outcomes": _stats(by_decision.get(decision, []), "adaptive_r"),
            }
            for decision in DECISIONS
        },
        "signals": rows,
    }


def render_text(report: dict[str, Any]) -> str:
    comparison = report["comparison"]
    lines = [
        "CB6 ADAPTIVE GATE SHADOW AUDIT",
        "=" * 34,
        f"Mode: {report['mode']}",
        f"Window: {report.get('window')}",
        f"Input: {report['input']}",
        "",
        "IDENTICAL-DATA COMPARISON",
        "-" * 25,
        "Scanner                  Count    Win rate    Avg R    Total R",
    ]
    for label, key in (
        ("Original scanner", "original_scanner"),
        ("Adaptive-gate scanner", "adaptive_gate_scanner"),
    ):
        stats = comparison[key]
        lines.append(
            f"{label:<24} {stats['count']:>5}    {stats['win_rate_pct']:>7.2f}%"
            f"    {stats['average_r']:>+6.3f}R    {stats['total_r']:>+7.3f}R"
        )
    lines.extend(
        [
            "",
            f"Total R delta: {comparison['total_r_delta']:+.3f}R",
            f"Average R delta: {comparison['average_r_delta']:+.3f}R",
            f"Edge retention: {comparison['edge_retention_pct']}%",
            f"Verdict: {report['verdict']['summary']}",
            "",
            "ADAPTIVE DECISION CATEGORIES",
            "-" * 28,
            "Category                 Count   Original R   Adaptive R   Adaptive avgR",
        ]
    )
    for decision in DECISIONS:
        original = report["categories"][decision]["original_outcomes"]
        adaptive = report["categories"][decision]["adaptive_outcomes"]
        lines.append(
            f"{decision:<24} {adaptive['count']:>5}    {original['total_r']:>+8.3f}R"
            f"    {adaptive['total_r']:>+8.3f}R    {adaptive['average_r']:>+8.3f}R"
        )
        lines.append(
            f"{'  win rate':<24} {'':>5}    {original['win_rate_pct']:>7.2f}%"
            f"     {adaptive['win_rate_pct']:>7.2f}%"
        )
    lines.extend(
        [
            "",
            "LIMITATIONS",
            "-" * 11,
            f"- {report['methodology']['blocked_limitation']}",
            f"- {report['methodology']['counter_h4_proxy']}",
            "- RR, session quality, sweep quality, and FVG quality require future audit artifacts.",
            "- Statistics only. No execution modules or live settings were changed.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("reports/week_backtest_june8_11.json"),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("reports/adaptive_gate_shadow_audit_20260611.json"),
    )
    parser.add_argument(
        "--text-output",
        type=Path,
        default=Path("reports/adaptive_gate_shadow_audit_20260611.txt"),
    )
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    report = build_report(payload, args.input)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.text_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.text_output.write_text(render_text(report), encoding="utf-8")
    print(render_text(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
