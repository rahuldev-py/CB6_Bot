# trader/live_trader.py — CB6 Bot Live Order Engine (Fyers API)
# WARNING: Places REAL orders with REAL money.
# Paper-trade first. Enable only after win rate validation (≥56%).
#
# NOTE: Partial-booking logic (33% T1 / 33% T2 / 34% T3) NOT yet ported here.
# When validation passes, port from paper_trader.py before going live.
import os, sys, json, threading
from datetime import datetime
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger
from utils.telegram_alerts import send_message
from settings import CAPITAL, MAX_TRADES_PER_DAY, RISK_PER_TRADE_PCT

STATE_FILE          = os.path.join(os.path.dirname(__file__), '..', 'data', 'live_state.json')
_state_lock         = threading.Lock()
INTRADAY_MARGIN_PCT = 0.20   # 20% margin on short positions (intraday MIS)


# ── STATE ─────────────────────────────────────────────────────────────────────

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {
        'capital': CAPITAL, 'available_capital': CAPITAL,
        'open_trades': [], 'closed_trades': [],
        'daily_losses': 0, 'daily_trades': 0,
        'total_pnl': 0, 'date': datetime.now().strftime('%Y-%m-%d')
    }


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with _state_lock:
            tmp = STATE_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(state, f, indent=2, default=str)
            os.replace(tmp, STATE_FILE)
    except Exception as e:
        logger.error(f"Live save_state error: {e}")


def reset_daily_counters(state):
    today = datetime.now().strftime('%Y-%m-%d')
    if state.get('date') != today:
        state['date']         = today
        state['daily_losses'] = 0
        state['daily_trades'] = 0
        logger.info("Live daily counters reset")
    return state


# ── POSITION SIZING ────────────────────────────────────────────────────────────

def _round_to_lot(qty, symbol):
    """Round down to nearest F&O lot size; 1 for cash equity."""
    try:
        from scanner.index_futures import get_lot_size
        lot = get_lot_size(symbol)
        if lot > 1:
            return max(1, qty // lot) * lot
    except Exception:
        pass
    return max(1, qty)


def _calc_qty_buy(capital, entry, stop_loss, symbol=''):
    """RISK_PER_TRADE_PCT risk per trade, capped at 20% capital. Lot-aware."""
    risk_per_share = entry - stop_loss
    if risk_per_share <= 0:
        return _round_to_lot(1, symbol)
    qty = int((capital * RISK_PER_TRADE_PCT / 100) / risk_per_share)
    if qty * entry > capital * 0.20:
        qty = int(capital * 0.20 / entry)
    return _round_to_lot(qty, symbol)


def _calc_qty_sell(capital, entry, stop_loss, symbol=''):
    """For shorts: SL above entry. Margin = 20% of notional. Lot-aware."""
    risk_per_share = stop_loss - entry
    if risk_per_share <= 0:
        return _round_to_lot(1, symbol)
    qty = int((capital * RISK_PER_TRADE_PCT / 100) / risk_per_share)
    if qty * entry * INTRADAY_MARGIN_PCT > capital * 0.20:
        qty = int(capital * 0.20 / (entry * INTRADAY_MARGIN_PCT))
    return _round_to_lot(qty, symbol)


def _can_trade(state):
    """
    Pre-entry risk gate. Delegates to core.execution_guard for centralized
    risk enforcement. Local capital floor check runs first (broker-specific).
    """
    if state.get('available_capital', 0) < 5000:
        return False, "Insufficient capital (< Rs 5,000 available)"
    from core.execution_guard import guard_dict_entry
    return guard_dict_entry(state, CAPITAL, symbol="", mode="LIVE", intent_type="ENTRY")


# ── FYERS ORDER CALLS ──────────────────────────────────────────────────────────

def _place_order(fyers, symbol, qty, side, product="INTRADAY", tag="CB6LIVE"):
    """
    Place a market order.
    side: 1 = Buy, -1 = Sell
    Returns order_id string or None on failure.
    """
    try:
        clean_tag = ''.join(ch for ch in str(tag or 'CB6LIVE') if ch.isalnum())[:20] or 'CB6LIVE'
        data = {
            "symbol":       symbol,
            "qty":          qty,
            "type":         2,        # 2 = Market
            "side":         side,
            "productType":  product,
            "limitPrice":   0,
            "stopPrice":    0,
            "validity":     "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopLoss":     0,
            "takeProfit":   0,
            "orderTag":     clean_tag,
        }
        resp = fyers.place_order(data=data)
        if resp.get('code') == 200:
            order_id = resp.get('id', '')
            logger.info(f"Order placed: {symbol} qty={qty} side={side} id={order_id}")
            return order_id
        logger.error(f"Order failed: {resp}")
        return None
    except Exception as e:
        logger.error(f"place_order error: {e}")
        return None


def _modify_sl(fyers, order_id, new_stop):
    """Trail stop loss by modifying an existing SL order."""
    try:
        resp = fyers.modify_order(data={"id": order_id, "type": 3, "stopPrice": new_stop})
        if resp.get('s') == 'ok':
            logger.info(f"SL modified: order {order_id} → {new_stop}")
            return True
        logger.error(f"Modify SL failed: {resp}")
        return False
    except Exception as e:
        logger.error(f"modify_sl error: {e}")
        return False


# ── OPEN TRADE ─────────────────────────────────────────────────────────────────

def open_live_trade(fyers, setup):
    """Open a real Fyers trade based on an ICT setup dict."""
    try:
        state     = load_state()
        state     = reset_daily_counters(state)
        sig       = setup['entry_signal']
        symbol    = setup['symbol']
        direction = setup.get('direction', 'BUY')
        clean     = symbol.replace("NSE:", "").replace("-EQ", "")

        required_levels = ('entry', 'stop_loss', 'target1')
        missing_levels = [key for key in required_levels if not sig.get(key)]
        if missing_levels:
            logger.warning(f"Live trade blocked {clean}: missing levels {missing_levels}")
            return None
        if abs(float(sig['entry']) - float(sig['stop_loss'])) <= 0:
            logger.warning(f"Live trade blocked {clean}: sl_pts <= 0")
            return None

        for t in state['open_trades']:
            if t['symbol'] == symbol:
                logger.info(f"Already in: {clean}")
                return None

        ok, reason = _can_trade(state)
        if not ok:
            logger.info(f"Cannot trade: {reason}")
            return None

        try:
            pass  # correlation filter removed (SB-only mode)
        except Exception as e:
            logger.error(f"Live correlation check error: {e}")

        if direction == 'BUY':
            qty          = _calc_qty_buy(state['available_capital'], sig['entry'], sig['stop_loss'], symbol)
            capital_used = qty * sig['entry']
            side         = 1
        else:
            qty          = _calc_qty_sell(state['available_capital'], sig['entry'], sig['stop_loss'], symbol)
            capital_used = qty * sig['entry'] * INTRADAY_MARGIN_PCT
            side         = -1

        order_id = _place_order(fyers, symbol, qty, side)
        if not order_id:
            send_message(f"LIVE ORDER FAILED\n{clean} {direction} qty={qty}\nCheck logs.")
            return None

        trade = {
            'id'           : order_id,
            'symbol'       : symbol,
            'direction'    : direction,
            'timeframe'    : setup.get('timeframe', '15min'),
            'entry_price'  : sig['entry'],
            'stop_loss'    : sig['stop_loss'],
            'target1'      : sig['target1'],
            'target2'      : sig['target2'],
            'target3'      : sig['target3'],
            'quantity'     : qty,
            'position_value': qty * sig['entry'],
            'capital_used' : capital_used,
            'risk'         : sig['risk'],
            'rr_ratio'     : sig['rr_ratio'],
            'entry_time'   : datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'exit_time'    : None, 'exit_price': None,
            'status'       : 'OPEN',
            'current_sl'   : sig['stop_loss'],
            'targets_hit'  : [], 'pnl': 0
        }

        state['open_trades'].append(trade)
        state['available_capital'] -= capital_used
        state['daily_trades']      += 1
        save_state(state)

        send_message(
            f"CB6 LIVE TRADE OPENED\n\n"
            f"ID        : {order_id}\n"
            f"Symbol    : {clean}\n"
            f"Direction : {direction}\n"
            f"Entry     : {sig['entry']}\n"
            f"Stop Loss : {sig['stop_loss']}\n"
            f"Target 1  : {sig['target1']}\n"
            f"Target 2  : {sig['target2']}\n"
            f"Target 3  : {sig['target3']}\n"
            f"Qty       : {qty}\n"
            f"Capital   : Rs {capital_used:.0f}\n"
            f"RR        : 1:{sig['rr_ratio']}"
        )
        logger.info(f"Live trade opened: {clean} {direction} qty={qty}")
        return trade

    except Exception as e:
        logger.error(f"open_live_trade error: {e}")
        return None


# ── MONITOR TRADES ─────────────────────────────────────────────────────────────

def update_live_trades(fyers):
    """Check open live positions, trail SL, close at target or SL."""
    try:
        state = load_state()
        if not state['open_trades']:
            return

        from scanner.data_fetcher import get_historical_data

        for trade in state['open_trades'][:]:
            symbol    = trade['symbol']
            direction = trade.get('direction', 'BUY')

            df = get_historical_data(fyers, symbol, "15", days=1)
            if df is None or len(df) == 0:
                continue

            price = df['close'].iloc[-1]
            hi    = df['high'].iloc[-1]
            lo    = df['low'].iloc[-1]
            clean = symbol.replace("NSE:", "").replace("-EQ", "")

            if direction == 'BUY':
                trade['pnl'] = round((price - trade['entry_price']) * trade['quantity'], 2)

                if lo <= trade['current_sl']:
                    _close_live(fyers, trade, trade['current_sl'], 'SL_HIT', state)
                    continue
                if 'T1' not in trade['targets_hit'] and hi >= trade['target1']:
                    trade['targets_hit'].append('T1')
                    trade['current_sl'] = trade['entry_price']
                    send_message(f"LIVE T1 HIT: {clean}\nSL trailed to entry {trade['entry_price']}")
                if 'T2' not in trade['targets_hit'] and hi >= trade['target2']:
                    trade['targets_hit'].append('T2')
                    new_sl = round(trade['target1'] + (trade['target2'] - trade['target1']) * 0.5, 2)
                    trade['current_sl'] = new_sl
                    send_message(f"LIVE T2 HIT: {clean}\nSL trailed to {new_sl} (mid T1-T2)")
                if hi >= trade['target3']:
                    _close_live(fyers, trade, trade['target3'], 'TARGET_HIT', state)
                    continue

            else:  # SHORT
                trade['pnl'] = round((trade['entry_price'] - price) * trade['quantity'], 2)

                if hi >= trade['current_sl']:
                    _close_live(fyers, trade, trade['current_sl'], 'SL_HIT', state)
                    continue
                if 'T1' not in trade['targets_hit'] and lo <= trade['target1']:
                    trade['targets_hit'].append('T1')
                    trade['current_sl'] = trade['entry_price']
                    send_message(f"LIVE SHORT T1: {clean}\nSL trailed to entry {trade['entry_price']}")
                if 'T2' not in trade['targets_hit'] and lo <= trade['target2']:
                    trade['targets_hit'].append('T2')
                    new_sl = round(trade['target1'] - (trade['target1'] - trade['target2']) * 0.5, 2)
                    trade['current_sl'] = new_sl
                    send_message(f"LIVE SHORT T2: {clean}\nSL trailed to {new_sl} (mid T1-T2)")
                if lo <= trade['target3']:
                    _close_live(fyers, trade, trade['target3'], 'TARGET_HIT', state)
                    continue

            save_state(state)

    except Exception as e:
        logger.error(f"update_live_trades error: {e}")


def _close_live(fyers, trade, exit_price, reason, state):
    """Exit position with a market order and record result."""
    try:
        symbol    = trade['symbol']
        direction = trade.get('direction', 'BUY')
        clean     = symbol.replace("NSE:", "").replace("-EQ", "")
        exit_side = -1 if direction == 'BUY' else 1

        exit_order_id = _place_order(
            fyers, symbol, trade['quantity'], exit_side,
            tag=f"CB6CLOSE{reason}",
        )
        if not exit_order_id:
            logger.critical(f"Live close failed: {clean} reason={reason} qty={trade['quantity']}")
            try:
                send_message(
                    f"CRITICAL LIVE CLOSE FAILED\n{clean}\n"
                    f"Reason: {reason}\nQty: {trade['quantity']}\n"
                    "State remains OPEN. Close manually if needed."
                )
            except Exception:
                pass
            return

        trade['exit_price'] = exit_price
        trade['exit_time']  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        trade['status']     = reason

        from utils.brokerage import net_pnl as _net_pnl
        qty = trade['quantity']
        if direction == 'BUY':
            gross = (exit_price - trade['entry_price']) * qty
        else:
            gross = (trade['entry_price'] - exit_price) * qty
        net_leg, costs = _net_pnl(gross, trade['entry_price'], exit_price, qty, direction)
        trade['brokerage_paid'] = round(trade.get('brokerage_paid', 0) + costs['total'], 2)
        partial_pnl = trade.get('realized_pnl', 0) or 0
        trade['pnl'] = round(partial_pnl + net_leg, 2)

        if trade in state['open_trades']:
            state['open_trades'].remove(trade)
        state['closed_trades'].append(trade)
        state['available_capital'] += trade['capital_used'] + net_leg
        state['total_pnl']         += net_leg
        if trade['pnl'] < 0:
            state['daily_losses'] += 1

        result = "WIN" if trade['pnl'] >= 0 else "LOSS"
        send_message(
            f"CB6 LIVE TRADE CLOSED\n\n"
            f"Symbol : {clean}\n"
            f"Reason : {reason}\n"
            f"Result : {result}\n"
            f"Entry  : {trade['entry_price']}\n"
            f"Exit   : {exit_price}\n"
            f"Qty    : {trade['quantity']}\n"
            f"PnL    : Rs {trade['pnl']:.0f}\n"
            f"Total  : Rs {state['total_pnl']:.0f}"
        )
        save_state(state)
        logger.info(f"Live closed: {clean} {result} PnL={trade['pnl']:.0f}")

    except Exception as e:
        logger.error(f"_close_live error: {e}")


def get_live_summary(fyers):
    """Send live portfolio summary to Telegram."""
    try:
        state    = load_state()
        closed   = state['closed_trades']
        total    = len(closed)
        wins     = sum(1 for t in closed if t.get('pnl', 0) > 0)
        open_pnl = sum(t.get('pnl', 0) for t in state['open_trades'])
        wr       = round(wins / total * 100, 1) if total > 0 else 0

        send_message(
            "CB6 LIVE PORTFOLIO\n\n"
            f"Capital   : Rs {state['available_capital']:.0f}\n"
            f"Realized  : Rs {state['total_pnl']:.0f}\n"
            f"Open PnL  : Rs {open_pnl:.0f}\n"
            f"Total PnL : Rs {state['total_pnl'] + open_pnl:.0f}\n\n"
            f"Closed    : {total}  Win Rate: {wr}%\n"
            f"Open      : {len(state['open_trades'])}\n"
            f"Today     : {state['daily_trades']}/{MAX_TRADES_PER_DAY} trades"
        )
    except Exception as e:
        logger.error(f"live summary error: {e}")
