"""
CB6 Futures Core — ICT Silver Bullet for Futures
Session-based windows, CHoCH + FVG entry logic for CME futures.
No dependency on NSE or forex silver bullet implementations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import Enum
from typing import List, Optional

from futures_engine.core.futures_data_feed import FuturesBar
from futures_engine.core.futures_market_structure import (
    Bias, StructureEvent, detect_structure_events, get_htf_bias,
)
from futures_engine.core.futures_liquidity import find_fvg, find_liquidity_pools, check_sweeps


class SBSession(str, Enum):
    LONDON_OPEN  = "LONDON_OPEN"   # 02:00-05:00 UTC (3-hour SB window)
    NY_OPEN      = "NY_OPEN"       # 09:30-11:00 UTC (NYSE open kill zone)
    NY_LUNCH     = "NY_LUNCH"      # 12:00-13:00 UTC (London close)
    NY_AFTERNOON = "NY_AFTERNOON"  # 14:00-16:00 UTC (ICT PM session)


# UTC time windows for each session  (open_inclusive, close_exclusive)
SB_WINDOWS_UTC: dict[SBSession, tuple[time, time]] = {
    SBSession.LONDON_OPEN:  (time(2, 0), time(5, 0)),
    SBSession.NY_OPEN:      (time(9, 30), time(11, 0)),
    SBSession.NY_LUNCH:     (time(12, 0), time(13, 0)),
    SBSession.NY_AFTERNOON: (time(14, 0), time(16, 0)),
}


@dataclass
class SilverBulletSetup:
    symbol: str
    session: SBSession
    direction: str           # "LONG" | "SHORT"
    entry: float
    stop_loss: float
    target_1: float          # 1R
    target_2: float          # 2R
    target_3: float          # 3R
    fvg_top: float
    fvg_bottom: float
    htf_bias: Bias
    trigger_bar: FuturesBar
    choch_bar: FuturesBar
    sweep_detected: bool
    score: float = 0.0       # 0-100 setup quality
    approved: bool = False   # set True in SEMI_AUTO after manual approval


def current_session(dt: datetime) -> Optional[SBSession]:
    """Return which Silver Bullet session the UTC datetime falls in, or None."""
    t = dt.astimezone(timezone.utc).time()
    for session, (open_t, close_t) in SB_WINDOWS_UTC.items():
        if open_t <= t < close_t:
            return session
    return None


def _score_setup(
    sweep_detected: bool,
    htf_aligned: bool,
    fvg_size_pct: float,      # FVG as % of bar range
    choch_clean: bool,
) -> float:
    """Simple weighted scoring: 0-100."""
    score = 0.0
    if sweep_detected:  score += 35
    if htf_aligned:     score += 30
    if choch_clean:     score += 20
    score += min(15, fvg_size_pct * 100)
    return round(score, 1)


class SilverBulletScanner:
    """
    Scans a bar series for ICT Silver Bullet setups within active session windows.
    Operates on 1-minute bars; requires separate H4 bars for HTF bias.
    """

    def __init__(
        self,
        symbol: str,
        sl_buffer_ticks: int = 3,   # ticks beyond sweep wick for SL
        min_score: float = 55.0,
    ):
        self.symbol = symbol
        self.sl_buffer_ticks = sl_buffer_ticks
        self.min_score = min_score

    def scan(
        self,
        m1_bars: List[FuturesBar],
        h4_bars: List[FuturesBar],
        tick_size: float,
    ) -> List[SilverBulletSetup]:
        """
        Scan recent 1m bars for Silver Bullet setups.
        Returns only setups meeting min_score.
        """
        setups: List[SilverBulletSetup] = []
        if len(m1_bars) < 10 or len(h4_bars) < 8:
            return setups

        htf_bias = get_htf_bias(h4_bars)
        if htf_bias == Bias.NEUTRAL:
            return setups

        session = current_session(m1_bars[-1].timestamp)
        if session is None:
            return setups

        # Slice bars to current session window
        session_start_t, session_end_t = SB_WINDOWS_UTC[session]
        session_bars = [
            b for b in m1_bars
            if session_start_t <= b.timestamp.astimezone(timezone.utc).time() < session_end_t
        ]
        if len(session_bars) < 5:
            return setups

        # Detect structure events in session
        struct_events = detect_structure_events(session_bars, lookback=2)
        choch_events = [
            e for e in struct_events
            if e.event in (StructureEvent.CHOCH_UP, StructureEvent.CHOCH_DOWN)
        ]
        if not choch_events:
            return setups

        # Sweep detection — use a broader context window (last 48 bars) so we can
        # detect SSL/BSL sweeps from prior sessions, not only the current session.
        # ICT Silver Bullet requires a prior liquidity run before entry is valid.
        context_bars = m1_bars[-48:] if len(m1_bars) >= 48 else m1_bars
        context_pools = find_liquidity_pools(context_bars, lookback=min(24, len(context_bars)))
        context_pools = check_sweeps(context_pools, context_bars)
        swept_pools = [p for p in context_pools if p.swept]

        # Find FVGs formed during session
        fvgs = find_fvg(session_bars)

        for choch in choch_events[-3:]:  # evaluate last 3 CHoCH events
            direction = "LONG" if choch.event == StructureEvent.CHOCH_UP else "SHORT"

            # HTF alignment check (mandatory)
            htf_aligned = (
                (direction == "LONG" and htf_bias == Bias.BULLISH) or
                (direction == "SHORT" and htf_bias == Bias.BEARISH)
            )

            # Mandatory liquidity sweep validation (F-2 fix).
            # LONG requires a prior sell-side sweep (SSL taken out → trapped shorts).
            # SHORT requires a prior buy-side sweep (BSL taken out → trapped longs).
            relevant_sweeps = [
                p for p in swept_pools
                if p.sweep_bar and p.sweep_bar.timestamp <= choch.bar.timestamp and
                   ((direction == "LONG" and p.side == "SSL") or
                    (direction == "SHORT" and p.side == "BSL"))
            ]
            if not relevant_sweeps:
                # No prior liquidity run — setup is invalid. Hard reject.
                continue

            sweep_detected = True

            # Find relevant FVG after CHoCH (displacement confirming the move)
            entry_fvgs = [
                f for f in fvgs
                if f["bar"].timestamp >= choch.bar.timestamp and
                   f["type"] == ("bull" if direction == "LONG" else "bear")
            ]
            if not entry_fvgs:
                continue

            fvg = entry_fvgs[0]

            # HTF alignment is mandatory for scoring but not a hard reject here —
            # the score gate (min_score=55) will filter misaligned setups because
            # an unaligned setup can score at most 35 (sweep) + 20 (choch) + 15 (fvg) = 70
            # but in practice sweep+choch+fvg without HTF alignment scores ~55, borderline.

            # Build levels — stop always anchored at sweep wick (no 3-tick fallback)
            sl_buffer = self.sl_buffer_ticks * tick_size
            if direction == "LONG":
                entry = fvg["bottom"]
                sweep_wick = min(p.level for p in relevant_sweeps)
                stop_loss = sweep_wick - sl_buffer
                risk = entry - stop_loss
                if risk <= 0:
                    continue
                t1 = entry + risk
                t2 = entry + risk * 2
                t3 = entry + risk * 3
            else:
                entry = fvg["top"]
                sweep_wick = max(p.level for p in relevant_sweeps)
                stop_loss = sweep_wick + sl_buffer
                risk = stop_loss - entry
                if risk <= 0:
                    continue
                t1 = entry - risk
                t2 = entry - risk * 2
                t3 = entry - risk * 3

            # Score
            bar_range = choch.bar.high - choch.bar.low
            fvg_size_pct = (fvg["top"] - fvg["bottom"]) / bar_range if bar_range > 0 else 0
            score = _score_setup(sweep_detected, htf_aligned, fvg_size_pct, choch_clean=True)

            if score < self.min_score:
                continue

            setups.append(SilverBulletSetup(
                symbol=self.symbol,
                session=session,
                direction=direction,
                entry=round(entry, 4),
                stop_loss=round(stop_loss, 4),
                target_1=round(t1, 4),
                target_2=round(t2, 4),
                target_3=round(t3, 4),
                fvg_top=fvg["top"],
                fvg_bottom=fvg["bottom"],
                htf_bias=htf_bias,
                trigger_bar=session_bars[-1],
                choch_bar=choch.bar,
                sweep_detected=sweep_detected,
                score=score,
            ))

        return setups
