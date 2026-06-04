"""
CB6 Futures Core — News Guard
Blackout windows around high-impact US macro events.
Events: CPI, NFP, FOMC, PPI, GDP, ISM, Retail Sales, Powell speeches.

MFF Flex allows news trading — this guard is CB6-internal policy, not an MFF rule.
Configuration: blackout_before_minutes and blackout_after_minutes are adjustable.

Data sources:
  1. data/futures/news/news_calendar.json  — user-maintained calendar (primary)
  2. Inline 2024-2026 schedule as fallback
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger("cb6.futures.news_guard")

NEWS_CALENDAR_PATH = "data/futures/news/news_calendar.json"


class NewsImpact(str, Enum):
    HIGH    = "HIGH"     # NFP, CPI, FOMC → full blackout
    MEDIUM  = "MEDIUM"   # PPI, GDP, ISM → reduced blackout
    LOW     = "LOW"      # Regional data → no blackout by default


@dataclass
class NewsEvent:
    event_id: str
    name: str
    impact: NewsImpact
    scheduled_utc: datetime
    actual_released: bool = False
    description: str = ""
    source: str = "calendar"


@dataclass
class NewsBlackoutWindow:
    event: NewsEvent
    start_utc: datetime
    end_utc: datetime

    def active(self, dt: datetime) -> bool:
        return self.start_utc <= dt.astimezone(timezone.utc) < self.end_utc

    def minutes_to_event(self, dt: datetime) -> float:
        diff = (self.event.scheduled_utc - dt.astimezone(timezone.utc)).total_seconds()
        return diff / 60.0


# ── Inline fallback calendar 2024-2026 ────────────────────────────────────────
# All times UTC. NFP = first Friday of month 13:30 UTC.
# FOMC = 8 meetings/year, decision day 19:00 UTC + Powell presser 19:30 UTC.
# CPI = mid-month, 13:30 UTC.

def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


INLINE_CALENDAR: List[dict] = [
    # ── 2025 ──────────────────────────────────────────────────────────────
    # NFP
    {"name":"NFP","impact":"HIGH","time":"2025-01-10T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-02-07T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-03-07T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-04-04T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-05-02T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-06-06T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-07-04T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-08-01T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-09-05T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-10-03T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-11-07T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2025-12-05T13:30:00"},
    # CPI
    {"name":"CPI","impact":"HIGH","time":"2025-01-15T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-02-12T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-03-12T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-04-10T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-05-13T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-06-11T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-07-15T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-08-13T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-09-10T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-10-15T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-11-12T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2025-12-10T13:30:00"},
    # FOMC decisions
    {"name":"FOMC","impact":"HIGH","time":"2025-01-29T19:00:00"},
    {"name":"FOMC","impact":"HIGH","time":"2025-03-19T18:00:00"},
    {"name":"FOMC","impact":"HIGH","time":"2025-05-07T18:00:00"},
    {"name":"FOMC","impact":"HIGH","time":"2025-06-18T18:00:00"},
    {"name":"FOMC","impact":"HIGH","time":"2025-07-30T18:00:00"},
    {"name":"FOMC","impact":"HIGH","time":"2025-09-17T18:00:00"},
    {"name":"FOMC","impact":"HIGH","time":"2025-10-29T18:00:00"},
    {"name":"FOMC","impact":"HIGH","time":"2025-12-10T19:00:00"},
    # PPI
    {"name":"PPI","impact":"MEDIUM","time":"2025-01-14T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-02-13T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-03-13T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-04-11T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-05-14T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-06-12T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-07-11T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-08-14T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-09-11T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-10-16T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-11-13T13:30:00"},
    {"name":"PPI","impact":"MEDIUM","time":"2025-12-11T13:30:00"},
    # GDP (advance, second, third)
    {"name":"GDP_ADV","impact":"HIGH","time":"2025-01-30T13:30:00"},
    {"name":"GDP_ADV","impact":"HIGH","time":"2025-04-30T13:30:00"},
    {"name":"GDP_ADV","impact":"HIGH","time":"2025-07-30T13:30:00"},
    {"name":"GDP_ADV","impact":"HIGH","time":"2025-10-30T13:30:00"},
    # ── 2026 (partial, add full list when available) ───────────────────────
    {"name":"NFP","impact":"HIGH","time":"2026-01-09T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2026-02-06T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2026-03-06T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2026-04-03T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2026-05-01T13:30:00"},
    {"name":"NFP","impact":"HIGH","time":"2026-06-05T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2026-01-14T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2026-02-11T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2026-03-11T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2026-04-10T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2026-05-13T13:30:00"},
    {"name":"CPI","impact":"HIGH","time":"2026-06-10T13:30:00"},
    {"name":"FOMC","impact":"HIGH","time":"2026-01-28T19:00:00"},
    {"name":"FOMC","impact":"HIGH","time":"2026-03-18T18:00:00"},
    {"name":"FOMC","impact":"HIGH","time":"2026-05-06T18:00:00"},
    {"name":"FOMC","impact":"HIGH","time":"2026-06-17T18:00:00"},
]


def _load_inline_calendar() -> List[NewsEvent]:
    events = []
    for i, row in enumerate(INLINE_CALENDAR):
        impact = NewsImpact(row["impact"])
        events.append(NewsEvent(
            event_id=f"INLINE_{i:04d}",
            name=row["name"],
            impact=impact,
            scheduled_utc=_dt(row["time"]),
            source="inline",
        ))
    return events


def _load_file_calendar(path: str) -> List[NewsEvent]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        events = []
        for i, row in enumerate(raw):
            impact_str = row.get("impact", "HIGH").upper()
            try:
                impact = NewsImpact(impact_str)
            except ValueError:
                impact = NewsImpact.MEDIUM
            events.append(NewsEvent(
                event_id=row.get("id", f"FILE_{i:04d}"),
                name=row["name"],
                impact=impact,
                scheduled_utc=_dt(row["time"]),
                actual_released=row.get("released", False),
                description=row.get("description", ""),
                source="file",
            ))
        return events
    except Exception as e:
        logger.warning("News calendar load error (%s): %s", path, e)
        return []


class FuturesNewsGuard:
    """
    Maintains an event calendar and answers blackout queries.

    Default blackout windows (CB6 internal policy):
        HIGH impact:   30 min before → 15 min after
        MEDIUM impact: 15 min before → 10 min after
        LOW impact:    0  min before →  0 min after  (no blackout)

    All values are configurable.
    """

    def __init__(
        self,
        calendar_path: str = NEWS_CALENDAR_PATH,
        blackout_high_before: int = 30,
        blackout_high_after: int  = 15,
        blackout_med_before: int  = 15,
        blackout_med_after: int   = 10,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self._blackout_cfg: Dict[NewsImpact, tuple[int, int]] = {
            NewsImpact.HIGH:   (blackout_high_before, blackout_high_after),
            NewsImpact.MEDIUM: (blackout_med_before,  blackout_med_after),
            NewsImpact.LOW:    (0, 0),
        }

        os.makedirs(os.path.dirname(calendar_path), exist_ok=True)

        # File calendar takes precedence; inline fills gaps
        file_events = _load_file_calendar(calendar_path)
        inline_events = _load_inline_calendar()

        # Deduplicate: if file calendar covers same (name, date), prefer file
        file_keys = {
            (e.name, e.scheduled_utc.date())
            for e in file_events
        }
        merged = file_events + [
            e for e in inline_events
            if (e.name, e.scheduled_utc.date()) not in file_keys
        ]
        self._events: List[NewsEvent] = sorted(merged, key=lambda e: e.scheduled_utc)
        logger.info("NewsGuard loaded %d events (%d file, %d inline)",
                    len(self._events), len(file_events), len(inline_events))

    def _build_window(self, event: NewsEvent) -> NewsBlackoutWindow:
        before_min, after_min = self._blackout_cfg[event.impact]
        return NewsBlackoutWindow(
            event=event,
            start_utc=event.scheduled_utc - timedelta(minutes=before_min),
            end_utc=event.scheduled_utc + timedelta(minutes=after_min),
        )

    def in_blackout(self, dt: datetime) -> tuple[bool, Optional[NewsBlackoutWindow]]:
        """
        Returns (in_blackout: bool, window: NewsBlackoutWindow | None).
        Checks all events within ±4 hours of dt.
        """
        if not self.enabled:
            return False, None

        dt_utc = dt.astimezone(timezone.utc)
        search_start = dt_utc - timedelta(hours=4)
        search_end   = dt_utc + timedelta(hours=4)

        for event in self._events:
            if not (search_start <= event.scheduled_utc <= search_end):
                continue
            window = self._build_window(event)
            if window.active(dt_utc):
                return True, window

        return False, None

    def next_event(self, dt: datetime, impact: Optional[NewsImpact] = None) -> Optional[NewsEvent]:
        """Return the next upcoming event (optionally filtered by impact)."""
        dt_utc = dt.astimezone(timezone.utc)
        for event in self._events:
            if event.scheduled_utc <= dt_utc:
                continue
            if impact and event.impact != impact:
                continue
            return event
        return None

    def upcoming_events(
        self,
        dt: datetime,
        hours_ahead: int = 24,
        min_impact: NewsImpact = NewsImpact.MEDIUM,
    ) -> List[NewsEvent]:
        """Events in the next N hours at or above min_impact."""
        dt_utc = dt.astimezone(timezone.utc)
        cutoff = dt_utc + timedelta(hours=hours_ahead)
        impact_rank = {NewsImpact.HIGH: 3, NewsImpact.MEDIUM: 2, NewsImpact.LOW: 1}
        min_rank = impact_rank[min_impact]
        return [
            e for e in self._events
            if dt_utc <= e.scheduled_utc <= cutoff
            and impact_rank[e.impact] >= min_rank
        ]

    def add_event(
        self,
        name: str,
        impact: str,
        scheduled_utc: datetime,
        description: str = "",
        persist: bool = True,
        calendar_path: str = NEWS_CALENDAR_PATH,
    ) -> NewsEvent:
        """Add a one-off event (e.g. Powell speech) and optionally persist it."""
        event = NewsEvent(
            event_id=f"MANUAL_{len(self._events):04d}",
            name=name,
            impact=NewsImpact(impact.upper()),
            scheduled_utc=scheduled_utc.astimezone(timezone.utc),
            description=description,
            source="manual",
        )
        self._events.append(event)
        self._events.sort(key=lambda e: e.scheduled_utc)

        if persist:
            self._save_to_file(calendar_path)

        return event

    def _save_to_file(self, path: str) -> None:
        manual_events = [e for e in self._events if e.source in ("manual", "file")]
        data = [
            {
                "id": e.event_id,
                "name": e.name,
                "impact": e.impact.value,
                "time": e.scheduled_utc.isoformat(),
                "description": e.description,
                "released": e.actual_released,
            }
            for e in manual_events
        ]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def blackout_summary(self, dt: datetime) -> dict:
        in_bo, window = self.in_blackout(dt)
        next_ev = self.next_event(dt, NewsImpact.HIGH)
        return {
            "in_blackout": in_bo,
            "blackout_event": window.event.name if window else None,
            "blackout_until_utc": window.end_utc.isoformat() if window else None,
            "next_high_impact": next_ev.name if next_ev else None,
            "next_high_impact_utc": next_ev.scheduled_utc.isoformat() if next_ev else None,
            "minutes_to_next": (
                (next_ev.scheduled_utc - dt.astimezone(timezone.utc)).total_seconds() / 60
                if next_ev else None
            ),
        }
