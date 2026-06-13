# trader/order_manager.py — Fyers Order Placement for Silver Bullet Setups
#
# Responsibilities:
#   - place_silver_bullet_trade(): position-size + limit order via Fyers API
#   - SL monitor thread: T1 partial exit → SL to BE → T2 full exit
#   - Theta watchdog: exit if stuck in FVG > 15 min (options time-decay rule)

from __future__ import annotations

import sys
import os
import threading
import time
from datetime import datetime
from typing import Optional, Dict

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.logger import logger
from utils.greeks import theta_ok
from utils.trade_journal import log_entry, log_exit
from ml_engine.memory.shadow_logger import log_closed_trade
from ml_engine.memory.replay_shadow import archive_closed_trade_shadow

try:
    from trader.paper_trader import (
        open_paper_trade, update_paper_trades, load_state,
        get_option_strike_count, register_option_strike,
    )
    _PAPER_OK = True
except ImportError:
    _PAPER_OK = False

# ── constants ─────────────────────────────────────────────────────────────────

PRODUCT_MARGIN    = "MARGIN"     # NRML — carry forward (first trade on a strike)
PRODUCT_INTRADAY  = "INTRADAY"   # MIS  — intraday (second trade on same strike today)
ORDER_TYPE_LIMIT  = 1
ORDER_TYPE_MKT    = 2
SIDE_BUY          = 1
SIDE_SELL         = -1

MAX_RISK_PCT     = 0.10           # 10% of total equity (capital + profits) per trade
THETA_EXIT_MINS  = 20             # exit if stuck in FVG > 20 minutes (theta burn rule)
SL_MONITOR_SEC   = 5              # check P&L every 5 seconds (was 30 — NIFTY moves 30pts in 10s)
MPP_BUY_BUFFER   = 0.02          # +2% above LTP for limit buy — fills quickly, prevents spread overpay
MAX_SPREAD_PCT   = 0.01          # abort if bid-ask spread > 1% of bid (illiquid contract)
MIN_DAILY_VOLUME = 25_000        # abort if cumulative contracts traded today < this
ORDER_FILL_TTL   = 60            # cancel live limit order if unfilled after this many seconds
MAX_LIVE_RISK_INR = 500.0        # hard NSE live pilot cap; ML/strategy cannot override

# ── active trade tracking (in-memory, single-process) ────────────────────────

_active_trades: Dict[str, Dict] = {}   # order_id → trade metadata
_monitor_lock  = threading.Lock()


# ── position sizing ───────────────────────────────────────────────────────────

def calc_position_size(capital: float, risk_pct: float,
                       entry: float, stop_loss: float,
                       lot_size: int) -> int:
    """
    Return number of LOTS to trade.
    risk_amount = capital × risk_pct
    risk_per_lot = |entry - stop_loss| × lot_size
    lots = floor(risk_amount / risk_per_lot), minimum 1
    """
    risk_per_unit = abs(entry - stop_loss)
    if risk_per_unit <= 0:
        return 1
    risk_amount  = capital * risk_pct
    risk_per_lot = risk_per_unit * lot_size
    lots = max(1, int(risk_amount / risk_per_lot))
    return lots


def _ml_budget(setup: Dict, key: str, default: float) -> float:
    alloc = setup.get('ml_alloc') if isinstance(setup, dict) else None
    if not isinstance(alloc, dict):
        return default
    try:
        value = float(alloc.get(key, 0) or 0)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _cap_lots(lots: int, lot_size: int, risk_per_unit: float,
              *, capital_per_lot: float = 0.0,
              capital_budget: float = 0.0) -> tuple[int, str]:
    if lots <= 0 or lot_size <= 0:
        return 0, "invalid lot sizing"
    if risk_per_unit <= 0:
        return 0, "missing or zero SL distance"

    risk_per_lot = risk_per_unit * lot_size
    if risk_per_lot > MAX_LIVE_RISK_INR:
        return 0, f"1 lot risk Rs{risk_per_lot:.0f} exceeds Rs{MAX_LIVE_RISK_INR:.0f} cap"

    max_by_risk = int(MAX_LIVE_RISK_INR / risk_per_lot)
    capped = min(lots, max_by_risk)

    if capital_per_lot > 0 and capital_budget > 0:
        max_by_capital = int(capital_budget / capital_per_lot)
        if max_by_capital <= 0:
            return 0, (
                f"1 lot cost Rs{capital_per_lot:.0f} exceeds "
                f"ML capital budget Rs{capital_budget:.0f}"
            )
        capped = min(capped, max_by_capital)

    if capped < lots:
        return capped, f"clamped lots {lots}->{capped} by Rs{MAX_LIVE_RISK_INR:.0f} risk/capital cap"
    return capped, "OK"


# ── Fyers order helpers ───────────────────────────────────────────────────────

def _get_option_quote(fyers, symbol: str) -> Optional[Dict]:
    """
    Fetch live bid, ask, LTP, and daily volume for a single option contract.
    Returns {'ltp', 'bid', 'ask', 'volume'} or None.
    Used for liquidity validation and MPP limit-price calculation before execution.
    """
    try:
        resp = fyers.quotes({"symbols": symbol})
        if resp and resp.get('code') == 200:
            items = resp.get('d') or []
            if items:
                v   = items[0].get('v', {})
                ltp = float(v.get('lp') or v.get('ltp') or 0)
                bid = float(v.get('bid_price') or v.get('bid') or v.get('bp') or 0)
                ask = float(v.get('ask_price') or v.get('ask') or v.get('ap') or 0)
                vol = int(v.get('vol_traded_today') or v.get('volume') or v.get('vol') or 0)
                if ltp > 0:
                    if bid <= 0:
                        bid = round(ltp * 0.995, 2)
                    if ask <= 0:
                        ask = round(ltp * 1.005, 2)
                    return {'ltp': ltp, 'bid': bid, 'ask': ask, 'volume': vol}
    except Exception as e:
        logger.debug(f"_get_option_quote error {symbol}: {e}")
    return None


def _is_liquidity_valid(fyers, symbol: str) -> tuple[bool, Optional[Dict]]:
    """
    3-layer pre-entry liquidity gate. Applied to BOTH paper and live trades so
    paper results are not inflated by ghost fills on illiquid contracts.

    Layer 1 — Volume  : cumulative contracts traded today >= MIN_DAILY_VOLUME
    Layer 2 — Spread  : (ask - bid) / bid <= MAX_SPREAD_PCT (1%)
    Layer 3 — Book    : best bid and ask must both be non-zero

    Returns (is_valid, quote_dict). If invalid, logs the rejection reason.
    The FINNIFTY 25150 PE ghost trade had bid≈317 / ask≈349 — spread ~10%.
    That would have been rejected here long before any order was placed.
    """
    quote = _get_option_quote(fyers, symbol)
    if not quote:
        logger.warning(f"[LIQUIDITY REJECT] {symbol} — no quote data available")
        return False, None

    bid    = quote['bid']
    ask    = quote['ask']
    vol    = quote['volume']
    spread = (ask - bid) / bid if bid > 0 else float('inf')

    if bid <= 0 or ask <= 0:
        logger.warning(f"[LIQUIDITY REJECT] {symbol} — empty order book (bid={bid} ask={ask})")
        return False, None

    if vol < MIN_DAILY_VOLUME:
        logger.warning(
            f"[LIQUIDITY REJECT] {symbol} — volume {vol:,} < {MIN_DAILY_VOLUME:,} "
            f"(contract too illiquid today)"
        )
        return False, None

    if spread > MAX_SPREAD_PCT:
        logger.warning(
            f"[LIQUIDITY REJECT] {symbol} — spread {spread:.2%} > {MAX_SPREAD_PCT:.0%} "
            f"(bid={bid} ask={ask})"
        )
        return False, None

    logger.info(
        f"[LIQUIDITY OK] {symbol} vol={vol:,} bid={bid} ask={ask} spread={spread:.2%}"
    )
    return True, quote


def _handle_auth_failure(code: int, symbol: str) -> None:
    """Log + alert on 401/403 — token expired or access revoked."""
    msg = (
        f"<b>AUTH FAILURE (HTTP {code})</b>\n"
        f"Symbol: {symbol}\n"
        f"Action: Re-run <code>python auto_token.py</code> to refresh token.\n"
        f"Trading is blocked until token is refreshed."
    )
    logger.critical(f"[AUTH FAILURE] code={code} symbol={symbol} — token expired or revoked")
    try:
        from utils.telegram_alerts import send_message as _tg
        _tg(msg)
    except Exception:
        pass
    # NSE auth failure sets NSE-only stop — does NOT touch forex EMERGENCY_STOP.flag
    # Fallback removed: writing the global flag was silencing the forex engine.
    from utils.emergency_stop import set_nse_emergency_stop
    set_nse_emergency_stop(f"Fyers auth failure HTTP {code}")


def _fyers_order(fyers, symbol: str, qty: int, side: int,
                 order_type: int, limit_price: float = 0.0,
                 tag: str = "CB6SB",
                 product_type: str = PRODUCT_MARGIN,
                 _retry: int = 0) -> Optional[str]:
    """
    Place a single Fyers order. Returns order_id string or None on failure.
    product_type: PRODUCT_MARGIN (NRML/carry forward) or PRODUCT_INTRADAY (MIS).

    Error handling:
      HTTP 200/1101 → success
      HTTP 401/403  → auth failure → emergency stop + Telegram alert
      HTTP 429      → rate limit → back-off 5s and retry once
      Other codes   → rejection alert + return None
    """
    clean_tag = ''.join(ch for ch in str(tag or 'CB6SB') if ch.isalnum())[:20] or 'CB6SB'
    data = {
        "symbol"      : symbol,
        "qty"         : qty,
        "type"        : order_type,
        "side"        : side,
        "productType" : product_type,
        "limitPrice"  : round(limit_price, 2) if order_type == ORDER_TYPE_LIMIT else 0,
        "stopPrice"   : 0,
        "validity"    : "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
        "orderTag"    : clean_tag,
    }
    try:
        from core.execution_guard import execute_guarded_order
        logger.info(
            f"[TRACE] ORDER_SENT | sym={symbol} | qty={qty} | side={side} "
            f"| type={order_type} | price={round(limit_price, 2)}"
        )
        resp = execute_guarded_order(
            fyers.place_order, data,
            symbol=symbol, intent="ENTRY",
        )
        if resp and resp.get('code') == 200:
            order_id = str(resp.get('id') or resp.get('data', {}).get('id', ''))
            logger.info(f"[TRACE] ORDER_ACCEPTED | sym={symbol} | order_id={order_id}")
            logger.info(f"Order placed: {symbol} qty={qty} side={side} id={order_id}")
            return order_id

        err_code = resp.get('code', 0) if resp else 0
        err_msg  = resp.get('message', str(resp)) if resp else 'no_response'

        # HTTP 401 / 403 — token expired or access revoked
        if err_code in (401, 403, -300, -301):
            _handle_auth_failure(err_code, symbol)
            return None

        # HTTP 429 — rate limit, back-off and retry once
        if err_code == 429:
            if _retry < 1:
                logger.warning(f"[RATE LIMIT] HTTP 429 for {symbol} — backing off 5s then retry")
                time.sleep(5)
                return _fyers_order(
                    fyers, symbol, qty, side, order_type, limit_price, tag, product_type,
                    _retry=_retry + 1,
                )
            logger.error(f"[RATE LIMIT] HTTP 429 retry exhausted for {symbol}")

        logger.error(f"[TRACE] ORDER_REJECTED | sym={symbol} | code={err_code} | reason={err_msg}")
        logger.error(f"Order rejected: {resp}")
        # Telegram alert for broker rejection — silent failures must reach the trader
        try:
            from utils.telegram_alerts import send_message as _tg
            _tg(
                f"<b>ORDER REJECTED</b>\n"
                f"Symbol: {symbol}\n"
                f"Code: {err_code}\n"
                f"Reason: {err_msg}"
            )
        except Exception:
            pass
        return None
    except Exception as e:
        logger.error(f"Fyers order error: {e}")
        return None


# ── main entry — Silver Bullet trade placement ────────────────────────────────

def place_silver_bullet_trade(fyers, setup: Dict, strike_info: Dict,
                               paper_mode: bool = True) -> Optional[Dict]:
    """
    Place a Silver Bullet option trade.

    `setup`       : dict from scan_silver_bullet()
    `strike_info` : dict from get_best_strike() — contains symbol, lot_size, ltp, delta…
    `paper_mode`  : True = record in paper_trader only; False = live Fyers order

    Returns trade record dict or None.
    """
    try:
        sig       = setup['entry_signal']
        direction = setup['direction']
        symbol    = strike_info['symbol']
        lot_size  = strike_info.get('lot_size', 75)
        ltp       = strike_info.get('ltp', 0)
        delta     = strike_info.get('delta', 0)
        theta_val = strike_info.get('theta', 0)

        if ltp <= 0:
            logger.warning(f"LTP=0 for {symbol} — skipping trade")
            return None

        # ── Refresh option LTP immediately before entry ───────────────────────
        # Scan-time LTP can be 1-5 min stale; at volatile open this causes entry
        # at extremes (e.g. entering a PE at the day's HIGH). Always re-fetch.
        scan_ltp = ltp
        try:
            fresh_quote = _get_option_quote(fyers, symbol)
            if fresh_quote and fresh_quote['ltp'] > 0:
                ltp   = fresh_quote['ltp']
                delta = fresh_quote.get('delta', delta) or delta   # keep scan delta if not in quote
                drift_pct = (ltp - scan_ltp) / max(scan_ltp, 1)
                if abs(drift_pct) > 0.05:
                    # Option drifted >5% from scan price in either direction — signal stale
                    direction_str = "up" if drift_pct > 0 else "down"
                    logger.warning(
                        f"Entry aborted: {symbol} option {direction_str} {abs(drift_pct):.1%} from scan "
                        f"({scan_ltp} → {ltp}) — price drifted, signal stale"
                    )
                    return None
                if abs(drift_pct) > 0.03:
                    logger.info(f"Option LTP refreshed: {symbol} {scan_ltp} → {ltp} ({drift_pct:+.1%})")
        except Exception as _qe:
            logger.debug(f"LTP refresh failed for {symbol}: {_qe} — using scan-time LTP {ltp}")

        # ── 3-layer liquidity gate (paper AND live) ───────────────────────────
        # Rejects contracts where volume is too low or bid-ask spread is too wide.
        # Applied in paper mode too so ghost fills don't pollute strategy stats.
        liq_ok, liq_quote = _is_liquidity_valid(fyers, symbol)
        if not liq_ok:
            return None
        # Paper/live buy fills should not assume the bid. Use mid when the book
        # is available; it is conservative enough for paper without always
        # overstating slippage at the ask.
        if liq_quote:
            conservative_ltp = round((liq_quote['bid'] + liq_quote['ask']) / 2, 2)
            if conservative_ltp > 0:
                ltp = conservative_ltp

        # ── Fyers same-strike averaging rule ─────────────────────────────────
        # First trade on a strike  → NRML (carry forward, normal behaviour)
        # Second trade same strike → MIS (intraday, avoids Fyers cost averaging)
        # Third trade same strike  → blocked; try next OTM strike instead
        if _PAPER_OK:
            strike_count = get_option_strike_count(symbol)
        else:
            strike_count = 0

        if strike_count >= 2:
            logger.info(
                f"{symbol} traded {strike_count}x today — "
                f"Fyers averaging rule: trying next OTM strike"
            )
            try:
                from scanner.option_strike_selector import (
                    get_index_spot, select_next_otm_strike
                )
                underlying = setup.get('symbol', '').replace('NSE:', '').replace('-INDEX', '')
                spot = get_index_spot(fyers, underlying)
                if not spot:
                    logger.warning(f"Could not fetch index spot for {underlying} — skipping fallback")
                    return None
                alt  = select_next_otm_strike(fyers, setup, spot, symbol)
                if alt:
                    strike_info = alt
                    symbol      = alt['symbol']
                    ltp         = alt.get('ltp', ltp)
                    delta       = alt.get('delta', delta)
                    theta_val   = alt.get('theta', theta_val)
                    lot_size    = alt.get('lot_size', lot_size)
                    logger.info(f"Switched to next-OTM: {symbol} LTP={ltp}")
                else:
                    logger.warning(f"No alternate strike found — skipping trade")
                    return None
            except Exception as _fe:
                logger.warning(f"Next-OTM fallback failed: {_fe} — skipping")
                return None
            product_type = PRODUCT_MARGIN   # fresh strike → NRML
        elif strike_count == 1:
            product_type = PRODUCT_INTRADAY  # same strike, second time → MIS
            logger.info(f"{symbol} traded once today — using MIS (intraday) to avoid averaging")
        else:
            product_type = PRODUCT_MARGIN    # first trade on this strike → NRML

        # Theta gate before entry — loosen for weekly options (DTE ≤ 7)
        dte = strike_info.get('dte', 7)
        theta_thresh = -50.0 if dte <= 7 else -3.0
        if not theta_ok(theta_val, threshold=theta_thresh):
            logger.warning(f"Theta gate blocked: {symbol} theta={theta_val:.2f} dte={dte}")
            return None

        # Derive option levels from underlying ICT setup targets via delta.
        # This gives realistic intraday targets instead of arbitrary premium multiples.
        # For a BEARISH PE: underlying falls → PE premium rises by delta * pts_fallen.
        sig       = setup.get('entry_signal', {})
        und_entry = float(sig.get('entry', 0) or 0)
        und_sl    = float(sig.get('stop_loss', 0) or 0)
        und_t1    = float(sig.get('target1', 0) or 0)
        und_t2    = float(sig.get('target2', 0) or 0)
        und_t3    = float(sig.get('target3', 0) or 0)
        d = abs(delta) if delta else 0.50

        if und_entry > 0 and und_sl > 0 and und_t1 > 0:
            sl_pts = abs(und_entry - und_sl)
            t1_pts = abs(und_entry - und_t1)
            t2_pts = abs(und_entry - und_t2) if und_t2 else t1_pts * 1.4
            t3_pts = abs(und_entry - und_t3) if und_t3 else t1_pts * 2.0
            # Floor SL at 20% of premium — absorbs micro-noise
            opt_sl = max(round(ltp - d * sl_pts, 2), round(ltp * 0.80, 2))
            opt_t1 = round(ltp + d * t1_pts, 2)
            opt_t2 = round(ltp + d * t2_pts, 2)
            opt_t3 = round(ltp + d * t3_pts, 2)
        else:
            # Fallback when underlying setup levels not available
            opt_sl = round(ltp * 0.80, 2)
            opt_t1 = round(ltp * 1.20, 2)
            opt_t2 = round(ltp * 1.35, 2)
            opt_t3 = round(ltp * 1.60, 2)

        # Position size — 10% of total equity (capital + accumulated profits) per trade.
        # For long options the premium paid = max possible loss, so we size by premium cost.
        state        = load_state() if _PAPER_OK else {}
        base_capital = state.get('capital', 100000)
        total_pnl    = state.get('total_pnl', 0)
        total_equity = base_capital + total_pnl          # grows as profits accumulate
        options_ctx  = setup.get('options_context') or {}
        try:
            risk_mult = float(options_ctx.get('risk_multiplier', 1.0) or 1.0)
        except (TypeError, ValueError):
            risk_mult = 1.0
        risk_mult    = min(max(risk_mult, 0.0), 1.0)      # options may reduce risk, never increase it
        if setup.get('options_context_block'):
            logger.warning(f"Options context blocked trade: {symbol} {options_ctx}")
            return None
        max_spend    = total_equity * MAX_RISK_PCT * risk_mult
        premium_per_lot = ltp * lot_size
        lots = max(1, int(max_spend / premium_per_lot)) if premium_per_lot > 0 else 1

        # Session risk multiplier — expiry day / high-impact news day reduces size
        _session_risk = float(setup.get('risk_multiplier_override', 1.0))
        if _session_risk < 1.0:
            max_spend = max_spend * _session_risk
            lots = max(1, int(max_spend / premium_per_lot)) if premium_per_lot > 0 else 1
            logger.info(f"Session risk override {_session_risk:.0%} applied: max_spend={max_spend:.0f} lots={lots}")

        # SHORT (BEARISH) 1.10x lot boost — ML: BEARISH WR=68.6% PF=8.03 vs BULLISH 65.4% PF=5.66
        # Conservative: 1.10x now. Review → 1.25x after 30 live BEARISH trades.
        # Max risk cap is unchanged — only lot count grows, not the spend ceiling.
        _short_boost_applied = False
        if direction == 'BEARISH' and lots >= 1:
            lots_boosted = max(1, int(lots * 1.10))
            if lots_boosted > lots:
                lots = lots_boosted
                _short_boost_applied = True

        ml_boost = setup.get('ml_lot_boost', 1.0)
        if ml_boost > 1.0:
            lots = max(1, int(lots * ml_boost))

        risk_per_unit = max(ltp - opt_sl, 0.0)
        ml_capital_budget = _ml_budget(setup, 'capital_to_use', max_spend)
        lots, cap_reason = _cap_lots(
            lots,
            lot_size,
            risk_per_unit,
            capital_per_lot=premium_per_lot,
            capital_budget=ml_capital_budget,
        )
        if lots <= 0:
            logger.warning(f"Options live risk BLOCKED {symbol}: {cap_reason}")
            return None
        if cap_reason != "OK":
            logger.info(f"Options live risk clamp {symbol}: {cap_reason}")

        qty  = lots * lot_size
        logger.info(
            f"Position size: equity={total_equity:.0f} max_spend={max_spend:.0f} "
            f"premium/lot={premium_per_lot:.0f} → {lots} lot(s) ({qty} qty)"
            + (" [SHORT 1.10x boost]" if _short_boost_applied else "")
            + (f" [ML boost {ml_boost}×]" if ml_boost > 1.0 else "")
        )

        # Capture IV at entry for IV crush detection in SL monitor
        _entry_iv         = strike_info.get('iv', 0) or 0
        _entry_vega       = strike_info.get('vega', 0) or 0
        _entry_delta      = delta or 0.5
        _entry_underlying = float(sig.get('entry', 0) or 0)  # underlying index price at entry

        # Build trade record for paper trader
        trade_rec = {
            'symbol'            : symbol,
            'underlying'        : setup['symbol'],
            'direction'         : direction,
            'setup_type'        : 'SILVER_BULLET',
            'timeframe'         : '5',
            'entry_price'       : round(ltp, 2),
            'current_sl'        : opt_sl,
            'target1'           : opt_t1,
            'target2'           : opt_t2,
            'target3'           : opt_t3,
            'quantity'          : qty,
            'original_quantity' : qty,
            'lot_size'          : lot_size,
            'delta'             : delta,
            'theta'             : theta_val,
            'strike'            : strike_info.get('strike'),
            'expiry'            : strike_info.get('expiry'),
            'iv'                : strike_info.get('iv'),
            'window'            : setup.get('window'),
            'confluence'        : setup.get('confluence', 0),
            'fvg_entry_time'    : datetime.now().isoformat(),
            'in_fvg'            : setup.get('in_fvg', False),
            'in_ote'            : False,
            'targets_hit'       : [],
            'realized_pnl'      : 0.0,
            'pnl'               : 0.0,
            'product_type'      : product_type,
            'dte'               : setup.get('dte', 99),
            'regime'            : setup.get('regime', 'NEUTRAL'),
            'options_context'   : options_ctx,
            # IV crush tracking — captured at entry, used by _check_iv_crush in SL monitor
            'entry_iv'          : _entry_iv,
            'entry_vega'        : _entry_vega,
            'entry_delta'       : _entry_delta,
            'entry_underlying'  : _entry_underlying,
            'iv_crush_flag'     : False,
        }

        # Journal entry (always — paper and live)
        journal_id = None
        try:
            journal_id = log_entry(trade_rec, setup, strike_info)
            trade_rec['journal_id'] = journal_id
        except Exception as je:
            logger.debug(f"Journal entry error: {je}")

        # Build entry_signal dict that open_paper_trade() expects
        risk_per_unit = ltp - trade_rec['current_sl']
        reward_t3     = trade_rec['target3'] - ltp
        rr            = round(reward_t3 / risk_per_unit, 2) if risk_per_unit > 0 else 3.5
        sb_setup = {
            'symbol'          : symbol,
            'direction'       : direction,
            'timeframe'       : '5',    # SB trades are 5-min setups (must match square_off intraday filter)
            'instrument_type' : 'OPTION',
            'confluence'      : setup.get('confluence', 0),
            'window'          : setup.get('window'),
            'in_fvg'          : setup.get('in_fvg', False),
            'product_type'    : product_type,
            'quantity'        : qty,
            'original_quantity': qty,
            'underlying'      : setup['symbol'],
            'lot_size'        : lot_size,
            'delta'           : delta,
            'theta'           : theta_val,
            'strike'          : strike_info.get('strike'),
            'expiry'          : strike_info.get('expiry'),
            'iv'              : strike_info.get('iv'),
            'journal_id'      : journal_id,
            'dte'             : setup.get('dte', 99),
            'regime'          : setup.get('regime', 'NEUTRAL'),
            'options_context'  : options_ctx,
            '_candles_df'     : setup.get('_candles_df'),  # raw OHLCV for CNN/RNN training
            'entry_signal': {
                'entry'    : ltp,
                'stop_loss': trade_rec['current_sl'],
                'target1'  : trade_rec['target1'],
                'target2'  : trade_rec['target2'],
                'target3'  : trade_rec['target3'],
                'risk'     : risk_per_unit,
                'rr_ratio' : rr,
                'in_ote'   : False,
                'in_fvg'   : setup.get('in_fvg', False),
            },
        }

        # ── Conviction evaluation (Phase 7) ──────────────────────────────────
        _nse_session = "off_window"
        try:
            from datetime import datetime as _dt_om
            import pytz as _pytz_om
            _ist = _dt_om.now(_pytz_om.timezone('Asia/Kolkata'))
            _ih, _im = _ist.hour, _ist.minute
            if 10 <= _ih < 11:
                _nse_session = "nse_am"
            elif 13 <= _ih < 14:
                _nse_session = "nse_pm"
            elif _ih == 15 and _im < 30:
                _nse_session = "nse_close"
        except Exception:
            pass

        _underlying_sym = setup.get('symbol', '')
        _nse_sym_map = {
            'NIFTY50': 'NSE:NIFTY50-INDEX',
            'NIFTY':   'NSE:NIFTY50-INDEX',
            'BANKNIFTY': 'NSE:NIFTYBANK-INDEX',
            'FINNIFTY':  'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
        }
        _raw_key    = _underlying_sym.replace('NSE:', '').split('-')[0].upper()
        _conv_symbol = _nse_sym_map.get(_raw_key, _underlying_sym)
        _nse_conviction = None
        try:
            from utils.conviction_engine import evaluate_conviction as _ev_conv
            _nse_conviction = _ev_conv(
                market    = 'NSE',
                symbol    = _conv_symbol,
                direction = direction,
                setup     = setup,
                session   = _nse_session,
            )
            logger.info(
                f"NSE conviction={_nse_conviction.conviction_score:.0f} "
                f"grade={_nse_conviction.conviction_grade} "
                f"mult={_nse_conviction.recommended_risk_multiplier}× "
                f"({_conv_symbol} {_nse_session})"
            )
            if not _nse_conviction.should_trade():
                logger.info(
                    f"NSE CONVICTION BLOCK — {_conv_symbol} {direction} "
                    f"grade={_nse_conviction.conviction_grade} "
                    f"score={_nse_conviction.conviction_score:.0f}"
                )
                try:
                    from utils.telegram_alerts import send_message as _tg_cv
                    _tg_cv(
                        f"<b>⛔ NSE CONVICTION BLOCK</b>\n\n"
                        f"Symbol : {_conv_symbol}\n"
                        f"Grade  : {_nse_conviction.conviction_grade} "
                        f"({_nse_conviction.conviction_score:.0f}/100)\n"
                        f"Reason : {_nse_conviction.hard_block_reason or 'Grade D — no edge'}\n"
                        f"Session: {_nse_session}\n"
                        f"Setup  : {direction} score={setup.get('confluence',0)}/15"
                    )
                except Exception:
                    pass
                return None
        except Exception as _conv_e:
            logger.debug(f"NSE conviction eval skipped: {_conv_e}")

        if paper_mode:
            if _PAPER_OK:
                _pt_result = open_paper_trade(sb_setup)
                register_option_strike(symbol)
                logger.info(
                    f"Paper trade opened: {symbol} qty={qty} entry={ltp} "
                    f"product={product_type}"
                )
                try:
                    from utils.trade_replay import capture_entry_context as _cap_ctx
                    if _pt_result and _pt_result.get('id'):
                        _cap_ctx(
                            trade_id  = _pt_result['id'],
                            market    = 'NSE',
                            symbol    = _conv_symbol,
                            direction = direction,
                            setup     = setup,
                            session   = _nse_session,
                        )
                except Exception as _cap_e:
                    logger.debug(f"NSE trade replay capture skipped: {_cap_e}")
            return trade_rec

        # ── Live mode: ExecutionGuard check before any broker call ────────────
        # Verifies daily loss, trade count, and capital limits. ML/bot trades
        # are treated identically — blocked only if risk limits are actually hit.
        # mode="LIVE" so guard internal errors fail closed (block trade).
        from core.execution_guard import guard_dict_entry
        state_for_guard = load_state() if _PAPER_OK else {}
        from settings import CAPITAL as _CAPITAL
        _guard_ok, _guard_reason = guard_dict_entry(
            state_for_guard, _CAPITAL, symbol, mode="LIVE", intent_type="ENTRY"
        )
        if not _guard_ok:
            logger.warning(
                f"place_silver_bullet_trade BLOCKED by ExecutionGuard: "
                f"{symbol} — {_guard_reason}"
            )
            return None

        # Live mode — place strict LIMIT order at bid (never chase the ask)
        # Liquidity has already been validated above; reuse liq_quote for price.
        exec_ltp  = liq_quote['ltp'] if liq_quote else ltp
        bid_price = liq_quote['bid'] if liq_quote else ltp
        # Place at bid + 2% buffer: fills instantly against the lowest ask while
        # preventing overpayment if the ask spikes in the milliseconds between
        # quote fetch and order submission.
        mpp_limit = round(bid_price * (1 + MPP_BUY_BUFFER), 2)

        side     = SIDE_BUY   # options: always buy CE or PE
        order_id = _fyers_order(fyers, symbol, qty, side,
                                 ORDER_TYPE_LIMIT, limit_price=mpp_limit,
                                 product_type=product_type)
        if not order_id:
            return None

        # ── TTL fill tracker: wait up to ORDER_FILL_TTL seconds for COMPLETE ──
        # If the limit order sits unfilled (price moved away), cancel it rather
        # than leaving an open order that may fill at a stale/adverse price.
        filled = _wait_for_fill(fyers, order_id, timeout=ORDER_FILL_TTL)
        if not filled:
            logger.warning(
                f"Order {order_id} unfilled after {ORDER_FILL_TTL}s — canceling to avoid stale fill"
            )
            try:
                fyers.cancel_order({"id": order_id})
            except Exception as _ce:
                logger.error(f"Cancel failed for {order_id}: {_ce}")
            return None

        trade_rec['order_id'] = order_id

        # Place broker-side SL-M (GTT crash protection) immediately after fill.
        # Fires at Fyers even if Python crashes before _sl_monitor_loop can exit.
        _gtt_id = _place_gtt_sl(
            fyers, symbol, qty, trade_rec['current_sl'],
            product_type, instrument='OPTION',
        )
        trade_rec['gtt_sl_order_id'] = _gtt_id or ''

        paper_trade = None
        if _PAPER_OK:
            paper_trade = open_paper_trade(sb_setup)
            if paper_trade and paper_trade.get('id'):
                trade_rec['id'] = paper_trade['id']
                try:
                    from utils.trade_replay import capture_entry_context as _cap_ctx_live
                    _cap_ctx_live(
                        trade_id  = paper_trade['id'],
                        market    = 'NSE',
                        symbol    = _conv_symbol,
                        direction = direction,
                        setup     = setup,
                        session   = _nse_session,
                    )
                except Exception as _cap_le:
                    logger.debug(f"NSE live trade replay capture skipped: {_cap_le}")
            else:
                logger.critical(
                    f"LIVE STATE WRITE FAILED after fill: {symbol} order_id={order_id}. "
                    "SL monitor is in-memory only; restart recovery is degraded."
                )
                try:
                    from utils.telegram_alerts import send_message as _tg
                    _tg(
                        f"<b>CRITICAL: LIVE STATE WRITE FAILED</b>\n"
                        f"Symbol: {symbol}\n"
                        f"Order ID: {order_id}\n"
                        f"Bot will monitor in memory, but restart recovery is degraded."
                    )
                except Exception:
                    pass
            register_option_strike(symbol)

        # ── Trade verifier: record live fill ──────────────────────────────────
        # The fill price for a live order may differ from planned_entry (bid).
        # We look up the actual fill from Fyers orderbook after TTL confirmation.
        try:
            from utils.trade_verifier import get_verifier
            # Attempt to get the actual fill price from the order
            _fill_px = None
            try:
                _ob_resp = fyers.orderbook()
                if _ob_resp and _ob_resp.get('code') == 200:
                    for _ord in _ob_resp.get('orderBook', []):
                        if str(_ord.get('id', '')) == str(order_id):
                            _fill_px = float(_ord.get('tradedPrice', 0) or _ord.get('avgPrice', 0) or 0) or None
                            break
            except Exception:
                pass
            # Also tag lot_size_source on the setup so record_entry (called from
            # open_paper_trade above) can read it
            _ls_src = sb_setup.get('_lot_size_source', 'fallback')
            if _fill_px:
                # Update the fill price recorded at open_paper_trade
                _tid = trade_rec.get('id', '')
                if _tid:
                    get_verifier().record_fill(
                        trade_id    = _tid,
                        fill_price  = _fill_px,
                        order_price = mpp_limit,
                        bid         = bid_price,
                        ask         = liq_quote.get('ask') if liq_quote else None,
                    )
        except Exception:
            pass

        # Live entry Telegram alert
        try:
            from utils.telegram_alerts import send_message as _tg
            risk_pts  = ltp - trade_rec['current_sl']
            risk_rs   = round(risk_pts * qty, 0)
            rr_val    = round((trade_rec['target3'] - ltp) / max(abs(risk_pts), 0.01), 2)
            _tg(
                f"<b>🟢 LIVE ENTRY — {symbol}</b>\n\n"
                f"Direction  : <b>{direction}</b>\n"
                f"Setup      : ICT Silver Bullet (OPTION)\n"
                f"Entry      : {ltp}\n"
                f"Stop Loss  : {trade_rec['current_sl']}\n"
                f"Target 1   : {trade_rec['target1']}\n"
                f"Target 2   : {trade_rec['target2']}\n"
                f"Target 3   : {trade_rec['target3']}\n"
                f"Risk       : Rs {risk_rs:.0f}\n"
                f"RR (T3)    : 1:{rr_val}\n"
                f"Confidence : {setup.get('confluence', 0)}/10\n"
                f"Qty        : {qty} (lots={lots})\n"
                f"Order ID   : {order_id}"
            )
        except Exception as _ae:
            logger.debug(f"Options entry alert error: {_ae}")

        _start_sl_monitor(fyers, order_id, trade_rec)
        return trade_rec

    except Exception as e:
        logger.error(f"place_silver_bullet_trade error: {e}")
        return None


# ── Broker-side GTT Stop-Loss helpers ────────────────────────────────────────

def _place_gtt_sl(fyers, symbol: str, qty: int, sl_price: float,
                  product_type: str, direction: str = 'BULLISH',
                  instrument: str = 'OPTION') -> Optional[str]:
    """
    Place a Stop-Loss Market (SL-M) order at Fyers after a fill.
    Acts as broker-side crash protection — fires even if Python is dead.
    Returns the Fyers order_id string or None on failure.
    Fail-open: a GTT placement failure does NOT block the trade.
    """
    try:
        is_short_futures = (instrument == 'FUTURES' and direction == 'BEARISH')
        close_side = SIDE_BUY if is_short_futures else SIDE_SELL
        data = {
            "symbol"      : symbol,
            "qty"         : qty,
            "type"        : 4,   # SL-M (Stop Loss Market)
            "side"        : close_side,
            "productType" : product_type,
            "limitPrice"  : 0,
            "stopPrice"   : round(sl_price, 2),
            "validity"    : "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "orderTag"    : "CB6GTTSL",
        }
        resp = fyers.place_order(data)
        if resp and resp.get('code') == 200:
            gtt_id = str(resp.get('id') or resp.get('data', {}).get('id', ''))
            if gtt_id:
                logger.info(f"GTT SL placed: {symbol} sl={sl_price} qty={qty} id={gtt_id}")
                return gtt_id
        logger.warning(f"GTT SL placement failed: {symbol} sl={sl_price} resp={resp}")
        return None
    except Exception as e:
        logger.warning(f"GTT SL placement error {symbol}: {e}")
        return None


def _cancel_gtt_sl(fyers, gtt_order_id: str, symbol: str = '') -> bool:
    """
    Cancel a pending GTT SL order. Best-effort — never raises.
    Returns True if cancel succeeded or order was already empty.
    """
    if not gtt_order_id:
        return True
    try:
        resp = fyers.cancel_order({"id": gtt_order_id})
        if resp and resp.get('code') == 200:
            logger.info(f"GTT SL cancelled: {gtt_order_id} ({symbol})")
            return True
        logger.warning(f"GTT SL cancel response: {gtt_order_id} resp={resp}")
        return False
    except Exception as e:
        logger.warning(f"GTT SL cancel error {gtt_order_id} ({symbol}): {e}")
        return False


# ── Fill status tracker ───────────────────────────────────────────────────────

def _wait_for_fill(fyers, order_id: str, timeout: int = ORDER_FILL_TTL) -> bool:
    """
    Poll Fyers order status until COMPLETE or timeout.
    P&L is only registered after a confirmed fill — never on assumed execution.
    Returns True if order reached COMPLETE, False if timed out or rejected.

    Handles HTTP 429 (rate limit) with 3s back-off.
    Handles network disconnects gracefully — retries until TTL expires.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = fyers.orderbook()
            code = resp.get('code', 0) if resp else 0

            # 429 rate limit during polling — back off briefly, don't abort
            if code == 429:
                logger.warning(f"[FILL POLL] HTTP 429 rate limit — backing off 3s (order {order_id})")
                time.sleep(3)
                continue

            if code == 200:
                for order in resp.get('orderBook', []):
                    if str(order.get('id', '')) == str(order_id):
                        status = (order.get('status') or '').upper()
                        if status in ('COMPLETE', 'FILLED', '2'):
                            logger.info(f"Order {order_id} FILLED — proceeding with trade")
                            return True
                        if status in ('CANCELLED', 'REJECTED', '5', '6'):
                            logger.warning(f"Order {order_id} {status} — aborting")
                            return False
        except Exception as _pe:
            logger.debug(f"Fill poll error {order_id}: {_pe}")
        time.sleep(1)
    return False   # timeout


# ── SL monitor thread ─────────────────────────────────────────────────────────

def _start_sl_monitor(fyers, order_id: str, trade: Dict):
    """Spawn a daemon thread to watch the trade and manage exits."""
    t = threading.Thread(
        target=_sl_monitor_loop,
        args=(fyers, order_id, trade),
        daemon=True,
        name=f"SLMon-{order_id[:8]}"
    )
    with _monitor_lock:
        _active_trades[order_id] = trade
    t.start()


_SWEEP_CHECK_EVERY  = 12   # every 60s at 5s poll interval (12 × 5s)
_IV_CHECK_EVERY     = 24   # every 120s (24 × 5s)


def _check_post_entry_sweep(fyers, underlying: str, direction: str,
                             entry: float, current_sl: float, current_ltp: float,
                             t1_done: bool) -> tuple:
    """
    Detect a fresh liquidity sweep AGAINST the open position.

    Returns (action, new_sl, reason) where action is one of:
      'NONE'          — no opposite sweep, continue holding
      'TIGHTEN_SL'    — opposite sweep present, halve remaining risk
      'EXIT_PARTIAL'  — after T1, move SL to BE or exit runner
      'EXIT_FULL'     — sweep + MSS reversal against trade = full exit

    Never raises — returns ('NONE', current_sl, '') on any error.
    """
    try:
        from scanner.data_fetcher import get_historical_data
        from scanner.silver_bullet import detect_liquidity_sweep, detect_sb_mss

        df = get_historical_data(fyers, underlying, '3', days=1)
        if df is None or len(df) < 20:
            return 'NONE', current_sl, ''

        sweep = detect_liquidity_sweep(df, lookback=40, sweep_window=10)
        if sweep is None:
            return 'NONE', current_sl, ''

        # Only act if sweep is fresh (within last 5 candles = 15 min) and OPPOSITE direction
        is_opposite = (sweep['direction'] != direction)
        is_fresh    = (sweep.get('candles_ago', 99) <= 5)
        if not (is_opposite and is_fresh):
            return 'NONE', current_sl, ''

        # Opposite sweep confirmed — determine severity
        # Case C: Also has a confirming MSS against the trade?
        mss = detect_sb_mss(df)
        has_reversal_mss = (mss is not None and mss['direction'] != direction and
                            mss.get('candles_ago', 99) <= 8)

        if has_reversal_mss:
            reason = (f"OPPOSITE_SWEEP+MSS: {sweep['sweep_type']} + "
                      f"{mss['type']} against {direction}")
            logger.warning(f"Post-entry [FULL EXIT]: {reason}")
            return 'EXIT_FULL', current_sl, reason

        # Case B: Already hit T1, opposite sweep → exit runner
        if t1_done:
            reason = f"OPPOSITE_SWEEP after T1: {sweep['sweep_type']} against {direction}"
            logger.warning(f"Post-entry [EXIT_PARTIAL/BE]: {reason}")
            return 'EXIT_PARTIAL', entry, reason   # SL → BE effectively closes remaining

        # Case A: Pre-T1, option premium already negative > 8%?
        drawdown_pct = (entry - current_ltp) / entry if entry > 0 else 0
        if drawdown_pct > 0.08:
            reason = (f"OPPOSITE_SWEEP + drawdown {drawdown_pct:.0%} > 8%: "
                      f"exit to protect capital")
            logger.warning(f"Post-entry [EXIT_FULL drawdown]: {reason}")
            return 'EXIT_FULL', current_sl, reason

        # Case A: Pre-T1, no excessive drawdown → tighten SL by 50% of remaining risk
        remaining_risk = abs(current_ltp - current_sl)
        new_sl = round(current_ltp - remaining_risk * 0.5, 2)
        # Ensure tightened SL is strictly better than current
        if direction == 'BEARISH':
            new_sl = min(new_sl, current_sl)
        else:
            new_sl = max(new_sl, current_sl)
        reason = (f"OPPOSITE_SWEEP pre-T1: {sweep['sweep_type']} — "
                  f"tightening SL {current_sl:.1f} -> {new_sl:.1f}")
        logger.warning(f"Post-entry [TIGHTEN_SL]: {reason}")
        return 'TIGHTEN_SL', new_sl, reason

    except Exception as e:
        logger.debug(f"_check_post_entry_sweep error: {e}")
        return 'NONE', current_sl, ''


def _check_iv_crush(fyers, symbol: str, underlying: str, direction: str,
                    entry_ltp: float, entry_iv: float, entry_delta: float,
                    entry_underlying: float,
                    t1_underlying: float, current_sl: float) -> tuple:
    """
    Detect IV crush: underlying moved toward target but option premium lagged.

    Returns (action, new_sl, reason):
      'NONE'        — no IV crush or underlying not far enough
      'TIGHTEN_SL'  — IV crush suspected, tighten to protect profit
      'EXIT_EARLY'  — IV crush + theta combined killing the trade

    Triggers when:
      - Underlying moved >= 40% toward T1
      - Option premium moved < 50% of what delta predicts
      - OR IV dropped >= 8 percentage points from entry

    Never raises — returns ('NONE', current_sl, '') on any error.
    """
    try:
        if entry_iv <= 0 or entry_delta <= 0 or entry_underlying <= 0:
            return 'NONE', current_sl, ''

        # Get current option LTP and underlying LTP
        opt_ltp = _get_ltp(fyers, symbol)
        if opt_ltp is None or opt_ltp <= 0:
            return 'NONE', current_sl, ''

        und_ltp = _get_ltp(fyers, underlying) if underlying and underlying != symbol else None

        # Option premium move
        opt_move = opt_ltp - entry_ltp   # positive = option gained value

        # Underlying progress toward T1 (0=at entry, 1=at T1)
        t1_distance = abs(t1_underlying - entry_underlying)
        if t1_distance <= 0:
            return 'NONE', current_sl, ''

        # Fetch current IV via option quote
        current_iv = entry_iv  # default — update if quote available
        try:
            q = _get_option_quote(fyers, symbol)
            if q and q.get('iv'):
                current_iv = float(q['iv'])
        except Exception:
            pass

        iv_drop = entry_iv - current_iv   # positive = IV fell (crush)

        # If underlying data available, compute progress
        if und_ltp is not None:
            if direction == 'BULLISH':
                und_progress = (und_ltp - entry_underlying) / t1_distance
            else:
                und_progress = (entry_underlying - und_ltp) / t1_distance
            und_progress = max(0.0, min(1.0, und_progress))
        else:
            und_progress = 0.0

        # Expected option move via delta (theoretical)
        expected_opt_move = entry_delta * (und_ltp - entry_underlying) if und_ltp else 0
        if direction == 'BEARISH':
            expected_opt_move = entry_delta * (entry_underlying - und_ltp) if und_ltp else 0

        # Underperformance ratio (actual / expected)
        underperf_ratio = (opt_move / expected_opt_move) if expected_opt_move > 0.5 else 1.0

        # IV crush condition: underlying >= 40% to T1 AND option underperforming
        iv_crush_detected = (
            und_progress >= 0.40 and
            (underperf_ratio < 0.50 or iv_drop >= 0.08)
        )

        if not iv_crush_detected:
            return 'NONE', current_sl, ''

        reason = (
            f"IV_CRUSH: und_progress={und_progress:.0%} "
            f"opt_underperf={underperf_ratio:.2f} iv_drop={iv_drop:.1%}"
        )

        # If option is still profitable (opt_move > 0), just tighten SL
        if opt_move > 0:
            # Move SL up to lock in at least 30% of current gains
            new_sl = round(entry_ltp + opt_move * 0.30, 2)
            new_sl = max(new_sl, current_sl)   # never loosen
            return 'TIGHTEN_SL', new_sl, reason

        # Option losing while underlying winning = severe crush → exit early
        return 'EXIT_EARLY', current_sl, reason

    except Exception as e:
        logger.debug(f"_check_iv_crush error: {e}")
        return 'NONE', current_sl, ''


def _sl_monitor_loop(fyers, order_id: str, trade: Dict):
    """
    Monitor a live trade:
    - T1 hit: exit 50%, move SL to break-even
    - T2 hit: exit remaining
    - SL hit: exit all
    - Theta watchdog: if stuck in FVG > 20 min, exit all
    - Post-entry opposite sweep: tighten SL or exit
    - IV crush: tighten SL or exit early

    Direction-aware: BEARISH futures shorts have SL above entry and targets below.
    Options are always long premium regardless of underlying direction — standard checks apply.
    """
    symbol     = trade['symbol']
    entry      = trade['entry_price']
    target1    = trade['target1']
    target2    = trade['target2']
    sl         = trade['current_sl']
    qty_left   = trade['quantity']
    lot_size   = trade.get('lot_size') or 1   # default 1 if None/missing (restart recovery)
    direction    = trade.get('direction', 'BULLISH')
    instrument   = trade.get('instrument_type', 'OPTION')
    product_type = trade.get('product_type', PRODUCT_INTRADAY)
    underlying   = trade.get('underlying', symbol)   # index futures symbol for sweep re-scan

    # Vega/IV crush tracking fields (captured at entry in trade_rec)
    entry_iv         = float(trade.get('entry_iv', 0) or 0)
    entry_vega       = float(trade.get('entry_vega', 0) or 0)
    entry_delta      = float(trade.get('entry_delta', trade.get('delta', 0.5)) or 0.5)
    entry_underlying = float(trade.get('entry_underlying', 0) or 0)
    # T1 in underlying terms (for IV crush progress calculation)
    t1_underlying    = float(trade.get('entry_signal', {}).get('target1', 0) or target1)

    # SHORT futures: SL is above entry, targets are below. All comparisons invert.
    # OPTIONS: always long the premium — standard ltp<=sl / ltp>=target applies.
    is_short_futures = (instrument == 'FUTURES' and direction == 'BEARISH')
    t1_done       = False
    entry_ts      = datetime.fromisoformat(
        trade.get('fvg_entry_time') or trade.get('entry_time', datetime.now().isoformat())
    )
    journal_id      = trade.get('journal_id')
    gtt_sl_order_id = trade.get('gtt_sl_order_id', '')  # broker-side crash SL
    exit_reason   = 'UNKNOWN'
    exit_ltp      = entry
    _poll_counter = 0    # drives sweep + IV check intervals

    logger.info(f"SL monitor started: {symbol} {direction} {instrument}")

    while qty_left > 0:
        time.sleep(SL_MONITOR_SEC)
        _poll_counter += 1

        mins_in_fvg = (datetime.now() - entry_ts).total_seconds() / 60

        # Theta watchdog (20-minute rule — options only; futures hold until target/SL/EOD)
        if instrument == 'OPTION' and trade.get('in_fvg') and mins_in_fvg > THETA_EXIT_MINS:
            logger.info(f"Theta burn: {symbol} in FVG {mins_in_fvg:.0f}min — exiting")
            _cancel_gtt_sl(fyers, gtt_sl_order_id, symbol); gtt_sl_order_id = ''
            _exit_position(fyers, symbol, qty_left, lot_size, "THETA_BURN", direction, instrument, product_type)
            exit_reason = 'THETA_BURN'
            exit_ltp    = _get_ltp(fyers, symbol) or entry
            break

        # EOD force-exit at 15:10 IST (futures only — options handled by OptionsOrderManager)
        if instrument == 'FUTURES':
            try:
                import pytz as _pytz_om
                from datetime import datetime as _dt_om
                _ist_now = _dt_om.now(_pytz_om.timezone('Asia/Kolkata'))
                if (_ist_now.hour, _ist_now.minute) >= (15, 10):
                    logger.info(f"EOD force-exit (15:10 IST): {symbol} — squaring off {qty_left} qty")
                    _cancel_gtt_sl(fyers, gtt_sl_order_id, symbol); gtt_sl_order_id = ''
                    _exit_position(fyers, symbol, qty_left, lot_size, "FORCE_EXIT_EOD", direction, instrument, product_type)
                    exit_reason = 'FORCE_EXIT_EOD'
                    exit_ltp    = _get_ltp(fyers, symbol) or entry
                    break
            except Exception as _eod_err:
                logger.warning(f"EOD force-exit check failed: {_eod_err}")

        ltp = _get_ltp(fyers, symbol)
        if ltp is None:
            continue
        exit_ltp = ltp

        sl_hit = (ltp >= sl) if is_short_futures else (ltp <= sl)
        if sl_hit:
            logger.info(f"SL hit: {symbol} ltp={ltp} sl={sl}")
            _cancel_gtt_sl(fyers, gtt_sl_order_id, symbol); gtt_sl_order_id = ''
            _exit_position(fyers, symbol, qty_left, lot_size, "STOP_LOSS", direction, instrument, product_type)
            exit_reason = 'STOP_LOSS'
            break

        t1_hit = (ltp <= target1) if is_short_futures else (ltp >= target1)
        if not t1_done and t1_hit:
            half = max(lot_size, (qty_left // 2 // lot_size) * lot_size)
            logger.info(f"T1 hit: {symbol} ltp={ltp} — exiting {half} qty, SL→BE")
            # Cancel GTT SL now — bot is actively managing, GTT for full qty is no longer valid
            _cancel_gtt_sl(fyers, gtt_sl_order_id, symbol); gtt_sl_order_id = ''
            _exit_position(fyers, symbol, half, lot_size, "TARGET1", direction, instrument, product_type)
            qty_left -= half
            sl = entry   # move SL to break-even
            t1_done = True
            exit_reason = 'TARGET1'

        t2_hit = (ltp <= target2) if is_short_futures else (ltp >= target2)
        if t1_done and t2_hit:
            logger.info(f"T2 hit: {symbol} ltp={ltp} — full exit {qty_left} qty")
            _cancel_gtt_sl(fyers, gtt_sl_order_id, symbol); gtt_sl_order_id = ''
            _exit_position(fyers, symbol, qty_left, lot_size, "TARGET2", direction, instrument, product_type)
            exit_reason = 'TARGET2'
            break

        # ── POST-ENTRY OPPOSITE SWEEP CHECK (every 60s) ───────────────────────
        if _poll_counter % _SWEEP_CHECK_EVERY == 0:
            s_action, new_sl, s_reason = _check_post_entry_sweep(
                fyers, underlying, direction, entry, sl, ltp, t1_done
            )
            if s_action == 'EXIT_FULL':
                logger.warning(f"Post-entry sweep EXIT_FULL: {symbol} | {s_reason}")
                _cancel_gtt_sl(fyers, gtt_sl_order_id, symbol); gtt_sl_order_id = ''
                _exit_position(fyers, symbol, qty_left, lot_size, "OPPOSITE_SWEEP", direction, instrument, product_type)
                exit_reason = 'OPPOSITE_SWEEP_FULL'
                exit_ltp    = _get_ltp(fyers, symbol) or ltp
                try:
                    from utils.telegram_alerts import send_message as _tg
                    _tg(
                        f"<b>POST-ENTRY OPPOSITE SWEEP — FULL EXIT</b>\n\n"
                        f"Symbol : {symbol}\n"
                        f"Reason : {s_reason}\n"
                        f"LTP    : {exit_ltp}"
                    )
                except Exception:
                    pass
                break
            elif s_action in ('EXIT_PARTIAL', 'TIGHTEN_SL'):
                if s_action == 'EXIT_PARTIAL' and t1_done and qty_left > 0:
                    _cancel_gtt_sl(fyers, gtt_sl_order_id, symbol); gtt_sl_order_id = ''
                    _exit_position(fyers, symbol, qty_left, lot_size, "OPPOSITE_SWEEP_RUNNER", direction, instrument, product_type)
                    exit_reason = 'OPPOSITE_SWEEP_RUNNER'
                    exit_ltp    = _get_ltp(fyers, symbol) or ltp
                    try:
                        from utils.telegram_alerts import send_message as _tg
                        _tg(
                            f"<b>POST-ENTRY OPPOSITE SWEEP — RUNNER EXITED</b>\n\n"
                            f"Symbol : {symbol}\n"
                            f"Reason : {s_reason}"
                        )
                    except Exception:
                        pass
                    break
                if new_sl != sl:
                    old_sl = sl
                    sl = new_sl
                    logger.warning(
                        f"Post-entry sweep TIGHTEN_SL: {symbol} "
                        f"{old_sl:.1f} -> {sl:.1f} | {s_reason}"
                    )
                    try:
                        from utils.telegram_alerts import send_message as _tg
                        _tg(
                            f"<b>POST-ENTRY OPPOSITE SWEEP — SL TIGHTENED</b>\n\n"
                            f"Symbol : {symbol}\n"
                            f"Old SL : {old_sl:.1f}\n"
                            f"New SL : {sl:.1f}\n"
                            f"Reason : {s_reason}"
                        )
                    except Exception:
                        pass

        # ── IV CRUSH CHECK (every 120s, options only) ─────────────────────────
        if instrument == 'OPTION' and _poll_counter % _IV_CHECK_EVERY == 0:
            iv_action, new_sl_iv, iv_reason = _check_iv_crush(
                fyers, symbol, underlying, direction,
                entry, entry_iv, entry_delta, entry_underlying,
                t1_underlying, sl
            )
            if iv_action == 'EXIT_EARLY':
                logger.warning(f"IV crush EXIT_EARLY: {symbol} | {iv_reason}")
                _cancel_gtt_sl(fyers, gtt_sl_order_id, symbol); gtt_sl_order_id = ''
                _exit_position(fyers, symbol, qty_left, lot_size, "IV_CRUSH", direction, instrument, product_type)
                exit_reason = 'IV_CRUSH'
                exit_ltp    = _get_ltp(fyers, symbol) or ltp
                try:
                    from utils.telegram_alerts import send_message as _tg
                    _tg(
                        f"<b>IV CRUSH WARNING — EARLY EXIT</b>\n\n"
                        f"Symbol : {symbol}\n"
                        f"Reason : {iv_reason}\n"
                        f"LTP    : {exit_ltp}"
                    )
                except Exception:
                    pass
                break
            elif iv_action == 'TIGHTEN_SL' and new_sl_iv != sl:
                old_sl = sl
                sl = new_sl_iv
                logger.warning(
                    f"IV crush TIGHTEN_SL: {symbol} {old_sl:.2f} -> {sl:.2f} | {iv_reason}"
                )
                try:
                    from utils.telegram_alerts import send_message as _tg
                    _tg(
                        f"<b>IV CRUSH WARNING — SL TIGHTENED</b>\n\n"
                        f"Symbol : {symbol}\n"
                        f"Old SL : {old_sl:.2f}\n"
                        f"New SL : {sl:.2f}\n"
                        f"Reason : {iv_reason}"
                    )
                except Exception:
                    pass

    # Journal exit + Telegram exit alert
    pnl_sign   = -1 if is_short_futures else 1
    hold_mins  = (datetime.now() - entry_ts).total_seconds() / 60
    realized   = pnl_sign * (exit_ltp - entry) * trade.get('original_quantity', trade.get('quantity', 1))
    r_multiple = round(realized / max(abs(entry - trade.get('current_sl', entry)), 0.01) /
                       max(trade.get('lot_size', 1), 1), 2)

    if journal_id:
        try:
            log_exit(journal_id, exit_ltp, exit_reason, realized, hold_mins)
        except Exception as je:
            logger.debug(f"Journal exit error: {je}")

    # Live exit Telegram alert
    try:
        from utils.telegram_alerts import send_message as _tg
        pnl_emoji = "✅" if realized >= 0 else "❌"
        _tg(
            f"<b>{pnl_emoji} TRADE EXIT — {symbol}</b>\n\n"
            f"Exit Price : {exit_ltp}\n"
            f"Exit Reason: {exit_reason}\n"
            f"PnL        : <b>Rs {realized:+.0f}</b>\n"
            f"R Multiple : {r_multiple:+.2f}R\n"
            f"Hold Time  : {hold_mins:.0f} min\n"
            f"Direction  : {direction}"
        )
    except Exception as _ae:
        logger.debug(f"Exit alert error: {_ae}")

    # ML Memory: update category/FVG/index stats with this trade outcome
    try:
        from ml.trade_memory import update_from_trade
        update_from_trade(
            mss_type   = trade.get('mss_type', trade.get('mss', {}).get('type', 'BOS') if isinstance(trade.get('mss'), dict) else 'BOS'),
            direction  = direction,
            fvg_size   = (trade.get('fvg', {}) or {}).get('size', 0) if isinstance(trade.get('fvg'), dict) else abs(entry - trade.get('current_sl', entry)),
            score      = float(trade.get('confluence', trade.get('score', 0)) or 0),
            sweep_q    = float(trade.get('sweep_quality', 0) or 0),
            outcome    = exit_reason,
            r_multiple = r_multiple,
            pnl_rs     = realized,
            index_name = str(trade.get('underlying', symbol)).replace('NSE:', '').split('-')[0].split('2')[0],
            hold_mins  = hold_mins,
            entry_time = entry_ts.strftime('%H:%M'),
        )
    except Exception as _ml_upd_e:
        logger.debug(f"ML memory update (non-blocking): {_ml_upd_e}")

    with _monitor_lock:
        _active_trades.pop(order_id, None)
    try:
        outcome = 'WIN' if realized > 0 else ('BREAKEVEN' if realized == 0 else 'LOSS')
        log_closed_trade(
            'nse',
            'nse_live_order_manager',
            trade,
            result=outcome,
            rr_achieved=r_multiple,
            metadata={
                'exit_reason': exit_reason,
                'mode': 'live',
                'symbol': symbol,
            },
        )
        archive_closed_trade_shadow(
            'nse',
            'nse_live_order_manager',
            trade,
            result=outcome,
            rr_achieved=r_multiple,
            metadata={
                'exit_reason': exit_reason,
                'mode': 'live',
                'symbol': symbol,
            },
        )
    except Exception:
        pass

    # ── Persist to cb6_trades.db + pattern DB ────────────────────────────────
    try:
        from data.persistence.trade_persistence import write_nse_trade
        write_nse_trade(
            trade,
            setup=trade.get('_setup_ctx'),
            exit_context={
                'exit_reason' : exit_reason,
                'exit_price'  : exit_ltp,
                'pnl'         : realized,
                'r_multiple'  : r_multiple,
                'hold_mins'   : int(hold_mins),
            },
        )
    except Exception:
        pass

    logger.info(f"SL monitor done: {symbol} | reason={exit_reason} | pnl=Rs {realized:+.0f}")


def _exit_position(fyers, symbol: str, qty: int, lot_size: int, reason: str,
                   direction: str = 'BULLISH', instrument: str = 'OPTION',
                   product_type: str = None):
    """
    Place market order to close `qty` contracts.
    Long positions (options + bullish futures) → SELL to close.
    Short positions (bearish futures) → BUY to close.
    product_type: pass the same product used at entry (MIS/NRML).
                  If None, defaults to MIS for futures, MIS for options (safe intraday default).
    """
    try:
        if qty <= 0:
            return
        is_short_futures = (instrument == 'FUTURES' and direction == 'BEARISH')
        close_side = SIDE_BUY if is_short_futures else SIDE_SELL
        # Always match the entry product type. Default to MIS (intraday) for both
        # futures and options — never MARGIN (NRML) for an intraday entry, which would
        # open a new short position rather than closing the existing long.
        product = product_type if product_type else PRODUCT_INTRADAY
        exit_tag = ''.join(ch for ch in f"CB6EXIT{reason}" if ch.isalnum())[:20] or "CB6EXIT"
        order_data = {
            "symbol"      : symbol,
            "qty"         : qty,
            "type"        : ORDER_TYPE_MKT,
            "side"        : close_side,
            "productType" : product,
            "limitPrice"  : 0,
            "stopPrice"   : 0,
            "validity"    : "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "orderTag"    : exit_tag,
        }
        # Exit orders always proceed — guard is for entries only.
        # execute_guarded_order provides audit trail without blocking.
        from core.execution_guard import execute_guarded_order
        resp = execute_guarded_order(
            fyers.place_order, order_data,
            symbol=symbol, intent=f"CLOSE_{reason}",
        )
        logger.info(f"Exit order ({reason}): {symbol} qty={qty} side={close_side} resp={resp}")
    except Exception as e:
        logger.error(f"Exit order error: {e}")


def _get_ltp(fyers, symbol: str) -> Optional[float]:
    try:
        resp = fyers.quotes({"symbols": symbol})
        if resp and resp.get('code') == 200:
            d = resp.get('d', [{}])
            v = d[0].get('v', {}) if d else {}
            ltp = float(v.get('lp') or v.get('ltp') or 0)
            return ltp if ltp > 0 else None
    except Exception:
        pass
    return None


# ── Futures trade placement ───────────────────────────────────────────────────

def place_futures_trade(fyers, setup: Dict, paper_mode: bool = True) -> Optional[Dict]:
    """
    Place a Silver Bullet trade directly on the index futures contract.

    `setup`     : dict from scan_silver_bullet()
    `paper_mode`: True = paper record only; False = live Fyers order

    Returns trade record dict or None.
    """
    try:
        from scanner.index_futures import get_lot_size, _base_ticker

        sig       = setup['entry_signal']
        direction = setup['direction']
        symbol    = setup['symbol']          # e.g. NSE:NIFTY26MAYFUT

        entry     = float(sig['entry'])
        stop_loss = float(sig['stop_loss'])
        target1   = float(sig['target1'])
        target2   = float(sig['target2'])
        target3   = float(sig['target3'])

        # Derive lot size from futures symbol
        lot_size  = get_lot_size(symbol)
        if lot_size <= 1:
            lot_size = 75   # default fallback

        # Position sizing — risk-based (10% of equity)
        state        = load_state() if _PAPER_OK else {}
        base_capital = state.get('capital', 100_000)
        total_pnl    = state.get('total_pnl', 0)
        total_equity = base_capital + total_pnl
        forced_lots = int(setup.get('ml_lots') or setup.get('routing_lots') or 0)
        if forced_lots > 0:
            lots = forced_lots
            ml_boost = 1.0
        else:
            lots = calc_position_size(total_equity, MAX_RISK_PCT, entry, stop_loss, lot_size)
            ml_boost = setup.get('ml_lot_boost', 1.0)
            if ml_boost > 1.0:
                lots = max(1, int(lots * ml_boost))

        risk_per_unit = abs(entry - stop_loss)
        lots, cap_reason = _cap_lots(lots, lot_size, risk_per_unit)
        if lots <= 0:
            logger.warning(f"Futures live risk BLOCKED {symbol}: {cap_reason}")
            return None
        if cap_reason != "OK":
            logger.info(f"Futures live risk clamp {symbol}: {cap_reason}")
        qty          = lots * lot_size

        side = SIDE_BUY if direction == 'BULLISH' else SIDE_SELL

        logger.info(
            f"Futures position size: equity={total_equity:.0f}  "
            f"lots={lots}  qty={qty}  symbol={symbol}"
            + (f"  [ML boost {ml_boost}×]" if ml_boost > 1.0 else "")
        )

        # Risk / reward metrics
        reward_t3     = abs(target3 - entry)
        rr            = round(reward_t3 / risk_per_unit, 2) if risk_per_unit > 0 else 3.0

        trade_rec = {
            'symbol'            : symbol,
            'direction'         : direction,
            'instrument_type'   : 'FUTURES',
            'setup_type'        : 'SILVER_BULLET',
            'timeframe'         : setup.get('timeframe', '3'),
            'entry_price'       : entry,
            'current_sl'        : stop_loss,
            'target1'           : target1,
            'target2'           : target2,
            'target3'           : target3,
            'quantity'          : qty,
            'original_quantity' : qty,
            'lot_size'          : lot_size,
            'confluence'        : setup.get('confluence', 0),
            'window'            : setup.get('window'),
            'in_fvg'            : setup.get('in_fvg', False),
            'fvg_entry_time'    : datetime.now().isoformat(),
            'targets_hit'       : [],
            'realized_pnl'      : 0.0,
            'pnl'               : 0.0,
            'product_type'      : PRODUCT_INTRADAY,   # MIS — square off by EOD
            'regime'            : setup.get('regime', 'NEUTRAL'),
        }

        # Build setup dict for open_paper_trade()
        sb_setup = {
            'symbol'           : symbol,
            'direction'        : direction,
            'timeframe'        : setup.get('timeframe', '3'),
            'instrument_type'  : 'FUTURES',
            'confluence'       : setup.get('confluence', 0),
            'window'           : setup.get('window'),
            'in_fvg'           : setup.get('in_fvg', False),
            'product_type'     : PRODUCT_INTRADAY,
            'quantity'         : qty,
            'original_quantity': qty,
            'lot_size'         : lot_size,
            'regime'           : setup.get('regime', 'NEUTRAL'),
            '_candles_df'      : setup.get('_candles_df'),
            'entry_signal': {
                'entry'    : entry,
                'stop_loss': stop_loss,
                'target1'  : target1,
                'target2'  : target2,
                'target3'  : target3,
                'risk'     : risk_per_unit,
                'rr_ratio' : rr,
                'in_fvg'   : setup.get('in_fvg', False),
            },
        }

        # ── Conviction evaluation (Phase 7) ──────────────────────────────────
        _fut_session = "off_window"
        try:
            from datetime import datetime as _dt_fut
            import pytz as _pytz_fut
            _ist_fut = _dt_fut.now(_pytz_fut.timezone('Asia/Kolkata'))
            _fih, _fim = _ist_fut.hour, _ist_fut.minute
            if 10 <= _fih < 11:
                _fut_session = "nse_am"
            elif 13 <= _fih < 14:
                _fut_session = "nse_pm"
            elif _fih == 15 and _fim < 30:
                _fut_session = "nse_close"
        except Exception:
            pass

        _fut_sym_map = {
            'NIFTY': 'NSE:NIFTY50-INDEX', 'NIFTY50': 'NSE:NIFTY50-INDEX',
            'BANKNIFTY': 'NSE:NIFTYBANK-INDEX',
            'FINNIFTY': 'NSE:FINNIFTY-INDEX',
            'MIDCPNIFTY': 'NSE:MIDCPNIFTY-INDEX',
        }
        _fut_base = _base_ticker(symbol) if 'NSE:' in symbol else symbol
        _fut_conv_sym = _fut_sym_map.get(_fut_base.upper(), f'NSE:{_fut_base}-INDEX')
        _fut_conviction = None
        try:
            from utils.conviction_engine import evaluate_conviction as _ev_fut
            _fut_conviction = _ev_fut(
                market    = 'NSE',
                symbol    = _fut_conv_sym,
                direction = direction,
                setup     = setup,
                session   = _fut_session,
            )
            logger.info(
                f"NSE Futures conviction={_fut_conviction.conviction_score:.0f} "
                f"grade={_fut_conviction.conviction_grade} ({_fut_conv_sym})"
            )
            if not _fut_conviction.should_trade():
                logger.info(
                    f"NSE FUTURES CONVICTION BLOCK — {symbol} {direction} "
                    f"grade={_fut_conviction.conviction_grade}"
                )
                return None
        except Exception as _conv_fe:
            logger.debug(f"NSE futures conviction eval skipped: {_conv_fe}")

        if paper_mode:
            if _PAPER_OK:
                _fut_pt = open_paper_trade(sb_setup)
                logger.info(
                    f"Futures paper trade opened: {symbol}  "
                    f"qty={qty}  entry={entry}  SL={stop_loss}  "
                    f"T1={target1}  T2={target2}  T3={target3}"
                )
                try:
                    from utils.trade_replay import capture_entry_context as _cap_fut
                    if _fut_pt and _fut_pt.get('id'):
                        _cap_fut(
                            trade_id  = _fut_pt['id'],
                            market    = 'NSE',
                            symbol    = _fut_conv_sym,
                            direction = direction,
                            setup     = setup,
                            session   = _fut_session,
                        )
                except Exception as _cap_fe:
                    logger.debug(f"NSE futures trade replay capture skipped: {_cap_fe}")
            return trade_rec

        # ── Live mode: ExecutionGuard check before any broker call ────────────
        # mode="LIVE" so guard internal errors fail closed (block trade).
        from core.execution_guard import guard_dict_entry
        state_for_guard = load_state() if _PAPER_OK else {}
        from settings import CAPITAL as _CAPITAL
        _guard_ok, _guard_reason = guard_dict_entry(
            state_for_guard, _CAPITAL, symbol, mode="LIVE", intent_type="ENTRY"
        )
        if not _guard_ok:
            logger.warning(
                f"place_futures_trade BLOCKED by ExecutionGuard: "
                f"{symbol} — {_guard_reason}"
            )
            return None

        # ── Live mode — Fyers limit order ─────────────────────────────────────
        order_id = _fyers_order(
            fyers, symbol, qty, side,
            ORDER_TYPE_LIMIT, limit_price=entry,
            product_type=PRODUCT_INTRADAY,
        )
        if not order_id:
            return None

        filled = _wait_for_fill(fyers, order_id, timeout=ORDER_FILL_TTL)
        if not filled:
            logger.warning(f"Futures order {order_id} unfilled after {ORDER_FILL_TTL}s — canceling")
            try:
                fyers.cancel_order({"id": order_id})
            except Exception as _ce:
                logger.error(f"Cancel failed for {order_id}: {_ce}")
            return None

        trade_rec['order_id'] = order_id

        # Broker-side SL-M crash protection for futures position.
        _gtt_id_fut = _place_gtt_sl(
            fyers, symbol, qty, stop_loss,
            PRODUCT_INTRADAY, direction=direction, instrument='FUTURES',
        )
        trade_rec['gtt_sl_order_id'] = _gtt_id_fut or ''

        paper_trade = None
        if _PAPER_OK:
            paper_trade = open_paper_trade(sb_setup)
            if paper_trade and paper_trade.get('id'):
                trade_rec['id'] = paper_trade['id']
                try:
                    from utils.trade_replay import capture_entry_context as _cap_fut_live
                    _cap_fut_live(
                        trade_id  = paper_trade['id'],
                        market    = 'NSE',
                        symbol    = _fut_conv_sym,
                        direction = direction,
                        setup     = setup,
                        session   = _fut_session,
                    )
                except Exception as _cap_fle:
                    logger.debug(f"NSE futures live trade replay capture skipped: {_cap_fle}")
            else:
                logger.critical(
                    f"LIVE STATE WRITE FAILED after fill: {symbol} order_id={order_id}. "
                    "SL monitor is in-memory only; restart recovery is degraded."
                )
                try:
                    from utils.telegram_alerts import send_message as _tg
                    _tg(
                        f"<b>CRITICAL: LIVE STATE WRITE FAILED</b>\n"
                        f"Symbol: {symbol}\n"
                        f"Order ID: {order_id}\n"
                        f"Bot will monitor in memory, but restart recovery is degraded."
                    )
                except Exception:
                    pass

        try:
            from utils.trade_verifier import get_verifier
            _fill_px = None
            try:
                _ob_resp = fyers.orderbook()
                if _ob_resp and _ob_resp.get('code') == 200:
                    for _ord in _ob_resp.get('orderBook', []):
                        if str(_ord.get('id', '')) == str(order_id):
                            _fill_px = float(_ord.get('tradedPrice', 0) or _ord.get('avgPrice', 0) or 0) or None
                            break
            except Exception:
                pass
            _tid = trade_rec.get('id', '')
            if _tid and _fill_px:
                get_verifier().record_fill(
                    trade_id    = _tid,
                    fill_price  = _fill_px,
                    order_price = entry,
                )
        except Exception:
            pass

        # Live entry Telegram alert
        try:
            from utils.telegram_alerts import send_message as _tg
            risk_rs = round(risk_per_unit * qty, 0)
            _tg(
                f"<b>🟢 LIVE ENTRY — {symbol}</b>\n\n"
                f"Direction  : <b>{direction}</b>\n"
                f"Setup      : ICT Silver Bullet (FUTURES)\n"
                f"Entry      : {entry}\n"
                f"Stop Loss  : {stop_loss}\n"
                f"Target 1   : {target1}\n"
                f"Target 2   : {target2}\n"
                f"Target 3   : {target3}\n"
                f"Risk       : Rs {risk_rs:.0f}\n"
                f"RR         : 1:{rr}\n"
                f"Confidence : {setup.get('confluence', 0)}/10\n"
                f"Qty        : {qty} (lots={lots})\n"
                f"Order ID   : {order_id}"
            )
        except Exception as _ae:
            logger.debug(f"Futures entry alert error: {_ae}")

        _start_sl_monitor(fyers, order_id, trade_rec)
        return trade_rec

    except Exception as e:
        logger.error(f"place_futures_trade error: {e}")
        return None


# ── status helpers ────────────────────────────────────────────────────────────

def active_trade_count() -> int:
    with _monitor_lock:
        return len(_active_trades)


def get_active_trades() -> Dict:
    with _monitor_lock:
        return dict(_active_trades)
