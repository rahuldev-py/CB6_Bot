"""
CB6 Futures Core — Risk Guard
Broker-agnostic kill-switches and guard rails.
Operates independently of strategy signals.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import List, Optional

logger = logging.getLogger("cb6.futures.risk_guard")


class KillSwitchReason(str, Enum):
    DAILY_LOSS_LIMIT       = "DAILY_LOSS_LIMIT"
    DRAWDOWN_LIMIT         = "DRAWDOWN_LIMIT"
    CONSECUTIVE_LOSSES     = "CONSECUTIVE_LOSSES"
    MAX_POSITION_SIZE      = "MAX_POSITION_SIZE"
    SESSION_LOCKOUT        = "SESSION_LOCKOUT"
    NEWS_LOCKOUT           = "NEWS_LOCKOUT"
    OVERNIGHT_BLOCKED      = "OVERNIGHT_BLOCKED"
    MANUAL_HALT            = "MANUAL_HALT"
    INACTIVITY             = "INACTIVITY"


@dataclass
class RiskEvent:
    reason: KillSwitchReason
    timestamp: datetime
    detail: str
    value: float = 0.0


@dataclass
class RiskGuardConfig:
    daily_loss_limit: float          # USD, absolute — halt day trading
    daily_loss_warning: float        # USD — emit warning, reduce size
    daily_loss_reduce: float         # USD — halve lot size
    max_consecutive_losses: int      # halt after N back-to-back losses
    max_contracts: int               # hard cap per trade
    allow_overnight: bool            # False = flat before session close
    news_blackout_minutes: int       # 0 = news trading allowed
    session_lockout_enabled: bool    # halt outside of kill zone
    max_total_loss: Optional[float] = None  # overall account stop


class FuturesRiskGuard:
    """
    Central risk gate for futures trading.
    All order requests must pass `allow_trade()` before execution.
    """

    def __init__(self, config: RiskGuardConfig):
        self.cfg = config
        self._halted: bool = False
        self._halt_reason: Optional[KillSwitchReason] = None
        self._events: List[RiskEvent] = []
        self._daily_pnl: float = 0.0
        self._total_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._last_trade_date: Optional[date] = None
        self._news_times: List[datetime] = []  # upcoming high-impact news UTC

    # ── Daily reset ────────────────────────────────────────────────────────

    def reset_daily(self) -> None:
        self._daily_pnl = 0.0
        self._halted = False
        self._halt_reason = None
        self._last_trade_date = date.today()
        logger.info("Risk guard daily reset")

    # ── Trade update ───────────────────────────────────────────────────────

    def record_trade(self, pnl: float) -> None:
        self._daily_pnl += pnl
        self._total_pnl += pnl
        self._last_trade_date = date.today()

        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        self._evaluate_guards()

    def _evaluate_guards(self) -> None:
        cfg = self.cfg

        if abs(min(self._daily_pnl, 0)) >= cfg.daily_loss_limit:
            self._trigger_halt(KillSwitchReason.DAILY_LOSS_LIMIT,
                               f"Daily PnL ${self._daily_pnl:.2f} hit limit ${cfg.daily_loss_limit}")

        if self._consecutive_losses >= cfg.max_consecutive_losses:
            self._trigger_halt(KillSwitchReason.CONSECUTIVE_LOSSES,
                               f"{self._consecutive_losses} consecutive losses")

        if cfg.max_total_loss and abs(min(self._total_pnl, 0)) >= cfg.max_total_loss:
            self._trigger_halt(KillSwitchReason.DRAWDOWN_LIMIT,
                               f"Total PnL ${self._total_pnl:.2f} hit max loss ${cfg.max_total_loss}")

    def _trigger_halt(self, reason: KillSwitchReason, detail: str) -> None:
        if not self._halted:
            self._halted = True
            self._halt_reason = reason
            evt = RiskEvent(reason=reason, timestamp=datetime.now(timezone.utc), detail=detail)
            self._events.append(evt)
            logger.warning("KILL-SWITCH: %s — %s", reason.value, detail)

    # ── News management ────────────────────────────────────────────────────

    def set_news_times(self, times: List[datetime]) -> None:
        self._news_times = times

    def _in_news_blackout(self, now: datetime) -> bool:
        if self.cfg.news_blackout_minutes == 0:
            return False
        window = timedelta(minutes=self.cfg.news_blackout_minutes)
        for news_time in self._news_times:
            if abs((now - news_time).total_seconds()) <= window.total_seconds():
                return True
        return False

    # ── Gate check ─────────────────────────────────────────────────────────

    def allow_trade(
        self,
        contracts: int,
        now: Optional[datetime] = None,
    ) -> tuple[bool, Optional[KillSwitchReason], str]:
        """
        Returns (allowed, reason, message).
        Call this before every order submission.
        """
        now = now or datetime.now(timezone.utc)

        if self._halted:
            return False, self._halt_reason, f"Trading halted: {self._halt_reason}"

        if contracts > self.cfg.max_contracts:
            return False, KillSwitchReason.MAX_POSITION_SIZE, \
                   f"Requested {contracts} contracts > max {self.cfg.max_contracts}"

        if self._in_news_blackout(now):
            return False, KillSwitchReason.NEWS_LOCKOUT, \
                   "Within news blackout window"

        daily_loss = abs(min(self._daily_pnl, 0))
        if daily_loss >= self.cfg.daily_loss_limit:
            return False, KillSwitchReason.DAILY_LOSS_LIMIT, \
                   f"Daily loss ${daily_loss:.2f} at limit"

        return True, None, "OK"

    def should_reduce_size(self) -> bool:
        return abs(min(self._daily_pnl, 0)) >= self.cfg.daily_loss_reduce

    def should_warn(self) -> bool:
        return abs(min(self._daily_pnl, 0)) >= self.cfg.daily_loss_warning

    def manual_halt(self, reason: str = "manual") -> None:
        self._trigger_halt(KillSwitchReason.MANUAL_HALT, reason)

    def manual_resume(self) -> None:
        if self._halt_reason == KillSwitchReason.MANUAL_HALT:
            self._halted = False
            self._halt_reason = None
            logger.info("Manual resume: risk guard cleared")

    # ── State ──────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        return {
            "halted": self._halted,
            "halt_reason": self._halt_reason.value if self._halt_reason else None,
            "daily_pnl": round(self._daily_pnl, 2),
            "total_pnl": round(self._total_pnl, 2),
            "consecutive_losses": self._consecutive_losses,
            "should_warn": self.should_warn(),
            "should_reduce": self.should_reduce_size(),
        }

    def events(self) -> List[dict]:
        return [
            {
                "reason": e.reason.value,
                "timestamp": e.timestamp.isoformat(),
                "detail": e.detail,
            }
            for e in self._events
        ]
