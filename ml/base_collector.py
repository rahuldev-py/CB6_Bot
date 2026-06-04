# ml/base_collector.py
#
# Shared JSONL writer used by both NSE and Forex ML collectors.
# Each trade = one JSON line.  Entry fields written immediately;
# outcome fields patched in when the trade closes.
#
# File layout:
#   data/ml/nse/trades.jsonl        ← NSE paper + live trades
#   data/ml/forex/ftmo_trades.jsonl ← FTMO live trades
#   data/ml/forex/gft_trades.jsonl  ← GFT live trades
#
# Format: newline-delimited JSON (JSONL) — one complete trade record per line.
# Records are immutable after writing; outcome is appended as a separate
# UPDATE line with the same trade_id so the file stays append-only.
# Training scripts should group by trade_id and use the latest record.

import os
import json
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path(market: str, account: str = '') -> str:
    """
    market  : 'nse' | 'forex'
    account : '' | 'ftmo' | 'gft'
    """
    base = os.path.join(_root(), 'data', 'ml', market)
    os.makedirs(base, exist_ok=True)
    fname = f"{account}_trades.jsonl" if account else "trades.jsonl"
    return os.path.join(base, fname)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_record(market: str, record: dict, account: str = '') -> bool:
    """
    Append one JSON record to the appropriate JSONL file.
    Thread-safe. Never raises — logs and returns False on error.
    """
    try:
        fpath = _path(market, account)
        record['_written_at'] = _now_iso()
        line  = json.dumps(record, default=str) + '\n'
        with _LOCK:
            with open(fpath, 'a', encoding='utf-8') as f:
                f.write(line)
        return True
    except Exception as e:
        from utils.logger import logger
        logger.error(f"ML collector write error ({market}/{account}): {e}")
        return False


def patch_outcome(market: str, trade_id: str, outcome: dict, account: str = '') -> bool:
    """
    Append an OUTCOME record for an existing trade_id.
    Training code: group by trade_id, take the record where _type=='OUTCOME'.
    """
    record = {
        '_type'    : 'OUTCOME',
        'trade_id' : trade_id,
        'outcome'  : outcome,
    }
    return append_record(market, record, account)


def get_utc_context() -> dict:
    """Return time-based features useful for all models."""
    now = datetime.now(timezone.utc)
    return {
        'utc_hour'      : now.hour,
        'utc_minute'    : now.minute,
        'day_of_week'   : now.weekday(),        # 0=Mon … 4=Fri
        'day_name'      : now.strftime('%A'),
        'week_of_month' : (now.day - 1) // 7 + 1,
        'timestamp_utc' : now.isoformat(),
    }
