# utils/brokerage.py — Indian F&O brokerage calculator (Fyers, NSE)
#
# Charges per leg (verified against live Fyers order screen, May 2026):
#   Brokerage    : Rs 20 flat per executed order  (Fyers)
#   STT          : 0.02% on SELL leg turnover only (Budget 2024, eff. Oct 1 2024)
#   NSE Exch+Clr : 0.00223% per leg turnover      (exchange 0.00173% + clearing 0.00048% + rounding)
#   SEBI         : Rs 10 per crore per leg
#   NSE IPFT     : Rs 10 per crore per leg        (Investor Protection Fund Trust levy)
#   Stamp duty   : 0.002% on BUY leg only
#   GST          : 18% on (brokerage + exchange + SEBI) per leg — NOT on STT/stamp/IPFT
#
# Round-trip = entry leg charges + exit leg charges (called twice internally).

BROKERAGE_PER_ORDER  = 20.0        # Rs per executed order (Fyers flat)
STT_SELL_PCT         = 0.0002      # 0.02% on sell-leg turnover (futures, post Oct 2024)
NSE_EXCH_PCT         = 0.0000223   # 0.00223% per leg turnover (exchange + clearing)
SEBI_PER_CRORE       = 10.0        # Rs 10 per crore of leg turnover
NSE_IPFT_PER_CRORE   = 10.0        # Rs 10 per crore — NSE IPFT levy (separate from SEBI)
STAMP_BUY_PCT        = 0.00002     # 0.002% on buy-leg turnover only
GST_PCT              = 0.18        # 18% on (brokerage + exchange + SEBI) per leg


def _leg_charges(turnover: float, is_sell: bool, is_buy: bool) -> dict:
    """Charges for a single order leg (entry or exit)."""
    brokerage = BROKERAGE_PER_ORDER
    stt       = STT_SELL_PCT * turnover if is_sell else 0.0
    exchange  = NSE_EXCH_PCT * turnover
    sebi      = (SEBI_PER_CRORE   / 1e7) * turnover
    ipft      = (NSE_IPFT_PER_CRORE / 1e7) * turnover
    stamp     = STAMP_BUY_PCT * turnover if is_buy else 0.0
    gst       = GST_PCT * (brokerage + exchange + sebi)
    total     = brokerage + stt + exchange + sebi + ipft + stamp + gst
    return {
        'brokerage': brokerage, 'stt': stt, 'exchange': exchange,
        'sebi': sebi, 'ipft': ipft, 'stamp': stamp, 'gst': gst, 'total': total,
    }


def calc_trade_cost(entry_price: float, exit_price: float,
                    quantity: int, direction: str = 'BUY') -> dict:
    """
    Calculate all-in transaction costs for one complete F&O futures trade.

    Args:
        entry_price : price at which position was opened
        exit_price  : price at which position was closed
        quantity    : number of units (e.g. 75 for 1 NIFTY lot)
        direction   : 'BUY'/'BULLISH' or 'SELL'/'BEARISH'

    Returns dict with individual components + 'total' (all in Rs).
    """
    is_long = direction in ('BUY', 'BULLISH')
    if is_long:
        buy_value  = entry_price * quantity
        sell_value = exit_price  * quantity
    else:
        buy_value  = exit_price  * quantity   # cover (buy back) is the buy leg
        sell_value = entry_price * quantity   # initial short is the sell leg

    entry_leg = _leg_charges(buy_value  if is_long else sell_value, is_sell=not is_long, is_buy=is_long)
    exit_leg  = _leg_charges(sell_value if is_long else buy_value,  is_sell=is_long,     is_buy=not is_long)

    def _add(k):
        return round(entry_leg[k] + exit_leg[k], 2)

    return {
        'brokerage' : _add('brokerage'),
        'stt'       : _add('stt'),
        'exchange'  : _add('exchange'),
        'sebi'      : _add('sebi'),
        'ipft'      : _add('ipft'),
        'stamp'     : _add('stamp'),
        'gst'       : _add('gst'),
        'total'     : _add('total'),
        # Per-leg breakdown for transparency
        'entry_leg' : {k: round(v, 2) for k, v in entry_leg.items()},
        'exit_leg'  : {k: round(v, 2) for k, v in exit_leg.items()},
    }


def net_pnl(gross_pnl: float, entry_price: float, exit_price: float,
            quantity: int, direction: str = 'BUY') -> tuple:
    """
    Returns (net_pnl, cost_breakdown) after deducting all charges from gross P&L.
    """
    costs = calc_trade_cost(entry_price, exit_price, quantity, direction)
    return round(gross_pnl - costs['total'], 2), costs
