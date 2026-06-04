# communications/bot_crypto.py
#
# Dedicated Telegram bot for the BTC crypto engine.
# Uses CRYPTO_TELEGRAM_TOKEN + CRYPTO_TELEGRAM_CHAT_ID — completely independent
# of the NSE bot (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).
#
# Commands:
#   /btc_status    — engine health, window, last scan time
#   /btc_pnl       — today's P&L summary
#   /btc_positions — open trades with live mark price
#   /btc_stop      — pause the crypto engine
#   /btc_resume    — resume the crypto engine
#   /btc_help      — strategy rules + command list

import os
import sys
import time
import threading
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.logger import logger

# ── Config ────────────────────────────────────────────────────────────────────

CRYPTO_TOKEN   = os.getenv('CRYPTO_TELEGRAM_TOKEN', '')
CRYPTO_CHAT_ID = os.getenv('CRYPTO_TELEGRAM_CHAT_ID', '')

_last_update_id = 0
_adapter_ref    = None   # BinanceAdapter — set by crypto_worker


def set_adapter(adapter):
    global _adapter_ref
    _adapter_ref = adapter


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _send(text: str) -> bool:
    if not CRYPTO_TOKEN or not CRYPTO_CHAT_ID:
        logger.warning("Crypto bot: token/chat ID not configured")
        return False
    try:
        url  = f"https://api.telegram.org/bot{CRYPTO_TOKEN}/sendMessage"
        data = {
            'chat_id'    : CRYPTO_CHAT_ID,
            'text'       : text,
            'parse_mode' : 'HTML',
        }
        r = requests.post(url, data=data, timeout=10)
        if r.status_code != 200:
            logger.warning(f"Crypto bot send failed: {r.status_code} {r.text[:200]}")
            return False
        return True
    except Exception as e:
        logger.error(f"Crypto bot send error: {e}")
        return False


def _get_updates() -> list:
    global _last_update_id
    if not CRYPTO_TOKEN:
        return []
    try:
        url    = f"https://api.telegram.org/bot{CRYPTO_TOKEN}/getUpdates"
        params = {'offset': _last_update_id + 1, 'timeout': 5}
        r = requests.get(url, params=params, timeout=(5, 10))
        if r.status_code == 200:
            return r.json().get('result', [])
        return []
    except requests.exceptions.Timeout:
        return []
    except Exception as e:
        logger.warning(f"Crypto bot get_updates error: {e}")
        return []


# ── Command handlers ──────────────────────────────────────────────────────────

def _cmd_status():
    try:
        from crypto_engine.crypto_paper_trader import get_crypto_summary, load_state
        s     = load_state()
        summ  = get_crypto_summary()

        # Pull live Binance balance as source of truth
        bnb_balance = None
        if _adapter_ref:
            try:
                bnb_balance = _adapter_ref.get_usdt_balance()
            except Exception:
                pass

        heartbeat_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'data', 'crypto_heartbeat.txt'
        )
        last_beat = 'N/A'
        if os.path.exists(heartbeat_file):
            age = int(time.time() - os.path.getmtime(heartbeat_file))
            last_beat = f"{age}s ago"

        import datetime, pytz
        now_ist   = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
        weekday   = now_ist.weekday()
        day_names = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        window    = (f'Trading ({day_names[weekday]}) — 24/5 active'
                     if weekday < 5 else
                     f'Weekend REST ({day_names[weekday]}) — resumes Mon')

        # Live mark prices + unrealized PnL per open trade (correct symbol per trade)
        open_trades  = s.get('open_trades', [])
        total_unrealised = 0.0
        mark_lines   = []
        if _adapter_ref and open_trades:
            seen_syms = {}
            for t in open_trades:
                sym = t.get('symbol', 'ETHUSDT')
                if sym not in seen_syms:
                    try:
                        seen_syms[sym] = _adapter_ref.get_mark_price(sym)
                    except Exception:
                        seen_syms[sym] = None
                mark = seen_syms[sym]
                if mark:
                    is_long = t['direction'] == 'BULLISH'
                    upnl = round((mark - t['entry_price']) * t['qty_btc'] *
                                 (1 if is_long else -1), 2)
                    total_unrealised += upnl
                    mark_lines.append(
                        f"{sym[:3]} Mark: ${mark:,.2f}  "
                        f"UPnL: ${upnl:+.2f}")
        elif _adapter_ref:
            try:
                m = _adapter_ref.get_mark_price('ETHUSDT')
                if m:
                    mark_lines.append(f"ETH Mark: ${m:,.2f}")
            except Exception:
                pass

        realized_pnl   = round(s.get('daily_pnl', 0), 2)
        total_pnl_line = (f"Realised:    ${realized_pnl:+.2f}\n"
                          f"Unrealised:  ${total_unrealised:+.2f}\n"
                          f"Total MTM:   ${realized_pnl + total_unrealised:+.2f}")

        marks_str  = '\n'.join(mark_lines) if mark_lines else 'ETH Mark: N/A'
        paused_str = 'PAUSED' if summ['paused'] else 'RUNNING'
        starting   = summ.get('starting_capital', summ['capital'])

        # Use Binance real balance as equity truth; fall back to state
        display_equity = bnb_balance if bnb_balance is not None else summ['capital']
        net_growth     = round(display_equity - starting, 2)
        pct_growth     = round(net_growth / starting * 100, 1) if starting else 0
        bnb_str        = f"${bnb_balance:,.4f}" if bnb_balance is not None else "N/A"

        _send(
            f"<b>CB6 CRYPTO — ENGINE STATUS</b>\n"
            f"<b>🔴 LIVE TRADING · Binance USDT-M Futures</b>\n\n"
            f"State      : {paused_str}\n"
            f"Window     : {window}\n"
            f"{marks_str}\n"
            f"Heartbeat  : {last_beat}\n\n"
            f"Binance Bal: {bnb_str} USDT\n"
            f"Started    : ${starting:,.4f} USDT\n"
            f"Growth     : ${net_growth:+.4f} ({pct_growth:+.1f}%)\n"
            f"Open trades: {summ['open_count']}\n\n"
            f"<b>TODAY P&amp;L (software est.)</b>\n"
            f"{total_pnl_line}\n\n"
            f"Trades: {summ['today_trades']}  "
            f"{summ['today_wins']}W / {summ['today_losses']}L"
        )
    except Exception as e:
        _send(f"Status error: {e}")


def _cmd_pnl():
    try:
        from crypto_engine.crypto_paper_trader import load_state, DEFAULT_CAPITAL
        import datetime

        state    = load_state()
        today    = datetime.datetime.now().strftime('%Y-%m-%d')
        closed   = state.get('closed_trades', [])
        starting = state.get('starting_capital', state.get('capital', DEFAULT_CAPITAL))

        today_closed = [t for t in closed if (t.get('exit_time') or '')[:10] == today]
        wins         = sum(1 for t in today_closed if t.get('pnl_usdt', 0) > 0)
        losses       = sum(1 for t in today_closed if t.get('pnl_usdt', 0) < 0)
        all_wins     = sum(1 for t in closed if t.get('pnl_usdt', 0) > 0)
        wr           = round(all_wins / len(closed) * 100, 1) if closed else 0

        # ── Binance as source of truth ────────────────────────────────────────
        bnb_balance  = None
        bnb_pnl_all  = None
        bnb_pnl_today = None
        if _adapter_ref:
            try:
                bnb_balance = _adapter_ref.get_usdt_balance()
            except Exception:
                pass
            try:
                # All-time realized PnL (last 500 entries)
                all_entries = _adapter_ref.get_realized_pnl('ETHUSDT', limit=500) or []
                bnb_pnl_all = round(sum(e['income'] for e in all_entries), 4)
                # Today only
                today_ms = int(datetime.datetime.strptime(today, '%Y-%m-%d').timestamp() * 1000)
                today_entries = _adapter_ref.get_realized_pnl('ETHUSDT', since_ms=today_ms,
                                                               limit=100) or []
                bnb_pnl_today = round(sum(e['income'] for e in today_entries), 4)
            except Exception:
                pass

        equity     = bnb_balance if bnb_balance is not None else state.get('capital', DEFAULT_CAPITAL)
        net_growth = round(equity - starting, 4)
        pct_growth = round(net_growth / starting * 100, 2) if starting else 0

        # Display lines — prefer Binance, show software estimate in brackets if different
        sw_today = round(sum(t.get('pnl_usdt', 0) for t in today_closed), 4)
        sw_all   = round(sum(t.get('pnl_usdt', 0) for t in closed), 4)

        today_pnl_line = (
            f"${bnb_pnl_today:+.4f} USDT (Binance)" if bnb_pnl_today is not None
            else f"${sw_today:+.4f} USDT (est.)"
        )
        all_pnl_line = (
            f"${bnb_pnl_all:+.4f} USDT (Binance)" if bnb_pnl_all is not None
            else f"${sw_all:+.4f} USDT (est.)"
        )
        balance_line = (
            f"${bnb_balance:,.4f} USDT (Binance live)" if bnb_balance is not None
            else f"${state.get('available_capital', DEFAULT_CAPITAL):,.4f} USDT (state)"
        )

        _send(
            f"<b>CB6 CRYPTO — P&amp;L SUMMARY</b>\n\n"
            f"<b>TODAY ({today})</b>\n"
            f"Trades     : {len(today_closed)}  ({wins}W / {losses}L)\n"
            f"Today PnL  : {today_pnl_line}\n\n"
            f"<b>ALL TIME</b>\n"
            f"Total Trades: {len(closed)}\n"
            f"Win Rate    : {wr}%\n"
            f"Total PnL   : {all_pnl_line}\n\n"
            f"<b>ACCOUNT</b>\n"
            f"Starting    : ${starting:,.4f}\n"
            f"Balance     : {balance_line}\n"
            f"Net Growth  : ${net_growth:+.4f} ({pct_growth:+.2f}%)"
        )
    except Exception as e:
        _send(f"PnL error: {e}")


def _cmd_positions():
    try:
        from crypto_engine.crypto_paper_trader import load_state

        state       = load_state()
        open_trades = state.get('open_trades', [])

        if not open_trades:
            _send("CB6 CRYPTO — No open positions.")
            return

        _send(f"CB6 CRYPTO 🔴 LIVE — {len(open_trades)} Open Position(s)")

        mark_cache = {}
        for t in open_trades:
            direction = t['direction']
            entry     = t['entry_price']
            qty       = t['qty_btc']
            sl        = t['current_sl']
            t1        = t['target1']
            t2        = t['target2']
            t3        = t['target3']
            sym       = t.get('symbol', 'ETHUSDT')
            is_long   = direction == 'BULLISH'
            targets   = ', '.join(t.get('targets_hit', [])) or 'None'

            # Fetch live mark price per symbol (correct symbol, not hardcoded)
            if sym not in mark_cache and _adapter_ref:
                try:
                    mark_cache[sym] = _adapter_ref.get_mark_price(sym)
                except Exception:
                    mark_cache[sym] = None
            mark = mark_cache.get(sym)

            if mark:
                upnl     = round((mark - entry) * qty * (1 if is_long else -1), 2)
                mark_str = f"${mark:,.2f}"
                sl_dist  = round(abs(mark - sl), 2)
                t1_dist  = round(abs(t1 - mark), 2)
            else:
                upnl     = 0.0
                mark_str = 'N/A'
                sl_dist  = round(abs(entry - sl), 2)
                t1_dist  = round(abs(t1 - entry), 2)

            dir_arrow = '🟢 LONG' if is_long else '🔴 SHORT'
            _send(
                f"<b>{t['id']}</b>  {dir_arrow}\n"
                f"Entry     : ${entry:,.2f}\n"
                f"Mark      : {mark_str}\n"
                f"Unrealised: ${upnl:+.2f}\n"
                f"SL        : ${sl:,.2f}  ({sl_dist} pts away)\n"
                f"T1        : ${t1:,.2f}  ({t1_dist} pts)\n"
                f"T2        : ${t2:,.2f}\n"
                f"T3        : ${t3:,.2f}\n"
                f"Qty       : {qty} {sym[:3]}\n"
                f"Targets   : {targets}\n"
                f"Opened    : {t['entry_time']}"
            )
            time.sleep(0.3)
    except Exception as e:
        _send(f"Positions error: {e}")


def _cmd_stop():
    try:
        from crypto_engine.crypto_paper_trader import load_state, save_state
        state = load_state()
        state['paused'] = True
        save_state(state)
        _send(
            "CB6 CRYPTO — Engine paused.\n"
            "No new LIVE trades will open.\n"
            "Open positions continue to be monitored.\n"
            "Send /btc_resume to re-enable."
        )
    except Exception as e:
        _send(f"Stop error: {e}")


def _cmd_resume():
    try:
        from crypto_engine.crypto_paper_trader import load_state, save_state
        state = load_state()
        state['paused'] = False
        save_state(state)
        _send("CB6 CRYPTO — Engine resumed. 🔴 LIVE trading re-enabled.")
    except Exception as e:
        _send(f"Resume error: {e}")


def _cmd_memory():
    try:
        from crypto_engine.trade_memory import memory_summary
        _send(memory_summary())
    except Exception as e:
        _send(f"Memory error: {e}")


def _cmd_binance():
    """Fetch live account data directly from Binance — balance, positions, recent realized PnL."""
    try:
        if not _adapter_ref:
            _send("Adapter not ready — engine still starting up.")
            return

        balance = _adapter_ref.get_usdt_balance()
        acct    = _adapter_ref.get_account()

        # Positions
        pos_lines = []
        if acct:
            for p in acct.get('positions', []):
                amt = float(p.get('positionAmt', 0))
                if amt == 0:
                    continue
                upnl = float(p.get('unrealizedProfit', 0))
                side = 'LONG' if amt > 0 else 'SHORT'
                pos_lines.append(
                    f"{p['symbol']} {side} {abs(amt)} "
                    f"entry={float(p.get('entryPrice',0)):,.2f} "
                    f"UPnL=${upnl:+.2f}"
                )

        # Recent realized PnL (last 20 entries across all symbols)
        pnl_entries = _adapter_ref.get_realized_pnl('ETHUSDT', limit=20) or []
        recent_pnl  = round(sum(e['income'] for e in pnl_entries), 4)

        pos_str = '\n'.join(pos_lines) if pos_lines else 'No open positions'
        _send(
            f"<b>CB6 CRYPTO — BINANCE LIVE</b>\n\n"
            f"USDT Balance  : ${balance:,.4f}\n\n"
            f"<b>OPEN POSITIONS</b>\n{pos_str}\n\n"
            f"<b>RECENT REALIZED PnL (last 20 ETHUSDT)</b>\n"
            f"${recent_pnl:+.4f} USDT"
        )
    except Exception as e:
        _send(f"Binance fetch error: {e}")


def _cmd_help():
    _send(
        "<b>CB6 CRYPTO — ETH ICT SILVER BULLET 🔴 LIVE</b>\n\n"
        "<b>MARKET</b>\n"
        "Instrument : ETH/USDT Perpetual Futures (Binance USDT-M)\n"
        "Mode       : 🔴 LIVE TRADING\n\n"
        "<b>SCHEDULE</b>\n"
        "Mon – Fri : 24 hrs (all day &amp; night)\n"
        "Sat – Sun : REST (no trades)\n\n"
        "<b>SETUP CHAIN</b>\n"
        "1. Draw on Liquidity — unswept swing high/low\n"
        "2. Market Structure Shift — CHoCH or BOS\n"
        "3. Fair Value Gap — displaced 3-candle imbalance (wicks)\n"
        "4. OB + UT Bot + 3-Bar Reversal — confluence filters\n"
        "5. Entry — mark price inside FVG at open\n\n"
        "<b>TRADE PLAN</b>\n"
        "Timeframe : 5 min\n"
        "SL        : Opposite FVG edge\n"
        "T1 (1/3)  : 2R  — SL trails to breakeven\n"
        "T2 (1/3)  : 3R\n"
        "T3 (1/3)  : 4R or DOL level\n"
        "Risk      : 5% of available capital per trade\n"
        "Min qty   : 0.001 ETH\n\n"
        "<b>DAILY LIMITS</b>\n"
        "Max trades : 3 per day\n"
        "Loss limit : Stop after 2 consecutive SL hits (no booked targets)\n\n"
        "<b>COMMANDS</b>\n"
        "/btc_status    Engine health + window\n"
        "/btc_pnl       P&amp;L summary (today + all time)\n"
        "/btc_positions Open trades with unrealised P&amp;L\n"
        "/btc_memory    Pattern win rates (trade memory)\n"
        "/btc_binance   Live Binance balance + realized PnL\n"
        "/btc_stop      Pause the engine\n"
        "/btc_resume    Resume the engine\n"
        "/btc_help      This message"
    )


# ── Command dispatch ──────────────────────────────────────────────────────────

def _cmd_start():
    try:
        from crypto_engine.crypto_paper_trader import get_crypto_summary
        s = get_crypto_summary()
        pnl_sign = '+' if s['today_pnl'] >= 0 else ''

        # Prefer live Binance balance
        bnb_balance = None
        if _adapter_ref:
            try:
                bnb_balance = _adapter_ref.get_usdt_balance()
            except Exception:
                pass
        display_bal = bnb_balance if bnb_balance is not None else s['available']
        bal_label   = "Binance Bal" if bnb_balance is not None else "Available"

        _send(
            "<b>CB6 CRYPTO BOT — ETH/USDT Perpetual 🔴 LIVE</b>\n\n"
            "ICT Silver Bullet strategy · Binance USDT-M Futures.\n\n"
            "<b>SCHEDULE</b>\n"
            "Mon – Fri : 24 hrs (all day &amp; night)\n"
            "Sat – Sun : REST — no trades\n\n"
            "<b>STRATEGY CHAIN</b>\n"
            "DOL → MSS → FVG → Live Mark Price Gate → Entry\n\n"
            "<b>ACCOUNT</b>\n"
            f"{bal_label}: ${display_bal:,.4f} USDT\n"
            f"Growth     : ${round(s['growth'],4):+.4f} USDT\n"
            f"Open trades: {s['open_count']}\n"
            f"Today PnL  : ${s['today_pnl']:{pnl_sign}.2f} USDT\n"
            f"Status     : {'⏸ PAUSED' if s['paused'] else '▶ RUNNING'}\n\n"
            "<b>COMMANDS</b>\n"
            "/btc_status    — Engine health + BTC price\n"
            "/btc_pnl       — Full P&amp;L breakdown\n"
            "/btc_positions — Open trades + unrealised P&amp;L\n"
            "/btc_stop      — Pause engine (no new trades)\n"
            "/btc_resume    — Resume engine\n"
            "/btc_help      — Full strategy rules\n\n"
            "Mode: 🔴 LIVE TRADING (Binance USDT-M Futures)\n"
            "Dashboard: http://localhost:8080 → BTC CRYPTO tab"
        )
    except Exception as e:
        _send(f"Start error: {e}")


_COMMANDS = {
    '/start'         : _cmd_start,
    '/btc_status'    : _cmd_status,
    '/btc_pnl'       : _cmd_pnl,
    '/btc_positions' : _cmd_positions,
    '/btc_memory'    : _cmd_memory,
    '/btc_binance'   : _cmd_binance,
    '/btc_stop'      : _cmd_stop,
    '/btc_resume'    : _cmd_resume,
    '/btc_help'      : _cmd_help,
}


def _process_updates(updates: list):
    global _last_update_id
    for update in updates:
        _last_update_id = update['update_id']
        message = update.get('message', {})
        text    = message.get('text', '').strip()
        chat_id = str(message.get('chat', {}).get('id', ''))

        if chat_id != str(CRYPTO_CHAT_ID):
            continue

        # Strip @botname suffix
        if text.startswith('/') and '@' in text.split()[0]:
            first, *rest = text.split()
            text = ' '.join([first.split('@')[0]] + rest)

        logger.info(f"Crypto bot command: {text}")
        fn = _COMMANDS.get(text)
        if fn:
            threading.Thread(target=fn, daemon=True).start()
        elif text.startswith('/'):
            _send(
                f"Unknown command: {text}\n"
                "Use /btc_help to see all commands."
            )


# ── Listener loop ─────────────────────────────────────────────────────────────

def start_listening():
    """
    Blocking poll loop — run in a dedicated daemon thread from crypto_worker.
    Polls every 3 seconds (Telegram long-poll timeout = 5s).
    """
    if not CRYPTO_TOKEN:
        logger.warning("Crypto bot: CRYPTO_TELEGRAM_TOKEN not set — listener disabled")
        return

    while True:
        try:
            updates = _get_updates()
            if updates:
                _process_updates(updates)
            time.sleep(3)
        except Exception as e:
            logger.error(f"Crypto bot listener error: {e}")
            time.sleep(10)


def send_alert(text: str) -> bool:
    """Public helper — used by crypto_worker to push trade alerts."""
    return _send(text)
