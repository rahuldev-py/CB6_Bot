from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ml_engine.memory.gft_challenge_simulator import (
    GFTChallengeRules,
    simulate_gft_challenge,
)


def _csv_to_list(raw: str):
    val = str(raw or "").strip()
    if not val:
        return None
    return [x.strip() for x in val.split(",") if x.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CB6 GFT Challenge Mode simulator (simulation-only, no live integration)"
    )
    parser.add_argument("--market", default="forex", choices=["nse", "forex", "futures", "crypto"])
    parser.add_argument("--starting-equity", type=float, default=5000.0)
    parser.add_argument("--daily-loss-limit", type=float, default=200.0)
    parser.add_argument("--max-drawdown", type=float, default=500.0)
    parser.add_argument("--profit-target", type=float, default=400.0)
    parser.add_argument("--max-trades-per-day", type=int, default=5)
    parser.add_argument("--min-quality-score", type=float, default=0.0)
    parser.add_argument("--min-memory-score", type=float, default=0.0)
    parser.add_argument("--allowed-regimes", default="")
    parser.add_argument("--allowed-setup-keys", default="")
    parser.add_argument("--min-trades-required", type=int, default=20)
    args = parser.parse_args()

    rules = GFTChallengeRules(
        starting_equity=args.starting_equity,
        daily_loss_limit_abs=args.daily_loss_limit,
        max_drawdown_abs=args.max_drawdown,
        profit_target_abs=args.profit_target,
        max_trades_per_day=max(1, args.max_trades_per_day),
        min_quality_score=args.min_quality_score,
        min_memory_score=args.min_memory_score,
        allowed_regimes=_csv_to_list(args.allowed_regimes),
        allowed_setup_keys=_csv_to_list(args.allowed_setup_keys),
        min_trades_required=max(1, args.min_trades_required),
    )

    result = simulate_gft_challenge(args.market, rules)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

