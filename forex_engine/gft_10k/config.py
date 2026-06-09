import os
from dotenv import load_dotenv
load_dotenv()


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _int_env(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


ACCOUNT_NAMESPACE = "GFT_10K"
ACCOUNT_ID        = "GFT_10K"

GFT_10K_PROFILE = {
    "name"               : ACCOUNT_NAMESPACE,
    "account_size"       : _float_env("CB6_GFT_10K_ACCOUNT_SIZE",         "10000"),
    "daily_dd_limit"     : _float_env("CB6_GFT_10K_DAILY_DD_LIMIT",       "500"),
    "max_dd_limit"       : _float_env("CB6_GFT_10K_MAX_DD_LIMIT",         "1000"),
    "daily_dd_danger"    : 400.0,
    "max_dd_danger"      : 900.0,
    "max_risk_usd"       : _float_env("CB6_GFT_10K_MAX_RISK_USD",         "50"),
    "risk_per_trade_pct" : _float_env("CB6_GFT_10K_RISK_PER_TRADE_PCT",   "0.5"),
    "max_lot"            : _float_env("CB6_GFT_10K_MAX_LOT",              "0.10"),
    "magic"              : _int_env  ("CB6_GFT_10K_MAGIC",                "100100"),
    "state_dir"          : os.getenv ("CB6_GFT_10K_STATE_DIR",            "data/gft_10k"),
    "enabled_symbols"    : ["XAUUSD", "XAGUSD", "USOIL"],
    "disabled_symbols"   : [],
    # XAUUSD lot sizing: risk$50 / (100oz × SL_distance)
    # SL $5  → 0.10 lots | SL $10 → 0.05 lots | SL $15 → 0.03 lots
    # Capped at max_lot=0.10 (A+ with $75 risk at SL $5 = 0.15 → capped to 0.10)
    "max_lot_per_symbol" : {"XAUUSD": 0.10, "XAGUSD": 0.10, "USOIL": 0.10},
    "min_rr"             : 1.5,
    "max_open_positions" : 1,
    "alert_prefix"       : "[GFT-10K]",
}


def is_enabled() -> bool:
    return _bool_env("CB6_GFT_10K_ENABLED", "false")


def live_execution_enabled() -> bool:
    return is_enabled() and _bool_env("CB6_GFT_10K_LIVE_EXECUTION", "false")
