from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ml_engine.memory.replay_maintenance import (
    MARKETS,
    rebuild_replay_index,
    validate_replay_index,
    detect_orphan_replay_files,
    detect_missing_replay_files_from_index,
    trim_index_entries,
    delete_orphan_files,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="CB6 replay index maintenance (read-only by default)")
    parser.add_argument("--market", required=True, choices=list(MARKETS))
    parser.add_argument(
        "--action",
        required=True,
        choices=[
            "rebuild",
            "validate",
            "orphans",
            "missing",
            "trim",
            "delete_orphans",
        ],
    )
    parser.add_argument("--apply", action="store_true", help="Apply write/delete changes (default: dry-run/read-only)")
    parser.add_argument("--max-entries", type=int, default=5000, help="Used with --action trim")
    args = parser.parse_args()

    if args.action == "rebuild":
        out = rebuild_replay_index(args.market, apply=args.apply)
    elif args.action == "validate":
        out = validate_replay_index(args.market)
    elif args.action == "orphans":
        out = {"market": args.market, "orphans": detect_orphan_replay_files(args.market)}
    elif args.action == "missing":
        out = {"market": args.market, "missing": detect_missing_replay_files_from_index(args.market)}
    elif args.action == "trim":
        out = trim_index_entries(args.market, max_entries=max(1, args.max_entries), apply=args.apply)
    elif args.action == "delete_orphans":
        out = delete_orphan_files(args.market, apply=args.apply)
    else:
        out = {"error": "unsupported action"}

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

