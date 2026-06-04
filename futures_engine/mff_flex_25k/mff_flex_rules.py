"""
CB6 Futures Core — MFF Flex Rule Engine
Validates account state against MFF Flex $25K rules.
Returns pass/fail + violation details for each rule category.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from futures_engine.mff_flex_25k.mff_flex_config import (
    EVAL_CONFIG, FUNDED_CONFIG, PAYOUT_CONFIG, MFFFlexEvalConfig,
    MFFFlexFundedConfig, MFFFlexPayoutConfig,
)


@dataclass
class RuleViolation:
    rule: str
    detail: str
    current_value: float
    limit_value: float
    breach_pct: float   # how close to limit (1.0 = at limit, >1.0 = breached)


@dataclass
class RuleCheckResult:
    phase: str              # "EVAL" | "FUNDED"
    passed: bool
    violations: List[RuleViolation] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class MFFFlexRuleEngine:
    """
    Stateless rule validator.
    Pass current account metrics → receive pass/fail + violation list.
    """

    def check_eval(
        self,
        current_equity: float,
        peak_equity: float,
        daily_pnl: float,
        total_pnl: float,
        trading_days: int,
        best_day_pnl: float,
        cfg: MFFFlexEvalConfig = EVAL_CONFIG,
    ) -> RuleCheckResult:
        violations: List[RuleViolation] = []
        warnings: List[str] = []

        # 1. Drawdown check (EOD trailing)
        drawdown = peak_equity - current_equity
        if drawdown >= cfg.max_drawdown:
            violations.append(RuleViolation(
                rule="MAX_DRAWDOWN",
                detail=f"Drawdown ${drawdown:.2f} ≥ limit ${cfg.max_drawdown}",
                current_value=drawdown,
                limit_value=cfg.max_drawdown,
                breach_pct=drawdown / cfg.max_drawdown,
            ))
        elif drawdown >= cfg.max_drawdown * 0.80:
            warnings.append(f"Drawdown warning: ${drawdown:.2f} / ${cfg.max_drawdown} (80% used)")

        # 2. Consistency rule — no single day > 50% of total profit
        if total_pnl > 0 and best_day_pnl > 0:
            best_day_share = best_day_pnl / total_pnl
            if best_day_share > cfg.consistency_rule_pct:
                violations.append(RuleViolation(
                    rule="CONSISTENCY",
                    detail=f"Best day ${best_day_pnl:.2f} = {best_day_share:.0%} of total profit ${total_pnl:.2f} (limit 50%)",
                    current_value=best_day_share,
                    limit_value=cfg.consistency_rule_pct,
                    breach_pct=best_day_share / cfg.consistency_rule_pct,
                ))

        # 3. Profit target check (informational)
        if total_pnl >= cfg.profit_target and trading_days >= cfg.min_trading_days:
            warnings.append(
                f"EVAL PASSED: profit ${total_pnl:.2f} ≥ target ${cfg.profit_target} "
                f"with {trading_days} trading days"
            )

        passed = len(violations) == 0
        return RuleCheckResult(
            phase="EVAL",
            passed=passed,
            violations=violations,
            warnings=warnings,
            metrics={
                "equity": current_equity,
                "peak_equity": peak_equity,
                "drawdown": round(drawdown, 2),
                "total_pnl": round(total_pnl, 2),
                "profit_target": cfg.profit_target,
                "pnl_to_target": round(cfg.profit_target - total_pnl, 2),
                "trading_days": trading_days,
                "min_trading_days": cfg.min_trading_days,
                "best_day_pnl": round(best_day_pnl, 2),
                "consistency_ok": not any(v.rule == "CONSISTENCY" for v in violations),
            },
        )

    def check_funded(
        self,
        current_equity: float,
        peak_equity: float,
        days_since_last_trade: int,
        total_pnl: float,
        cfg: MFFFlexFundedConfig = FUNDED_CONFIG,
    ) -> RuleCheckResult:
        violations: List[RuleViolation] = []
        warnings: List[str] = []

        # 1. Drawdown
        drawdown = peak_equity - current_equity
        if drawdown >= cfg.max_drawdown:
            violations.append(RuleViolation(
                rule="MAX_DRAWDOWN",
                detail=f"Funded account drawdown ${drawdown:.2f} ≥ ${cfg.max_drawdown}",
                current_value=drawdown,
                limit_value=cfg.max_drawdown,
                breach_pct=drawdown / cfg.max_drawdown,
            ))

        # 2. Inactivity
        if days_since_last_trade >= cfg.inactivity_days:
            violations.append(RuleViolation(
                rule="INACTIVITY",
                detail=f"{days_since_last_trade} days since last trade (limit {cfg.inactivity_days})",
                current_value=days_since_last_trade,
                limit_value=cfg.inactivity_days,
                breach_pct=days_since_last_trade / cfg.inactivity_days,
            ))
        elif days_since_last_trade >= cfg.inactivity_days - 1:
            warnings.append(f"Inactivity warning: {days_since_last_trade} days idle")

        passed = len(violations) == 0
        return RuleCheckResult(
            phase="FUNDED",
            passed=passed,
            violations=violations,
            warnings=warnings,
            metrics={
                "equity": current_equity,
                "peak_equity": peak_equity,
                "drawdown": round(drawdown, 2),
                "days_since_last_trade": days_since_last_trade,
                "total_pnl": round(total_pnl, 2),
            },
        )

    def check_payout_eligibility(
        self,
        trading_days: int,
        daily_profits: List[float],
        net_profit_since_last_payout: float,
        total_requestable_profit: float,
        payout_count: int,
        cfg: MFFFlexPayoutConfig = PAYOUT_CONFIG,
    ) -> RuleCheckResult:
        violations: List[RuleViolation] = []
        warnings: List[str] = []

        # 1. Minimum trading days
        if trading_days < cfg.days_to_first_payout:
            violations.append(RuleViolation(
                rule="PAYOUT_MIN_DAYS",
                detail=f"{trading_days} trading days < {cfg.days_to_first_payout} required",
                current_value=trading_days,
                limit_value=cfg.days_to_first_payout,
                breach_pct=trading_days / cfg.days_to_first_payout,
            ))

        # 2. Daily $100 minimum on active days
        bad_days = [d for d in daily_profits if 0 < d < cfg.min_profit_per_day]
        if bad_days:
            warnings.append(
                f"{len(bad_days)} days with profit below ${cfg.min_profit_per_day} minimum"
            )

        # 3. Net profit between payouts
        if net_profit_since_last_payout < cfg.net_profit_between_payouts:
            violations.append(RuleViolation(
                rule="NET_PROFIT_BETWEEN_PAYOUTS",
                detail=f"Net profit ${net_profit_since_last_payout:.2f} < ${cfg.net_profit_between_payouts} required",
                current_value=net_profit_since_last_payout,
                limit_value=cfg.net_profit_between_payouts,
                breach_pct=net_profit_since_last_payout / cfg.net_profit_between_payouts,
            ))

        # 4. Max payout count
        if payout_count >= cfg.max_simulated_payouts:
            violations.append(RuleViolation(
                rule="MAX_PAYOUT_COUNT",
                detail=f"Payout count {payout_count} ≥ max {cfg.max_simulated_payouts}",
                current_value=payout_count,
                limit_value=cfg.max_simulated_payouts,
                breach_pct=payout_count / cfg.max_simulated_payouts,
            ))

        requestable = total_requestable_profit * cfg.requestable_profit_pct
        max_request = min(requestable, cfg.max_payout_amount)

        passed = len(violations) == 0
        return RuleCheckResult(
            phase="PAYOUT",
            passed=passed,
            violations=violations,
            warnings=warnings,
            metrics={
                "trading_days": trading_days,
                "net_profit_since_last_payout": round(net_profit_since_last_payout, 2),
                "requestable_amount": round(requestable, 2),
                "max_request": round(max_request, 2),
                "after_split": round(max_request * cfg.profit_split, 2),
                "payout_count": payout_count,
            },
        )
