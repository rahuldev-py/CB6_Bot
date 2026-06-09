# trader/paper_trader.py - CB6 QUANTUM Paper Trading Engine
import os
import sys
import json
import threading
from datetime import datetime
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger
from utils.state_io import load_json_locked, save_json_locked
from utils.telegram_alerts import send_message
from settings import CAPITAL, MAX_TRADES_PER_DAY, RISK_PER_TRADE_PCT, MAX_DAILY_LOSS_PCT
from ml_engine.memory.shadow_logger import log_closed_trade
from ml_engine.memory.replay_shadow import archive_closed_trade_shadow

# Paper trading state file
STATE_FILE = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'paper_state.json'
)

# REQ-2: RLock so the same thread can re-enter (e.g. update_paper_trades →
#         close_paper_trade → save_state) without deadlocking.
_state_lock = threading.RLock()

def reconcile_capital(state):
    """Recompute available_capital and total_pnl from scratch on every load."""
    base     = state.get('capital', CAPITAL)
    realized = round(sum(t.get('pnl', 0) for t in state.get('closed_trades', [])), 2)
    # Use capital_used from each trade (already accounts for instrument type:
    # options=full premium, futures=12% SPAN margin)
    locked = sum(
        t.get('capital_used', 0)
        for t in state.get('open_trades', [])
    )
    locked = min(locked, base)   # can never lock more than total capital
    state['available_capital'] = round(base + realized - locked, 2)
    state['total_pnl']         = realized
    return state


def load_state():
    """Load current paper trading state"""
    try:
        state = load_json_locked(STATE_FILE, {
            'capital'              : CAPITAL,
            'available_capital'    : CAPITAL,
            'open_trades'          : [],
            'closed_trades'        : [],
            'daily_losses'         : 0,
            'daily_trades'         : 0,
            'total_pnl'            : 0,
            'date'                 : datetime.now().strftime('%Y-%m-%d'),
            'daily_option_strikes' : {},
        })
        return reconcile_capital(state)
    except Exception as e:
        logger.error(f"State load error (state reset to default): {e}")

    return {
        'capital'              : CAPITAL,
        'available_capital'    : CAPITAL,
        'open_trades'          : [],
        'closed_trades'        : [],
        'daily_losses'         : 0,
        'daily_trades'         : 0,
        'total_pnl'            : 0,
        'date'                 : datetime.now().strftime('%Y-%m-%d'),
        'daily_option_strikes' : {},  # symbol â†’ trade count today
    }

def save_state(state):
    """Save paper trading state - caller must already hold _state_lock."""
    try:
        save_json_locked(STATE_FILE, state)
    except Exception as e:
        logger.error(f"Save state error: {e}")

def reset_daily_counters(state):
    """Reset daily counters if new day; auto-close overnight intraday trades."""
    today = datetime.now().strftime('%Y-%m-%d')
    if state['date'] != today:
        prev_date                      = state['date']   # save before overwriting
        state['date']                  = today
        state['daily_losses']          = 0
        state['daily_trades']          = 0
        state['daily_option_strikes']  = {}
        # Clear halt flags — a new trading day starts clean.
        state.pop('daily_halted',      None)
        state.pop('daily_halt_reason', None)
        state.pop('daily_halt_time',   None)
        logger.info("Daily counters reset for new day")

        # Force-close any intraday trades that were left open overnight
        still_open = []
        for t in state.get('open_trades', []):
            product = (t.get('product') or t.get('product_type') or 'INTRADAY').upper()
            if 'CNC' in product or 'DELIVERY' in product or 'POSITIONAL' in product:
                still_open.append(t)  # positional - keep open
            else:
                # Intraday trade carried overnight: close at last known price
                last_px = t.get('ltp') or t.get('exit_price') or t.get('entry_price')
                t['exit_price'] = last_px
                t['exit_time']  = prev_date + ' 15:30:00'
                t['status']     = 'EXPIRED_OVERNIGHT'
                direction = (t.get('direction') or 'BUY').upper()
                entry = t['entry_price']
                qty   = t.get('quantity', 0)
                is_option = (t.get('instrument_type', 'OPTION').upper() == 'OPTION')
                if is_option or direction in ('BUY', 'BULLISH'):
                    gross = (last_px - entry) * qty
                else:
                    gross = (entry - last_px) * qty
                t['pnl'] = round((t.get('realized_pnl', 0) or 0) + gross, 2)
                state['closed_trades'].append(t)
                state['total_pnl'] = round(state.get('total_pnl', 0) + gross, 2)
                logger.warning(f"Overnight close: {t.get('symbol')} @ {last_px} | PnL Rs {gross:.0f}")
        state['open_trades'] = still_open

    return state

def _round_to_lot(quantity, symbol):
    """Round quantity down to nearest lot size for futures/options; 1 for equity."""
    # NIFTY options lot size is typically 50. Your option symbols end with CE/PE.
    try:
        sym = (symbol or "").upper()
        if sym.endswith("CE") or sym.endswith("PE"):
            from scanner.index_futures import INDEX_LOT_SIZES
            option_lot = 65  # fallback if underlying not found (NIFTY lot as of May 2026)
            for idx_name in sorted(INDEX_LOT_SIZES.keys(), key=len, reverse=True):
                if idx_name in sym:
                    option_lot = INDEX_LOT_SIZES[idx_name]
                    break
            return max(option_lot, (quantity // option_lot) * option_lot) if quantity >= option_lot else 0

        from scanner.index_futures import get_lot_size
        lot = get_lot_size(symbol)
        if lot > 1:
            lots = quantity // lot   # floor to whole lots; 0 = below 1 lot, caller skips
            return lots * lot
    except Exception:
        pass
    return max(1, quantity)


_INDEX_MARGIN_PCT  = 0.12   # ~12% SPAN+exposure for index futures (realistic)
_STOCK_MARGIN_PCT  = 0.20   # 20% for stock futures
_OPTION_MARGIN_PCT = 1.00   # options: full premium is at risk (no leverage)


def _margin_pct(symbol: str) -> float:
    """Return margin fraction for the given symbol."""
    from scanner.index_futures import INDEX_LOT_SIZES
    sym = (symbol or '').upper()
    # Options are long premium - full cost is at risk, no margin benefit
    if sym.endswith('CE') or sym.endswith('PE'):
        return _OPTION_MARGIN_PCT
    for idx in INDEX_LOT_SIZES:
        if idx in sym:
            return _INDEX_MARGIN_PCT
    return _STOCK_MARGIN_PCT


def _lot_size(symbol: str) -> int:
    try:
        from scanner.index_futures import get_lot_size
        return get_lot_size(symbol) or 1
    except Exception:
        return 1


def calculate_quantity(capital, entry_price, stop_loss, symbol='', risk_pct=None):
    """
    Risk-based sizing capped by available margin.
    Index futures use 12% margin (SPAN+exposure); stocks use 20%.
    Always returns whole lots; returns 0 if capital < 1-lot margin.
    """
    try:
        lot  = _lot_size(symbol)
        mrgn = _margin_pct(symbol)
        # Minimum margin needed for 1 lot
        if capital < lot * entry_price * mrgn:
            logger.warning(f"Insufficient capital for 1 lot {symbol}: need Rs {lot*entry_price*mrgn:.0f}, have Rs {capital:.0f}")
            return 0

        pct = risk_pct if risk_pct is not None else RISK_PER_TRADE_PCT
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share <= 0:
            risk_per_share = entry_price * 0.005   # fallback: 0.5% risk

        # Risk-based quantity
        quantity = int((capital * pct / 100) / risk_per_share)
        # Cap: margin for position â‰¤ available capital
        max_qty = int(capital / (entry_price * mrgn))
        quantity = min(quantity, max_qty)
        return _round_to_lot(quantity, symbol)
    except Exception as e:
        logger.error(f"Quantity error: {e}")
        return 0


def calculate_quantity_short(capital, entry_price, stop_loss, symbol=''):
    """Short sizing - mirrors calculate_quantity but uses SL above entry."""
    try:
        lot  = _lot_size(symbol)
        mrgn = _margin_pct(symbol)
        if capital < lot * entry_price * mrgn:
            logger.warning(f"Insufficient capital for 1 lot {symbol}: need Rs {lot*entry_price*mrgn:.0f}, have Rs {capital:.0f}")
            return 0

        risk_per_share = abs(stop_loss - entry_price)
        if risk_per_share <= 0:
            risk_per_share = entry_price * 0.005

        quantity = int((capital * RISK_PER_TRADE_PCT / 100) / risk_per_share)
        max_qty  = int(capital / (entry_price * mrgn))
        quantity = min(quantity, max_qty)
        return _round_to_lot(quantity, symbol)
    except Exception as e:
        logger.error(f"Quantity short error: {e}")
        return 0


def _partial_book(trade, price, portion, state):
    """
    Close `portion` of original_quantity at `price`, rounded DOWN to whole lots.
    Indian F&O: you cannot exit a fraction of a lot - only whole lots.
    Returns (booked_qty, net_pnl_after_costs), or (0, 0) if < 1 lot available.
    """
    from utils.brokerage import net_pnl as _net_pnl
    symbol   = trade.get('symbol', '')
    lot      = _lot_size(symbol)
    orig_qty = trade.get('original_quantity', trade['quantity'])
    # How many lots to close
    target_qty = max(lot, int(orig_qty * portion))
    # Round DOWN to whole lot - must leave at least 1 lot remaining
    target_qty = min(target_qty, trade['quantity'] - lot)
    target_qty = (target_qty // lot) * lot  # whole lots only
    if target_qty < lot:
        return 0, 0   # not enough lots to do a partial; caller will close all

    book_qty  = target_qty
    direction = trade.get('direction', 'BUY')
    sym_upper = trade.get('symbol', '').upper()
    # Options (CE/PE) are always long - P&L is always (exit - entry) regardless of direction.
    # instrument_type='INDEX' for all index setups so we detect via symbol suffix instead.
    is_option = sym_upper.endswith('CE') or sym_upper.endswith('PE')
    if is_option or direction in ('BUY', 'BULLISH'):
        gross = round((price - trade['entry_price']) * book_qty, 2)
    else:
        gross = round((trade['entry_price'] - price) * book_qty, 2)

    pnl, costs = _net_pnl(gross, trade['entry_price'], price, book_qty, direction)
    trade['brokerage_paid'] = round(trade.get('brokerage_paid', 0) + costs['total'], 2)

    mrgn = _margin_pct(symbol)
    freed_capital = book_qty * trade['entry_price'] * mrgn + pnl

    trade['quantity']      -= book_qty
    trade['realized_pnl']   = round(trade.get('realized_pnl', 0) + pnl, 2)
    trade['capital_used']   = round(trade['quantity'] * trade['entry_price'] * mrgn, 2)
    state['total_pnl']         = round(state['total_pnl'] + pnl, 2)
    state['available_capital'] = round(state['available_capital'] + freed_capital, 2)
    return book_qty, pnl


def _rearm_trade_triggers(trade):
    """Refresh WebSocket triggers after SL/target state changes."""
    try:
        from core.trade_triggers import register_trade_triggers
        register_trade_triggers(trade)
    except Exception as e:
        logger.debug(f"WS trigger re-arm skipped: {e}")


def reset_paper_state_if_new_day():
    """Call at startup to auto-reset daily counters if date changed."""
    state = load_state()
    state = reset_daily_counters(state)
    save_state(state)

def can_take_trade(state):
    """Check if we can take a new trade."""
    # Daily loss halt takes absolute priority — persists until the next trading day.
    if state.get('daily_halted'):
        return False, (
            f"Daily loss halt active "
            f"({state.get('daily_halt_reason', 'cap hit')}) — "
            "no new entries until tomorrow"
        )

    if state.get('paused'):
        return False, "Bot paused - send /resume to re-enable"

    if state['daily_trades'] >= MAX_TRADES_PER_DAY:
        return False, "Max trades reached"

    if state['available_capital'] < 5000:
        return False, "Insufficient capital"

    # REQ-4: Hard 2% daily loss floor - compute today's closed PnL
    today = datetime.now().strftime('%Y-%m-%d')
    today_pnl = sum(
        t.get('pnl', 0)
        for t in state.get('closed_trades', [])
        if (t.get('exit_time') or '')[:10] == today
    )
    loss_limit = state.get('capital', CAPITAL) * MAX_DAILY_LOSS_PCT / 100
    if today_pnl <= -loss_limit:
        return False, (
            f"Daily loss limit hit: Rs {abs(today_pnl):.0f} >= "
            f"{MAX_DAILY_LOSS_PCT}% of capital (Rs {loss_limit:.0f}) "
            "- no new entries today"
        )

    return True, "OK"

def get_option_strike_count(symbol: str) -> int:
    """How many times has this exact option symbol been traded today (open + closed)."""
    state = load_state()
    return state.get('daily_option_strikes', {}).get(symbol, 0)


def register_option_strike(symbol: str) -> int:
    """
    Increment today's trade count for this option symbol.
    Returns the NEW count (1 = first trade, 2 = second, …).
    """
    # REQ-2: atomic read-modify-write
    _state_lock.acquire()
    try:
        state  = load_state()
        bucket = state.setdefault('daily_option_strikes', {})
        bucket[symbol] = bucket.get(symbol, 0) + 1
        save_state(state)
        return bucket[symbol]
    finally:
        _state_lock.release()


def open_paper_trade(setup, risk_pct=None):
    """Open a new paper trade"""
    # REQ-2: Acquire RLock for the entire load-validate-mutate-save cycle.
    # RLock allows re-entry by the same thread (e.g. inner save_state calls).
    _state_lock.acquire()
    try:
        state = load_state()
        state = reset_daily_counters(state)

        sig    = setup['entry_signal']
        symbol = setup['symbol']


        # Index-only gate - block all equity/stock trades
        inst_type = setup.get('instrument_type', 'EQUITY')
        if inst_type == 'EQUITY':
            clean = symbol.replace('NSE:', '').replace('-EQ', '')
            logger.warning(f"BLOCKED equity trade: {symbol} - index-only mode active")
            send_message(
                f"CB6 BLOCKED - {clean}\n"
                "Equity trades disabled. Index futures & options only.\n"
                "Use /sb to scan NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY."
            )
            return None

        today = datetime.now().strftime('%Y-%m-%d')

        # Skip if already have an open trade on this symbol in same direction
        direction_check = setup.get('direction', 'BUY')
        for trade in state['open_trades']:
            if trade['symbol'] == symbol and trade.get('direction') == direction_check:
                logger.info(f"Already open {symbol} {direction_check} - skip duplicate")
                return None

        # Skip if already traded this exact FVG zone today (zone-based dedup, not direction-wide)
        # Allows afternoon re-entry if a new FVG zone forms at a different level
        fvg_data  = setup.get('fvg', setup.get('entry_signal', {}))
        new_zone  = round(fvg_data.get('fvg_low', sig.get('fvg_low', 0)) / 50) * 50
        for trade in state['closed_trades']:
            entry_date = (trade.get('entry_time') or '')[:10]
            prior_zone = round(trade.get('fvg_low', trade.get('stop_loss', 0)) / 50) * 50
            if (entry_date == today
                    and trade['symbol'] == symbol
                    and trade.get('direction') == direction_check
                    and prior_zone == new_zone):
                logger.info(f"Already traded {symbol} {direction_check} zone~{new_zone} today - skip re-entry")
                return None

        ok, reason = can_take_trade(state)
        if not ok:
            logger.info(f"Trade gate blocked {symbol}: {reason}")
            return None

        direction = setup.get('direction', 'BUY')
        mrgn      = _margin_pct(symbol)

        fixed_quantity = int(setup.get('quantity') or 0)
        if fixed_quantity > 0:
            quantity = _round_to_lot(fixed_quantity, symbol)
        elif direction in ('SELL', 'BEARISH'):
            quantity = calculate_quantity_short(
                state['available_capital'],
                sig['entry'], sig['stop_loss'], symbol
            )
        else:
            quantity = calculate_quantity(
                state['available_capital'],
                sig['entry'], sig['stop_loss'], symbol,
                risk_pct=risk_pct
            )

        if quantity <= 0:
            sl_dist = round(abs(sig['entry'] - sig['stop_loss']), 1)
            logger.warning(
                f"Skipping {symbol} {direction} - SL {sl_dist}pts too wide: "
                f"1 lot would risk Rs {sl_dist * _lot_size(symbol):.0f} "
                f"(>{RISK_PER_TRADE_PCT}% of Rs {state['available_capital']:.0f})"
            )
            return None

        position_value = quantity * sig['entry']
        capital_used   = round(position_value * mrgn, 2)

        trade = {
            'id'              : f"PT{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'symbol'          : symbol,
            'direction'       : direction,
            'timeframe'       : setup.get('timeframe', '15min'),
            'instrument_type' : setup.get('instrument_type', 'EQUITY'),
            'entry_price'     : sig['entry'],
            'stop_loss'       : sig['stop_loss'],
            'target1'         : sig['target1'],
            'target2'         : sig['target2'],
            'target3'         : sig['target3'],
            'quantity'        : quantity,
            'original_quantity': quantity,
            'position_value'  : position_value,
            'capital_used'    : capital_used,
            'product_type'     : setup.get('product_type', 'INTRADAY'),
            'underlying'       : setup.get('underlying'),
            'lot_size'         : setup.get('lot_size'),
            'delta'            : setup.get('delta'),
            'theta'            : setup.get('theta'),
            'strike'           : setup.get('strike'),
            'expiry'           : setup.get('expiry'),
            'iv'               : setup.get('iv'),
            'options_context'  : setup.get('options_context'),
            'journal_id'       : setup.get('journal_id'),
            'b1_price'        : sig.get('b1_price', sig['entry']),
            'b2_price'        : sig.get('b2_price', sig['entry']),
            'neck_price'      : sig.get('neck_price', sig['entry']),
            'risk'            : sig['risk'],
            'rr_ratio'        : sig['rr_ratio'],
            'confluence'      : setup.get('confluence', sig.get('confluence', 0)),
            'dte'             : setup.get('dte', 99),
            'regime'          : setup.get('regime', 'NEUTRAL'),
            'in_ote'          : sig.get('in_ote', False),
            'in_fvg'          : sig.get('in_fvg', False),
            'fvg_low'         : setup.get('fvg', {}).get('fvg_low', sig.get('fvg_low', sig['stop_loss'])),
            'fvg_high'        : setup.get('fvg', {}).get('fvg_high', sig.get('fvg_high')),
            'frvp'            : setup.get('frvp'),
            'psychology'      : setup.get('psychology'),
            'entry_time'      : datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'exit_time'       : None,
            'exit_price'      : None,
            'status'          : 'OPEN',
            'current_sl'      : sig['stop_loss'],
            'targets_hit'     : [],
            'realized_pnl'    : 0,
            'brokerage_paid'  : 0,
            'pnl'             : 0
        }

        state['open_trades'].append(trade)
        state['available_capital'] = round(state['available_capital'] - capital_used, 2)
        state['daily_trades']      += 1
        save_state(state)

        # ── Trade verifier: record entry ──────────────────────────────────────
        try:
            from utils.trade_verifier import get_verifier
            get_verifier().record_entry(
                trade,
                setup,
                lot_size_source=setup.get('_lot_size_source', 'fallback'),
            )
        except Exception:
            pass

        # Register WebSocket triggers (no-op if WS feed not active)
        try:
            from core.trade_triggers import register_trade_triggers
            from scanner.websocket_feed import is_active, subscribe
            register_trade_triggers(trade)
            if is_active():
                subscribe([symbol])
        except Exception as e:
            logger.debug(f"WS trigger registration skipped: {e}")

        lot   = _lot_size(symbol)
        lots  = quantity // lot if lot > 1 else quantity
        clean = symbol.replace("NSE:", "").replace("-EQ", "")
        send_message(
            f"CB6 ENTRY - {clean} {direction}\n\n"
            f"Entry  : {sig['entry']}\n"
            f"SL     : {sig['stop_loss']}\n"
            f"T1     : {sig['target1']}\n"
            f"T2     : {sig['target2']}\n"
            f"T3     : {sig['target3']}\n"
            f"Qty    : {quantity} ({lots} lot{'s' if lots != 1 else ''}) | RR: 1:{sig['rr_ratio']}\n"
            f"Margin : Rs {capital_used:,.0f} ({mrgn*100:.0f}%)"
        )

        logger.info(f"Paper trade opened: {clean} qty:{quantity}")

        # ── ML price series (CNN/RNN training data) ───────────────────────────
        try:
            candles_df = setup.get('_candles_df')
            if candles_df is not None and len(candles_df) >= 5:
                from ml.data_pipeline import save_price_series
                cols = ['open', 'high', 'low', 'close', 'volume']
                # normalise column names (Fyers returns lowercase)
                df_cols = {c.lower(): c for c in candles_df.columns}
                candle_list = []
                for _, row in candles_df.iterrows():
                    candle_list.append({
                        'open'  : float(row.get(df_cols.get('open',   'open'),   0)),
                        'high'  : float(row.get(df_cols.get('high',   'high'),   0)),
                        'low'   : float(row.get(df_cols.get('low',    'low'),    0)),
                        'close' : float(row.get(df_cols.get('close',  'close'),  0)),
                        'volume': float(row.get(df_cols.get('volume', 'volume'), 0)),
                    })
                save_price_series(trade['id'], 'nse', '', candle_list, n_before=50)
                logger.debug(f"ML price series saved for {trade['id']} ({len(candle_list)} candles)")
        except Exception as _ml_e:
            logger.debug(f"ML NSE price series save skipped: {_ml_e}")

        # ── ML data capture ───────────────────────────────────────────────────
        try:
            from ml.nse_collector import record_entry as ml_entry
            ml_entry(trade, setup, mode='paper')
        except Exception as _ml_e:
            logger.debug(f"ML NSE entry capture skipped: {_ml_e}")

        # ── ML shadow prediction ───────────────────────────────────────────────
        try:
            from ml.predictor import predict_nse
            candles_df = setup.get('_candles_df')
            candles_arr = None
            if candles_df is not None and len(candles_df) >= 5:
                import numpy as np
                df_cols = {c.lower(): c for c in candles_df.columns}
                candles_arr = candles_df[[
                    df_cols.get('open','open'), df_cols.get('high','high'),
                    df_cols.get('low','low'),   df_cols.get('close','close'),
                    df_cols.get('volume','volume'),
                ]].values.astype(float)
            predict_nse(trade['id'], setup, candles=candles_arr)
        except Exception as _ml_e:
            logger.debug(f"ML NSE shadow predict skipped: {_ml_e}")

        return trade

    except Exception as e:
        logger.error(f"Open trade error: {e}")
        return None
    finally:
        _state_lock.release()   # REQ-2: always release even on exception


def update_paper_trades(fyers):
    """Check open trades and update SL/targets using live price."""
    # REQ-2: Hold lock for the full monitor cycle - prevents a concurrent
    # open_paper_trade or close_by_id from interleaving mid-loop.
    _state_lock.acquire()
    try:
        state = load_state()

        if not state['open_trades']:
            logger.info("No open trades to update")
            return

        from scanner.data_fetcher import get_historical_data
        from scanner.live_price   import get_live_price

        for trade in state['open_trades'][:]:
            symbol = trade['symbol']

            # Try live price first; fall back to last 15min candle
            ltp = get_live_price(fyers, symbol)
            df  = get_historical_data(fyers, symbol, "15", days=1)
            if df is None or len(df) == 0:
                continue

            current_high  = float(df['high'].iloc[-1])
            current_low   = float(df['low'].iloc[-1])
            current_price = ltp if ltp else float(df['close'].iloc[-1])
            # Extend candle HIGH with LTP (for target hit detection)
            # Do NOT extend candle LOW with LTP for SL - that causes false SL
            # triggers when price wicks down then recovers within the same candle.
            if ltp:
                current_high = max(current_high, ltp)

            # SL uses LTP only (confirmed live price), NOT candle low.
            # Candle wick spikes that recover should not close the trade.
            # If LTP unavailable fall back to candle close (never candle low).
            sl_check_price = current_price   # always LTP or close, never wick

            clean = symbol.replace("NSE:", "").replace("-EQ", "")

            direction = trade.get('direction', 'BUY')

            # â"€â"€ Expiry-day theta protection â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            # On expiry day (DTE=0), take profit at 30% premium gain rather
            # than waiting for index-based targets that theta decay will erode.
            dte = trade.get('dte', 99)
            _sym_up = trade['symbol'].upper()
            _is_option = (
                _sym_up.endswith('CE') or _sym_up.endswith('PE')
                or trade.get('instrument_type', '').upper() in ('OPTION', 'INDEX')
            )
            if dte == 0 and _is_option:
                entry_px = trade['entry_price']
                premium_gain_pct = (current_price - entry_px) / entry_px if entry_px else 0
                if premium_gain_pct >= 0.30 and 'EXPIRY_EXIT' not in trade.get('targets_hit', []):
                    trade.setdefault('targets_hit', []).append('EXPIRY_EXIT')
                    close_paper_trade(trade, current_price, 'EXPIRY_THETA_EXIT', state)
                    send_message(
                        f"CB6 QUANTUM - EXPIRY THETA EXIT\n{clean}\n"
                        f"Premium +{premium_gain_pct*100:.0f}% â†’ closing before theta eats it\n"
                        f"Exit: {current_price} | PnL: Rs {trade.get('pnl',0):.0f}"
                    )
                    continue

            is_option = (trade.get('instrument_type', 'OPTION').upper() == 'OPTION')
            if is_option or direction in ('BUY', 'BULLISH'):
                trade['pnl'] = round(
                    (current_price - trade['entry_price']) * trade['quantity'], 2
                )
                if sl_check_price <= trade['current_sl']:
                    # Close at SL price, not the polled market price.
                    # Simulates a stop-limit order filling at the stop level.
                    # Using current LTP (which could be far below SL due to polling lag)
                    # would overstate losses and distort strategy statistics.
                    sl_exit_price = trade['current_sl']
                    close_paper_trade(trade, sl_exit_price, 'SL_HIT', state)
                    continue
                if 'T1' not in trade['targets_hit'] and current_high >= trade['target1']:
                    bqty, bpnl = _partial_book(trade, trade['target1'], 0.33, state)
                    trade['targets_hit'].append('T1')
                    trade['current_sl'] = trade['entry_price']
                    _rearm_trade_triggers(trade)
                    send_message(
                        f"CB6 QUANTUM - BUY T1 HIT\n{clean} @ {trade['target1']}\n"
                        f"Booked {bqty} units | PnL: Rs {bpnl:.0f}\n"
                        f"SL trailed to entry {trade['entry_price']}\n"
                        f"Remaining: {trade['quantity']} units"
                    )
                    logger.info(f"T1 partial: {clean} booked {bqty}")
                if 'T2' not in trade['targets_hit'] and current_high >= trade['target2']:
                    bqty, bpnl = _partial_book(trade, trade['target2'], 0.33, state)
                    trade['targets_hit'].append('T2')
                    # Trail SL to 50% between T1 and T2 - locks in more profit
                    new_sl = round(
                        trade['target1'] + (trade['target2'] - trade['target1']) * 0.5, 2
                    )
                    trade['current_sl'] = new_sl
                    _rearm_trade_triggers(trade)
                    send_message(
                        f"CB6 QUANTUM - BUY T2 HIT\n{clean} @ {trade['target2']}\n"
                        f"Booked {bqty} units | PnL: Rs {bpnl:.0f}\n"
                        f"SL trailed to {new_sl} (mid T1-T2)\n"
                        f"Remaining: {trade['quantity']} units"
                    )
                    logger.info(f"T2 partial: {clean} booked {bqty}")
                if current_high >= trade['target3']:
                    close_paper_trade(trade, trade['target3'], 'TARGET_HIT', state)
                    continue

            else:  # SHORT futures/equity - not options (options handled above)
                trade['pnl'] = round(
                    (trade['entry_price'] - current_price) * trade['quantity'], 2
                )
                if sl_check_price >= trade['current_sl']:   # LTP only, not candle high
                    close_paper_trade(trade, trade['current_sl'], 'SL_HIT', state)
                    continue
                if 'T1' not in trade['targets_hit'] and current_low <= trade['target1']:
                    bqty, bpnl = _partial_book(trade, trade['target1'], 0.33, state)
                    trade['targets_hit'].append('T1')
                    trade['current_sl'] = trade['entry_price']
                    _rearm_trade_triggers(trade)
                    send_message(
                        f"CB6 QUANTUM - SHORT T1 HIT\n{clean} @ {trade['target1']}\n"
                        f"Booked {bqty} units | PnL: Rs {bpnl:.0f}\n"
                        f"SL trailed to entry {trade['entry_price']}\n"
                        f"Remaining: {trade['quantity']} units"
                    )
                    logger.info(f"Short T1 partial: {clean} booked {bqty}")
                if 'T2' not in trade['targets_hit'] and current_low <= trade['target2']:
                    bqty, bpnl = _partial_book(trade, trade['target2'], 0.33, state)
                    trade['targets_hit'].append('T2')
                    # Trail SL to 50% between T1 and T2 - locks in more profit
                    new_sl = round(
                        trade['target1'] - (trade['target1'] - trade['target2']) * 0.5, 2
                    )
                    trade['current_sl'] = new_sl
                    _rearm_trade_triggers(trade)
                    send_message(
                        f"CB6 QUANTUM - SHORT T2 HIT\n{clean} @ {trade['target2']}\n"
                        f"Booked {bqty} units | PnL: Rs {bpnl:.0f}\n"
                        f"SL trailed to {new_sl} (mid T1-T2)\n"
                        f"Remaining: {trade['quantity']} units"
                    )
                    logger.info(f"Short T2 partial: {clean} booked {bqty}")
                if current_low <= trade['target3']:
                    close_paper_trade(trade, trade['target3'], 'TARGET_HIT', state)
                    continue

            save_state(state)

    except Exception as e:
        logger.error(f"Update trades error: {e}")
    finally:
        _state_lock.release()   # REQ-2: always release


def close_paper_trade(trade, exit_price, reason, state):
    """Close remaining position. Aggregates with any prior partial bookings."""
    try:
        from utils.brokerage import net_pnl as _net_pnl
        direction     = trade.get('direction', 'BUY')
        remaining_qty = max(trade['quantity'], 0)
        partial_pnl   = trade.get('realized_pnl', 0)  # from T1/T2 bookings

        trade['exit_price'] = exit_price
        trade['exit_time']  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        trade['status']     = reason

        sym_upper = trade.get('symbol', '').upper()
        is_option = sym_upper.endswith('CE') or sym_upper.endswith('PE')
        if is_option or direction in ('BUY', 'BULLISH'):
            gross_final = round((exit_price - trade['entry_price']) * remaining_qty, 2)
        else:
            gross_final = round((trade['entry_price'] - exit_price) * remaining_qty, 2)

        final_leg_pnl, costs = _net_pnl(
            gross_final, trade['entry_price'], exit_price, remaining_qty, direction
        )
        trade['brokerage_paid'] = round(
            trade.get('brokerage_paid', 0) + costs['total'], 2
        )

        trade['pnl']          = round(partial_pnl + final_leg_pnl, 2)
        trade['realized_pnl'] = trade['pnl']

        mrgn = _margin_pct(trade.get('symbol', ''))
        freed_capital = trade.get('capital_used', 0) + final_leg_pnl

        if trade in state['open_trades']:
            state['open_trades'].remove(trade)

        state['closed_trades'].append(trade)
        state['available_capital'] = round(state['available_capital'] + freed_capital, 2)
        state['total_pnl']         = round(state['total_pnl'] + final_leg_pnl, 2)

        if trade['pnl'] < 0:
            state['daily_losses'] += 1

        from journal.trade_journal import log_trade
        log_trade(trade)

        # Write exit to CSV trade journal (utils/trade_journal.py)
        try:
            from utils.trade_journal import log_exit as _csv_exit
            entry_t   = trade.get('entry_time', '')
            entry_iso = entry_t.replace(' ', 'T') if entry_t else ''
            if entry_iso:
                _dt_in  = datetime.strptime(entry_t[:19], '%Y-%m-%d %H:%M:%S')
                _dt_out = datetime.now()
                _mins   = round((_dt_out - _dt_in).total_seconds() / 60, 1)
                _csv_exit(
                    journal_id  = entry_iso,
                    exit_price  = trade.get('exit_price', exit_price),
                    exit_reason = reason,
                    realized_pnl= trade.get('pnl', 0),
                    mins_in_fvg = _mins,
                )
        except Exception as _je:
            logger.debug(f"CSV journal exit skipped: {_je}")

        try:
            import threading as _t
            from data.trade_lessons import record_trade_lesson
            _t.Thread(
                target=record_trade_lesson, args=(dict(trade),), daemon=True
            ).start()
        except Exception:
            pass

        # ── ML outcome capture ────────────────────────────────────────────────
        try:
            from ml.nse_collector import record_outcome as ml_outcome
            ml_outcome(trade, exit_reason=reason, exit_price=exit_price,
                       pnl_inr=trade['pnl'])
        except Exception as _ml_e:
            logger.debug(f"ML NSE outcome capture skipped: {_ml_e}")

        # ── ML shadow monitor + auto-trainer ──────────────────────────────────
        try:
            actual_r  = trade.get('r_multiple', 0) or 0
            act_res   = 'WIN' if trade.get('pnl', 0) >= 0 else 'LOSS'
            from ml.shadow_monitor import on_trade_closed
            on_trade_closed(trade['id'], 'nse', '', act_res, float(actual_r))
            from ml.auto_trainer import check_and_train
            check_and_train('nse', '')
        except Exception as _ml_e:
            logger.debug(f"ML NSE monitor/train skipped: {_ml_e}")

        try:
            rr = trade.get('r_multiple')
            if rr is None:
                risk = abs(float(trade.get('entry_price', 0)) - float(trade.get('stop_loss', trade.get('current_sl', 0))))
                rr = round((float(trade.get('pnl', 0)) / max(risk * max(float(trade.get('original_quantity', trade.get('quantity', 1))), 1.0), 1e-9)), 2)
            outcome = 'WIN' if float(trade.get('pnl', 0)) > 0 else ('BREAKEVEN' if float(trade.get('pnl', 0)) == 0 else 'LOSS')
            log_closed_trade(
                'nse', 'nse_paper_trader', trade,
                result=outcome,
                rr_achieved=rr,
                metadata={'exit_reason': reason, 'mode': 'paper'},
            )
            archive_closed_trade_shadow(
                'nse', 'nse_paper_trader', trade,
                result=outcome,
                rr_achieved=rr,
                metadata={'exit_reason': reason, 'mode': 'paper'},
            )
        except Exception:
            pass

        save_state(state)
        try:
            from utils.hermes_close_adapter import (
                is_trade_durably_closed,
                notify_hermes_trade_closed,
            )
            if is_trade_durably_closed(load_state, trade):
                notify_hermes_trade_closed(
                    trade,
                    source='nse_paper_close',
                    account='nse_paper_trader',
                    market='nse',
                )
        except Exception as _hermes_e:
            logger.debug(f"Hermes paper close observer skipped: {_hermes_e}")

        clean      = trade['symbol'].replace("NSE:", "").replace("-EQ", "")
        result     = "WIN" if trade['pnl'] >= 0 else "LOSS"
        orig_qty   = trade.get('original_quantity', remaining_qty)
        targets    = ', '.join(trade.get('targets_hit', [])) or 'None'
        brok_paid  = trade.get('brokerage_paid', 0)
        partial_line = (
            f"Partial PnL : Rs {partial_pnl:.0f} (T1+T2)\n"
            if trade.get('targets_hit') else ""
        )

        send_message(
            "CB6 QUANTUM - TRADE CLOSED\n\n"
            f"Symbol  : {clean}\n"
            f"Reason  : {reason}\n"
            f"Result  : {result}\n\n"
            f"Entry   : {trade['entry_price']}\n"
            f"Exit    : {exit_price}\n"
            f"Qty     : {orig_qty} | Lots: {orig_qty // (_lot_size(trade['symbol']) or 1)}\n"
            f"Targets : {targets}\n"
            + partial_line
            + f"Gross PnL   : Rs {(partial_pnl + gross_final):.0f}\n"
            f"Brokerage   : Rs {brok_paid:.0f} (STT+tax)\n"
            f"Net PnL     : Rs {trade['pnl']:.0f}\n\n"
            f"Portfolio   : Rs {state['total_pnl']:.0f}\n"
            f"Capital     : Rs {state['available_capital']:.0f}"
        )

        logger.info(f"Trade closed: {clean} PnL: {trade['pnl']:.0f} (brok: {brok_paid:.0f}")

        # ── Trade verifier: record exit ───────────────────────────────────────
        try:
            from utils.trade_verifier import get_verifier
            get_verifier().record_exit(
                trade_id       = trade.get('id', ''),
                exit_price     = exit_price,
                exit_reason    = reason,
                gross_pnl      = round(partial_pnl + gross_final, 2),
                brokerage      = trade.get('brokerage_paid', 0),
                net_pnl        = trade.get('pnl', 0),
                telegram_sent  = True,   # send_message called above
                ml_updated     = True,   # ml.nse_collector.record_outcome called above
                excel_written  = False,  # updated asynchronously; mark via update_flags later
                journal_updated= bool(trade.get('journal_id')),
            )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Close trade error: {e}")

def close_paper_trade_by_id(trade_id, exit_price, reason='SL_HIT_WS'):
    """
    Close a paper trade by ID at a specific exit price (called from tick watcher).
    Idempotent: safe to call even if trade already closed.
    """
    # REQ-2: Serialize with open_paper_trade and update_paper_trades.
    _state_lock.acquire()
    try:
        state = load_state()
        # Find the open trade
        match = None
        for t in state.get('open_trades', []):
            if t.get('id') == trade_id:
                match = t
                break
        if not match:
            logger.debug(f"close_by_id: trade {trade_id} not in open_trades (already closed?)")
            return False

        # Compute final P&L (with brokerage, matching close_paper_trade path)
        from utils.brokerage import net_pnl as _net_pnl
        entry = match['entry_price']
        qty   = match['quantity']
        direction = (match.get('direction') or 'BUY').upper()
        partial_pnl = match.get('realized_pnl', 0) or 0
        is_option = (match.get('instrument_type', 'OPTION').upper() == 'OPTION')
        if is_option or direction in ('BUY', 'BULLISH'):
            gross = (exit_price - entry) * qty
        else:
            gross = (entry - exit_price) * qty
        leg_pnl, costs = _net_pnl(gross, entry, exit_price, qty, direction)
        match['brokerage_paid'] = round(match.get('brokerage_paid', 0) + costs['total'], 2)
        match['pnl']        = round(partial_pnl + leg_pnl, 2)
        match['exit_price'] = exit_price
        match['exit_time']  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        match['status']     = reason

        # Move to closed
        state['open_trades']   = [t for t in state['open_trades'] if t.get('id') != trade_id]
        state['closed_trades'].append(match)
        state['total_pnl']     = round(state.get('total_pnl', 0) + leg_pnl, 2)
        state['available_capital'] = round(
            state.get('available_capital', CAPITAL) + match.get('capital_used', 0) + leg_pnl, 2
        )
        if leg_pnl < 0:
            state['daily_losses'] = state.get('daily_losses', 0) + 1
        save_state(state)
        try:
            from utils.hermes_close_adapter import (
                is_trade_durably_closed,
                notify_hermes_trade_closed,
            )
            if is_trade_durably_closed(load_state, match):
                notify_hermes_trade_closed(
                    match,
                    source='nse_paper_close_by_id',
                    account='nse_paper_trader',
                    market='nse',
                )
        except Exception as _hermes_e:
            logger.debug(f"Hermes paper close-by-id observer skipped: {_hermes_e}")

        from journal.trade_journal import log_trade
        log_trade(match)

        clean = match['symbol'].replace('NSE:', '').replace('-EQ', '')
        send_message(
            f"CB6 QUANTUM - REALTIME EXIT ({reason})\n\n"
            f"Symbol  : {clean}\n"
            f"Exit    : {exit_price}\n"
            f"PnL     : Rs {leg_pnl:.0f}\n"
            f"Trigger : WebSocket tick"
        )
        logger.info(f"WS close: {clean} @ {exit_price} | reason={reason} | PnL={leg_pnl:.0f}")
        try:
            rr = match.get('r_multiple')
            if rr is None:
                risk = abs(float(match.get('entry_price', 0)) - float(match.get('stop_loss', match.get('current_sl', 0))))
                rr = round((float(match.get('pnl', 0)) / max(risk * max(float(match.get('original_quantity', match.get('quantity', 1))), 1.0), 1e-9)), 2)
            outcome = 'WIN' if float(match.get('pnl', 0)) > 0 else ('BREAKEVEN' if float(match.get('pnl', 0)) == 0 else 'LOSS')
            log_closed_trade(
                'nse', 'nse_paper_trader', match,
                result=outcome,
                rr_achieved=rr,
                metadata={'exit_reason': reason, 'mode': 'paper_ws'},
            )
            archive_closed_trade_shadow(
                'nse', 'nse_paper_trader', match,
                result=outcome,
                rr_achieved=rr,
                metadata={'exit_reason': reason, 'mode': 'paper_ws'},
            )
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"close_paper_trade_by_id error: {e}")
        return False
    finally:
        _state_lock.release()   # REQ-2


def handle_target_hit_by_id(trade_id, target_idx, hit_price):
    """
    Mark a target hit on an open trade (called from tick watcher).
    Books partial: T1=50%, T2=30% of remaining, T3=full close.
    Also promotes SL: T1 hit → SL to entry (BE), T2 hit → SL to T1.
    Idempotent.
    """
    # REQ-2: Serialize with other state writers.
    _state_lock.acquire()
    try:
        state = load_state()
        match = None
        for t in state.get('open_trades', []):
            if t.get('id') == trade_id:
                match = t
                break
        if not match:
            return False

        target_key  = f'T{target_idx}'
        if target_key in set(match.get('targets_hit', [])):
            return False   # already booked

        if target_idx == 3:
            match.setdefault('targets_hit', []).append(target_key)
            close_paper_trade(match, hit_price, 'TARGET_HIT_WS', state)
            return True
        else:
            booked_qty, partial_pnl = _partial_book(match, hit_price, 0.33, state)
            match.setdefault('targets_hit', []).append(target_key)

        # Promote SL on T1/T2 hit
        if target_idx == 1:
            match['current_sl'] = match['entry_price']
        elif target_idx == 2:
            match['current_sl'] = match.get('target1', match['current_sl'])

        save_state(state)
        _rearm_trade_triggers(match)
        clean = match['symbol'].replace('NSE:', '').replace('-EQ', '')
        send_message(
            f"CB6 QUANTUM - REALTIME T{target_idx} HIT\n\n"
            f"Symbol  : {clean}\n"
            f"Target  : T{target_idx} @ {hit_price}\n"
            f"Booked  : {booked_qty} qty\n"
            f"PnL     : Rs {partial_pnl:.0f}\n"
            f"New SL  : {match['current_sl']}\n"
            f"Trigger : WebSocket tick"
        )
        logger.info(f"WS target hit: {clean} T{target_idx}@{hit_price} PnL={partial_pnl:.0f}")
        return True
    except Exception as e:
        logger.error(f"handle_target_hit_by_id error: {e}")
        return False
    finally:
        _state_lock.release()   # REQ-2


def square_off_all_trades(fyers):
    """
    3:15pm square-off: close only intraday trades (5min / 15min timeframe)
    where T1 has already been hit (SL is trailing, partial profit booked).
    Swing trades (60min / overnight) are left untouched.
    """
    try:
        from scanner.live_price import get_live_price
        state       = load_state()
        open_trades = state.get('open_trades', [])

        if not open_trades:
            send_message("CB6 QUANTUM - No open trades to square off.")
            return

        INTRADAY_TF = {'5min', '15min', '5', '15'}

        candidates = [
            t for t in open_trades
            if t.get('timeframe', '') in INTRADAY_TF
        ]
        skipped = [
            t for t in open_trades if t not in candidates
        ]

        if not candidates:
            send_message(
                "CB6 QUANTUM - SQUARE OFF\n\n"
                "No open intraday trades to close.\n"
                f"Swing/overnight trades kept open: {len(skipped)}"
            )
            return

        send_message(
            f"CB6 QUANTUM - SQUARE OFF (3:15pm)\n\n"
            f"Closing {len(candidates)} intraday trade(s).\n"
            + (f"Keeping {len(skipped)} swing/overnight open.\n" if skipped else "")
            + "Market closes in 15 minutes."
        )

        for trade in candidates:
            symbol = trade['symbol']
            price  = get_live_price(fyers, symbol)
            if not price:
                from scanner.data_fetcher import get_historical_data
                df    = get_historical_data(fyers, symbol, "15", days=1)
                price = df['close'].iloc[-1] if df is not None and len(df) > 0 else trade['entry_price']
            close_paper_trade(trade, price, 'SQUARE_OFF', state)

        logger.info(f"Square off: {len(candidates)} intraday closed, {len(skipped)} swing kept")

    except Exception as e:
        logger.error(f"Square off error: {e}")


def get_portfolio_summary():
    """Get and send portfolio summary"""
    try:
        import csv as _csv, os as _os
        state    = load_state()
        closed   = state['closed_trades']

        # Merge with archive CSV so metrics survive after /archive clears state
        archive_path = _os.path.join(_os.path.dirname(__file__), '..', 'data', 'cb6_master_archive.csv')
        if _os.path.exists(archive_path):
            try:
                with open(archive_path, newline='') as f:
                    archived = list(_csv.DictReader(f))
                # Don't double-count trades already in closed list
                closed_ids = {t.get('id', '') for t in closed}
                for row in archived:
                    if row.get('id', '') not in closed_ids:
                        try:
                            closed.append({
                                'pnl'           : float(row.get('pnl', 0) or 0),
                                'brokerage_paid': float(row.get('brokerage_paid', 0) or 0),
                                'exit_time'     : row.get('exit_time', ''),
                                'id'            : row.get('id', ''),
                            })
                        except Exception:
                            pass
            except Exception:
                pass

        today    = datetime.now().strftime('%Y-%m-%d')
        total    = len(closed)
        wins     = sum(1 for t in closed if t.get('pnl', 0) > 0)
        losses   = sum(1 for t in closed if t.get('pnl', 0) < 0)
        win_rate = round(wins / total * 100, 1) if total > 0 else 0
        open_pnl = sum(t.get('pnl', 0) for t in state['open_trades'])
        realized = state['total_pnl']
        total_brok = sum(t.get('brokerage_paid', 0) for t in closed)

        send_message(
            "CB6 QUANTUM - PORTFOLIO\n\n"
            f"Capital      : Rs {state['capital']:,.0f}\n"
            f"Available    : Rs {state['available_capital']:,.0f}\n"
            f"Realized PnL : Rs {realized:,.0f}\n"
            f"Open PnL     : Rs {open_pnl:,.0f}\n"
            f"Total PnL    : Rs {realized + open_pnl:,.0f}\n"
            f"Brokerage    : Rs {total_brok:,.0f}\n\n"
            f"Closed Trades: {total} ({wins}W / {losses}L)\n"
            f"Open Trades  : {len(state['open_trades'])}\n"
            f"Win Rate     : {win_rate}%"
        )

        return state

    except Exception as e:
        logger.error(f"Portfolio summary error: {e}")

