# utils/telegram_alerts.py — CB6 Bot Telegram Alerts
import os
import sys
import requests
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils.logger import logger

def send_message(message, parse_mode=None):
    """
    Send message to Telegram with retry.
    parse_mode: None (plain text) | 'HTML' | 'Markdown'
    When parse_mode is None, no parse_mode key is sent — safe for messages
    with literal '<', '>', '*' characters (e.g. "/ask <question>").
    """
    import time
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id" : TELEGRAM_CHAT_ID,
        "text"    : message[:4096],
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code == 200:
                return True
            if response.status_code == 429:
                time.sleep(2)
                continue
            # If HTML parse failed, retry as plain text
            if response.status_code == 400 and parse_mode:
                logger.warning(f"Telegram parse_mode={parse_mode} failed, retrying plain text")
                payload.pop("parse_mode", None)
                continue
            logger.error(f"Telegram error: {response.status_code}")
            return False
        except Exception as e:
            logger.error(f"Telegram attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(1)
    return False


def send_photo(photo_bytes, caption=""):
    """Send PNG image to Telegram. Used for chart screenshots and replays."""
    if not photo_bytes:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        files = {'photo': ('chart.png', photo_bytes, 'image/png')}
        data  = {'chat_id': TELEGRAM_CHAT_ID, 'caption': caption[:1024]}
        resp  = requests.post(url, files=files, data=data, timeout=15)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram photo error: {e}")
        return False


def send_startup_alert():
    """Send bot started alert"""
    import datetime, pytz
    now = datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%H:%M IST')
    message = (
        "CB6 QUANTUM — STARTED\n\n"
        f"Time     : {now}\n"
        "Mode     : Local · NSE Index Options\n"
        "Universe : NIFTY | BANKNIFTY | FINNIFTY | MIDCPNIFTY\n"
        "Strategy : ICT Silver Bullet (DOL → MSS → FVG)\n"
        "Windows  : 10:00–11:00 | 13:30–14:30 IST\n\n"
        "/sb    Trigger scan now\n"
        "/start Show all commands"
    )
    send_message(message)


def send_setup_alert(setup):
    """Send trade setup alert"""
    sig    = setup['entry_signal']
    symbol = setup['symbol'].replace("NSE:", "").replace("-EQ", "")
    tf     = setup.get('timeframe', '15min')

    message = f"""
CB6 QUANTUM - SETUP FOUND

Symbol    : {symbol}
Timeframe : {tf}

B1 Price  : {sig['b1_price']}
B2 Price  : {sig['b2_price']}
Neckline  : {sig['neck_price']}

ENTRY     : {sig['entry']}
STOP LOSS : {sig['stop_loss']}
TARGET 1  : {sig['target1']}
TARGET 2  : {sig['target2']}
TARGET 3  : {sig['target3']}

RISK      : {sig['risk']}
RR RATIO  : 1:{sig['rr_ratio']}

Mode: Paper Trade - No real order placed
    """
    send_message(message)


def send_scan_complete_alert(setups_15, setups_60):
    """Send scan summary alert"""
    total = len(setups_15) + len(setups_60)

    if total == 0:
        message = """
CB6 QUANTUM - SCAN COMPLETE
No setups found right now.
Will scan again on next trigger.
        """
    else:
        message = f"""
CB6 QUANTUM - SCAN COMPLETE
Total Setups Found: {total}
15min setups: {len(setups_15)}
60min setups: {len(setups_60)}
Check above messages for details.
        """
    send_message(message)


def send_document(file_path, caption=""):
    """Send a file (any type) to Telegram. Used for Excel dashboard, CSVs, etc."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        filename = os.path.basename(file_path)
        with open(file_path, 'rb') as f:
            files = {'document': (filename, f, 'application/octet-stream')}
            data  = {'chat_id': TELEGRAM_CHAT_ID, 'caption': caption[:1024]}
            resp  = requests.post(url, files=files, data=data, timeout=30)
        if resp.status_code == 200:
            return True
        logger.error(f"Telegram document error: {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"Telegram document send error: {e}")
        return False


def send_test_alert():
    """Send test message to verify connection"""
    message = """
CB6 QUANTUM - TEST MESSAGE
Telegram is connected successfully!
Your bot is ready.
    """
    return send_message(message)