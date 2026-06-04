"""
Write CB6 research learning memory and final human report.

Offline only. Memory files are advisory and do not affect live filters.
"""

from __future__ import annotations

import json
from pathlib import Path


MEMORY_DIR = Path("ml_engine/memory")


LIVE_LEARNING_SCHEMA = {
    "trade_id": "string",
    "market": "NSE|FOREX",
    "symbol": "string",
    "direction": "long|short",
    "entry_reason": "string",
    "exit_reason": "string",
    "sl_reason": "structure|fvg|ob|fixed|atr|manual",
    "tp_reason": "liquidity_pool|fixed_r|session_extreme|manual",
    "trailing_sl_used": "none|breakeven_1r|trail_after_1_5r|structure_trail",
    "trend_bias": "bullish|bearish|ranging",
    "h4_bias": "bullish|bearish|ranging",
    "h1_bias": "bullish|bearish|ranging",
    "choch": "bool",
    "bos": "bool",
    "mss": "bool",
    "fvg_size": "float",
    "fvg_quality": "none|weak|strong",
    "ob_overlap": "bool",
    "liquidity_sweep_type": "BSL|SSL|EQH|EQL|session|none",
    "score": "float",
    "ml_confidence": "float",
    "result": "win|loss|breakeven",
    "r_multiple": "float",
    "max_favorable_excursion": "float",
    "max_adverse_excursion": "float",
    "should_repeat": "bool",
}


def _load_json(path: Path) -> dict | list:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_pf(value) -> str:
    if value == float("inf") or str(value).lower() == "inf":
        return "inf"
    try:
        return f"{float(value):.2f}"
    except Exception:
        return str(value)


def _best(data: dict, rank: str | None = None) -> list[dict]:
    rows = data.get("top_results", [])
    if rank:
        rows = [r for r in rows if r.get("rank") == rank]
    return rows


def _line(row: dict) -> str:
    return (
        f"- {row.get('name')} | N={row.get('n')} | WR={row.get('win_rate')}% | "
        f"PF={_fmt_pf(row.get('profit_factor'))} | AvgR={row.get('avg_r')} | "
        f"DD={row.get('max_dd_r')}R | confidence={row.get('confidence')} | filters={row.get('filters')}"
    )


def build_summary(paths: dict[str, str] | None = None) -> str:
    nse = _load_json(MEMORY_DIR / "nse_backtest_learning.json")
    forex = _load_json(MEMORY_DIR / "forex_backtest_learning.json")
    combined = _load_json(MEMORY_DIR / "combined_backtest_learning.json")
    best_filters = _load_json(MEMORY_DIR / "best_filters.json")
    rejected = _load_json(MEMORY_DIR / "rejected_filters.json")

    nse_best = _best(nse)[:5]
    fx_best = _best(forex)[:5]
    combo_best = _best(combined)[:5]
    aplus = [r for r in best_filters if r.get("rank") == "A+"]
    a_rank = [r for r in best_filters if r.get("rank") == "A"]

    can_reach = bool(aplus)
    if can_reach:
        reach_text = "Yes, but only under stricter filters and only where sample confidence is acceptable."
    else:
        reach_text = "Not proven yet. Current data finds strong pockets, but no robust 80-85% WR plus PF 2.25 edge across enough trades."

    def _robust_best(rows: list[dict]) -> dict:
        robust = [r for r in rows if r.get("confidence") in ("HIGH", "ACCEPTABLE") and r.get("n", 0) >= 50]
        return robust[0] if robust else (rows[0] if rows else {})

    better_market = "NSE"
    if fx_best and nse_best:
        fx_row = _robust_best(fx_best)
        nse_row = _robust_best(nse_best)
        fx_score = (
            1 if fx_row.get("confidence") in ("HIGH", "ACCEPTABLE") else 0,
            float(fx_row.get("profit_factor", 0) or 0),
            float(fx_row.get("win_rate", 0) or 0),
            int(fx_row.get("n", 0) or 0),
        )
        nse_score = (
            1 if nse_row.get("confidence") in ("HIGH", "ACCEPTABLE") else 0,
            float(nse_row.get("profit_factor", 0) or 0),
            float(nse_row.get("win_rate", 0) or 0),
            int(nse_row.get("n", 0) or 0),
        )
        better_market = "Forex" if fx_score > nse_score else "NSE"

    lines = [
        "# CB6 ML Learning Summary",
        "",
        "## Executive Summary",
        f"Can CB6 realistically reach 80-85% WR? {reach_text}",
        "The safest path is not higher risk. It is fewer trades: stricter score gates, direction/session filtering, displacement-only FVGs, and skipping weak/noisy regimes.",
        "Profit factor improves when poor-context trades are removed; it is not solved by widening TP alone.",
        "",
        "## NSE Findings",
    ]
    lines += [_line(r) for r in nse_best] or ["- No usable NSE results."]
    lines += [
        "",
        "## Forex Findings",
    ]
    lines += [_line(r) for r in fx_best] or ["- No usable Forex results."]
    lines += [
        "",
        "## Combined Market Comparison",
        f"- Better current research market: {better_market}",
    ]
    lines += [_line(r) for r in combo_best[:3]]

    lines += [
        "",
        "## Best Setup Combination",
    ]
    if aplus:
        lines += [_line(r) for r in aplus[:10]]
    elif a_rank:
        lines += ["- No A+ configuration met all targets. Best A-grade candidates:"] + [_line(r) for r in a_rank[:10]]
    else:
        lines += ["- No A/A+ robust configuration found yet."]

    lines += [
        "",
        "## Worst Setup Combination",
    ]
    lines += [_line(r) for r in rejected[:10]] or ["- No rejected filters stored."]

    lines += [
        "",
        "## Recommended Live Rule Changes",
        "- Do not apply automatically.",
        "- Manually review the best_filters.json candidates first.",
        "- Prefer displacement-only FVGs, higher score gates, no choppy regime, and direction/session filters supported by sample size.",
        "- Treat H1/H4 alignment results as incomplete unless historical rows include h1_bias/h4_bias.",
        "",
        "## Backtest Caveats",
        "- This run uses existing labeled/backtest rows, not a fresh candle-path simulation for every SL/TP/trailing variant.",
        "- TP/SL/trailing experiments are R-multiple proxies until MFE/MAE and post-entry candle paths are stored.",
        "- News filter is unavailable in historical labels and is marked as unavailable.",
        "- H1/H4 bias is not consistently logged in the existing labeled dataset.",
        "- Any result with N < 30 is LOW CONFIDENCE; N >= 50 is acceptable; N >= 100 is preferred.",
        "",
        "## Future Live Learning Schema",
        "```json",
        json.dumps(LIVE_LEARNING_SCHEMA, indent=2),
        "```",
        "",
        "## ML Memory Files Saved",
    ]
    if paths:
        lines += [f"- {k}: {v}" for k, v in paths.items()]
    else:
        lines += [f"- {p.name}" for p in sorted(MEMORY_DIR.glob("*"))]
    lines += [
        "",
        "## Next Safe Step",
        "Run this research after each new batch of closed trades. Do not change live filters until a human approves a specific rule change and the sample size is acceptable.",
    ]

    return "\n".join(lines)


def write_summary(paths: dict[str, str] | None = None) -> str:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    schema_path = MEMORY_DIR / "future_live_learning_schema.json"
    schema_path.write_text(json.dumps(LIVE_LEARNING_SCHEMA, indent=2), encoding="utf-8")

    summary = build_summary(paths)
    out = MEMORY_DIR / "ml_learning_summary.md"
    out.write_text(summary, encoding="utf-8")
    return str(out)
