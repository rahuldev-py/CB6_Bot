"""
CB6 Futures Core — Drawdown Guard
EOD trailing drawdown model used by MFF Flex and other prop firms.
Tracks peak equity and enforces drawdown limits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional


@dataclass
class DrawdownSnapshot:
    date: str
    equity: float
    peak_equity: float
    drawdown: float       # negative number, e.g. -350.00
    drawdown_pct: float   # as decimal, e.g. -0.014
    at_limit: bool


class EODDrawdownGuard:
    """
    End-of-day trailing drawdown model.
    Peak equity only updates at EOD (not intraday).
    Halt triggers when (peak_equity - current_equity) >= max_drawdown.
    """

    def __init__(
        self,
        starting_equity: float,
        max_drawdown: float,           # absolute USD, positive number
        warning_threshold: float = 0.7,  # warn at 70% of max drawdown used
    ):
        self.starting_equity = starting_equity
        self.max_drawdown = max_drawdown
        self.warning_threshold = warning_threshold

        self._peak_equity = starting_equity
        self._current_equity = starting_equity
        self._last_eod_date: Optional[date] = None
        self._history: List[DrawdownSnapshot] = []

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def current_equity(self) -> float:
        return self._current_equity

    @property
    def current_drawdown(self) -> float:
        return self._current_equity - self._peak_equity  # negative if underwater

    @property
    def remaining_drawdown(self) -> float:
        """How much more drawdown is allowed before limit breach."""
        return self.max_drawdown + self.current_drawdown  # e.g. 1000 + (-350) = 650

    @property
    def drawdown_used_pct(self) -> float:
        return abs(self.current_drawdown) / self.max_drawdown if self.max_drawdown > 0 else 0.0

    def update_intraday(self, equity: float) -> None:
        """Update current equity during the trading session (does NOT move peak)."""
        self._current_equity = equity

    def end_of_day(self, equity: float, eod_date: Optional[date] = None) -> DrawdownSnapshot:
        """
        Call at EOD after all positions are closed.
        Peak equity ratchets up if today's equity is higher than peak.
        """
        self._current_equity = equity
        eod = eod_date or date.today()

        if equity > self._peak_equity:
            self._peak_equity = equity

        dd = self.current_drawdown
        snap = DrawdownSnapshot(
            date=eod.isoformat(),
            equity=equity,
            peak_equity=self._peak_equity,
            drawdown=round(dd, 2),
            drawdown_pct=round(dd / self._peak_equity, 6) if self._peak_equity else 0,
            at_limit=abs(dd) >= self.max_drawdown,
        )
        self._history.append(snap)
        self._last_eod_date = eod
        return snap

    def is_breached(self) -> bool:
        return abs(self.current_drawdown) >= self.max_drawdown

    def is_warning(self) -> bool:
        return self.drawdown_used_pct >= self.warning_threshold and not self.is_breached()

    def snapshot(self) -> dict:
        return {
            "peak_equity": self._peak_equity,
            "current_equity": self._current_equity,
            "drawdown": round(self.current_drawdown, 2),
            "remaining": round(self.remaining_drawdown, 2),
            "used_pct": round(self.drawdown_used_pct * 100, 1),
            "breached": self.is_breached(),
            "warning": self.is_warning(),
        }

    def history(self) -> List[DrawdownSnapshot]:
        return list(self._history)
