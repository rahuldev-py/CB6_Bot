# forex_engine/prop_firms/ftmo/ftmo_max_loss.py
# FTMO max total drawdown — EOD trailing floor logic.

from forex_engine.forex_instruments import FTMO_RULES


def eod_dd_floor(eod_peak: float, starting: float = 10000.0,
                 mode: str = 'free_trial') -> float:
    """The current FTMO EOD trailing DD floor."""
    rules     = FTMO_RULES.get(mode, FTMO_RULES['free_trial'])
    dd_limit  = starting * rules['max_total_dd_pct'] / 100
    return round(eod_peak - dd_limit, 2)


def official_max_loss_ok(capital: float, eod_peak: float,
                          starting: float = 10000.0,
                          mode: str = 'free_trial') -> tuple[bool, str]:
    """
    Official FTMO 10% EOD trailing DD floor check.
    """
    floor = eod_dd_floor(eod_peak, starting, mode)
    if capital <= floor:
        return False, (
            f"FTMO EOD trailing DD breached "
            f"(equity ${capital:.2f} ≤ floor ${floor:.2f})"
        )
    return True, 'OK'


def max_loss_remaining(capital: float, eod_peak: float,
                        starting: float = 10000.0,
                        mode: str = 'free_trial') -> float:
    """Dollar buffer between current equity and EOD DD floor."""
    floor = eod_dd_floor(eod_peak, starting, mode)
    return round(capital - floor, 2)
