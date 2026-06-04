from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _index_path(market: str) -> Path:
    return Path("replays") / market / "replay_index.json"


def _load_index(market: str) -> Dict[str, Any]:
    p = _index_path(market)
    if not p.exists():
        return {"entries": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": []}


def _search(entries: List[Dict[str, Any]], *, symbol: str, result: str, regime: str, session: str) -> List[Dict[str, Any]]:
    out = []
    for e in entries:
        if symbol and str(e.get("symbol", "")).upper() != symbol.upper():
            continue
        if result and str(e.get("result", "")).upper() != result.upper():
            continue
        if regime and str(e.get("regime", "")).upper() != regime.upper():
            continue
        if session and str(e.get("session", "")).upper() != session.upper():
            continue
        out.append(e)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="CB6 Replay diagnostics/search (read-only)")
    parser.add_argument("--market", required=True, choices=["nse", "forex", "futures", "crypto"])
    parser.add_argument("--symbol", default="")
    parser.add_argument("--result", default="", choices=["", "WIN", "LOSS", "BREAKEVEN"])
    parser.add_argument("--regime", default="")
    parser.add_argument("--session", default="")
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    idx = _load_index(args.market)
    entries = idx.get("entries", [])
    entries = entries if isinstance(entries, list) else []

    if args.summary:
        summary = {
            "market": args.market,
            "total_replays": len(entries),
            "winners": sum(1 for e in entries if str(e.get("result", "")).upper() == "WIN"),
            "losers": sum(1 for e in entries if str(e.get("result", "")).upper() == "LOSS"),
            "breakeven": sum(1 for e in entries if str(e.get("result", "")).upper() == "BREAKEVEN"),
            "symbols": sorted(set(str(e.get("symbol", "UNKNOWN")) for e in entries)),
        }
        print(json.dumps(summary, indent=2))
        return 0

    rows = _search(
        entries,
        symbol=args.symbol,
        result=args.result,
        regime=args.regime,
        session=args.session,
    )
    print(json.dumps(rows[: max(1, args.top_n)], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

