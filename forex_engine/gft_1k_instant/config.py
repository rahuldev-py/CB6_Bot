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


ACCOUNT_NAMESPACE = "GFT_1K_INSTANT"
ACCOUNT_ID = "GFT_1K_INSTANT"

GFT_1K_INSTANT_PROFILE = {
    "name": ACCOUNT_NAMESPACE,
    "account_size": _float_env("CB6_GFT_1K_INSTANT_ACCOUNT_SIZE", "1000"),
    "daily_dd_limit": _float_env("CB6_GFT_1K_INSTANT_DAILY_DD_LIMIT", "30"),
    "max_dd_limit": _float_env("CB6_GFT_1K_INSTANT_MAX_DD_LIMIT", "60"),
    "daily_dd_danger": 25.0,
    "max_dd_danger": 55.0,
    "max_risk_usd": _float_env("CB6_GFT_1K_INSTANT_MAX_RISK_USD", "2.50"),
    "risk_per_trade_pct": _float_env("CB6_GFT_1K_INSTANT_RISK_PER_TRADE_PCT", "0.25"),
    "max_lot": _float_env("CB6_GFT_1K_INSTANT_MAX_LOT", "0.01"),
    "magic": _int_env("CB6_GFT_1K_INSTANT_MAGIC", "100061"),
    "state_dir": os.getenv("CB6_GFT_1K_INSTANT_STATE_DIR", "data/gft_1k_instant"),
    "enabled_symbols": ["XAUUSD", "XAGUSD"],
    "disabled_symbols": ["USOIL"],
    # XAUUSD lot sizing: risk$2.50 / (100oz × SL_distance)
    # SL $2.50 → 0.01 lots (minimum — only viable with very tight SL)
    # SL > $2.50 → lots < 0.01 → engine auto-skips (below min_lot)
    # Effectively: $1K only trades Gold when setup has ≤$2.50 SL distance
    # USOIL disabled: GFT min lot is 0.10 → risk=$5.50 > $2.50 max → can never trade
    "max_lot_per_symbol": {"XAUUSD": 0.01, "XAGUSD": 0.01},
    "min_rr": 1.5,
    "max_open_positions": 1,
    "alert_prefix": "[GFT-1K-INSTANT]",
}


def is_enabled() -> bool:
    return _bool_env("CB6_GFT_1K_INSTANT_ENABLED", "false")


def live_execution_enabled() -> bool:
    return (
        is_enabled()
        and _bool_env("CB6_GFT_1K_INSTANT_LIVE_EXECUTION", "false")
    )


def strict_startup_enabled() -> bool:
    return _bool_env("CB6_GFT_1K_INSTANT_STRICT_STARTUP", "false")

