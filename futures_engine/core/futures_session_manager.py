"""
CB6 Futures Core — Session Manager
Knows RTH, ETH, pre-open, holiday, half-day, and rollover-week status
for every CME/COMEX/NYMEX/CBOT futures symbol.

All times are UTC internally. Caller converts if needed.
CME Group: Sunday 17:00 CT → Friday 16:00 CT (23h/day, 1h break 16:00-17:00 CT)
CT = UTC-5 (winter) / UTC-6 (summer). We use UTC offsets throughout.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Dict, FrozenSet, Optional, Tuple

import zoneinfo  # stdlib 3.9+


CT  = zoneinfo.ZoneInfo("America/Chicago")
ET  = zoneinfo.ZoneInfo("America/New_York")
UTC = timezone.utc


class SessionType(str, Enum):
    RTH          = "RTH"           # Regular Trading Hours
    ETH          = "ETH"           # Extended / Globex overnight
    PRE_OPEN     = "PRE_OPEN"      # 15-min window before RTH open
    POST_CLOSE   = "POST_CLOSE"    # 15-min window after RTH close
    CLOSED       = "CLOSED"        # Weekday 1-hour CME break, or weekend
    HOLIDAY      = "HOLIDAY"       # Exchange closed
    HALF_DAY     = "HALF_DAY"      # Shortened session (day before holiday)
    ROLLOVER     = "ROLLOVER"      # Active rollover week (ETH/RTH still open)


@dataclass
class SessionWindow:
    session: SessionType
    start_utc: datetime
    end_utc: datetime
    symbol: str
    note: str = ""

    def contains(self, dt: datetime) -> bool:
        dt_utc = dt.astimezone(UTC)
        return self.start_utc <= dt_utc < self.end_utc


# ── CME Holiday Calendar (US market holidays) ─────────────────────────────────
# Full-day CME closures — equity index, metals, energy all close on these dates.
# Add new years at the start of each year.
CME_HOLIDAYS: FrozenSet[date] = frozenset([
    # 2024
    date(2024, 1, 1),   # New Year's Day
    date(2024, 1, 15),  # MLK Day
    date(2024, 2, 19),  # Presidents' Day
    date(2024, 3, 29),  # Good Friday
    date(2024, 5, 27),  # Memorial Day
    date(2024, 6, 19),  # Juneteenth
    date(2024, 7, 4),   # Independence Day
    date(2024, 9, 2),   # Labor Day
    date(2024, 11, 28), # Thanksgiving
    date(2024, 12, 25), # Christmas
    # 2025
    date(2025, 1, 1),
    date(2025, 1, 20),
    date(2025, 2, 17),
    date(2025, 4, 18),
    date(2025, 5, 26),
    date(2025, 6, 19),
    date(2025, 7, 4),
    date(2025, 9, 1),
    date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),   # Independence Day observed
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
])

# Half-day sessions (early close at 12:00 CT = 18:00 UTC winter / 17:00 UTC summer)
CME_HALF_DAYS: FrozenSet[date] = frozenset([
    # 2024
    date(2024, 7, 3),   # Day before July 4
    date(2024, 11, 29), # Day after Thanksgiving
    date(2024, 12, 24), # Christmas Eve
    # 2025
    date(2025, 7, 3),
    date(2025, 11, 28),
    date(2025, 12, 24),
    # 2026
    date(2026, 7, 2),
    date(2026, 11, 27),
    date(2026, 12, 24),
])


# ── Symbol session definitions ────────────────────────────────────────────────
# Times are (open_utc_offset_winter, close_utc_offset_winter) in hours.
# Winter = CT is UTC-6; summer = CT is UTC-5.
# "RTH" is approximate pit/electronic RTH window used for CB6 signal filtering.

@dataclass
class SymbolSessionSpec:
    globex_open_ct:  time    # 24h Globex open CT (typically 17:00)
    globex_close_ct: time    # 24h Globex close CT (typically 16:00)
    rth_open_ct:     time    # RTH open CT
    rth_close_ct:    time    # RTH close CT
    break_start_ct:  time    # Daily break start (Globex pause)
    break_end_ct:    time    # Daily break end


SYMBOL_SESSIONS: Dict[str, SymbolSessionSpec] = {
    # Equity index: CME, 24h Globex, RTH 08:30-15:15 CT
    "ES":  SymbolSessionSpec(time(17,0), time(16,0), time(8,30),  time(15,15), time(16,0), time(17,0)),
    "MES": SymbolSessionSpec(time(17,0), time(16,0), time(8,30),  time(15,15), time(16,0), time(17,0)),
    "NQ":  SymbolSessionSpec(time(17,0), time(16,0), time(8,30),  time(15,15), time(16,0), time(17,0)),
    "MNQ": SymbolSessionSpec(time(17,0), time(16,0), time(8,30),  time(15,15), time(16,0), time(17,0)),
    "RTY": SymbolSessionSpec(time(17,0), time(16,0), time(8,30),  time(15,15), time(16,0), time(17,0)),
    "M2K": SymbolSessionSpec(time(17,0), time(16,0), time(8,30),  time(15,15), time(16,0), time(17,0)),
    "YM":  SymbolSessionSpec(time(17,0), time(16,0), time(7,20),  time(15,15), time(16,0), time(17,0)),
    "MYM": SymbolSessionSpec(time(17,0), time(16,0), time(7,20),  time(15,15), time(16,0), time(17,0)),
    # Gold/Silver: COMEX, 24h Globex, RTH 07:20-13:30 CT
    "GC":  SymbolSessionSpec(time(17,0), time(16,0), time(7,20),  time(13,30), time(16,0), time(17,0)),
    "MGC": SymbolSessionSpec(time(17,0), time(16,0), time(7,20),  time(13,30), time(16,0), time(17,0)),
    "SI":  SymbolSessionSpec(time(17,0), time(16,0), time(7,20),  time(13,25), time(16,0), time(17,0)),
    "SIL": SymbolSessionSpec(time(17,0), time(16,0), time(7,20),  time(13,25), time(16,0), time(17,0)),
    # Crude oil: NYMEX, 24h Globex, RTH 08:00-14:30 CT
    "CL":  SymbolSessionSpec(time(17,0), time(16,0), time(8,0),   time(14,30), time(16,0), time(17,0)),
    "MCL": SymbolSessionSpec(time(17,0), time(16,0), time(8,0),   time(14,30), time(16,0), time(17,0)),
    # Treasuries: CBOT, RTH 07:20-14:00 CT
    "ZN":  SymbolSessionSpec(time(17,0), time(16,0), time(7,20),  time(14,0),  time(16,0), time(17,0)),
    "ZB":  SymbolSessionSpec(time(17,0), time(16,0), time(7,20),  time(14,0),  time(16,0), time(17,0)),
}

# Fallback spec for unknown symbols
_DEFAULT_SPEC = SymbolSessionSpec(time(17,0), time(16,0), time(8,30), time(15,15), time(16,0), time(17,0))


# ── CB6 Kill-Zone windows (UTC, approximate — adjusted per DST) ───────────────
# Used by signal scanner to restrict entries to high-probability windows.
# Times are CT; conversion to UTC happens in classify_datetime().

CB6_KILLZONES_CT: Dict[str, Tuple[time, time]] = {
    "LONDON_OPEN":  (time(2, 0),  time(5, 0)),    # 02-05 CT ≈ 08-11 London
    "NY_OPEN":      (time(8, 30), time(10, 30)),   # NYSE cash open
    "NY_LUNCH":     (time(11,30), time(13, 0)),    # London close / NY midday
    "NY_AFTERNOON": (time(13, 0), time(15, 15)),   # Afternoon session
}


def _to_utc(dt_ct: datetime) -> datetime:
    """Convert a CT-aware datetime to UTC."""
    return dt_ct.astimezone(UTC)


def _ct_time_on_date(t: time, d: date) -> datetime:
    """Return a UTC datetime for time t (CT) on date d."""
    ct_naive = datetime.combine(d, t)
    ct_aware = ct_naive.replace(tzinfo=CT)
    return ct_aware.astimezone(UTC)


class FuturesSessionManager:
    """
    Classifies any datetime + symbol combination into a SessionType.
    Also exposes helpers for kill-zone checks, holiday detection,
    half-day detection, and rollover-week flags.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self._spec = SYMBOL_SESSIONS.get(self.symbol, _DEFAULT_SPEC)

    # ── Primary classification ─────────────────────────────────────────────

    def classify(self, dt: datetime) -> SessionType:
        """
        Return the SessionType for the given datetime.
        dt may be in any timezone — internally converted to CT for comparison.
        """
        dt_utc = dt.astimezone(UTC)
        dt_ct  = dt_utc.astimezone(CT)
        d = dt_ct.date()
        t = dt_ct.time()

        # Weekend: Friday 16:00 CT → Sunday 17:00 CT
        weekday = d.weekday()   # 0=Mon … 6=Sun
        if weekday == 5:        # Saturday — always closed
            return SessionType.CLOSED
        if weekday == 6 and t < self._spec.globex_open_ct:
            return SessionType.CLOSED
        if weekday == 4 and t >= self._spec.globex_close_ct:
            return SessionType.CLOSED

        # Holiday
        if d in CME_HOLIDAYS:
            return SessionType.HOLIDAY

        # Half-day: closes at 12:00 CT
        if d in CME_HALF_DAYS:
            half_close = time(12, 0)
            if t >= half_close:
                return SessionType.CLOSED
            # Still determine whether it's RTH or ETH up to early close
            if self._spec.rth_open_ct <= t < half_close:
                return SessionType.HALF_DAY
            return SessionType.ETH

        # Daily 1-hour Globex break
        if self._spec.break_start_ct <= t < self._spec.break_end_ct:
            return SessionType.CLOSED

        # RTH window
        if self._spec.rth_open_ct <= t < self._spec.rth_close_ct:
            # 15-min pre-open
            spec = self._spec
            pre_open_start = (
                datetime.combine(d, spec.rth_open_ct, tzinfo=CT)
                - timedelta(minutes=15)
            ).time()
            if pre_open_start <= t < spec.rth_open_ct:
                return SessionType.PRE_OPEN
            return SessionType.RTH

        # Post-close window (15 min after RTH close)
        post_close_end = (
            datetime.combine(d, self._spec.rth_close_ct, tzinfo=CT)
            + timedelta(minutes=15)
        ).time()
        if self._spec.rth_close_ct <= t < post_close_end:
            return SessionType.POST_CLOSE

        # Everything else during the 23h Globex window = ETH
        return SessionType.ETH

    # ── Kill-zone check ────────────────────────────────────────────────────

    def in_killzone(self, dt: datetime) -> Optional[str]:
        """Return kill-zone name if dt falls inside one, else None."""
        dt_ct = dt.astimezone(CT)
        t = dt_ct.time()
        for name, (open_t, close_t) in CB6_KILLZONES_CT.items():
            if open_t <= t < close_t:
                return name
        return None

    def is_tradeable(self, dt: datetime, rth_only: bool = False) -> bool:
        """
        True if the session is open for trading.
        rth_only=True: only RTH counts (metals pit, signals preference).
        """
        session = self.classify(dt)
        if session in (SessionType.HOLIDAY, SessionType.CLOSED):
            return False
        if rth_only:
            return session in (SessionType.RTH, SessionType.HALF_DAY)
        return session not in (SessionType.HOLIDAY, SessionType.CLOSED)

    # ── Rollover week ──────────────────────────────────────────────────────

    def is_rollover_week(self, dt: datetime, rollover_days_before: int = 5) -> bool:
        """
        True if dt is within the rollover window for the current front-month contract.
        During rollover week volume migrates to the next contract.
        """
        from futures_engine.core.futures_contract_manager import (
            front_month, expiry_date, should_rollover
        )
        d = dt.astimezone(UTC).date()
        return should_rollover(self.symbol, d)

    def rollover_note(self, dt: datetime) -> str:
        if not self.is_rollover_week(dt):
            return ""
        from futures_engine.core.futures_contract_manager import (
            front_month, expiry_date
        )
        d = dt.astimezone(UTC).date()
        yr, mo = front_month(self.symbol, d)
        exp = expiry_date(self.symbol, yr, mo)
        return f"ROLLOVER WEEK — {self.symbol} expires {exp.isoformat()}"

    # ── Batch helper ───────────────────────────────────────────────────────

    def filter_tradeable_bars(
        self,
        bars: list,
        rth_only: bool = False,
        require_killzone: bool = False,
    ) -> list:
        """
        Filter a list of FuturesBar objects to tradeable bars only.
        Optionally restrict to kill-zone windows.
        """
        result = []
        for bar in bars:
            if not self.is_tradeable(bar.timestamp, rth_only=rth_only):
                continue
            if require_killzone and self.in_killzone(bar.timestamp) is None:
                continue
            result.append(bar)
        return result

    # ── Summary ────────────────────────────────────────────────────────────

    def session_summary(self, dt: datetime) -> dict:
        """Full session metadata for a given datetime."""
        session = self.classify(dt)
        kz = self.in_killzone(dt)
        rv = self.is_rollover_week(dt)
        return {
            "symbol": self.symbol,
            "timestamp_utc": dt.astimezone(UTC).isoformat(),
            "timestamp_ct": dt.astimezone(CT).isoformat(),
            "session": session.value,
            "killzone": kz,
            "is_rollover_week": rv,
            "rollover_note": self.rollover_note(dt) if rv else "",
            "tradeable": self.is_tradeable(dt),
            "tradeable_rth_only": self.is_tradeable(dt, rth_only=True),
        }


# ── Convenience factory ────────────────────────────────────────────────────────

def session_for(symbol: str) -> FuturesSessionManager:
    return FuturesSessionManager(symbol)


def classify_bar_session(symbol: str, dt: datetime) -> SessionType:
    return FuturesSessionManager(symbol).classify(dt)
