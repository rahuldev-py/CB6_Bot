# forex_engine/prop_firms/gft/gft_daily_loss_guard.py
# GFT daily loss — relative to 5PM EST equity snapshot. Internal tiered guards.

from datetime import datetime
from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE


_P = GFT_2STEP_PROFILE


def daily_loss_from_snapshot(state: dict) -> float:
    """
    GFT daily loss = capital at 5PM EST yesterday − current capital.
    Positive = loss, negative = gain.
    """
    snapshot = state.get('gft_daily_snapshot', _P['account_size'])
    capital  = state.get('capital', _P['account_size'])
    return round(snapshot - capital, 2)


def official_daily_loss_ok(state: dict) -> tuple[bool, str]:
    """
    Official GFT 4% daily loss rule ($200 on $5K).
    Relative to 5PM EST equity snapshot.
    """
    loss  = daily_loss_from_snapshot(state)
    limit = _P['official_daily_loss_usd']
    if loss >= limit:
        return False, (
            f"GFT official daily loss limit hit "
            f"(${loss:.2f} ≥ ${limit:.2f} from 5PM snapshot)"
        )
    return True, 'OK'


def get_internal_risk_mode(state: dict) -> tuple[str, str]:
    """
    Internal daily loss guard tiers.
    Returns (mode, reason).
    mode: 'normal' | 'reduced' | 'paused' | 'warning'
    """
    loss = daily_loss_from_snapshot(state)

    if loss >= _P['internal_daily_hard_stop']:
        return ('paused',
                f"Internal daily hard stop — ${loss:.2f} ≥ ${_P['internal_daily_hard_stop']:.2f}")
    if loss >= _P['internal_daily_risk_cut']:
        return ('reduced',
                f"Internal daily risk cut — ${loss:.2f} ≥ ${_P['internal_daily_risk_cut']:.2f}")
    if loss >= _P['internal_daily_warning']:
        return ('warning',
                f"Internal daily warning — ${loss:.2f} ≥ ${_P['internal_daily_warning']:.2f}")
    return ('normal', 'OK')


def daily_loss_remaining(state: dict) -> float:
    """How much of the official daily limit remains (positive = room left)."""
    loss = daily_loss_from_snapshot(state)
    return round(_P['official_daily_loss_usd'] - loss, 2)
