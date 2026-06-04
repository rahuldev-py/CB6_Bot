"""
CB6 Futures Core — Symbol Registry
Defines all MFF-permitted CME Group futures contracts.
Tick sizes, tick values, point values, sessions, and micro mappings.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FuturesSymbol:
    symbol: str                  # e.g. "MES"
    full_name: str
    exchange: str                # CME, CBOT, COMEX, NYMEX
    currency: str                # USD
    tick_size: float             # minimum price increment
    tick_value: float            # USD value per tick
    point_value: float           # USD value per full point
    margin_est: float            # approximate initial margin USD (indicative)
    standard_symbol: Optional[str]  # e.g. "ES" for "MES"
    micro_ratio: int             # 10 for micro:standard, 1 for standard
    asset_class: str             # equity_index / metal / energy / treasury
    session_utc: tuple           # (open_hour, close_hour) primary session UTC
    overnight: bool              # True if 23h session
    mff_permitted: bool          # True = allowed on MFF Flex
    prohibited_reason: str = ""  # filled when mff_permitted=False


# Full MFF-permitted symbol list — CME Group standardised futures only
SYMBOL_REGISTRY: dict[str, FuturesSymbol] = {

    # ── Equity Index — Micros (default trading tier) ──────────────────────
    "MES": FuturesSymbol(
        symbol="MES", full_name="Micro E-mini S&P 500",
        exchange="CME", currency="USD",
        tick_size=0.25, tick_value=1.25, point_value=5.0,
        margin_est=450, standard_symbol="ES", micro_ratio=10,
        asset_class="equity_index", session_utc=(13, 20), overnight=True,
        mff_permitted=True,
    ),
    "MNQ": FuturesSymbol(
        symbol="MNQ", full_name="Micro E-mini Nasdaq-100",
        exchange="CME", currency="USD",
        tick_size=0.25, tick_value=0.50, point_value=2.0,
        margin_est=650, standard_symbol="NQ", micro_ratio=10,
        asset_class="equity_index", session_utc=(13, 20), overnight=True,
        mff_permitted=True,
    ),
    "M2K": FuturesSymbol(
        symbol="M2K", full_name="Micro E-mini Russell 2000",
        exchange="CME", currency="USD",
        tick_size=0.10, tick_value=0.50, point_value=5.0,
        margin_est=300, standard_symbol="RTY", micro_ratio=10,
        asset_class="equity_index", session_utc=(13, 20), overnight=True,
        mff_permitted=True,
    ),
    "MYM": FuturesSymbol(
        symbol="MYM", full_name="Micro E-mini Dow Jones",
        exchange="CBOT", currency="USD",
        tick_size=1.0, tick_value=0.50, point_value=0.50,
        margin_est=350, standard_symbol="YM", micro_ratio=10,
        asset_class="equity_index", session_utc=(13, 20), overnight=True,
        mff_permitted=True,
    ),

    # ── Equity Index — Standards ──────────────────────────────────────────
    "ES": FuturesSymbol(
        symbol="ES", full_name="E-mini S&P 500",
        exchange="CME", currency="USD",
        tick_size=0.25, tick_value=12.50, point_value=50.0,
        margin_est=12000, standard_symbol=None, micro_ratio=1,
        asset_class="equity_index", session_utc=(13, 20), overnight=True,
        mff_permitted=True,
    ),
    "NQ": FuturesSymbol(
        symbol="NQ", full_name="E-mini Nasdaq-100",
        exchange="CME", currency="USD",
        tick_size=0.25, tick_value=5.00, point_value=20.0,
        margin_est=18000, standard_symbol=None, micro_ratio=1,
        asset_class="equity_index", session_utc=(13, 20), overnight=True,
        mff_permitted=True,
    ),
    "RTY": FuturesSymbol(
        symbol="RTY", full_name="E-mini Russell 2000",
        exchange="CME", currency="USD",
        tick_size=0.10, tick_value=5.00, point_value=50.0,
        margin_est=7500, standard_symbol=None, micro_ratio=1,
        asset_class="equity_index", session_utc=(13, 20), overnight=True,
        mff_permitted=True,
    ),
    "YM": FuturesSymbol(
        symbol="YM", full_name="E-mini Dow Jones",
        exchange="CBOT", currency="USD",
        tick_size=1.0, tick_value=5.00, point_value=5.0,
        margin_est=8000, standard_symbol=None, micro_ratio=1,
        asset_class="equity_index", session_utc=(13, 20), overnight=True,
        mff_permitted=True,
    ),

    # ── Metals — Micros ───────────────────────────────────────────────────
    "MGC": FuturesSymbol(
        symbol="MGC", full_name="Micro Gold",
        exchange="COMEX", currency="USD",
        tick_size=0.10, tick_value=1.00, point_value=10.0,
        margin_est=600, standard_symbol="GC", micro_ratio=10,
        asset_class="metal", session_utc=(0, 23), overnight=True,
        mff_permitted=True,
    ),
    "SIL": FuturesSymbol(
        symbol="SIL", full_name="Micro Silver",
        exchange="COMEX", currency="USD",
        tick_size=0.005, tick_value=0.25, point_value=50.0,
        margin_est=1500, standard_symbol="SI", micro_ratio=5,
        asset_class="metal", session_utc=(0, 23), overnight=True,
        mff_permitted=True,
    ),

    # ── Metals — Standards ────────────────────────────────────────────────
    "GC": FuturesSymbol(
        symbol="GC", full_name="Gold Futures (100 oz)",
        exchange="COMEX", currency="USD",
        tick_size=0.10, tick_value=10.00, point_value=100.0,
        margin_est=9000, standard_symbol=None, micro_ratio=1,
        asset_class="metal", session_utc=(0, 23), overnight=True,
        mff_permitted=True,
    ),
    "SI": FuturesSymbol(
        symbol="SI", full_name="Silver Futures (5000 oz)",
        exchange="COMEX", currency="USD",
        tick_size=0.005, tick_value=25.00, point_value=5000.0,
        margin_est=8000, standard_symbol=None, micro_ratio=1,
        asset_class="metal", session_utc=(0, 23), overnight=True,
        mff_permitted=True,
    ),

    # ── Energy — Micros ───────────────────────────────────────────────────
    "MCL": FuturesSymbol(
        symbol="MCL", full_name="Micro WTI Crude Oil",
        exchange="NYMEX", currency="USD",
        tick_size=0.01, tick_value=0.10, point_value=10.0,
        margin_est=500, standard_symbol="CL", micro_ratio=10,
        asset_class="energy", session_utc=(0, 23), overnight=True,
        mff_permitted=True,
    ),

    # ── Energy — Standards ────────────────────────────────────────────────
    "CL": FuturesSymbol(
        symbol="CL", full_name="WTI Crude Oil",
        exchange="NYMEX", currency="USD",
        tick_size=0.01, tick_value=10.00, point_value=1000.0,
        margin_est=6000, standard_symbol=None, micro_ratio=1,
        asset_class="energy", session_utc=(0, 23), overnight=True,
        mff_permitted=True,
    ),

    # ── Treasuries ────────────────────────────────────────────────────────
    "ZN": FuturesSymbol(
        symbol="ZN", full_name="10-Year T-Note Futures",
        exchange="CBOT", currency="USD",
        tick_size=0.015625, tick_value=15.625, point_value=1000.0,
        margin_est=1500, standard_symbol=None, micro_ratio=1,
        asset_class="treasury", session_utc=(0, 23), overnight=True,
        mff_permitted=True,
    ),
    "ZB": FuturesSymbol(
        symbol="ZB", full_name="30-Year T-Bond Futures",
        exchange="CBOT", currency="USD",
        tick_size=0.03125, tick_value=31.25, point_value=1000.0,
        margin_est=3500, standard_symbol=None, micro_ratio=1,
        asset_class="treasury", session_utc=(0, 23), overnight=True,
        mff_permitted=True,
    ),

    # ── Prohibited examples (stored for validation) ───────────────────────
    "EURUSD": FuturesSymbol(
        symbol="EURUSD", full_name="EUR/USD Forex Spot",
        exchange="OTC", currency="USD",
        tick_size=0.0, tick_value=0.0, point_value=0.0,
        margin_est=0, standard_symbol=None, micro_ratio=1,
        asset_class="forex_spot", session_utc=(0, 0), overnight=False,
        mff_permitted=False, prohibited_reason="Forex spot — not a CME standardised futures contract",
    ),
}

# Symbols approved for Phase 1 trading (micros only, risk-gated)
PHASE1_SYMBOLS = ["MES", "MNQ", "MGC", "MCL"]

# Symbols unlocked after risk approval
PHASE2_SYMBOLS = ["ES", "NQ", "GC", "CL", "RTY", "M2K", "MYM", "YM", "SI", "SIL", "ZN", "ZB"]


def get_symbol(sym: str) -> FuturesSymbol:
    s = SYMBOL_REGISTRY.get(sym.upper())
    if s is None:
        raise KeyError(f"Symbol '{sym}' not in CB6 Futures registry")
    return s


def assert_mff_permitted(sym: str) -> None:
    s = get_symbol(sym)
    if not s.mff_permitted:
        raise ValueError(f"Symbol '{sym}' is prohibited on MFF: {s.prohibited_reason}")


def point_value(sym: str) -> float:
    return get_symbol(sym).point_value


def tick_value(sym: str) -> float:
    return get_symbol(sym).tick_value


def tick_size(sym: str) -> float:
    return get_symbol(sym).tick_size
