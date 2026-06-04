# forex_engine/prop_firms/gft/gft_max_loss_guard.py
# GFT total max loss — static floor (10% = $500 on $5K). Internal tiered guards.

from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE

_P = GFT_2STEP_PROFILE


def total_loss_from_start(state: dict) -> float:
    """Total loss from account starting size. Positive = loss."""
    start   = _P['account_size']
    capital = state.get('capital', start)
    return round(start - capital, 2)


def official_max_loss_ok(state: dict) -> tuple[bool, str]:
    """
    Official GFT 10% static max loss ($500 on $5K).
    Floor never moves — calculated from original account size.
    """
    loss  = total_loss_from_start(state)
    limit = _P['official_max_loss_usd']
    floor = _P['account_size'] - limit
    capital = state.get('capital', _P['account_size'])

    if capital <= floor or loss >= limit:
        return False, (
            f"GFT static max loss floor breached "
            f"(equity ${capital:.2f} ≤ floor ${floor:.2f} = "
            f"${_P['account_size']:.0f} − ${limit:.0f})"
        )
    return True, 'OK'


def get_internal_risk_mode(state: dict) -> tuple[str, str]:
    """
    Internal total loss tiered guards.
    Returns (mode, reason).
    """
    loss = total_loss_from_start(state)

    if loss >= _P['internal_total_hard_stop']:
        return ('paused',
                f"Internal total hard stop — ${loss:.2f} ≥ ${_P['internal_total_hard_stop']:.2f}")
    if loss >= _P['internal_total_risk_cut']:
        return ('reduced',
                f"Internal total risk cut — ${loss:.2f} ≥ ${_P['internal_total_risk_cut']:.2f}")
    if loss >= _P['internal_total_warning']:
        return ('warning',
                f"Internal total warning — ${loss:.2f} ≥ ${_P['internal_total_warning']:.2f}")
    return ('normal', 'OK')


def max_loss_remaining(state: dict) -> float:
    """Dollar room before official max loss floor."""
    capital = state.get('capital', _P['account_size'])
    floor   = _P['account_size'] - _P['official_max_loss_usd']
    return round(capital - floor, 2)
