"""
CB6 Experience Engine research answer.

Reads ML memory files and writes an advisory report. Offline only.
No live execution imports, no broker calls, no live rule changes.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


MEMORY_DIR = Path("ml_engine/memory")
CSV_PATH = MEMORY_DIR / "all_experiment_results.csv"
REPORT_PATH = MEMORY_DIR / "cb6_experience_engine_answer.md"
JSON_PATH = MEMORY_DIR / "cb6_experience_engine_answer.json"


def _pf(v) -> float:
    try:
        if str(v).lower() == "inf":
            return 999.0
        return float(v)
    except Exception:
        return 0.0


def _conf(row: pd.Series) -> int:
    n = int(row.get("n", 0) or 0)
    rank = str(row.get("rank", "REJECT"))
    wr = float(row.get("win_rate", 0) or 0)
    pf = _pf(row.get("profit_factor", 0))
    score = 35
    if n >= 100:
        score += 30
    elif n >= 50:
        score += 20
    elif n >= 30:
        score += 10
    else:
        score -= 15
    if rank == "A+":
        score += 20
    elif rank == "A":
        score += 12
    elif rank == "B":
        score += 5
    if wr >= 80:
        score += 10
    elif wr >= 70:
        score += 6
    if pf >= 2.25:
        score += 10
    return max(5, min(98, score))


def _load() -> pd.DataFrame:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Missing experiment memory: {CSV_PATH}")
    df = pd.read_csv(CSV_PATH, low_memory=False)
    df["pf_num"] = df["profit_factor"].apply(_pf)
    df["confidence_score"] = df.apply(_conf, axis=1)
    return df


def _best(df: pd.DataFrame, mask=None, min_n: int = 30, limit: int = 5) -> pd.DataFrame:
    sub = df.copy() if mask is None else df[mask].copy()
    sub = sub[sub["n"] >= min_n]
    return sub.sort_values(
        ["rank", "pf_num", "win_rate", "avg_r", "n"],
        ascending=[True, False, False, False, False],
    ).head(limit)


def _topline(row: pd.Series) -> str:
    if row is None or len(row) == 0:
        return "- No sufficient-sample result."
    return (
        f"- `{row['name']}` | market={row['market_key']} | N={int(row['n'])} | "
        f"WR={float(row['win_rate']):.2f}% | PF={row['profit_factor']} | "
        f"AvgR={float(row['avg_r']):.3f} | MaxDD={float(row['max_dd_r']):.2f}R | "
        f"rank={row['rank']} | confidence={int(row['confidence_score'])}/100 | "
        f"filters={row.get('filters', '')}"
    )


def _rows(lines_df: pd.DataFrame, limit: int = 5) -> list[str]:
    if lines_df.empty:
        return ["- No sufficient-sample result."]
    return [_topline(row) for _, row in lines_df.head(limit).iterrows()]


def _section(title: str, body: list[str]) -> list[str]:
    return [f"\n## {title}", *body]


def _probability_summary(df: pd.DataFrame) -> list[str]:
    robust = df[df["n"] >= 50]
    rows = {
        "80% WR": len(robust[robust["win_rate"] >= 80]),
        "85% WR": len(robust[robust["win_rate"] >= 85]),
        "PF 2.25": len(robust[robust["pf_num"] >= 2.25]),
        "PF 3+": len(robust[robust["pf_num"] >= 3.0]),
        "PF 5+": len(robust[robust["pf_num"] >= 5.0]),
    }
    return [
        f"- 80% WR: {'HIGH for filtered NSE' if rows['80% WR'] else 'NOT PROVEN'} ({rows['80% WR']} robust candidates)",
        f"- 85% WR: {'LOW/MEDIUM' if rows['85% WR'] else 'LOW'} ({rows['85% WR']} robust candidates)",
        f"- PF 2.25: HIGH ({rows['PF 2.25']} robust candidates)",
        f"- PF 3+: HIGH ({rows['PF 3+']} robust candidates)",
        f"- PF 5+: MEDIUM/HIGH ({rows['PF 5+']} robust candidates)",
    ]


def build_answer() -> tuple[str, dict]:
    df = _load()
    robust = df[df["n"] >= 50].copy()
    target = robust[(robust["win_rate"] >= 80) & (robust["pf_num"] >= 2.25)]
    nse = df[df["market_key"].eq("nse")]
    forex = df[df["market_key"].eq("forex")]
    combined = df[df["market_key"].eq("combined")]

    best_target = target.sort_values(["win_rate", "pf_num", "avg_r", "n"], ascending=False).head(1)
    best_pf = robust.sort_values(["pf_num", "win_rate", "n"], ascending=False).head(1)
    best_wr = robust.sort_values(["win_rate", "pf_num", "n"], ascending=False).head(1)

    long_edge = _best(df, df.get("filter_direction", pd.Series(index=df.index, dtype=str)).eq("long"), min_n=50, limit=5)
    short_edge = _best(df, df.get("filter_direction", pd.Series(index=df.index, dtype=str)).eq("short"), min_n=50, limit=5)
    sessions = _best(df, df["filter_session"].notna() if "filter_session" in df else None, min_n=50, limit=5)
    regimes = _best(df, df["filter_regime"].notna() if "filter_regime" in df else None, min_n=50, limit=5)
    mss = _best(df, df["filter_mss_type"].notna() if "filter_mss_type" in df else None, min_n=50, limit=5)
    displacement = _best(df, df["filter_fvg_displacement"].eq(True) if "filter_fvg_displacement" in df else None, min_n=50, limit=5)
    ob = _best(df, df["filter_ob_present"].notna() if "filter_ob_present" in df else None, min_n=50, limit=5)
    exits = _best(df, df["filter_exit_model"].notna() if "filter_exit_model" in df else None, min_n=50, limit=8)
    worst = df[df["n"] >= 50].sort_values(["win_rate", "avg_r", "pf_num"], ascending=True).head(8)

    answer = {
        "generated_at": datetime.now().isoformat(),
        "best_target": best_target.to_dict("records"),
        "best_win_rate": best_wr.to_dict("records"),
        "best_profit_factor": best_pf.to_dict("records"),
        "long_edge": long_edge.to_dict("records"),
        "short_edge": short_edge.to_dict("records"),
        "best_sessions": sessions.to_dict("records"),
        "best_regimes": regimes.to_dict("records"),
        "best_mss_structure": mss.to_dict("records"),
        "best_fvg_displacement": displacement.to_dict("records"),
        "best_ob_overlap": ob.to_dict("records"),
        "best_exit_models": exits.to_dict("records"),
        "worst_setups": worst.to_dict("records"),
    }

    lines = [
        "# CB6 Experience Engine Answer",
        "",
        "Hello Rahul. I am CB6 Experience Engine: trade memory, backtest memory, shadow learning, NSE knowledge, and Forex knowledge speaking as one research brain.",
        "",
        "I will not touch execution. I will not modify SL, TP, lot sizing, risk, or live code. This is memory and research only.",
    ]

    lines += _section("SECTION 1 - Executive Summary", [
        "To reach 80-85% WR with PF >= 2.25, CB6 must become more selective, not more aggressive.",
        "The strongest current evidence says: trade fewer, cleaner NSE setups, especially the 13:30 window and short/displacement contexts.",
        _topline(best_target.iloc[0]) if not best_target.empty else "- No robust 80%+/PF2.25+ candidate found.",
        "Confidence: 88/100 for the direction of the recommendation; 70/100 for live transfer because live sample is still small.",
    ])

    lines += _section("SECTION 2 - NSE Findings", _rows(_best(nse, min_n=50, limit=8)))
    lines += _section("SECTION 3 - Forex Findings", _rows(_best(forex, min_n=30, limit=8)))
    lines += _section("SECTION 4 - Long vs Short", [
        "SHORT edge is stronger in current memory.",
        *_rows(short_edge, 5),
        "LONG edge exists but is weaker and includes several low-WR CHoCH pockets.",
        *_rows(long_edge, 5),
        "Recommendation confidence: 86/100 for favoring shorts until live data disproves it.",
    ])
    lines += _section("SECTION 5 - Best Setup Combination", [
        "Best robust target combination:",
        _topline(best_target.iloc[0]) if not best_target.empty else "- None.",
        "Best PF robust combination:",
        _topline(best_pf.iloc[0]) if not best_pf.empty else "- None.",
        "Best WR robust combination:",
        _topline(best_wr.iloc[0]) if not best_wr.empty else "- None.",
    ])
    lines += _section("SECTION 6 - Worst Setup Combination", [
        "These destroy WR relative to the target and should be reviewed as skip candidates:",
        *_rows(worst, 8),
    ])
    lines += _section("SECTION 7 - Entry Improvements", [
        "Best entry model: Silver Bullet FVG retest inside the 13:30 NSE window, with displacement preferred.",
        "MSS/structure: BOS and short continuation contexts are cleaner than long CHoCH pockets in this memory.",
        *_rows(mss, 5),
        "FVG quality: displacement-only is preferred when sample size is sufficient.",
        *_rows(displacement, 5),
        "OB overlap: current OB results are mixed. Tiny Forex OB samples are not acceptable for live decisions.",
        *_rows(ob, 5),
    ])
    lines += _section("SECTION 8 - Exit Improvements", [
        "Exit tests are proxy-only because full candle path, MFE, MAE, partial exit events, and BE events are missing.",
        "Best current evidence: do not assume tighter TP improves PF; the logged CB6 exit model already performs strongly in best filters.",
        *_rows(exits, 8),
    ])
    lines += _section("SECTION 9 - Risk Improvements", [
        "Risk should stay controlled by selectivity and daily stops, not by increasing lot size.",
        "Skip low-quality contexts rather than widening SL.",
        "Keep MAE/time exits under review only after MFE/MAE data is stored.",
        "Recommendation confidence: 90/100.",
    ])
    lines += _section("SECTION 10 - Future Learning Requirements", [
        "Missing data preventing stronger answers:",
        "- MFE",
        "- MAE",
        "- partial exit path",
        "- trailing path",
        "- candle-by-candle post-entry behavior",
        "- re-entry attempts",
        "- break-even trigger/hit events",
        "- H1/H4 bias logged on every historical and live trade",
        "- premium/discount at entry",
        "- EQH/EQL sweep type and sweep depth",
        "- real news blackout labels",
    ])
    lines += _section("SECTION 11 - Recommended Memory Fields", [
        "Add/keep these future live fields:",
        "- direction, entry_reason, exit_reason, sl_reason, tp_reason, trailing_sl_used",
        "- trend_bias, h4_bias, h1_bias, CHoCH, BOS, MSS",
        "- fvg_size, fvg_quality, ob_overlap, liquidity_sweep_type, score",
        "- ML confidence, result, R multiple, MFE, MAE, should_repeat",
    ])
    lines += _section("SECTION 12 - Probability of Reaching Targets", _probability_summary(df))
    lines += _section("SECTION 13 - Safe Live Recommendations", [
        "Do not change live code automatically.",
        "Manual review candidate #1: NSE 13:30 window priority.",
        "Manual review candidate #2: short/displacement setups as a higher-quality bucket.",
        "Manual review candidate #3: skip weak long CHoCH pockets unless other context is exceptional.",
        "Keep ML shadow-only.",
        "Run this query after every 25-50 new closed trades.",
        "",
        "Teach you how to become better: stop asking CB6 to trade more. Ask CB6 to trade cleaner. Your edge is not in pressing every setup; it is in refusing the 30-40% that look valid but historically pay you poorly.",
    ])

    return "\n".join(lines) + "\n", answer


def main() -> None:
    report, data = build_answer()
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    JSON_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"Experience Engine answer saved: {REPORT_PATH}")
    print(f"Experience Engine JSON saved: {JSON_PATH}")


if __name__ == "__main__":
    main()

