# crypto_engine/crypto_paper_trader.py
#
# Paper trade manager for BTC/USDT perpetual futures.
# Completely separate from NSE paper_state.json — no shared state, no interference.
#
# Capital is denominated in USDT.
# Quantity is in BTC (floored to 0.001 BTC step).
# PnL is in USDT.

import json
import os
import sys
import threading
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.logger import logger
from crypto_engine.trade_memory import record_trade_close
from ml_engine.memory.shadow_logger import log_closed_trade
from ml_engine.memory.replay_shadow import archive_closed_trade_shadow

STATE_FILE      = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               'data', 'crypto_paper_state.json')
DEFAULT_CAPITAL = float(os.getenv('CRYPTO_CAPITAL', '1000'))   # USDT
MAX_TRADES_DAY  = 3     # max 3 trades per day — prevent bleed in choppy markets
_lock           = threading.Lock()


# ── State I/O ─────────────────────────────────────────────────────────────────

def _default_state() -> dict:
    return {
        'starting_capital' : DEFAULT_CAPITAL,   # never changes — baseline for growth tracking
        'capital'          : DEFAULT_CAPITAL,   # live total equity (available + locked margin)
        'available_capital': DEFAULT_CAPITAL,
        'open_trades'      : [],
        'closed_trades'    : [],
        'daily_trades'     : 0,
        'daily_losses'     : 0,
        'daily_pnl'        : 0.0,
        'last_reset_date'  : datetime.now().strftime('%Y-%m-%d'),
        'paused'           : False,
    }


def _sync_equity(state: dict):
    """Keep state['capital'] = available_capital + sum of locked margins in open trades."""
    locked = sum(t.get('margin_est', 0) for t in state.get('open_trades', []))
    state['capital'] = round(state['available_capital'] + locked, 2)
    # Seed starting_capital on first run of upgraded state files
    if 'starting_capital' not in state:
        state['starting_capital'] = state['capital']


def load_state() -> dict:
    with _lock:
        # Try primary file, fall back to .bak if primary is empty/corrupt
        for path in (STATE_FILE, STATE_FILE + '.bak'):
            try:
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    with open(path) as f:
                        data = json.load(f)
                    if path != STATE_FILE:
                        logger.warning(f"Loaded state from backup {path}")
                    return data
            except Exception as e:
                logger.error(f"Crypto state load error ({path}): {e}")
        return _default_state()


def save_state(state: dict):
    with _lock:
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            tmp = STATE_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())    # REQ-2: flush OS write buffers to disk
            # Backup the last good state before replacing
            if os.path.exists(STATE_FILE) and os.path.getsize(STATE_FILE) > 0:
                os.replace(STATE_FILE, STATE_FILE + '.bak')
            os.replace(tmp, STATE_FILE)   # atomic rename — never leaves a partial file
        except Exception as e:
            logger.exception(f"Crypto state save error: {e}")  # REQ-5: never silent


def _reset_daily_if_new_day(state: dict) -> dict:
    today = datetime.now().strftime('%Y-%m-%d')
    if state.get('last_reset_date') != today:
        state['daily_trades']     = 0
        state['daily_losses']     = 0
        state['daily_pnl']        = 0.0
        state['last_reset_date']  = today
        logger.info("Crypto: daily counters reset")
    return state


# ── Trade gates ───────────────────────────────────────────────────────────────

def can_take_trade(state: dict, symbol: str = None):
    if state.get('paused'):
        return False, "Crypto engine paused"
    if state['available_capital'] < 1.0:
        return False, "Insufficient USDT capital (< $1)"
    if state.get('daily_trades', 0) >= MAX_TRADES_DAY:
        return False, f"Daily trade cap reached ({MAX_TRADES_DAY})"
    if state.get('daily_losses', 0) >= 2:
        return False, "Daily loss limit: 2 consecutive losses — rest today"
    # Block new trade if same symbol already has an open position
    if symbol:
        open_syms = [t['symbol'] for t in state.get('open_trades', [])]
        if symbol in open_syms:
            return False, f"Already have open trade for {symbol}"
    return True, "OK"


# ── Open trade ────────────────────────────────────────────────────────────────

def open_crypto_trade(setup: dict, qty_btc: float) -> Optional[dict]:
    """
    Open a paper crypto trade from a Silver Bullet setup.

    setup must contain 'entry_signal' with:
      entry, stop_loss, target1, target2, target3, risk, rr_ratio
    and 'direction': 'BULLISH' (long) or 'BEARISH' (short).

    qty_btc: BTC quantity (already floored to lot step).
    Returns trade dict or None.
    """
    try:
        state = load_state()
        state = _reset_daily_if_new_day(state)

        symbol_key = setup.get('symbol')
        ok, reason = can_take_trade(state, symbol=symbol_key)
        if not ok:
            logger.info(f"Crypto trade gate: {reason}")
            return None

        sig       = setup['entry_signal']
        direction = setup.get('direction', 'BULLISH')
        entry     = sig['entry']
        sl        = sig['stop_loss']
        risk_usdt = round(abs(entry - sl) * qty_btc, 2)

        # Margin estimate: 20x cross margin (matches Binance account setting)
        notional   = round(entry * qty_btc, 2)
        margin_est = round(notional * 0.05, 2)   # 1/20 of notional

        # Dedup: skip if already have an open trade in same direction
        for t in state['open_trades']:
            if t.get('direction') == direction:
                logger.info(f"Crypto: already open {direction} — skip duplicate")
                return None

        # Margin check: don't open if required margin > available capital
        if margin_est > state['available_capital']:
            logger.info(f"Crypto: margin ${margin_est} > available ${state['available_capital']:.2f} — skip")
            return None

        trade = {
            'id'           : f"CRYPTO_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'symbol'       : setup.get('symbol', 'BTCUSDT'),
            'direction'    : direction,
            'qty_btc'      : qty_btc,
            'entry_price'  : entry,
            'stop_loss'    : sl,
            'target1'      : sig['target1'],
            'target2'      : sig['target2'],
            'target3'      : sig['target3'],
            'risk_usdt'    : risk_usdt,
            'notional'     : notional,
            'margin_est'   : margin_est,
            'rr_ratio'     : sig.get('rr_ratio', 3.0),
            'confluence'   : setup.get('confluence', 0),
            'window'       : setup.get('window', ''),
            'mss_type'     : setup.get('mss_type', ''),
            'entry_time'   : datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'exit_time'    : None,
            'exit_price'   : None,
            'pnl_usdt'     : 0.0,
            'targets_hit'  : [],
            'realized_pnl' : 0.0,
            'current_sl'   : sl,
            'status'       : 'OPEN',
            'sl_order_id'  : None,   # Binance STOP_MARKET order ID for SL management
        }

        state['open_trades'].append(trade)
        state['available_capital'] = round(
            state['available_capital'] - margin_est, 2)
        state['daily_trades'] += 1
        _sync_equity(state)
        save_state(state)

        logger.info(f"Crypto paper trade opened: {direction} {qty_btc} BTC "
                    f"@ {entry} | SL {sl} | Risk ${risk_usdt}")
        return trade

    except Exception as e:
        logger.error(f"open_crypto_trade error: {e}")
        return None


def _write_state_safe(state: dict) -> None:
    """Atomic tmp+replace write — crash-safe. Must be called inside _lock."""
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)


def rollback_open_trade(trade_id: str):
    """Remove a just-opened trade from state (used when real order placement fails)."""
    with _lock:
        try:
            if not os.path.exists(STATE_FILE):
                return
            with open(STATE_FILE) as f:
                state = json.load(f)
            before = [t for t in state['open_trades'] if t['id'] == trade_id]
            if not before:
                return
            t = before[0]
            state['open_trades'] = [x for x in state['open_trades'] if x['id'] != trade_id]
            state['available_capital'] = round(state['available_capital'] + t['margin_est'], 2)
            state['daily_trades'] = max(0, state['daily_trades'] - 1)
            _write_state_safe(state)
            logger.info(f"Rolled back trade {trade_id}")
        except Exception as e:
            logger.error(f"rollback_open_trade error: {e}")


def update_trade_sl_order(trade_id: str, sl_order_id):
    """Store Binance SL order ID on an open trade so it can be cancelled later."""
    with _lock:
        try:
            if not os.path.exists(STATE_FILE):
                return
            with open(STATE_FILE) as f:
                state = json.load(f)
            for t in state['open_trades']:
                if t['id'] == trade_id:
                    t['sl_order_id'] = sl_order_id
                    break
            _write_state_safe(state)
        except Exception as e:
            logger.error(f"update_trade_sl_order error: {e}")


# ── Position monitor ──────────────────────────────────────────────────────────

def update_crypto_trades(mark_price: float, symbol: str = '') -> list:
    """
    Check open trades for the given symbol against current mark_price.
    Hits SL or targets, books partial profits, trails SL.
    Returns list of event dicts for Telegram notification.
    """
    events = []
    try:
        state      = load_state()
        state      = _reset_daily_if_new_day(state)
        still_open = []

        for trade in state['open_trades']:
            # Skip trades for other symbols when symbol filter provided
            if symbol and trade.get('symbol', '') != symbol:
                still_open.append(trade)
                continue

            direction = trade['direction']
            entry     = trade['entry_price']
            sl        = trade['current_sl']
            t1        = trade['target1']
            t2        = trade['target2']
            t3        = trade['target3']
            qty       = trade['qty_btc']
            is_long   = direction == 'BULLISH'
            partial_q = round(qty / 3, 3)   # 1/3 at each target

            # ── SL check ─────────────────────────────────────────────────────
            sl_hit = (is_long and mark_price <= sl) or \
                     (not is_long and mark_price >= sl)

            if sl_hit:
                # Remaining qty = full qty minus already-booked portions
                booked   = len(trade.get('targets_hit', []))
                rem_qty  = round(qty - booked * partial_q, 3)
                # Bug fix: use SL level as estimated fill price, NOT mark_price at poll time.
                # Binance STOP_MARKET (MARK_PRICE) triggers at sl, fills near sl.
                # mark_price can be far below sl when the monitor polls 30s later.
                # _fetch_binance_pnl will overwrite with actual fill price after reconciliation.
                sl_fill  = sl
                pnl      = round((sl_fill - entry) * rem_qty * (1 if is_long else -1), 2)
                total_pnl = round(trade.get('realized_pnl', 0) + pnl, 2)
                trade['exit_price'] = sl_fill
                trade['exit_time']  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                trade['pnl_usdt']   = total_pnl
                trade['status']     = 'CLOSED_SL'
                state['daily_pnl']    = round(state.get('daily_pnl', 0) + pnl, 2)
                if booked == 0:  # pure loss (no targets hit before SL)
                    state['daily_losses'] = state.get('daily_losses', 0) + 1
                # Return margin — partial profits were already added to available on each hit
                state['available_capital'] = round(
                    state['available_capital'] + trade['margin_est'] + pnl, 2)
                state['closed_trades'].append(trade)
                events.append({'type': 'SL', 'trade': trade, 'price': sl_fill, 'pnl': pnl})
                logger.info(f"Crypto SL hit: {direction} @ {sl_fill} (mark={mark_price}) | PnL ${pnl}")
                # Memory: outcome depends on whether any targets were hit before SL
                _sl_outcome  = 'PARTIAL' if booked > 0 else 'LOSS'
                _sl_rr       = total_pnl / trade['risk_usdt'] if trade.get('risk_usdt') else 0.0
                _sl_reason   = f"T{booked}+SL" if booked > 0 else 'SL'
                record_trade_close(trade['id'], _sl_outcome, _sl_reason,
                                   round(_sl_rr, 2), round(total_pnl, 2))
                try:
                    outcome = 'BREAKEVEN' if abs(total_pnl) < 1e-9 else ('WIN' if total_pnl > 0 else 'LOSS')
                    log_closed_trade(
                        'crypto', 'crypto_paper_trader', trade,
                        result=outcome,
                        rr_achieved=round(_sl_rr, 2),
                        metadata={'exit_reason': _sl_reason, 'pnl_usdt': total_pnl},
                    )
                    archive_closed_trade_shadow(
                        'crypto', 'crypto_paper_trader', trade,
                        result=outcome,
                        rr_achieved=round(_sl_rr, 2),
                        metadata={'exit_reason': _sl_reason, 'pnl_usdt': total_pnl},
                    )
                except Exception:
                    pass
                continue

            # ── Target hits ───────────────────────────────────────────────────
            targets_hit = set(trade.get('targets_hit', []))

            for label, level in [('T1', t1), ('T2', t2), ('T3', t3)]:
                if label in targets_hit:
                    continue
                t_hit = (is_long and mark_price >= level) or \
                        (not is_long and mark_price <= level)
                if not t_hit:
                    continue

                targets_hit.add(label)
                partial_pnl = round((level - entry) * partial_q * (1 if is_long else -1), 2)
                trade['realized_pnl'] = round(trade.get('realized_pnl', 0) + partial_pnl, 2)
                state['daily_pnl'] = round(state.get('daily_pnl', 0) + partial_pnl, 2)
                # Add profit to available capital immediately on each partial exit
                state['available_capital'] = round(
                    state['available_capital'] + partial_pnl, 2)
                events.append({'type': label, 'trade': trade,
                               'price': level, 'pnl': partial_pnl})
                logger.info(f"Crypto {label} hit: {direction} @ {level} | "
                            f"Partial PnL ${partial_pnl}")

                # Trail SL to breakeven after T1; T1 hit = consecutive loss streak broken
                if label == 'T1':
                    trade['current_sl'] = entry
                    state['daily_losses'] = 0   # reset — a booked target means it's no longer a pure loss
                    logger.info(f"Crypto SL trailed to breakeven @ {entry}")

            trade['targets_hit'] = list(targets_hit)

            # Close if all 3 targets hit — return margin (profits already added above)
            if {'T1', 'T2', 'T3'} <= targets_hit:
                trade['exit_price'] = t3
                trade['exit_time']  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                trade['status']     = 'CLOSED_TP'
                trade['pnl_usdt']   = round(trade.get('realized_pnl', 0), 2)
                state['available_capital'] = round(
                    state['available_capital'] + trade['margin_est'], 2)
                state['closed_trades'].append(trade)
                logger.info(f"Crypto full TP: {direction} | Total PnL ${trade['pnl_usdt']}")
                _tp_rr = round(trade['pnl_usdt'] / trade['risk_usdt'], 2) if trade.get('risk_usdt') else 0.0
                record_trade_close(trade['id'], 'WIN', 'T3',
                                   round(_tp_rr, 2), trade['pnl_usdt'])
                try:
                    outcome = 'BREAKEVEN' if abs(float(trade.get('pnl_usdt', 0))) < 1e-9 else ('WIN' if float(trade.get('pnl_usdt', 0)) > 0 else 'LOSS')
                    log_closed_trade(
                        'crypto', 'crypto_paper_trader', trade,
                        result=outcome,
                        rr_achieved=round(_tp_rr, 2),
                        metadata={'exit_reason': 'T3', 'pnl_usdt': trade.get('pnl_usdt', 0)},
                    )
                    archive_closed_trade_shadow(
                        'crypto', 'crypto_paper_trader', trade,
                        result=outcome,
                        rr_achieved=round(_tp_rr, 2),
                        metadata={'exit_reason': 'T3', 'pnl_usdt': trade.get('pnl_usdt', 0)},
                    )
                except Exception:
                    pass
                continue

            still_open.append(trade)

        state['open_trades'] = still_open
        _sync_equity(state)
        save_state(state)

    except Exception as e:
        logger.error(f"update_crypto_trades error: {e}")

    return events


# ── Binance PnL reconciliation ────────────────────────────────────────────────

def reconcile_pnl(trade_id: str, actual_pnl: float,
                   exit_price: float = None, exit_time: str = None):
    """
    Correct state after Binance confirms the actual trade outcome.
    - actual_pnl   : Binance realized PnL (income API sum)
    - exit_price   : actual fill price from Binance userTrades (optional)
    - exit_time    : ISO timestamp of the fill (optional)
    Updates pnl_usdt and available_capital whenever |diff| >= $0.01.
    Always updates exit_price/exit_time when provided (they may be wrong
    even when PnL diff is small, e.g. when SL used mark vs fill price).
    """
    with _lock:
        try:
            if not os.path.exists(STATE_FILE):
                return
            with open(STATE_FILE) as f:
                state = json.load(f)

            trade = next((t for t in state.get('closed_trades', [])
                          if t['id'] == trade_id), None)
            if not trade:
                return

            changed = False

            # Fix PnL if Binance differs from software estimate
            software_pnl = trade.get('pnl_usdt', 0.0)
            diff = round(actual_pnl - software_pnl, 4)
            if abs(diff) >= 0.01:
                logger.info(
                    f"PnL reconcile {trade_id}: software=${software_pnl} "
                    f"binance=${actual_pnl}  adj=${diff:+.4f}"
                )
                trade['pnl_usdt']          = round(actual_pnl, 4)
                state['available_capital'] = round(state['available_capital'] + diff, 4)
                changed = True

            # Fix exit_price if Binance fill differs from software estimate
            if exit_price is not None and abs(exit_price - trade.get('exit_price', 0)) >= 0.01:
                logger.info(
                    f"Exit price reconcile {trade_id}: "
                    f"software={trade.get('exit_price')} binance={exit_price}"
                )
                trade['exit_price'] = round(exit_price, 2)
                changed = True

            if exit_time is not None:
                trade['exit_time'] = exit_time
                changed = True

            if changed:
                _sync_equity(state)
                save_state(state)
        except Exception as e:
            logger.error(f"reconcile_pnl error: {e}")


# ── Summary ───────────────────────────────────────────────────────────────────

def get_crypto_summary() -> dict:
    state  = load_state()
    closed = state.get('closed_trades', [])
    open_t = state.get('open_trades', [])
    today  = datetime.now().strftime('%Y-%m-%d')

    today_closed = [t for t in closed if (t.get('exit_time') or '')[:10] == today]
    wins   = sum(1 for t in today_closed if t.get('pnl_usdt', 0) > 0)
    losses = sum(1 for t in today_closed if t.get('pnl_usdt', 0) < 0)

    equity   = state.get('capital', DEFAULT_CAPITAL)
    starting = state.get('starting_capital', equity)
    growth   = round(equity - starting, 2)

    return {
        'starting_capital': starting,
        'capital'         : equity,
        'growth'          : growth,
        'available'       : state.get('available_capital', DEFAULT_CAPITAL),
        'open_count'      : len(open_t),
        'open_trades'     : open_t,
        'today_trades'    : len(today_closed),
        'today_wins'      : wins,
        'today_losses'    : losses,
        'today_pnl'       : round(state.get('daily_pnl', 0), 2),
        'total_closed'    : len(closed),
        'paused'          : state.get('paused', False),
    }
