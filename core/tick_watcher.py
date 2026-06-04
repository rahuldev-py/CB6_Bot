# core/tick_watcher.py — Pure tick→trigger evaluation engine.
# Maintains a list of "watch conditions" and fires callbacks when matched.
# Zero I/O. Thread-safe. Used by WebSocket feed but works with any tick source.
import threading
import time
from typing import Callable, Dict, List, Optional


# Trigger types
TRIGGER_BUY_ENTRY  = 'BUY_ENTRY'    # fire when LTP rises through entry price
TRIGGER_SELL_ENTRY = 'SELL_ENTRY'   # fire when LTP falls through entry price
TRIGGER_SL_LONG    = 'SL_LONG'      # long position, fire when LTP <= SL
TRIGGER_SL_SHORT   = 'SL_SHORT'     # short position, fire when LTP >= SL
TRIGGER_TP_LONG    = 'TP_LONG'      # long position, fire when LTP >= target
TRIGGER_TP_SHORT   = 'TP_SHORT'     # short position, fire when LTP <= target


class TickWatcher:
    """
    Thread-safe registry of price triggers.
    Each trigger fires its callback exactly once when its condition is met.
    Triggers can be tagged with metadata (trade_id, target_index, etc.).
    """

    def __init__(self):
        self._lock      = threading.Lock()
        # _watches[symbol] = list of dicts
        self._watches   : Dict[str, List[Dict]] = {}
        self._fired     : set = set()  # IDs of triggers already fired
        self._tick_count = 0
        self._fire_count = 0
        self._last_tick_ts = 0.0

    # ────────────────────────────────────────────────────────────────────
    #   REGISTRATION
    # ────────────────────────────────────────────────────────────────────
    def watch(self, trigger_id: str, symbol: str, kind: str, level: float,
              callback: Callable[[Dict], None], meta: Optional[Dict] = None) -> bool:
        """
        Register a trigger. Returns False if trigger_id already exists.
        - kind: one of TRIGGER_* constants
        - level: price threshold
        - callback: called with dict {symbol, trigger_id, ltp, kind, meta}
        """
        with self._lock:
            if trigger_id in self._fired:
                return False
            for w in self._watches.get(symbol, []):
                if w['trigger_id'] == trigger_id:
                    return False  # duplicate
            self._watches.setdefault(symbol, []).append({
                'trigger_id': trigger_id,
                'kind'      : kind,
                'level'     : float(level),
                'callback'  : callback,
                'meta'      : meta or {},
                'created_at': time.time(),
            })
        return True

    def cancel(self, trigger_id: str) -> bool:
        """Remove a pending trigger by ID."""
        with self._lock:
            for sym, watches in list(self._watches.items()):
                before = len(watches)
                self._watches[sym] = [w for w in watches if w['trigger_id'] != trigger_id]
                if not self._watches[sym]:
                    del self._watches[sym]
                if len(self._watches.get(sym, [])) < before:
                    return True
        return False

    def cancel_symbol(self, symbol: str) -> int:
        """Cancel all watches for a symbol. Returns count cancelled."""
        with self._lock:
            n = len(self._watches.get(symbol, []))
            self._watches.pop(symbol, None)
            return n

    def clear(self):
        """Remove all watches and fired-tracking. Use between sessions."""
        with self._lock:
            self._watches.clear()
            self._fired.clear()
            self._tick_count = 0
            self._fire_count = 0

    # ────────────────────────────────────────────────────────────────────
    #   EVALUATION
    # ────────────────────────────────────────────────────────────────────
    def on_tick(self, symbol: str, ltp: float, ts: float = None) -> List[Dict]:
        """
        Process a tick. Returns list of fired-trigger dicts.
        Callbacks are called OUTSIDE the lock to avoid deadlocks.
        """
        with self._lock:
            self._tick_count += 1
            self._last_tick_ts = ts or time.time()
            watches = self._watches.get(symbol, [])
            if not watches:
                return []

            fired_now = []
            remaining = []
            for w in watches:
                if self._is_triggered(w, ltp):
                    fired_now.append(w)
                    self._fired.add(w['trigger_id'])
                    self._fire_count += 1
                else:
                    remaining.append(w)
            if remaining:
                self._watches[symbol] = remaining
            else:
                del self._watches[symbol]

        # Fire callbacks outside the lock
        results = []
        for w in fired_now:
            payload = {
                'symbol'    : symbol,
                'trigger_id': w['trigger_id'],
                'ltp'       : ltp,
                'kind'      : w['kind'],
                'level'     : w['level'],
                'meta'      : w['meta'],
            }
            try:
                w['callback'](payload)
            except Exception as e:
                # Don't let a bad callback crash the whole watcher
                payload['error'] = str(e)
            results.append(payload)
        return results

    @staticmethod
    def _is_triggered(watch: Dict, ltp: float) -> bool:
        kind = watch['kind']
        level = watch['level']
        if kind in (TRIGGER_BUY_ENTRY, TRIGGER_SL_SHORT, TRIGGER_TP_LONG):
            return ltp >= level
        if kind in (TRIGGER_SELL_ENTRY, TRIGGER_SL_LONG, TRIGGER_TP_SHORT):
            return ltp <= level
        return False

    # ────────────────────────────────────────────────────────────────────
    #   INTROSPECTION
    # ────────────────────────────────────────────────────────────────────
    def status(self) -> Dict:
        with self._lock:
            symbol_count = len(self._watches)
            watch_count  = sum(len(v) for v in self._watches.values())
            return {
                'symbols_watched' : symbol_count,
                'active_watches'  : watch_count,
                'ticks_processed' : self._tick_count,
                'triggers_fired'  : self._fire_count,
                'last_tick_age_s' : round(time.time() - self._last_tick_ts, 1)
                                    if self._last_tick_ts else None,
            }

    def list_watches(self) -> List[Dict]:
        """Return a list of all active watches (for debugging)."""
        with self._lock:
            out = []
            for sym, watches in self._watches.items():
                for w in watches:
                    out.append({
                        'symbol'    : sym,
                        'trigger_id': w['trigger_id'],
                        'kind'      : w['kind'],
                        'level'     : w['level'],
                        'meta'      : w['meta'],
                    })
            return out


# Singleton instance for the bot
_global_watcher = TickWatcher()


def get_watcher() -> TickWatcher:
    return _global_watcher
