# forex_engine/prop_firms/ftmo/ftmo_state.py
#
# FTMO paper/live state management for CB6 Quantum Forex Engine.
# Canonical home for all FTMO trade state logic.
# State file: data/ftmo_10k/state.json  (isolated per-account directory)
# Legacy:     data/forex_paper_state.json  (migrated on first run)

import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from typing import Optional
from utils.state_io import load_json_locked, save_json_locked
from ml_engine.memory.shadow_logger import log_closed_trade
from ml_engine.memory.replay_shadow import archive_closed_trade_shadow

_STATE_LOCK = threading.Lock()

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ── Isolated state file (Phase 4) ──────────────────────────────────────────────
STATE_FILE = os.path.join(_ROOT, 'data', 'ftmo_10k', 'state.json')
_LEGACY_STATE_FILE = os.path.join(_ROOT, 'data', 'forex_paper_state.json')

# One-time migration: copy legacy file to isolated directory if not yet done
def _migrate_once():
    if not os.path.exists(STATE_FILE) and os.path.exists(_LEGACY_STATE_FILE):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            shutil.copy2(_LEGACY_STATE_FILE, STATE_FILE)
            from utils.logger import logger
            logger.info(f"FTMO state migrated: {_LEGACY_STATE_FILE} → {STATE_FILE}")
        except Exception as e:
            from utils.logger import logger
            logger.warning(f"FTMO state migration failed (non-fatal): {e}")

_migrate_once()

_DEFAULT_STATE = {
    'capital'              : 10000.0,
    'available_capital'    : 10000.0,
    'starting_capital'     : 10000.0,
    'open_trades'          : [],
    'closed_trades'        : [],
    'daily_trades'         : 0,
    'daily_losses'         : 0,
    'daily_pnl'            : 0.0,
    'best_day_pnl'         : 0.0,
    'daily_closed_pnl'     : 0.0,
    'last_reset_date'      : '',
    'gft_daily_snapshot'   : 10000.0,
    'paused'               : False,
    'total_pnl'            : 0.0,
    'peak_capital'         : 10000.0,
    'eod_equity_peak'      : 10000.0,
    'broker'               : 'ftmo',
    'mode'                 : 'free_trial',
    'risk_mode'            : 'normal',
    'high_slippage_symbols': [],
}


def load_state() -> dict:
    try:
        state = load_json_locked(STATE_FILE, _DEFAULT_STATE.copy())
        for k, v in _DEFAULT_STATE.items():
            if k not in state:
                state[k] = v
        return state
    except Exception:
        return _DEFAULT_STATE.copy()


def _save(state: dict):
    try:
        save_json_locked(STATE_FILE, state)
    except Exception as e:
        from utils.logger import logger
        logger.error(f"forex state save error: {e}")


# REQ-4: Public alias so forex_worker can import and call explicitly.
save_state = _save


def _reset_daily_if_needed(state: dict) -> dict:
    from datetime import timezone, timedelta
    broker = state.get('broker', 'ftmo')
    # Both FTMO and GFT reset at 22:00 UTC (FTMO server day boundary)
    utc_now = datetime.now(timezone.utc)
    day_key = (utc_now - timedelta(hours=22)).strftime('%Y-%m-%d')

    if state.get('last_reset_date') != day_key:
        current_eod_peak = state.get('eod_equity_peak', state.get('starting_capital', 10000.0))
        state['eod_equity_peak']    = max(current_eod_peak, state['capital'])
        state['gft_daily_snapshot'] = state['capital']
        state['daily_trades']       = 0
        state['daily_losses']       = 0
        state['daily_pnl']          = 0.0
        state['daily_closed_pnl']   = 0.0
        state['best_day_pnl']       = 0.0
        state['last_reset_date']    = day_key
    return state


# REQ-4: Public alias — allows forex_worker to deterministically reset daily
# counters before evaluating the daily PnL guard inside _run_scan.
reset_daily_if_needed = _reset_daily_if_needed


# ── Prop-Risk Guard ────────────────────────────────────────────────────────────

def get_risk_mode(state: dict) -> tuple:
    """
    Returns (mode, reason) based on internal guard rails (not official FTMO limits).
    mode = 'normal' | 'reduced' | 'aplus_only' | 'paused'
    """
    from forex_engine.forex_instruments import FTMO_RISK_GUARD
    g        = FTMO_RISK_GUARD
    start    = state.get('starting_capital', 10000.0)
    cap      = state.get('capital', start)
    daily    = state.get('daily_pnl', 0.0)
    total_dd = round(start - cap, 2)

    dl_stop  = round(start * g['daily_loss_stop_pct']   / 100, 2)
    dl_aplus = round(start * g['daily_loss_aplus_pct']  / 100, 2)
    dl_red   = round(start * g['daily_loss_reduce_pct'] / 100, 2)

    if daily <= -dl_stop:
        return ('paused',
                f"Daily loss ${abs(daily):.2f} ≥ stop gate ${dl_stop:.2f} — no more entries today")
    if daily <= -dl_aplus:
        return ('aplus_only',
                f"Daily loss ${abs(daily):.2f} ≥ A+ gate ${dl_aplus:.2f} — elite setups only")
    if daily <= -dl_red:
        return ('reduced',
                f"Daily loss ${abs(daily):.2f} ≥ reduce gate ${dl_red:.2f} — 50% lots")

    dd_stop  = round(start * g['total_dd_stop_pct']   / 100, 2)
    dd_aplus = round(start * g['total_dd_aplus_pct']  / 100, 2)
    dd_red   = round(start * g['total_dd_reduce_pct'] / 100, 2)

    if total_dd >= dd_stop:
        return ('paused',
                f"Total DD ${total_dd:.2f} ≥ stop gate ${dd_stop:.2f} — trading halted")
    if total_dd >= dd_aplus:
        return ('aplus_only',
                f"Total DD ${total_dd:.2f} ≥ A+ gate ${dd_aplus:.2f} — elite setups only")
    if total_dd >= dd_red:
        return ('reduced',
                f"Total DD ${total_dd:.2f} ≥ reduce gate ${dd_red:.2f} — 50% lots")

    pp_stop = round(start * g['daily_profit_stop_pct']   / 100, 2)
    pp_red  = round(start * g['daily_profit_reduce_pct'] / 100, 2)

    if daily >= pp_stop:
        return ('paused',
                f"Daily profit ${daily:.2f} ≥ protect stop ${pp_stop:.2f} — locking in gains")
    if daily >= pp_red:
        return ('reduced',
                f"Daily profit ${daily:.2f} ≥ protect reduce ${pp_red:.2f} — 50% lots")

    today        = datetime.now().strftime('%Y-%m-%d')
    closed       = state.get('closed_trades', [])
    today_profit = sum(
        t.get('pnl_usd', 0) for t in closed
        if (t.get('exit_time') or '')[:10] == today and t.get('pnl_usd', 0) > 0
    )
    total_pos = sum(t.get('pnl_usd', 0) for t in closed if t.get('pnl_usd', 0) > 0)
    if total_pos > 0 and today_profit > 0:
        contribution = today_profit / total_pos * 100
        max_pct      = g['best_day_max_pct']
        if contribution >= max_pct:
            return ('paused',
                    f"Best Day {contribution:.1f}% ≥ {max_pct:.0f}% limit — stop to maintain consistency")

    return ('normal', 'OK')


# ── Trade gates ────────────────────────────────────────────────────────────────

def can_open_trade(state: dict) -> tuple:
    """
    Broker-aware gate. Returns (allowed: bool, reason: str).
    """
    from forex_engine.forex_instruments import FTMO_RULES, GFT_RULES

    if state.get('paused'):
        return False, 'Engine paused'

    if len(state.get('open_trades', [])) > 0:
        return False, 'Position already open (1 trade at a time)'

    starting = state.get('starting_capital', 10000.0)
    broker   = state.get('broker', 'ftmo')

    if broker.startswith('gft'):
        gft_model        = '1_step' if broker == 'gft_1step' else 'instant_pro'
        rules            = GFT_RULES[gft_model]
        snapshot         = state.get('gft_daily_snapshot', starting)
        daily_loss_limit = snapshot * rules['max_daily_loss_pct'] / 100
        current_equity   = state['capital']
        daily_loss       = snapshot - current_equity
        if daily_loss >= daily_loss_limit:
            return False, (
                f"GFT daily loss limit hit "
                f"(lost ${daily_loss:.2f} of ${daily_loss_limit:.2f} "
                f"from 5PM snapshot ${snapshot:.2f})"
            )
        dd_floor = starting * (1 - rules['max_total_dd_pct'] / 100)
        if current_equity <= dd_floor:
            return False, (
                f"GFT static DD floor breached "
                f"(equity ${current_equity:.2f} ≤ floor ${dd_floor:.2f})"
            )
        daily_closed = state.get('daily_closed_pnl', 0.0)
        if daily_closed >= rules['daily_profit_cap']:
            return False, (
                f"GFT daily profit cap reached "
                f"(${daily_closed:.2f} ≥ ${rules['daily_profit_cap']:.0f})"
            )
        closed = state.get('closed_trades', [])
        if closed and closed[-1].get('pnl_usd', 0) < 0:
            last_lots = closed[-1].get('lots', 0)
            state['_last_lots'] = last_lots
        if state.get('daily_trades', 0) >= GFT_RULES['max_trades_per_day']:
            return False, f"GFT daily trade limit hit ({GFT_RULES['max_trades_per_day']})"
        return True, 'OK'

    mode  = state.get('mode', 'free_trial')
    rules = FTMO_RULES.get(mode, FTMO_RULES['free_trial'])

    risk_mode, risk_reason = get_risk_mode(state)
    if risk_mode == 'paused':
        return False, f"RISK GUARD — {risk_reason}"

    daily_loss_limit = starting * rules['max_daily_loss_pct'] / 100
    daily_pnl        = state.get('daily_pnl', 0.0)
    if daily_pnl <= -daily_loss_limit:
        return False, f"FTMO daily loss limit hit (${abs(daily_pnl):.2f} of ${daily_loss_limit:.2f})"

    dd_limit_usd = starting * rules['max_total_dd_pct'] / 100
    eod_peak     = state.get('eod_equity_peak', starting)
    dd_floor     = eod_peak - dd_limit_usd
    if state['capital'] <= dd_floor:
        return False, (
            f"FTMO EOD trailing DD limit hit "
            f"(equity ${state['capital']:.2f} ≤ floor ${dd_floor:.2f} "
            f"= peak ${eod_peak:.2f} − ${dd_limit_usd:.0f})"
        )

    profit_target  = starting * rules['profit_target_pct'] / 100
    best_day_limit = profit_target * rules['best_day_rule_pct'] / 100
    best_day_pnl   = state.get('best_day_pnl', 0.0)
    if best_day_pnl >= best_day_limit:
        return False, f"FTMO Best Day Rule hit (${best_day_pnl:.2f} of ${best_day_limit:.2f} max)"
    # Also block if equity PnL (realized + floating) would breach the cap.
    # FTMO evaluates best-day on account equity, not just closed trades.
    daily_equity_pnl = state.get('daily_pnl', 0.0)
    if daily_equity_pnl >= best_day_limit:
        return False, (
            f"FTMO Best Day Rule (equity) — today ${daily_equity_pnl:.2f} "
            f"≥ ${best_day_limit:.2f} cap (realized + floating)"
        )

    if state['daily_trades'] >= FTMO_RULES['max_trades_per_day']:
        return False, f"Daily trade limit hit ({FTMO_RULES['max_trades_per_day']})"

    return True, 'OK'


# ── Open / close trades ────────────────────────────────────────────────────────

def open_trade(setup: dict, lots: float, ticket: int = 0) -> Optional[dict]:
    with _STATE_LOCK:
        state = load_state()
        state = _reset_daily_if_needed(state)

        allowed, reason = can_open_trade(state)
        if not allowed:
            from utils.logger import logger
            logger.info(f"Forex trade blocked: {reason}")
            return None

        sig     = setup['entry_signal']
        sl_dist = abs(sig['entry'] - sig['stop_loss'])
        t2_dist = abs(sig['target2'] - sig['entry'])
        exp_rrr = round(t2_dist / sl_dist, 2) if sl_dist > 0 else 0.0
        trade = {
            'id'              : str(uuid.uuid4())[:8],
            'ticket'          : ticket,
            'symbol'          : setup['symbol'],
            'direction'       : setup['direction'],
            'lots'            : lots,
            'entry_price'     : sig['entry'],
            'stop_loss'       : sig['stop_loss'],
            'current_sl'      : sig['stop_loss'],
            'target1'         : sig['target1'],
            'target2'         : sig['target2'],
            'target3'         : sig['target3'],
            'risk_usd'        : sig.get('risk_usd', 0),
            'rr_ratio'        : sig['rr_ratio'],
            'expected_rrr'    : exp_rrr,
            'actual_rrr'      : 0.0,
            'confluence'      : setup['confluence'],
            'mss_type'        : setup.get('mss_type', 'BOS'),
            'entry_time'      : datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'entry_reason'    : setup.get('entry_reason', f"{setup.get('mss_type','BOS')} score={setup['confluence']}"),
            'spread_at_entry' : setup.get('spread_at_entry', 0.0),
            'risk_mode'       : setup.get('risk_mode', 'normal'),
            'targets_hit'     : [],
            'be_triggered'    : False,
            'pnl_usd'         : 0.0,
            'status'          : 'OPEN',
            'exit_reason'     : None,
        }

        state['open_trades'].append(trade)
        state['daily_trades'] += 1
        state['available_capital'] -= sig.get('risk_usd', 0)
        _save(state)
        return trade


def rollback_trade(trade_id: str, risk_usd: float):
    """Remove a trade whose MT5 order failed. Thread-safe."""
    with _STATE_LOCK:
        state  = load_state()
        before = len(state['open_trades'])
        state['open_trades'] = [t for t in state['open_trades'] if t['id'] != trade_id]
        if len(state['open_trades']) < before:
            state['daily_trades']      = max(0, state['daily_trades'] - 1)
            state['available_capital'] = round(
                state.get('available_capital', state['capital']) + risk_usd, 2
            )
            _save(state)


def update_trade_ticket(trade_id: str, ticket: int):
    """Write the real MT5 ticket number back to an open trade."""
    with _STATE_LOCK:
        state = load_state()
        for t in state['open_trades']:
            if t['id'] == trade_id:
                t['ticket'] = ticket
                _save(state)
                return


def update_trade_fill_price(trade_id: str, fill_price: float,
                            sl: float, t1: float, t2: float, t3: float, risk_usd: float):
    """Overwrite entry + SL/targets with actual MT5 fill values."""
    with _STATE_LOCK:
        state = load_state()
        for t in state['open_trades']:
            if t['id'] == trade_id:
                t['entry_price'] = fill_price
                t['stop_loss']   = sl
                t['current_sl']  = sl
                t['target1']     = t1
                t['target2']     = t2
                t['target3']     = t3
                t['risk_usd']    = risk_usd
                _save(state)
                return


def update_trades(current_price: float, symbol: str) -> list:
    """Check SL/TP hits for open trades on this symbol. Returns exit events."""
    with _STATE_LOCK:
        return _update_trades_locked(current_price, symbol)


def _update_trades_locked(current_price: float, symbol: str) -> list:
    """Must be called with _STATE_LOCK held."""
    from forex_engine.forex_instruments import INSTRUMENTS
    state  = load_state()
    events = []

    for trade in list(state['open_trades']):
        if trade['symbol'] != symbol:
            continue

        direction = trade['direction']
        sl        = trade['current_sl']
        t1        = trade['target1']
        t2        = trade['target2']
        t3        = trade['target3']
        lots      = trade['lots']
        entry     = trade['entry_price']

        cfg           = INSTRUMENTS.get(symbol, {})
        contract_size = cfg.get('contract_size', 100000)
        min_lot       = cfg.get('min_lot', 0.01)
        max_spread    = cfg.get('max_spread', 0.0)
        booked        = len(trade.get('targets_hit', []))

        partial_lot = round(lots / 3, 2)
        can_partial = partial_lot >= min_lot
        t1_was_be   = (not can_partial) and ('T1' in trade.get('targets_hit', []))

        def _pnl(exit_px, close_lots):
            dist     = (exit_px - entry) if direction == 'BULLISH' else (entry - exit_px)
            raw      = round(close_lots * contract_size * dist, 2)
            spd_cost = round(max_spread * contract_size * close_lots, 2)
            return round(raw - spd_cost, 2)

        def _remaining_lots():
            if t1_was_be:
                return lots
            return round(lots * (3 - booked) / 3, 2)

        hit_type   = None
        exit_price = current_price

        # Early break-even trigger (40% to T1)
        orig_sl = trade.get('stop_loss', sl)
        if not trade.get('be_triggered') and 'T1' not in trade.get('targets_hit', []):
            from forex_engine.forex_instruments import FTMO_RISK_GUARD
            be_pct = FTMO_RISK_GUARD.get('be_trigger_pct', 0.40)
            if direction == 'BULLISH':
                be_trigger_px = entry + (t1 - entry) * be_pct
                if current_price >= be_trigger_px:
                    trade['current_sl']   = entry
                    trade['be_triggered'] = True
                    events.append({'type': 'BE_TRIGGER', 'trade': trade,
                                   'price': current_price, 'pnl': 0.0, 'close_lots': 0.0})
            else:
                be_trigger_px = entry - (entry - t1) * be_pct
                if current_price <= be_trigger_px:
                    trade['current_sl']   = entry
                    trade['be_triggered'] = True
                    events.append({'type': 'BE_TRIGGER', 'trade': trade,
                                   'price': current_price, 'pnl': 0.0, 'close_lots': 0.0})
            sl = trade['current_sl']

        # MAE protection (85% of SL distance, before first target)
        if (hit_type is None and not trade.get('targets_hit')
                and not trade.get('be_triggered')):
            from forex_engine.forex_instruments import FTMO_RISK_GUARD
            mae_pct = FTMO_RISK_GUARD.get('mae_exit_pct', 0.85)
            if direction == 'BULLISH':
                sl_dist = entry - orig_sl
                if sl_dist > 0 and current_price <= entry - sl_dist * mae_pct:
                    hit_type = 'MAE_EXIT'; exit_price = current_price
            else:
                sl_dist = orig_sl - entry
                if sl_dist > 0 and current_price >= entry + sl_dist * mae_pct:
                    hit_type = 'MAE_EXIT'; exit_price = current_price

        # Time-based exit (8 candles × 15m = 2 hours, T1 not hit)
        if hit_type is None and 'T1' not in trade.get('targets_hit', []):
            from forex_engine.forex_instruments import FTMO_RISK_GUARD
            max_mins    = FTMO_RISK_GUARD.get('max_candles_no_progress', 8) * 15
            entry_t_str = trade.get('entry_time', '')
            if entry_t_str:
                try:
                    entry_dt = datetime.strptime(entry_t_str, '%Y-%m-%d %H:%M:%S')
                    elapsed  = (datetime.now() - entry_dt).total_seconds() / 60
                    if elapsed >= max_mins:
                        hit_type = 'TIME_EXIT'; exit_price = current_price
                except Exception:
                    pass

        if direction == 'BULLISH':
            if hit_type is None and current_price <= sl:
                hit_type = 'SL';    exit_price = sl
            elif hit_type is None and 'T3' not in trade['targets_hit'] and current_price >= t3:
                hit_type = 'T3';    exit_price = t3
            elif hit_type is None and 'T2' not in trade['targets_hit'] and current_price >= t2:
                hit_type = 'T2';    exit_price = t2;    trade['current_sl'] = entry
            elif hit_type is None and 'T1' not in trade['targets_hit'] and current_price >= t1:
                hit_type = 'T1' if can_partial else 'T1_BE'
                exit_price = t1;    trade['current_sl'] = entry
        else:
            if hit_type is None and current_price >= sl:
                hit_type = 'SL';    exit_price = sl
            elif hit_type is None and 'T3' not in trade['targets_hit'] and current_price <= t3:
                hit_type = 'T3';    exit_price = t3
            elif hit_type is None and 'T2' not in trade['targets_hit'] and current_price <= t2:
                hit_type = 'T2';    exit_price = t2;    trade['current_sl'] = entry
            elif hit_type is None and 'T1' not in trade['targets_hit'] and current_price <= t1:
                hit_type = 'T1' if can_partial else 'T1_BE'
                exit_price = t1;    trade['current_sl'] = entry

        if hit_type is None:
            continue

        if hit_type == 'T1_BE':
            trade['targets_hit'].append('T1')
            events.append({'type': 'T1_BE', 'trade': trade,
                           'price': exit_price, 'pnl': 0.0, 'close_lots': 0.0})

        elif hit_type in ('SL', 'T3', 'MAE_EXIT', 'TIME_EXIT'):
            rem       = _remaining_lots()
            pnl       = _pnl(exit_price, rem)
            total_pnl = round(trade.get('pnl_usd', 0) + pnl, 2)
            sl_dist_o = abs(entry - trade.get('stop_loss', entry))
            if sl_dist_o > 0:
                move    = (exit_price - entry) if direction == 'BULLISH' else (entry - exit_price)
                act_rrr = round(move / sl_dist_o, 2)
            else:
                act_rrr = 0.0
            trade['status']       = 'CLOSED'
            trade['exit_time']    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            trade['exit_price']   = exit_price
            trade['pnl_usd']      = total_pnl
            trade['exit_reason']  = hit_type
            trade['actual_rrr']   = act_rrr
            state['open_trades'].remove(trade)
            state['closed_trades'].append(trade)
            try:
                outcome = 'WIN' if total_pnl > 0 else ('BREAKEVEN' if total_pnl == 0 else 'LOSS')
                log_closed_trade(
                    'forex', 'forex_ftmo_state', trade,
                    result=outcome,
                    rr_achieved=act_rrr,
                    metadata={'exit_reason': hit_type, 'pnl_usd': total_pnl},
                )
                archive_closed_trade_shadow(
                    'forex', 'forex_ftmo_state', trade,
                    result=outcome,
                    rr_achieved=act_rrr,
                    metadata={'exit_reason': hit_type, 'pnl_usd': total_pnl},
                )
            except Exception:
                pass
            state['capital']           += pnl
            state['available_capital'] += pnl + trade.get('risk_usd', 0)
            state['total_pnl']         += pnl
            state['daily_pnl']         += pnl
            if pnl > 0:
                state['daily_closed_pnl'] = round(state.get('daily_closed_pnl', 0.0) + pnl, 2)
            if pnl < 0:
                state['daily_losses'] += 1
            if state['daily_pnl'] > state.get('best_day_pnl', 0.0):
                state['best_day_pnl'] = state['daily_pnl']
            state['peak_capital'] = max(state['peak_capital'], state['capital'])
            events.append({'type': hit_type, 'trade': trade,
                           'price': exit_price, 'pnl': pnl, 'close_lots': rem})

        else:
            # T1 (partial) or T2
            if hit_type == 'T2' and t1_was_be:
                rem       = _remaining_lots()
                pnl       = _pnl(exit_price, rem)
                total_pnl = round(trade.get('pnl_usd', 0) + pnl, 2)
                sl_dist_o = abs(entry - trade.get('stop_loss', entry))
                act_rrr   = (
                    round((exit_price - entry) / sl_dist_o, 2) if sl_dist_o > 0 and direction == 'BULLISH'
                    else round((entry - exit_price) / sl_dist_o, 2) if sl_dist_o > 0
                    else 0.0
                )
                trade['status']       = 'CLOSED'
                trade['exit_time']    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                trade['exit_price']   = exit_price
                trade['pnl_usd']      = total_pnl
                trade['exit_reason']  = 'T2'
                trade['actual_rrr']   = act_rrr
                trade['targets_hit'].append('T2')
                state['open_trades'].remove(trade)
                state['closed_trades'].append(trade)
                try:
                    outcome = 'WIN' if total_pnl > 0 else ('BREAKEVEN' if total_pnl == 0 else 'LOSS')
                    log_closed_trade(
                        'forex', 'forex_ftmo_state', trade,
                        result=outcome,
                        rr_achieved=act_rrr,
                        metadata={'exit_reason': 'T2', 'pnl_usd': total_pnl},
                    )
                    archive_closed_trade_shadow(
                        'forex', 'forex_ftmo_state', trade,
                        result=outcome,
                        rr_achieved=act_rrr,
                        metadata={'exit_reason': 'T2', 'pnl_usd': total_pnl},
                    )
                except Exception:
                    pass
                state['capital']           += pnl
                state['available_capital'] += pnl + trade.get('risk_usd', 0)
                state['total_pnl']         += pnl
                state['daily_pnl']         += pnl
                if pnl > 0:
                    state['daily_closed_pnl'] = round(state.get('daily_closed_pnl', 0.0) + pnl, 2)
                if pnl < 0:
                    state['daily_losses'] += 1
                if state['daily_pnl'] > state.get('best_day_pnl', 0.0):
                    state['best_day_pnl'] = state['daily_pnl']
                state['peak_capital'] = max(state['peak_capital'], state['capital'])
                events.append({'type': 'T2', 'trade': trade,
                               'price': exit_price, 'pnl': pnl, 'close_lots': rem})
            else:
                pnl = _pnl(exit_price, partial_lot)
                trade['targets_hit'].append(hit_type)
                trade['pnl_usd']           += pnl
                state['capital']           += pnl
                state['available_capital'] += pnl
                state['total_pnl']         += pnl
                state['daily_pnl']         += pnl
                if pnl > 0:
                    state['daily_closed_pnl'] = round(state.get('daily_closed_pnl', 0.0) + pnl, 2)
                if state['daily_pnl'] > state.get('best_day_pnl', 0.0):
                    state['best_day_pnl'] = state['daily_pnl']
                events.append({'type': hit_type, 'trade': trade,
                               'price': exit_price, 'pnl': pnl, 'close_lots': partial_lot})

    if events:
        _save(state)
    return events


def manual_exit_trade(trade_id: str, exit_price: float) -> Optional[dict]:
    """Sync a manual MT5 exit into paper state."""
    from forex_engine.forex_instruments import INSTRUMENTS
    with _STATE_LOCK:
        state = load_state()
        for trade in list(state['open_trades']):
            if trade['id'] != trade_id:
                continue
            sym           = trade['symbol']
            cfg           = INSTRUMENTS.get(sym, {})
            contract_size = cfg.get('contract_size', 100000)
            direction     = trade['direction']
            entry         = trade['entry_price']
            lots          = trade['lots']
            booked        = len(trade.get('targets_hit', []))
            rem_lots      = round(lots * (3 - booked) / 3, 2)
            dist          = (exit_price - entry) if direction == 'BULLISH' else (entry - exit_price)
            pnl           = round(rem_lots * contract_size * dist, 2)
            total_pnl     = round(trade.get('pnl_usd', 0) + pnl, 2)

            trade['status']      = 'CLOSED'
            trade['exit_time']   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            trade['exit_price']  = exit_price
            trade['pnl_usd']     = total_pnl
            trade['exit_reason'] = 'MANUAL'
            state['open_trades'].remove(trade)
            state['closed_trades'].append(trade)
            try:
                outcome = 'WIN' if total_pnl > 0 else ('BREAKEVEN' if total_pnl == 0 else 'LOSS')
                rr = trade.get('actual_rrr', 0.0)
                log_closed_trade(
                    'forex', 'forex_ftmo_state', trade,
                    result=outcome,
                    rr_achieved=rr,
                    metadata={'exit_reason': 'MANUAL', 'pnl_usd': total_pnl},
                )
                archive_closed_trade_shadow(
                    'forex', 'forex_ftmo_state', trade,
                    result=outcome,
                    rr_achieved=rr,
                    metadata={'exit_reason': 'MANUAL', 'pnl_usd': total_pnl},
                )
            except Exception:
                pass
            state['capital']           += pnl
            state['available_capital'] += pnl + trade.get('risk_usd', 0)
            state['total_pnl']         += pnl
            state['daily_pnl']         += pnl
            if pnl > 0:
                state['daily_closed_pnl'] = round(state.get('daily_closed_pnl', 0.0) + pnl, 2)
            if pnl < 0:
                state['daily_losses'] += 1
            if state['daily_pnl'] > state.get('best_day_pnl', 0.0):
                state['best_day_pnl'] = state['daily_pnl']
            state['peak_capital'] = max(state['peak_capital'], state['capital'])
            _save(state)
            return {'type': 'MANUAL', 'trade': trade, 'price': exit_price, 'pnl': pnl}
        return None


def compute_best_day_stats(closed_trades: list) -> tuple:
    """FTMO-style Best Day Rule stats from closed trades grouped by exit date."""
    from collections import defaultdict
    daily: dict = defaultdict(float)
    for t in closed_trades:
        day = (t.get('exit_time') or t.get('entry_time') or '')[:10]
        daily[day] += t.get('pnl_usd', 0.0)
    positive = {d: p for d, p in daily.items() if p > 0}
    if not positive:
        return 0.0, 0.0, 0.0, ''
    best_date = max(positive, key=positive.get)
    best_day  = positive[best_date]
    total_pos = sum(positive.values())
    ratio     = round(best_day / total_pos * 100, 1) if total_pos > 0 else 0.0
    return round(best_day, 2), round(total_pos, 2), ratio, best_date


def get_summary() -> dict:
    state  = load_state()
    closed = state.get('closed_trades', [])
    wins   = [t for t in closed if t.get('pnl_usd', 0) > 0]
    losses = [t for t in closed if t.get('pnl_usd', 0) < 0]
    _wr_d  = len(wins) + len(losses)
    wr     = round(len(wins) / _wr_d * 100, 1) if _wr_d else 0.0
    start  = state.get('starting_capital', 10000.0)
    cap    = state.get('capital', 10000.0)
    growth = round((cap - start) / start * 100, 2)
    from forex_engine.forex_instruments import FTMO_RULES
    mode         = state.get('mode', 'free_trial')
    rules        = FTMO_RULES.get(mode, FTMO_RULES['free_trial'])
    dd_limit_usd = start * rules['max_total_dd_pct'] / 100
    eod_peak     = state.get('eod_equity_peak', start)
    dd_floor     = round(eod_peak - dd_limit_usd, 2)
    return {
        'capital'        : round(cap, 2),
        'starting'       : start,
        'growth_pct'     : growth,
        'total_pnl'      : round(state.get('total_pnl', 0), 2),
        'open_trades'    : len(state.get('open_trades', [])),
        'total_trades'   : len(closed),
        'wins'           : len(wins),
        'losses'         : len(losses),
        'win_rate'       : wr,
        'daily_pnl'      : round(state.get('daily_pnl', 0), 2),
        'peak_capital'   : round(state.get('peak_capital', start), 2),
        'eod_equity_peak': round(eod_peak, 2),
        'dd_floor'       : dd_floor,
        'drawdown_pct'   : round((cap - dd_floor) / dd_limit_usd * 100, 1)
                           if dd_limit_usd > 0 else 0.0,
    }
