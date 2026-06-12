# utils/latency_monitor.py
# Order-to-fill latency tracker. Records the wall-clock time between
# signal detection and confirmed MT5 fill for every trade.
# Alerts via logger.warning when latency exceeds thresholds.
#
# Usage:
#   from utils.latency_monitor import LatencyMonitor
#   _lat = LatencyMonitor(account='gft_5k')
#   token = _lat.start('gft_5k_trade_id')
#   ... place order ...
#   _lat.finish(token, ticket=12345)

from __future__ import annotations

import time
import threading
from datetime import datetime, timezone
from utils.logger import logger
from utils.audit_log import append as _audit

# Latency thresholds (seconds)
_WARN_LATENCY_S  = 3.0    # warn: fill took more than 3s
_ALERT_LATENCY_S = 10.0   # alert: fill took more than 10s (broker connectivity issue)


class LatencyMonitor:
    def __init__(self, account: str = 'unknown', market: str = 'forex'):
        self._account = account
        self._market  = market
        self._pending: dict[str, float] = {}
        self._lock    = threading.Lock()

    def start(self, trade_id: str) -> str:
        """Record signal-detected time. Returns the trade_id as token."""
        with self._lock:
            self._pending[trade_id] = time.perf_counter()
        return trade_id

    def finish(self, trade_id: str, ticket: int = 0, symbol: str = ''):
        """Record fill time. Logs and audits the latency."""
        with self._lock:
            start_t = self._pending.pop(trade_id, None)
        if start_t is None:
            return
        latency = round(time.perf_counter() - start_t, 3)
        level = (
            'ALERT' if latency >= _ALERT_LATENCY_S else
            'WARN'  if latency >= _WARN_LATENCY_S  else
            'OK'
        )
        msg = (
            f"Order latency {level}: {latency:.3f}s "
            f"trade={trade_id} ticket={ticket} {symbol} [{self._account}]"
        )
        if level == 'ALERT':
            logger.warning(msg)
        elif level == 'WARN':
            logger.info(msg)
        else:
            logger.debug(msg)

        _audit('LATENCY', self._account, self._market,
               trade_id=trade_id, ticket=ticket, symbol=symbol,
               latency_s=latency, level=level)

    def cancel(self, trade_id: str):
        """Discard a pending latency record (e.g., order was rolled back)."""
        with self._lock:
            self._pending.pop(trade_id, None)
