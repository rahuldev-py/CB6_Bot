# communications/telegram_helpers.py
#
# Shared pure helpers for CB6 Quantum Telegram bots (FTMO + GFT).
# Wave 4 refactor (2026-05-29) — extracted identical implementations from
# forex_bot.py and gft_bot.py.
#
# Rules:
#   - No module-level state.
#   - No trading logic, no broker calls, no ML imports.
#   - All functions receive token / chat_id / state dicts as parameters.
#   - Each bot keeps its own thin _send() / _get_updates() wrappers that
#     bind module-level token + chat_id, preserving backward-compatible APIs.

import time
import requests


def mask_token(token: str) -> str:
    """Return a masked representation — never log real token values."""
    if not token:
        return '(empty)'
    return token[:8] + '***'


def send_message(token: str, chat_id: str, text: str,
                 parse_mode: str = 'HTML', logger=None) -> bool:
    """Post a Telegram message. Falls back to plain text on HTML parse error."""
    try:
        payload = {'chat_id': chat_id, 'text': text[:4096]}
        if parse_mode:
            payload['parse_mode'] = parse_mode
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, timeout=10,
        )
        if r.status_code == 400 and parse_mode:
            payload.pop('parse_mode', None)
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload, timeout=10,
            )
        return r.status_code == 200
    except Exception as e:
        if logger:
            logger.error(f"Telegram send error: {e}")
        return False


def get_updates(token: str, last_update_id: int, logger=None) -> tuple:
    """
    Fetch pending updates from Telegram.
    Returns (updates_list, new_last_update_id).
    """
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={'offset': last_update_id + 1, 'timeout': 5},
            timeout=(5, 10),
        )
        if r.status_code == 200:
            updates = r.json().get('result', [])
            if updates:
                new_id = updates[-1]['update_id']
                return updates, new_id
            return [], last_update_id
        return [], last_update_id
    except requests.exceptions.Timeout:
        return [], last_update_id
    except Exception as e:
        if logger:
            logger.warning(f"Telegram get_updates error: {e}")
        return [], last_update_id


def is_authorized_chat(chat_id: str, authorized_ids: set) -> bool:
    """True if chat_id is in the authorized set."""
    return bool(chat_id) and chat_id in authorized_ids


def is_rate_limited(chat_id: str, command: str,
                    cache: dict, limit_secs: int) -> bool:
    """
    True if this (chat_id, command) pair was seen within limit_secs.
    Mutates cache in place — callers pass their module-level _last_command_at dict.
    """
    now = time.time()
    key = (chat_id, command)
    if now - cache.get(key, 0) < limit_secs:
        return True
    cache[key] = now
    return False


def check_confirmation(chat_id: str, text: str,
                       pending: dict, confirm_ttl: int,
                       send_fn) -> tuple:
    """
    Two-step confirmation protocol.
    - First call: registers the command as pending + sends the confirmation prompt.
    - Second call (text ends with ' confirm'): validates and clears the pending entry.
    Returns (confirmed: bool, base_command: str).
    Mutates pending dict in place.
    """
    now = time.time()
    normalized = text.strip()
    if normalized.lower().endswith(' confirm'):
        base = normalized[:-8].strip()
        key  = (chat_id, base)
        expires = pending.get(key, 0)
        if expires >= now:
            pending.pop(key, None)
            return True, base
        return False, base

    pending[(chat_id, normalized)] = now + confirm_ttl
    send_fn(
        "Confirmation required.\n"
        f"Send exactly: <code>{normalized} confirm</code>\n"
        f"Expires in {confirm_ttl}s."
    )
    return False, normalized
