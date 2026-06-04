# forex_engine/trade/exit_manager.py
# Exit event processing — SL/T1/T2/T3/MAE/TIME exits, partial close logic.

import json
import os
import threading
from datetime import datetime
from typing import Optional
from utils.logger import logger
from forex_engine.forex_instruments import INSTRUMENTS, FTMO_RISK_GUARD


_LOCK = threading.Lock()


def compute_pnl(symbol: str, direction: str, entry: float, exit_px: float,
                lots: float, max_spread: float = 0.0) -> float:
    """Dollar PnL for a close, accounting for spread cost."""
    cfg           = INSTRUMENTS.get(symbol, {})
    contract_size = cfg.get('contract_size', 100000)
    dist          = (exit_px - entry) if direction == 'BULLISH' else (entry - exit_px)
    raw           = round(lots * contract_size * dist, 2)
    spd_cost      = round(max_spread * contract_size * lots, 2)
    return round(raw - spd_cost, 2)


def process_exit_event(state: dict, trade: dict, event_type: str,
                       exit_price: float, close_lots: float,
                       save_fn) -> dict:
    """
    Apply an exit event to the state dict and save.
    Returns an event dict suitable for alert formatting.
    """
    symbol    = trade['symbol']
    direction = trade['direction']
    entry     = trade['entry_price']
    lots      = trade['lots']
    booked    = len(trade.get('targets_hit', []))
    t1_was_be = (not (round(lots / 3, 2) >= INSTRUMENTS.get(symbol, {}).get('min_lot', 0.01))
                 and 'T1' in trade.get('targets_hit', []))

    cfg       = INSTRUMENTS.get(symbol, {})
    max_spd   = cfg.get('max_spread', 0.0)

    pnl = compute_pnl(symbol, direction, entry, exit_price, close_lots, max_spd)

    if event_type in ('SL', 'T3', 'MAE_EXIT', 'TIME_EXIT'):
        sl_dist_orig = abs(entry - trade.get('stop_loss', entry))
        if sl_dist_orig > 0:
            move   = (exit_price - entry) if direction == 'BULLISH' else (entry - exit_price)
            act_rrr = round(move / sl_dist_orig, 2)
        else:
            act_rrr = 0.0

        total_pnl = round(trade.get('pnl_usd', 0) + pnl, 2)
        trade['status']     = 'CLOSED'
        trade['exit_time']  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        trade['exit_price'] = exit_price
        trade['pnl_usd']    = total_pnl
        trade['exit_reason']= event_type
        trade['actual_rrr'] = act_rrr

        state['open_trades']  = [t for t in state['open_trades'] if t['id'] != trade['id']]
        state['closed_trades'].append(trade)
        _apply_pnl(state, pnl, close_lots, trade.get('risk_usd', 0))

    elif event_type == 'T1_BE':
        trade['targets_hit'].append('T1')

    elif event_type in ('T1', 'T2', 'T3_PARTIAL'):
        trade['targets_hit'].append(event_type.replace('_PARTIAL', ''))
        trade['pnl_usd'] = round(trade.get('pnl_usd', 0) + pnl, 2)
        if event_type == 'T2' and t1_was_be:
            # T1 was BE-only, T2 closes everything
            trade['status']     = 'CLOSED'
            trade['exit_time']  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            trade['exit_price'] = exit_price
            trade['exit_reason']= 'T2'
            state['open_trades']  = [t for t in state['open_trades'] if t['id'] != trade['id']]
            state['closed_trades'].append(trade)
        _apply_pnl(state, pnl, 0, 0)

    if state.get('daily_pnl', 0) > state.get('best_day_pnl', 0.0):
        state['best_day_pnl'] = state['daily_pnl']
    state['peak_capital'] = max(state.get('peak_capital', 0), state.get('capital', 0))

    save_fn(state)

    return {
        'type'       : event_type,
        'trade'      : trade,
        'price'      : exit_price,
        'pnl'        : pnl,
        'close_lots' : close_lots,
    }


def _apply_pnl(state: dict, pnl: float, close_lots: float, risk_usd: float):
    state['capital']           = round(state.get('capital', 0) + pnl, 2)
    state['available_capital'] = round(state.get('available_capital', 0) + pnl + risk_usd, 2)
    state['total_pnl']         = round(state.get('total_pnl', 0) + pnl, 2)
    state['daily_pnl']         = round(state.get('daily_pnl', 0) + pnl, 2)
    if pnl > 0:
        state['daily_closed_pnl'] = round(state.get('daily_closed_pnl', 0.0) + pnl, 2)
    if pnl < 0:
        state['daily_losses'] = state.get('daily_losses', 0) + 1


def manual_exit_trade(state: dict, trade_id: str, exit_price: float,
                      save_fn) -> Optional[dict]:
    """
    Sync a manual MT5 exit into paper/live state.
    Call after user closes a position directly in MT5 terminal.
    Returns exit event dict or None if trade not found.
    """
    for trade in list(state.get('open_trades', [])):
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

        state['open_trades']  = [t for t in state['open_trades'] if t['id'] != trade_id]
        state['closed_trades'].append(trade)
        _apply_pnl(state, pnl, rem_lots, trade.get('risk_usd', 0))

        if state.get('daily_pnl', 0) > state.get('best_day_pnl', 0.0):
            state['best_day_pnl'] = state['daily_pnl']
        state['peak_capital'] = max(state.get('peak_capital', 0), state.get('capital', 0))

        save_fn(state)
        return {'type': 'MANUAL', 'trade': trade, 'price': exit_price, 'pnl': pnl}

    return None
