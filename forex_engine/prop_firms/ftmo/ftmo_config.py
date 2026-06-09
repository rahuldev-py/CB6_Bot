# forex_engine/prop_firms/ftmo/ftmo_config.py
# FTMO account configuration — re-exports from forex_instruments for clean imports.

from forex_engine.forex_instruments import (
    FTMO_RULES,
    FTMO_RISK_GUARD,
    INSTRUMENTS,
    calc_lot_size,
    dollar_risk,
)

# ── FTMO Account Modes ──────────────────────────────────────────────────────────
FREE_TRIAL = FTMO_RULES['free_trial']
CHALLENGE  = FTMO_RULES['challenge']

ACCOUNT_SIZE       = 25000.0
LEVERAGE           = FTMO_RULES['leverage']
RISK_PER_TRADE_PCT = FTMO_RULES['risk_per_trade_pct']
MAX_TRADES_PER_DAY = FTMO_RULES['max_trades_per_day']

# ── Risk guard thresholds (internal — stricter than FTMO limits) ───────────────
# See FTMO_RISK_GUARD in forex_instruments.py for current values.
RISK_GUARD = FTMO_RISK_GUARD

# ── Symbols active on FTMO ──────────────────────────────────────────────────────
FTMO_ACTIVE_SYMBOLS = ['XAUUSD', 'XAGUSD', 'USOIL', 'EURUSD']
FTMO_DISABLED_SYMBOLS = []

# ── Magic number — single source: read from env, default 62002 ─────────────────
import os as _os
FTMO_MAGIC = int(_os.getenv("FTMO_MAGIC", 62002))
