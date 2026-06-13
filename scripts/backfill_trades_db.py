# scripts/backfill_trades_db.py
#
# CB6 Quantum — Backfill cb6_trades.db and trade_pattern_db from CSV journals
#
# Usage:
#   python scripts/backfill_trades_db.py
#   python scripts/backfill_trades_db.py --dry-run   (print rows without writing)
#
# Safe to run multiple times — idempotent via INSERT OR IGNORE

from __future__ import annotations

import csv
import json
import os
import sys
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.persistence.trade_persistence import (
    write_nse_trade, write_gft_trade, _migrate
)

_ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NSE_JOURNAL  = os.path.join(_ROOT, 'data', 'trade_journal.csv')
_FOREX_JOURNAL= os.path.join(_ROOT, 'data', 'forex_journal.csv')


def _safe_float(v) -> float:
    try:
        return float(v) if v not in (None, '', 'nan', 'N/A') else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v) -> int:
    try:
        return int(float(v)) if v not in (None, '', 'nan', 'N/A') else 0
    except (TypeError, ValueError):
        return 0


def backfill_nse(dry_run: bool = False) -> int:
    """Backfill NSE trades from trade_journal.csv."""
    if not os.path.exists(_NSE_JOURNAL):
        print(f"[NSE] Journal not found: {_NSE_JOURNAL}")
        return 0

    count = 0
    skipped = 0
    with open(_NSE_JOURNAL, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Only process rows that have exit data (closed trades)
            if not row.get('exit_price') or not row.get('exit_reason'):
                skipped += 1
                continue

            trade = {
                'symbol'         : row.get('symbol', ''),
                'underlying'     : row.get('underlying', ''),
                'direction'      : row.get('direction', ''),
                'entry_price'    : _safe_float(row.get('entry_price')),
                'current_sl'     : _safe_float(row.get('stop_loss')),
                'target1'        : _safe_float(row.get('target1')),
                'target2'        : _safe_float(row.get('target2')),
                'target3'        : _safe_float(row.get('target3')),
                'quantity'       : _safe_int(row.get('qty')),
                'lot_size'       : _safe_int(row.get('lot_size')) or 1,
                'fvg_entry_time' : f"{row.get('date', '')}T{row.get('entry_time', '09:00:00')}",
                'strike'         : _safe_int(row.get('strike')),
                'expiry'         : row.get('expiry', ''),
                'delta'          : _safe_float(row.get('delta')),
                'iv'             : _safe_float(row.get('iv')),
                'theta'          : _safe_float(row.get('theta')),
                'confluence'     : _safe_int(row.get('score')),
            }

            exit_context = {
                'exit_reason' : row.get('exit_reason', ''),
                'exit_price'  : _safe_float(row.get('exit_price')),
                'pnl'         : _safe_float(row.get('realized_pnl')),
                'r_multiple'  : 0.0,  # not stored in NSE CSV, will be 0
                'hold_mins'   : _safe_int(row.get('mins_in_fvg')),
            }

            if dry_run:
                print(f"[NSE DRY] {trade['symbol']} {trade['direction']} "
                      f"entry={trade['entry_price']} exit_reason={exit_context['exit_reason']}")
                count += 1
                continue

            ok = write_nse_trade(trade, setup={}, exit_context=exit_context)
            if ok:
                count += 1
            else:
                print(f"[NSE] FAILED: {trade['symbol']} {trade['fvg_entry_time']}")

    print(f"[NSE] Backfilled {count} trades  ({skipped} skipped — no exit data)")
    return count


def backfill_forex(dry_run: bool = False) -> int:
    """Backfill GFT forex trades from forex_journal.csv."""
    if not os.path.exists(_FOREX_JOURNAL):
        print(f"[FOREX] Journal not found: {_FOREX_JOURNAL}")
        return 0

    count   = 0
    skipped = 0
    with open(_FOREX_JOURNAL, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get('exit_price') or not row.get('result'):
                skipped += 1
                continue

            # Determine account from mode/label field
            mode  = str(row.get('mode', '') or '').lower()
            label = str(row.get('label', '') or '').lower()
            if '5k' in label or '2step' in label or 'goat' in label:
                account_id = 'GFT_5K'
            elif '1k' in label or 'instant' in label:
                account_id = 'GFT_1K_INSTANT'
            elif '10k' in label:
                account_id = 'GFT_10K'
            else:
                account_id = 'GFT_5K'  # default

            trade = {
                'symbol'      : row.get('symbol', ''),
                'direction'   : row.get('direction', ''),
                'entry_price' : _safe_float(row.get('entry')),
                'stop_loss'   : _safe_float(row.get('stop_loss')),
                'target1'     : _safe_float(row.get('target1')),
                'target2'     : _safe_float(row.get('target2')),
                'target3'     : _safe_float(row.get('target3')),
                'lots'        : _safe_float(row.get('lots')),
                'risk_usd'    : _safe_float(row.get('risk_usd')),
                'entry_time'  : f"{row.get('date', '')}T{row.get('entry_time', '08:00:00')}",
                'session'     : row.get('session', ''),
                'mss_type'    : row.get('mss_type', 'BOS'),
                'score'       : _safe_int(row.get('score')),
                'sim_ratio'   : _safe_float(row.get('rr_ratio')),  # not perfect but available
            }

            exit_context = {
                'hit'      : _map_forex_result(row.get('result', '')),
                'close_px' : _safe_float(row.get('exit_price')),
                'pnl'      : _safe_float(row.get('pnl_usd')),
                'r_multiple': _safe_float(row.get('r_multiple')),
            }

            if dry_run:
                print(f"[FOREX DRY] {account_id} {trade['symbol']} {trade['direction']} "
                      f"entry={trade['entry_price']} pnl={exit_context['pnl']}")
                count += 1
                continue

            ok = write_gft_trade(
                account_id   = account_id,
                trade        = trade,
                exit_context = exit_context,
            )
            if ok:
                count += 1
            else:
                print(f"[FOREX] FAILED: {trade['symbol']} {trade['entry_time']}")

    print(f"[FOREX] Backfilled {count} trades  ({skipped} skipped — no exit data)")
    return count


def _map_forex_result(result: str) -> str:
    r = result.upper()
    if r in ('WIN', '1', 'TRUE'):
        return 'TP'
    if r in ('LOSS', '0', 'FALSE'):
        return 'SL'
    return result


def main():
    parser = argparse.ArgumentParser(description='Backfill CB6 trade DBs from CSV journals')
    parser.add_argument('--dry-run', action='store_true', help='Print rows without writing')
    parser.add_argument('--nse-only',   action='store_true')
    parser.add_argument('--forex-only', action='store_true')
    args = parser.parse_args()

    _migrate()

    total = 0
    if not args.forex_only:
        total += backfill_nse(dry_run=args.dry_run)
    if not args.nse_only:
        total += backfill_forex(dry_run=args.dry_run)

    print(f"\nTotal records backfilled: {total}")
    if args.dry_run:
        print("(DRY RUN — no changes written)")


if __name__ == '__main__':
    main()
