# forex_engine/prop_firms/gft/gft_anti_hft_guard.py
# GFT Anti-HFT guard — min time between trades, max trades/hour, min hold time.

from datetime import datetime
from collections import deque
from typing import Optional
from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE


class AntiHFTGuard:
    """
    Enforces GFT anti-HFT rules:
    - Minimum seconds between consecutive entry signals
    - Maximum trades per rolling 60-minute window
    - Minimum hold time check (compared at exit, not entry)
    """

    def __init__(self):
        cfg = GFT_2STEP_PROFILE
        self._min_gap_secs   = cfg['min_seconds_between_trades']
        self._max_per_hour   = cfg['max_trades_per_hour']
        self._min_hold_secs  = cfg['minimum_hold_time_seconds']
        self._entry_times    = deque()  # rolling 60-min entry timestamps
        self._last_entry_ts: Optional[datetime] = None

    def can_enter(self) -> tuple[bool, str]:
        """Check if a new entry is allowed right now."""
        now = datetime.now()

        # Min gap between trades
        if self._last_entry_ts is not None:
            elapsed = (now - self._last_entry_ts).total_seconds()
            if elapsed < self._min_gap_secs:
                remaining = int(self._min_gap_secs - elapsed)
                return False, (
                    f"MIN GAP — last entry {elapsed:.0f}s ago, "
                    f"{remaining}s remaining (min {self._min_gap_secs}s)"
                )

        # Max trades per hour
        self._purge_old()
        if len(self._entry_times) >= self._max_per_hour:
            return False, (
                f"MAX TRADES/HOUR — {len(self._entry_times)} entries in last 60 min "
                f"(max {self._max_per_hour})"
            )

        return True, 'OK'

    def record_entry(self):
        """Call after a trade is opened."""
        now = datetime.now()
        self._last_entry_ts = now
        self._entry_times.append(now)

    def _purge_old(self):
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(hours=1)
        while self._entry_times and self._entry_times[0] < cutoff:
            self._entry_times.popleft()

    def check_min_hold(self, entry_time_str: str) -> tuple[bool, int]:
        """
        Check if minimum hold time has elapsed before closing.
        Returns (ok, seconds_remaining).
        """
        try:
            entry_dt  = datetime.strptime(entry_time_str, '%Y-%m-%d %H:%M:%S')
            elapsed   = (datetime.now() - entry_dt).total_seconds()
            remaining = max(0, int(self._min_hold_secs - elapsed))
            return elapsed >= self._min_hold_secs, remaining
        except Exception:
            return True, 0  # if we can't parse, don't block
