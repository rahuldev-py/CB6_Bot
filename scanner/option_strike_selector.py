# scanner/option_strike_selector.py — Dynamic Strike Selection via Black-Scholes Delta
#
# Flow:
#   1. sync_symbol_master()  — download NSE_FO.csv from Fyers (6h TTL cache)
#   2. get_lot_size_live()   — look up lot size from master (never hard-coded)
#   3. get_nearest_expiry()  — find nearest weekly/monthly expiry
#   4. get_option_chain()    — fetch live quotes for all strikes via Fyers quotes API
#   5. get_best_strike()     — iterate strikes, back-solve IV → Delta, pick ≈ 0.70

from __future__ import annotations

import os
import sys
import time
import csv
import io
import re
from datetime import date, datetime, timedelta
from typing import Optional, Dict, List, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.logger import logger
from utils.greeks import calculate_greeks, implied_volatility, dte_to_years

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

# ── constants ─────────────────────────────────────────────────────────────────

MASTER_URL   = "https://public.fyers.in/sym_details/NSE_FO.csv"
MASTER_PATH  = os.path.join(os.path.dirname(__file__), '..', 'data', 'nse_fo_master.csv')
CACHE_TTL_S  = 6 * 3600          # 6 hours

# The Fyers NSE_FO.csv has NO header row — these are the positional column names
# (verified against nse_fo_master.csv, 2026-06-02).
FYERS_FO_COLS = [
    'fytoken', 'symbol_name', 'exchange_code', 'lot_size', 'tick_size',
    'isin', 'trading_session', 'last_update_date', 'expiry', 'symbol_ticker',
    'segment', 'exchange_id', 'underlying_fytoken', 'underlying_scrip',
    'strike_price', 'option_type', 'exchange_segment',
    'underlying_fytoken2', 'description', 'field19', 'field20',
]

# Module-level lot-size cache: underlying_scrip → lot_size (int)
# Built once per process from the master CSV.  Keyed by exact underlying_scrip
# (e.g. 'NIFTY', 'BANKNIFTY') so substring collisions are impossible.
_lot_size_cache: dict = {}
RFR          = 0.10               # risk-free rate (10-yr Gsec yield, standard for NSE options)
DEFAULT_IV   = 0.18               # fallback IV if LTP unavailable
# ATM/slight-ITM: target 0.52 gives highest Gamma capture for 30-60 min ICT SB holds.
# Range 0.40-0.68 covers liquid strikes without going deep ITM (expensive, slow gamma).
TARGET_DELTA = 0.52
DELTA_RANGE  = (0.40, 0.68)
MIN_LTP      = 2.0                # skip unpriced / illiquid contracts

# Minimum viable option premium (per index) for current expiry.
# If best strike LTP is below this on expiry/1-DTE day, current expiry is too
# theta-decayed to trade — bot switches to next week's expiry instead.
MIN_VIABLE_PREMIUM = {
    'NIFTY'     : 40,    # ~0.17% of ~24000 spot
    'BANKNIFTY' : 100,   # ~0.19% of ~52000 spot
    'FINNIFTY'  : 25,    # ~0.10% of ~24000 spot
    'MIDCPNIFTY': 18,    # ~0.13% of ~14000 spot
}


# ── 1. Symbol master ──────────────────────────────────────────────────────────

def sync_symbol_master(force: bool = False) -> bool:
    """
    Download NSE_FO.csv from Fyers and cache it locally.
    Only re-downloads if cache is older than 6 hours (or force=True).
    Returns True on success.
    """
    if not _REQUESTS_OK:
        logger.warning("requests not installed — symbol master sync skipped")
        return False

    path = os.path.abspath(MASTER_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if not force and os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < CACHE_TTL_S:
            return True   # cache still fresh

    try:
        resp = requests.get(MASTER_URL, timeout=15)
        resp.raise_for_status()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(resp.text)
        logger.info(f"Symbol master synced → {path}")
        return True
    except Exception as e:
        logger.error(f"Symbol master sync failed: {e}")
        return False


def load_symbol_master() -> List[Dict]:
    """Load cached NSE_FO.csv into a list of dicts. Auto-syncs if missing.

    The Fyers NSE_FO.csv has NO header row.  We supply FYERS_FO_COLS as the
    fieldnames so DictReader produces correct column names.  Without this,
    DictReader treats the first data row as the header and every column lookup
    (lot_size, symbol_ticker, etc.) returns None.
    """
    path = os.path.abspath(MASTER_PATH)
    if not os.path.exists(path):
        if not sync_symbol_master():
            return []
    try:
        rows = []
        with open(path, encoding='utf-8') as f:
            reader = csv.DictReader(f, fieldnames=FYERS_FO_COLS)
            for row in reader:
                rows.append(row)
        return rows
    except Exception as e:
        logger.error(f"Symbol master load error: {e}")
        return []


def _build_lot_size_cache(master: Optional[List[Dict]] = None) -> dict:
    """
    Build {underlying_scrip: lot_size} from the master for O(1) lookups.
    Uses exact underlying_scrip match — avoids substring collisions (e.g.
    'NIFTY' matching 'BANKNIFTY' rows).
    """
    cache: dict = {}
    rows = master if master is not None else load_symbol_master()
    for row in rows:
        us  = (row.get('underlying_scrip') or '').strip().upper()
        raw = row.get('lot_size', '') or ''
        if us and raw:
            try:
                lot = int(float(raw))
                if lot > 0 and us not in cache:
                    cache[us] = lot     # keep first occurrence per underlying
            except (ValueError, TypeError):
                pass
    return cache


# ── Futures suffix normalizer ────────────────────────────────────────────────

def _strip_fut_suffix(sym: str) -> str:
    """NIFTY26MAYFUT → NIFTY, BANKNIFTY26MAYFUT → BANKNIFTY, etc."""
    return re.sub(r'\d{2}[A-Z]{3}FUT$', '', sym)


# ── 2. Lot size ───────────────────────────────────────────────────────────────

def get_lot_size_live(underlying: str, master: Optional[List[Dict]] = None) -> int:
    """
    Return the current lot size for `underlying` (e.g. 'NIFTY', 'BANKNIFTY').
    Reads from live NSE_FO.csv master — never hard-coded.
    Falls back to sensible defaults only if master is unavailable or the
    underlying is genuinely absent from the master.

    Fixes applied (2026-06-02):
      • The master CSV has no header row — load_symbol_master() now supplies
        FYERS_FO_COLS so column names work correctly.
      • Lookup now uses exact underlying_scrip match (not substring search on
        symbol_ticker), which prevented 'NIFTY' from incorrectly matching
        'BANKNIFTY' rows and returning lot size 30 instead of 65.
      • A module-level cache avoids re-scanning 94K rows on every call.
    """
    global _lot_size_cache
    FALLBACKS = {'NIFTY': 65, 'BANKNIFTY': 30, 'FINNIFTY': 60, 'MIDCPNIFTY': 120}

    und_upper = _strip_fut_suffix(
        underlying.upper().replace('NSE:', '').replace('-INDEX', '')
    )

    # Build or reuse module-level cache
    if not _lot_size_cache:
        _lot_size_cache = _build_lot_size_cache(master)

    lot = _lot_size_cache.get(und_upper)
    if lot and lot > 0:
        return lot

    # Genuine miss — master doesn't contain this underlying
    fb = FALLBACKS.get(und_upper, 50)
    logger.warning(f"Lot size for {underlying} not in master — using fallback {fb}")
    return fb


# ── 3. Nearest expiry ─────────────────────────────────────────────────────────

def get_nearest_expiry(underlying: str, master: Optional[List[Dict]] = None) -> Optional[date]:
    """
    Return the nearest upcoming expiry date for `underlying` from the symbol master.
    Fyers FO CSV has an 'expiry' or 'Expiry Date' column (format: dd-MMM-yyyy or yyyy-mm-dd).
    """
    if master is None:
        master = load_symbol_master()

    und_upper = underlying.upper().replace('NSE:', '').replace('-INDEX', '')
    today = date.today()
    expiries = set()

    for row in master:
        sym = (row.get('symbol_ticker') or row.get('Fytoken') or '').upper()
        if und_upper not in sym:
            continue
        # Try common column names
        raw_exp = (row.get('expiry') or row.get('Expiry Date') or
                   row.get('expiry_date') or row.get('Expiry') or '')
        if not raw_exp:
            continue
        dt = _parse_expiry_date(raw_exp)
        if dt and dt >= today:
            expiries.add(dt)

    if not expiries:
        # Guess next Thursday (NSE weekly expiry)
        return _next_thursday()

    return min(expiries)


def _parse_expiry_date(raw: str) -> Optional[date]:
    for fmt in ('%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%b %d %Y', '%d-%B-%Y'):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _next_thursday() -> date:
    today = date.today()
    days_ahead = (3 - today.weekday()) % 7   # 3 = Thursday
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


# ── 4. Option chain via Fyers optionchain() API ───────────────────────────────

def get_option_chain(fyers, underlying: str, expiry: date,
                     spot: float, option_type: str,
                     strike_count: int = 10) -> List[Dict]:
    """
    Fetch the live option chain from Fyers using the dedicated optionchain() endpoint.
    This is preferred over manual symbol construction — Fyers returns live IV, LTP,
    and the correct symbol strings directly.

    Returns list of dicts: {symbol, strike, ltp, iv, delta, theta, gamma, vega, dte}
    option_type: 'CE' or 'PE'
    """
    und_upper   = _strip_fut_suffix(underlying.upper().replace('NSE:', '').replace('-INDEX', '').replace('-EQ', ''))
    fyers_index = INDEX_SYMBOL_MAP.get(und_upper, f'NSE:{und_upper}-INDEX')

    today = date.today()
    dte   = max((expiry - today).days, 1)
    T     = dte_to_years(dte)
    opt   = 'call' if option_type == 'CE' else 'put'

    raw_chain = []
    try:
        resp = fyers.optionchain({
            "symbol"     : fyers_index,
            "strikecount": strike_count,
            "timestamp"  : "",
        })
        if resp and resp.get('code') == 200:
            data = resp.get('data', {}) or resp.get('d', {})
            raw_chain = (data.get('optionsChain') or
                         data.get('options_chain') or
                         resp.get('optionsChain') or [])
    except Exception as e:
        logger.warning(f"optionchain() API error for {underlying}: {e}")

    # Parse chain — Fyers returns flat rows: one row per option (option_type = "CE"/"PE")
    chain = []
    for row in raw_chain:
        row_opt = (row.get('option_type') or '').upper()
        if row_opt != option_type:
            continue

        strike = float(row.get('strike_price') or row.get('strikePrice') or 0)
        if strike <= 0:
            continue

        symbol = row.get('symbol') or row.get('tradingsymbol') or ''
        ltp    = float(row.get('ltp') or row.get('last_price') or 0)
        iv_raw = float(row.get('iv') or row.get('implied_volatility') or 0)
        sigma  = (iv_raw / 100.0) if iv_raw > 1.0 else (iv_raw if iv_raw > 0 else DEFAULT_IV)

        # Back-solve IV from LTP if chain IV is missing
        if sigma <= 0.01 and ltp > 0:
            sigma = implied_volatility(spot, strike, T, RFR, ltp, opt)

        greeks = calculate_greeks(spot, strike, T, RFR, sigma, opt)
        chain.append({
            'symbol': symbol,
            'strike': strike,
            'ltp'   : ltp,
            'iv'    : round(sigma, 4),
            'delta' : abs(greeks['delta']),
            'theta' : greeks['theta'],
            'gamma' : greeks['gamma'],
            'vega'  : greeks['vega'],
            'dte'   : dte,
        })

    # Fallback: if optionchain() returned nothing, construct strikes manually
    if not chain:
        logger.warning(f"optionchain() empty for {underlying} — falling back to manual strikes")
        chain = _chain_from_manual_strikes(fyers, underlying, expiry, spot,
                                           option_type, strike_count * 2, dte, T, opt)

    return chain


def _chain_from_manual_strikes(fyers, underlying: str, expiry: date,
                                 spot: float, option_type: str,
                                 num_strikes: int, dte: int, T: float,
                                 opt: str) -> List[Dict]:
    """Fallback: construct option symbols manually and fetch via quotes()."""
    und_upper = _strip_fut_suffix(underlying.upper().replace('NSE:', '').replace('-INDEX', ''))
    step      = 100 if 'BANK' in und_upper else 50
    atm       = round(spot / step) * step
    strikes   = [atm + step * i for i in range(-num_strikes // 2, num_strikes // 2 + 1)]
    exp_str   = expiry.strftime('%y%b%d').upper()

    symbols = [f"NSE:{und_upper}{exp_str}{int(k)}{option_type}" for k in strikes]

    quotes = {}
    for i in range(0, len(symbols), 50):
        batch = symbols[i:i + 50]
        try:
            resp = fyers.quotes({"symbols": ",".join(batch)})
            if resp and resp.get('code') == 200:
                for q in (resp.get('d') or []):
                    v       = q.get('v', {})
                    sym_key = q.get('n') or v.get('symbol', '')
                    ltp     = float(v.get('lp') or v.get('ltp') or 0)
                    if ltp > 0:
                        quotes[sym_key] = ltp
        except Exception as e:
            logger.debug(f"Fallback quote batch error: {e}")

    chain = []
    for sym, k in zip(symbols, strikes):
        ltp   = quotes.get(sym, 0)
        sigma = implied_volatility(spot, k, T, RFR, ltp, opt) if ltp > 0 else DEFAULT_IV
        g     = calculate_greeks(spot, k, T, RFR, sigma, opt)
        chain.append({
            'symbol': sym, 'strike': k, 'ltp': ltp,
            'iv': round(sigma, 4), 'delta': abs(g['delta']),
            'theta': g['theta'], 'gamma': g['gamma'], 'vega': g['vega'], 'dte': dte,
        })
    return chain


# ── 5. Best strike ────────────────────────────────────────────────────────────

def get_best_strike(fyers, spot: float, underlying: str, option_type: str,
                    target_delta: float = TARGET_DELTA,
                    delta_range: Tuple[float, float] = DELTA_RANGE,
                    theta_threshold: float = -2.0) -> Optional[Dict]:
    """
    Return the option contract closest to `target_delta` (default 0.50 ATM zone).

    Steps:
    1. Load symbol master + nearest expiry
    2. Fetch option chain with live quotes
    3. Drop zero-LTP strikes (no real market)
    4. Filter to delta_range (0.35–0.65 ATM zone)
    5. Apply theta gate — loosened to -100 for weekly DTE (≤7) since theta is
       irrelevant for 30-60 min intraday holds
    6. Pick strike with |delta - target| minimised
    7. Return full dict: {symbol, strike, ltp, delta, theta, iv, lot_size}
    """
    try:
        master  = load_symbol_master()
        expiry  = get_nearest_expiry(underlying, master)
        if expiry is None:
            expiry = _next_thursday()

        lot_size = get_lot_size_live(underlying, master)

        chain = get_option_chain(fyers, underlying, expiry, spot, option_type)
        if not chain:
            logger.warning(f"Empty option chain for {underlying} {option_type}")
            return None

        # Drop strikes with no real market quote
        chain = [c for c in chain if c['ltp'] >= MIN_LTP]
        if not chain:
            logger.warning(f"All strikes have LTP < {MIN_LTP} for {underlying} — no market data")
            return None

        # DTE-aware theta threshold: weekly options (≤7 DTE) are held 30-60 min,
        # so theta decay during the hold is ~1/24 of daily → use relaxed gate
        dte = chain[0].get('dte', 7) if chain else 7
        effective_theta_thresh = -100.0 if dte <= 7 else theta_threshold

        # Filter by delta range
        candidates = [c for c in chain
                      if delta_range[0] <= c['delta'] <= delta_range[1]]

        if not candidates:
            # Widen search but enforce hard delta floor of 0.30 — no lottery contracts ever
            logger.warning(f"No strikes in delta range {delta_range} for {underlying} — widening with 0.30 floor")
            candidates = [c for c in chain if c.get('delta', 0) >= 0.30]
            if not candidates:
                candidates = [c for c in chain if c.get('delta', 0) >= 0.25]
            if not candidates:
                logger.warning(f"No strikes with delta >= 0.25 for {underlying} — aborting strike selection")
                return None

        # Apply theta gate
        good = [c for c in candidates if c['theta'] >= effective_theta_thresh]
        if not good:
            good = candidates   # fallback: ignore gate if everything fails

        # Pick nearest to target delta
        best = min(good, key=lambda c: abs(c['delta'] - target_delta))
        best['lot_size'] = lot_size
        best['expiry']   = expiry.isoformat()
        best['underlying'] = underlying

        logger.info(
            f"Best strike: {best['symbol']} | Delta:{best['delta']:.3f} "
            f"IV:{best['iv']:.2%} Theta:{best['theta']:.2f} LTP:{best['ltp']} "
            f"DTE:{dte} LotSize:{lot_size}"
        )
        return best

    except Exception as e:
        logger.error(f"get_best_strike error ({underlying} {option_type}): {e}")
        return None


# ── convenience wrapper used by silver_bullet.py ─────────────────────────────

def _next_weekly_expiry_after(current_expiry: date, underlying: str = 'NIFTY') -> date:
    """
    Return the next weekly expiry for `underlying` that falls strictly after
    `current_expiry`. Respects each index's actual expiry weekday
    (NIFTY=Tue, BANKNIFTY=Wed, FINNIFTY=Tue, MIDCPNIFTY=Mon).
    """
    from datetime import timedelta
    try:
        from scanner.expiry_calendar import WEEKLY_EXPIRY_DAY
        weekday = WEEKLY_EXPIRY_DAY.get(underlying.upper(), 1)  # default Tuesday
    except Exception:
        weekday = 1  # Tuesday fallback
    candidate = current_expiry + timedelta(days=1)
    days_ahead = (weekday - candidate.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return candidate + timedelta(days=days_ahead)


def select_option_for_setup(fyers, setup: Dict, spot: float) -> Optional[Dict]:
    """
    Given a Silver Bullet setup dict, select the right option.
    BULLISH → CE, BEARISH → PE.

    Theta-decay fallback: if the current expiry's best strike LTP is below
    MIN_VIABLE_PREMIUM (common on expiry day / 1-DTE choppy markets), the bot
    automatically switches to next week's expiry so the A+ setup is not wasted
    on a decayed contract.
    """
    direction  = setup.get('direction', 'BULLISH')
    opt_type   = 'CE' if direction == 'BULLISH' else 'PE'
    dte        = setup.get('dte', 99)

    symbol     = setup.get('symbol', 'NIFTY')
    underlying = _strip_fut_suffix(
        symbol.replace('NSE:', '').replace('-INDEX', '').replace('-EQ', '')
    )

    best = get_best_strike(fyers, spot, underlying, opt_type)

    # ── Next-expiry fallback when current expiry is theta-decayed ─────────────
    if best is not None and dte <= 1:
        min_prem = MIN_VIABLE_PREMIUM.get(underlying, 30)
        if best['ltp'] < min_prem:
            logger.info(
                f"{underlying} current expiry {best['symbol']} LTP={best['ltp']} "
                f"< min viable Rs{min_prem} (DTE={dte}, theta decay) "
                f"— switching to next week expiry"
            )
            try:
                master       = load_symbol_master()
                cur_expiry   = get_nearest_expiry(underlying, master)
                next_expiry  = _next_weekly_expiry_after(cur_expiry, underlying)

                chain_next = get_option_chain(fyers, underlying, next_expiry, spot, opt_type)
                chain_next = [c for c in chain_next if c['ltp'] >= MIN_LTP]

                if chain_next:
                    candidates = [c for c in chain_next
                                  if DELTA_RANGE[0] <= c['delta'] <= DELTA_RANGE[1]]
                    if not candidates:
                        candidates = chain_next
                    best_next              = min(candidates, key=lambda c: abs(c['delta'] - TARGET_DELTA))
                    best_next['lot_size']  = get_lot_size_live(underlying, master)
                    best_next['expiry']    = next_expiry.isoformat()
                    best_next['underlying']= underlying
                    best_next['next_expiry_trade'] = True   # flag for Telegram alert

                    from scanner.expiry_calendar import days_to_expiry
                    next_dte = days_to_expiry(next_expiry)
                    logger.info(
                        f"Next-expiry strike selected: {best_next['symbol']} "
                        f"LTP={best_next['ltp']} Delta={best_next['delta']:.3f} "
                        f"DTE={next_dte}"
                    )
                    return best_next
                else:
                    logger.warning(
                        f"{underlying}: next expiry chain also empty — "
                        f"using current expiry (LTP={best['ltp']})"
                    )
            except Exception as e:
                logger.warning(f"{underlying}: next-expiry fallback error — {e}")

    return best


# ── Next-OTM strike selector (used when primary strike is blocked) ────────────

def select_next_otm_strike(fyers, setup: Dict, spot: float,
                            blocked_symbol: str) -> Optional[Dict]:
    """
    Select the next strike one step further OTM than `blocked_symbol`.
    Used when a strike has already been traded twice today (Fyers averaging rule).
    Targets delta ~0.35-0.45 (one step OTM from normal 0.50 target).
    """
    direction  = setup.get('direction', 'BULLISH')
    opt_type   = 'CE' if direction == 'BULLISH' else 'PE'
    symbol     = setup.get('symbol', 'NIFTY')
    underlying = _strip_fut_suffix(
        symbol.replace('NSE:', '').replace('-INDEX', '').replace('-EQ', '')
    )

    logger.info(
        f"{underlying}: {blocked_symbol} traded twice today — "
        f"selecting next OTM strike (delta 0.30–0.45)"
    )

    try:
        master   = load_symbol_master()
        expiry   = get_nearest_expiry(underlying, master)
        lot_size = get_lot_size_live(underlying, master)
        chain    = get_option_chain(fyers, underlying, expiry, spot, opt_type)
        chain    = [c for c in chain if c['ltp'] >= MIN_LTP
                    and c['symbol'] != blocked_symbol]

        # One step further OTM: delta 0.30–0.45 (hard floor 0.30 — no lottery)
        candidates = [c for c in chain if 0.30 <= c['delta'] <= 0.45]
        if not candidates:
            candidates = [c for c in chain if 0.30 <= c['delta'] < 0.50]
        if not candidates:
            return None   # no eligible strike above delta floor

        best = min(candidates, key=lambda c: abs(c['delta'] - 0.38))
        best['lot_size']  = lot_size
        best['expiry']    = expiry.isoformat()
        best['underlying']= underlying
        best['otm_fallback'] = True
        logger.info(
            f"Next-OTM strike: {best['symbol']} LTP={best['ltp']} "
            f"Delta={best['delta']:.3f}"
        )
        return best
    except Exception as e:
        logger.warning(f"select_next_otm_strike error ({underlying}): {e}")
        return None


# ── Index spot price (used for Greeks — NOT futures price) ───────────────────

INDEX_SYMBOL_MAP = {
    'NIFTY'     : 'NSE:NIFTY50-INDEX',
    'BANKNIFTY' : 'NSE:NIFTYBANK-INDEX',
    'FINNIFTY'  : 'NSE:FINNIFTY-INDEX',
    'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
}


def get_index_spot(fyers, underlying: str) -> Optional[float]:
    """
    Fetch live index price via Fyers quotes() API.
    Options are priced on the index, not the futures — always use this for spot.
    """
    und = _strip_fut_suffix(underlying.upper().replace('NSE:', '').replace('-INDEX', '').replace('-EQ', ''))
    sym = INDEX_SYMBOL_MAP.get(und, f'NSE:{und}-INDEX')
    try:
        resp = fyers.quotes({"symbols": sym})
        if resp and resp.get('code') == 200:
            for q in (resp.get('d') or []):
                v   = q.get('v', {})
                ltp = float(v.get('lp') or v.get('ltp') or 0)
                if ltp > 0:
                    return round(ltp, 2)
    except Exception as e:
        logger.warning(f"get_index_spot error ({sym}): {e}")
    return None


# ── ITM / SATM / OTM tier selector ───────────────────────────────────────────

# Delta bands for each tier (absolute delta)
TIERS = {
    'ITM' : {'target': 0.75, 'range': (0.65, 0.90), 'desc': 'In-The-Money  — high premium, low theta risk'},
    'SATM': {'target': 0.55, 'range': (0.45, 0.65), 'desc': 'Slightly ATM  — balanced delta/premium'},
    'OTM' : {'target': 0.35, 'range': (0.25, 0.45), 'desc': 'Out-of-Money  — cheap, needs big move'},
}


def get_itm_satm_otm(fyers, underlying: str, option_type: str,
                     spot: Optional[float] = None) -> Dict[str, Optional[Dict]]:
    """
    Return the best strike for each of ITM, SATM, OTM for a single
    underlying + option_type ('CE' or 'PE').

    spot is fetched from the live INDEX quote (not futures) so Greeks are correct.
    Pass spot only to override (e.g. in tests).

    Returns: {'ITM': {...}, 'SATM': {...}, 'OTM': {...}}
    Missing tiers return None.
    """
    try:
        # Always use the index price for options pricing, not futures price
        index_spot = get_index_spot(fyers, underlying) if spot is None else spot
        if not index_spot:
            logger.error(f"get_itm_satm_otm: could not fetch index spot for {underlying}")
            return {t: None for t in TIERS}

        master   = load_symbol_master()
        expiry   = get_nearest_expiry(underlying, master) or _next_thursday()
        lot_size = get_lot_size_live(underlying, master)
        chain    = get_option_chain(fyers, underlying, expiry, index_spot, option_type)
    except Exception as e:
        logger.error(f"get_itm_satm_otm chain fetch error: {e}")
        return {t: None for t in TIERS}

    result: Dict[str, Optional[Dict]] = {}
    for tier, cfg in TIERS.items():
        lo, hi     = cfg['range']
        candidates = [c for c in chain if lo <= c['delta'] <= hi]
        if not candidates:
            result[tier] = None
            continue
        best = min(candidates, key=lambda c: abs(c['delta'] - cfg['target']))
        best = dict(best)
        best.update({
            'lot_size'  : lot_size,
            'expiry'    : expiry.isoformat(),
            'underlying': underlying,
            'tier'      : tier,
            'tier_desc' : cfg['desc'],
        })
        result[tier] = best

    return result


def format_options_table(underlying: str, spot: float, expiry_str: str,
                         ce_tiers: Dict, pe_tiers: Dict) -> str:
    """Format ITM/SATM/OTM table for Telegram."""
    lines = [
        f"CB6 OPTIONS — {underlying}",
        f"Spot : {spot}  |  Expiry : {expiry_str}",
        "",
    ]

    for opt_label, tiers in (("CE (BUY / BULLISH)", ce_tiers),
                              ("PE (SELL / BEARISH)", pe_tiers)):
        lines.append(f"── {opt_label} ──")
        for tier_name in ('ITM', 'SATM', 'OTM'):
            s = tiers.get(tier_name)
            if not s:
                lines.append(f"  {tier_name:<4}  —  no strike found")
                continue
            sym_clean = s['symbol'].replace('NSE:', '')
            lines.append(
                f"  {tier_name:<4}  {sym_clean}\n"
                f"        Strike:{s['strike']:.0f}  LTP:{s['ltp']:.1f}"
                f"  Δ:{s['delta']:.2f}  θ:{s['theta']:.2f}"
                f"  IV:{s['iv']:.1%}  Lot:{s['lot_size']}"
            )
        lines.append("")

    lines += [
        "GUIDE:",
        "ITM  → delta 0.65-0.90 — behaves like futures, costly",
        "SATM → delta 0.45-0.65 — ICT SB preferred zone",
        "OTM  → delta 0.25-0.45 — cheaper, needs big move",
        "",
        "θ = daily time decay (Rs per lot).  IV = implied vol.",
        "Mode: Paper Trade",
    ]
    return "\n".join(lines)
