# utils/trade_journal.py — Silver Bullet Trade Journal (CSV)
#
# Logged at entry and exit:
#   Date, Time, Symbol, Direction, Window, Score, Strike, Delta, IV, Theta,
#   Entry, SL, T1, T2, T3, Exit Price, PnL, Lots, Reason
#
# Theta burn detection: if a trade's "mins_in_fvg" column > 20 with no target
# hit, the post-analysis script can flag it as theta-burn for review.

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Dict, Optional

JOURNAL_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'trade_journal.csv')

HEADERS = [
    'date', 'entry_time', 'exit_time',
    'symbol', 'underlying', 'direction', 'window',
    'score', 'strike', 'expiry',
    'delta', 'iv', 'theta', 'gamma', 'vega',
    'ltp_at_entry', 'entry_price', 'stop_loss',
    'target1', 'target2', 'target3',
    'exit_price', 'exit_reason',
    'lots', 'lot_size', 'qty',
    'realized_pnl', 'mins_in_fvg', 'theta_burn',
    'displacement', 'in_fvg',
    'atm_strike', 'option_bias', 'ce_pressure_score', 'pe_pressure_score',
    'iv_status', 'expiry_risk', 'option_data_available', 'source_latency_ms',
]


def _ensure_file():
    path = os.path.abspath(JOURNAL_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, 'w', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=HEADERS).writeheader()
    else:
        try:
            with open(path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                existing = reader.fieldnames or []
            if any(h not in existing for h in HEADERS):
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=HEADERS)
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({h: row.get(h, '') for h in HEADERS})
        except Exception:
            pass
    return path


def log_entry(trade: Dict, setup: Dict, strike_info: Dict) -> str:
    """
    Call this immediately after opening a position.
    Returns a journal_id (ISO timestamp string) to pass to log_exit().
    """
    path = _ensure_file()
    now  = datetime.now()
    sig  = setup.get('entry_signal', {})
    fvg  = setup.get('fvg', {})
    lot_size = strike_info.get('lot_size', trade.get('lot_size', 1))
    qty      = trade.get('quantity', 0)
    lots     = qty // lot_size if lot_size else 0
    opt_ctx  = setup.get('options_context') or trade.get('options_context') or {}
    pressure = opt_ctx.get('pressure') or {}

    row = {
        'date'        : now.strftime('%Y-%m-%d'),
        'entry_time'  : now.strftime('%H:%M:%S'),
        'exit_time'   : '',
        'symbol'      : trade.get('symbol', ''),
        'underlying'  : setup.get('symbol', ''),
        'direction'   : setup.get('direction', ''),
        'window'      : setup.get('window', ''),
        'score'       : setup.get('confluence', ''),
        'strike'      : strike_info.get('strike', ''),
        'expiry'      : strike_info.get('expiry', ''),
        'delta'       : strike_info.get('delta', ''),
        'iv'          : f"{strike_info.get('iv', 0):.2%}",
        'theta'       : strike_info.get('theta', ''),
        'gamma'       : strike_info.get('gamma', ''),
        'vega'        : strike_info.get('vega', ''),
        'ltp_at_entry': strike_info.get('ltp', ''),
        'entry_price' : trade.get('entry_price', ''),
        'stop_loss'   : trade.get('current_sl', sig.get('stop_loss', '')),
        'target1'     : trade.get('target1', sig.get('target1', '')),
        'target2'     : trade.get('target2', sig.get('target2', '')),
        'target3'     : trade.get('target3', sig.get('target3', '')),
        'exit_price'  : '',
        'exit_reason' : '',
        'lots'        : lots,
        'lot_size'    : lot_size,
        'qty'         : qty,
        'realized_pnl': '',
        'mins_in_fvg' : '',
        'theta_burn'  : '',
        'displacement': fvg.get('displacement', ''),
        'in_fvg'      : setup.get('in_fvg', ''),
        'atm_strike'  : opt_ctx.get('atm_strike', ''),
        'option_bias' : opt_ctx.get('option_bias', ''),
        'ce_pressure_score': opt_ctx.get('ce_pressure_score', pressure.get('ce_oi', '')),
        'pe_pressure_score': opt_ctx.get('pe_pressure_score', pressure.get('pe_oi', '')),
        'iv_status'   : opt_ctx.get('iv_status', ''),
        'expiry_risk' : opt_ctx.get('expiry_risk', ''),
        'option_data_available': opt_ctx.get('option_data_available', ''),
        'source_latency_ms': opt_ctx.get('source_latency_ms', ''),
    }

    with open(path, 'a', newline='', encoding='utf-8') as f:
        csv.DictWriter(f, fieldnames=HEADERS).writerow(row)

    journal_id = now.isoformat()

    # Enriched pattern library record (JSONL — richer than CSV)
    try:
        from utils.trade_enrichment import build_enriched_entry, append_enriched_entry
        enriched = build_enriched_entry(trade, setup, strike_info)
        record_id = append_enriched_entry(enriched)
        # Store enrichment ID alongside journal_id for exit update
        trade['_enriched_record_id'] = record_id
    except Exception:
        pass

    return journal_id


def log_exit(journal_id: str, exit_price: float, exit_reason: str,
             realized_pnl: float, mins_in_fvg: float):
    """
    Update the matching entry row with exit details.
    Marks theta_burn=YES if mins_in_fvg > 20 with no target hit.
    """
    path = _ensure_file()
    # journal_id is a full ISO datetime (2026-05-19T10:09:23.456789)
    # The CSV stores date and entry_time as separate columns (date=YYYY-MM-DD, entry_time=HH:MM:SS)
    # Match on BOTH to avoid false positives across days with identical timestamps
    entry_date     = journal_id[:10]       # YYYY-MM-DD
    entry_time_hms = journal_id[11:19]     # HH:MM:SS

    theta_burn = 'YES' if (mins_in_fvg > 20 and
                            exit_reason not in ('TARGET1', 'TARGET2', 'TARGET3')) else 'NO'

    # Read all rows, find matching entry_time row, update it
    rows = []
    updated = False
    with open(path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (not updated
                    and row.get('date', '') == entry_date
                    and row['entry_time'] == entry_time_hms):
                row['exit_time']   = datetime.now().strftime('%H:%M:%S')
                row['exit_price']  = exit_price
                row['exit_reason'] = exit_reason
                row['realized_pnl']= round(realized_pnl, 2)
                row['mins_in_fvg'] = round(mins_in_fvg, 1)
                row['theta_burn']  = theta_burn
                updated = True
            rows.append(row)

    if updated:
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=HEADERS)
            w.writeheader()
            w.writerows(rows)

    # Enrich the JSONL record with exit data
    try:
        from utils.trade_enrichment import enrich_exit
        # Find matching enriched record by entry_time prefix
        enrich_exit(
            record_id=journal_id,   # journal_id contains the full timestamp
            exit_data={
                'exit_time'       : datetime.now().isoformat(),
                'exit_price'      : exit_price,
                'exit_reason'     : exit_reason,
                'realized_pnl'    : round(realized_pnl, 2),
                'hold_time_mins'  : round(mins_in_fvg, 1),
                'theta_burn_flag' : theta_burn == 'YES',
            }
        )
    except Exception:
        pass


def get_journal_path() -> str:
    return os.path.abspath(JOURNAL_PATH)


def get_today_summary() -> Dict:
    """Return today's trade stats from the journal."""
    path = _ensure_file()
    today = datetime.now().strftime('%Y-%m-%d')
    trades, wins, losses, pnl, theta_burns = 0, 0, 0, 0.0, 0

    with open(path, 'r', newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['date'] != today or not row['exit_price']:
                continue
            trades += 1
            p = float(row['realized_pnl'] or 0)
            pnl += p
            if p > 0:
                wins += 1
            else:
                losses += 1
            if row.get('theta_burn') == 'YES':
                theta_burns += 1

    return {
        'trades'      : trades,
        'wins'        : wins,
        'losses'      : losses,
        'win_rate'    : round(wins / trades * 100, 1) if trades else 0,
        'pnl'         : round(pnl, 2),
        'theta_burns' : theta_burns,
    }
