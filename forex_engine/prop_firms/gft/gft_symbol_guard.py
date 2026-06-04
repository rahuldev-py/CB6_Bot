# forex_engine/prop_firms/gft/gft_symbol_guard.py
# GFT symbol guard — reads enabled/disabled lists from gft_config.GFT_2STEP_PROFILE.

from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE
from utils.logger import logger

ENABLED  = set(GFT_2STEP_PROFILE['enabled_symbols'])
DISABLED = set(GFT_2STEP_PROFILE['disabled_symbols'])


def is_allowed(symbol: str) -> tuple[bool, str]:
    if symbol in DISABLED:
        return False, (
            f"{symbol} is PERMANENTLY disabled on GFT 2-Step. "
            f"Only {sorted(ENABLED)} are allowed."
        )
    if symbol not in ENABLED:
        return False, f"{symbol} not in GFT 2-Step enabled list: {sorted(ENABLED)}"
    return True, 'OK'


def validate_symbol(symbol: str):
    """Raise if symbol is not allowed. Use at worker startup."""
    ok, reason = is_allowed(symbol)
    if not ok:
        raise ValueError(f"GFT 2-Step symbol blocked: {reason}")


def filter_symbols(symbols: list) -> list:
    """Return only the allowed subset of a symbol list."""
    allowed = [s for s in symbols if s in ENABLED]
    blocked = [s for s in symbols if s in DISABLED]
    if blocked:
        logger.warning(f"GFT 2-Step: blocked symbols filtered out: {blocked}")
    return allowed
