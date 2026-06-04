# forex_engine/prop_firms/gft/gft_5k_2step.py
# GFT $5K 2-Step GOAT — complete isolated trade pipeline.
#
# Architecture:
#   MT5Connector (GFT credentials) → signal scanner → guards → lot calc → order → monitor
#
# State:   data/gft_5k/state.json  (isolated from FTMO and Instant Pro)
# Alerts:  [GFT-2STEP] prefix via communications.gft_bot.send_alert
# Magic:   GFT_2STEP_MAGIC env var — MUST be set as a fixed integer in .env

import os
import uuid
import time
import random
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

from utils.logger import logger
from utils.emergency_stop import is_emergency_stop_active  # REQ-3
from forex_engine.forex_instruments import INSTRUMENTS
from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE, get_risk_pct, is_kz_active
from forex_engine.prop_firms.gft.gft_phase_tracker import (
    load_state, _save, reset_daily_if_needed, advance_phase_if_complete, get_summary
)
from forex_engine.prop_firms.gft.gft_risk_rules import get_risk_mode, can_open_trade
from forex_engine.prop_firms.gft.gft_symbol_guard import filter_symbols
from forex_engine.prop_firms.gft.gft_anti_hft_guard import AntiHFTGuard
from forex_engine.prop_firms.gft.gft_anti_hedge_guard import check_no_hedge
from forex_engine.trade.lot_calculator import calc_lot_size, dollar_risk, gft_lot_modifier
from forex_engine.trade.sl_tp_manager import (
    adjust_for_fill, breakeven_trigger_price, mae_exit_price
)
from forex_engine.trade.duplicate_guard import DuplicateGuard
from forex_engine.scanner.signal_scanner import scan_setup, is_in_kill_zone, in_rollover_window
from forex_engine.scanner.liquidity_sweep import sweep_confirmed as _sweep_ok
from forex_engine.scanner.structure_scanner import get_h1_bias, get_h4_bias
from forex_engine.scanner.setup_scorer import score_aplus_similarity, lot_boost_factor
from forex_engine.data.slippage_tracker import SlippageTracker
from ml_engine.memory.gft_shadow_recommendation import recommend_shadow_for_candidate
from ml_engine.memory.gft_soft_gate import evaluate_soft_gate_and_log
from settings import CB6_GFT_HARD_ENFORCEMENT_ENABLED

_P           = GFT_2STEP_PROFILE
_STATE_LOCK  = threading.Lock()

_magic_raw = os.getenv('GFT_2STEP_MAGIC', '').strip()
if not _magic_raw:
    raise RuntimeError(
        "GFT_2STEP_MAGIC is not set in .env — refusing to start with a random magic number.\n"
        "A random magic means MT5 cannot identify this bot's positions after a restart,\n"
        "leaving open trades unmonitored and risking duplicate entries.\n"
        "Fix: add GFT_2STEP_MAGIC=<your_fixed_integer> to .env, then restart."
    )
_GFT_MAGIC = int(_magic_raw)

# Project root — 4 dirs up from this file (forex_engine/prop_firms/gft/gft_5k_2step.py)
_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

SYMBOL_MIN_SCORE = {
    'XAUUSD': 11,
    'XAGUSD': 11,
    'USOIL' : 11,
}

GFT_ALERT_PREFIX = _P['alert_prefix']


def _send(msg: str):
    try:
        from communications.gft_bot import send_alert
        send_alert(f"{GFT_ALERT_PREFIX} {msg}")
    except Exception:
        logger.info(f"{GFT_ALERT_PREFIX} {msg[:120]}")


# ── Telegram alert formatters ───────────────────────────────────────────────────

def _format_entry_alert(setup: dict, lots: float, risk_usd: float,
                        ticket: int = 0, phase: str = 'phase_1',
                        risk_mode: str = 'normal',
                        sim_ratio: float = 0.0, boost: float = 1.0) -> str:
    sig   = setup['entry_signal']
    sym   = setup['symbol']
    label = INSTRUMENTS.get(sym, {}).get('label', sym)
    dlab  = 'LONG (BUY)' if setup['direction'] == 'BULLISH' else 'SHORT (SELL)'
    ut    = setup.get('ut_bot', {})
    liq   = setup.get('liq_sweep')
    sc    = setup.get('sweep_confirmed', False)

    sweep_line = (
        f"Liq Sweep  : {'LOW swept ✅' if liq and liq.get('sweep_type') == 'LOW_SWEEP' else 'HIGH swept ✅'} "
        f"({liq.get('candles_ago', '?')} candles ago)\n"
        if sc and liq else
        f"Liq Sweep  : {liq.get('sweep_type', 'None')} (opposite dir)\n"
        if liq else "Liq Sweep  : None\n"
    )
    ob     = setup.get('ob')
    ob_l   = (f"Order Block: {ob['type']} {ob['ob_low']:.5f}–{ob['ob_high']:.5f} ✅\n"
              if ob else "Order Block: Not detected\n")
    sim_l  = (f"A+ Match   : {sim_ratio:.0%} ⭐ — lots boosted {boost}×\n"
              if sim_ratio >= 0.55 else
              f"A+ Match   : {sim_ratio:.0%}\n" if sim_ratio > 0 else "")
    mode_l = f"Risk Mode  : {risk_mode.upper()}\n" if risk_mode != 'normal' else ""
    tk_l   = f"MT5 Ticket : #{ticket}\n" if ticket else ""

    return (
        f"<b>CB6 QUANTUM — GFT 2-STEP {label} [{setup['confluence']}/15]</b>\n"
        f"Phase      : {phase.upper().replace('_', ' ')}\n\n"
        f"Direction  : {dlab}\n"
        f"Time       : {datetime.now().strftime('%H:%M:%S IST')}\n\n"
        f"<b>STRUCTURE</b>\n"
        f"{sweep_line}"
        f"DOL        : {sig['dol_level']}\n"
        f"MSS        : {sig['mss_level']} ({setup['mss_type']})\n"
        f"FVG Zone   : {sig['fvg_low']} – {sig['fvg_high']}\n"
        f"FVG Status : {'IN ZONE ✅' if setup.get('in_fvg') else 'APPROACHING'}\n"
        f"{ob_l}"
        f"UT Bot     : {ut.get('trend','?')} | {'✅' if ut.get('aligned') else '⚠️'}\n\n"
        f"<b>TRADE PLAN</b>\n"
        f"Entry      : {sig['entry']}\n"
        f"SL         : {sig['stop_loss']}\n"
        f"T1 (1/3)   : {sig['target1']}  (1:2R)\n"
        f"T2 (1/3)   : {sig['target2']}  (1:3R)\n"
        f"T3 (1/3)   : {sig['target3']}  (DOL)\n"
        f"RR         : 1:{sig['rr_ratio']}\n"
        f"Lots       : {lots}  |  Risk ${risk_usd}\n"
        f"{sim_l}"
        f"{mode_l}"
        f"{tk_l}"
    )


def _format_exit_alert(event: dict, phase: str = 'phase_1') -> str:
    t     = event['trade']
    sym   = t.get('symbol', 'XAGUSD')
    label = INSTRUMENTS.get(sym, {}).get('label', sym)
    pnl   = event['pnl']
    sign  = '+' if pnl >= 0 else ''
    etype = event['type']
    dlab  = 'LONG' if t.get('direction') == 'BULLISH' else 'SHORT'

    result_map = {
        'SL'      : '🔴 STOP LOSS HIT',
        'T1_BE'   : '🟡 T1 HIT — SL moved to BE',
        'T1'      : '🟡 T1 HIT — 1/3 booked, SL→BE',
        'T2'      : '🟢 T2 HIT — 2/3 booked',
        'T3'      : '✅ T3 HIT — full target (DOL)',
        'MAE_EXIT': '⏱️ MAE EXIT — cut before SL',
        'TIME_EXIT': '⏱️ TIME EXIT — no progress',
    }
    result = result_map.get(etype, f'{etype} HIT')

    state    = load_state()
    daily_pnl = state.get('daily_pnl', 0.0)

    return (
        f"<b>CB6 QUANTUM — GFT 2-STEP {label}</b>\n"
        f"{result}\n\n"
        f"Phase      : {phase.upper().replace('_', ' ')}\n"
        f"Direction  : {dlab}\n"
        f"Entry      : {t['entry_price']}\n"
        f"Exit       : {event['price']}\n"
        f"PnL        : {sign}${pnl:.2f}\n"
        f"Daily PnL  : {'+' if daily_pnl >= 0 else ''}${daily_pnl:.2f}\n"
        f"Time       : {datetime.now().strftime('%H:%M:%S IST')}\n"
        f"Trade      : {t['id']}"
    )


def _format_phase_alert(phase: str, message: str) -> str:
    return (
        f"<b>🎯 GFT 2-STEP — PHASE COMPLETE</b>\n\n"
        f"Phase  : {phase.upper().replace('_', ' ')}\n"
        f"Result : {message}\n"
        f"Time   : {datetime.now().strftime('%H:%M:%S IST')}\n\n"
        f"{'🚀 Moving to Phase 2!' if phase == 'phase_1' else '✅ FUNDED! Congratulations!'}"
    )


# ── Trade state management ──────────────────────────────────────────────────────

def _open_trade_state(setup: dict, lots: float, ticket: int = 0) -> Optional[dict]:
    with _STATE_LOCK:
        state = load_state()
        state = reset_daily_if_needed(state)

        # Gate checks
        ok, reason = can_open_trade(state, setup['symbol'], setup['direction'])
        if not ok:
            logger.info(f"GFT 2-STEP {setup['symbol']}: trade blocked — {reason}")
            return None

        sig     = setup['entry_signal']
        sl_dist = abs(sig['entry'] - sig['stop_loss'])
        t2_dist = abs(sig['target2'] - sig['entry'])
        exp_rrr = round(t2_dist / sl_dist, 2) if sl_dist > 0 else 0.0

        trade = {
            'id'             : str(uuid.uuid4())[:8],
            'ticket'         : ticket,
            'symbol'         : setup['symbol'],
            'direction'      : setup['direction'],
            'lots'           : lots,
            'entry_price'    : sig['entry'],
            'stop_loss'      : sig['stop_loss'],
            'current_sl'     : sig['stop_loss'],
            'target1'        : sig['target1'],
            'target2'        : sig['target2'],
            'target3'        : sig['target3'],
            'risk_usd'       : sig.get('risk_usd', 0),
            'rr_ratio'       : sig['rr_ratio'],
            'expected_rrr'   : exp_rrr,
            'actual_rrr'     : 0.0,
            'confluence'     : setup['confluence'],
            'mss_type'       : setup.get('mss_type', 'BOS'),
            'entry_time'     : datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'entry_reason'   : setup.get('entry_reason', ''),
            'spread_at_entry': setup.get('spread_at_entry', 0.0),
            'risk_mode'      : setup.get('risk_mode', 'normal'),
            'sim_ratio'      : setup.get('sim_ratio', 0.0),
            'lot_boost'      : setup.get('lot_boost', 1.0),
            'targets_hit'    : [],
            'be_triggered'   : False,
            'pnl_usd'        : 0.0,
            'status'         : 'OPEN',
            'exit_reason'    : None,
            'phase'          : state.get('current_phase', 'phase_1'),
        }

        state['open_trades'].append(trade)
        state['daily_trades'] += 1
        state['available_capital'] -= sig.get('risk_usd', 0)
        _save(state)
        return trade


def _rollback_trade(trade_id: str, risk_usd: float):
    with _STATE_LOCK:
        state = load_state()
        before = len(state['open_trades'])
        state['open_trades'] = [t for t in state['open_trades'] if t['id'] != trade_id]
        if len(state['open_trades']) < before:
            state['daily_trades']      = max(0, state['daily_trades'] - 1)
            state['available_capital'] = round(
                state.get('available_capital', state['capital']) + risk_usd, 2
            )
            _save(state)


def _update_ticket(trade_id: str, ticket: int):
    with _STATE_LOCK:
        state = load_state()
        for t in state['open_trades']:
            if t['id'] == trade_id:
                t['ticket'] = ticket
                _save(state)
                return


def _update_fill(trade_id: str, fill: float, sl: float,
                 t1: float, t2: float, t3: float, risk_usd: float):
    with _STATE_LOCK:
        state = load_state()
        for t in state['open_trades']:
            if t['id'] == trade_id:
                t.update({'entry_price': fill, 'stop_loss': sl, 'current_sl': sl,
                          'target1': t1, 'target2': t2, 'target3': t3,
                          'risk_usd': risk_usd})
                _save(state)
                return


def manual_exit_trade(trade_id: str, exit_price: float):
    """Sync a manual MT5 exit into GFT 2-Step state."""
    with _STATE_LOCK:
        state = load_state()
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
            state['open_trades'] = [t for t in state['open_trades'] if t['id'] != trade_id]
            state['closed_trades'].append(trade)
            _apply_pnl(state, pnl, trade.get('risk_usd', 0))
            _save(state)
            return {'type': 'MANUAL', 'trade': trade, 'price': exit_price, 'pnl': pnl}
        return None


def _apply_pnl(state: dict, pnl: float, risk_usd: float = 0):
    state['capital']           = round(state.get('capital', 0) + pnl, 2)
    state['available_capital'] = round(state.get('available_capital', 0) + pnl + risk_usd, 2)
    state['total_pnl']         = round(state.get('total_pnl', 0) + pnl, 2)
    state['daily_pnl']         = round(state.get('daily_pnl', 0) + pnl, 2)
    if pnl > 0:
        state['daily_closed_pnl'] = round(state.get('daily_closed_pnl', 0.0) + pnl, 2)
    if pnl < 0:
        state['daily_losses'] = state.get('daily_losses', 0) + 1
    if state['daily_pnl'] > state.get('best_day_pnl', 0.0):
        state['best_day_pnl'] = state['daily_pnl']
    state['peak_capital'] = max(state.get('peak_capital', 0), state.get('capital', 0))


# ── Position monitor ────────────────────────────────────────────────────────────

def _check_exits(connector, symbol: str) -> list:
    """Evaluate open trades for SL/TP hits. Returns list of exit events."""
    with _STATE_LOCK:
        state   = load_state()
        events  = []
        cfg     = INSTRUMENTS.get(symbol, {})
        cs      = cfg.get('contract_size', 100000)
        min_lot = cfg.get('min_lot', 0.01)
        max_spd = cfg.get('max_spread', 0.0)

        price = connector.get_price(symbol)
        if price is None:
            return []

        for trade in list(state.get('open_trades', [])):
            if trade.get('symbol') != symbol:
                continue

            direction  = trade['direction']
            entry      = trade['entry_price']
            sl         = trade['current_sl']
            orig_sl    = trade.get('stop_loss', sl)
            t1, t2, t3 = trade['target1'], trade['target2'], trade['target3']
            lots       = trade['lots']
            booked     = len(trade.get('targets_hit', []))
            partial    = round(lots / 3, 2)
            can_part   = partial >= min_lot
            t1_be      = (not can_part) and ('T1' in trade.get('targets_hit', []))

            def _pnl(px, cl):
                dist = (px - entry) if direction == 'BULLISH' else (entry - px)
                return round(cl * cs * dist - max_spd * cs * cl, 2)

            def _rem():
                return lots if t1_be else round(lots * (3 - booked) / 3, 2)

            hit = None; exit_px = price

            # BE trigger
            if not trade.get('be_triggered') and 'T1' not in trade.get('targets_hit', []):
                be_px = breakeven_trigger_price(entry, t1, direction, 0.40)
                if ((direction == 'BULLISH' and price >= be_px) or
                        (direction == 'BEARISH' and price <= be_px)):
                    trade['current_sl']   = entry
                    trade['be_triggered'] = True
                    events.append({'type': 'BE_TRIGGER', 'trade': trade,
                                   'price': price, 'pnl': 0.0, 'close_lots': 0.0})
                    sl = entry

            # MAE
            if not hit and not trade.get('targets_hit') and not trade.get('be_triggered'):
                mae_px = mae_exit_price(entry, orig_sl, direction, 0.85)
                if ((direction == 'BULLISH' and price <= mae_px) or
                        (direction == 'BEARISH' and price >= mae_px)):
                    hit = 'MAE_EXIT'

            # Time exit — 2 hours
            if not hit and 'T1' not in trade.get('targets_hit', []):
                et = trade.get('entry_time', '')
                if et:
                    try:
                        elapsed = (datetime.now() -
                                   datetime.strptime(et, '%Y-%m-%d %H:%M:%S')
                                   ).total_seconds() / 60
                        if elapsed >= 120:   # 2 hours
                            hit = 'TIME_EXIT'
                    except Exception:
                        pass

            # SL / T1 / T2 / T3
            if direction == 'BULLISH':
                if not hit and price <= sl:                                        hit = 'SL';   exit_px = sl
                elif not hit and 'T3' not in trade['targets_hit'] and price >= t3: hit = 'T3';   exit_px = t3
                elif not hit and 'T2' not in trade['targets_hit'] and price >= t2: hit = 'T2';   exit_px = t2; trade['current_sl'] = entry
                elif not hit and 'T1' not in trade['targets_hit'] and price >= t1: hit = 'T1' if can_part else 'T1_BE'; exit_px = t1; trade['current_sl'] = entry
            else:
                if not hit and price >= sl:                                        hit = 'SL';   exit_px = sl
                elif not hit and 'T3' not in trade['targets_hit'] and price <= t3: hit = 'T3';   exit_px = t3
                elif not hit and 'T2' not in trade['targets_hit'] and price <= t2: hit = 'T2';   exit_px = t2; trade['current_sl'] = entry
                elif not hit and 'T1' not in trade['targets_hit'] and price <= t1: hit = 'T1' if can_part else 'T1_BE'; exit_px = t1; trade['current_sl'] = entry

            if hit is None:
                continue

            if hit == 'T1_BE':
                trade['targets_hit'].append('T1')
                events.append({'type': 'T1_BE', 'trade': trade, 'price': exit_px,
                               'pnl': 0.0, 'close_lots': 0.0})
            elif hit in ('SL', 'T3', 'MAE_EXIT', 'TIME_EXIT'):
                rem   = _rem()
                pnl   = _pnl(exit_px, rem)
                total = round(trade.get('pnl_usd', 0) + pnl, 2)
                sd    = abs(entry - orig_sl)
                move  = (exit_px - entry) if direction == 'BULLISH' else (entry - exit_px)
                rrr   = round(move / sd, 2) if sd > 0 else 0.0
                trade.update({
                    'status': 'CLOSED', 'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'exit_price': exit_px, 'pnl_usd': total, 'exit_reason': hit, 'actual_rrr': rrr,
                })
                state['open_trades']  = [t for t in state['open_trades'] if t['id'] != trade['id']]
                state['closed_trades'].append(trade)
                _apply_pnl(state, pnl, trade.get('risk_usd', 0))
                events.append({'type': hit, 'trade': trade, 'price': exit_px,
                               'pnl': pnl, 'close_lots': rem})
            elif hit == 'T2' and t1_be:
                rem  = _rem()
                pnl  = _pnl(exit_px, rem)
                total= round(trade.get('pnl_usd', 0) + pnl, 2)
                sd   = abs(entry - orig_sl)
                move = (exit_px - entry) if direction == 'BULLISH' else (entry - exit_px)
                rrr  = round(move / sd, 2) if sd > 0 else 0.0
                trade.update({
                    'status': 'CLOSED', 'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'exit_price': exit_px, 'pnl_usd': total, 'exit_reason': 'T2', 'actual_rrr': rrr,
                })
                trade['targets_hit'].append('T2')
                state['open_trades']  = [t for t in state['open_trades'] if t['id'] != trade['id']]
                state['closed_trades'].append(trade)
                _apply_pnl(state, pnl, trade.get('risk_usd', 0))
                events.append({'type': 'T2', 'trade': trade, 'price': exit_px,
                               'pnl': pnl, 'close_lots': rem})
            else:
                pnl = _pnl(exit_px, partial)
                trade['targets_hit'].append(hit)
                trade['pnl_usd'] = round(trade.get('pnl_usd', 0) + pnl, 2)
                _apply_pnl(state, pnl)
                events.append({'type': hit, 'trade': trade, 'price': exit_px,
                               'pnl': pnl, 'close_lots': partial})

        if events:
            _save(state)
        return events


# ── Main GFT 2-Step Worker ──────────────────────────────────────────────────────

class GFT2StepWorker:
    """
    Full isolated GFT $5K 2-Step trade engine.
    Uses its own MT5 connector, state file, alert prefix, and guard set.
    """

    def __init__(self, paper: bool = True):
        self._paper = paper
        # ── Multi-account terminal isolation ────────────────────────────────────
        # Build connector via GFT adapter — passes the GFT terminal path to
        # mt5.initialize() so this process connects to MT5_GFT_5K exclusively.
        # This subprocess never shares an MT5 session with the FTMO subprocess.
        from forex_engine.accounts.gft_adapter import build_gft_connector
        self._connector = build_gft_connector(paper=paper)
        self._hft_guard = AntiHFTGuard()
        self._dedup     = DuplicateGuard(
            persist_path=os.path.join(_ROOT, 'data', 'gft_5k', 'dedup.json')
        )
        self._slip      = SlippageTracker()
        self._candles   : dict = {}
        self._locks     = {s: threading.Lock() for s in _P['enabled_symbols']}
        self._running   = False
        self._ema_alerted: dict = {s: set() for s in _P['enabled_symbols']}

        logger.info(
            f"GFT 2-Step worker initialized — "
            f"paper={paper} symbols={_P['enabled_symbols']} magic={_GFT_MAGIC}"
        )

    def on_closed_candle(self, symbol: str, df):
        if symbol not in _P['enabled_symbols']:
            return
        self._candles[symbol] = df
        threading.Thread(
            target=self._scan, args=(symbol,),
            daemon=True, name=f"GFT2_{symbol}"
        ).start()

    def _scan(self, symbol: str):
        if not self._locks[symbol].acquire(blocking=False):
            return
        try:
            self._run(symbol)
        except Exception as e:
            logger.error(f"GFT 2-Step scan({symbol}): {e}")
        finally:
            self._locks[symbol].release()

    def _run(self, symbol: str):
        # REQ-3: Emergency stop — abort scan immediately if flag is active
        if is_emergency_stop_active():
            logger.warning(
                f"EMERGENCY_STOP.flag active — GFT 2-Step scan skipped ({symbol})"
            )
            return

        utc_hour = datetime.now(timezone.utc).hour
        today    = datetime.now().strftime('%Y-%m-%d')

        # Rollover / KZ gate
        if in_rollover_window(utc_hour):
            return
        if not is_kz_active(utc_hour):
            logger.debug(f"GFT 2-Step {symbol}: outside KZ ({utc_hour}:xx UTC)")
            return

        df = self._candles.get(symbol)
        if df is None or len(df) < 40:
            return

        setup = scan_setup(df, symbol, min_rr=2.0)
        if not setup:
            return
        # Shadow recommendation — fires for ALL scanner-passing setups (before HTF/score rejections)
        # so that we log what the reco engine would say even on setups that later get filtered.
        try:
            _shadow_setup = dict(setup)
            _shadow_setup.setdefault('session', f'UTC_{utc_hour:02d}')
            _shadow_setup.setdefault('window', f'UTC_{utc_hour:02d}')
            _shadow_state = load_state()
            recommend_shadow_for_candidate(
                setup=_shadow_setup,
                state=_shadow_state if isinstance(_shadow_state, dict) else {},
                engine='gft_2step_worker',
                market='forex',
                daily_loss_limit_abs=_P['official_daily_loss_usd'],
                max_drawdown_abs=_P['official_max_loss_usd'],
                profit_target_abs=400.0,
                max_trades_per_day=_P['max_trades_per_day'],
            )
        except Exception:
            pass

        sig       = setup['entry_signal']
        direction = setup['direction']
        score     = setup['confluence']
        mss_type  = setup.get('mss_type', 'BOS')

        # HTF bias checks
        h1_bias = get_h1_bias(self._connector, symbol)
        h4_bias = get_h4_bias(self._connector, symbol)

        choch_ok = mss_type == 'CHOCH' and score >= 11
        if h4_bias not in ('RANGING', direction):
            logger.info(f"GFT 2-Step {symbol}: H4 block — H4={h4_bias} setup={direction} mss={mss_type} score={score}")
            return
        if h1_bias not in ('RANGING', direction) and not choch_ok:
            logger.info(f"GFT 2-Step {symbol}: H1 block — H1={h1_bias} setup={direction}")
            return

        # Silver Asia SELL block — A+ score (≥13) overrides
        _gft_asia_aplus = (score + (1 if mss_type == 'CHOCH' else 0)) >= 13
        if symbol == 'XAGUSD' and utc_hour < 7 and direction == 'BEARISH':
            if not _gft_asia_aplus:
                logger.info(f"GFT 2-Step XAGUSD: Asia SELL block (hour={utc_hour} UTC) — need score ≥13 to override")
                return
            logger.info(f"GFT 2-Step XAGUSD: Asia SELL block OVERRIDDEN — A+ score {score}/15 (hour={utc_hour})")

        # ── Sweep quality assessment (soft filter — mirrors NSE/FTMO philosophy)
        # Bugs fixed:
        #   Bug 1: hard `return` on no-sweep replaced with score-gate +2.
        #   Bug 2: inline level_state == 'SWEPT' replaced with _sweep_ok helper
        #          (lenient: allows level_state None or SWEPT).
        #   Bug 3: confidence < 45 hard block removed — clean wick close-back-inside
        #          IS the sweep per ICT; ATR/volume gates kill quiet-KZ entries.
        liq      = setup.get('liq_sweep')
        sweep_ok = _sweep_ok(liq, direction, max_candles_ago=15, min_confidence=0)
        if not sweep_ok:
            logger.info(
                f"GFT 2-Step {symbol}: NO CONFIRMED SWEEP — score gate raised +2. "
                f"score={score}/18 (CHoCH+FVG must compensate)"
            )

        if not setup.get('in_fvg'):
            logger.info(f"GFT 2-Step {symbol}: NOT IN FVG")
            return

        # Score gate
        sym_min  = SYMBOL_MIN_SCORE.get(symbol, 11)
        eff_sc   = score + (1 if mss_type == 'CHOCH' else 0)
        min_sc   = sym_min + (0 if is_kz_active(utc_hour) else 1)
        if h1_bias == 'RANGING':
            min_sc += 1
        # No gate penalty for absent sweep — scoring already penalises naturally
        # (sweep_confirmed=False ⟹ −2 confluence). Double-penalising freezes engine.
        if eff_sc < min_sc:
            logger.info(f"GFT 2-Step {symbol}: score {eff_sc} < {min_sc} — skip")
            return

        # Dedup
        fvg_low = sig['fvg_low']
        if self._dedup.is_duplicate(symbol, direction, fvg_low):
            return

        # State checks
        state = load_state()
        state = reset_daily_if_needed(state)
        ok, reason = can_open_trade(state, symbol, direction)
        if not ok:
            logger.info(f"GFT 2-Step {symbol}: BLOCKED — {reason}")
            return

        # HFT guard
        ok, reason = self._hft_guard.can_enter()
        if not ok:
            logger.info(f"GFT 2-Step {symbol}: HFT GUARD — {reason}")
            return

        # Risk mode
        risk_mode, risk_reason = get_risk_mode(state)
        if risk_mode == 'paused':
            logger.info(f"GFT 2-Step {symbol}: PAUSED — {risk_reason}")
            return

        is_aplus = eff_sc >= 13
        if risk_mode == 'aplus_only' and not is_aplus:
            logger.info(f"GFT 2-Step {symbol}: A+ only mode — {risk_reason}")
            return

        # Soft gate — fires here, after all cheap rejections, with accurate state and risk estimate.
        # Moving it here (vs before HTF checks) means it only runs on setups that will actually trade,
        # and gets a real projected_risk_usd instead of 0.0.
        _gate_decision = None
        try:
            _cap_now = float(state.get('capital') or _P['account_size'])
            _proj_risk = _cap_now * get_risk_pct('reduced' if risk_mode == 'reduced' else 'normal') / 100.0
            _gate_result = evaluate_soft_gate_and_log(
                setup=setup,
                state=state,
                engine='gft_2step_worker',
                market='forex',
                daily_loss_limit_abs=_P['official_daily_loss_usd'],
                max_drawdown_abs=_P['official_max_loss_usd'],
                max_trades_per_day=_P['max_trades_per_day'],
                projected_risk_usd=_proj_risk,
            )
            if isinstance(_gate_result, dict):
                _gate_decision = _gate_result.get('gate_decision')
        except Exception:
            pass

        # A+ similarity
        df15      = self._candles.get(symbol)
        sim_ratio, _ = score_aplus_similarity(setup, df15, h4_bias, h1_bias, utc_hour)
        boost     = lot_boost_factor(sim_ratio)

        # ── Conviction evaluation (Phase 7) ──────────────────────────────────
        _gft_session = (
            "london"   if 7  <= utc_hour < 12 else
            "new_york" if 16 <= utc_hour < 20 else
            "off_session"
        )
        setup['sim_ratio'] = sim_ratio   # ensure technical scorer reads it
        _gft_conviction = None
        try:
            from utils.conviction_engine import evaluate_conviction as _ev_gft
            _gft_conviction = _ev_gft(
                market    = 'FOREX',
                symbol    = symbol,
                direction = direction,
                setup     = setup,
                session   = _gft_session,
                regime_4h = setup.get('market_regime'),
            )
            logger.info(
                f"GFT 2-Step {symbol}: conviction={_gft_conviction.conviction_score:.0f} "
                f"grade={_gft_conviction.conviction_grade} "
                f"mult={_gft_conviction.recommended_risk_multiplier}×"
            )
            if not _gft_conviction.should_trade():
                logger.info(
                    f"GFT 2-Step {symbol}: CONVICTION BLOCK — "
                    f"grade={_gft_conviction.conviction_grade} "
                    f"score={_gft_conviction.conviction_score:.0f} "
                    f"({_gft_conviction.hard_block_reason or 'grade D — no edge'})"
                )
                _send(
                    f"⛔ <b>GFT CONVICTION BLOCK — {symbol}</b>\n\n"
                    f"Grade  : {_gft_conviction.conviction_grade} "
                    f"({_gft_conviction.conviction_score:.0f}/100)\n"
                    f"Reason : {_gft_conviction.hard_block_reason or 'Grade D — no edge'}\n"
                    f"Setup  : {direction} score={score}/15 sim={sim_ratio:.0%}\n"
                    f"Time   : {datetime.now().strftime('%H:%M:%S IST')}"
                )
                return
        except Exception as _conv_e:
            logger.debug(f"GFT 2-Step {symbol}: conviction eval skipped: {_conv_e}")

        # Risk pct
        base_pct = get_risk_pct('reduced' if risk_mode == 'reduced' else 'normal')
        if risk_mode == 'normal' and boost > 1.0 and is_aplus:
            risk_pct = min(base_pct * boost, _P['risk_max_pct'])
        else:
            risk_pct = base_pct

        # Lot size
        # REQ-6: Pull live MT5 equity for accurate lot sizing.
        # Falls back to state-file capital only if MT5 is unreachable.
        capital = state.get('capital', _P['account_size'])
        if not self._paper:
            try:
                _mt5_eq = self._connector.get_equity()
                if _mt5_eq and _mt5_eq > 0:
                    capital = _mt5_eq
                    logger.debug(
                        f"GFT 2-Step {symbol}: lot sizing on live MT5 equity ${_mt5_eq:.2f}"
                    )
            except Exception:
                logger.warning(
                    f"GFT 2-Step {symbol}: MT5 equity fetch failed — "
                    f"using state capital ${capital:.2f}"
                )
        lots    = calc_lot_size(symbol, capital, sig['entry'], sig['stop_loss'], risk_pct)
        lots    = gft_lot_modifier(lots)
        min_lot = INSTRUMENTS.get(symbol, {}).get('min_lot', 0.01)
        if CB6_GFT_HARD_ENFORCEMENT_ENABLED and _gate_decision in ('BLOCK', 'CAUTION'):
            if _gate_decision == 'BLOCK':
                lots = min_lot
                logger.info(f"GFT 2-Step {symbol}: gate BLOCK — lots clamped to minimum {min_lot}")
                _send(f"⚠️ Gate BLOCK on {symbol} — entering at minimum size ({min_lot} lots). Check gft_soft_gate_decisions.jsonl for reason codes.")
            else:
                lots = max(round(lots * 0.5, 2), min_lot)
                logger.info(f"GFT 2-Step {symbol}: gate CAUTION — lots reduced 50% to {lots}")
        if lots < min_lot:
            return

        # ── Conviction lot adjustment (only reduces — never boosts) ───────────
        if _gft_conviction is not None and _gft_conviction.recommended_risk_multiplier < 1.0:
            _gft_conv_mult = _gft_conviction.recommended_risk_multiplier
            lots = max(min_lot, round(lots * _gft_conv_mult, 2))
            logger.info(
                f"GFT 2-Step {symbol}: conviction grade={_gft_conviction.conviction_grade} "
                f"({_gft_conviction.conviction_score:.0f}) → lots ×{_gft_conv_mult} = {lots}"
            )

        risk_usd = dollar_risk(symbol, lots, sig['entry'], sig['stop_loss'])
        sig['risk_usd'] = risk_usd

        setup.update({
            'sim_ratio'      : sim_ratio,
            'lot_boost'      : boost,
            'risk_mode'      : risk_mode,
            'entry_reason'   : (f"{mss_type} score={score}/15 H4={h4_bias} "
                                f"sim={sim_ratio:.0%} boost={boost}×"),
            'spread_at_entry': self._connector.get_spread(symbol) or 0.0,
        })

        # Open state record
        trade = _open_trade_state(setup, lots)
        if not trade:
            return

        self._dedup.mark_seen(symbol, direction, fvg_low)
        self._hft_guard.record_entry()
        ticket = 0

        # MT5 order
        if not self._paper:
            time.sleep(random.uniform(0.05, 0.25))   # GFT fingerprint randomization
            result = self._connector.place_market_order(
                symbol    = symbol,
                direction = 'BUY' if direction == 'BULLISH' else 'SELL',
                lots      = lots,
                sl        = sig['stop_loss'],
                tp        = sig['target2'],
                magic     = _GFT_MAGIC,
            )
            if not result:
                logger.error(f"GFT 2-Step {symbol}: MT5 FAILED — rolling back")
                _rollback_trade(trade['id'], risk_usd)
                _send(f"⚠️ GFT 2-Step MT5 order FAILED for {symbol}. Check AutoTrading.")
                return

            ticket = result.get('ticket', 0)
            fill   = result.get('price', 0.0)
            if ticket:
                _update_ticket(trade['id'], ticket)
            if fill:
                adj = adjust_for_fill(symbol, direction, fill, sig['entry'],
                                      sig['stop_loss'], lots)
                _update_fill(trade['id'], fill, adj['stop_loss'], adj['target1'],
                             adj['target2'], adj['target3'], adj['risk_usd'])
                self._slip.check(symbol, sig['entry'], fill)
                sig.update(adj)
                risk_usd = adj['risk_usd']
                if ticket:
                    try:
                        self._connector.modify_sl(symbol, ticket, adj['stop_loss'])
                    except Exception:
                        # REQ-5: Best-effort SL adjust — log but don't block entry
                        logger.warning(
                            f"GFT 2-Step {symbol}: post-fill modify_sl failed "
                            f"(ticket={ticket}, sl={adj['stop_loss']:.5f})",
                            exc_info=True
                        )

        phase = state.get('current_phase', 'phase_1')
        logger.info(
            f"GFT 2-Step {symbol}: TRADE OPENED {direction} {lots}L "
            f"@ {sig['entry']:.5f} phase={phase}"
        )
        _send(_format_entry_alert(
            setup, lots, risk_usd, ticket=ticket, phase=phase,
            risk_mode=risk_mode, sim_ratio=sim_ratio, boost=boost
        ))

        # ── ML price series (CNN/RNN) ────────────────────────────────────────────
        try:
            _df15 = self._candles.get(symbol)
            if _df15 is not None and len(_df15) >= 5:
                from ml.data_pipeline import save_price_series
                _gft_candle_list = [
                    {'open': float(r['open']), 'high': float(r['high']),
                     'low': float(r['low']),   'close': float(r['close']),
                     'volume': float(r.get('volume', r.get('tick_volume', 0)))}
                    for _, r in _df15.iterrows()
                ]
                save_price_series(
                    trade.get('id', ''), 'forex', 'gft',
                    _gft_candle_list, n_before=50
                )
                logger.debug(f"ML GFT price series saved: {len(_gft_candle_list)} candles")
        except Exception as _ml_e:
            logger.debug(f"ML GFT price series save skipped: {_ml_e}")

        # ── ML data capture ────────────────────────────────────────────────────
        try:
            from ml.forex_collector import record_entry as _ml_gft_entry
            _ml_gft_entry(
                trade     = trade,
                setup     = setup,
                account   = 'gft',
                lots      = lots,
                risk_usd  = risk_usd,
                h1_bias   = h1_bias,
                h4_bias   = h4_bias,
                sim_ratio = sim_ratio,
                lot_boost = boost,
                risk_mode = risk_mode,
            )
        except Exception as _ml_e:
            logger.debug(f"ML GFT entry capture skipped: {_ml_e}")

        # ── ML shadow prediction ────────────────────────────────────────────────
        try:
            from ml.predictor import predict_forex
            _df15 = self._candles.get(symbol)
            _gft_candles_arr = None
            if _df15 is not None and len(_df15) >= 5:
                import numpy as np
                _vol_col = 'volume' if 'volume' in _df15.columns else \
                           'tick_volume' if 'tick_volume' in _df15.columns else None
                if _vol_col:
                    _gft_candles_arr = _df15[['open','high','low','close',_vol_col]].values.astype(float)
                else:
                    _gft_candles_arr = np.column_stack([
                        _df15[['open','high','low','close']].values,
                        np.zeros(len(_df15))
                    ]).astype(float)
            predict_forex(
                trade.get('id', ''),
                {**setup,
                 'h1_bias': h1_bias, 'h4_bias': h4_bias,
                 'aplus_sim_ratio': sim_ratio,
                 'aplus_lot_boost': boost,
                 'is_aplus': 1 if sim_ratio >= 0.55 else 0},
                'gft',
                candles=_gft_candles_arr,
            )
        except Exception as _ml_e:
            logger.debug(f"ML GFT shadow predict skipped: {_ml_e}")

        # ── Trade replay + conviction context capture (Phase 3.5/7) ──────────
        try:
            from utils.trade_replay import capture_entry_context as _cap_gft
            _cap_gft(
                trade_id  = trade.get('id', ''),
                market    = 'FOREX',
                symbol    = symbol,
                direction = direction,
                setup     = setup,
                session   = _gft_session,
            )
        except Exception as _rep_e:
            logger.debug(f"GFT trade replay capture skipped: {_rep_e}")

    # ── Monitor loop ─────────────────────────────────────────────────────────

    def _monitor_loop(self):
        while self._running:
            # REQ-3: Emergency stop — skip entire monitor cycle if flag is active
            if is_emergency_stop_active():
                logger.warning(
                    "EMERGENCY_STOP.flag active — GFT 2-Step monitor cycle skipped"
                )
                time.sleep(15)
                continue
            for sym in _P['enabled_symbols']:
                try:
                    events = _check_exits(self._connector, sym)
                    for ev in events:
                        self._handle_exit(sym, ev)
                except Exception as e:
                    logger.error(f"GFT 2-Step monitor ({sym}): {e}")
            time.sleep(15)  # 15s matches GFT candle poll — ensures SL/TP/drawdown/emergency-stop checked every 15s

    def _handle_exit(self, symbol: str, ev: dict):
        t      = ev['trade']
        ticket = t.get('ticket', 0)
        cl     = ev.get('close_lots', 0.0)
        etype  = ev['type']
        phase  = t.get('phase', 'phase_1')

        # Min hold time check before any close
        if etype in ('T1', 'T1_BE', 'T2', 'T3') and t.get('entry_time'):
            ok, remaining = self._hft_guard.check_min_hold(t['entry_time'])
            if not ok:
                logger.info(f"GFT 2-Step {symbol}: min hold — waiting {remaining}s")
                time.sleep(remaining + 1)

        if not self._paper and ticket and cl > 0:
            if etype == 'T1_BE':
                self._connector.modify_sl(symbol, ticket, t['entry_price'])
            elif etype == 'BE_TRIGGER':
                self._connector.modify_sl(symbol, ticket, t['entry_price'])
            elif etype in ('SL', 'T3', 'T2', 'MAE_EXIT', 'TIME_EXIT'):
                self._connector.close_position(symbol, ticket, cl, t['direction'])
            elif etype == 'T1':
                self._connector.close_position(symbol, ticket, cl, t['direction'])
                self._connector.modify_sl(symbol, ticket, t['entry_price'])

        if etype not in ('BE_TRIGGER',):
            _send(_format_exit_alert(ev, phase=phase))

        # ── ML outcome capture ────────────────────────────────────────────────
        if etype in ('SL', 'T1', 'T2', 'T3', 'MAE_EXIT', 'TIME_EXIT'):
            try:
                from ml.forex_collector import record_outcome as _ml_gft_out
                _ml_gft_out(
                    trade       = t,
                    account     = 'gft',
                    exit_reason = etype,
                    exit_price  = ev.get('price', t.get('exit_price', 0)),
                    pnl_usd     = ev.get('pnl', t.get('pnl_usd', 0)),
                )
            except Exception as _ml_e:
                logger.debug(f"ML GFT outcome capture skipped: {_ml_e}")

            # ── ML shadow monitor + auto-trainer ──────────────────────────────
            try:
                _pnl = ev.get('pnl', t.get('pnl_usd', 0)) or 0
                _res = 'WIN' if _pnl >= 0 else 'LOSS'
                _r   = t.get('r_multiple', 0) or 0
                from ml.shadow_monitor import on_trade_closed as _ml_otc
                _ml_otc(t.get('id', ''), 'forex', 'gft', _res, float(_r))
                from ml.auto_trainer import check_and_train as _ml_ct
                _ml_ct('forex', 'gft')
            except Exception as _ml_e:
                logger.debug(f"ML GFT monitor/train skipped: {_ml_e}")

        # Check phase completion
        state = load_state()
        updated, advanced, msg = advance_phase_if_complete(state)
        if advanced:
            _send(_format_phase_alert(phase, msg))
            logger.info(f"GFT 2-Step: PHASE ADVANCED — {msg}")

    # ── Heartbeat ────────────────────────────────────────────────────────────

    def _heartbeat_loop(self):
        # Use module-level _ROOT (4 dirname calls from gft_5k_2step.py → c:\cb6_bot)
        hb = os.path.join(_ROOT, 'data', 'gft_2step_heartbeat.txt')
        while self._running:
            try:
                with open(hb, 'w') as f:
                    f.write(datetime.now().isoformat())
            except Exception:
                # REQ-5: Log heartbeat write failures — never swallow silently
                logger.warning("GFT 2-Step heartbeat write failed", exc_info=True)
            time.sleep(60)

    # ── Run ──────────────────────────────────────────────────────────────────

    def run(self):
        self._running = True

        # Wire connector into GFT Telegram bot for live price lookups + /gft_exit
        try:
            from communications.gft_bot import set_connector as _set_gft_connector
            _set_gft_connector(self._connector)
        except Exception:
            pass

        # Start GFT Telegram bot listener (isolated from FTMO bot)
        try:
            from communications.gft_bot import start_listening as _gft_listen
            threading.Thread(target=_gft_listen, daemon=True,
                             name="GFTTGBot").start()
        except Exception as _e:
            logger.warning(f"GFT bot listener failed to start: {_e}")

        state   = load_state()
        summary = get_summary(state)
        phase   = summary['phase']
        capital = summary['capital']

        logger.info("=" * 55)
        logger.info("GFT 2-Step GOAT Engine")
        logger.info(f"Phase   : {phase}")
        logger.info(f"Capital : ${capital:.2f} (start ${_P['account_size']:.0f})")
        logger.info(f"Symbols : {_P['enabled_symbols']}")
        logger.info(f"Risk    : {_P['risk_normal_pct']}% normal | "
                    f"{_P['risk_reduced_pct']}% reduced")
        logger.info(f"Mode    : {'Paper' if self._paper else 'LIVE — GFT'}")
        logger.info("=" * 55)

        _send(
            f"<b>GFT 2-STEP ENGINE STARTED</b>\n\n"
            f"Phase   : {phase.upper().replace('_', ' ')}\n"
            f"Capital : ${capital:.2f}\n"
            f"Symbols : {', '.join(_P['enabled_symbols'])}\n"
            f"Mode    : {'Paper' if self._paper else '🔴 LIVE'}\n"
            f"KZ      : {_P['kill_zone_windows_utc']} UTC\n"
            f"Risk    : {_P['risk_normal_pct']}% / {_P['risk_reduced_pct']}% reduced"
        )

        threading.Thread(target=self._monitor_loop, daemon=True,
                         name="GFT2Monitor").start()
        threading.Thread(target=self._heartbeat_loop, daemon=True,
                         name="GFT2Heartbeat").start()

        active = filter_symbols(_P['enabled_symbols'])
        poll   = 60 if self._paper else 15  # Match FTMO 15s polling for simultaneous entries
        self._connector.start_polling(
            symbols          = active,
            interval         = '15m',
            on_closed_candle = self.on_closed_candle,
            poll_secs        = poll,
        )

        logger.info("GFT 2-Step engine running.")
        while self._running:
            time.sleep(10)

    def stop(self):
        self._running = False
        self._connector.stop_polling()
        self._connector.disconnect()
        logger.info("GFT 2-Step engine stopped")
        _send("GFT 2-STEP ENGINE STOPPED")
