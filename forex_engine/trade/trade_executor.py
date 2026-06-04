# forex_engine/trade/trade_executor.py
# Trade execution pipeline — from scanner signal to MT5 order.
# Coordinates: validator → risk mode → lot calc → A+ boost → order → state → alert.

import uuid
import time
import threading
from datetime import datetime, timezone
from typing import Optional, Callable
from utils.logger import logger

from forex_engine.forex_instruments import INSTRUMENTS, FTMO_RULES, FTMO_RISK_GUARD
from forex_engine.trade.lot_calculator import (
    calc_lot_size, dollar_risk, apply_risk_mode, apply_lot_boost, gft_lot_modifier
)
from forex_engine.trade.sl_tp_manager import adjust_for_fill
from forex_engine.trade.duplicate_guard import DuplicateGuard
from forex_engine.trade.trade_validator import (
    validate_trade, validate_cooldown, validate_session_limit
)
from forex_engine.scanner.structure_scanner import get_h1_bias, get_h4_bias
from forex_engine.scanner.setup_scorer import score_aplus_similarity, lot_boost_factor
from forex_engine.data.slippage_tracker import SlippageTracker


SYMBOL_MIN_SCORE = {
    'XAUUSD': 12,
    'XAGUSD': 11,
    'USOIL' : 11,
    'EURUSD': 11,
}


class TradeExecutor:
    """
    Executes a trade signal end-to-end:
      validate → risk mode → A+ boost → lots → MT5 order → state → alert.

    Designed to be called per symbol when a new candle closes.
    Thread-safe — uses per-symbol locks.
    """

    def __init__(self, connector, load_state_fn: Callable, open_trade_fn: Callable,
                 rollback_fn: Callable, update_ticket_fn: Callable,
                 update_fill_fn: Callable, get_risk_mode_fn: Callable,
                 on_entry_alert: Callable, symbols: list,
                 paper: bool = True,
                 base_risk_pct: float = None,
                 magic: int = 62002,
                 platform: str = 'FTMO'):
        self._connector       = connector
        self._load_state      = load_state_fn
        self._open_trade      = open_trade_fn
        self._rollback        = rollback_fn
        self._update_ticket   = update_ticket_fn
        self._update_fill     = update_fill_fn
        self._get_risk_mode   = get_risk_mode_fn
        self._on_entry_alert  = on_entry_alert
        self._symbols         = symbols
        self._paper           = paper
        self._base_risk_pct   = base_risk_pct or FTMO_RULES['risk_per_trade_pct']
        self._magic           = magic
        self._platform        = platform
        self._locks           = {s: threading.Lock() for s in symbols}
        self._candles         = {}
        self._dedup           = DuplicateGuard()
        self._slip_tracker    = SlippageTracker()
        self._ema_alerted     = {s: set() for s in symbols}
        self._risk_alerted    = {}

    def on_closed_candle(self, symbol: str, df):
        """Called by the candle poller when a new 15m candle closes."""
        self._candles[symbol] = df
        threading.Thread(
            target=self._execute, args=(symbol,),
            daemon=True, name=f"Exec_{symbol}"
        ).start()

    def _execute(self, symbol: str):
        if not self._locks[symbol].acquire(blocking=False):
            return
        try:
            self._run(symbol)
        except Exception as e:
            logger.error(f"TradeExecutor._execute({symbol}): {e}")
        finally:
            self._locks[symbol].release()

    def _run(self, symbol: str):
        from forex_engine.scanner.signal_scanner import (
            scan_setup, in_rollover_window, is_in_kill_zone
        )

        utc_now  = datetime.now(timezone.utc)
        utc_hour = utc_now.hour
        today    = datetime.now().strftime('%Y-%m-%d')

        state = self._load_state()
        if state.get('paused'):
            return

        df = self._candles.get(symbol)
        if df is None or len(df) < 40:
            return

        setup = scan_setup(df, symbol)
        if not setup:
            return

        sig = setup['entry_signal']

        # HTF bias
        h1_bias = get_h1_bias(self._connector, symbol)
        h4_bias = get_h4_bias(self._connector, symbol)

        # Hard validation
        spread = self._connector.get_spread(symbol)
        ok, reason = validate_trade(
            setup, utc_hour, h1_bias, h4_bias,
            SYMBOL_MIN_SCORE, state, spread, gft_mode=False
        )
        if not ok:
            logger.info(f"FOREX {symbol}: BLOCKED — {reason}")
            return

        # A+ flag
        score    = setup['confluence']
        mss_type = setup.get('mss_type', 'BOS')
        is_aplus = (score + (1 if mss_type == 'CHOCH' else 0)) >= 13

        # Cooldown
        ok, reason = validate_cooldown(symbol, state, minutes=90, is_aplus=is_aplus)
        if not ok:
            logger.info(f"FOREX {symbol}: {reason}")
            return

        # Session limit
        ok, reason = validate_session_limit(symbol, state, utc_hour, is_aplus=is_aplus)
        if not ok:
            logger.info(f"FOREX {symbol}: {reason}")
            return

        # Dedup
        fvg_low = sig['fvg_low']
        if self._dedup.is_duplicate(symbol, setup['direction'], fvg_low):
            logger.info(f"FOREX {symbol}: DEDUP — already traded this FVG zone today")
            return

        # Risk mode
        risk_mode, risk_reason = self._get_risk_mode(state)
        if risk_mode == 'paused':
            logger.info(f"FOREX {symbol}: RISK PAUSED — {risk_reason}")
            return
        if risk_mode == 'aplus_only' and not is_aplus:
            logger.info(f"FOREX {symbol}: A+ ONLY mode — {risk_reason}")
            return

        # A+ similarity boost
        df15     = self._candles.get(symbol)
        sim_ratio, sim_bd = score_aplus_similarity(setup, df15, h4_bias, h1_bias, utc_hour)
        boost    = lot_boost_factor(sim_ratio)

        # Risk pct
        risk_pct = self._base_risk_pct
        if risk_mode in ('reduced', 'aplus_only'):
            risk_pct = apply_risk_mode(risk_pct, risk_mode,
                                       FTMO_RISK_GUARD.get('risk_reduction_factor', 0.5))
            logger.info(f"FOREX {symbol}: REDUCED RISK — {risk_pct:.3f}%")
        elif boost > 1.0 and risk_mode == 'normal':
            risk_pct = apply_lot_boost(risk_pct, boost, risk_mode)
            logger.info(f"FOREX {symbol}: A+ BOOST {boost}× → risk={risk_pct:.3f}%")

        # Lot size
        capital = state.get('capital', 10000.0)
        lots    = calc_lot_size(symbol, capital, sig['entry'], sig['stop_loss'], risk_pct)
        min_lot = INSTRUMENTS.get(symbol, {}).get('min_lot', 0.01)
        if lots < min_lot:
            logger.info(f"FOREX {symbol}: lots {lots} < min {min_lot} — skip")
            return

        risk_usd = dollar_risk(symbol, lots, sig['entry'], sig['stop_loss'])
        sig['risk_usd'] = risk_usd

        setup['sim_ratio']       = sim_ratio
        setup['lot_boost']       = boost
        setup['risk_mode']       = risk_mode
        setup['entry_reason']    = (
            f"{mss_type} score={score}/15 H4={h4_bias} "
            f"sim={sim_ratio:.0%} boost={boost}× mode={risk_mode}"
        )
        setup['spread_at_entry'] = self._connector.get_spread(symbol) or 0.0

        # Open paper/state record
        with threading.Lock():
            fresh = self._load_state()
            if fresh.get('open_trades'):
                logger.info(f"FOREX {symbol}: position already open")
                return
            fresh_daily = fresh.get('daily_pnl', 0.0)
            cap         = fresh.get('starting_capital', 10000.0)
            pp_cap      = round(cap * FTMO_RISK_GUARD.get('daily_profit_stop_pct', 2.4) / 100, 2)
            if fresh_daily >= pp_cap:
                logger.info(f"FOREX {symbol}: daily profit cap (${fresh_daily:.2f} ≥ ${pp_cap:.2f})")
                return

            trade = self._open_trade(setup, lots)

        if not trade:
            return

        self._dedup.mark_seen(symbol, setup['direction'], fvg_low)
        ticket = 0

        # MT5 order (live only)
        if not self._paper:
            result = self._connector.place_market_order(
                symbol    = symbol,
                direction = 'BUY' if setup['direction'] == 'BULLISH' else 'SELL',
                lots      = lots,
                sl        = sig['stop_loss'],
                tp        = sig['target2'],
                magic     = self._magic,
            )
            if not result:
                logger.error(f"FOREX {symbol}: MT5 order FAILED — rolling back")
                self._rollback(trade['id'], risk_usd)
                return

            ticket = result.get('ticket', 0)
            fill   = result.get('price', 0.0)
            if ticket:
                self._update_ticket(trade['id'], ticket)

            if fill:
                adjusted = adjust_for_fill(
                    symbol, setup['direction'], fill, sig['entry'],
                    sig['stop_loss'], lots, sig.get('dol_level', 0)
                )
                self._update_fill(
                    trade['id'], fill,
                    adjusted['stop_loss'], adjusted['target1'],
                    adjusted['target2'], adjusted['target3'],
                    adjusted['risk_usd']
                )
                # Slippage check
                slip_result = self._slip_tracker.check(symbol, sig['entry'], fill)
                # Update sig for alert
                sig.update(adjusted)
                risk_usd = adjusted['risk_usd']

                # Adjust live MT5 SL to fill-adjusted level
                if ticket and adjusted['stop_loss'] != sig.get('stop_loss'):
                    try:
                        self._connector.modify_sl(symbol, ticket, adjusted['stop_loss'])
                    except Exception as e:
                        logger.warning(f"FOREX {symbol}: SL adjust failed: {e}")

        logger.info(
            f"FOREX {symbol}: trade opened {setup['direction']} "
            f"{lots}L @ {sig['entry']:.5f} platform={self._platform}"
        )
        self._on_entry_alert(setup, lots, risk_usd, ticket=ticket,
                             platform=self._platform)
