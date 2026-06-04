"""
Run offline CB6 research experiments and write raw result files.

No live imports. No broker calls. No execution changes.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from ml_engine.chat.strategy_optimizer import ExperimentResult, StrategyOptimizer


MEMORY_DIR = Path("ml_engine/memory")
MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _to_records(results: list[ExperimentResult]) -> list[dict]:
    return [r.to_dict() for r in results]


def top_results(results: list[ExperimentResult], limit: int = 25) -> list[ExperimentResult]:
    rank_score = {"A+": 4, "A": 3, "B": 2, "REJECT": 1}
    return sorted(
        results,
        key=lambda r: (rank_score.get(r.rank, 0), r.profit_factor, r.win_rate, r.avg_r, r.n),
        reverse=True,
    )[:limit]


def rejected_results(results: list[ExperimentResult], limit: int = 25) -> list[ExperimentResult]:
    return sorted(
        [r for r in results if r.rank == "REJECT"],
        key=lambda r: (r.n, -r.avg_r),
        reverse=True,
    )[:limit]


def run_experiments() -> dict:
    optimizer = StrategyOptimizer()
    results = optimizer.run_all()

    payload = {
        "generated_at": datetime.now().isoformat(),
        "question": "How can CB6 achieve 80-85% win rate and at least 2.25 profit factor while keeping risk controlled?",
        "markets": {},
    }

    for market, rows in results.items():
        payload["markets"][market] = {
            "all_results": _to_records(rows),
            "top_results": _to_records(top_results(rows, 30)),
            "rejected_results": _to_records(rejected_results(rows, 30)),
        }

    return payload


def save_experiment_outputs(payload: dict) -> dict[str, str]:
    paths: dict[str, str] = {}
    for market in ["nse", "forex", "combined"]:
        path = MEMORY_DIR / f"{market}_backtest_learning.json"
        path.write_text(json.dumps(payload["markets"][market], indent=2, default=str), encoding="utf-8")
        paths[f"{market}_backtest_learning"] = str(path)

    all_top = []
    all_rejected = []
    for market, data in payload["markets"].items():
        for row in data["top_results"]:
            row = dict(row)
            row["market_key"] = market
            all_top.append(row)
        for row in data["rejected_results"]:
            row = dict(row)
            row["market_key"] = market
            all_rejected.append(row)

    best_path = MEMORY_DIR / "best_filters.json"
    best_path.write_text(json.dumps(all_top, indent=2, default=str), encoding="utf-8")
    paths["best_filters"] = str(best_path)

    reject_path = MEMORY_DIR / "rejected_filters.json"
    reject_path.write_text(json.dumps(all_rejected, indent=2, default=str), encoding="utf-8")
    paths["rejected_filters"] = str(reject_path)

    # Focused edge views.
    focused = {
        "long_short_edge": [r for r in all_top if r["filters"].get("direction") in ("long", "short") or "direction" in r["filters"]],
        "entry_exit_sl_tp_edge": [r for r in all_top if any(k in r["filters"] for k in ["exit_model", "score_gate", "mss_type", "fvg_displacement", "ob_present"])],
    }
    for name, rows in focused.items():
        path = MEMORY_DIR / f"{name}.json"
        path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        paths[name] = str(path)

    # CSV for quick sorting in Excel.
    flat = []
    for market, data in payload["markets"].items():
        for row in data["all_results"]:
            flat.append({**row, "market_key": market, **{f"filter_{k}": v for k, v in row.get("filters", {}).items()}})
    csv_path = MEMORY_DIR / "all_experiment_results.csv"
    pd.DataFrame(flat).to_csv(csv_path, index=False)
    paths["all_experiment_results_csv"] = str(csv_path)

    return paths


def main() -> None:
    payload = run_experiments()
    paths = save_experiment_outputs(payload)
    print("CB6 ML research experiments complete.")
    for key, path in paths.items():
        print(f"  {key}: {path}")


if __name__ == "__main__":
    main()

