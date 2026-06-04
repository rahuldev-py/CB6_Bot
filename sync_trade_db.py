"""
CB6 Quantum — Unified Trade DB Sync
Run from project root: python sync_trade_db.py

Options:
  --stats          Print aggregate stats after sync
  --account ACCT   Filter stats to one account (FTMO, GFT_5K, etc.)
  --trade ID       Print full detail for one trade_id
  --query "SQL"    Run a custom SELECT query
"""

import sys
import json
import argparse
from utils.trade_db import sync_all, stats, get_trade, query


def print_table(rows: list[dict]):
    if not rows:
        print("  (no rows)")
        return
    keys = list(rows[0].keys())
    # Exclude bulky raw JSON columns from display
    keys = [k for k in keys if k not in ("raw_entry_json", "raw_outcome_json")]
    widths = {k: max(len(k), max((len(str(r.get(k, "") or "")) for r in rows), default=0)) for k in keys}
    header = "  ".join(k.ljust(widths[k]) for k in keys)
    sep    = "  ".join("-" * widths[k] for k in keys)
    print(header)
    print(sep)
    for r in rows:
        print("  ".join(str(r.get(k, "") or "").ljust(widths[k]) for k in keys))


def main():
    parser = argparse.ArgumentParser(description="CB6 Trade DB sync and query tool")
    parser.add_argument("--stats",   action="store_true", help="Print aggregate stats")
    parser.add_argument("--account", help="Filter stats/query to one account")
    parser.add_argument("--trade",   help="Print full detail for a trade_id")
    parser.add_argument("--query",   help="Run a custom SQL SELECT")
    parser.add_argument("--no-sync", action="store_true", help="Skip sync, query only")
    args = parser.parse_args()

    if not args.no_sync:
        print("Syncing all trade sources...")
        summary = sync_all(verbose=True)
        print()
        print("  Source                      Rows")
        print("  " + "-" * 40)
        total = 0
        for source, n in summary.items():
            print(f"  {source:<30} {n}")
            total += n
        print("  " + "-" * 40)
        print(f"  {'TOTAL':<30} {total}")
        print()

    if args.trade:
        trade = get_trade(args.trade)
        if trade is None:
            print(f"Trade not found: {args.trade}")
            sys.exit(1)
        print(f"\n=== Trade {args.trade} ===")
        for k, v in trade.items():
            if k in ("raw_entry_json", "raw_outcome_json") and v:
                print(f"  {k}:")
                try:
                    parsed = json.loads(v)
                    for ek, ev in parsed.items():
                        print(f"      {ek}: {ev}")
                except Exception:
                    print(f"    {v}")
            else:
                print(f"  {k}: {v}")
        return

    if args.query:
        rows = query(args.query)
        print_table(rows)
        return

    if args.stats:
        print("=== Aggregate Stats" + (f" — {args.account}" if args.account else " — All Accounts") + " ===")
        s = stats(args.account)
        total = s.get("total") or 0
        if total == 0:
            print("  No closed trades with results yet.")
        else:
            print(f"  Total trades : {total}")
            print(f"  Wins         : {s.get('wins')}")
            print(f"  Losses       : {s.get('losses')}")
            print(f"  Breakeven    : {s.get('be')}")
            print(f"  Win rate     : {s.get('win_rate')}%")
            print(f"  Total PnL    : ${s.get('total_pnl')}")
            print(f"  Avg PnL/trade: ${s.get('avg_pnl')}")
            print(f"  Avg R        : {s.get('avg_r')}R")
            print(f"  Best trade   : ${s.get('best_trade')}")
            print(f"  Worst trade  : ${s.get('worst_trade')}")
        print()

        # Per-account breakdown
        print("=== Per-Account Breakdown ===")
        rows = query("""
            SELECT account, COUNT(*) as total,
                   SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                   ROUND(SUM(pnl_usd), 2) as total_pnl,
                   ROUND(AVG(r_multiple), 3) as avg_r
            FROM trades
            WHERE result IS NOT NULL
            GROUP BY account
            ORDER BY account
        """)
        for r in rows:
            total = r["total"] or 0
            wins  = r["wins"]  or 0
            wr = round(wins / total * 100, 1) if total > 0 else 0
            print(f"  {r['account']:<15} {total:>3} trades  WR={wr}%  PnL=${r['total_pnl']}  AvgR={r['avg_r']}R")
        return

    # Default: print recent 20 trades
    print("=== Recent 20 Closed Trades ===")
    rows = query("""
        SELECT trade_id, account, symbol, direction, entry_time,
               pnl_usd, r_multiple, result, exit_reason, targets_hit
        FROM trades
        WHERE result IS NOT NULL
        ORDER BY entry_time DESC
        LIMIT 20
    """)
    print_table(rows)
    print()
    print("Tip: run with --stats for aggregate view, --trade <id> for full detail, --account <name> to filter")


if __name__ == "__main__":
    main()
