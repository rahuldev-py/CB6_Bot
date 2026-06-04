# core/account_router.py — Single source of truth for account/symbol/lot routing
#
# Every engine reads account params from here. Never hardcode account IDs,
# magic numbers, lot sizes, or symbol suffixes anywhere else.
#
# Usage:
#   from core.account_router import get_account, get_symbol_config
#   acct = get_account(Engine.FTMO)
#   sym  = get_symbol_config(Engine.GFT, "XAGUSD")

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from core.schemas import Engine, Market


# ── Account profile ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AccountProfile:
    engine:           Engine
    account_id:       str
    broker:           str           # "fyers", "mt5_ftmo", "mt5_gft", "binance"
    market:           Market
    capital:          float
    currency:         str           # "INR", "USD"
    is_live:          bool
    magic_number:     Optional[int] = None   # MT5 magic number
    terminal_path:    Optional[str] = None   # MT5 terminal exe path
    login:            Optional[int] = None   # MT5 login
    server:           Optional[str] = None   # MT5 server
    state_file:       Optional[str] = None   # JSON state file path
    max_daily_loss_abs: float = 0.0
    max_daily_loss_pct: float = 0.0
    max_total_loss_abs: float = 0.0
    allowed_symbols:  tuple = field(default_factory=tuple)
    blocked_symbols:  tuple = field(default_factory=tuple)


# ── Symbol config ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SymbolConfig:
    symbol:       str
    engine:       Engine
    pip_size:     float
    lot_min:      float
    lot_max:      float
    lot_step:     float
    contract_size: float  # units per lot (e.g. 1000 for XAGUSD)
    spread_max:   float   # max acceptable spread in pips
    digits:       int     # decimal places


# ── Registry ──────────────────────────────────────────────────────────────────

def _build_registry() -> dict[Engine, AccountProfile]:
    """Build account registry from env vars. Called once at module load."""
    return {
        Engine.NSE_PAPER: AccountProfile(
            engine=Engine.NSE_PAPER,
            account_id="NSE_PAPER",
            broker="fyers",
            market=Market.NSE,
            capital=float(os.getenv("CAPITAL", 200000)),
            currency="INR",
            is_live=False,
            state_file="data/paper_state.json",
            max_daily_loss_pct=2.0,
            max_daily_loss_abs=float(os.getenv("CAPITAL", 200000)) * 0.02,
        ),
        Engine.NSE_LIVE: AccountProfile(
            engine=Engine.NSE_LIVE,
            account_id=os.getenv("CLIENT_ID", ""),
            broker="fyers",
            market=Market.NSE,
            capital=float(os.getenv("CAPITAL", 200000)),
            currency="INR",
            is_live=True,
            state_file="data/live_state.json",
            max_daily_loss_pct=2.0,
            max_daily_loss_abs=float(os.getenv("CAPITAL", 200000)) * 0.02,
        ),
        Engine.FTMO: AccountProfile(
            engine=Engine.FTMO,
            account_id=os.getenv("FTMO_ACCOUNT", "FTMO_10K"),
            broker="mt5_ftmo",
            market=Market.FOREX,
            capital=float(os.getenv("FTMO_CAPITAL", 9891.91)),
            currency="USD",
            is_live=True,
            magic_number=int(os.getenv("FTMO_MAGIC", 62002)),
            terminal_path=os.getenv("MT5_TERMINAL_FTMO", ""),
            state_file="data/ftmo_10k/state.json",
            max_daily_loss_abs=300.0,
            max_daily_loss_pct=3.0,
            max_total_loss_abs=1000.0,
            allowed_symbols=("XAUUSD", "XAGUSD", "USOIL", "EURUSD"),
            blocked_symbols=(),
        ),
        Engine.GFT: AccountProfile(
            engine=Engine.GFT,
            account_id=os.getenv("GFT_ACCOUNT", "GFT_5K"),
            broker="mt5_gft",
            market=Market.FOREX,
            capital=float(os.getenv("GFT_CAPITAL", 4985.72)),
            currency="USD",
            is_live=True,
            magic_number=int(os.getenv("GFT_2STEP_MAGIC", 62001)),
            terminal_path=os.getenv("MT5_TERMINAL_GFT", ""),
            state_file="data/gft_5k/state.json",
            max_daily_loss_abs=200.0,
            max_daily_loss_pct=4.0,
            max_total_loss_abs=500.0,
            allowed_symbols=("XAUUSD", "XAGUSD", "USOIL"),
            blocked_symbols=(),
        ),
        Engine.CRYPTO_PAPER: AccountProfile(
            engine=Engine.CRYPTO_PAPER,
            account_id="CRYPTO_PAPER",
            broker="binance",
            market=Market.CRYPTO,
            capital=float(os.getenv("CRYPTO_CAPITAL", 8.4)),
            currency="USDT",
            is_live=False,
            state_file="data/crypto_state.json",
        ),
    }


_REGISTRY: dict[Engine, AccountProfile] = _build_registry()


# ── Symbol configs ────────────────────────────────────────────────────────────

_SYMBOL_CONFIGS: dict[tuple[Engine, str], SymbolConfig] = {
    (Engine.FTMO, "XAUUSD"): SymbolConfig(
        symbol="XAUUSD", engine=Engine.FTMO,
        pip_size=0.01, lot_min=0.01, lot_max=50.0, lot_step=0.01,
        contract_size=100.0, spread_max=150.0, digits=2,
    ),
    (Engine.FTMO, "XAGUSD"): SymbolConfig(
        symbol="XAGUSD", engine=Engine.FTMO,
        pip_size=0.001, lot_min=0.01, lot_max=10.0, lot_step=0.01,
        contract_size=5000.0, spread_max=5.0, digits=3,
    ),
    (Engine.FTMO, "USOIL"): SymbolConfig(
        symbol="USOIL", engine=Engine.FTMO,
        pip_size=0.01, lot_min=0.01, lot_max=10.0, lot_step=0.01,
        contract_size=100.0, spread_max=8.0, digits=2,
    ),
    (Engine.FTMO, "EURUSD"): SymbolConfig(
        symbol="EURUSD", engine=Engine.FTMO,
        pip_size=0.0001, lot_min=0.01, lot_max=10.0, lot_step=0.01,
        contract_size=100000.0, spread_max=3.0, digits=5,
    ),
    (Engine.GFT, "XAUUSD"): SymbolConfig(
        symbol="XAUUSD", engine=Engine.GFT,
        pip_size=0.01, lot_min=0.01, lot_max=50.0, lot_step=0.01,
        contract_size=100.0, spread_max=150.0, digits=2,
    ),
    (Engine.GFT, "XAGUSD"): SymbolConfig(
        symbol="XAGUSD", engine=Engine.GFT,
        pip_size=0.001, lot_min=0.01, lot_max=5.0, lot_step=0.01,
        contract_size=5000.0, spread_max=5.0, digits=3,
    ),
    (Engine.GFT, "USOIL"): SymbolConfig(
        symbol="USOIL", engine=Engine.GFT,
        pip_size=0.01, lot_min=0.01, lot_max=5.0, lot_step=0.01,
        contract_size=100.0, spread_max=8.0, digits=2,
    ),
}


# ── Public API ────────────────────────────────────────────────────────────────

def get_account(engine: Engine) -> AccountProfile:
    """Return the AccountProfile for an engine. Raises KeyError if not registered."""
    profile = _REGISTRY.get(engine)
    if profile is None:
        raise KeyError(f"No account registered for engine '{engine}'")
    return profile


def get_symbol_config(engine: Engine, symbol: str) -> Optional[SymbolConfig]:
    """Return symbol-specific config for an engine. Returns None if unknown."""
    return _SYMBOL_CONFIGS.get((engine, symbol))


def is_symbol_allowed(engine: Engine, symbol: str) -> bool:
    """Quick check: is this symbol allowed for this engine?"""
    profile = _REGISTRY.get(engine)
    if profile is None:
        return False
    if symbol in profile.blocked_symbols:
        return False
    if profile.allowed_symbols and symbol not in profile.allowed_symbols:
        return False
    return True


def get_all_engines() -> list[Engine]:
    """Return all registered engine keys."""
    return list(_REGISTRY.keys())
