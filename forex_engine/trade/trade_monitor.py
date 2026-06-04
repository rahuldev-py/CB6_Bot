# forex_engine/trade/trade_monitor.py
# Position monitor — polls price, detects SL/TP hits, fires exit events.

import time
import threading
from datetime import datetime
from typing import Callable, Optional
from utils.logger import logger
from forex_engine.forex_instruments import INSTRUMENTS, FTMO_RISK_GUARD
from forex_engine.trade.sl_tp_manager import (
    breakeven_trigger_price, mae_exit_price
)


class TradeMonitor:
    """
    Monitors open positions in a background thread.
    Calls on_event(event_dict) when SL/T1/T2/T3/MAE/TIME exit detected.
    """

    def __init__(self, connector, load_state_fn: Callable, save_state_fn: Callable,
                 on_event: Callable, symbols: list, poll_secs: int = 30):
        self._connector    = connector
        self._load_state   = load_state_fn
        self._save_state   = save_state_fn
        self._on_event     = on_event
        self._symbols      = symbols
        self._poll_secs    = poll_secs
        self._running      = False
        self._rollover_fired = False

    def start(self):
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True, name="TradeMonitor")
        t.start()
        logger.info("TradeMonitor started")

    def stop(self):
        self._running = False

    def _loop(self):
        from forex_engine.scanner.signal_scanner import approaching_rollover
        while self._running:
            try:
                if approaching_rollover():
                    if not self._rollover_fired:
                        self._pre_rollover_guard()
                        self._rollover_fired = True
                else:
                    self._rollover_fired = False

                for sym in self._symbols:
                    price = self._connector.get_price(sym)
                    if price is None:
                        continue
                    self._check_symbol(sym, price)
            except Exception as e:
                logger.error(f"TradeMonitor loop error: {e}")
            time.sleep(self._poll_secs)

    def _check_symbol(self, symbol: str, current_price: float):
        state  = self._load_state()
        events = self._process_trades(state, symbol, current_price)
        for ev in events:
            self._on_event(ev)

    def _process_trades(self, state: dict, symbol: str,
                        current_price: float) -> list:
        """
        Evaluate all open trades for this symbol.
        Returns list of exit event dicts.
        """
        events = []
        cfg    = INSTRUMENTS.get(symbol, {})
        contract_size = cfg.get('contract_size', 100000)
        min_lot       = cfg.get('min_lot', 0.01)
        max_spread    = cfg.get('max_spread', 0.0)

        for trade in list(state.get('open_trades', [])):
            if trade.get('symbol') != symbol:
                continue

            direction = trade['direction']
            entry     = trade['entry_price']
            sl        = trade['current_sl']
            t1, t2, t3 = trade['target1'], trade['target2'], trade['target3']
            lots      = trade['lots']
            booked    = len(trade.get('targets_hit', []))
            orig_sl   = trade.get('stop_loss', sl)

            partial_lot = round(lots / 3, 2)
            can_partial = partial_lot >= min_lot
            t1_was_be   = (not can_partial) and ('T1' in trade.get('targets_hit', []))

            def _pnl(px, close_l):
                dist  = (px - entry) if direction == 'BULLISH' else (entry - px)
                raw   = round(close_l * contract_size * dist, 2)
                cost  = round(max_spread * contract_size * close_l, 2)
                return round(raw - cost, 2)

            def _rem():
                return lots if t1_was_be else round(lots * (3 - booked) / 3, 2)

            hit_type   = None
            exit_price = current_price

            # Early break-even trigger
            if not trade.get('be_triggered') and 'T1' not in trade.get('targets_hit', []):
                be_pct = FTMO_RISK_GUARD.get('be_trigger_pct', 0.40)
                be_px  = breakeven_trigger_price(entry, t1, direction, be_pct)
                triggered = (
                    (direction == 'BULLISH' and current_price >= be_px) or
                    (direction == 'BEARISH' and current_price <= be_px)
                )
                if triggered:
                    trade['current_sl']   = entry
                    trade['be_triggered'] = True
                    events.append({'type': 'BE_TRIGGER', 'trade': trade,
                                   'price': current_price, 'pnl': 0.0,
                                   'close_lots': 0.0})
                    sl = entry

            # MAE exit
            if hit_type is None and not trade.get('targets_hit') and not trade.get('be_triggered'):
                mae_pct = FTMO_RISK_GUARD.get('mae_exit_pct', 0.85)
                mae_px  = mae_exit_price(entry, orig_sl, direction, mae_pct)
                if ((direction == 'BULLISH' and current_price <= mae_px) or
                        (direction == 'BEARISH' and current_price >= mae_px)):
                    hit_type = 'MAE_EXIT'

            # Time exit
            if hit_type is None and 'T1' not in trade.get('targets_hit', []):
                max_mins = FTMO_RISK_GUARD.get('max_candles_no_progress', 8) * 15
                entry_t  = trade.get('entry_time', '')
                if entry_t:
                    try:
                        elapsed = (datetime.now() -
                                   datetime.strptime(entry_t, '%Y-%m-%d %H:%M:%S')
                                   ).total_seconds() / 60
                        if elapsed >= max_mins:
                            hit_type = 'TIME_EXIT'
                    except Exception:
                        pass

            # SL / T1 / T2 / T3
            if direction == 'BULLISH':
                if hit_type is None and current_price <= sl:
                    hit_type = 'SL';    exit_price = sl
                elif hit_type is None and 'T3' not in trade['targets_hit'] and current_price >= t3:
                    hit_type = 'T3';    exit_price = t3
                elif hit_type is None and 'T2' not in trade['targets_hit'] and current_price >= t2:
                    hit_type = 'T2';    exit_price = t2
                    trade['current_sl'] = entry
                elif hit_type is None and 'T1' not in trade['targets_hit'] and current_price >= t1:
                    hit_type = 'T1' if can_partial else 'T1_BE'
                    exit_price = t1;    trade['current_sl'] = entry
            else:
                if hit_type is None and current_price >= sl:
                    hit_type = 'SL';    exit_price = sl
                elif hit_type is None and 'T3' not in trade['targets_hit'] and current_price <= t3:
                    hit_type = 'T3';    exit_price = t3
                elif hit_type is None and 'T2' not in trade['targets_hit'] and current_price <= t2:
                    hit_type = 'T2';    exit_price = t2
                    trade['current_sl'] = entry
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
                rem   = _rem()
                pnl   = _pnl(exit_price, rem)
                total = round(trade.get('pnl_usd', 0) + pnl, 2)
                sl_d  = abs(entry - orig_sl)
                move  = (exit_price - entry) if direction == 'BULLISH' else (entry - exit_price)
                rrr   = round(move / sl_d, 2) if sl_d > 0 else 0.0

                trade.update({
                    'status': 'CLOSED', 'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'exit_price': exit_price, 'pnl_usd': total,
                    'exit_reason': hit_type, 'actual_rrr': rrr,
                })
                state['open_trades']  = [t for t in state['open_trades'] if t['id'] != trade['id']]
                state['closed_trades'].append(trade)
                self._apply_pnl(state, pnl, rem, trade.get('risk_usd', 0))
                events.append({'type': hit_type, 'trade': trade,
                               'price': exit_price, 'pnl': pnl, 'close_lots': rem})
            else:
                if hit_type == 'T2' and t1_was_be:
                    rem = _rem()
                    pnl = _pnl(exit_price, rem)
                    sl_d = abs(entry - orig_sl)
                    move = (exit_price - entry) if direction == 'BULLISH' else (entry - exit_price)
                    rrr  = round(move / sl_d, 2) if sl_d > 0 else 0.0
                    trade.update({
                        'status': 'CLOSED', 'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'exit_price': exit_price, 'pnl_usd': round(trade.get('pnl_usd', 0) + pnl, 2),
                        'exit_reason': 'T2', 'actual_rrr': rrr,
                    })
                    trade['targets_hit'].append('T2')
                    state['open_trades']  = [t for t in state['open_trades'] if t['id'] != trade['id']]
                    state['closed_trades'].append(trade)
                    self._apply_pnl(state, pnl, rem, trade.get('risk_usd', 0))
                    events.append({'type': 'T2', 'trade': trade,
                                   'price': exit_price, 'pnl': pnl, 'close_lots': rem})
                else:
                    pnl = _pnl(exit_price, partial_lot)
                    trade['targets_hit'].append(hit_type)
                    trade['pnl_usd'] = round(trade.get('pnl_usd', 0) + pnl, 2)
                    self._apply_pnl(state, pnl, 0, 0)
                    events.append({'type': hit_type, 'trade': trade,
                                   'price': exit_price, 'pnl': pnl,
                                   'close_lots': partial_lot})

        if events:
            state_copy = {**state}
            if state_copy.get('daily_pnl', 0) > state_copy.get('best_day_pnl', 0.0):
                state_copy['best_day_pnl'] = state_copy['daily_pnl']
            state_copy['peak_capital'] = max(
                state_copy.get('peak_capital', 0), state_copy.get('capital', 0)
            )
            self._save_state(state_copy)

        return events

    def _apply_pnl(self, state: dict, pnl: float, close_lots: float, risk_usd: float):
        state['capital']           = round(state.get('capital', 0) + pnl, 2)
        state['available_capital'] = round(state.get('available_capital', 0) + pnl + risk_usd, 2)
        state['total_pnl']         = round(state.get('total_pnl', 0) + pnl, 2)
        state['daily_pnl']         = round(state.get('daily_pnl', 0) + pnl, 2)
        if pnl > 0:
            state['daily_closed_pnl'] = round(state.get('daily_closed_pnl', 0.0) + pnl, 2)
        if pnl < 0:
            state['daily_losses'] = state.get('daily_losses', 0) + 1

    def _pre_rollover_guard(self):
        """Warn at 21:55 UTC; close trades with tight SL before spread explodes."""
        from forex_engine.forex_instruments import INSTRUMENTS
        state = self._load_state()
        for trade in state.get('open_trades', []):
            sym    = trade['symbol']
            price  = self._connector.get_price(sym)
            spread = self._connector.get_spread(sym)
            if price is None:
                continue
            sl     = trade.get('current_sl', trade.get('stop_loss', 0))
            sl_dist = abs(price - sl)
            max_spd = INSTRUMENTS.get(sym, {}).get('max_spread', 999)
            danger  = spread is not None and sl_dist < (max_spd * 3)
            logger.info(
                f"ROLLOVER GUARD {sym}: SL_dist={sl_dist:.4f} danger={danger}"
            )
            if danger:
                ticket = trade.get('ticket', 0)
                if ticket:
                    self._connector.close_position(sym, ticket, trade['lots'], trade['direction'])
