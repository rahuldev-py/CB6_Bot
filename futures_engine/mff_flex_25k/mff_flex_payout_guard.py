"""
CB6 Futures Core — MFF Flex Payout Guard
Tracks payout eligibility, MLL lock, and 80/20 split calculation.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from futures_engine.mff_flex_25k.mff_flex_config import PAYOUT_CONFIG, MFFFlexPayoutConfig
from futures_engine.mff_flex_25k.mff_flex_rules import MFFFlexRuleEngine
from futures_engine.mff_flex_25k.mff_flex_state import MFFFlexState

logger = logging.getLogger("cb6.futures.mff_flex.payout_guard")


@dataclass
class PayoutRequest:
    amount_requested: float
    amount_after_split: float    # 80% goes to trader
    amount_retained: float       # 20% MFF share
    eligible: bool
    blocking_reasons: List[str]
    metrics: dict = field(default_factory=dict)


class MFFFlexPayoutGuard:
    """
    Manages payout tracking and eligibility for MFF Flex funded account.
    Enforces: 5-day minimum, $100/day minimum, $250 net between payouts,
    $1000 max request, 80/20 split, MLL lock.
    """

    def __init__(
        self,
        state: MFFFlexState,
        cfg: MFFFlexPayoutConfig = PAYOUT_CONFIG,
    ):
        self._state = state
        self._cfg = cfg
        self._rule_engine = MFFFlexRuleEngine()

    def check_eligibility(self, requested_amount: Optional[float] = None) -> PayoutRequest:
        state = self._state
        cfg = self._cfg
        full = state.full_state()

        trading_days = state.trading_day_count
        daily_history = full.get("daily_pnl_history", {})
        daily_profits = list(daily_history.values())
        net_since_last = state.total_pnl - (full.get("last_payout_equity", full["starting_equity"]) -
                                             full["starting_equity"])
        payout_count = full.get("payout_count", 0)
        total_requestable = max(0.0, state.total_pnl)

        check = self._rule_engine.check_payout_eligibility(
            trading_days=trading_days,
            daily_profits=daily_profits,
            net_profit_since_last_payout=net_since_last,
            total_requestable_profit=total_requestable,
            payout_count=payout_count,
        )

        requestable = check.metrics.get("max_request", 0.0)
        if requested_amount is None:
            requested_amount = requestable

        blocking: List[str] = [v.detail for v in check.violations]

        # Validate requested amount is within allowed range
        if requested_amount < cfg.min_payout_amount:
            blocking.append(f"Requested ${requested_amount:.2f} below minimum ${cfg.min_payout_amount}")
        if requested_amount > requestable:
            requested_amount = requestable
        if requested_amount > cfg.max_payout_amount:
            requested_amount = cfg.max_payout_amount

        eligible = len(blocking) == 0 and requested_amount >= cfg.min_payout_amount

        after_split = round(requested_amount * cfg.profit_split, 2)
        retained = round(requested_amount - after_split, 2)

        return PayoutRequest(
            amount_requested=round(requested_amount, 2),
            amount_after_split=after_split,
            amount_retained=retained,
            eligible=eligible,
            blocking_reasons=blocking,
            metrics=check.metrics,
        )

    def process_payout(self, amount: float) -> PayoutRequest:
        """
        Process a payout if eligible.
        Updates state with MLL lock after first payout.
        """
        req = self.check_eligibility(amount)
        if not req.eligible:
            logger.warning("Payout denied: %s", req.blocking_reasons)
            return req

        self._state.record_payout(amount)
        logger.info(
            "Payout processed: $%.2f requested | $%.2f to trader (80%%) | $%.2f MFF (20%%)",
            amount, req.amount_after_split, req.amount_retained
        )
        return req

    def mll_floor(self) -> float:
        """
        Return the current Maximum Loss Lock floor.
        After first payout, equity cannot fall below (first_payout_equity - $100).
        """
        full = self._state.full_state()
        mll = full.get("mll_locked", 0.0)
        if mll > 0:
            payout_history = full.get("payout_history", [])
            if payout_history:
                equity_at_first = payout_history[0].get("equity_at_payout", 0.0)
                return equity_at_first - mll
        return 0.0

    def summary(self) -> dict:
        req = self.check_eligibility()
        return {
            "eligible": req.eligible,
            "max_requestable": req.amount_requested,
            "after_split": req.amount_after_split,
            "blocking_reasons": req.blocking_reasons,
            "metrics": req.metrics,
            "mll_floor": self.mll_floor(),
            "payout_count": self._state.full_state().get("payout_count", 0),
        }
