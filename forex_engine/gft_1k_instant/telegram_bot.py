import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from communications.telegram_helpers import (
    get_updates as _telegram_get_updates,
    mask_token,
    send_message,
)
from forex_engine.gft_1k_instant.config import GFT_1K_INSTANT_PROFILE
from forex_engine.gft_1k_instant.risk import daily_drawdown, max_drawdown, risk_mode
from forex_engine.gft_1k_instant.state import (
    HEARTBEAT_FILE,
    TELEGRAM_AUDIT_FILE,
    TELEGRAM_COMMANDS_FILE,
    TELEGRAM_ERRORS_LOG,
    ensure_state_dir,
    load_lock_state,
    load_state,
    save_lock_state,
    save_state,
)
from utils.logger import logger


_last_update_id = 0
_listener_running = False
_connector_ref = None


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    return _bool_env("GFT_1K_TELEGRAM_ENABLED", "false")


def commands_enabled() -> bool:
    return _bool_env("GFT_1K_TELEGRAM_COMMANDS_ENABLED", "true")


def _token() -> str:
    return os.getenv("GFT_1K_TELEGRAM_BOT_TOKEN", "").strip()


def _chat_id() -> str:
    return os.getenv("GFT_1K_TELEGRAM_CHAT_ID", "").strip()


def _admin_ids() -> set[str]:
    raw = os.getenv("GFT_1K_TELEGRAM_ADMIN_IDS", "").strip()
    return {item.strip() for item in raw.split(",") if item.strip()}


def set_connector(connector) -> None:
    global _connector_ref
    _connector_ref = connector


def _jsonl(path: str, payload: dict) -> None:
    ensure_state_dir()
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _error(message: str) -> None:
    ensure_state_dir()
    with open(TELEGRAM_ERRORS_LOG, "a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")


def _audit(event: str, **payload) -> None:
    _jsonl(TELEGRAM_AUDIT_FILE, {"event": event, **payload})


def _command_log(command: str, chat_id: str, user_id: str, allowed: bool) -> None:
    _jsonl(
        TELEGRAM_COMMANDS_FILE,
        {
            "command": command,
            "chat_id": chat_id,
            "user_id": user_id,
            "allowed": allowed,
        },
    )


def _authorized(user_id: str) -> bool:
    admins = _admin_ids()
    return bool(user_id) and user_id in admins


def _safe_send(text: str, parse_mode: str = "HTML") -> bool:
    token = _token()
    chat_id = _chat_id()
    if not is_enabled():
        return False
    if not token or not chat_id:
        _error("telegram send skipped: missing token or chat id")
        return False
    return send_message(token, chat_id, text, parse_mode, logger)


def send_alert(alert_type: str, message: str, payload: Optional[dict] = None) -> bool:
    clean_payload = payload or {}
    _audit("alert", alert_type=alert_type, payload=clean_payload)
    title = alert_type.replace("_", " ").title()
    return _safe_send(f"<b>GFT 1K Instant - {title}</b>\n{message}")


def startup_alert() -> bool:
    return send_alert(
        "startup",
        "GFT 1K Instant worker started with isolated Telegram routing.",
    )


def _balance_tuple() -> tuple[float, float, float, bool]:
    if _connector_ref:
        try:
            balance = float(_connector_ref.get_balance() or 0.0)
            equity = float(_connector_ref.get_equity() or 0.0)
            if balance > 0 or equity > 0:
                return balance, equity, round(equity - balance, 2), True
        except Exception as exc:
            _error(f"equity fetch failed: {exc}")
    state = load_state()
    capital = float(state.get("capital", GFT_1K_INSTANT_PROFILE["account_size"]))
    return capital, capital, 0.0, False


def _cmd_status() -> str:
    state = load_state()
    lock = load_lock_state()
    heartbeat = "N/A"
    if os.path.exists(HEARTBEAT_FILE):
        heartbeat = f"{int(time.time() - os.path.getmtime(HEARTBEAT_FILE))}s ago"
    mode, reason = risk_mode(state)
    return (
        "<b>GFT 1K Instant Status</b>\n"
        f"Heartbeat: {heartbeat}\n"
        f"Paused: {state.get('paused', False)}\n"
        f"Locked: {lock.get('locked', False)}\n"
        f"Dry Run: {lock.get('dry_run', True)}\n"
        f"Risk Mode: {mode} - {reason}"
    )


def _cmd_equity() -> str:
    balance, equity, open_pnl, live = _balance_tuple()
    return (
        "<b>GFT 1K Instant Equity</b>\n"
        f"Balance: ${balance:,.2f}\n"
        f"Equity: ${equity:,.2f}\n"
        f"Open PnL: ${open_pnl:+.2f}\n"
        f"Source: {'MT5' if live else 'state'}"
    )


def _cmd_positions() -> str:
    trades = load_state().get("open_trades", [])
    if not trades:
        return "No open GFT 1K Instant positions."
    lines = ["<b>GFT 1K Instant Positions</b>"]
    for trade in trades[-10:]:
        lines.append(
            f"{trade.get('id')} {trade.get('symbol')} {trade.get('direction')} "
            f"{trade.get('lots')}L risk=${trade.get('risk_usd', 0):.2f}"
        )
    return "\n".join(lines)


def _cmd_risk() -> str:
    state = load_state()
    daily_loss = daily_drawdown(state)
    total_loss = max_drawdown(state)
    mode, reason = risk_mode(state)
    return (
        "<b>GFT 1K Instant Risk</b>\n"
        f"Daily DD: ${daily_loss:.2f} / ${GFT_1K_INSTANT_PROFILE['daily_dd_limit']:.2f}\n"
        f"Max DD: ${total_loss:.2f} / ${GFT_1K_INSTANT_PROFILE['max_dd_limit']:.2f}\n"
        f"Max Risk/Trade: ${GFT_1K_INSTANT_PROFILE['max_risk_usd']:.2f}\n"
        f"Max Lot: {GFT_1K_INSTANT_PROFILE['max_lot']:.2f}\n"
        f"Mode: {mode} - {reason}"
    )


def _set_lock(user_id: str, locked: bool, reason: str) -> str:
    state = load_lock_state()
    state.update(
        {
            "locked": locked,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": user_id,
            "reason": reason,
        }
    )
    save_lock_state(state)
    _audit("unlock" if not locked else "lock", user_id=user_id, reason=reason)
    return f"GFT 1K Instant {'unlocked' if not locked else 'locked'}."


def _set_dry_run(user_id: str, enabled: bool) -> str:
    state = load_lock_state()
    state.update(
        {
            "dry_run": enabled,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": user_id,
        }
    )
    save_lock_state(state)
    _audit("dryrun_on" if enabled else "dryrun_off", user_id=user_id)
    return f"GFT 1K Instant dry run {'ON' if enabled else 'OFF'}."


def _set_paused(user_id: str, paused: bool) -> str:
    state = load_state()
    state["paused"] = paused
    save_state(state)
    _audit("pause" if paused else "resume", user_id=user_id)
    return f"GFT 1K Instant {'paused' if paused else 'resumed'}."


def _cmd_last_trade() -> str:
    state = load_state()
    trades = state.get("closed_trades", []) or state.get("open_trades", [])
    if not trades:
        return "No GFT 1K Instant trades found."
    trade = trades[-1]
    return (
        "<b>Last GFT 1K Instant Trade</b>\n"
        f"ID: {trade.get('id')}\n"
        f"Symbol: {trade.get('symbol')}\n"
        f"Direction: {trade.get('direction')}\n"
        f"Status: {trade.get('status')}\n"
        f"PnL: ${float(trade.get('pnl_usd', 0) or 0):+.2f}"
    )


def _cmd_today() -> str:
    state = load_state()
    return (
        "<b>GFT 1K Instant Today</b>\n"
        f"Trades: {state.get('daily_trades', 0)}\n"
        f"Daily PnL: ${float(state.get('daily_pnl', 0) or 0):+.2f}\n"
        f"Open Trades: {len(state.get('open_trades', []))}"
    )


def _cmd_help() -> str:
    return (
        "<b>GFT 1K Instant Commands</b>\n"
        "/start /status /equity /positions /risk\n"
        "/lock /unlock /dryrun_on /dryrun_off\n"
        "/pause /resume /last_trade /today /help"
    )


def dispatch_command(command: str, user_id: str) -> str:
    command = (command or "").split()[0].lower()
    if command == "/start":
        return _cmd_help()
    if command == "/status":
        return _cmd_status()
    if command == "/equity":
        return _cmd_equity()
    if command == "/positions":
        return _cmd_positions()
    if command == "/risk":
        return _cmd_risk()
    if command == "/lock":
        return _set_lock(user_id, True, "telegram command")
    if command == "/unlock":
        return _set_lock(user_id, False, "telegram admin unlock")
    if command == "/dryrun_on":
        return _set_dry_run(user_id, True)
    if command == "/dryrun_off":
        return _set_dry_run(user_id, False)
    if command == "/pause":
        return _set_paused(user_id, True)
    if command == "/resume":
        return _set_paused(user_id, False)
    if command == "/last_trade":
        return _cmd_last_trade()
    if command == "/today":
        return _cmd_today()
    if command == "/help":
        return _cmd_help()
    return "Unknown command. Use /help."


def handle_update(update: dict) -> Optional[str]:
    message = update.get("message") or {}
    text = str(message.get("text") or "").strip()
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    chat_id = str(chat.get("id") or "")
    user_id = str(from_user.get("id") or "")

    if not text.startswith("/"):
        return None

    allowed = _authorized(user_id)
    _command_log(text.split()[0].lower(), chat_id, user_id, allowed)
    if not allowed:
        _audit("unknown_user_ignored", chat_id=chat_id, user_id=user_id)
        logger.warning(f"GFT 1K Telegram ignored unauthorized user_id={user_id}")
        return None

    if not commands_enabled():
        return "GFT 1K Telegram commands are disabled."

    response = dispatch_command(text, user_id)
    _safe_send(response)
    return response


def _get_updates() -> list:
    global _last_update_id
    token = _token()
    if not token:
        return []
    updates, _last_update_id = _telegram_get_updates(token, _last_update_id, logger)
    return updates


def start_listening() -> None:
    global _listener_running
    if not is_enabled():
        logger.info("GFT 1K Telegram disabled")
        return
    if not _token() or not _chat_id() or not _admin_ids():
        _error("telegram listener not started: missing token, chat id, or admin ids")
        return
    if _listener_running:
        return
    _listener_running = True
    logger.info(f"GFT 1K Telegram bot armed (token {mask_token(_token())})")
    while _listener_running:
        try:
            for update in _get_updates():
                handle_update(update)
        except Exception as exc:
            _error(f"listener error: {exc}")
        time.sleep(1)


def start_background_listener() -> bool:
    if not is_enabled():
        logger.info("GFT 1K Telegram disabled")
        return False
    thread = threading.Thread(
        target=start_listening,
        daemon=True,
        name="GFT1KTelegram",
    )
    thread.start()
    return True
