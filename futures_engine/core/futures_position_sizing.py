"""
CB6 Futures Core — Position Sizing
Risk-based contract sizing for futures.
Supports micro/standard scaling and MFF 2-contract maximum.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from futures_engine.core.futures_symbol_registry import get_symbol


@dataclass
class SizeResult:
    symbol: str
    contracts: int
    risk_per_contract: float   # USD
    total_risk: float          # USD
    entry: float
    stop_loss: float
    risk_points: float
    point_value: float
    tier: str                  # "micro" | "standard" | "mixed"
    capped: bool               # True if limited by max_contracts rule


def calculate_contracts(
    symbol: str,
    account_equity: float,
    entry: float,
    stop_loss: float,
    risk_pct: float = 0.01,         # 1% default
    max_contracts: int = 2,         # MFF Flex hard cap
    micro_only: bool = True,        # Phase 1: micros only
    micro_ratio: int = 10,          # 10 micros = 1 standard
) -> SizeResult:
    """
    Calculate number of contracts to trade given:
    - Account equity and risk %
    - Entry and stop-loss prices
    - Max contracts cap (MFF rule)
    """
    sym = get_symbol(symbol)
    risk_points = abs(entry - stop_loss)
    if risk_points == 0:
        raise ValueError("Entry and stop-loss cannot be equal")

    pv = sym.point_value
    risk_usd = account_equity * risk_pct
    raw_contracts = risk_usd / (risk_points * pv)

    contracts = max(1, math.floor(raw_contracts))
    capped = False

    if contracts > max_contracts:
        contracts = max_contracts
        capped = True

    # Enforce micro-only in Phase 1
    if micro_only and sym.standard_symbol is not None:
        # symbol is already a micro — OK
        pass
    elif micro_only and sym.standard_symbol is None:
        # Standard contract requested in micro-only mode → find micro equivalent
        raise ValueError(
            f"micro_only=True but '{symbol}' is a standard contract. "
            f"Use the micro equivalent instead."
        )

    actual_risk = contracts * risk_points * pv
    tier = "micro" if sym.micro_ratio > 1 else "standard"

    return SizeResult(
        symbol=symbol,
        contracts=contracts,
        risk_per_contract=round(risk_points * pv, 2),
        total_risk=round(actual_risk, 2),
        entry=entry,
        stop_loss=stop_loss,
        risk_points=risk_points,
        point_value=pv,
        tier=tier,
        capped=capped,
    )


def max_risk_for_contracts(
    symbol: str,
    contracts: int,
    entry: float,
    stop_loss: float,
) -> float:
    """Return total USD risk for a given number of contracts."""
    sym = get_symbol(symbol)
    risk_points = abs(entry - stop_loss)
    return round(contracts * risk_points * sym.point_value, 2)
