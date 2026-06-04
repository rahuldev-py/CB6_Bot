# scanner/option_selector.py
#
# NSE Options Contract Selector — Silver Bullet / ICT strategy
#
# Selects the best CE/PE contract for a directional setup using:
#   1. Delta targeting (0.55–0.65 ITM range) for high underlying correlation
#   2. Theta decay guard  — shifts to next expiry on D-0 / D-1 if theta > 15% of premium
#   3. IV percentile gate — rejects overpriced premiums (IV > 95th pct over 30-day window)
#
# Integration point: called by capital_router.py when available margin < futures requirement.

import math
import os
import time
from datetime import datetime, date, timedelta
from typing import Optional

import pytz

from utils.logger import logger

IST = pytz.timezone('Asia/Kolkata')

# ── Index → base symbol map (Fyers option chain naming) ──────────────────────
INDEX_BASE = {
    'NIFTY'     : 'NIFTY',
    'BANKNIFTY' : 'BANKNIFTY',
    'FINNIFTY'  : 'FINNIFTY',
    'MIDCPNIFTY': 'MIDCPNIFTY',
}

# Strike spacing per index (NSE option chain grid in points)
STRIKE_GAPS = {
    'NIFTY'     : 50,
    'BANKNIFTY' : 100,
    'FINNIFTY'  : 50,
    'MIDCPNIFTY': 25,
}

# Target delta range (ITM — high underlying correlation)
DELTA_TARGET_LOW  = 0.55
DELTA_TARGET_HIGH = 0.65

# Theta decay safety threshold (fraction of premium that may decay in 45 min)
THETA_DECAY_MAX_PCT = 0.15   # 15% of premium → shift to next expiry

# IV percentile rejection threshold
IV_PERCENTILE_GATE = 0.95    # reject if IV > 95th pct of 30-day window

# ── Execution-integrity constants ─────────────────────────────────────────────
NSE_OPTION_TICK    = 0.05    # NSE index option minimum price tick (₹)
LIMIT_OFFSET_TICKS = 5       # anti-slippage limit offset: Ask − 5 × tick = Ask − ₹0.25
MAX_SPREAD_PCT     = 0.02    # 2% bid-ask spread threshold → switch to limit order
DELTA_THETA_MIN_RATIO = 2.0  # minimum |Δ| / theta_decay_fraction — reject below this

# In-memory IV history cache  {symbol: [iv_float, ...]}
_iv_history: dict = {}
_iv_history_lock = __import__('threading').Lock()


# ── Black-Scholes helpers ─────────────────────────────────────────────────────

def _erf(x: float) -> float:
    """Approximate erf — avoids scipy dependency."""
    t = 1.0 / (1.0 + 0.3275911 * abs(x))
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741
                + t * (-1.453152027 + t * 1.061405429))))
    result = 1.0 - poly * math.exp(-x * x)
    return result if x >= 0 else -result


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + _erf(x / math.sqrt(2.0)))


def bs_delta(
    S: float,     # spot price
    K: float,     # strike price
    T: float,     # time to expiry in years
    r: float,     # risk-free rate (annual)
    sigma: float, # implied volatility (annual)
    option_type: str = 'call',
) -> float:
    """Black-Scholes delta for European option."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.5
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        if option_type == 'call':
            return _norm_cdf(d1)
        return _norm_cdf(d1) - 1.0
    except Exception:
        return 0.5


def bs_theta(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str = 'call',
) -> float:
    """Black-Scholes theta (per calendar day, in price units)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        phi_d1 = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
        if option_type == 'call':
            theta = (-S * phi_d1 * sigma / (2 * sqrt_T)
                     - r * K * math.exp(-r * T) * _norm_cdf(d2))
        else:
            theta = (-S * phi_d1 * sigma / (2 * sqrt_T)
                     + r * K * math.exp(-r * T) * _norm_cdf(-d2))
        return theta / 365.0   # per calendar day
    except Exception:
        return 0.0


def _implied_vol_newton(
    market_price: float,
    S: float, K: float, T: float, r: float,
    option_type: str = 'call',
    max_iter: int = 50,
) -> float:
    """Solve for IV via Newton-Raphson given market option price."""
    if market_price <= 0 or T <= 0:
        return 0.20   # fallback 20%
    sigma = 0.25
    for _ in range(max_iter):
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        phi_d1 = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
        if option_type == 'call':
            price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
        else:
            price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        vega = S * phi_d1 * math.sqrt(T)
        diff = price - market_price
        if abs(diff) < 1e-6:
            break
        if vega < 1e-10:
            break
        sigma -= diff / vega
        sigma = max(0.001, min(sigma, 5.0))
    return sigma


# ── Expiry helpers ────────────────────────────────────────────────────────────

def _get_expiry_dates(index_name: str, fyers=None) -> list:
    """
    Return upcoming expiry dates for this index (closest first).
    Falls back to heuristic Thursday schedule when broker API unavailable.
    """
    today = date.today()

    # Try to fetch from Fyers option chain (most accurate)
    if fyers is not None:
        try:
            resp = fyers.optionchain({'symbol': f'NSE:{INDEX_BASE.get(index_name, index_name)}',
                                      'strikecount': 1, 'timestamp': ''})
            if resp and resp.get('s') == 'ok':
                exps = sorted({
                    e.get('expiry') or e.get('expiryDate', '')
                    for e in resp.get('optionsChain', []) if e.get('expiry')
                })
                dates = []
                for e in exps:
                    try:
                        dates.append(datetime.strptime(str(e), '%d-%b-%Y').date())
                    except Exception:
                        pass
                if dates:
                    return [d for d in sorted(dates) if d >= today]
        except Exception:
            pass

    # Heuristic: NSE weekly expiry on Thursdays
    # BANKNIFTY = Thursday; NIFTY = Thursday; FINNIFTY = Tuesday; MIDCPNIFTY = Monday
    weekday_map = {'BANKNIFTY': 3, 'NIFTY': 3, 'FINNIFTY': 1, 'MIDCPNIFTY': 0}
    target_wd = weekday_map.get(index_name.upper(), 3)
    expiries = []
    d = today
    while len(expiries) < 4:
        if d.weekday() == target_wd:
            expiries.append(d)
        d += timedelta(days=1)
    return expiries


def _tte_years(expiry_date: date) -> float:
    """Time-to-expiry in years (trading-day fraction)."""
    now_ist = datetime.now(IST)
    market_close = datetime(now_ist.year, now_ist.month, now_ist.day,
                            15, 30, 0, tzinfo=IST)
    if now_ist > market_close:
        delta_days = (expiry_date - date.today()).days
    else:
        remaining_today = (market_close - now_ist).total_seconds() / 3600.0
        delta_days = (expiry_date - date.today()).days + remaining_today / 6.5
    return max(delta_days / 365.0, 1e-6)


def _is_expiry_or_tomorrow(expiry_date: date) -> bool:
    today = date.today()
    return expiry_date == today or expiry_date == today + timedelta(days=1)


# ── IV percentile tracking ────────────────────────────────────────────────────

def _update_iv_history(symbol: str, iv: float) -> None:
    with _iv_history_lock:
        hist = _iv_history.setdefault(symbol, [])
        hist.append(iv)
        if len(hist) > 30 * 75:   # ~30 trading days × 75 bars/day
            _iv_history[symbol] = hist[-30 * 75:]


def _iv_percentile(symbol: str, iv: float) -> float:
    with _iv_history_lock:
        hist = _iv_history.get(symbol, [])
    if len(hist) < 20:
        return 0.5   # insufficient data — don't block
    below = sum(1 for x in hist if x <= iv)
    return below / len(hist)


# ── Execution-integrity guards ───────────────────────────────────────────────

def check_execution_spread(
    live_bid: float,
    live_ask: float,
) -> dict:
    """
    Anti-slippage bid/ask spread guard.

    ICT displacement legs are explosive — a market order during a fast move
    lands at the top of a wick.  If the bid-ask spread exceeds 2% of the
    bid price, switch to a limit order placed at (Ask − ₹0.25) to avoid
    chasing the spike while still getting filled on any slight retrace.

    Returns:
        order_type  : 'MARKET' | 'LIMIT'
        entry_price : float   (ask for market; ask − offset for limit)
        spread_pct  : float   (spread / bid)
        wide_spread : bool
    """
    if live_bid <= 0 or live_ask <= 0:
        return {
            'order_type' : 'MARKET',
            'entry_price': max(live_ask, 0.05),
            'spread_pct' : 0.0,
            'wide_spread': False,
            'reason'     : 'bid_ask_unavailable',
        }

    spread     = live_ask - live_bid
    spread_pct = spread / live_bid

    if spread_pct > MAX_SPREAD_PCT:
        limit_price = round(live_ask - LIMIT_OFFSET_TICKS * NSE_OPTION_TICK, 2)
        limit_price = max(limit_price, live_bid + NSE_OPTION_TICK)  # never below bid
        logger.info(
            f"option_selector: wide spread {spread_pct:.1%} > {MAX_SPREAD_PCT:.0%} "
            f"— switching to LIMIT @ ₹{limit_price:.2f} (ask ₹{live_ask:.2f})"
        )
        return {
            'order_type' : 'LIMIT',
            'entry_price': limit_price,
            'spread_pct' : round(spread_pct, 4),
            'wide_spread': True,
            'reason'     : f'spread_{spread_pct:.1%}_gt_2pct',
        }

    return {
        'order_type' : 'MARKET',
        'entry_price': live_ask,
        'spread_pct' : round(spread_pct, 4),
        'wide_spread': False,
        'reason'     : 'spread_ok',
    }


def calc_option_tp(
    entry_premium: float,
    delta: float,
    underlying_target_pts: float,
) -> float:
    """
    Map an ICT structural target (in index points) to the option premium TP.

    Formula (ICT Delta-to-Target):
        TP_premium = entry_premium + |Δ| × |underlying_target_pts|

    This price is placed as an IMMEDIATE resting limit sell (CE) / buy-to-close
    (PE) the millisecond the entry order confirms, so the contract is liquidated
    exactly when the index touches the ICT liquidity pool — even on a wick spike
    that wouldn't wait for a polling cycle.

    Args:
        entry_premium        : ₹ premium at which the option was bought
        delta                : contract delta at entry (positive for CE, negative for PE)
        underlying_target_pts: index-point distance from entry to ICT target
                               (e.g. T2 − spot_entry for BULLISH)
    Returns:
        TP premium (₹), rounded to nearest tick, minimum entry + 1 tick
    """
    tp = entry_premium + abs(delta) * abs(underlying_target_pts)
    tp = round(round(tp / NSE_OPTION_TICK) * NSE_OPTION_TICK, 2)   # snap to tick grid
    return max(tp, entry_premium + NSE_OPTION_TICK)


def check_delta_theta_viability(
    delta: float,
    theta_per_day: float,
    premium: float,
    window_minutes: int = 45,
    min_ratio: float = DELTA_THETA_MIN_RATIO,
) -> tuple:
    """
    Delta/Theta horsepower ratio filter.

    Ensures the option has enough directional power (Δ) to overpower time-decay
    (θ) within the ICT Silver Bullet 45-minute trade window.

    Ratio = |Δ| / (theta_decay_fraction_in_window)
    where:
        theta_decay_fraction = (|θ/day| × window_min / 1440) / premium

    Reject if ratio < 2.0 (theta erodes > half the directional edge in the window).

    Returns: (valid: bool, reason: str, ratio: float)
    """
    if premium <= 0:
        return True, 'premium_unknown', 999.0
    if theta_per_day == 0:
        return True, 'theta_zero', 999.0

    theta_in_window   = abs(theta_per_day) * window_minutes / 1440.0
    theta_decay_frac  = theta_in_window / premium

    if theta_decay_frac <= 0:
        return True, 'negligible_theta', 999.0

    ratio = abs(delta) / theta_decay_frac

    if ratio < min_ratio:
        reason = (
            f"Δ/θ ratio {ratio:.2f} < {min_ratio:.1f} — "
            f"theta erodes {theta_decay_frac:.1%} of premium "
            f"in {window_minutes}min vs delta {abs(delta):.3f}"
        )
        return False, reason, round(ratio, 2)

    return True, f'ratio_{ratio:.2f}_ok', round(ratio, 2)


# ── Option chain fetch ────────────────────────────────────────────────────────

def _fetch_option_chain(index_name: str, expiry_date: date, fyers=None) -> list:
    """
    Fetch option chain rows for given index/expiry.
    Returns list of dicts: {strike, opt_type, ltp, iv, delta, theta, token}.
    """
    if fyers is None:
        return []
    try:
        exp_str = expiry_date.strftime('%d-%b-%Y').upper()
        resp = fyers.optionchain({
            'symbol'     : f'NSE:{INDEX_BASE.get(index_name, index_name)}',
            'strikecount': 20,
            'timestamp'  : exp_str,
        })
        if not resp or resp.get('s') != 'ok':
            return []
        rows = []
        for item in resp.get('optionsChain', []):
            for side in ('CE', 'PE'):
                data = item.get(side.lower(), {}) or item.get(side, {})
                if not data:
                    continue
                rows.append({
                    'strike'   : float(item.get('strikePrice', 0)),
                    'opt_type' : side,
                    'ltp'      : float(data.get('ltp', 0) or 0),
                    'iv'       : float(data.get('iv', 0) or 0) / 100.0,
                    'delta'    : float(data.get('delta', 0) or 0),
                    'theta'    : float(data.get('theta', 0) or 0),
                    'token'    : data.get('token') or data.get('fyToken', ''),
                    'symbol'   : data.get('symbol', ''),
                })
        return rows
    except Exception as exc:
        logger.debug(f"option_chain fetch error: {exc}")
        return []


# ── Main selector ─────────────────────────────────────────────────────────────

def select_option(
    direction: str,      # 'BULLISH' or 'BEARISH'
    index_name: str,     # 'NIFTY' | 'BANKNIFTY' | 'FINNIFTY' | 'MIDCPNIFTY'
    spot_price: float,
    fyers=None,
    risk_free_rate: float = 0.065,   # 6.5% — approx NSE 91-day T-bill
) -> Optional[dict]:
    """
    Select the best CE (BULLISH) or PE (BEARISH) contract.

    Returns dict with keys:
        symbol, strike, opt_type, expiry, ltp, delta, theta, iv,
        theta_decay_45min_pct, iv_percentile, token
    or None if no suitable contract found.
    """
    opt_type = 'CE' if direction == 'BULLISH' else 'PE'

    expiries = _get_expiry_dates(index_name, fyers)
    if not expiries:
        logger.warning(f"option_selector: no expiry dates for {index_name}")
        return None

    # Determine working expiry — skip D-0/D-1 if theta crush risk
    working_expiry = expiries[0]
    theta_crush_skip = False
    if _is_expiry_or_tomorrow(working_expiry) and len(expiries) > 1:
        # Pre-check: estimate theta decay on nearest expiry
        tte = _tte_years(working_expiry)
        strike_gap = STRIKE_GAPS.get(index_name.upper(), 50)
        # Approximate ITM strike
        atm_strike = round(spot_price / strike_gap) * strike_gap
        itm_offset = 1 if direction == 'BULLISH' else -1
        test_strike = atm_strike - itm_offset * strike_gap   # 1 strike ITM
        test_iv = 0.15   # conservative placeholder IV
        test_price = spot_price * 0.02   # rough premium estimate
        theta_val = abs(bs_theta(spot_price, test_strike, tte, risk_free_rate,
                                  test_iv, 'call' if opt_type == 'CE' else 'put'))
        decay_45min = (theta_val / 24 / 60) * 45   # theta per minute × 45
        if test_price > 0 and (decay_45min / test_price) > THETA_DECAY_MAX_PCT:
            working_expiry = expiries[1]
            theta_crush_skip = True
            logger.info(
                f"option_selector {index_name}: D-0/D-1 theta crush risk "
                f"({decay_45min/test_price:.1%} > {THETA_DECAY_MAX_PCT:.0%}) "
                f"→ shifted to next expiry {working_expiry}"
            )

    # Fetch option chain
    chain = _fetch_option_chain(index_name, working_expiry, fyers)

    # If broker chain unavailable, build synthetic candidates via BS
    if not chain:
        chain = _build_synthetic_chain(index_name, spot_price, working_expiry,
                                        risk_free_rate)

    # Filter to correct option type
    candidates = [r for r in chain if r['opt_type'] == opt_type and r['ltp'] > 0]
    if not candidates:
        logger.info(f"option_selector {index_name}: no {opt_type} candidates")
        return None

    tte = _tte_years(working_expiry)

    # Compute BS delta & theta where broker didn't provide them
    for c in candidates:
        if c['delta'] == 0:
            c['iv'] = c['iv'] or 0.15
            c['delta'] = bs_delta(spot_price, c['strike'], tte, risk_free_rate,
                                   c['iv'], 'call' if opt_type == 'CE' else 'put')
        if c['theta'] == 0 and c['iv'] > 0:
            c['theta'] = bs_theta(spot_price, c['strike'], tte, risk_free_rate,
                                   c['iv'], 'call' if opt_type == 'CE' else 'put')
        # Solve IV from market price when not provided
        if c['iv'] == 0 and c['ltp'] > 0:
            c['iv'] = _implied_vol_newton(c['ltp'], spot_price, c['strike'],
                                           tte, risk_free_rate,
                                           'call' if opt_type == 'CE' else 'put')

    # ── Delta filter: 0.55–0.65 ──────────────────────────────────────────────
    abs_delta = lambda c: abs(c['delta'])
    in_range = [c for c in candidates
                if DELTA_TARGET_LOW <= abs_delta(c) <= DELTA_TARGET_HIGH]
    if not in_range:
        # Relax to ±0.05 of target midpoint (0.60)
        in_range = sorted(candidates, key=lambda c: abs(abs_delta(c) - 0.60))[:3]
    if not in_range:
        logger.info(f"option_selector {index_name}: no candidate in delta range")
        return None

    # ── Theta decay guard ────────────────────────────────────────────────────
    valid = []
    for c in in_range:
        if c['ltp'] <= 0:
            continue
        theta_per_45min = abs(c['theta']) / 24 / 60 * 45
        decay_pct = theta_per_45min / c['ltp']
        c['theta_decay_45min_pct'] = round(decay_pct, 4)
        if decay_pct > THETA_DECAY_MAX_PCT and not theta_crush_skip:
            logger.info(
                f"option_selector {index_name} {opt_type} {c['strike']:.0f}: "
                f"theta decay {decay_pct:.1%} > {THETA_DECAY_MAX_PCT:.0%} — skip"
            )
            continue
        valid.append(c)
    if not valid:
        valid = in_range   # fall back if all are theta-heavy (expiry day edge case)

    # ── IV percentile gate ───────────────────────────────────────────────────
    filtered = []
    for c in valid:
        iv = c.get('iv', 0)
        sym_key = f"{index_name}_{opt_type}"
        if iv > 0:
            _update_iv_history(sym_key, iv)
            pct = _iv_percentile(sym_key, iv)
        else:
            pct = 0.5
        c['iv_percentile'] = round(pct, 3)
        if pct >= IV_PERCENTILE_GATE:
            logger.info(
                f"option_selector {index_name} {opt_type} {c['strike']:.0f}: "
                f"IV={iv:.1%} at {pct:.0%} percentile — overpriced, skip"
            )
            continue
        filtered.append(c)
    if not filtered:
        filtered = valid   # relax IV gate if all candidates are overpriced

    # ── Best contract: closest delta to 0.60 ────────────────────────────────
    candidates_sorted = sorted(filtered, key=lambda c: abs(abs_delta(c) - 0.60))

    # ── Delta/Theta viability filter ─────────────────────────────────────────
    # Walk from best-delta candidate downward until one passes the Δ/θ ratio.
    # A ratio < 2.0 means theta erodes more than half the directional edge in
    # the 45-min Silver Bullet window — the setup lacks option horsepower.
    best = None
    dt_ratio_final = 0.0
    for candidate in candidates_sorted:
        dt_ok, dt_reason, dt_ratio = check_delta_theta_viability(
            delta         = candidate['delta'],
            theta_per_day = candidate.get('theta', 0),
            premium       = candidate['ltp'],
            window_minutes= 45,
        )
        if dt_ok:
            best = candidate
            dt_ratio_final = dt_ratio
            break
        logger.info(
            f"option_selector {index_name} {opt_type} {candidate['strike']:.0f}: "
            f"Δ/θ FAIL — {dt_reason}"
        )

    if best is None:
        logger.info(
            f"option_selector {index_name}: all {opt_type} candidates failed "
            f"delta/theta viability (Δ/θ < {DELTA_THETA_MIN_RATIO}) — no trade"
        )
        return None

    best['delta_theta_ratio'] = dt_ratio_final
    best['expiry']            = working_expiry.strftime('%Y-%m-%d')
    best['index']             = index_name
    best['direction']         = direction

    logger.info(
        f"option_selector: {index_name} {opt_type} strike={best['strike']:.0f} "
        f"δ={best['delta']:.3f} Δ/θ={dt_ratio_final:.1f} "
        f"ltp={best['ltp']} iv={best.get('iv',0):.1%} "
        f"θ-decay={best.get('theta_decay_45min_pct',0):.1%} "
        f"iv_pct={best.get('iv_percentile',0):.0%} expiry={best['expiry']}"
    )
    return best


def _build_synthetic_chain(
    index_name: str, spot: float, expiry: date, rfr: float,
) -> list:
    """
    Build synthetic option chain when broker API is unavailable.
    Uses BS model with a flat 15% IV assumption across strikes.
    """
    gap = STRIKE_GAPS.get(index_name.upper(), 50)
    atm = round(spot / gap) * gap
    strikes = [atm + i * gap for i in range(-6, 7)]
    tte = _tte_years(expiry)
    iv = 0.15
    rows = []
    for k in strikes:
        for otype, btype in [('CE', 'call'), ('PE', 'put')]:
            delta = bs_delta(spot, k, tte, rfr, iv, btype)
            theta = bs_theta(spot, k, tte, rfr, iv, btype)
            # Rough premium via BS price
            sqrt_T = math.sqrt(tte)
            d1 = (math.log(spot / k) + (rfr + 0.5 * iv**2) * tte) / (iv * sqrt_T)
            d2 = d1 - iv * sqrt_T
            if btype == 'call':
                price = spot * _norm_cdf(d1) - k * math.exp(-rfr * tte) * _norm_cdf(d2)
            else:
                price = k * math.exp(-rfr * tte) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
            rows.append({
                'strike'   : float(k),
                'opt_type' : otype,
                'ltp'      : round(max(price, 0.05), 2),
                'iv'       : iv,
                'delta'    : round(delta, 4),
                'theta'    : round(theta, 4),
                'token'    : '',
                'symbol'   : f'NSE:{index_name}{expiry.strftime("%y%b").upper()}{k:.0f}{otype}',
            })
    return rows
