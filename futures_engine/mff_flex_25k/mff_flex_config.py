"""
CB6 Futures Core — MFF Flex $25K Configuration
MyFundedFutures — Flex Plan — $25,000 account.
All values sourced directly from MFF Flex plan terms.
Never hardcode rule values inside strategy files — always read from here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class MFFFlexEvalConfig:
    """Evaluation phase parameters — Flex $25K."""
    account_size: float        = 25_000.0
    profit_target: float       = 1_500.0    # 6% of account
    max_drawdown: float        = 1_000.0    # 4% trailing EOD
    drawdown_mode: str         = "EOD"      # End-of-day trailing
    daily_drawdown: float      = 0.0        # No daily drawdown limit
    max_contracts: int         = 2          # Hard cap per trade
    micro_scaling_ratio: int   = 10         # 10 micros = 1 standard
    consistency_rule_pct: float= 0.50       # No single day > 50% of total profit
    scaling_rule: bool         = False      # No scaling during eval
    min_trading_days: int      = 2          # Calendar days with at least 1 trade
    news_trading_allowed: bool = True       # MFF Flex allows news trading


@dataclass(frozen=True)
class MFFFlexFundedConfig:
    """Funded account parameters."""
    max_drawdown: float        = 1_000.0    # EOD trailing
    drawdown_mode: str         = "EOD"
    daily_drawdown: float      = 0.0        # No daily DD limit
    max_contracts: int         = 2
    micro_scaling_ratio: int   = 10
    consistency_rule: bool     = False      # No consistency requirement
    scaling_rule: bool         = True       # Scaling up permitted
    inactivity_days: int       = 7          # Calendar days — max gap between trades
    news_trading_allowed: bool = True
    buffer: float              = 0.0        # No buffer on funded


@dataclass(frozen=True)
class MFFFlexPayoutConfig:
    """Payout rules for funded account."""
    days_to_first_payout: int  = 5          # Minimum trading days before requesting
    min_profit_per_day: float  = 100.0      # Every active day must show ≥$100 profit
    min_payout_amount: float   = 250.0      # Minimum payout request
    max_payout_amount: float   = 1_000.0    # Maximum single payout request
    net_profit_between_payouts: float = 250.0  # Must accumulate $250 net between payouts
    requestable_profit_pct: float = 0.50    # Can request up to 50% of requestable profit
    profit_split: float        = 0.80       # Trader keeps 80%
    mll_after_first_payout: float = 100.0   # Maximum loss lock after first payout ($100)
    max_simulated_payouts: int = 5          # Evaluation payouts capped at 5


@dataclass(frozen=True)
class MFFFlexInternalGuards:
    """
    CB6-internal safety margins — fire BEFORE official MFF limits.
    These are our own guards on top of MFF limits.
    """
    daily_warning_usd: float   = 100.0      # Warn trader
    daily_reduce_usd: float    = 150.0      # Halve position size
    daily_hard_stop_usd: float = 200.0      # No new trades today

    total_warning_usd: float   = 400.0      # Warn on total drawdown
    total_reduce_usd: float    = 600.0      # Reduce on total drawdown
    total_halt_usd: float      = 800.0      # Halt — $200 buffer before MFF $1000 limit

    max_consecutive_losses: int = 2         # Halt after 2 back-to-back losses
    default_risk_pct: float    = 0.005      # 0.5% risk per trade (micro sizing)
    max_trade_contracts: int   = 1          # Start with 1 micro max


@dataclass(frozen=True)
class MFFFlexSymbols:
    """Trading tiers for MFF Flex."""
    phase1: List[str] = field(default_factory=lambda: ["MES", "MNQ", "MGC", "MCL"])
    phase2: List[str] = field(default_factory=lambda: ["ES", "NQ", "GC", "CL"])
    all_permitted: List[str] = field(default_factory=lambda: [
        "ES", "MES", "NQ", "MNQ", "RTY", "M2K", "YM", "MYM",
        "GC", "MGC", "SI", "SIL", "CL", "MCL", "ZN", "ZB",
    ])


# ── Singleton instances ────────────────────────────────────────────────────────
EVAL_CONFIG   = MFFFlexEvalConfig()
FUNDED_CONFIG = MFFFlexFundedConfig()
PAYOUT_CONFIG = MFFFlexPayoutConfig()
GUARDS_CONFIG = MFFFlexInternalGuards()
SYMBOLS       = MFFFlexSymbols()
