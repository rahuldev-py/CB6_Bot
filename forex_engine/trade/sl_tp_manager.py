# forex_engine/trade/sl_tp_manager.py
# SL/TP calculation and post-fill adjustment.

from forex_engine.forex_instruments import INSTRUMENTS
from forex_engine.trade.lot_calculator import dollar_risk


def build_trade_plan(symbol: str, direction: str, entry: float, sl: float,
                     fvg_size: float, dol_level: float) -> dict:
    """
    Build T1/T2/T3 plan from entry and SL distance.
    T1 = 2R, T2 = 3R, T3 = DOL (if beyond T2) else 4R.
    """
    risk = round(abs(entry - sl), 5)
    if risk <= 0:
        return {}

    if direction == 'BULLISH':
        t1  = round(entry + risk * 2.0, 5)
        t2  = round(entry + risk * 3.0, 5)
        t3  = round(max(dol_level if dol_level > t2 else entry + risk * 4.0, t2), 5)
        rr  = round((t2 - entry) / risk, 1)
    else:
        t1  = round(entry - risk * 2.0, 5)
        t2  = round(entry - risk * 3.0, 5)
        t3  = round(min(dol_level if dol_level < t2 else entry - risk * 4.0, t2), 5)
        rr  = round((entry - t2) / risk, 1)

    return {
        'entry'    : entry,
        'stop_loss': sl,
        'target1'  : t1,
        'target2'  : t2,
        'target3'  : t3,
        'risk'     : risk,
        'rr_ratio' : rr,
    }


def adjust_for_fill(symbol: str, direction: str, fill_price: float,
                    alert_entry: float, sl: float, lots: float,
                    dol_level: float = 0.0) -> dict:
    """
    Recalculate SL/TP levels using actual fill price instead of alert price.
    Maintains the same R:R ratio — SL/TP move proportionally with fill.
    """
    sl_dist = abs(alert_entry - sl)
    is_long = direction == 'BULLISH'

    new_sl = round(fill_price - sl_dist if is_long else fill_price + sl_dist, 5)
    new_t1 = round(fill_price + sl_dist * 2 if is_long else fill_price - sl_dist * 2, 5)
    new_t2 = round(fill_price + sl_dist * 3 if is_long else fill_price - sl_dist * 3, 5)
    new_t3 = round(fill_price + sl_dist * 4 if is_long else fill_price - sl_dist * 4, 5)

    if dol_level > 0:
        if is_long and dol_level > new_t2:
            new_t3 = dol_level
        elif not is_long and dol_level < new_t2:
            new_t3 = dol_level

    new_risk = dollar_risk(symbol, lots, fill_price, new_sl)

    return {
        'entry'    : fill_price,
        'stop_loss': new_sl,
        'current_sl': new_sl,
        'target1'  : new_t1,
        'target2'  : new_t2,
        'target3'  : new_t3,
        'risk_usd' : new_risk,
    }


def breakeven_trigger_price(entry: float, t1: float, direction: str,
                             trigger_pct: float = 0.40) -> float:
    """Price at which to move SL to BE (40% of the way to T1)."""
    if direction == 'BULLISH':
        return entry + (t1 - entry) * trigger_pct
    return entry - (entry - t1) * trigger_pct


def mae_exit_price(entry: float, sl: float, direction: str,
                   mae_pct: float = 0.85) -> float:
    """Early exit price when trade has moved 85% of SL distance against us."""
    sl_dist = abs(entry - sl)
    if direction == 'BULLISH':
        return entry - sl_dist * mae_pct
    return entry + sl_dist * mae_pct
