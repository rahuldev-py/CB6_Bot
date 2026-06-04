# communications/forex_bot.py
#
# CB6 Quantum — FTMO Telegram bot (FTMO $10K engine ONLY).
#
# Token  : TELEGRAM_BOT_TOKEN_FTMO   (hard-fail at startup if missing)
# Auth   : CB6_ADMIN_USER_ID         (single admin numeric user ID)
# Scope  : ALL commands touch only FTMO state/connector/terminal.
#          GFT is fully isolated in communications/gft_bot.py.
#
# Commands:
#   /start                — FTMO overview + command list
#   /fx_status            — FTMO engine health + heartbeat
#   /fx_pnl               — FTMO P&L + challenge progress
#   /fx_ftmo              — FTMO rule tracker with progress bars
#   /fx_positions         — FTMO open trades with live price + uPnL
#   /fx_lots              — FTMO lot sizes + risk per symbol
#   /fx_journal           — Last 5 FTMO closed trades
#   /fx_exit              — Close FTMO trade manually — /fx_exit A
#   /fx_stop              — Pause FTMO engine (confirmation required)
#   /fx_resume            — Resume FTMO engine (confirmation required)
#   /fx_terminals         — FTMO terminal isolation status
#   /forex_execution_mode — Execution gate config
#   /forex_execution_stats— Execution validation stats
#   /fx_help              — FTMO strategy rules + command list
#   /ml_status            — ML shadow accuracy (shared read-only)
#   /ml_train             — Force ML retrain (shared)

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
from settings import (
    FOREX_EXECUTION_MODE,
    FOREX_EXECUTION_REVALIDATE_CYCLE_SECONDS,
    FOREX_MAX_SPREAD_PCT,
    FOREX_DISABLED_SYMBOLS,
    FOREX_ALLOWED_UTC_WINDOWS,
    FOREX_ALLOWED_SIGNAL_AGE_SECONDS,
    FOREX_MAX_ENTRY_DRIFT_PERCENT,
    FOREX_MAX_ENTRY_DRIFT_POINTS,
    FOREX_EXECUTION_MIN_RR,
    FOREX_EXECUTION_INVALIDATION_BUFFER_POINTS,
)

# ── Token config — hard-fail if FTMO token absent ──────────────────────────────
_FTMO_TOKEN_RAW = os.getenv('TELEGRAM_BOT_TOKEN_FTMO', '').strip()
if not _FTMO_TOKEN_RAW:
    raise SystemExit(
        "FATAL: TELEGRAM_BOT_TOKEN_FTMO not set in .env — "
        "FTMO bot cannot start. Set the key and restart."
    )
FTMO_TOKEN = _FTMO_TOKEN_RAW

_ADMIN_ID_RAW = os.getenv('CB6_ADMIN_USER_ID', '').strip()
if not _ADMIN_ID_RAW:
    raise SystemExit(
        "FATAL: CB6_ADMIN_USER_ID not set in .env — "
        "FTMO bot cannot authenticate. Set the key and restart."
    )
FTMO_CHAT_ID = _ADMIN_ID_RAW

AUTHORIZED_CHAT_IDS = {
    cid.strip()
    for cid in str(FTMO_CHAT_ID or '').split(',')
    if cid.strip()
}

COMMAND_RATE_LIMIT_SECS = int(os.getenv('FOREX_TELEGRAM_RATE_LIMIT_SECS', '3'))
CONFIRM_TTL_SECS        = int(os.getenv('FOREX_CONFIRM_TTL_SECS', '30'))

# ── FTMO MT5 connector reference (set by forex_worker at startup) ───────────────
_last_update_id   = 0
_listener_running = False
_adapter_ref      = None   # FTMO MT5Connector
_last_command_at  = {}
_pending_confirms = {}


def _mask(token: str) -> str:
    return mask_token(token)


logger.info(f"FTMO Telegram bot armed (token {_mask(FTMO_TOKEN)})")


def set_adapter(adapter):
    global _adapter_ref
    _adapter_ref = adapter


# ── Balance helpers ─────────────────────────────────────────────────────────────

def _get_mt5_balance() -> tuple:
    """Return (balance, equity, open_pnl, is_live) for FTMO account."""
    is_live = os.getenv('FOREX_PAPER', 'true').lower() == 'false'
    if is_live and _adapter_ref:
        try:
            bal = _adapter_ref.get_balance()
            eq  = _adapter_ref.get_equity()
            if bal and bal > 0:
                return bal, eq, round(eq - bal, 2), True
        except Exception:
            pass
    from forex_engine.forex_paper_trader import load_state
    st = load_state()
    cap = st.get('capital', 10000.0)
    return cap, cap, 0.0, False


# ── Telegram helpers — thin wrappers over communications.telegram_helpers ────────

def _send(text: str, parse_mode: str = 'HTML') -> bool:
    return send_message(FTMO_TOKEN, FTMO_CHAT_ID, text, parse_mode, logger)


def _get_updates() -> list:
    global _last_update_id
    updates, _last_update_id = _tg_get_updates(FTMO_TOKEN, _last_update_id, logger)
    return updates


# ── Auth + rate-limiting ────────────────────────────────────────────────────────

def _is_authorized_chat(chat_id: str) -> bool:
    return is_authorized_chat(chat_id, AUTHORIZED_CHAT_IDS)


def _rate_limited(chat_id: str, command: str) -> bool:
    return is_rate_limited(chat_id, command, _last_command_at, COMMAND_RATE_LIMIT_SECS)


def _needs_confirmation(cmd: str, arg: str) -> bool:
    if cmd in ('/fx_stop', '/fx_resume'):
        return True
    return cmd == '/fx_exit' and bool(arg.strip())


def _confirmation_ok(chat_id: str, text: str) -> tuple:
    return check_confirmation(chat_id, text, _pending_confirms, CONFIRM_TTL_SECS, _send)


# ── Command handlers ────────────────────────────────────────────────────────────

def _cmd_start():
    try:
        balance, _, _, is_live = _get_mt5_balance()
        starting = 10000.0
        growth   = round((balance - starting) / starting * 100, 2)
        mode     = 'LIVE — FTMO MT5' if is_live else 'Paper (tracking)'
        _send(
            "<b>CB6 QUANTUM — FTMO ENGINE</b>\n\n"
            "Markets  : Silver (XAGUSD) | Oil (USOIL) | EUR/USD\n"
            "           Gold (XAUUSD) ⛔ PAUSED — trend conflict\n"
            "Strategy : ICT Silver Bullet · 15-min candles\n"
            "Platform : MetaTrader 5 — FTMO $10K\n\n"
            "<b>ACCOUNT [FTMO]</b>\n"
            f"Balance  : ${balance:,.2f}\n"
            f"Started  : ${starting:,.2f}\n"
            f"Growth   : {growth:+.2f}%\n"
            f"Mode     : {mode}\n\n"
            "<b>SESSIONS (UTC)</b>\n"
            "London KZ : 07-12 UTC  ← entries allowed\n"
            "NY KZ     : 16-20 UTC  ← entries allowed\n"
            "Rollover  : 22-23 UTC  ← blocked (spreads explode)\n\n"
            "<b>ACCOUNT STATUS</b>\n"
            "/fx_status    FTMO engine health + heartbeat\n"
            "/fx_pnl       FTMO P&amp;L + challenge progress\n"
            "/fx_ftmo      FTMO rule tracker with progress bars\n"
            "/fx_terminals FTMO terminal status\n\n"
            "<b>TRADES</b>\n"
            "/fx_positions Open FTMO trades with live price + uPnL\n"
            "/fx_journal   Last 5 FTMO closed trades\n"
            "/fx_lots      Live FTMO lot sizes + risk\n"
            "/fx_exit      Close a trade — /fx_exit A\n\n"
            "<b>CONTROL</b>\n"
            "/fx_stop      Pause FTMO engine\n"
            "/fx_resume    Resume FTMO engine\n\n"
            "<b>ML &amp; ANALYTICS</b>\n"
            "/ml_status    ML shadow accuracy + model status\n"
            "/ml_train     Force ML retrain\n\n"
            "<b>ADVANCED</b>\n"
            "/forex_execution_mode  Execution gate config\n"
            "/forex_execution_stats Execution validation stats\n"
            "/fx_help      Full strategy rules"
        )
    except Exception as e:
        _send(f"Start error: {e}")


def _cmd_status():
    try:
        from forex_engine.forex_paper_trader import get_summary, load_state

        s     = get_summary()
        state = load_state()

        balance, equity, open_pnl, is_live = _get_mt5_balance()

        base    = os.path.dirname(os.path.dirname(__file__))
        hb_path = os.path.join(base, 'data', 'forex_heartbeat.txt')

        def _hb_age(path):
            if os.path.exists(path):
                return f"{int(time.time() - os.path.getmtime(path))}s ago"
            return 'N/A'

        utc_hour = datetime.now(timezone.utc).hour
        if 7 <= utc_hour < 10:
            session = 'London Open KZ (entries OK)'
        elif 10 <= utc_hour < 12:
            session = 'London Mid KZ (off-peak)'
        elif 12 <= utc_hour < 16:
            session = 'Out of KZ — no entries'
        elif 16 <= utc_hour < 18:
            session = 'NY Open KZ (entries OK)'
        elif 18 <= utc_hour < 20:
            session = 'NY Session KZ (off-peak)'
        elif 22 <= utc_hour < 23:
            session = 'ROLLOVER BLOCK (22-23 UTC)'
        else:
            session = 'Off-hours — no entries'

        starting    = 10000.0
        total_pnl   = round(balance - starting, 2)
        paused      = 'PAUSED' if state.get('paused') else 'RUNNING'
        open_line   = f"  open ${open_pnl:+.2f}" if open_pnl != 0 else ""

        _send(
            f"<b>CB6 QUANTUM — FTMO STATUS</b>\n\n"
            f"Session    : {session} ({utc_hour:02d}:xx UTC)\n\n"
            f"<b>FTMO $10K [{paused}]</b>  hb:{_hb_age(hb_path)}\n"
            f"Balance    : ${balance:,.2f}{open_line}\n"
            f"Daily PnL  : ${s['daily_pnl']:+.2f}  |  Total: ${total_pnl:+.2f}\n"
            f"Trades     : {s['open_trades']} open | {s['total_trades']} total "
            f"({s['wins']}W/{s['losses']}L  {s['win_rate']}% WR)"
        )
    except Exception as e:
        _send(f"Status error: {e}")


def _cmd_pnl():
    try:
        from forex_engine.forex_paper_trader import get_summary, load_state, compute_best_day_stats
        from forex_engine.forex_instruments import FTMO_RULES

        s     = get_summary()
        state = load_state()
        mode  = state.get('mode', 'free_trial')
        rules = FTMO_RULES[mode]

        starting       = 10000.0
        profit_target  = starting * rules['profit_target_pct'] / 100
        daily_limit    = starting * rules['max_daily_loss_pct'] / 100
        total_dd_limit = starting * rules['max_total_dd_pct'] / 100

        mt5_balance  = 0.0
        mt5_equity   = 0.0
        mt5_open_pnl = 0.0
        is_live = os.getenv('FOREX_PAPER', 'true').lower() == 'false'
        if is_live and _adapter_ref:
            try:
                mt5_balance  = _adapter_ref.get_balance()
                mt5_equity   = _adapter_ref.get_equity()
                mt5_open_pnl = round(mt5_equity - mt5_balance, 2)
            except Exception:
                pass

        actual_balance = mt5_balance if (is_live and mt5_balance > 0) else state.get('capital', starting)
        total_pnl      = round(actual_balance - starting, 2)
        progress_pct   = round(total_pnl / profit_target * 100, 1) if profit_target else 0
        remaining      = round(profit_target - total_pnl, 2)

        daily_pnl  = state.get('daily_pnl', 0.0)
        daily_used = abs(daily_pnl) if daily_pnl < 0 else 0.0

        eod_peak = max(state.get('eod_equity_peak', starting), actual_balance)
        dd_floor = round(eod_peak - total_dd_limit, 2)
        headroom = round(actual_balance - dd_floor, 2)

        mode_label = 'LIVE — FTMO MT5' if is_live else 'PAPER (tracking)'
        open_line  = f"\nOpen P&amp;L  : ${mt5_open_pnl:+.2f}  (unrealized)" if is_live and mt5_open_pnl != 0 else ""

        closed = state.get('closed_trades', [])
        bd_real, total_pos, bd_ratio, bd_date = compute_best_day_stats(closed)
        bd_warn = ' ⚠️' if bd_ratio > 45 else ''
        bd_line = (
            f"\nBest Day Rule: {bd_ratio:.1f}%{bd_warn}  (max 50%)"
            f"  [${bd_real:.2f} best day / ${total_pos:.2f} total +days]"
        ) if bd_real > 0 else ""

        _send(
            f"<b>CB6 QUANTUM — FTMO P&amp;L</b>  [{mode_label}]\n\n"
            f"<b>FTMO $10K ACCOUNT</b>\n"
            f"MT5 Balance  : ${actual_balance:,.2f}\n"
            f"Total PnL    : ${total_pnl:+.2f}"
            f"{open_line}\n\n"
            f"<b>TODAY [FTMO]</b>\n"
            f"Daily PnL    : ${daily_pnl:+.2f}  (limit -${daily_limit:.0f})\n"
            f"Daily used   : ${daily_used:.2f} / ${daily_limit:.0f} "
            f"({round(daily_used/daily_limit*100,1) if daily_limit else 0}%)\n\n"
            f"<b>FTMO PROGRESS ({mode.upper().replace('_', ' ')})</b>\n"
            f"Target       : ${profit_target:.0f}  →  {progress_pct}% there\n"
            f"Still need   : ${remaining:.2f}\n"
            f"EOD DD floor : ${dd_floor:,.2f}\n"
            f"Headroom     : ${headroom:.2f}"
            f"{bd_line}"
        )
    except Exception as e:
        _send(f"PnL error: {e}")


def _cmd_positions():
    try:
        from forex_engine.forex_instruments import INSTRUMENTS
        items = _get_all_open_trades()

        if not items:
            _send("CB6 QUANTUM FTMO — No open FTMO positions.")
            return

        for item in items:
            t     = item['trade']
            sym   = t['symbol']
            cfg   = INSTRUMENTS.get(sym, {})
            contract = cfg.get('contract_size', 100)
            label    = cfg.get('label', sym)
            is_long  = t['direction'] == 'BULLISH'
            entry    = t['entry_price']
            sl       = t['current_sl']
            lots     = t['lots']

            price = None
            if _adapter_ref:
                try:
                    price = _adapter_ref.get_price(sym)
                except Exception:
                    pass

            if price:
                upnl    = round(lots * contract * (price - entry) * (1 if is_long else -1), 2)
                sl_dist = round(abs(price - sl), 5)
                t1_dist = round(abs(t['target1'] - price), 5)
                price_s = f"{price:.5f}"
            else:
                upnl    = 0.0
                sl_dist = round(abs(entry - sl), 5)
                t1_dist = round(abs(t['target1'] - entry), 5)
                price_s = 'N/A'

            dir_s  = 'LONG' if is_long else 'SHORT'
            ghost  = t.get('ticket', 0) == 0
            ghost_w = "\n⚠️ NO MT5 ORDER — state only" if ghost else ""
            _send(
                f"<b>[FTMO] {label} — {dir_s}</b>  [{t['id']}]{ghost_w}\n\n"
                f"Entry      : {entry}\n"
                f"Live Price : {price_s}\n"
                f"Unrealised : ${upnl:+.2f}\n\n"
                f"SL         : {sl}  ({sl_dist} away)\n"
                f"T1 (1/3)   : {t['target1']}  ({t1_dist} away)\n"
                f"T2 (1/3)   : {t['target2']}\n"
                f"T3 (1/3)   : {t['target3']}\n\n"
                f"Lots       : {lots}\n"
                f"Risk       : ${t['risk_usd']}\n"
                f"Targets hit: {','.join(t.get('targets_hit', [])) or 'None'}\n"
                f"Opened     : {t['entry_time']}"
            )
            time.sleep(0.3)
    except Exception as e:
        _send(f"Positions error: {e}")


def _cmd_lots():
    try:
        from forex_engine.trade.lot_calculator import calc_lot_size, dollar_risk
        from forex_engine.forex_instruments import INSTRUMENTS, FTMO_RULES, margin_required

        balance, _, _, _ = _get_mt5_balance()
        risk_pct = FTMO_RULES['risk_per_trade_pct']
        risk_usd = balance * risk_pct / 100

        lines = [
            f"<b>CB6 QUANTUM — FTMO LOT SIZING</b>\n",
            f"Account   : ${balance:,.2f}",
            f"Risk/trade: {risk_pct}% = ${risk_usd:.2f}",
            f"Leverage  : 1:100 (FTMO Standard)\n",
        ]

        symbols = ['XAUUSD', 'XAGUSD', 'USOIL']
        for sym in symbols:
            cfg    = INSTRUMENTS[sym]
            label  = cfg['label']
            min_sl = cfg['min_sl_dist']

            price = None
            if _adapter_ref:
                try:
                    price = _adapter_ref.get_price(sym)
                except Exception:
                    pass

            if price is None:
                lines.append(f"\n<b>{label}</b>\nPrice: unavailable (MT5 not connected)")
                continue

            typical_sl_dist = min_sl * 2
            entry_long  = price
            sl_long     = round(price - typical_sl_dist, 5)

            lots     = calc_lot_size(sym, balance, entry_long, sl_long, risk_pct)
            risk_d   = dollar_risk(sym, lots, entry_long, sl_long)
            margin   = margin_required(sym, lots, price)
            notional = round(lots * cfg['contract_size'] * price, 2)

            pip     = cfg['pip_size']
            pip_val = round(lots * cfg['contract_size'] * pip, 4)

            daily_limit  = balance * 0.03
            trades_to_dd = int(daily_limit / risk_d) if risk_d > 0 else 0

            lines.append(
                f"\n<b>{label} ({sym})</b>\n"
                f"Live Price : {price:.5f}\n"
                f"Lot size   : {lots} lots\n"
                f"SL distance: {typical_sl_dist} (typical)\n"
                f"Risk       : ${risk_d:.2f} per trade\n"
                f"Margin used: ${margin:.2f} (of ${balance:,.0f})\n"
                f"Notional   : ${notional:,.0f}\n"
                f"Pip value  : ${pip_val:.4f} per pip\n"
                f"Max losses/day before $300 limit: {trades_to_dd}"
            )

        from forex_engine.forex_paper_trader import load_state as _ls
        _st       = _ls()
        _mode     = _st.get('mode', 'free_trial')
        _rules    = FTMO_RULES.get(_mode, FTMO_RULES['free_trial'])
        _daily_lim = balance * _rules['max_daily_loss_pct'] / 100
        _dd_lim    = balance * _rules['max_total_dd_pct'] / 100
        _pt        = balance * _rules['profit_target_pct'] / 100
        _bd_lim    = _pt * _rules['best_day_rule_pct'] / 100
        lines.append(
            f"\n<b>FTMO DAILY RULES ({_mode.upper().replace('_', ' ')})</b>\n"
            f"Max daily loss : ${_daily_lim:.0f} ({_rules['max_daily_loss_pct']}%)\n"
            f"Best day limit : ${_bd_lim:.0f} ({_rules['best_day_rule_pct']}% of target)\n"
            f"Total DD limit : ${_dd_lim:.0f} ({_rules['max_total_dd_pct']}% EOD trailing)\n"
            f"Risk per trade : ${risk_usd:.2f} = {FTMO_RULES['risk_per_trade_pct']}%"
        )

        _send('\n'.join(lines))
    except Exception as e:
        _send(f"Lots error: {e}")


def _cmd_ftmo():
    try:
        from forex_engine.forex_paper_trader import load_state, compute_best_day_stats
        from forex_engine.forex_instruments import FTMO_RULES

        state   = load_state()
        mode    = state.get('mode', 'free_trial')
        rules   = FTMO_RULES[mode]
        start   = state.get('starting_capital', 10000.0)
        daily   = state.get('daily_pnl', 0.0)

        capital, equity, open_pnl, is_live = _get_mt5_balance()

        profit_target  = start * rules['profit_target_pct'] / 100
        daily_limit    = start * rules['max_daily_loss_pct'] / 100
        total_dd_limit = start * rules['max_total_dd_pct'] / 100
        hard_day_cap   = profit_target * rules['best_day_rule_pct'] / 100

        total_pnl   = round(capital - start, 2)
        eod_peak    = state.get('eod_equity_peak', start)
        dd_floor    = eod_peak - total_dd_limit
        dd_consumed = max(0.0, dd_floor - capital)
        daily_loss  = abs(daily) if daily < 0 else 0
        still_need  = round(profit_target - total_pnl, 2)

        closed = state.get('closed_trades', [])
        bd_real, total_pos, bd_ratio, bd_date = compute_best_day_stats(closed)
        today_pnl = state.get('daily_pnl', 0.0)
        bd_warn   = ' ⚠️ WARNING' if bd_ratio > 45 else ' ✅'

        def _bar(used, limit):
            pct    = min(used / limit * 100, 100) if limit > 0 else 0
            filled = int(pct / 10)
            return f"{'█'*filled}{'░'*(10-filled)} {pct:.0f}%"

        status = 'ON TRACK ✅'
        if dd_consumed > 0 or daily_loss >= daily_limit * 0.9 or bd_ratio > 45:
            status = 'WARNING ⚠️'
        if total_pnl >= profit_target:
            status = 'TARGET HIT 🎉'

        open_line = f"\nOpen P&amp;L : ${open_pnl:+.2f}  (unrealized)" if is_live and open_pnl != 0 else ""
        src_label = 'MT5' if is_live else 'Paper'

        _send(
            f"<b>CB6 QUANTUM — FTMO RULE STATUS</b>\n"
            f"Mode: {mode.upper().replace('_', ' ')}  [{src_label}]\n\n"
            f"<b>PROFIT TARGET (need ${profit_target:.0f})</b>\n"
            f"Earned  : ${total_pnl:+.2f}  →  still need ${still_need:.2f}\n"
            f"{_bar(max(total_pnl, 0), profit_target)}\n\n"
            f"<b>DAILY LOSS (max ${daily_limit:.0f})</b>\n"
            f"Used    : ${daily_loss:.2f}\n"
            f"{_bar(daily_loss, daily_limit)}\n\n"
            f"<b>EOD TRAILING DD (floor = peak − ${total_dd_limit:.0f})</b>\n"
            f"EOD peak  : ${eod_peak:,.2f}\n"
            f"DD floor  : ${dd_floor:,.2f}  (must stay above)\n"
            f"Current   : ${capital:,.2f}  ({'safe ✅' if capital > dd_floor else 'BREACH ❌'}){open_line}\n"
            f"{_bar(max(0, dd_floor - capital + total_dd_limit), total_dd_limit)}\n\n"
            f"<b>BEST DAY RULE (FTMO: best day ≤ 50% of +days)</b>\n"
            f"Best day  : ${bd_real:.2f}  ({bd_date})\n"
            f"Total +days: ${total_pos:.2f}\n"
            f"FTMO ratio: {bd_ratio:.1f}%{bd_warn}  (max 50%)\n"
            f"{_bar(bd_ratio, 50)}\n"
            f"Today PnL : ${today_pnl:+.2f}  |  Bot cap: ${hard_day_cap:.0f}/day\n\n"
            f"Account : ${capital:,.2f}  (started ${start:,.0f})\n"
            f"Status  : {status}"
        )
    except Exception as e:
        _send(f"FTMO status error: {e}")


def _cmd_terminals():
    """FTMO terminal isolation status — shows FTMO terminal only."""
    try:
        from forex_engine.prop_firms.ftmo.ftmo_state import load_state as ftmo_load
        from utils.emergency_stop import is_emergency_stop_active
        from forex_engine.accounts.account_registry import status_summary

        estop   = is_emergency_stop_active()
        summary = status_summary()
        ftmo    = summary.get('FTMO_10K', {})

        paper  = ftmo.get('paper', True)
        found  = ftmo.get('terminal_found', False)
        path   = ftmo.get('terminal_path') or 'not configured'
        magic  = ftmo.get('magic', 0)

        if paper:
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
            path_disp = '❌ MISSING — run setup'
            algo      = '❌ UNKNOWN'

        try:
            st   = ftmo_load()
            cap  = st.get('capital', 0.0)
            dpnl = st.get('daily_pnl', 0.0)
            paus = '⏸ PAUSED' if st.get('paused') else 'RUNNING'
        except Exception:
            cap, dpnl, paus = 0.0, 0.0, '?'

        estop_line = '\n🔴 <b>EMERGENCY STOP ACTIVE</b> — send /fx_resume to clear\n' if estop else ''

        msg = (
            f"{estop_line}"
            f"<b>CB6 QUANTUM — FTMO TERMINAL STATUS</b>\n"
            f"<code>"
            f"{'─'*58}\n"
            f" {'ENGINE':<9} {'STATUS':<11} {'PATH / ALGO TOGGLE'}\n"
            f"{'─'*58}\n"
            f" {'FTMO':<9} {status:<11} {path_disp}\n"
            f"          {'':11} {algo}\n"
            f"          {'':11} Balance: ${cap:.2f}  Daily: ${dpnl:+.2f}  {paus}\n"
            f"{'─'*58}"
            f"</code>"
        )
        _send(msg)
    except Exception as e:
        _send(f"Terminals error: {e}")


def _cmd_stop():
    try:
        from forex_engine.prop_firms.ftmo.ftmo_state import load_state, save_state, STATE_FILE
        from utils.emergency_stop import set_emergency_stop

        state = load_state()
        state['paused'] = True
        save_state(state)

        set_emergency_stop('Telegram /stop — forex halted')

        _send(
            "<b>FTMO ENGINE — PAUSED</b>\n\n"
            "No new FTMO trades will open.\n"
            "Open positions continue to be monitored.\n"
            "Send /fx_resume to re-enable.\n\n"
            "<i>GFT engine unaffected — use GFT bot to control GFT.</i>"
        )
    except Exception as e:
        _send(f"Stop error: {e}")


def _cmd_resume():
    try:
        import json
        from forex_engine.forex_paper_trader import load_state, STATE_FILE
        from utils.emergency_stop import clear_emergency_stop, is_emergency_stop_active

        flag_was_active = is_emergency_stop_active()
        clear_emergency_stop()

        state = load_state()
        state['paused'] = False
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)

        flag_note = "\n<i>Emergency stop flag cleared.</i>" if flag_was_active else ""
        _send(
            "<b>FTMO ENGINE — RESUMED</b>\n\n"
            f"FTMO engine : RUNNING{flag_note}\n\n"
            "<i>GFT engine unaffected — use GFT bot to control GFT.</i>"
        )
    except Exception as e:
        _send(f"Resume error: {e}")


def _cmd_forex_execution_mode():
    try:
        _send(
            "<b>FTMO FOREX EXECUTION MODE</b>\n\n"
            f"FOREX_EXECUTION_MODE: {FOREX_EXECUTION_MODE}\n"
            f"FOREX_EXECUTION_REVALIDATE_CYCLE_SECONDS: {FOREX_EXECUTION_REVALIDATE_CYCLE_SECONDS}\n"
            f"FOREX_MAX_SPREAD_PCT: {FOREX_MAX_SPREAD_PCT}\n"
            f"FOREX_ALLOWED_SIGNAL_AGE_SECONDS: {FOREX_ALLOWED_SIGNAL_AGE_SECONDS}\n"
            f"FOREX_MAX_ENTRY_DRIFT_PERCENT: {FOREX_MAX_ENTRY_DRIFT_PERCENT}\n"
            f"FOREX_MAX_ENTRY_DRIFT_POINTS: {FOREX_MAX_ENTRY_DRIFT_POINTS}\n"
            f"FOREX_EXECUTION_MIN_RR: {FOREX_EXECUTION_MIN_RR}\n"
            f"FOREX_EXECUTION_INVALIDATION_BUFFER_POINTS: {FOREX_EXECUTION_INVALIDATION_BUFFER_POINTS}\n"
            f"FOREX_ALLOWED_UTC_WINDOWS: {FOREX_ALLOWED_UTC_WINDOWS}\n"
            f"FOREX_DISABLED_SYMBOLS: {FOREX_DISABLED_SYMBOLS}"
        )
    except Exception as e:
        _send(f"Execution mode error: {e}")


def _cmd_forex_execution_stats():
    try:
        from utils.execution_validation import get_forex_execution_stats_for_date
        s = get_forex_execution_stats_for_date()
        if s.get('total_signals', 0) == 0:
            _send(
                "FTMO Execution Stats (Today)\n\n"
                "No signals found in data/forex_execution_validation_audit.jsonl for today."
            )
            return
        lines = []
        for reason, cnt in sorted(
            (s.get('blocked_reason_breakdown') or {}).items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            lines.append(f"{reason}: {cnt}")
        breakdown = '\n'.join(lines) if lines else 'None'
        _send(
            "FTMO Execution Validation Stats (Today)\n\n"
            f"total_signals: {s.get('total_signals')}\n"
            f"armed_count: {s.get('armed_count')}\n"
            f"executed_count: {s.get('executed_count')}\n"
            f"blocked_count: {s.get('blocked_count')}\n"
            f"block_rate: {s.get('block_rate_pct')}%\n\n"
            "blocked reason breakdown:\n"
            f"{breakdown}"
        )
    except Exception as e:
        _send(f"Execution stats error: {e}")


def _cmd_help():
    _send(
        "<b>CB6 QUANTUM — FTMO ICT SILVER BULLET</b>\n\n"
        "<b>MARKETS (FTMO $10K)</b>\n"
        "Silver (XAGUSD) : min score 11 | risk 0.7% = $70\n"
        "Oil    (USOIL)  : min score 11 | risk 0.7% = $70\n"
        "EUR/USD (EURUSD): min score 11 | risk 0.7% = $70\n"
        "Gold   (XAUUSD) : ⛔ PAUSED — trend issue May 2026\n\n"
        "<b>ACCOUNT — FTMO $10K Free Trial</b>\n"
        "Leverage       : 1:100\n"
        "Risk/trade     : 0.7% = $70  (sprint mode)\n"
        "Max daily loss : 3% = $300\n"
        "Best day rule  : $250 cap/day\n"
        "Total DD limit : 10% = $1,000 (EOD trailing)\n"
        "Profit target  : 5% = $500\n\n"
        "<b>SETUP CHAIN (ICT Silver Bullet)</b>\n"
        "1. Liquidity Sweep — stop hunt confirmed\n"
        "2. Market Structure Shift — CHoCH or BOS\n"
        "3. Fair Value Gap — 3-candle imbalance entry\n"
        "4. H1 + H4 EMA bias — aligned direction\n"
        "5. UT Bot confirmation\n"
        "   Min score: 11 prime KZ | 12 off-peak KZ\n\n"
        "<b>TRADE PLAN</b>\n"
        "Timeframe : 15-min\n"
        "SL        : Opposite FVG edge\n"
        "T1 (1/3)  : 1:2R — SL → breakeven\n"
        "T2 (1/3)  : 1:3R\n"
        "T3 (1/3)  : DOL level or 1:4R+\n\n"
        "<b>SESSIONS (UTC)</b>\n"
        "London KZ   : 07-12 UTC  ← entries allowed\n"
        "             Prime: 07-10 | Off-peak: 10-12\n"
        "NY KZ       : 16-20 UTC  ← entries allowed\n"
        "             Prime: 16-18 | Off-peak: 18-20\n"
        "Rollover    : 22-23 UTC  ← hard block\n\n"
        "<b>COMMANDS</b>\n"
        "/fx_status    FTMO engine health + heartbeat\n"
        "/fx_pnl       FTMO P&amp;L + challenge progress\n"
        "/fx_ftmo      FTMO rule tracker with progress bars\n"
        "/fx_terminals FTMO terminal status\n\n"
        "/fx_positions Open FTMO trades with live price + uPnL\n"
        "/fx_journal   Last 5 FTMO closed trades\n"
        "/fx_lots      Live FTMO lot sizes + risk\n"
        "/fx_exit      List open — /fx_exit A to close trade A\n\n"
        "/fx_stop      Pause FTMO engine\n"
        "/fx_resume    Resume FTMO engine\n\n"
        "/ml_status    ML shadow accuracy + model status\n"
        "/ml_train     Force retrain (all | nse | forex | forex gft)\n\n"
        "/forex_execution_mode  Execution gate config\n"
        "/forex_execution_stats Execution validation stats\n"
        "/fx_help      This message"
    )


def _get_all_open_trades() -> list:
    """Return open FTMO trades only."""
    from forex_engine.prop_firms.ftmo.ftmo_state import load_state as ftmo_load
    items = []
    for t in ftmo_load().get('open_trades', []):
        items.append({'trade': t, 'platform': 'FTMO', 'account': None})
    return items


def _cmd_exit(arg: str = ''):
    try:
        from forex_engine.forex_instruments import INSTRUMENTS
        from forex_engine.prop_firms.ftmo.ftmo_state import manual_exit_trade as ftmo_manual_exit

        items = _get_all_open_trades()

        if not items:
            _send("CB6 QUANTUM FTMO — No open FTMO trades to exit.")
            return

        arg = arg.strip().upper()
        if not arg:
            lines = ["<b>CB6 QUANTUM — FTMO OPEN TRADES</b>\n\nSend /fx_exit A, /fx_exit B, etc. to close:\n"]
            letters = 'ABCDEFGHIJ'
            for i, item in enumerate(items):
                t      = item['trade']
                sym    = t['symbol']
                label  = INSTRUMENTS.get(sym, {}).get('label', sym)
                dirn   = 'LONG' if t['direction'] == 'BULLISH' else 'SHORT'
                entry  = t['entry_price']
                targets = ','.join(t.get('targets_hit', [])) or 'none'
                ltr    = letters[i] if i < len(letters) else str(i + 1)

                price = None
                if _adapter_ref:
                    try:
                        price = _adapter_ref.get_price(sym)
                    except Exception:
                        pass
                upnl_str = ''
                if price:
                    cfg    = INSTRUMENTS.get(sym, {})
                    cs     = cfg.get('contract_size', 100000)
                    lots   = t['lots']
                    booked = len(t.get('targets_hit', []))
                    rem    = round(lots * (3 - booked) / 3, 2)
                    dist   = (price - entry) if t['direction'] == 'BULLISH' else (entry - price)
                    upnl   = round(rem * cs * dist, 2)
                    upnl_str = f"  |  uPnL: ${upnl:+.2f}"
                lines.append(
                    f"<b>{ltr}.</b> {label} {dirn} [FTMO]  id:{t['id']}\n"
                    f"   Entry {entry}  targets:{targets}{upnl_str}"
                )
            _send('\n'.join(lines))
            return

        letters = 'ABCDEFGHIJ'
        matched = None
        if arg in letters and letters.index(arg) < len(items):
            matched = items[letters.index(arg)]
        elif arg.isdigit() and 1 <= int(arg) <= len(items):
            matched = items[int(arg) - 1]
        else:
            for item in items:
                if item['trade']['id'].upper().startswith(arg):
                    matched = item
                    break

        if not matched:
            _send(
                f"CB6 QUANTUM FTMO — trade reference '{arg}' not found.\n"
                f"Send /fx_exit (no args) to see open FTMO trades."
            )
            return

        trade = matched['trade']
        sym   = trade['symbol']
        label = INSTRUMENTS.get(sym, {}).get('label', sym)
        dirn  = 'LONG' if trade['direction'] == 'BULLISH' else 'SHORT'

        exit_price = None
        if _adapter_ref:
            try:
                exit_price = _adapter_ref.get_price(sym)
            except Exception:
                pass
        if exit_price is None:
            exit_price = trade['entry_price']
            _send(
                f"⚠️ Could not fetch live price for {sym} — "
                f"using entry price {exit_price} (PnL will show $0)."
            )

        ev = ftmo_manual_exit(trade['id'], exit_price)
        if ev is None:
            _send(f"CB6 QUANTUM FTMO — trade {trade['id']} not found in state (already closed?).")
            return

        pnl  = ev['pnl']
        sign = '+' if pnl >= 0 else ''
        icon = '✅' if pnl >= 0 else '🔴'
        _send(
            f"<b>CB6 QUANTUM — FTMO MANUAL EXIT</b>\n\n"
            f"{icon} {label} {dirn}\n\n"
            f"Entry      : {trade['entry_price']}\n"
            f"Exit price : {exit_price}\n"
            f"PnL        : {sign}${pnl:.2f}\n"
            f"Targets hit: {','.join(trade.get('targets_hit', [])) or 'none'}\n"
            f"Trade ID   : {trade['id']}\n"
            f"Time       : {datetime.now().strftime('%H:%M:%S IST')}\n\n"
            f"FTMO state updated."
        )
    except Exception as e:
        _send(f"Exit error: {e}")


def _cmd_journal():
    try:
        from forex_engine.forex_paper_trader import load_state as ftmo_load

        closed = ftmo_load().get('closed_trades', [])
        closed_sorted = sorted(closed, key=lambda x: x.get('entry_time', ''), reverse=True)

        if not closed_sorted:
            _send("CB6 QUANTUM FTMO — No closed FTMO trades yet.")
            return

        recent = closed_sorted[:5]
        lines  = ["<b>CB6 QUANTUM — FTMO RECENT TRADES (last 5)</b>\n"]
        for t in recent:
            sym     = t.get('symbol', '?')
            dirn    = 'L' if t.get('direction') == 'BULLISH' else 'S'
            pnl     = t.get('pnl_usd', 0.0)
            sign    = '+' if pnl >= 0 else ''
            targets = ','.join(t.get('targets_hit', [])) or 'none'
            mss     = t.get('mss_type', '?')
            score   = t.get('confluence', '?')
            entry   = t.get('entry_price', '?')
            exit_p  = t.get('exit_price', '?')
            entry_t = t.get('entry_time', '')[:16]
            icon    = '✅' if pnl > 0 else '❌'
            lines.append(
                f"{icon} <b>[FTMO] {sym} {dirn}</b>  ${sign}{pnl:.2f}\n"
                f"   {mss} · score {score} · targets {targets}\n"
                f"   Entry {entry} → Exit {exit_p}\n"
                f"   {entry_t}"
            )
        _send('\n\n'.join(lines))
    except Exception as e:
        _send(f"Journal error: {e}")


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
            for mkt, acc in [('nse', ''), ('forex', 'ftmo'), ('forex', 'gft')]:
                trigger_now(mkt, acc)
            _send("ML training triggered for all markets. Results in ~2 min.")
        elif parts[0] == 'nse':
            trigger_now('nse', '')
            _send("ML training triggered for NSE.")
        elif parts[0] == 'forex':
            acc = parts[1] if len(parts) > 1 else ''
            if acc in ('ftmo', 'gft'):
                trigger_now('forex', acc)
                _send(f"ML training triggered for forex/{acc.upper()}.")
            else:
                trigger_now('forex', 'ftmo')
                trigger_now('forex', 'gft')
                _send("ML training triggered for FTMO + GFT.")
        else:
            _send("Usage: /ml_train | /ml_train nse | /ml_train forex | /ml_train forex gft")
    except Exception as e:
        _send(f"ML train error: {e}")


# ── Dispatch ────────────────────────────────────────────────────────────────────

_COMMANDS = {
    '/start'                 : _cmd_start,
    '/fx_status'             : _cmd_status,
    '/fx_pnl'                : _cmd_pnl,
    '/fx_positions'          : _cmd_positions,
    '/fx_lots'               : _cmd_lots,
    '/fx_ftmo'               : _cmd_ftmo,
    '/fx_journal'            : _cmd_journal,
    '/fx_stop'               : _cmd_stop,
    '/fx_resume'             : _cmd_resume,
    '/fx_terminals'          : _cmd_terminals,
    '/forex_execution_mode'  : _cmd_forex_execution_mode,
    '/forex_execution_stats' : _cmd_forex_execution_stats,
    '/fx_execution_mode'     : _cmd_forex_execution_mode,
    '/fx_execution_stats'    : _cmd_forex_execution_stats,
    '/fx_help'               : _cmd_help,
    '/fx_exit'               : _cmd_exit,
    '/ml_status'             : _cmd_ml_status,
    '/ml_train'              : _cmd_ml_train,
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
                logger.warning(f"FTMO bot rejected command from unknown chat_id={chat_id}")
            continue

        if text.startswith('/') and '@' in text.split()[0]:
            first, *rest = text.split()
            text = ' '.join([first.split('@')[0]] + rest)

        logger.info(f"FTMO bot command: {text}")

        parts = text.split(None, 1)
        cmd   = parts[0]
        arg   = parts[1] if len(parts) > 1 else ''

        fn = _COMMANDS.get(cmd)
        if fn:
            is_confirm_reply = text.lower().endswith(' confirm')
            if not is_confirm_reply and _rate_limited(chat_id, cmd):
                _send("Command rate limit active. Try again in a few seconds.")
                continue
            if _needs_confirmation(cmd, arg):
                ok, confirmed_text = _confirmation_ok(chat_id, text)
                if not ok:
                    continue
                parts = confirmed_text.split(None, 1)
                cmd   = parts[0]
                arg   = parts[1] if len(parts) > 1 else ''
                fn    = _COMMANDS.get(cmd)
            if cmd == '/fx_exit':
                threading.Thread(target=fn, args=(arg,), daemon=True).start()
            else:
                threading.Thread(target=fn, daemon=True).start()
        elif text.startswith('/'):
            _send(f"Unknown command: {cmd}\nUse /fx_help to see FTMO commands.")


# ── Lock file — prevents duplicate FTMO bot listeners ──────────────────────────

_BOT_LOCK_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'data', 'ftmo_bot.lock'
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _acquire_bot_lock() -> bool:
    if os.path.exists(_BOT_LOCK_FILE):
        try:
            with open(_BOT_LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid() and _pid_alive(old_pid):
                logger.warning(
                    f"FTMO bot: another listener already running (PID {old_pid}) — "
                    f"skipping duplicate start"
                )
                return False
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


# ── Listener ────────────────────────────────────────────────────────────────────

def start_listening():
    global _listener_running, _last_update_id
    if _listener_running:
        logger.warning("FTMO bot: listener already running — duplicate start ignored")
        return

    if not _acquire_bot_lock():
        return

    _listener_running = True
    # Drain stale pending updates to avoid replaying old commands on restart
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{FTMO_TOKEN}/getUpdates",
            params={'offset': -1, 'timeout': 1},
            timeout=(3, 5),
        )
        result = r.json().get('result', []) if r.status_code == 200 else []
        if result:
            _last_update_id = result[-1]['update_id']
            logger.info(f"FTMO bot: drained {len(result)} pending update(s) (last id={_last_update_id})")
    except Exception:
        pass

    logger.info("FTMO Telegram bot listener started")
    try:
        while True:
            try:
                updates = _get_updates()
                if updates:
                    _process_updates(updates)
                time.sleep(3)
            except Exception as e:
                logger.error(f"FTMO bot listener error: {e}")
                time.sleep(10)
    finally:
        _release_bot_lock()


def send_alert(text: str) -> bool:
    """Send an alert to the FTMO Telegram chat. Called by forex_worker."""
    return _send(text)
