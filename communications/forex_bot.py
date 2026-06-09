# communications/forex_bot.py
#
# CB6 Quantum — GFT $10K Instant Telegram bot (@cb6forexbot)
#
# Token  : TELEGRAM_BOT_TOKEN_FTMO   (existing @cb6forexbot token — repurposed)
# Auth   : CB6_ADMIN_USER_ID
# Scope  : GFT $10K Instant account only
#
# Commands:
#   /start            — GFT $10K overview + command list
#   /g10k_status      — Engine health + capital + heartbeat
#   /g10k_pnl         — P&L + DD progress bars
#   /g10k_positions   — Open trades with live price + uPnL
#   /g10k_journal     — Last 5 closed trades
#   /g10k_stop        — Pause engine (confirmation required)
#   /g10k_resume      — Resume engine (confirmation required)
#   /g10k_terminals   — Terminal isolation status
#   /ml_status        — ML shadow accuracy (shared read-only)
#   /ml_train         — Force ML retrain (shared)

import os
import sys
import time
import threading
import requests
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.logger import logger
from communications.telegram_helpers import (
    mask_token, send_message, get_updates as _tg_get_updates,
    is_authorized_chat, is_rate_limited, check_confirmation,
)

# ── Token — reusing TELEGRAM_BOT_TOKEN_FTMO (this is @cb6forexbot) ─────────────
_TOKEN_RAW = os.getenv('TELEGRAM_BOT_TOKEN_FTMO', '').strip()
if not _TOKEN_RAW:
    raise SystemExit(
        "FATAL: TELEGRAM_BOT_TOKEN_FTMO not set — GFT $10K bot cannot start."
    )
BOT_TOKEN = _TOKEN_RAW

_ADMIN_ID_RAW = os.getenv('CB6_ADMIN_USER_ID', '').strip()
if not _ADMIN_ID_RAW:
    raise SystemExit("FATAL: CB6_ADMIN_USER_ID not set — bot cannot authenticate.")
BOT_CHAT_ID = _ADMIN_ID_RAW

AUTHORIZED_CHAT_IDS = {
    cid.strip() for cid in str(BOT_CHAT_ID or '').split(',') if cid.strip()
}

COMMAND_RATE_LIMIT_SECS = int(os.getenv('FOREX_TELEGRAM_RATE_LIMIT_SECS', '3'))
CONFIRM_TTL_SECS        = int(os.getenv('FOREX_CONFIRM_TTL_SECS', '30'))

# ── State ───────────────────────────────────────────────────────────────────────
_last_update_id   = 0
_listener_running = False
_connector_ref    = None   # GFT $10K MT5Connector (set by GFT10KWorker)
_last_command_at  = {}
_pending_confirms = {}

logger.info(f"GFT $10K bot armed (token {mask_token(BOT_TOKEN)})")


def set_adapter(connector):
    """Called by GFT10KWorker.run() to wire in the live MT5 connector."""
    global _connector_ref
    _connector_ref = connector


# ── Telegram helpers ─────────────────────────────────────────────────────────────

def _send(text: str, parse_mode: str = 'HTML') -> bool:
    return send_message(BOT_TOKEN, BOT_CHAT_ID, text, parse_mode, logger)


def _get_updates() -> list:
    global _last_update_id
    updates, _last_update_id = _tg_get_updates(BOT_TOKEN, _last_update_id, logger)
    return updates


def _is_authorized_chat(chat_id: str) -> bool:
    return is_authorized_chat(chat_id, AUTHORIZED_CHAT_IDS)


def _rate_limited(chat_id: str, command: str) -> bool:
    return is_rate_limited(chat_id, command, _last_command_at, COMMAND_RATE_LIMIT_SECS)


def _needs_confirmation(cmd: str, arg: str) -> bool:
    return cmd in ('/g10k_stop', '/g10k_resume')


def _confirmation_ok(chat_id: str, text: str) -> tuple:
    return check_confirmation(chat_id, text, _pending_confirms, CONFIRM_TTL_SECS, _send)


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _hb_age(path: str) -> str:
    if os.path.exists(path):
        return f"{int(time.time() - os.path.getmtime(path))}s ago"
    return 'N/A'


def _load_10k():
    from forex_engine.gft_10k.state import load_state, reset_daily_if_needed
    return reset_daily_if_needed(load_state())


def _live_capital(state: dict) -> tuple:
    """Return (capital, equity, open_pnl, is_live)."""
    from forex_engine.gft_10k.config import GFT_10K_PROFILE as _P, live_execution_enabled
    is_live = live_execution_enabled()
    if is_live and _connector_ref:
        try:
            bal = _connector_ref.get_balance()
            eq  = _connector_ref.get_equity()
            if bal and bal > 0:
                return bal, eq, round(eq - bal, 2), True
        except Exception:
            pass
    cap = state.get('capital', _P['account_size'])
    return cap, cap, 0.0, is_live


def _bar(used: float, limit: float) -> str:
    pct    = min(used / limit * 100, 100) if limit > 0 else 0
    filled = int(pct / 10)
    return f"{'█'*filled}{'░'*(10-filled)} {pct:.0f}%"


# ── Command handlers ──────────────────────────────────────────────────────────────

def _cmd_start():
    try:
        from forex_engine.gft_10k.config import GFT_10K_PROFILE as _P, live_execution_enabled
        state = _load_10k()
        cap, _, _, is_live = _live_capital(state)
        total_pnl = round(cap - _P['account_size'], 2)
        mode      = 'LIVE — GFT $10K MT5' if is_live else 'Paper (terminal pending)'

        _send(
            "<b>CB6 QUANTUM — GFT $10K INSTANT</b>\n\n"
            "Markets  : Silver (XAGUSD) | Oil (USOIL)\n"
            "           Gold (XAUUSD) ⛔ PERMANENTLY DISABLED\n"
            "Strategy : ICT Silver Bullet · 15-min candles\n"
            "Platform : MetaTrader 5 — GFT $10K Instant\n\n"
            f"<b>ACCOUNT</b>\n"
            f"Balance  : ${cap:,.2f}\n"
            f"Started  : ${_P['account_size']:,.0f}\n"
            f"Total PnL: ${total_pnl:+.2f}\n"
            f"Mode     : {mode}\n\n"
            f"<b>RISK RULES</b>\n"
            f"Risk/trade: {_P['risk_per_trade_pct']}% = ${_P['account_size'] * _P['risk_per_trade_pct'] / 100:.0f}\n"
            f"Daily DD  : ${_P['daily_dd_limit']:.0f} hard stop\n"
            f"Total DD  : ${_P['max_dd_limit']:.0f} hard stop\n"
            f"Max lot   : {_P['max_lot']}\n\n"
            "<b>SESSIONS (UTC)</b>\n"
            "London KZ : 07-12 UTC  ← entries allowed\n"
            "NY KZ     : 16-20 UTC  ← entries allowed\n\n"
            "<b>COMMANDS</b>\n"
            "/g10k_status    Engine health + heartbeat\n"
            "/g10k_pnl       P&amp;L + DD progress bars\n"
            "/g10k_positions Open trades with live price\n"
            "/g10k_journal   Last 5 closed trades\n"
            "/g10k_terminals Terminal isolation status\n\n"
            "/g10k_stop      Pause engine\n"
            "/g10k_resume    Resume engine\n\n"
            "/ml_status      ML shadow accuracy\n"
            "/ml_train       Force ML retrain"
        )
    except Exception as e:
        _send(f"Start error: {e}")


def _cmd_status():
    try:
        from forex_engine.gft_10k.config import GFT_10K_PROFILE as _P, live_execution_enabled
        from forex_engine.gft_10k.state import HEARTBEAT_FILE
        from forex_engine.gft_10k.risk import daily_drawdown, max_drawdown

        state = _load_10k()
        cap, equity, open_pnl, is_live = _live_capital(state)
        total_pnl  = round(cap - _P['account_size'], 2)
        dd_daily   = daily_drawdown(state)
        dd_total   = max_drawdown(state)
        open_trades= state.get('open_trades', [])
        paused     = '⏸ PAUSED' if state.get('paused') else '▶ RUNNING'

        utc_hour = datetime.now(timezone.utc).hour
        if 7 <= utc_hour < 12:
            session = 'London KZ (entries OK)'
        elif 16 <= utc_hour < 20:
            session = 'NY KZ (entries OK)'
        elif 22 <= utc_hour < 23:
            session = 'ROLLOVER BLOCK'
        else:
            session = 'Off-hours'

        open_line = f"  open ${open_pnl:+.2f}" if open_pnl != 0 else ""
        mode      = 'LIVE MT5' if is_live else 'Paper'

        open_trades_txt = ''
        for t in open_trades:
            sym  = t.get('symbol', '?')
            dirn = 'L' if t.get('direction') == 'BULLISH' else 'S'
            open_trades_txt += f"\n  • {sym} {dirn} {t.get('lots', 0)}L  id:{t.get('id', '?')}"

        _send(
            f"<b>CB6 QUANTUM — GFT $10K STATUS</b>\n\n"
            f"Session    : {session} ({utc_hour:02d}:xx UTC)\n"
            f"Engine     : {paused}  hb:{_hb_age(HEARTBEAT_FILE)}\n"
            f"Mode       : {mode}\n\n"
            f"<b>ACCOUNT</b>\n"
            f"Balance    : ${cap:,.2f}{open_line}\n"
            f"Total PnL  : ${total_pnl:+.2f}\n\n"
            f"<b>DAILY DD  (limit ${_P['daily_dd_limit']:.0f})</b>\n"
            f"  Used: ${dd_daily:.2f}  {_bar(dd_daily, _P['daily_dd_limit'])}\n\n"
            f"<b>TOTAL DD  (limit ${_P['max_dd_limit']:.0f})</b>\n"
            f"  Used: ${dd_total:.2f}  {_bar(dd_total, _P['max_dd_limit'])}\n\n"
            f"<b>OPEN TRADES ({len(open_trades)})</b>"
            f"{open_trades_txt if open_trades_txt else chr(10) + '  None'}"
        )
    except Exception as e:
        _send(f"Status error: {e}")


def _cmd_pnl():
    try:
        from forex_engine.gft_10k.config import GFT_10K_PROFILE as _P, live_execution_enabled
        from forex_engine.gft_10k.risk import daily_drawdown, max_drawdown

        state    = _load_10k()
        cap, _, open_pnl, is_live = _live_capital(state)
        total_pnl  = round(cap - _P['account_size'], 2)
        dd_daily   = daily_drawdown(state)
        dd_total   = max_drawdown(state)
        daily_pnl  = state.get('daily_pnl', 0.0)
        closed     = state.get('closed_trades', [])
        wins       = sum(1 for t in closed if t.get('pnl_usd', 0) > 0)
        losses     = len(closed) - wins
        wr         = round(wins / len(closed) * 100, 1) if closed else 0.0
        mode       = 'LIVE MT5' if is_live else 'Paper'
        open_line  = f"\nOpen PnL   : ${open_pnl:+.2f}  (unrealized)" if is_live and open_pnl != 0 else ""

        _send(
            f"<b>CB6 QUANTUM — GFT $10K P&amp;L</b>  [{mode}]\n\n"
            f"Balance    : ${cap:,.2f}\n"
            f"Total PnL  : ${total_pnl:+.2f}"
            f"{open_line}\n\n"
            f"<b>TODAY</b>\n"
            f"Daily PnL  : ${daily_pnl:+.2f}  (limit -${_P['daily_dd_limit']:.0f})\n\n"
            f"<b>DAILY DD  (${_P['daily_dd_limit']:.0f} hard stop)</b>\n"
            f"{_bar(dd_daily, _P['daily_dd_limit'])}  ${dd_daily:.2f} used\n\n"
            f"<b>TOTAL DD  (${_P['max_dd_limit']:.0f} hard stop)</b>\n"
            f"{_bar(dd_total, _P['max_dd_limit'])}  ${dd_total:.2f} used\n\n"
            f"<b>TRADES</b>\n"
            f"Closed : {len(closed)}  ({wins}W / {losses}L  {wr}% WR)\n"
            f"Open   : {len(state.get('open_trades', []))}\n\n"
            f"Risk/trade : {_P['risk_per_trade_pct']}%  max lot {_P['max_lot']}"
        )
    except Exception as e:
        _send(f"PnL error: {e}")


def _cmd_positions():
    try:
        from forex_engine.gft_10k.state import load_state
        from forex_engine.forex_instruments import INSTRUMENTS

        trades = load_state().get('open_trades', [])
        if not trades:
            _send("GFT $10K — No open positions.")
            return

        for t in trades:
            sym      = t['symbol']
            cfg      = INSTRUMENTS.get(sym, {})
            is_long  = t['direction'] == 'BULLISH'
            entry    = t['entry_price']
            lots     = t['lots']
            sl       = t.get('stop_loss', t.get('current_sl', 0))
            tp       = t.get('target', 0)
            contract = cfg.get('contract_size', 5000)

            price = None
            if _connector_ref:
                try:
                    price = _connector_ref.get_price(sym)
                except Exception:
                    pass

            if price:
                upnl    = round(lots * contract * (price - entry) * (1 if is_long else -1), 2)
                price_s = f"{price:.5f}"
            else:
                upnl    = 0.0
                price_s = 'N/A'

            dir_s   = 'LONG' if is_long else 'SHORT'
            ghost   = t.get('ticket', 0) == 0
            ghost_w = "\n⚠️ NO MT5 ORDER — state only" if ghost else ""
            _send(
                f"<b>[GFT-10K] {cfg.get('label', sym)} — {dir_s}</b>  [{t['id']}]{ghost_w}\n\n"
                f"Entry      : {entry}\n"
                f"Live Price : {price_s}\n"
                f"Unrealised : ${upnl:+.2f}\n\n"
                f"SL         : {sl}\n"
                f"TP         : {tp}\n\n"
                f"Lots       : {lots}\n"
                f"Risk       : ${t.get('risk_usd', 0):.2f}\n"
                f"Opened     : {t.get('entry_time', '?')}"
            )
            time.sleep(0.3)
    except Exception as e:
        _send(f"Positions error: {e}")


def _cmd_journal():
    try:
        from forex_engine.gft_10k.state import load_state

        closed = load_state().get('closed_trades', [])
        closed = sorted(closed, key=lambda x: x.get('entry_time', ''), reverse=True)[:5]

        if not closed:
            _send("GFT $10K — No closed trades yet.")
            return

        lines = ["<b>CB6 QUANTUM — GFT $10K RECENT TRADES (last 5)</b>\n"]
        for t in closed:
            sym    = t.get('symbol', '?')
            dirn   = 'L' if t.get('direction') == 'BULLISH' else 'S'
            pnl    = t.get('pnl_usd', 0.0)
            sign   = '+' if pnl >= 0 else ''
            entry  = t.get('entry_price', '?')
            exit_p = t.get('exit_price', '?')
            entry_t= t.get('entry_time', '')[:16]
            icon   = '✅' if pnl > 0 else '❌'
            lines.append(
                f"{icon} <b>[GFT-10K] {sym} {dirn}</b>  ${sign}{pnl:.2f}\n"
                f"   Entry {entry} → Exit {exit_p}\n"
                f"   {entry_t}"
            )
        _send('\n\n'.join(lines))
    except Exception as e:
        _send(f"Journal error: {e}")


def _cmd_stop():
    try:
        from forex_engine.gft_10k.state import load_state, save_state
        state = load_state()
        state['paused'] = True
        save_state(state)
        _send(
            "<b>GFT $10K ENGINE — PAUSED</b>\n\n"
            "No new $10K trades will open.\n"
            "Open positions continue to be monitored.\n"
            "Send /g10k_resume to re-enable."
        )
    except Exception as e:
        _send(f"Stop error: {e}")


def _cmd_resume():
    try:
        from forex_engine.gft_10k.state import load_state, save_state
        state = load_state()
        state['paused'] = False
        save_state(state)
        _send(
            "<b>GFT $10K ENGINE — RESUMED</b>\n\n"
            "GFT $10K engine : RUNNING"
        )
    except Exception as e:
        _send(f"Resume error: {e}")


def _cmd_terminals():
    try:
        from forex_engine.accounts.account_registry import status_summary
        from forex_engine.gft_10k.config import live_execution_enabled

        summary = status_summary()
        acc     = summary.get('GFT_10K', {})
        found   = acc.get('terminal_found', False)
        path    = acc.get('terminal_path') or 'not configured'
        magic   = acc.get('magic', 0)
        is_live = live_execution_enabled()

        if not is_live:
            status    = 'PAPER  📄'
            path_disp = '—'
            algo      = '—'
        elif found:
            status    = 'ONLINE ✅'
            short     = path.replace('\\', '/').split('/')
            path_disp = f"✅ .../{'/'.join(short[-3:])}"
            algo      = f"✅ ENABLED (Magic: {magic})"
        else:
            status    = 'OFFLINE ❌'
            path_disp = '❌ MISSING — install MT5 at C:\\CB6_MT5\\MT5_GFT_10K\\'
            algo      = '❌ NOT RUNNING'

        try:
            from forex_engine.gft_10k.state import load_state
            st   = load_state()
            cap  = st.get('capital', 0.0)
            dpnl = st.get('daily_pnl', 0.0)
            paus = '⏸ PAUSED' if st.get('paused') else 'RUNNING'
        except Exception:
            cap, dpnl, paus = 0.0, 0.0, '?'

        _send(
            f"<b>CB6 QUANTUM — GFT $10K TERMINAL</b>\n\n"
            f"Status   : {status}\n"
            f"Terminal : {path_disp}\n"
            f"Algo     : {algo}\n\n"
            f"Capital  : ${cap:,.2f}  Daily: ${dpnl:+.2f}\n"
            f"Engine   : {paus}\n\n"
            f"Login    : 514294187\n"
            f"Server   : GoatFunded-Server3"
        )
    except Exception as e:
        _send(f"Terminals error: {e}")


# ── ML commands (shared read-only) ─────────────────────────────────────────────

def _cmd_ml_status(arg: str):
    try:
        from ml.shadow_monitor import build_status_message
        _send(build_status_message(), parse_mode='Markdown')
    except Exception as e:
        _send(f"ML status error: {e}")


def _cmd_ml_train(arg: str):
    try:
        from ml.auto_trainer import trigger_now
        parts = arg.strip().lower().split()
        if not parts:
            for mkt, acc in [('nse', ''), ('forex', 'gft')]:
                trigger_now(mkt, acc)
            _send("ML training triggered for all markets. Results in ~2 min.")
        elif parts[0] == 'nse':
            trigger_now('nse', '')
            _send("ML training triggered for NSE.")
        elif parts[0] == 'forex':
            trigger_now('forex', 'gft')
            _send("ML training triggered for forex/GFT.")
        else:
            _send("Usage: /ml_train | /ml_train nse | /ml_train forex")
    except Exception as e:
        _send(f"ML train error: {e}")


# ── Dispatch ────────────────────────────────────────────────────────────────────

_COMMANDS = {
    '/start'           : _cmd_start,
    '/g10k_status'     : _cmd_status,
    '/g10k_pnl'        : _cmd_pnl,
    '/g10k_positions'  : _cmd_positions,
    '/g10k_journal'    : _cmd_journal,
    '/g10k_stop'       : _cmd_stop,
    '/g10k_resume'     : _cmd_resume,
    '/g10k_terminals'  : _cmd_terminals,
    '/ml_status'       : _cmd_ml_status,
    '/ml_train'        : _cmd_ml_train,
    # Aliases
    '/fx_status'       : _cmd_status,
    '/fx_pnl'          : _cmd_pnl,
    '/fx_positions'    : _cmd_positions,
    '/fx_stop'         : _cmd_stop,
    '/fx_resume'       : _cmd_resume,
}


def _process_updates(updates: list):
    global _last_update_id
    for update in updates:
        _last_update_id = update['update_id']
        message = update.get('message', {})
        text    = message.get('text', '').strip()
        chat_id = str(message.get('chat', {}).get('id', ''))

        if not _is_authorized_chat(chat_id):
            if chat_id:
                logger.warning(f"GFT $10K bot: rejected command from chat_id={chat_id}")
            continue

        if text.startswith('/') and '@' in text.split()[0]:
            first, *rest = text.split()
            text = ' '.join([first.split('@')[0]] + rest)

        logger.info(f"GFT $10K bot command: {text}")

        parts = text.split(None, 1)
        cmd   = parts[0]
        arg   = parts[1] if len(parts) > 1 else ''

        fn = _COMMANDS.get(cmd)
        if fn:
            is_confirm_reply = text.lower().endswith(' confirm')
            if not is_confirm_reply and _rate_limited(chat_id, cmd):
                _send("Rate limit active. Try again in a few seconds.")
                continue
            if _needs_confirmation(cmd, arg):
                ok, confirmed_text = _confirmation_ok(chat_id, text)
                if not ok:
                    continue
                parts = confirmed_text.split(None, 1)
                cmd   = parts[0]
                arg   = parts[1] if len(parts) > 1 else ''
                fn    = _COMMANDS.get(cmd)
            threading.Thread(target=fn if cmd not in ('/ml_status', '/ml_train') else lambda: fn(arg),
                             daemon=True).start()
        elif text.startswith('/'):
            _send(f"Unknown command: {cmd}\nSend /start to see all GFT $10K commands.")


# ── Lock file ────────────────────────────────────────────────────────────────────

_BOT_LOCK_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'data', 'gft_10k_bot.lock'
)


def _pid_alive(pid: int) -> bool:
    """Windows-safe PID check via psutil if available, else tasklist fallback."""
    if pid == os.getpid():
        return True
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    try:
        import subprocess as _sp
        out = _sp.check_output(
            ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
            stderr=_sp.DEVNULL, timeout=3
        ).decode(errors='ignore')
        return str(pid) in out
    except Exception:
        return False


def _acquire_bot_lock() -> bool:
    if os.path.exists(_BOT_LOCK_FILE):
        try:
            with open(_BOT_LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid() and _pid_alive(old_pid):
                logger.warning(f"GFT $10K bot: listener already running (PID {old_pid}) — skipping")
                return False
            # Stale lock — old process is dead, overwrite it
            logger.info(f"GFT $10K bot: stale lock (PID {old_pid} dead) — taking over")
        except Exception:
            pass
    try:
        os.makedirs(os.path.dirname(_BOT_LOCK_FILE), exist_ok=True)
        with open(_BOT_LOCK_FILE, 'w') as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    return True


def _release_bot_lock():
    try:
        if os.path.exists(_BOT_LOCK_FILE):
            os.remove(_BOT_LOCK_FILE)
    except Exception:
        pass


# ── Listener ─────────────────────────────────────────────────────────────────────

def start_listening():
    global _listener_running, _last_update_id
    if _listener_running:
        logger.warning("GFT $10K bot: listener already running — duplicate start ignored")
        return

    if not _acquire_bot_lock():
        return

    _listener_running = True
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={'offset': -1, 'timeout': 1},
            timeout=(3, 5),
        )
        result = r.json().get('result', []) if r.status_code == 200 else []
        if result:
            _last_update_id = result[-1]['update_id']
            logger.info(f"GFT $10K bot: drained {len(result)} pending update(s)")
    except Exception:
        pass

    logger.info("GFT $10K Telegram bot listener started")
    try:
        while True:
            try:
                updates = _get_updates()
                if updates:
                    _process_updates(updates)
                time.sleep(3)
            except Exception as e:
                logger.error(f"GFT $10K bot listener error: {e}")
                time.sleep(10)
    finally:
        _release_bot_lock()


def send_alert(text: str) -> bool:
    """Send an alert to the GFT $10K Telegram chat. Called by GFT10KWorker."""
    return _send(text)
