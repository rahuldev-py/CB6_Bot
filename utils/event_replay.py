"""
Event Replay Engine — CB6 Quantum Phase 8
Answers "What usually happens under similar macro conditions?"
Moves CB6 from reactive to anticipatory market intelligence.

Data: data/event_library.json

Usage:
    from utils.event_replay import EventReplay
    er = EventReplay()

    # Find events similar to current conditions
    matches = er.find_similar(tags=["RISK_OFF", "GEOPOLITICAL"], severity="HIGH")

    # Average reaction across similar events
    avg = er.average_reaction(event_ids=["EVT002","EVT003"], asset="OIL", horizon="T+7")

    # Full playbook for current symbol
    playbook = er.playbook(symbol="XAGUSD", tags=["RISK_OFF"])

    python -m utils.event_replay      # CLI demo
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

LIB_PATH = Path(__file__).parent.parent / "data" / "event_library.json"

# Map trading symbols to event library asset keys
SYMBOL_TO_ASSET = {
    "XAUUSD":               "GOLD",
    "XAGUSD":               "SILVER",
    "USOIL":                "OIL",
    "EURUSD":               "EURUSD",
    "NSE:NIFTY50-INDEX":    "NIFTY",
    "NSE:NIFTYBANK-INDEX":  "NIFTYBANK",
    "NSE:FINNIFTY-INDEX":   "NIFTYIT",
    "NSE:MIDCPNIFTY-INDEX": "NIFTY",
}

HORIZONS = ["T+1", "T+7", "T+30", "T+90"]

SEVERITY_ORDER = {"LOW": 1, "MODERATE": 2, "HIGH": 3, "EXTREME": 4}
ANTICIPATION_ORDER = {"NONE": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class EventMatch:
    event_id:    str
    name:        str
    event_type:  str
    tags:        list[str]
    severity:    str
    similarity:  float       # 0-1 tag overlap score
    match_reason: str

    def to_dict(self) -> dict:
        return {
            "event_id":    self.event_id,
            "name":        self.name,
            "event_type":  self.event_type,
            "tags":        self.tags,
            "severity":    self.severity,
            "similarity":  round(self.similarity, 2),
            "match_reason": self.match_reason,
        }


@dataclass
class ReactionSummary:
    asset:          str
    horizon:        str
    avg_pct:        float        # average % change across matched events
    min_pct:        float
    max_pct:        float
    direction:      str          # UP | DOWN | MIXED
    sample_size:    int
    contributing_events: list[str]

    def to_dict(self) -> dict:
        return {
            "asset":     self.asset,
            "horizon":   self.horizon,
            "avg_pct":   round(self.avg_pct, 2),
            "min_pct":   round(self.min_pct, 2),
            "max_pct":   round(self.max_pct, 2),
            "direction": self.direction,
            "sample":    self.sample_size,
            "events":    self.contributing_events,
        }


@dataclass
class EventPlaybook:
    symbol:         str
    asset:          str
    matched_events: list[EventMatch]
    reactions:      dict[str, ReactionSummary]   # horizon → summary
    average_recovery_bars: Optional[float]        # business days (rough)
    recommendation: str
    confidence:     str   # HIGH (n≥5) | MODERATE (n≥3) | LOW (n<3)
    lessons:        list[str]

    def to_dict(self) -> dict:
        return {
            "symbol":          self.symbol,
            "asset":           self.asset,
            "matched_events":  [e.to_dict() for e in self.matched_events],
            "reactions":       {k: v.to_dict() for k, v in self.reactions.items()},
            "recovery_days_avg": self.average_recovery_bars,
            "recommendation":  self.recommendation,
            "confidence":      self.confidence,
            "lessons":         self.lessons,
        }


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

class EventReplay:
    """
    Event similarity and reaction analytics engine.
    Loads event_library.json on init — no external API calls.
    """

    def __init__(self, lib_path: str = None):
        path = Path(lib_path) if lib_path else LIB_PATH
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self._events: list[dict] = data.get("events", [])

    # ---------------------------------------------------------------------------
    # Similarity search
    # ---------------------------------------------------------------------------

    def find_similar(
        self,
        tags:          list[str] = None,
        event_type:    str = None,
        severity:      str = None,
        min_similarity: float = 0.2,
        top_n:         int = 5,
    ) -> list[EventMatch]:
        """
        Find events that match provided tags / type / severity.
        Returns top_n matches sorted by similarity descending.
        """
        results: list[EventMatch] = []
        query_tags = set(t.upper() for t in (tags or []))

        for ev in self._events:
            ev_tags     = set(t.upper() for t in ev.get("tags", []))
            ev_type     = ev.get("event_type", "").upper()
            ev_severity = ev.get("severity", "")

            # Tag overlap score: Jaccard index
            if query_tags:
                intersection = query_tags & ev_tags
                union        = query_tags | ev_tags
                tag_sim      = len(intersection) / len(union) if union else 0.0
            else:
                tag_sim = 0.5   # neutral if no tag filter

            # Type bonus
            type_bonus = 0.2 if (event_type and ev_type == event_type.upper()) else 0.0

            # Severity bonus
            sev_bonus = 0.1 if (severity and ev_severity.upper() == severity.upper()) else 0.0

            sim = min(1.0, tag_sim + type_bonus + sev_bonus)

            if sim < min_similarity:
                continue

            # Build match reason
            matched_tags = list(query_tags & ev_tags)
            reasons = []
            if matched_tags:
                reasons.append(f"tags: {', '.join(sorted(matched_tags))}")
            if type_bonus:
                reasons.append(f"type: {event_type}")
            if sev_bonus:
                reasons.append(f"severity: {severity}")
            match_reason = "; ".join(reasons) if reasons else "low similarity"

            results.append(EventMatch(
                event_id=ev["event_id"],
                name=ev["name"],
                event_type=ev.get("event_type", ""),
                tags=ev.get("tags", []),
                severity=ev.get("severity", ""),
                similarity=sim,
                match_reason=match_reason,
            ))

        return sorted(results, key=lambda x: x.similarity, reverse=True)[:top_n]

    # ---------------------------------------------------------------------------
    # Average reaction
    # ---------------------------------------------------------------------------

    def average_reaction(
        self,
        event_ids: list[str],
        asset:     str,             # e.g. "OIL", "GOLD", "NIFTY"
        horizon:   str = "T+7",
    ) -> Optional[ReactionSummary]:
        """
        Compute average % reaction for an asset across the given event IDs.
        """
        asset = asset.upper()
        values: list[float] = []
        contributing: list[str] = []

        event_map = {ev["event_id"]: ev for ev in self._events}

        for eid in event_ids:
            ev = event_map.get(eid)
            if not ev:
                continue
            reactions = ev.get("market_reactions", {})
            h_data    = reactions.get(horizon, {})
            if asset in h_data:
                values.append(float(h_data[asset]))
                contributing.append(eid)

        if not values:
            return None

        avg = sum(values) / len(values)
        direction = "UP" if avg > 1.0 else ("DOWN" if avg < -1.0 else "MIXED")

        return ReactionSummary(
            asset=asset,
            horizon=horizon,
            avg_pct=avg,
            min_pct=min(values),
            max_pct=max(values),
            direction=direction,
            sample_size=len(values),
            contributing_events=contributing,
        )

    # ---------------------------------------------------------------------------
    # Full playbook for a symbol
    # ---------------------------------------------------------------------------

    def playbook(
        self,
        symbol: str,
        tags:   list[str] = None,
        event_type: str = None,
        severity:   str = None,
        min_similarity: float = 0.15,
        top_n: int = 5,
    ) -> EventPlaybook:
        """
        Build a full reaction playbook for a trading symbol.
        Finds similar events, averages reactions across all horizons,
        assembles lessons.
        """
        asset = SYMBOL_TO_ASSET.get(symbol, symbol.upper())

        matches = self.find_similar(
            tags=tags, event_type=event_type,
            severity=severity, min_similarity=min_similarity,
            top_n=top_n,
        )

        event_ids = [m.event_id for m in matches]

        reactions: dict[str, ReactionSummary] = {}
        for h in HORIZONS:
            r = self.average_reaction(event_ids, asset, h)
            if r:
                reactions[h] = r

        # Confidence from sample size
        n = len(matches)
        confidence = "HIGH" if n >= 5 else ("MODERATE" if n >= 3 else "LOW")

        # Average recovery time (rough: resolution_phase start - acute_phase end)
        recovery_days: list[float] = []
        event_map = {ev["event_id"]: ev for ev in self._events}
        for eid in event_ids:
            ev = event_map.get(eid)
            if not ev:
                continue
            try:
                from datetime import date
                acute_end = date.fromisoformat(ev["acute_phase"]["end"])
                rec_start = date.fromisoformat(ev["recovery_phase"]["start"])
                recovery_days.append((rec_start - acute_end).days)
            except Exception:
                pass

        avg_recovery = round(sum(recovery_days) / len(recovery_days)) if recovery_days else None

        # Collect lessons from matched events
        lessons: list[str] = []
        for eid in event_ids:
            ev = event_map.get(eid)
            if ev:
                for lesson in ev.get("lessons", []):
                    if lesson not in lessons:
                        lessons.append(lesson)
        lessons = lessons[:8]  # cap at 8 most relevant

        # Build recommendation from T+7 reaction
        recommendation = "NO_DATA"
        if "T+7" in reactions:
            r7 = reactions["T+7"]
            if r7.direction == "UP" and r7.avg_pct > 3.0:
                recommendation = f"BULLISH BIAS: avg {r7.avg_pct:+.1f}% at T+7 across {r7.sample_size} events"
            elif r7.direction == "DOWN" and r7.avg_pct < -3.0:
                recommendation = f"BEARISH BIAS: avg {r7.avg_pct:+.1f}% at T+7 across {r7.sample_size} events"
            else:
                recommendation = f"MIXED: avg {r7.avg_pct:+.1f}% at T+7 — no clear edge"

        return EventPlaybook(
            symbol=symbol,
            asset=asset,
            matched_events=matches,
            reactions=reactions,
            average_recovery_bars=avg_recovery,
            recommendation=recommendation,
            confidence=confidence,
            lessons=lessons,
        )

    # ---------------------------------------------------------------------------
    # Quick lookups
    # ---------------------------------------------------------------------------

    def get_event(self, event_id: str) -> Optional[dict]:
        for ev in self._events:
            if ev["event_id"] == event_id:
                return ev
        return None

    def list_events(self) -> list[dict]:
        return [
            {
                "event_id":   ev["event_id"],
                "name":       ev["name"],
                "event_type": ev["event_type"],
                "severity":   ev["severity"],
                "tags":       ev.get("tags", []),
                "acute_start": ev["acute_phase"]["start"],
            }
            for ev in self._events
        ]

    def assets_in_library(self) -> set[str]:
        assets: set[str] = set()
        for ev in self._events:
            for h_data in ev.get("market_reactions", {}).values():
                assets.update(h_data.keys())
        return assets


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    er = EventReplay()

    print("=== CB6 Quantum — Event Replay Engine Demo ===\n")
    print(f"Loaded {len(er._events)} events\n")

    print("--- Scenario: RISK_OFF geopolitical shock, looking at XAGUSD ---")
    pb = er.playbook(
        symbol="XAGUSD",
        tags=["RISK_OFF", "GEOPOLITICAL"],
        severity="HIGH",
    )
    print(f"Matches ({pb.confidence} confidence): {[m.name for m in pb.matched_events]}")
    print(f"Recommendation: {pb.recommendation}")
    print(f"Avg recovery days: {pb.average_recovery_bars}")
    print("\nReactions:")
    for h, r in pb.reactions.items():
        print(f"  {h}: {r.asset} avg {r.avg_pct:+.2f}% [{r.direction}] (n={r.sample_size})")
    print("\nTop lessons:")
    for l in pb.lessons[:3]:
        print(f"  • {l}")

    print("\n--- Scenario: Fed hiking cycle, USOIL ---")
    pb2 = er.playbook(
        symbol="USOIL",
        tags=["RATE_HIKE", "FED", "DOLLAR_STRENGTH"],
    )
    print(f"Matches: {[m.name for m in pb2.matched_events]}")
    print(f"Recommendation: {pb2.recommendation}")

    print("\n--- All events in library ---")
    for ev in er.list_events():
        print(f"  {ev['event_id']}  {ev['acute_start']}  [{ev['severity']:8s}]  {ev['name']}")
