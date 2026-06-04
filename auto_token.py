"""
auto_token.py — CB6 Quantum: Daily Fyers token refresh + NSE bot auto-launch.

Run this every morning at 8:45 AM IST (set up via setup_scheduler.ps1).

Flow:
  1. Token already fresh today → skip login, start main.py directly.
  2. Token stale → start local OAuth callback server on port 8085.
               → Open Fyers login in browser (local).
               → Send Telegram alert with direct login URL (for mobile).
               → Wait up to 3 minutes for OAuth callback.
               → Exchange auth code → save to .env.
  3. Launch main.py as a background process.
  4. Send Telegram confirmation once bot is live.

Usage:
  python auto_token.py              # normal (browser opens)
  python auto_token.py --headless   # server only, no browser (use Telegram link)
"""

import os
import sys
import time
import subprocess
import threading
import tempfile
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

import pytz

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import dotenv_values, load_dotenv

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
_ROOT        = os.path.dirname(os.path.abspath(__file__))
ENV_PATH     = os.path.join(_ROOT, '.env')
CALLBACK_PORT = 8085
REDIRECT_URI  = f"http://127.0.0.1:{CALLBACK_PORT}"
IST           = pytz.timezone('Asia/Kolkata')

# ── Telegram helper (uses TELEGRAM_BOT_TOKEN from .env) ───────────────────────
def _tg(msg: str):
    """Send a Telegram message to the configured chat."""
    try:
        import requests
        env   = dotenv_values(ENV_PATH)
        token = env.get('TELEGRAM_BOT_TOKEN', '') or os.getenv('TELEGRAM_BOT_TOKEN', '')
        chat  = env.get('TELEGRAM_CHAT_ID',   '') or os.getenv('TELEGRAM_CHAT_ID',   '')
        if not token or not chat:
            print(f"[TG] {msg}")
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[TG FAIL] {e}: {msg}")


# ── Token helpers ──────────────────────────────────────────────────────────────
def _read_token() -> str:
    try:
        return dotenv_values(ENV_PATH).get('ACCESS_TOKEN', '').strip("'\"")
    except Exception:
        return ''


def _is_token_fresh() -> bool:
    """True if the saved JWT was issued today in IST."""
    import base64, json as _json
    try:
        tok = _read_token()
        if not tok:
            return False
        jwt = tok.split(':', 1)[1] if ':' in tok else tok
        parts = jwt.split('.')
        if len(parts) < 2:
            return False
        payload_b64 = parts[1] + '=' * (-len(parts[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        iat = payload.get('iat')
        if not iat:
            return False
        from datetime import datetime as _dt, timezone as _tz
        iat_ist  = _dt.fromtimestamp(iat, tz=_tz.utc).astimezone(IST)
        today_ist = _dt.now(IST).strftime('%Y-%m-%d')
        return iat_ist.strftime('%Y-%m-%d') == today_ist
    except Exception as e:
        print(f"[TOKEN] freshness check error: {e}")
        return False


def _save_token(full_token: str):
    """Atomically write ACCESS_TOKEN=... to .env."""
    env_content = ''
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, 'r', encoding='utf-8') as f:
            env_content = f.read()
    lines = [l for l in env_content.splitlines() if not l.startswith('ACCESS_TOKEN=')]
    lines.append(f'ACCESS_TOKEN={full_token}')
    payload = '\n'.join(lines) + '\n'
    fd, tmp = tempfile.mkstemp(prefix='.env.', suffix='.tmp',
                                dir=os.path.dirname(os.path.abspath(ENV_PATH)))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ENV_PATH)
        print('[TOKEN] Saved to .env ✅')
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ── OAuth callback HTTP server ─────────────────────────────────────────────────
_auth_code   = None
_server_done = threading.Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        params = parse_qs(urlparse(self.path).query)
        if 'auth_code' in params:
            _auth_code = params['auth_code'][0]
            self._html('✅ Token Captured! You can close this tab. Bot is starting...', '#00B050')
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            _server_done.set()
        else:
            self._html('⏳ Waiting for Fyers login...', '#58a6ff')

    def _html(self, msg: str, color: str):
        body = (
            f'<html><body style="background:#0d1117;color:{color};'
            f'font-family:sans-serif;display:flex;align-items:center;'
            f'justify-content:center;height:100vh;font-size:24px;">'
            f'{msg}</body></html>'
        ).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass   # suppress access logs


# ── Token refresh flow ─────────────────────────────────────────────────────────
def refresh_token(headless: bool = False) -> bool:
    """
    Run the OAuth flow. Returns True if token was saved successfully.
    headless=True → no browser open, only Telegram link.
    """
    global _auth_code, _server_done
    _auth_code   = None
    _server_done = threading.Event()

    env = dotenv_values(ENV_PATH)
    client_id  = env.get('CLIENT_ID',  '') or os.getenv('CLIENT_ID',  '')
    secret_key = env.get('SECRET_KEY', '') or os.getenv('SECRET_KEY', '')

    if not client_id or not secret_key:
        print('[ERROR] CLIENT_ID / SECRET_KEY missing in .env')
        _tg('❌ CB6 QUANTUM: Cannot start — CLIENT_ID or SECRET_KEY missing in .env')
        return False

    try:
        from fyers_apiv3 import fyersModel
        session = fyersModel.SessionModel(
            client_id=client_id,
            secret_key=secret_key,
            redirect_uri=REDIRECT_URI,
            response_type='code',
            grant_type='authorization_code',
        )
        login_url = session.generate_authcode()
    except Exception as e:
        print(f'[ERROR] Fyers SessionModel failed: {e}')
        _tg(f'❌ CB6 QUANTUM: Fyers session error — {e}')
        return False

    # Start local callback server
    server = HTTPServer(('127.0.0.1', CALLBACK_PORT), _CallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f'[TOKEN] Callback server running on port {CALLBACK_PORT}')

    # Send Telegram with login link (works on mobile even if PC browser not available)
    now_ist = datetime.now(IST).strftime('%H:%M IST')
    _tg(
        f'🔑 <b>CB6 QUANTUM — Fyers Login Required</b>\n\n'
        f'Time: {now_ist}\n\n'
        f'<b>Open this link to login and auto-start the NSE bot:</b>\n'
        f'{login_url}\n\n'
        f'<i>After login, token saves automatically and bot starts within 30s.</i>'
    )
    print(f'[TOKEN] Telegram alert sent with login URL')

    # Open browser locally (skip if headless)
    if not headless:
        try:
            import webbrowser
            webbrowser.open(login_url)
            print('[TOKEN] Browser opened — complete Fyers login')
        except Exception:
            print('[TOKEN] Could not open browser — use Telegram link')
    else:
        print('[TOKEN] Headless mode — use Telegram link to login')

    print('[TOKEN] Waiting for OAuth callback... (timeout: 3 min)')
    _server_done.wait(timeout=180)

    if not _auth_code:
        print('[ERROR] Timed out waiting for auth code (180s)')
        _tg('⏰ <b>CB6 QUANTUM: Login timeout (3 min)</b>\n\nPlease run:\n<code>python auto_token.py</code>')
        return False

    # Exchange auth code → access token
    try:
        session.set_token(_auth_code)
        response = session.generate_token()
    except Exception as e:
        print(f'[ERROR] Token exchange failed: {e}')
        _tg(f'❌ CB6 QUANTUM: Token exchange failed — {e}')
        return False

    if response.get('code') != 200:
        print(f'[ERROR] Token generation failed: {response}')
        _tg(f'❌ CB6 QUANTUM: Token generation failed — {response}')
        return False

    access_token = response['access_token']
    full_token   = f"{client_id}:{access_token}"
    _save_token(full_token)
    return True


# ── Launch main.py ─────────────────────────────────────────────────────────────
def launch_nse_bot() -> subprocess.Popen:
    """Start main.py in a new process. Returns the Popen handle."""
    main_py = os.path.join(_ROOT, 'main.py')
    log_dir = os.path.join(_ROOT, 'logs')
    os.makedirs(log_dir, exist_ok=True)

    log_file = open(os.path.join(log_dir, 'nse_bot.log'), 'a', encoding='utf-8')
    proc = subprocess.Popen(
        [sys.executable, main_py],
        cwd=_ROOT,
        stdout=log_file,
        stderr=log_file,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
    )
    print(f'[BOT] main.py launched — PID {proc.pid}')
    return proc


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='CB6 Quantum auto token + NSE bot launcher')
    parser.add_argument('--headless', action='store_true',
                        help='No browser open — send Telegram link only')
    parser.add_argument('--token-only', action='store_true',
                        help='Only refresh token, do not launch bot')
    args = parser.parse_args()

    now = datetime.now(IST).strftime('%d %b %Y %H:%M IST')
    print(f'\n{"="*52}')
    print(f'  CB6 QUANTUM — Auto Token + NSE Bot Launcher')
    print(f'  {now}')
    print(f'{"="*52}\n')

    # ── Step 1: Check token freshness ─────────────────────────────────────────
    if _is_token_fresh():
        print('[TOKEN] Token is fresh — no login needed ✅')
        _tg(f'✅ <b>CB6 QUANTUM</b>: Token fresh. Starting NSE bot...')
    else:
        print('[TOKEN] Token is stale — starting OAuth refresh...')
        ok = refresh_token(headless=args.headless)
        if not ok:
            print('[FATAL] Could not get fresh token — exiting')
            sys.exit(1)
        # Re-verify
        time.sleep(2)
        if not _is_token_fresh():
            print('[FATAL] Token saved but still not fresh — check CLIENT_ID format')
            _tg('❌ CB6 QUANTUM: Token saved but freshness check failed. Check .env CLIENT_ID.')
            sys.exit(1)
        print('[TOKEN] Token verified fresh ✅')

    if args.token_only:
        print('[TOKEN] --token-only mode: done. Not launching bot.')
        return

    # ── Step 2: Launch main.py ────────────────────────────────────────────────
    proc = launch_nse_bot()
    time.sleep(5)

    if proc.poll() is not None:
        print(f'[ERROR] main.py exited immediately (code {proc.returncode})')
        _tg(
            f'❌ <b>CB6 QUANTUM NSE bot crashed at startup!</b>\n'
            f'Exit code: {proc.returncode}\n'
            f'Check logs/nse_bot.log for details.'
        )
        sys.exit(1)

    print(f'[BOT] NSE bot running — PID {proc.pid} ✅')
    _tg(
        f'🚀 <b>CB6 QUANTUM NSE Bot Started</b>\n\n'
        f'Time    : {now}\n'
        f'PID     : {proc.pid}\n'
        f'Token   : Fresh ✅\n'
        f'Scanner : Every 3 min\n'
        f'SB Windows: 10:00 &amp; 13:30 IST\n\n'
        f'<i>Use /nse_status to check engine health</i>'
    )

    # ── Step 3: Write PID file so watchdog can find this process ──────────────
    pid_file = os.path.join(_ROOT, 'data', 'nse_bot.pid')
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    with open(pid_file, 'w') as f:
        f.write(str(proc.pid))

    print(f'[BOT] PID written to data/nse_bot.pid')
    print(f'[BOT] Watchdog: run `python watchdog.py` to monitor & auto-restart')
    print(f'\nDone. NSE bot is live.\n')


if __name__ == '__main__':
    main()
