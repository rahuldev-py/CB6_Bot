# communications/gft_bot.py
#
# CB6 Quantum — GFT Telegram bot (GFT $5K 2-Step engine ONLY).
#
# Token  : TELEGRAM_BOT_TOKEN_GFT   (hard-fail at startup if missing)
# Auth   : CB6_ADMIN_USER_ID        (single admin numeric user ID)
# Scope  : ALL commands touch only GFT state/connector/terminal.
#          FTMO is fully isolated in communications/forex_bot.py.
#
# Commands:
#   /start         — GFT overview + command list
#   /gft_status    — GFT engine health + heartbeat
#   /gft_pnl       — GFT P&L + phase progress
#   /gft_phase     — GFT phase tracker with progress bars
#   /gft_positions — GFT open trades with live price + uPnL
#   /gft_lots      — GFT lot sizes + risk per symbol
#   /gft_journal   — Last 5 GFT closed trades
#   /gft_exit      — Close GFT trade manually — /gft_exit A
#   /gft_stop      — Pause GFT engine (confirmation required)
#   /gft_resume    — Resume GFT engine (confirmation required)
#   /gft_terminal  — GFT terminal isolation status
#   /gft_help      — GFT strategy rules + command list
#   /ml_status     — ML shadow accuracy (shared read-only)
#   /ml_train      — Force ML retrain (shared)

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

# ── Token config — hard-fail if GFT token absent ───────────────────────────────
_GFT_TOKEN_RAW = os.getenv('TELEGRAM_BOT_TOKEN_GFT', '').strip()
if not _GFT_TOKEN_RAW:
    raise SystemExit(
        "FATAL: TELEGRAM_BOT_TOKEN_GFT not set in .env — "
        "GFT bot cannot start. Create a bot via @BotFather, set the key, and restart."
    )
GFT_TOKEN = _GFT_TOKEN_RAW

_ADMIN_ID_RAW = os.getenv('CB6_ADMIN_USER_ID', '').strip()
if not _ADMIN_ID_RAW:
    raise SystemExit(
        "FATAL: CB6_ADMIN_USER_ID not set in .env — "
        "GFT bot cannot authenticate. Set the key and restart."
    )
GFT_CHAT_ID = _ADMIN_ID_RAW

AUTHORIZED_CHAT_IDS = {
    cid.strip()
    for cid in str(GFT_CHAT_ID or '').split(',')
    if cid.strip()
}

COMMAND_RATE_LIMIT_SECS = int(os.getenv('GFT_TELEGRAM_RATE_LIMIT_SECS', '3'))
CONFIRM_TTL_SECS        = int(os.getenv('GFT_CONFIRM_TTL_SECS', '30'))

# ── GFT MT5 connector references ───────────────────────────────────────────────
_last_update_id     = 0
_listener_running   = False
_connector_ref      = None   # GFT $5K 2-Step MT5Connector
_connector_10k_ref  = None   # GFT $10K Instant MT5Connector
_last_command_at    = {}
_pending_confirms   = {}


def _mask(token: str) -> str:
    return mask_token(token)


logger.info(f"GFT Telegram bot armed (token {_mask(GFT_TOKEN)})")


def set_connector(connector):
    """Called by GFT2StepWorker.run() to wire in the live MT5 connector."""
    global _connector_ref
    _connector_ref = connector


# Keep the old name as an alias so gft_5k_2step.py import is one line
set_gft_connector = set_connector


def set_10k_connector(connector):
    """Called by GFT10KWorker.run() to wire in the live MT5 connector."""
    global _connector_10k_ref
    _connector_10k_ref = connector


# ── Balance helper ──────────────────────────────────────────────────────────────

def _get_balance() -> tuple:
    """Return (balance, equity, open_pnl, is_live) for GFT account."""
    is_live = os.getenv('GFT_2STEP_PAPER', 'true').lower() == 'false'
    if is_live and _connector_ref:
        try:
            bal = _connector_ref.get_balance()
            eq  = _connector_ref.get_equity()
            if bal and bal > 0:
                return bal, eq, round(eq - bal, 2), True
        except Exception:
            pass
    from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load
    st  = gft_load()
    cap = st.get('capital', 5000.0)
    return cap, cap, 0.0, False


# ── Telegram helpers — thin wrappers over communications.telegram_helpers ────────

def _send(text: str, parse_mode: str = 'HTML') -> bool:
    return send_message(GFT_TOKEN, GFT_CHAT_ID, text, parse_mode, logger)


def _get_updates() -> list:
    global _last_update_id
    updates, _last_update_id = _tg_get_updates(GFT_TOKEN, _last_update_id, logger)
    return updates


# ── Auth + rate-limiting ────────────────────────────────────────────────────────

def _is_authorized_chat(chat_id: str) -> bool:
    return is_authorized_chat(chat_id, AUTHORIZED_CHAT_IDS)


def _rate_limited(chat_id: str, command: str) -> bool:
    return is_rate_limited(chat_id, command, _last_command_at, COMMAND_RATE_LIMIT_SECS)


def _needs_confirmation(cmd: str, arg: str) -> bool:
    if cmd in ('/gft_stop', '/gft_resume', '/g10k_stop', '/g10k_resume'):
        return True
    return cmd == '/gft_exit' and bool(arg.strip())


def _confirmation_ok(chat_id: str, text: str) -> tuple:
    return check_confirmation(chat_id, text, _pending_confirms, CONFIRM_TTL_SECS, _send)


# ── Command handlers ────────────────────────────────────────────────────────────

def _cmd_start():
    try:
        balance, _, _, is_live = _get_balance()
        starting = 5000.0
        growth   = round((balance - starting) / starting * 100, 2)
        mode     = 'LIVE — GFT MT5' if is_live else 'Paper (tracking)'

        from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load, get_summary as gft_sum
        state   = gft_load()
        gs      = gft_sum(state)
        phase   = state.get('current_phase', 'phase_1')

        _send(
            "<b>CB6 QUANTUM — GFT $5K 2-STEP ENGINE</b>\n\n"
            "Markets  : Silver (XAGUSD) | Oil (USOIL)\n"
            "           Gold (XAUUSD) ⛔ PERMANENTLY DISABLED on GFT\n"
            "Strategy : ICT Silver Bullet · 15-min candles\n"
            "Platform : MetaTrader 5 — GFT $5K 2-Step GOAT\n\n"
            f"<b>ACCOUNT [GFT]</b>\n"
            f"Balance  : ${balance:,.2f}\n"
            f"Started  : ${starting:,.2f}\n"
            f"Growth   : {growth:+.2f}%\n"
            f"Phase    : {phase.upper().replace('_', ' ')}\n"
            f"Mode     : {mode}\n\n"
            "<b>SESSIONS (UTC)</b>\n"
            "London KZ : 07-12 UTC  ← entries allowed\n"
            "NY KZ     : 16-20 UTC  ← entries allowed\n"
            "Rollover  : 22-23 UTC  ← blocked (spreads explode)\n\n"
            "<b>ACCOUNT STATUS</b>\n"
            "/gft_status   GFT engine health + heartbeat\n"
            "/gft_pnl      GFT P&amp;L + phase progress\n"
            "/gft_phase    GFT phase tracker with progress bars\n"
            "/gft_terminal GFT terminal status\n\n"
            "<b>TRADES</b>\n"
            "/gft_positions Open GFT trades with live price + uPnL\n"
            "/gft_journal   Last 5 GFT closed trades\n"
            "/gft_lots      Live GFT lot sizes + risk\n"
            "/gft_exit      Close a trade — /gft_exit A\n\n"
            "<b>CONTROL</b>\n"
            "/gft_stop      Pause GFT $5K engine\n"
            "/gft_resume    Resume GFT $5K engine\n\n"
            "<b>GFT $10K INSTANT</b>\n"
            "/g10k_status   $10K engine health + capital\n"
            "/g10k_pnl      $10K P&amp;L + DD tracker\n"
            "/g10k_positions $10K open trades\n"
            "/g10k_stop     Pause $10K engine\n"
            "/g10k_resume   Resume $10K engine\n\n"
            "<b>ML &amp; ANALYTICS</b>\n"
            "/ml_status     ML shadow accuracy + model status\n"
            "/ml_train      Force ML retrain\n\n"
            "/gft_help      Full GFT strategy rules"
        )
    except Exception as e:
        _send(f"Start error: {e}")


def _cmd_status():
    try:
        from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load, get_summary as gft_sum

        state = gft_load()
        gs    = gft_sum(state)

        balance, equity, open_pnl, is_live = _get_balance()

        base   = os.path.dirname(os.path.dirname(__file__))
        hb_gft = os.path.join(base, 'data', 'gft_2step_heartbeat.txt')

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

        starting  = 5000.0
        total_pnl = round(balance - starting, 2)
        paused    = 'PAUSED' if state.get('paused') else 'RUNNING'
        open_line = f"  open ${open_pnl:+.2f}" if open_pnl != 0 else ""

        # GFT $1K Instant summary
        try:
            from forex_engine.gft_1k_instant.state import load_state as _1k_load, HEARTBEAT_FILE as _1k_hb
            _1k = _1k_load()
            _1k_cap  = _1k.get('capital', 1000.0)
            _1k_dpnl = _1k.get('daily_pnl', 0.0)
            _1k_paus = '⏸' if _1k.get('paused') else '▶'
            _1k_hb_s = _hb_age(_1k_hb)
            _1k_line = (f"\n<b>GFT $1K Instant [{_1k_paus}]</b>  hb:{_1k_hb_s}\n"
                        f"Balance    : ${_1k_cap:,.2f}  |  Daily: ${_1k_dpnl:+.2f}")
        except Exception:
            _1k_line = "\n<b>GFT $1K Instant</b>  [state unavailable]"

        # GFT $10K Instant summary
        try:
            from forex_engine.gft_10k.state import load_state as _10k_load, HEARTBEAT_FILE as _10k_hb
            _10k = _10k_load()
            _10k_cap  = _10k.get('capital', 10000.0)
            _10k_dpnl = _10k.get('daily_pnl', 0.0)
            _10k_paus = '⏸' if _10k.get('paused') else '▶'
            _10k_hb_s = _hb_age(_10k_hb)
            _10k_line = (f"\n<b>GFT $10K Instant [{_10k_paus}]</b>  hb:{_10k_hb_s}\n"
                         f"Balance    : ${_10k_cap:,.2f}  |  Daily: ${_10k_dpnl:+.2f}")
        except Exception:
            _10k_line = "\n<b>GFT $10K Instant</b>  [state unavailable]"

        _send(
            f"<b>CB6 QUANTUM — GFT STATUS</b>\n\n"
            f"Session    : {session} ({utc_hour:02d}:xx UTC)\n\n"
            f"<b>GFT $5K 2-Step [{paused}]</b>  hb:{_hb_age(hb_gft)}\n"
            f"Balance    : ${balance:,.2f}{open_line}\n"
            f"Daily PnL  : ${gs['daily_pnl']:+.2f}  |  Total: ${total_pnl:+.2f}\n"
            f"Phase      : {gs['phase'].upper().replace('_', ' ')}\n"
            f"Trades     : {gs['open_trades']} open | {gs['daily_trades']} today\n"
            f"Risk Mode  : {gs['risk_mode'].upper()}"
            f"{_1k_line}"
            f"{_10k_line}"
        )
    except Exception as e:
        _send(f"Status error: {e}")


def _cmd_pnl():
    try:
        from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load, get_summary as gft_sum
        from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE as _GP

        state   = gft_load()
        gs      = gft_sum(state)
        prog    = gs['progress']
        phase   = state.get('current_phase', 'phase_1')

        balance, equity, open_pnl, is_live = _get_balance()
        start      = _GP['account_size']
        total_pnl  = round(balance - start, 2)
        daily_pnl  = state.get('daily_pnl', 0.0)
        daily_lim  = _GP['daily_loss_limit']
        daily_loss = abs(daily_pnl) if daily_pnl < 0 else 0.0

        p_target   = prog.get('profit_target', 0)
        p_earned   = prog.get('profit_earned', 0)
        p_pct      = round(p_earned / p_target * 100, 1) if p_target else 0
        p_remain   = round(p_target - p_earned, 2)

        static_floor = start - _GP['total_loss_limit']
        headroom     = round(balance - static_floor, 2)

        mode_label = 'LIVE — GFT MT5' if is_live else 'PAPER (tracking)'
        open_line  = f"\nOpen PnL     : ${open_pnl:+.2f}  (unrealized)" if is_live and open_pnl != 0 else ""

        closed = state.get('closed_trades', [])
        wins   = sum(1 for t in closed if t.get('pnl_usd', 0) > 0)
        losses = sum(1 for t in closed if t.get('pnl_usd', 0) <= 0)
        wr     = round(wins / len(closed) * 100, 1) if closed else 0

        _send(
            f"<b>CB6 QUANTUM — GFT P&amp;L</b>  [{mode_label}]\n\n"
            f"<b>GFT $5K 2-STEP ACCOUNT</b>\n"
            f"Balance      : ${balance:,.2f}\n"
            f"Total PnL    : ${total_pnl:+.2f}"
            f"{open_line}\n\n"
            f"<b>TODAY [GFT]</b>\n"
            f"Daily PnL    : ${daily_pnl:+.2f}  (limit -${daily_lim:.0f})\n"
            f"Daily loss   : ${daily_loss:.2f} / ${daily_lim:.0f} "
            f"({round(daily_loss/daily_lim*100,1) if daily_lim else 0}%)\n\n"
            f"<b>GFT PHASE ({phase.upper().replace('_', ' ')})</b>\n"
            f"Target       : ${p_target:.0f}  →  {p_pct}% there  (earned ${p_earned:.2f})\n"
            f"Still need   : ${p_remain:.2f}\n"
            f"DD floor     : ${static_floor:.2f}  (headroom ${headroom:.2f})\n"
            f"Trades       : {len(closed)} closed  ({wins}W/{losses}L  {wr}% WR)\n"
            f"Today trades : {gs['daily_trades']}\n"
            f"Risk Mode    : {gs['risk_mode'].upper()}"
        )
    except Exception as e:
        _send(f"PnL error: {e}")


def _cmd_phase():
    """Detailed GFT phase tracker with progress bars."""
    try:
        from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load, get_summary as gft_sum
        from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE as _GP
        from forex_engine.prop_firms.gft.gft_risk_rules import get_risk_mode

        state  = gft_load()
        gs     = gft_sum(state)
        prog   = gs['progress']
        phase  = state.get('current_phase', 'phase_1')

        balance, equity, open_pnl, is_live = _get_balance()
        start      = _GP['account_size']
        total_pnl  = round(balance - start, 2)

        p_target   = prog.get('profit_target', 0)
        p_earned   = prog.get('profit_earned', 0)
        p_pct      = round(p_earned / p_target * 100, 1) if p_target else 0
        p_remain   = round(p_target - p_earned, 2)

        daily_pnl  = state.get('daily_pnl', 0.0)
        daily_lim  = _GP['daily_loss_limit']
        total_lim  = _GP['total_loss_limit']
        daily_loss = abs(daily_pnl) if daily_pnl < 0 else 0.0
        daily_used_pct = round(daily_loss / daily_lim * 100, 1) if daily_lim else 0

        static_floor = start - total_lim
        headroom     = round(balance - static_floor, 2)

        risk_mode, risk_reason = get_risk_mode(state)

        def _bar(used, limit):
            pct    = min(used / limit * 100, 100) if limit > 0 else 0
            filled = int(pct / 10)
            return f"{'█'*filled}{'░'*(10-filled)} {pct:.0f}%"

        safe   = balance > static_floor
        status = 'ON TRACK ✅' if safe and daily_loss < daily_lim * 0.8 else 'WARNING ⚠️'
        if phase in ('phase_2_passed', 'funded'):
            status = 'FUNDED ✅'
        if not safe:
            status = 'BREACH ❌'

        open_line = f"\nOpen PnL : ${open_pnl:+.2f}  (unrealized)" if is_live and open_pnl != 0 else ""
        src = 'LIVE MT5' if is_live else 'Paper state'

        closed = state.get('closed_trades', [])
        wins   = sum(1 for t in closed if t.get('pnl_usd', 0) > 0)
        losses = sum(1 for t in closed if t.get('pnl_usd', 0) <= 0)
        wr     = round(wins / len(closed) * 100, 1) if closed else 0

        min_days    = _GP.get('min_trading_days', 3)
        active_days = state.get('trading_days_active', 0)
        days_ok     = active_days >= min_days
        days_icon   = '✅' if days_ok else f'{active_days}/{min_days} ⏳'

        _send(
            f"<b>CB6 QUANTUM — GFT $5K 2-STEP TRACKER</b>\n"
            f"Phase : {phase.upper().replace('_', ' ')}  [{src}]\n\n"
            f"<b>PHASE PROFIT TARGET (need ${p_target:.0f})</b>\n"
            f"Earned  : ${p_earned:+.2f}  →  still need ${p_remain:.2f}\n"
            f"{_bar(max(p_earned, 0), p_target)}\n\n"
            f"<b>TRADING DAYS (min {min_days} required)</b>\n"
            f"Active days : {days_icon}\n\n"
            f"<b>DAILY LOSS (max ${daily_lim:.0f})</b>\n"
            f"Used    : ${daily_loss:.2f}  ({daily_used_pct}%)\n"
            f"{_bar(daily_loss, daily_lim)}\n\n"
            f"<b>TOTAL LOSS GUARD (static floor ${static_floor:.0f})</b>\n"
            f"Balance : ${balance:,.2f}  ({'safe ✅' if safe else 'BREACH ❌'}){open_line}\n"
            f"Headroom: ${headroom:.2f} above floor\n"
            f"{_bar(max(0, total_lim - headroom), total_lim)}\n\n"
            f"<b>STATS</b>\n"
            f"Total PnL   : ${total_pnl:+.2f}\n"
            f"Daily trades: {gs['daily_trades']}\n"
            f"Open trades : {gs['open_trades']}\n"
            f"Closed      : {len(closed)}  ({wins}W/{losses}L  {wr}% WR)\n"
            f"Risk Mode   : {risk_mode.upper()}\n"
            f"  └ {risk_reason}\n\n"
            f"Status  : {status}"
        )
    except Exception as e:
        _send(f"Phase error: {e}")


def _cmd_positions():
    try:
        from forex_engine.forex_instruments import INSTRUMENTS
        items = _get_all_open_trades()

        if not items:
            _send("CB6 QUANTUM GFT — No open GFT positions.")
            return

        for item in items:
            t        = item['trade']
            sym      = t['symbol']
            cfg      = INSTRUMENTS.get(sym, {})
            contract = cfg.get('contract_size', 100)
            label    = cfg.get('label', sym)
            is_long  = t['direction'] == 'BULLISH'
            entry    = t['entry_price']
            sl       = t['current_sl']
            lots     = t['lots']
            phase_l  = f"  phase:{t.get('phase', '?')}"

            price = None
            if _connector_ref:
                try:
                    price = _connector_ref.get_price(sym)
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

            dir_s   = 'LONG' if is_long else 'SHORT'
            ghost   = t.get('ticket', 0) == 0
            ghost_w = "\n⚠️ NO MT5 ORDER — state only" if ghost else ""
            _send(
                f"<b>[GFT-2STEP] {label} — {dir_s}</b>  [{t['id']}]{ghost_w}\n\n"
                f"Entry      : {entry}\n"
                f"Live Price : {price_s}\n"
                f"Unrealised : ${upnl:+.2f}\n\n"
                f"SL         : {sl}  ({sl_dist} away)\n"
                f"T1 (1/3)   : {t['target1']}  ({t1_dist} away)\n"
                f"T2 (1/3)   : {t['target2']}\n"
                f"T3 (1/3)   : {t['target3']}\n\n"
                f"Lots       : {lots}\n"
                f"Risk       : ${t['risk_usd']}\n"
                f"Targets hit: {','.join(t.get('targets_hit', [])) or 'None'}{phase_l}\n"
                f"Opened     : {t['entry_time']}"
            )
            time.sleep(0.3)
    except Exception as e:
        _send(f"Positions error: {e}")


def _cmd_lots():
    try:
        from forex_engine.forex_instruments import INSTRUMENTS
        from forex_engine.trade.lot_calculator import calc_lot_size, dollar_risk
        from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE as _GP
        from forex_engine.prop_firms.gft.gft_risk_rules import get_risk_mode
        from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load

        state     = gft_load()
        balance, _, _, _ = _get_balance()
        risk_mode, risk_reason = get_risk_mode(state)

        risk_pct = {
            'normal' : _GP['risk_normal_pct'],
            'reduced': _GP['risk_reduced_pct'],
        }.get(risk_mode, _GP['risk_normal_pct'])

        risk_usd = balance * risk_pct / 100

        lines = [
            f"<b>CB6 QUANTUM — GFT LOT SIZING</b>\n",
            f"Account   : ${balance:,.2f}",
            f"Risk mode : {risk_mode.upper()}  ({risk_reason})",
            f"Risk/trade: {risk_pct}% = ${risk_usd:.2f}",
            f"Leverage  : 1:{_GP['leverage']} (GFT GOAT)\n",
        ]

        for sym in _GP['enabled_symbols']:
            cfg    = INSTRUMENTS.get(sym, {})
            label  = cfg.get('label', sym)
            min_sl = cfg.get('min_sl_dist', 0.05)

            price = None
            if _connector_ref:
                try:
                    price = _connector_ref.get_price(sym)
                except Exception:
                    pass

            if price is None:
                lines.append(f"\n<b>{label}</b>\nPrice: unavailable (MT5 not connected)")
                continue

            typical_sl = min_sl * 2
            entry_long = price
            sl_long    = round(price - typical_sl, 5)

            lots     = calc_lot_size(sym, balance, entry_long, sl_long, risk_pct)
            risk_d   = dollar_risk(sym, lots, entry_long, sl_long)
            notional = round(lots * cfg.get('contract_size', 5000) * price, 2)
            pip      = cfg.get('pip_size', 0.001)
            pip_val  = round(lots * cfg.get('contract_size', 5000) * pip, 4)

            daily_lim    = _GP['daily_loss_limit']
            trades_to_dd = int(daily_lim / risk_d) if risk_d > 0 else 0

            lines.append(
                f"\n<b>{label} ({sym})</b>\n"
                f"Live Price : {price:.5f}\n"
                f"Lot size   : {lots} lots\n"
                f"SL distance: {typical_sl} (typical)\n"
                f"Risk       : ${risk_d:.2f} per trade\n"
                f"Notional   : ${notional:,.0f}\n"
                f"Pip value  : ${pip_val:.4f} per pip\n"
                f"Max losses/day before ${daily_lim:.0f} limit: {trades_to_dd}"
            )

        lines.append(
            f"\n<b>GFT RULES</b>\n"
            f"Max daily loss : ${_GP['daily_loss_limit']:.0f} (4%)\n"
            f"Max total loss : ${_GP['total_loss_limit']:.0f} (10% static)\n"
            f"Normal risk    : {_GP['risk_normal_pct']}% = ${balance * _GP['risk_normal_pct'] / 100:.2f}\n"
            f"Reduced risk   : {_GP['risk_reduced_pct']}% = ${balance * _GP['risk_reduced_pct'] / 100:.2f}\n"
            f"Max risk (A+)  : {_GP['risk_max_pct']}% = ${balance * _GP['risk_max_pct'] / 100:.2f}"
        )

        _send('\n'.join(lines))
    except Exception as e:
        _send(f"Lots error: {e}")


def _cmd_terminal():
    """GFT terminal isolation status — shows GFT terminal only."""
    try:
        from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load
        from utils.emergency_stop import is_emergency_stop_active
        from forex_engine.accounts.account_registry import status_summary

        estop   = is_emergency_stop_active()
        summary = status_summary()
        gft     = summary.get('GFT_5K', {})

        paper  = gft.get('paper', True)
        found  = gft.get('terminal_found', False)
        path   = gft.get('terminal_path') or 'not configured'
        magic  = gft.get('magic', 0)

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
            st   = gft_load()
            cap  = st.get('capital', 0.0)
            dpnl = st.get('daily_pnl', 0.0)
            paus = '⏸ PAUSED' if st.get('paused') else 'RUNNING'
        except Exception:
            cap, dpnl, paus = 0.0, 0.0, '?'

        estop_line = '\n🔴 <b>EMERGENCY STOP ACTIVE</b> — send /gft_resume to clear\n' if estop else ''

        msg = (
            f"{estop_line}"
            f"<b>CB6 QUANTUM — GFT TERMINAL STATUS</b>\n"
            f"<code>"
            f"{'─'*58}\n"
            f" {'ENGINE':<9} {'STATUS':<11} {'PATH / ALGO TOGGLE'}\n"
            f"{'─'*58}\n"
            f" {'GFT':<9} {status:<11} {path_disp}\n"
            f"          {'':11} {algo}\n"
            f"          {'':11} Capital: ${cap:.2f}  Daily: ${dpnl:+.2f}  {paus}\n"
            f"{'─'*58}"
            f"</code>"
        )
        _send(msg)
    except Exception as e:
        _send(f"Terminal error: {e}")


def _cmd_stop():
    try:
        from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load, _save as gft_save

        gft_state = gft_load()
        gft_state['paused'] = True
        gft_save(gft_state)

        _send(
            "<b>GFT ENGINE — PAUSED</b>\n\n"
            "No new GFT trades will open.\n"
            "Open positions continue to be monitored.\n"
            "Send /gft_resume to re-enable.\n\n"
            "<i>FTMO engine unaffected — use FTMO bot to control FTMO.</i>"
        )
    except Exception as e:
        _send(f"Stop error: {e}")


def _cmd_resume():
    try:
        from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load, _save as gft_save
        from utils.emergency_stop import clear_emergency_stop, is_emergency_stop_active

        flag_was_active = is_emergency_stop_active()
        clear_emergency_stop()

        gft_state = gft_load()
        gft_state['paused'] = False
        gft_save(gft_state)

        flag_note = "\n<i>Emergency stop flag cleared.</i>" if flag_was_active else ""
        _send(
            "<b>GFT ENGINE — RESUMED</b>\n\n"
            f"GFT engine  : RUNNING{flag_note}\n\n"
            "<i>FTMO engine unaffected — use FTMO bot to control FTMO.</i>"
        )
    except Exception as e:
        _send(f"Resume error: {e}")


def _cmd_help():
    _send(
        "<b>CB6 QUANTUM — GFT $5K 2-STEP GOAT</b>\n\n"
        "<b>MARKETS</b>\n"
        "Silver (XAGUSD) : min score 11 | risk 0.50% normal\n"
        "Oil    (USOIL)  : min score 11 | risk 0.50% normal\n"
        "Gold   (XAUUSD) : ⛔ PERMANENTLY DISABLED on GFT\n\n"
        "<b>ACCOUNT — GFT $5K 2-Step GOAT</b>\n"
        "Leverage       : 1:100\n"
        "Normal risk    : 0.50% = ~$25/trade\n"
        "Reduced risk   : 0.25% = ~$12.50 (after daily risk cut)\n"
        "Max risk (A+)  : 0.75% = ~$37.50\n"
        "Max daily loss : 4% = $200  (hard stop at $170 internal)\n"
        "Max total loss : 10% = $500 (static floor $4,500)\n"
        "Phase 1 target : 8% = $400  → Phase 2\n"
        "Phase 2 target : 6% = $300  → Funded $5K real account\n"
        "Min trading days: 3 per phase\n\n"
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
        "/gft_status   GFT engine health + heartbeat\n"
        "/gft_pnl      GFT P&amp;L + phase progress\n"
        "/gft_phase    Phase tracker with progress bars\n"
        "/gft_terminal GFT terminal status\n\n"
        "/gft_positions Open GFT trades with live price + uPnL\n"
        "/gft_journal   Last 5 GFT closed trades\n"
        "/gft_lots      Live GFT lot sizes + risk\n"
        "/gft_exit      List open — /gft_exit A to close\n\n"
        "/gft_stop      Pause GFT $5K engine\n"
        "/gft_resume    Resume GFT $5K engine\n\n"
        "<b>GFT $10K INSTANT</b>\n"
        "/g10k_status   $10K health + capital\n"
        "/g10k_pnl      $10K P&amp;L + DD bars\n"
        "/g10k_positions $10K open trades\n"
        "/g10k_stop     Pause $10K engine\n"
        "/g10k_resume   Resume $10K engine\n\n"
        "/ml_status     ML shadow accuracy + model status\n"
        "/ml_train      Force retrain\n\n"
        "/gft_help      This message"
    )


def _get_all_open_trades() -> list:
    """Return open GFT trades only."""
    from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load
    items = []
    for t in gft_load().get('open_trades', []):
        items.append({'trade': t, 'platform': 'GFT-2STEP', 'account': 'gft2step'})
    return items


def _cmd_exit(arg: str = ''):
    try:
        from forex_engine.forex_instruments import INSTRUMENTS
        from forex_engine.prop_firms.gft.gft_5k_2step import manual_exit_trade as gft_manual_exit

        items = _get_all_open_trades()

        if not items:
            _send("CB6 QUANTUM GFT — No open GFT trades to exit.")
            return

        arg = arg.strip().upper()
        if not arg:
            lines = ["<b>CB6 QUANTUM — GFT OPEN TRADES</b>\n\nSend /gft_exit A, /gft_exit B, etc. to close:\n"]
            letters = 'ABCDEFGHIJ'
            for i, item in enumerate(items):
                t       = item['trade']
                sym     = t['symbol']
                label   = INSTRUMENTS.get(sym, {}).get('label', sym)
                dirn    = 'LONG' if t['direction'] == 'BULLISH' else 'SHORT'
                entry   = t['entry_price']
                targets = ','.join(t.get('targets_hit', [])) or 'none'
                ltr     = letters[i] if i < len(letters) else str(i + 1)
                phase_l = f"  [{t.get('phase', '?')}]"

                price = None
                if _connector_ref:
                    try:
                        price = _connector_ref.get_price(sym)
                    except Exception:
                        pass
                upnl_str = ''
                if price:
                    cfg    = INSTRUMENTS.get(sym, {})
                    cs     = cfg.get('contract_size', 5000)
                    lots   = t['lots']
                    booked = len(t.get('targets_hit', []))
                    rem    = round(lots * (3 - booked) / 3, 2)
                    dist   = (price - entry) if t['direction'] == 'BULLISH' else (entry - price)
                    upnl   = round(rem * cs * dist, 2)
                    upnl_str = f"  |  uPnL: ${upnl:+.2f}"
                lines.append(
                    f"<b>{ltr}.</b> {label} {dirn} [GFT]{phase_l}  id:{t['id']}\n"
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
                f"CB6 QUANTUM GFT — trade reference '{arg}' not found.\n"
                f"Send /gft_exit (no args) to see open GFT trades."
            )
            return

        trade = matched['trade']
        sym   = trade['symbol']
        label = INSTRUMENTS.get(sym, {}).get('label', sym)
        dirn  = 'LONG' if trade['direction'] == 'BULLISH' else 'SHORT'

        exit_price = None
        if _connector_ref:
            try:
                exit_price = _connector_ref.get_price(sym)
            except Exception:
                pass
        if exit_price is None:
            exit_price = trade['entry_price']
            _send(
                f"⚠️ Could not fetch live price for {sym} — "
                f"using entry price {exit_price} (PnL will show $0)."
            )

        ev = gft_manual_exit(trade['id'], exit_price)
        if ev is None:
            _send(f"CB6 QUANTUM GFT — trade {trade['id']} not found in state (already closed?).")
            return

        pnl  = ev['pnl']
        sign = '+' if pnl >= 0 else ''
        icon = '✅' if pnl >= 0 else '🔴'
        _send(
            f"<b>CB6 QUANTUM — GFT MANUAL EXIT</b>\n\n"
            f"{icon} {label} {dirn}\n\n"
            f"Entry      : {trade['entry_price']}\n"
            f"Exit price : {exit_price}\n"
            f"PnL        : {sign}${pnl:.2f}\n"
            f"Targets hit: {','.join(trade.get('targets_hit', [])) or 'none'}\n"
            f"Trade ID   : {trade['id']}\n"
            f"Time       : {datetime.now().strftime('%H:%M:%S IST')}\n\n"
            f"GFT state updated."
        )
    except Exception as e:
        _send(f"Exit error: {e}")


def _cmd_journal():
    try:
        from forex_engine.prop_firms.gft.gft_phase_tracker import load_state as gft_load

        closed = gft_load().get('closed_trades', [])
        closed_sorted = sorted(closed, key=lambda x: x.get('entry_time', ''), reverse=True)

        if not closed_sorted:
            _send("CB6 QUANTUM GFT — No closed GFT trades yet.")
            return

        recent = closed_sorted[:5]
        lines  = ["<b>CB6 QUANTUM — GFT RECENT TRADES (last 5)</b>\n"]
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
            phase_l = f" [{t.get('phase', '?')}]"
            icon    = '✅' if pnl > 0 else '❌'
            lines.append(
                f"{icon} <b>[GFT] {sym} {dirn}</b>{phase_l}  ${sign}{pnl:.2f}\n"
                f"   {mss} · score {score} · targets {targets}\n"
                f"   Entry {entry} → Exit {exit_p}\n"
                f"   {entry_t}"
            )
        _send('\n\n'.join(lines))
    except Exception as e:
        _send(f"Journal error: {e}")


# ── GFT $10K commands ──────────────────────────────────────────────────────────

def _cmd_10k_status():
    try:
        from forex_engine.gft_10k.state import load_state as _load, HEARTBEAT_FILE, reset_daily_if_needed
        from forex_engine.gft_10k.config import GFT_10K_PROFILE as _P, live_execution_enabled

        state   = reset_daily_if_needed(_load())
        capital = state.get('capital', _P['account_size'])
        dpnl    = state.get('daily_pnl', 0.0)
        open_t  = state.get('open_trades', [])
        paused  = '⏸ PAUSED' if state.get('paused') else '▶ RUNNING'
        mode    = 'LIVE — GFT $10K MT5' if live_execution_enabled() else 'Paper (terminal pending)'

        base = os.path.dirname(os.path.dirname(__file__))
        def _hb(p):
            return f"{int(time.time() - os.path.getmtime(p))}s ago" if os.path.exists(p) else 'N/A'

        if _connector_10k_ref:
            try:
                eq  = _connector_10k_ref.get_equity()
                bal = _connector_10k_ref.get_balance()
                if bal and bal > 0:
                    capital = bal
                    dpnl    = round(eq - bal, 2)
            except Exception:
                pass

        total_pnl   = round(capital - _P['account_size'], 2)
        daily_lim   = _P['daily_dd_limit']
        daily_used  = abs(dpnl) if dpnl < 0 else 0.0
        daily_pct   = round(daily_used / daily_lim * 100, 1) if daily_lim else 0

        open_line = ''
        for t in open_t:
            sym  = t.get('symbol', '?')
            dirn = 'L' if t.get('direction') == 'BULLISH' else 'S'
            lots = t.get('lots', 0)
            open_line += f"\n  • {sym} {dirn} {lots}L id:{t.get('id', '?')}"

        _send(
            f"<b>CB6 QUANTUM — GFT $10K STATUS</b>\n\n"
            f"Mode       : {mode}\n"
            f"Engine     : {paused}  hb:{_hb(HEARTBEAT_FILE)}\n\n"
            f"<b>ACCOUNT</b>\n"
            f"Balance    : ${capital:,.2f}  (start ${_P['account_size']:,.0f})\n"
            f"Total PnL  : ${total_pnl:+.2f}\n"
            f"Daily PnL  : ${dpnl:+.2f}\n"
            f"Daily DD   : ${daily_used:.2f} / ${daily_lim:.0f}  ({daily_pct}%)\n\n"
            f"<b>OPEN TRADES ({len(open_t)})</b>"
            f"{open_line if open_t else chr(10) + '  None'}\n\n"
            f"Symbols    : XAGUSD | USOIL  (XAUUSD ⛔ DISABLED)\n"
            f"Risk/trade : {_P['risk_per_trade_pct']}% = ${_P['account_size'] * _P['risk_per_trade_pct'] / 100:.0f}  max lot {_P['max_lot']}"
        )
    except Exception as e:
        _send(f"10K status error: {e}")


def _cmd_10k_pnl():
    try:
        from forex_engine.gft_10k.state import load_state as _load, reset_daily_if_needed
        from forex_engine.gft_10k.config import GFT_10K_PROFILE as _P, live_execution_enabled
        from forex_engine.gft_10k.risk import daily_drawdown, max_drawdown

        state   = reset_daily_if_needed(_load())
        capital = state.get('capital', _P['account_size'])

        if _connector_10k_ref:
            try:
                bal = _connector_10k_ref.get_balance()
                if bal and bal > 0:
                    capital = bal
            except Exception:
                pass

        total_pnl   = round(capital - _P['account_size'], 2)
        dd_daily    = daily_drawdown(state)
        dd_total    = max_drawdown(state)
        closed      = state.get('closed_trades', [])
        wins        = sum(1 for t in closed if t.get('pnl_usd', 0) > 0)
        losses      = len(closed) - wins
        wr          = round(wins / len(closed) * 100, 1) if closed else 0.0
        mode        = 'LIVE MT5' if live_execution_enabled() else 'Paper'

        def _bar(used, limit):
            pct    = min(used / limit * 100, 100) if limit > 0 else 0
            filled = int(pct / 10)
            return f"{'█'*filled}{'░'*(10-filled)} {pct:.0f}%"

        _send(
            f"<b>CB6 QUANTUM — GFT $10K P&amp;L</b>  [{mode}]\n\n"
            f"Balance    : ${capital:,.2f}\n"
            f"Total PnL  : ${total_pnl:+.2f}\n\n"
            f"<b>DAILY DD (limit ${_P['daily_dd_limit']:.0f})</b>\n"
            f"Used today : ${dd_daily:.2f}\n"
            f"{_bar(dd_daily, _P['daily_dd_limit'])}\n\n"
            f"<b>TOTAL DD (limit ${_P['max_dd_limit']:.0f})</b>\n"
            f"Used total : ${dd_total:.2f}\n"
            f"{_bar(dd_total, _P['max_dd_limit'])}\n\n"
            f"<b>TRADES</b>\n"
            f"Closed     : {len(closed)}  ({wins}W / {losses}L  {wr}% WR)\n"
            f"Open       : {len(state.get('open_trades', []))}"
        )
    except Exception as e:
        _send(f"10K PnL error: {e}")


def _cmd_10k_positions():
    try:
        from forex_engine.gft_10k.state import load_state as _load
        from forex_engine.forex_instruments import INSTRUMENTS

        trades = _load().get('open_trades', [])
        if not trades:
            _send("GFT $10K — No open positions.")
            return

        for t in trades:
            sym     = t['symbol']
            cfg     = INSTRUMENTS.get(sym, {})
            is_long = t['direction'] == 'BULLISH'
            entry   = t['entry_price']
            lots    = t['lots']
            sl      = t.get('stop_loss', t.get('current_sl', 0))
            tp      = t.get('target', 0)
            contract = cfg.get('contract_size', 5000)

            price = None
            if _connector_10k_ref:
                try:
                    price = _connector_10k_ref.get_price(sym)
                except Exception:
                    pass

            if price:
                upnl    = round(lots * contract * (price - entry) * (1 if is_long else -1), 2)
                price_s = f"{price:.5f}"
            else:
                upnl    = 0.0
                price_s = 'N/A'

            dir_s = 'LONG' if is_long else 'SHORT'
            ghost = t.get('ticket', 0) == 0
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
        _send(f"10K positions error: {e}")


def _cmd_10k_stop():
    try:
        from forex_engine.gft_10k.state import load_state as _load, save_state as _save
        state = _load()
        state['paused'] = True
        _save(state)
        _send(
            "<b>GFT $10K ENGINE — PAUSED</b>\n\n"
            "No new $10K trades will open.\n"
            "Send /g10k_resume to re-enable."
        )
    except Exception as e:
        _send(f"10K stop error: {e}")


def _cmd_10k_resume():
    try:
        from forex_engine.gft_10k.state import load_state as _load, save_state as _save
        state = _load()
        state['paused'] = False
        _save(state)
        _send(
            "<b>GFT $10K ENGINE — RESUMED</b>\n\n"
            "GFT $10K engine : RUNNING"
        )
    except Exception as e:
        _send(f"10K resume error: {e}")


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
    '/start'         : _cmd_start,
    '/gft_status'    : _cmd_status,
    '/gft_pnl'       : _cmd_pnl,
    '/gft_phase'     : _cmd_phase,
    '/gft_positions' : _cmd_positions,
    '/gft_lots'      : _cmd_lots,
    '/gft_journal'   : _cmd_journal,
    '/gft_stop'      : _cmd_stop,
    '/gft_resume'    : _cmd_resume,
    '/gft_terminal'  : _cmd_terminal,
    '/gft_help'      : _cmd_help,
    '/gft_exit'      : _cmd_exit,
    # GFT $10K Instant
    '/g10k_status'   : _cmd_10k_status,
    '/g10k_pnl'      : _cmd_10k_pnl,
    '/g10k_positions': _cmd_10k_positions,
    '/g10k_stop'     : _cmd_10k_stop,
    '/g10k_resume'   : _cmd_10k_resume,
    # ML (shared)
    '/ml_status'     : _cmd_ml_status,
    '/ml_train'      : _cmd_ml_train,
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
                logger.warning(f"GFT bot rejected command from unknown chat_id={chat_id}")
            continue

        if text.startswith('/') and '@' in text.split()[0]:
            first, *rest = text.split()
            text = ' '.join([first.split('@')[0]] + rest)

        logger.info(f"GFT bot command: {text}")

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
            if cmd == '/gft_exit':
                threading.Thread(target=fn, args=(arg,), daemon=True).start()
            else:
                threading.Thread(target=fn, daemon=True).start()
        elif text.startswith('/'):
            _send(f"Unknown command: {cmd}\nUse /gft_help to see GFT commands.")


# ── Lock file — prevents duplicate GFT bot listeners ───────────────────────────

_BOT_LOCK_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'data', 'gft_bot.lock'
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
                    f"GFT bot: another listener already running (PID {old_pid}) — "
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
        logger.warning("GFT bot: listener already running — duplicate start ignored")
        return

    if not _acquire_bot_lock():
        return

    _listener_running = True
    # Drain stale pending updates to avoid replaying old commands on restart
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{GFT_TOKEN}/getUpdates",
            params={'offset': -1, 'timeout': 1},
            timeout=(3, 5),
        )
        result = r.json().get('result', []) if r.status_code == 200 else []
        if result:
            _last_update_id = result[-1]['update_id']
            logger.info(f"GFT bot: drained {len(result)} pending update(s) (last id={_last_update_id})")
    except Exception:
        pass

    logger.info("GFT Telegram bot listener started")
    try:
        while True:
            try:
                updates = _get_updates()
                if updates:
                    _process_updates(updates)
                time.sleep(3)
            except Exception as e:
                logger.error(f"GFT bot listener error: {e}")
                time.sleep(10)
    finally:
        _release_bot_lock()


def send_alert(text: str) -> bool:
    """Send an alert to the GFT Telegram chat. Called by gft_5k_2step."""
    return _send(text)
