"""
watchdog.py — CB6 Quantum NSE bot supervisor.

Monitors main.py and auto-restarts it if it:
  - crashes unexpectedly
  - hangs (no heartbeat file update for > 5 min)
  - exits with non-zero code

Usage:
  python watchdog.py              # starts auto_token.py then monitors
  python watchdog.py --attach     # attach to already-running main.py via PID file

Sends Telegram alerts on:
  - Crash detected
  - Restart attempt
  - Token needs refresh (re-runs auto_token.py)
  - Market closed (pauses monitoring)
"""

import os
import sys
import time
import subprocess
import signal
import argparse
from datetime import datetime, timedelta

import pytz

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import dotenv_values

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

_ROOT    = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(_ROOT, '.env')
IST      = pytz.timezone('Asia/Kolkata')

# ── Config ─────────────────────────────────────────────────────────────────────
HEARTBEAT_FILE    = os.path.join(_ROOT, 'data', 'nse_heartbeat.txt')
PID_FILE          = os.path.join(_ROOT, 'data', 'nse_bot.pid')
LOG_DIR           = os.path.join(_ROOT, 'logs')
HEARTBEAT_TIMEOUT = 360      # seconds — restart if no heartbeat update for 6 min
POLL_INTERVAL     = 60       # seconds between watchdog checks
MAX_RESTARTS_DAY  = 5        # halt watchdog if bot restarts > 5x in one day
MARKET_OPEN_IST   = (9, 10)  # (hour, min) — don't restart before this
MARKET_CLOSE_IST  = (15, 35) # (hour, min) — stop monitoring after this


# ── Telegram ───────────────────────────────────────────────────────────────────
def _tg(msg: str):
    try:
        import requests
        env   = dotenv_values(ENV_PATH)
        token = env.get('TELEGRAM_BOT_TOKEN', '') or os.getenv('TELEGRAM_BOT_TOKEN', '')
        chat  = env.get('TELEGRAM_CHAT_ID',   '') or os.getenv('TELEGRAM_CHAT_ID',   '')
        if token and chat:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
                timeout=10,
            )
    except Exception:
        pass
    print(f'[WD] {msg.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","")}')


# ── Market hours check ─────────────────────────────────────────────────────────
def _is_market_hours() -> bool:
    now   = datetime.now(IST)
    if now.weekday() >= 5:   # Saturday / Sunday
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN_IST <= t <= MARKET_CLOSE_IST


def _minutes_to_open() -> int:
    now = datetime.now(IST)
    open_today = now.replace(hour=MARKET_OPEN_IST[0], minute=MARKET_OPEN_IST[1],
                              second=0, microsecond=0)
    delta = (open_today - now).total_seconds()
    return max(0, int(delta // 60))


# ── Process utilities ──────────────────────────────────────────────────────────
def _read_pid() -> int:
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return -1


def _is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        # On Windows, os.kill with signal 0 checks existence
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except OSError:
            # Windows: os.kill signal 0 not supported; assume not running
            return False


def _heartbeat_age() -> float:
    """Seconds since heartbeat file was last updated. Returns inf if missing."""
    try:
        mtime = os.path.getmtime(HEARTBEAT_FILE)
        return time.time() - mtime
    except FileNotFoundError:
        return float('inf')


def _kill_pid(pid: int):
    """Gracefully terminate a process."""
    try:
        import psutil
        p = psutil.Process(pid)
        p.terminate()
        p.wait(timeout=10)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


# ── Launch via auto_token.py ───────────────────────────────────────────────────
def _launch_bot(headless: bool = True) -> int:
    """
    Run auto_token.py which handles token refresh and launches main.py.
    Returns PID of main.py (read from PID file after launch).
    """
    auto_token = os.path.join(_ROOT, 'auto_token.py')
    os.makedirs(LOG_DIR, exist_ok=True)

    flags = ['--headless'] if headless else []
    log_path = os.path.join(LOG_DIR, 'auto_token.log')

    with open(log_path, 'a', encoding='utf-8') as log_f:
        proc = subprocess.Popen(
            [sys.executable, auto_token] + flags,
            cwd=_ROOT,
            stdout=log_f,
            stderr=log_f,
        )
        proc.wait(timeout=300)  # wait up to 5 min for token + launch

    # Read PID from file written by auto_token.py
    time.sleep(3)
    return _read_pid()


# ── Watchdog loop ──────────────────────────────────────────────────────────────
def run_watchdog(attach: bool = False):
    restarts_today   = 0
    last_restart_day = datetime.now(IST).date()
    current_pid      = -1

    _tg(
        f'🐕 <b>CB6 QUANTUM Watchdog Started</b>\n'
        f'Time: {datetime.now(IST).strftime("%H:%M IST")}\n'
        f'Heartbeat timeout: {HEARTBEAT_TIMEOUT}s\n'
        f'Poll interval: {POLL_INTERVAL}s'
    )

    # ── Attach mode: read existing PID ────────────────────────────────────────
    if attach:
        current_pid = _read_pid()
        if _is_running(current_pid):
            print(f'[WD] Attached to existing process PID={current_pid}')
        else:
            print('[WD] No running process found — launching bot...')
            attach = False

    # ── Initial launch (if not attaching) ─────────────────────────────────────
    if not attach:
        if not _is_market_hours():
            mins = _minutes_to_open()
            print(f'[WD] Outside market hours. Market opens in ~{mins} min. Waiting...')
            _tg(f'🕒 Watchdog: market opens in {mins} min. Will launch bot then.')
            # Sleep until close to market open
            while not _is_market_hours():
                time.sleep(60)

        print('[WD] Launching NSE bot via auto_token.py...')
        current_pid = _launch_bot(headless=True)
        if not _is_running(current_pid):
            _tg('❌ <b>Watchdog: Initial launch failed!</b>\nCheck logs/auto_token.log')
            return

    # ── Main monitoring loop ───────────────────────────────────────────────────
    print(f'[WD] Monitoring PID={current_pid}. Press Ctrl+C to stop.')

    while True:
        time.sleep(POLL_INTERVAL)
        now_ist = datetime.now(IST)

        # ── Daily restart counter reset ────────────────────────────────────────
        today = now_ist.date()
        if today != last_restart_day:
            restarts_today   = 0
            last_restart_day = today
            print(f'[WD] New day — restart counter reset')

        # ── After market close: stop monitoring ───────────────────────────────
        t = (now_ist.hour, now_ist.minute)
        if t > MARKET_CLOSE_IST:
            print(f'[WD] Market closed ({now_ist.strftime("%H:%M IST")}). Watchdog sleeping.')
            _tg(f'🌙 Watchdog: market closed. Sleeping until tomorrow.')
            # Sleep until the next market open day
            while True:
                time.sleep(300)
                if _is_market_hours():
                    break
            print('[WD] Market re-opened. Launching bot...')
            current_pid = _launch_bot(headless=True)
            restarts_today = 0
            continue

        # ── Outside market hours (weekend / pre-open): skip ───────────────────
        if not _is_market_hours():
            print(f'[WD] {now_ist.strftime("%H:%M IST")} — outside market hours, skipping check')
            continue

        # ── Max restart guard ──────────────────────────────────────────────────
        if restarts_today >= MAX_RESTARTS_DAY:
            _tg(
                f'🚨 <b>Watchdog: MAX RESTARTS REACHED ({MAX_RESTARTS_DAY}/day)</b>\n'
                f'Bot has crashed {restarts_today}x today. Halting watchdog.\n'
                f'Manual intervention required.'
            )
            print(f'[WD] MAX RESTARTS ({MAX_RESTARTS_DAY}) reached — stopping watchdog')
            break

        # ── Check 1: Process alive? ────────────────────────────────────────────
        process_alive = _is_running(current_pid)

        # ── Check 2: Heartbeat fresh? ──────────────────────────────────────────
        hb_age = _heartbeat_age()
        heartbeat_ok = hb_age < HEARTBEAT_TIMEOUT

        if process_alive and heartbeat_ok:
            print(f'[WD] {now_ist.strftime("%H:%M")} ✅ PID={current_pid} alive | HB age={hb_age:.0f}s')
            continue

        # ── Something is wrong — restart ──────────────────────────────────────
        reason = []
        if not process_alive:
            reason.append(f'process died (PID {current_pid})')
        if not heartbeat_ok:
            reason.append(f'heartbeat stale ({hb_age:.0f}s > {HEARTBEAT_TIMEOUT}s)')

        reason_str = ' + '.join(reason)
        restarts_today += 1

        _tg(
            f'⚠️ <b>CB6 QUANTUM NSE bot down!</b>\n'
            f'Reason : {reason_str}\n'
            f'Restart: #{restarts_today}/{MAX_RESTARTS_DAY}\n'
            f'Time   : {now_ist.strftime("%H:%M IST")}\n\n'
            f'<i>Attempting auto-restart...</i>'
        )

        # Kill stale process if still exists
        if process_alive and not heartbeat_ok:
            print(f'[WD] Killing hung process PID={current_pid}')
            _kill_pid(current_pid)
            time.sleep(5)

        # Re-launch
        print(f'[WD] Restarting bot (restart #{restarts_today})...')
        current_pid = _launch_bot(headless=True)

        if _is_running(current_pid):
            _tg(
                f'✅ <b>NSE bot restarted successfully</b>\n'
                f'New PID : {current_pid}\n'
                f'Restart : #{restarts_today}\n'
                f'Time    : {now_ist.strftime("%H:%M IST")}'
            )
            print(f'[WD] Bot restarted — new PID={current_pid}')
        else:
            _tg(
                f'❌ <b>NSE bot restart FAILED (attempt #{restarts_today})</b>\n'
                f'Check logs/auto_token.log and logs/nse_bot.log'
            )
            print(f'[WD] Restart failed — check logs')


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='CB6 Quantum NSE bot watchdog')
    parser.add_argument('--attach', action='store_true',
                        help='Attach to already-running bot (read PID from data/nse_bot.pid)')
    args = parser.parse_args()

    try:
        run_watchdog(attach=args.attach)
    except KeyboardInterrupt:
        print('\n[WD] Watchdog stopped by user')
        _tg('⛔ CB6 QUANTUM Watchdog stopped (manual Ctrl+C)')


if __name__ == '__main__':
    main()
