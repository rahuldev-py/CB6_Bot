# forex_engine/trade/duplicate_guard.py
# Deduplication — one trade per FVG zone per symbol per day.
# Persists seen-zones to disk so a process restart doesn't allow re-entry
# into the same FVG zone on the same trading day.

import json
import os
from datetime import datetime


class DuplicateGuard:
    """
    Prevent re-entering the same FVG zone twice in one day.
    Keyed by (date, direction, fvg_low_rounded).
    Auto-cleans stale keys from prior days.

    persist_path — optional path to a JSON file.  When given, the seen-set
                   is loaded on construction and written after every mark_seen()
                   call.  This survives process restarts within the same day.
    """

    def __init__(self, persist_path: str = None):
        self._seen: dict[str, set] = {}          # symbol → set of (date, direction, fvg_key)
        self._persist_path = persist_path
        if persist_path:
            self._load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self):
        """Load today's seen-zones from disk (ignore stale prior-day entries)."""
        try:
            if not os.path.exists(self._persist_path):
                return
            with open(self._persist_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            today = self._today()
            for sym, entries in raw.items():
                valid = set()
                for entry in entries:
                    tup = tuple(entry)
                    if len(tup) == 3 and tup[0] == today:
                        valid.add(tup)
                if valid:
                    self._seen[sym] = valid
        except Exception:
            pass  # corrupt or missing — start clean

    def _save(self):
        """Write current seen-zones to disk atomically."""
        if not self._persist_path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self._persist_path)), exist_ok=True)
            data = {sym: [list(k) for k in zones] for sym, zones in self._seen.items()}
            tmp = self._persist_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._persist_path)
        except Exception:
            pass  # non-fatal — dedup still works in-memory

    # ── Core interface ─────────────────────────────────────────────────────────

    def _today(self) -> str:
        return datetime.now().strftime('%Y-%m-%d')

    def _purge_stale(self, symbol: str):
        today = self._today()
        self._seen[symbol] = {k for k in self._seen.get(symbol, set()) if k[0] == today}

    def is_duplicate(self, symbol: str, direction: str, fvg_low: float) -> bool:
        fvg_key = round(fvg_low * 1000) / 1000
        key     = (self._today(), direction, fvg_key)
        self._purge_stale(symbol)
        return key in self._seen.get(symbol, set())

    def mark_seen(self, symbol: str, direction: str, fvg_low: float):
        fvg_key = round(fvg_low * 1000) / 1000
        key     = (self._today(), direction, fvg_key)
        self._purge_stale(symbol)
        self._seen.setdefault(symbol, set()).add(key)
        self._save()

    def reset(self, symbol: str = None):
        if symbol:
            self._seen[symbol] = set()
        else:
            self._seen.clear()
        self._save()
