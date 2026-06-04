"""
CB6 Futures Core — MFF Flex Risk Guard
MFF-specific risk gate wired to internal guards + MFF rule engine.
Hard kill-switches that operate independently of strategy signals.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from futures_engine.core.futures_risk_guard import (
    FuturesRiskGuard, KillSwitchReason, RiskGuardConfig,
)
from futures_engine.core.futures_drawdown_guard import EODDrawdownGuard
from futures_engine.mff_flex_25k.mff_flex_config import (
    EVAL_CONFIG, FUNDED_CONFIG, GUARDS_CONFIG, PAYOUT_CONFIG,
)
from futures_engine.mff_flex_25k.mff_flex_state import MFFFlexState
from futures_engine.mff_flex_25k.mff_flex_rules import MFFFlexRuleEngine

logger = logging.getLogger("cb6.futures.mff_flex.risk_guard")


def build_mff_risk_guard(phase: str = "EVAL") -> FuturesRiskGuard:
    """
    Factory: build a FuturesRiskGuard configured for MFF Flex internal guards.
    Uses CB6-internal limits (fires BEFORE official MFF limits).
    """
    g = GUARDS_CONFIG
    max_total = (
        EVAL_CONFIG.max_drawdown if phase == "EVAL"
        else FUNDED_CONFIG.max_drawdown
    )
    cfg = RiskGuardConfig(
        daily_loss_limit=g.daily_hard_stop_usd,
        daily_loss_warning=g.daily_warning_usd,
        daily_loss_reduce=g.daily_reduce_usd,
        max_consecutive_losses=g.max_consecutive_losses,
        max_contracts=EVAL_CONFIG.max_contracts,
        allow_overnight=False,
        news_blackout_minutes=0,    # MFF Flex allows news trading
        session_lockout_enabled=False,
        max_total_loss=g.total_halt_usd,
    )
    return FuturesRiskGuard(cfg)


class MFFFlexRiskGuard:
    """
    Combined risk guard for MFF Flex $25K.
    Integrates: base FuturesRiskGuard + EOD drawdown guard + MFF rule engine.
    """

    def __init__(self, state: MFFFlexState):
        self._state = state
        self._base_guard = build_mff_risk_guard(state.phase)
        self._dd_guard = EODDrawdownGuard(
            starting_equity=state.current_equity,
            max_drawdown=EVAL_CONFIG.max_drawdown if state.phase == "EVAL"
                         else FUNDED_CONFIG.max_drawdown,
            warning_threshold=0.70,
        )
        self._rule_engine = MFFFlexRuleEngine()

    def allow_trade(
        self,
        contracts: int,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).
        Checks: state halt → daily hard stop → drawdown → MFF rules.
        """
        now = now or datetime.now(timezone.utc)

        # 1. State-level halt
        if self._state.halted:
            return False, f"Account halted: {self._state.full_state().get('halt_reason', 'unknown')}"

        # 2. Phase blown
        if self._state.phase in ("BLOWN", "PASSED"):
            return False, f"Account phase is {self._state.phase} — no trading"

        # 3. Base risk guard (daily loss + consecutive losses)
        allowed, reason, msg = self._base_guard.allow_trade(contracts, now)
        if not allowed:
            return False, msg

        # 4. Drawdown guard
        if self._dd_guard.is_breached():
            return False, f"Internal drawdown limit breached (${abs(self._dd_guard.current_drawdown):.2f})"

        # 5. Contracts cap
        max_c = GUARDS_CONFIG.max_trade_contracts
        if contracts > max_c:
            return False, f"Contracts {contracts} > Phase 1 max {max_c}"

        # 6. MFF rule check
        rule_result = self._rule_engine.check_eval(
            current_equity=self._state.current_equity,
            peak_equity=self._state.peak_equity,
            daily_pnl=self._state.daily_pnl,
            total_pnl=self._state.total_pnl,
            trading_days=self._state.trading_day_count,
            best_day_pnl=self._state.best_day_pnl,
        )
        if not rule_result.passed:
            reasons = [v.detail for v in rule_result.violations]
            return False, "MFF rule violations: " + "; ".join(reasons)

        return True, "OK"

    def record_trade(self, pnl: float) -> None:
        """Call after every trade closes."""
        self._base_guard.record_trade(pnl)
        self._dd_guard.update_intraday(self._state.current_equity + pnl)
        self._state.record_trade(pnl)

        # Auto-halt on consecutive losses
        if self._state.consecutive_losses >= GUARDS_CONFIG.max_consecutive_losses:
            self._state.halt(f"Consecutive loss limit ({GUARDS_CONFIG.max_consecutive_losses}) reached")

        # Auto-halt on internal daily stop
        daily_loss = abs(min(self._state.daily_pnl, 0))
        if daily_loss >= GUARDS_CONFIG.daily_hard_stop_usd:
            self._state.halt(f"Daily internal stop hit: -${daily_loss:.2f}")

    def end_of_day(self) -> None:
        """Call at session close."""
        snap = self._dd_guard.end_of_day(self._state.current_equity)
        self._state.end_of_day()
        self._base_guard.reset_daily()

        # Check if MFF drawdown breached
        if snap.at_limit:
            self._state.set_phase("BLOWN")
            self._state.halt("MFF max drawdown breached — account blown")
            logger.error("MFF FLEX BLOWN: drawdown $%.2f ≥ limit", abs(snap.drawdown))

        # Check if eval target passed
        if (self._state.phase == "EVAL" and
                self._state.total_pnl >= EVAL_CONFIG.profit_target and
                self._state.trading_day_count >= EVAL_CONFIG.min_trading_days):
            # Consistency check before marking passed
            check = self._rule_engine.check_eval(
                current_equity=self._state.current_equity,
                peak_equity=self._state.peak_equity,
                daily_pnl=0.0,
                total_pnl=self._state.total_pnl,
                trading_days=self._state.trading_day_count,
                best_day_pnl=self._state.best_day_pnl,
            )
            if check.passed:
                self._state.set_phase("PASSED")
                logger.info("MFF FLEX EVAL PASSED! PnL=$%.2f", self._state.total_pnl)

    def should_reduce_size(self) -> bool:
        return self._base_guard.should_reduce_size()

    def should_warn(self) -> bool:
        return self._base_guard.should_warn() or self._dd_guard.is_warning()

    def snapshot(self) -> dict:
        return {
            "base_guard": self._base_guard.snapshot(),
            "drawdown_guard": self._dd_guard.snapshot(),
            "state": self._state.snapshot(),
        }
