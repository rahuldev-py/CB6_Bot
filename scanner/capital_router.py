# scanner/capital_router.py
#
# NSE Capital Abstraction Layer — Futures vs Options Routing
#
# Decision logic:
#   Available margin >= futures_margin  →  route to Index Futures
#   Available margin <  futures_margin  →  run Options Selector and route to Options
#
# Margin requirements are read from index_futures.py (SEBI-regulated lot sizes).
# Broker margin is fetched from Fyers funds() API.

import os
from typing import Optional

from utils.logger import logger

# ── Index futures margin approximations (SEBI SPAN+Exposure, INR) ────────────
# These are APPROXIMATE and must be refreshed whenever SEBI revises lot sizes.
# source: index_futures.py / live SEBI circulars
_FUTURES_MARGIN_INR = {
    'NIFTY'     : 95_000,    # ~0.95L per lot (50 qty × ~1900 margin/unit)
    'BANKNIFTY' : 55_000,    # ~0.55L per lot (15 qty × ~3700 margin/unit)
    'FINNIFTY'  : 25_000,    # ~0.25L per lot (40 qty × ~625 margin/unit)
    'MIDCPNIFTY': 20_000,    # ~0.20L per lot (75 qty × ~267 margin/unit)
}

# Buffer ratio: require this multiple of theoretical SPAN margin to account for
# intraday MTM swings without hitting margin call
_MARGIN_SAFETY_BUFFER = 1.20   # 20% safety headroom


def get_available_margin(fyers) -> Optional[float]:
    """
    Fetch available cash/margin from Fyers funds API.
    Returns float (INR) or None on error.
    """
    try:
        resp = fyers.funds()
        if not resp or resp.get('s') != 'ok':
            logger.warning(f"capital_router: funds() failed: {resp}")
            return None
        fund_data = resp.get('fund_limit', [])
        # Fyers returns a list; find 'Available Balance' or 'Utilisable Margin'
        for item in fund_data:
            title = (item.get('title') or '').lower()
            if 'available' in title or 'utilisable' in title:
                val = item.get('equityAmount') or item.get('val') or 0
                return float(val)
        # Fallback: sum of equity + commodity available
        for item in fund_data:
            val = item.get('equityAmount') or 0
            if float(val) > 0:
                return float(val)
        return None
    except Exception as exc:
        logger.error(f"capital_router.get_available_margin: {exc}")
        return None


def get_futures_margin_required(index_name: str, lots: int = 1) -> float:
    """Return estimated margin (INR) for `lots` of index futures."""
    base = _FUTURES_MARGIN_INR.get(index_name.upper(), 50_000)
    return base * lots * _MARGIN_SAFETY_BUFFER


def route_trade(
    setup: dict,
    fyers,
    lots: int = 1,
) -> dict:
    """
    Evaluate available margin and return a routing decision.

    Returns:
    {
        'route'      : 'FUTURES' | 'OPTIONS',
        'index'      : str,               # e.g. 'NIFTY'
        'direction'  : 'BULLISH'|'BEARISH',
        'lots'       : int,               # futures lots (FUTURES route)
        'option'     : dict | None,       # option_selector result (OPTIONS route)
        'margin_avail': float | None,
        'margin_req'  : float,
        'reason'      : str,
    }
    """
    from scanner.capital_router import get_available_margin, get_futures_margin_required
    from scanner.option_selector import select_option, calc_option_tp, check_execution_spread

    direction  = setup.get('direction', 'BULLISH')
    symbol_raw = setup.get('symbol', '')
    index_name = _parse_index(symbol_raw)
    spot_price = _get_spot(setup)

    margin_req   = get_futures_margin_required(index_name, lots)
    margin_avail = get_available_margin(fyers)

    if margin_avail is None:
        logger.warning(
            f"capital_router [{index_name}]: margin unavailable — defaulting to OPTIONS"
        )
        opt = select_option(direction, index_name, spot_price, fyers)
        _attach_execution_plan(opt, setup)
        return {
            'route'       : 'OPTIONS',
            'index'       : index_name,
            'direction'   : direction,
            'lots'        : 0,
            'option'      : opt,
            'margin_avail': None,
            'margin_req'  : margin_req,
            'reason'      : 'margin_api_unavailable',
        }

    if margin_avail >= margin_req:
        logger.info(
            f"capital_router [{index_name}]: margin ₹{margin_avail:,.0f} >= "
            f"₹{margin_req:,.0f} — routing to FUTURES ({lots} lot)"
        )
        return {
            'route'       : 'FUTURES',
            'index'       : index_name,
            'direction'   : direction,
            'lots'        : lots,
            'option'      : None,
            'margin_avail': margin_avail,
            'margin_req'  : margin_req,
            'reason'      : 'sufficient_margin',
        }

    # Condition B — insufficient margin → Options
    logger.info(
        f"capital_router [{index_name}]: margin ₹{margin_avail:,.0f} < "
        f"₹{margin_req:,.0f} — routing to OPTIONS selector"
    )
    opt = select_option(direction, index_name, spot_price, fyers)
    _attach_execution_plan(opt, setup)
    return {
        'route'       : 'OPTIONS',
        'index'       : index_name,
        'direction'   : direction,
        'lots'        : 0,
        'option'      : opt,
        'margin_avail': margin_avail,
        'margin_req'  : margin_req,
        'reason'      : 'insufficient_margin',
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _attach_execution_plan(opt: Optional[dict], setup: dict) -> None:
    """
    Mutate `opt` in-place to add ICT-to-Options execution fields:
      - tp1_premium, tp2_premium  : TP premium levels mapped from underlying targets
      - spread_guard              : pre-built spread check (refreshed at order time)
      - underlying_t1_pts         : index points to T1
      - underlying_t2_pts         : index points to T2

    The actual bid/ask values come from the live market at order-placement time;
    `spread_guard` here uses ltp as a proxy until the real quote arrives.
    Order execution must call check_execution_spread(live_bid, live_ask) fresh.
    """
    if opt is None:
        return

    from scanner.option_selector import calc_option_tp, check_execution_spread

    sig       = setup.get('entry_signal', {})
    entry_idx = float(sig.get('entry', 0) or 0)
    t1_idx    = float(sig.get('target1', 0) or 0)
    t2_idx    = float(sig.get('target2', 0) or 0)
    direction = setup.get('direction', 'BULLISH')

    t1_pts = abs(t1_idx - entry_idx) if entry_idx and t1_idx else 0.0
    t2_pts = abs(t2_idx - entry_idx) if entry_idx and t2_idx else 0.0

    entry_premium = float(opt.get('ltp', 0) or 0)
    delta         = float(opt.get('delta', 0) or 0)

    opt['underlying_t1_pts'] = round(t1_pts, 2)
    opt['underlying_t2_pts'] = round(t2_pts, 2)
    opt['tp1_premium']       = calc_option_tp(entry_premium, delta, t1_pts) if t1_pts else None
    opt['tp2_premium']       = calc_option_tp(entry_premium, delta, t2_pts) if t2_pts else None

    # Spread guard using ltp as a proxy (bid ≈ ltp − ½ spread; ask ≈ ltp + ½ spread)
    # This is approximate. Order manager must call check_execution_spread fresh.
    proxy_bid = round(entry_premium * 0.99, 2)
    proxy_ask = round(entry_premium * 1.01, 2)
    opt['spread_guard_proxy'] = check_execution_spread(proxy_bid, proxy_ask)

    logger.info(
        f"capital_router execution plan: "
        f"entry≈₹{entry_premium:.2f} δ={delta:.3f} "
        f"T1 underlying={t1_pts:.1f}pts → tp1≈₹{opt.get('tp1_premium','?')} | "
        f"T2 underlying={t2_pts:.1f}pts → tp2≈₹{opt.get('tp2_premium','?')}"
    )


def _parse_index(symbol: str) -> str:
    """Extract index name from Fyers symbol string, e.g. 'NSE:NIFTY24DECFUT'."""
    s = symbol.upper().replace('NSE:', '').replace('-INDEX', '')
    for idx in ('BANKNIFTY', 'MIDCPNIFTY', 'FINNIFTY', 'NIFTY'):
        if idx in s:
            return idx
    return 'NIFTY'


def _get_spot(setup: dict) -> float:
    """Best-effort spot price from setup dict."""
    sig = setup.get('entry_signal', {})
    entry = sig.get('entry', 0)
    if entry and entry > 0:
        return float(entry)
    fvg = setup.get('fvg', {})
    mid = fvg.get('mid', 0)
    return float(mid) if mid else 0.0


def format_routing_alert(routing: dict) -> str:
    """Telegram-ready summary of the routing decision."""
    r   = routing['route']
    idx = routing['index']
    dir_label = 'LONG' if routing['direction'] == 'BULLISH' else 'SHORT'
    avail = routing.get('margin_avail')
    req   = routing.get('margin_req', 0)

    if r == 'FUTURES':
        return (
            f"<b>CB6 — {idx} FUTURES</b>\n"
            f"Direction : {dir_label}\n"
            f"Lots      : {routing['lots']}\n"
            f"Margin    : ₹{avail:,.0f} / req ₹{req:,.0f} ✅"
        )

    opt = routing.get('option') or {}
    strike    = opt.get('strike', '?')
    opt_type  = opt.get('opt_type', '?')
    ltp       = opt.get('ltp', 0)
    delta_val = opt.get('delta', 0)
    expiry    = opt.get('expiry', '?')
    theta_pct = opt.get('theta_decay_45min_pct', 0)
    iv_pct    = opt.get('iv_percentile', 0)
    dt_ratio  = opt.get('delta_theta_ratio', 0)
    tp1       = opt.get('tp1_premium', '—')
    tp2       = opt.get('tp2_premium', '—')
    t1_pts    = opt.get('underlying_t1_pts', 0)
    t2_pts    = opt.get('underlying_t2_pts', 0)
    sg        = opt.get('spread_guard_proxy', {})
    order_type_label = sg.get('order_type', 'MARKET')

    margin_line = (
        f"₹{avail:,.0f} < req ₹{req:,.0f} — options used"
        if avail is not None else "margin unknown — fallback to options"
    )
    return (
        f"<b>CB6 — {idx} {opt_type} {strike}</b>\n"
        f"Direction  : {dir_label}\n"
        f"Strike     : {strike}  ({opt_type})\n"
        f"Entry LTP  : ₹{ltp}  [{order_type_label}]\n"
        f"Delta      : {delta_val:.3f}  |  Δ/θ ratio: {dt_ratio:.1f}\n"
        f"Expiry     : {expiry}\n"
        f"θ-decay/45m: {theta_pct:.1%}  |  IV pct: {iv_pct:.0%}\n\n"
        f"<b>ICT TP Mapping</b>\n"
        f"T1 (+{t1_pts:.0f}pts underlying) → TP1 ₹{tp1}\n"
        f"T2 (+{t2_pts:.0f}pts underlying) → TP2 ₹{tp2}\n"
        f"(Δ × pts = exact premium target; limit TP placed on fill)\n\n"
        f"Margin     : {margin_line}"
    )
