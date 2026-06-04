# forex_engine/trade/lot_calculator.py
# Lot size helpers — calc_lot_size and dollar_risk live in forex_instruments.py
# (canonical source). Re-exported here for backward compatibility with all callers
# that import from this module.

import random
from forex_engine.forex_instruments import (   # noqa: F401 — re-exports
    calc_lot_size,
    dollar_risk,
    INSTRUMENTS,
    FTMO_RULES,
)


def dollar_value_per_pip(symbol: str, lots: float) -> float:
    """Dollar value of 1 pip move for given lots."""
    cfg = INSTRUMENTS.get(symbol, {})
    return round(lots * cfg.get('contract_size', 100000) * cfg.get('pip_size', 0.0001), 4)


def gft_lot_modifier(lots: float) -> float:
    """Add ±0.01-0.02 fractional noise — avoids round-lot pattern detection on GFT."""
    offset = round(random.choice([-0.02, -0.01, 0.01, 0.02]), 2)
    return max(0.01, round(lots + offset, 2))


def apply_risk_mode(base_risk_pct: float, risk_mode: str,
                    reduction_factor: float = 0.5) -> float:
    """Scale risk_pct by the current risk mode."""
    if risk_mode in ('reduced', 'aplus_only'):
        return round(base_risk_pct * reduction_factor, 4)
    return base_risk_pct


def apply_lot_boost(base_risk_pct: float, boost_factor: float,
                    risk_mode: str = 'normal') -> float:
    """Apply A+ similarity boost — only in normal mode."""
    if risk_mode != 'normal' or boost_factor <= 1.0:
        return base_risk_pct
    return round(base_risk_pct * boost_factor, 4)
