"""
CB6 Futures Core — MFF Flex $25K State Machine
Persists account state to data/futures/mff_flex_25k/state.json.
Tracks phase, equity, drawdown, trading days, payout history.
Isolated from all other CB6 state files.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import List, Optional

logger = logging.getLogger("cb6.futures.mff_flex.state")

STATE_PATH = os.path.join("data", "futures", "mff_flex_25k", "state.json")


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MFFFlexState:
    """
    Manages persistent state for the MFF Flex $25K account.
    Never shares state with forex, NSE, or GFT modules.
    """

    def __init__(self, state_path: str = STATE_PATH):
        self._path = state_path
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._state = self._load()

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("State load error: %s", e)
        return self._default_state()

    def _default_state(self) -> dict:
        return {
            "account_id": "MFF_FLEX_25K",
            "phase": "EVAL",                     # EVAL | FUNDED | BLOWN | PASSED
            "mode": "PAPER",                     # OFF | PAPER | BACKTEST | MANUAL_MONITOR | SEMI_AUTO
            "starting_equity": 25000.0,
            "current_equity": 25000.0,
            "peak_equity": 25000.0,
            "total_pnl": 0.0,
            "daily_pnl": 0.0,
            "best_day_pnl": 0.0,
            "trading_days": [],                  # list of date strings with ≥1 trade
            "last_trade_date": None,
            "last_eod_date": None,
            "daily_pnl_history": {},             # {date_str: pnl}
            "payout_count": 0,
            "payout_history": [],
            "last_payout_equity": 25000.0,
            "mll_locked": 0.0,                   # Maximum Loss Lock after first payout
            "open_trade_count": 0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "consecutive_losses": 0,
            "halted": False,
            "halt_reason": None,
            "created_at": _now(),
            "updated_at": _now(),
        }

    def save(self) -> None:
        self._state["updated_at"] = _now()
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    # ── Getters ────────────────────────────────────────────────────────────

    @property
    def phase(self) -> str:
        return self._state["phase"]

    @property
    def mode(self) -> str:
        return self._state["mode"]

    @property
    def current_equity(self) -> float:
        return self._state["current_equity"]

    @property
    def peak_equity(self) -> float:
        return self._state["peak_equity"]

    @property
    def total_pnl(self) -> float:
        return self._state["total_pnl"]

    @property
    def daily_pnl(self) -> float:
        return self._state["daily_pnl"]

    @property
    def best_day_pnl(self) -> float:
        return self._state["best_day_pnl"]

    @property
    def trading_days(self) -> List[str]:
        return self._state["trading_days"]

    @property
    def trading_day_count(self) -> int:
        return len(set(self._state["trading_days"]))

    @property
    def drawdown(self) -> float:
        return self._state["peak_equity"] - self._state["current_equity"]

    @property
    def halted(self) -> bool:
        return self._state["halted"]

    @property
    def consecutive_losses(self) -> int:
        return self._state["consecutive_losses"]

    # ── Mutations ──────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        allowed = {"OFF", "PAPER", "BACKTEST", "MANUAL_MONITOR", "SEMI_AUTO"}
        if mode not in allowed:
            raise ValueError(f"Invalid mode '{mode}'. LIVE_AUTO is disabled.")
        self._state["mode"] = mode
        self.save()

    def record_trade(
        self,
        pnl: float,
        trade_date: Optional[str] = None,
    ) -> None:
        td = trade_date or _today()
        self._state["daily_pnl"] = round(self._state["daily_pnl"] + pnl, 2)
        self._state["total_pnl"] = round(self._state["total_pnl"] + pnl, 2)
        self._state["current_equity"] = round(self._state["current_equity"] + pnl, 2)
        self._state["total_trades"] += 1
        self._state["last_trade_date"] = td

        if pnl > 0:
            self._state["winning_trades"] += 1
            self._state["consecutive_losses"] = 0
        else:
            self._state["losing_trades"] += 1
            self._state["consecutive_losses"] += 1

        if td not in self._state["trading_days"]:
            self._state["trading_days"].append(td)

        # Update daily history
        self._state["daily_pnl_history"][td] = round(
            self._state["daily_pnl_history"].get(td, 0.0) + pnl, 2
        )

        self.save()

    def end_of_day(self, eod_date: Optional[str] = None) -> None:
        """
        Call at session close.
        Updates peak equity (EOD trailing model), resets daily PnL, tracks best day.
        """
        td = eod_date or _today()
        today_pnl = self._state["daily_pnl"]

        # Peak equity ratchets up only at EOD
        if self._state["current_equity"] > self._state["peak_equity"]:
            self._state["peak_equity"] = self._state["current_equity"]

        # Best day tracking (for consistency rule)
        if today_pnl > self._state["best_day_pnl"]:
            self._state["best_day_pnl"] = round(today_pnl, 2)

        self._state["last_eod_date"] = td
        self._state["daily_pnl"] = 0.0
        self.save()
        logger.info("EOD: equity=%.2f peak=%.2f pnl_today=%.2f",
                    self._state["current_equity"],
                    self._state["peak_equity"],
                    today_pnl)

    def set_phase(self, phase: str) -> None:
        valid = {"EVAL", "FUNDED", "BLOWN", "PASSED"}
        if phase not in valid:
            raise ValueError(f"Invalid phase '{phase}'")
        self._state["phase"] = phase
        self.save()
        logger.info("Phase → %s", phase)

    def halt(self, reason: str) -> None:
        self._state["halted"] = True
        self._state["halt_reason"] = reason
        self.save()
        logger.warning("State HALTED: %s", reason)

    def resume(self) -> None:
        self._state["halted"] = False
        self._state["halt_reason"] = None
        self.save()
        logger.info("State resumed")

    def record_payout(self, amount: float) -> None:
        self._state["payout_count"] += 1
        self._state["payout_history"].append({
            "count": self._state["payout_count"],
            "amount": round(amount, 2),
            "date": _now(),
            "equity_at_payout": self._state["current_equity"],
        })
        self._state["last_payout_equity"] = self._state["current_equity"]
        # Lock MLL after first payout
        if self._state["payout_count"] == 1:
            from futures_engine.mff_flex_25k.mff_flex_config import PAYOUT_CONFIG
            self._state["mll_locked"] = PAYOUT_CONFIG.mll_after_first_payout
        self.save()

    def snapshot(self) -> dict:
        s = self._state
        return {
            "phase": s["phase"],
            "mode": s["mode"],
            "equity": s["current_equity"],
            "peak_equity": s["peak_equity"],
            "drawdown": round(self.drawdown, 2),
            "total_pnl": s["total_pnl"],
            "daily_pnl": s["daily_pnl"],
            "best_day_pnl": s["best_day_pnl"],
            "trading_days": self.trading_day_count,
            "total_trades": s["total_trades"],
            "win_rate": round(s["winning_trades"] / s["total_trades"], 4) if s["total_trades"] else 0,
            "consecutive_losses": s["consecutive_losses"],
            "halted": s["halted"],
            "halt_reason": s["halt_reason"],
            "payout_count": s["payout_count"],
            "mll_locked": s["mll_locked"],
        }

    def full_state(self) -> dict:
        return dict(self._state)
