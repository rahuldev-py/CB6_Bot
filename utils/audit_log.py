# utils/audit_log.py
# Immutable append-only JSONL audit trail for all order state transitions.
#
# Every record is a single JSON line, never modified after write.
# Log file: data/audit/orders_YYYY-MM-DD.jsonl (rolls daily).
# Use audit_log.append() at every entry, exit, and SL modification event.

import json
import os
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_ROOT, 'data', 'audit')


def _log_path() -> str:
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return os.path.join(_LOG_DIR, f'orders_{date_str}.jsonl')


def append(event_type: str, account: str, market: str, **kwargs):
    """
    Append one audit record. Never raises — audit failures must not affect trading.

    event_type : ORDER_PLACED | ORDER_FILLED | SL_MODIFIED | POSITION_CLOSED |
                 POSITION_RECONCILED | GATE_BLOCKED | OFFLINE_CLOSE | ...
    account    : 'gft_5k' | 'gft_1k' | 'gft_10k' | 'nse_fyers' | 'ftmo'
    market     : 'forex' | 'nse'
    **kwargs   : any extra fields (symbol, ticket, lots, price, pnl, reason …)
    """
    try:
        record = {
            'ts'        : datetime.now(timezone.utc).isoformat(),
            'event'     : event_type,
            'account'   : account,
            'market'    : market,
        }
        record.update(kwargs)

        os.makedirs(_LOG_DIR, exist_ok=True)
        path = _log_path()
        line = json.dumps(record, default=str) + '\n'

        with _LOCK:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line)
    except Exception:
        pass  # audit must never crash the trading engine
