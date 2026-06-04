from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ml_engine.memory.analytics import (
    build_dashboard_ready_summary,
    query_average_rr_by_setup,
    query_best_worst_setup_dna,
    query_losing_pattern_clusters,
    query_win_rate_by_market,
    query_win_rate_by_regime,
    query_win_rate_by_session,
    query_win_rate_by_symbol,
    query_regime_performance_summary,
    query_regime_symbol_performance,
    query_regime_session_performance,
    query_regime_setup_performance,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="CB6 Memory V1 diagnostics (read-only)")
    parser.add_argument("--market", choices=["nse", "forex", "futures", "crypto"], help="Target market for isolated queries")
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument(
        "--query",
        required=True,
        choices=[
            "winrate_by_market",
            "best_worst_setup",
            "winrate_by_symbol",
            "winrate_by_session",
            "winrate_by_regime",
            "avg_rr_by_setup",
            "losing_clusters",
            "dashboard_summary",
            "regime_summary",
            "regime_symbol",
            "regime_session",
            "regime_setup",
        ],
    )
    args = parser.parse_args()

    q = args.query
    if q == "winrate_by_market":
        result = query_win_rate_by_market(min_samples=args.min_samples)
    else:
        if not args.market:
            raise SystemExit("--market is required for this query")
        if q == "best_worst_setup":
            result = query_best_worst_setup_dna(args.market, min_samples=args.min_samples, top_n=args.top_n)
        elif q == "winrate_by_symbol":
            result = query_win_rate_by_symbol(args.market, min_samples=args.min_samples)
        elif q == "winrate_by_session":
            result = query_win_rate_by_session(args.market, min_samples=args.min_samples)
        elif q == "winrate_by_regime":
            result = query_win_rate_by_regime(args.market, min_samples=args.min_samples)
        elif q == "avg_rr_by_setup":
            result = query_average_rr_by_setup(args.market, min_samples=args.min_samples, top_n=args.top_n)
        elif q == "losing_clusters":
            result = query_losing_pattern_clusters(args.market, min_samples=args.min_samples, top_n=args.top_n)
        elif q == "dashboard_summary":
            result = build_dashboard_ready_summary(args.market, min_samples=args.min_samples)
        elif q == "regime_summary":
            result = query_regime_performance_summary(args.market, min_samples=args.min_samples)
        elif q == "regime_symbol":
            result = query_regime_symbol_performance(args.market, min_samples=args.min_samples)
        elif q == "regime_session":
            result = query_regime_session_performance(args.market, min_samples=args.min_samples)
        elif q == "regime_setup":
            result = query_regime_setup_performance(args.market, min_samples=args.min_samples, top_n=args.top_n)
        else:
            result = {"error": f"Unknown query '{q}'"}

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
