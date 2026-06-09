# communications/telegram_bot.py
#
# NSE bot Telegram send helper.
# Thin wrapper around telegram_helpers.send_message that binds
# the NSE bot token + admin chat_id from environment variables.
# Used by main.py for routing alerts and NSE trade notifications.

import os
from communications.telegram_helpers import send_message as _send


def send_message(text: str, parse_mode: str = 'HTML') -> bool:
    token   = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('CB6_ADMIN_USER_ID', '').strip()
    if not token or not chat_id:
        return False
    return _send(token, chat_id, text, parse_mode=parse_mode)
