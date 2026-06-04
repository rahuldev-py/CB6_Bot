# orchestrator.py — CB6 Quantum multi-engine launcher (local mode)
#
# Engines:
#   NSE   — main.py       (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY, 9:15–3:30 IST)
#   FOREX — forex_main.py (XAUUSD/XAGUSD/USOIL via MT5, 24/7)
#   CRYPTO — disabled until further command
#
# Usage:
#   python orchestrator.py             # NSE + FOREX
#   python orchestrator.py --nse-only
#   python orchestrator.py --forex-only
#
# Kill switch: create  data/kill_all.flag

import argparse
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.path.join(BASE_DIR, 'data')
KILL_FLAG      = os.path.join(DATA_DIR, 'kill_all.flag')
KILL_TOKEN     = os.getenv('ORCHESTRATOR_KILL_TOKEN', '').strip()
NSE_HEARTBEAT  = os.path.join(DATA_DIR, 'nse_heartbeat.txt')
FOREX_HEARTBEAT= os.path.join(DATA_DIR, 'forex_heartbeat.txt')
GFT_HEARTBEAT  = os.path.join(DATA_DIR, 'gft_2step_heartbeat.txt')

HEARTBEAT_STALE = 180
CHECK_INTERVAL  = 60
MAX_RESTARTS    = 10
PYTHON          = sys.executable
BACKUP_INTERVAL = 24 * 60 * 60


def _log(msg: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] ORCHESTRATOR | {msg}", flush=True)


def _heartbeat_age(path: str) -> float:
    try:
        if os.path.exists(path):
            return time.time() - os.path.getmtime(path)
    except Exception:
        pass
    return float('inf')


def _kill_flag_set() -> bool:
    if not os.path.exists(KILL_FLAG):
        return False
    if not KILL_TOKEN:
        return True
    try:
        with open(KILL_FLAG) as f:
            supplied = f.read().strip()
        if supplied == KILL_TOKEN:
            return True
        _log("Kill flag ignored — token mismatch")
        return False
    except Exception:
        return False


def _send_telegram(token: str, chat_id: str, text: str):
    try:
        import requests
        requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': text[:4096]},
            timeout=8,
        )
    except Exception:
        pass


def _startup_telegram(engines: list):
    from dotenv import dotenv_values
    env     = dotenv_values(os.path.join(BASE_DIR, '.env'))
    admin   = env.get('CB6_ADMIN_USER_ID', env.get('TELEGRAM_CHAT_ID', ''))
    now     = datetime.now().strftime('%H:%M IST')
    names   = ' + '.join(e.name for e in engines)
    msg = (
        f"CB6 QUANTUM — STARTING\n"
        f"Time   : {now}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Engines: {names}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Kill: create data/kill_all.flag"
    )

    # NSE bot — only if NSE engine is running
    nse_running = any(e.name == 'NSE' for e in engines)
    if nse_running:
        token   = env.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = env.get('TELEGRAM_CHAT_ID', '')
        if token and chat_id:
            _send_telegram(token, chat_id, msg)

    # FTMO bot — only when FTMO engine is in the launch set
    ftmo_running = any(e.name == 'FTMO' for e in engines)
    if ftmo_running:
        ftmo_token = env.get('TELEGRAM_BOT_TOKEN_FTMO', '')
        if ftmo_token and admin:
            _send_telegram(ftmo_token, admin, msg)

    # GFT bot — only when GFT engine is in the launch set
    gft_running = any(e.name == 'GFT' for e in engines)
    if gft_running:
        gft_token = env.get('TELEGRAM_BOT_TOKEN_GFT', '')
        if gft_token and admin:
            _send_telegram(gft_token, admin, msg)

    # Legacy FOREX engine name (single-profile launch via --forex-profile flag)
    legacy_forex = any(e.name == 'FOREX' for e in engines)
    if legacy_forex and not ftmo_running:
        ftmo_token = env.get('TELEGRAM_BOT_TOKEN_FTMO', env.get('FOREX_TELEGRAM_TOKEN', ''))
        fx_chat    = admin or env.get('FOREX_TELEGRAM_CHAT_ID', '')
        if ftmo_token and fx_chat:
            _send_telegram(ftmo_token, fx_chat, msg)


def _backup_state_files():
    try:
        from utils.state_io import backup_json_dir
        out_dir = backup_json_dir(DATA_DIR)
        _log(f"State backup written: {out_dir}")
    except Exception as e:
        _log(f"State backup failed: {e}")


def _backup_loop():
    _backup_state_files()
    while True:
        time.sleep(BACKUP_INTERVAL)
        _backup_state_files()


class EngineProcess:
    def __init__(self, name: str, cmd: list, heartbeat_file: str):
        self.name           = name
        self.cmd            = cmd
        self.heartbeat_file = heartbeat_file
        self.proc           = None
        self.stdout_fh      = None
        self.stderr_fh      = None
        self.restarts       = 0
        self.last_start     = 0.0

    def start(self):
        _log(f"Starting {self.name}...")
        logs_dir = os.path.join(BASE_DIR, 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        log_name = self.name.lower().replace(' ', '_')
        self.stdout_fh = open(os.path.join(logs_dir, f'{log_name}.out.log'), 'a', encoding='utf-8')
        self.stderr_fh = open(os.path.join(logs_dir, f'{log_name}.err.log'), 'a', encoding='utf-8')
        self.proc       = subprocess.Popen(
            self.cmd,
            cwd=BASE_DIR,
            env=os.environ.copy(),
            stdout=self.stdout_fh,
            stderr=self.stderr_fh,
        )
        self.last_start = time.time()
        _log(f"{self.name} started (PID {self.proc.pid})")

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def heartbeat_ok(self) -> bool:
        return _heartbeat_age(self.heartbeat_file) < HEARTBEAT_STALE

    def terminate(self):
        if self.proc and self.is_alive():
            _log(f"Terminating {self.name} (PID {self.proc.pid})...")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._close_logs()

    def _close_logs(self):
        for fh_name in ('stdout_fh', 'stderr_fh'):
            fh = getattr(self, fh_name, None)
            if fh:
                try:
                    fh.close()
                except Exception:
                    pass
                setattr(self, fh_name, None)

    def check_and_restart(self) -> bool:
        dead  = not self.is_alive()
        stale = not self.heartbeat_ok()

        if dead:
            code = self.proc.returncode if self.proc else '?'
            _log(f"{self.name} died (exit {code}). Restart #{self.restarts + 1}")
            self._close_logs()
        elif stale:
            _log(f"{self.name} heartbeat stale (>{HEARTBEAT_STALE}s). Restarting...")
            self.terminate()
        else:
            return True

        if self.restarts >= MAX_RESTARTS:
            _log(f"{self.name} exceeded {MAX_RESTARTS} restarts — giving up.")
            return False

        uptime = time.time() - self.last_start
        if uptime < 15:
            wait = int(15 - uptime)
            _log(f"{self.name} crashed too fast — waiting {wait}s before restart...")
            time.sleep(wait)

        self.restarts += 1
        self.start()
        return True


def main():
    parser = argparse.ArgumentParser(description='CB6 Quantum Orchestrator')
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument('--nse-only',   action='store_true')
    grp.add_argument('--forex-only', action='store_true')
    grp.add_argument('--gft-only',   action='store_true',
                     help='Run GFT 2-Step engine only (requires GFT credentials in .env)')
    parser.add_argument('--forex-profile', default='ALL',
                        choices=['ALL', 'FTMO', 'GFT_5K_2STEP', 'PAPER_FOREX'],
                        help='Forex profile to run (default: ALL — FTMO + GFT 2-Step)')
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    # REQ-2.3: Hard kill-flag freeze protection.
    # KILL_FLAG must be removed manually by an operator before the orchestrator
    # is permitted to boot. Auto-clearing is explicitly forbidden — if trading
    # was halted for an emergency, a human must acknowledge it before restart.
    if _kill_flag_set():
        _log("CRITICAL — kill_all.flag found at startup. Trading engines will NOT start.")
        _log(f"  Flag file : {KILL_FLAG}")
        _log("  Action    : Investigate the reason the flag was created.")
        _log("  To resume : delete data/kill_all.flag — then re-run the orchestrator.")
        sys.exit(1)

    engines = []

    if not args.forex_only and not args.gft_only:
        engines.append(EngineProcess(
            name           = 'NSE',
            cmd            = [PYTHON, 'main.py'],
            heartbeat_file = NSE_HEARTBEAT,
        ))

    if not args.nse_only and not args.gft_only:
        if args.forex_profile == 'ALL':
            engines.append(EngineProcess(
                name           = 'FTMO',
                cmd            = [PYTHON, 'forex_main.py', '--profile', 'FTMO'],
                heartbeat_file = FOREX_HEARTBEAT,
            ))
            engines.append(EngineProcess(
                name           = 'GFT',
                cmd            = [PYTHON, 'forex_main.py', '--profile', 'GFT_5K_2STEP'],
                heartbeat_file = GFT_HEARTBEAT,
            ))
        else:
            engines.append(EngineProcess(
                name           = 'FOREX',
                cmd            = [PYTHON, 'forex_main.py', '--profile', args.forex_profile],
                heartbeat_file = FOREX_HEARTBEAT if args.forex_profile != 'GFT_5K_2STEP' else GFT_HEARTBEAT,
            ))

    # GFT 2-Step engine — enable once GFT credentials are added to .env:
    #   GFT_2STEP_LOGIN=<login>  GFT_2STEP_PASSWORD=<pass>  GFT_2STEP_SERVER=<server>
    if args.gft_only:
        engines.append(EngineProcess(
            name           = 'GFT',
            cmd            = [PYTHON, 'forex_main.py', '--profile', 'GFT_5K_2STEP'],
            heartbeat_file = GFT_HEARTBEAT,
        ))

    # CRYPTO ENGINE DISABLED — re-enable on future command
    # engines.append(EngineProcess(name='CRYPTO', cmd=[PYTHON, 'crypto_main.py'], heartbeat_file=...))

    if not engines:
        _log("No engines selected — exiting.")
        return

    threading.Thread(target=_startup_telegram, args=(engines,), daemon=True).start()
    threading.Thread(target=_backup_loop, daemon=True, name='StateBackup').start()

    for eng in engines:
        eng.start()
        time.sleep(2)

    _log(f"All engines running: {[e.name for e in engines]}")
    _log(f"Health check every {CHECK_INTERVAL}s | stale threshold: {HEARTBEAT_STALE}s")
    _log(f"Kill switch: create  {KILL_FLAG}")

    failed = set()
    try:
        while True:
            time.sleep(CHECK_INTERVAL)

            if _kill_flag_set():
                _log("Kill flag detected — shutting down...")
                for eng in engines:
                    eng.terminate()
                _log("All engines stopped. kill_all.flag preserved by design.")
                return

            for eng in engines:
                if eng.name in failed:
                    continue
                ok = eng.check_and_restart()
                if not ok:
                    failed.add(eng.name)

            if len(failed) == len(engines):
                _log("All engines permanently failed — orchestrator exiting.")
                return

    except KeyboardInterrupt:
        _log("Ctrl+C — shutting down all engines...")
        for eng in engines:
            eng.terminate()
        _log("Orchestrator stopped.")


if __name__ == '__main__':
    main()
