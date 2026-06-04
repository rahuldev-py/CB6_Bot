from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ml_engine.memory.gft_shadow_evaluation import EvalConfig, evaluate_shadow_recommendations


def main() -> int:
    parser = argparse.ArgumentParser(description="GFT shadow recommendation evaluation (read-only)")
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--starting-equity", type=float, default=5000.0)
    parser.add_argument("--daily-loss-limit", type=float, default=200.0)
    parser.add_argument("--max-drawdown", type=float, default=500.0)
    parser.add_argument("--match-window-minutes", type=int, default=360)
    args = parser.parse_args()

    cfg = EvalConfig(
        min_samples=max(1, args.min_samples),
        starting_equity=args.starting_equity,
        daily_loss_limit_abs=args.daily_loss_limit,
        max_drawdown_abs=args.max_drawdown,
        match_window_minutes=max(1, args.match_window_minutes),
    )
    report = evaluate_shadow_recommendations(cfg)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

