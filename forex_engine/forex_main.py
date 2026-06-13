# forex_engine/forex_main.py
#
# CB6 Quantum — Modular Forex Engine Launcher
# Entry point for the new forex_engine/ structure.
#
# Usage (from project root):
#   python -m forex_engine.forex_main
#   python -m forex_engine.forex_main --profile FTMO
#   python -m forex_engine.forex_main --profile GFT_5K_2STEP
#   python -m forex_engine.forex_main --profile PAPER_FOREX
#
# Legacy launcher at root/forex_main.py continues to work for FTMO paper mode.

import argparse
import datetime
import os
import subprocess
import sys
import threading
import time
import traceback

# Ensure project root is on sys.path when run as a module
_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import dotenv_values
_env = dotenv_values(os.path.join(_ROOT, '.env'))
for _k, _v in _env.items():
    if _k not in os.environ:
        os.environ[_k] = _v

from utils.logger import logger


def _banner(profile: str, mode: str, symbols: str | None = None):
    logger.info("=" * 60)
    logger.info("CB6 QUANTUM — MODULAR FOREX ENGINE")
    logger.info(f"Profile  : {profile}")
    logger.info(f"Mode     : {mode}")
    logger.info("Strategy : ICT Silver Bullet · 15m · CHoCH/BOS -> FVG -> ATR targets")
    if symbols:
        logger.info(f"Symbols  : {symbols}")
    else:
        logger.info("Symbols  : XAUUSD (H4 bias req) | XAGUSD | USOIL")
    logger.info("Sessions : London 07-12 UTC | NY 16-20 UTC")
    logger.info("ATR T1   : Entry +/- 0.15*ATR_daily  |  T2 skip if >0.50*ATR_daily")
    logger.info("=" * 60)


def _run_gft_10k():
    live = os.getenv('CB6_GFT_10K_LIVE_EXECUTION', 'false').lower() == 'true'
    _banner('GFT $10K Instant', 'LIVE MT5' if live else 'Paper')
    from forex_engine.gft_10k.monitor import main as _worker_main
    _worker_main(['--account-namespace', 'GFT_10K'])


def _run_gft_2step():
    paper = os.getenv('GFT_2STEP_PAPER', 'true').lower() == 'true'
    _banner('GFT $5K 2-Step GOAT', 'Paper (yfinance)' if paper else 'LIVE MT5')
    from forex_engine.prop_firms.gft.gft_5k_2step import GFT2StepWorker
    from forex_engine.prop_firms.gft.gft_phase_tracker import load_state, get_summary

    state = load_state()
    s = get_summary(state)
    logger.info(
        f"[GFT-2STEP] Phase={s['phase'].upper()} Capital=${s['capital']:.2f} "
        f"Phase PnL=${s['progress'].get('profit_earned',0):+.2f} RiskMode={s['risk_mode'].upper()}"
    )

    worker = GFT2StepWorker(paper=paper)
    worker.run()


def _run_gft_1k_instant():
    live = os.getenv('CB6_GFT_1K_INSTANT_LIVE_EXECUTION', 'false').lower() == 'true'
    _banner('GFT $1K Instant', 'LIVE MT5' if live else 'Paper')
    from forex_engine.gft_1k_instant.monitor import main as _worker_main
    _worker_main(['--account-namespace', 'GFT_1K_INSTANT'])


def _run_paper():
    _banner('Paper Forex', 'Paper (yfinance)')
    os.environ['FOREX_PAPER'] = 'true'
    from forex_engine.forex_worker import main as _worker_main
    _worker_main()


def start_gft_1k_instant_worker(env: dict | None = None):
    """Optionally start the isolated GFT 1K Instant subprocess."""
    launch_env = env or os.environ.copy()
    if launch_env.get('CB6_GFT_1K_INSTANT_ENABLED', 'false').lower() != 'true':
        logger.info("GFT 1K Instant disabled")
        return None

    state_dir = launch_env.get('CB6_GFT_1K_INSTANT_STATE_DIR', 'data/gft_1k_instant')
    error_dir = os.path.join(_ROOT, state_dir.replace('/', os.sep))
    error_log = os.path.join(error_dir, 'startup_error.log')
    strict = launch_env.get('CB6_GFT_1K_INSTANT_STRICT_STARTUP', 'false').lower() == 'true'
    cmd = [
        sys.executable,
        '-m',
        'forex_engine.gft_1k_instant.monitor',
        '--account-namespace',
        'GFT_1K_INSTANT',
    ]

    try:
        os.makedirs(error_dir, exist_ok=True)
        logger.info(f"[GFT_1K_INSTANT] starting subprocess: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, cwd=_ROOT, env=launch_env)
        logger.info(f"[GFT_1K_INSTANT] started PID {proc.pid}")
        return proc
    except Exception as e:
        try:
            os.makedirs(error_dir, exist_ok=True)
            with open(error_log, 'a', encoding='utf-8') as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} startup failed: {e}\n")
                f.write(traceback.format_exc())
                f.write("\n")
        except Exception:
            logger.warning("[GFT_1K_INSTANT] startup error log write failed", exc_info=True)
        logger.error(f"[GFT_1K_INSTANT] startup failed: {e}")
        if strict:
            raise
        return None


def _run_all():
    """Run GFT engines in separate Python processes."""
    logger.info("=" * 60)
    logger.info("CB6 QUANTUM — ALL FOREX ENGINES STARTING")
    logger.info("  Engine 1 : GFT $5K 2-Step GOAT (LIVE)")
    logger.info("  Engine 2 : GFT $1K Instant (LIVE)")
    logger.info("  Engine 3 : GFT $10K Instant (LIVE)")
    logger.info("=" * 60)

    # Start GFT $10K Telegram bot listener in this (supervisor) process.
    # forex_bot.py reads state files from disk so it works without an MT5 connection.
    # Starting here means the bot is always reachable even if the GFT_10K subprocess crashes.
    try:
        from communications.forex_bot import start_listening as _fx_listen, send_alert as _fx_alert
        threading.Thread(target=_fx_listen, daemon=True, name="GFT10KTGBot").start()
        logger.info("GFT $10K Telegram bot listener started (@cb6forexbot)")
        # Give the listener 3s to drain old updates, then send startup alert
        def _send_startup():
            time.sleep(3)
            try:
                from forex_engine.gft_10k.config import GFT_10K_PROFILE as _P, live_execution_enabled
                mode = 'LIVE MT5' if live_execution_enabled() else 'Paper'
                syms = ' · '.join(_P['enabled_symbols'])
                _fx_alert(
                    f"<b>CB6 QUANTUM — GFT $10K INSTANT</b>\n\n"
                    f"Mode     : {mode}\n"
                    f"Markets  : {syms}\n"
                    f"Sessions : London 07-12 UTC · NY 16-20 UTC\n\n"
                    f"Bot ready — type /start for full menu"
                )
            except Exception as _ae:
                logger.warning(f"GFT $10K startup alert failed: {_ae}")
        threading.Thread(target=_send_startup, daemon=True, name="GFT10KStartupAlert").start()
    except Exception as _e:
        logger.warning(f"GFT $10K bot listener failed to start: {_e}")

    # MT5 is effectively process-global. Running both accounts as threads can
    # make one account overwrite the other's initialized terminal/session.
    env = os.environ.copy()
    specs = [
        ('GFT_5K_2STEP', [sys.executable, '-m', 'forex_engine.forex_main', '--profile', 'GFT_5K_2STEP']),
    ]
    procs = {}
    restarts   = {name: 0 for name, _ in specs}
    next_delay = {name: 5 for name, _ in specs}   # exponential backoff per process
    max_restarts = int(os.getenv('FOREX_ALL_MAX_RESTARTS', '10'))
    _BACKOFF_STEPS = [5, 15, 30, 60, 120, 300]    # seconds; 300s cap

    try:
        for name, cmd in specs:
            logger.info(f"[{name}] starting subprocess: {' '.join(cmd)}")
            procs[name] = subprocess.Popen(cmd, cwd=_ROOT, env=env)
            logger.info(f"[{name}] started PID {procs[name].pid}")
            time.sleep(2)

        gft_1k_proc = start_gft_1k_instant_worker(env=env)
        if gft_1k_proc is not None:
            procs['GFT_1K_INSTANT'] = gft_1k_proc
            restarts['GFT_1K_INSTANT'] = 0
            next_delay['GFT_1K_INSTANT'] = 5

        # GFT $10K — starts when CB6_GFT_10K_ENABLED=true
        if env.get('CB6_GFT_10K_ENABLED', 'false').lower() == 'true':
            _10k_cmd = [sys.executable, '-m', 'forex_engine.forex_main', '--profile', 'GFT_10K']
            logger.info(f"[GFT_10K] starting subprocess: {' '.join(_10k_cmd)}")
            procs['GFT_10K'] = subprocess.Popen(_10k_cmd, cwd=_ROOT, env=env)
            restarts['GFT_10K'] = 0
            next_delay['GFT_10K'] = 5
            logger.info(f"[GFT_10K] started PID {procs['GFT_10K'].pid}")
        else:
            logger.info("[GFT_10K] disabled — set CB6_GFT_10K_ENABLED=true when credentials ready")

        while procs:
            for name, proc in list(procs.items()):
                code = proc.poll()
                if code is not None:
                    procs.pop(name, None)
                    if name == 'GFT_10K' and code != 0:
                        # Terminal not installed yet — back off 5 min between retries, max 3 attempts
                        logger.error(f"[GFT_10K] subprocess exited with code {code} (terminal not installed?)")
                        if restarts[name] < 3:
                            delay = 300
                            restarts[name] += 1
                            logger.info(f"[GFT_10K] restarting #{restarts[name]}/3 in {delay}s")
                            time.sleep(delay)
                            procs[name] = subprocess.Popen(
                                [sys.executable, '-m', 'forex_engine.forex_main', '--profile', 'GFT_10K'],
                                cwd=_ROOT, env=env
                            )
                        else:
                            logger.error("[GFT_10K] 3 restart attempts exhausted — check MT5 terminal at C:\\CB6_MT5\\MT5_GFT_10K\\")
                        continue
                    if code == 0 and name == 'GFT_10K':
                        logger.warning(f"[GFT_10K] exited cleanly — not restarting")
                        continue
                    logger.error(f"[{name}] subprocess exited with code {code}")
                    if restarts[name] < max_restarts:
                        if name == 'GFT_1K_INSTANT':
                            cmd = [
                                sys.executable,
                                '-m',
                                'forex_engine.gft_1k_instant.monitor',
                                '--account-namespace',
                                'GFT_1K_INSTANT',
                            ]
                        elif name == 'GFT_10K':
                            cmd = [sys.executable, '-m', 'forex_engine.forex_main', '--profile', 'GFT_10K']
                        else:
                            cmd = dict(specs)[name]
                        delay = next_delay[name]
                        restarts[name] += 1
                        # advance backoff: 5→15→30→60→120→300s (cap at 300s)
                        idx = min(restarts[name], len(_BACKOFF_STEPS) - 1)
                        next_delay[name] = _BACKOFF_STEPS[idx]
                        logger.info(
                            f"[{name}] restarting child "
                            f"#{restarts[name]}/{max_restarts} "
                            f"(backoff {delay}s, next {next_delay[name]}s)"
                        )
                        time.sleep(delay)
                        procs[name] = subprocess.Popen(cmd, cwd=_ROOT, env=env)
                    else:
                        logger.error(f"[{name}] exceeded restart limit; leaving stopped")
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("[ALL] Stop requested; terminating forex subprocesses")
        for name, proc in procs.items():
            if proc.poll() is None:
                logger.info(f"[{name}] terminating PID {proc.pid}")
                proc.terminate()

        deadline = time.time() + 10
        for name, proc in procs.items():
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.2)
            if proc.poll() is None:
                logger.warning(f"[{name}] killing PID {proc.pid}")
                proc.kill()
        raise


_PROFILE_MAP = {
    'ALL'           : _run_all,
    'GFT_5K_2STEP'  : _run_gft_2step,
    'GFT_1K_INSTANT': _run_gft_1k_instant,
    'GFT_10K'       : _run_gft_10k,
    'PAPER_FOREX'   : _run_paper,
}


def _start_eod_scheduler():
    """
    Background thread: fires EOD report at 20:00 UTC (after GFT NY kill zone closes).
    Runs once per calendar day. Harmless if forex_main is shut down before 20:00.
    """
    _fired_today: set = set()

    def _loop():
        while True:
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            date_key = now.strftime('%Y-%m-%d')
            # Fire between 20:00-20:02 UTC, weekdays only (Mon=0 … Fri=4)
            if (now.weekday() < 5 and now.hour == 20 and now.minute < 2
                    and date_key not in _fired_today):
                _fired_today.add(date_key)
                try:
                    from utils.eod_report import generate_and_send
                    fpath = generate_and_send(trigger='GFT_CLOSE')
                    logger.info(f"[EOD-Scheduler] GFT close report sent: {fpath}")
                except Exception as _e:
                    logger.error(f"[EOD-Scheduler] GFT close report failed: {_e}")
            time.sleep(30)

    t = threading.Thread(target=_loop, name='eod-scheduler', daemon=True)
    t.start()
    logger.info("EOD scheduler started — will fire at 20:00 UTC on weekdays")


def main():
    parser = argparse.ArgumentParser(description='CB6 Quantum Forex Engine')
    parser.add_argument(
        '--profile', '-p',
        default='ALL',
        choices=list(_PROFILE_MAP.keys()),
        help='Trading profile to run (default: ALL — FTMO + GFT 2-Step)',
    )
    args = parser.parse_args()

    # Backfill closed trades into pattern DB on startup (idempotent — INSERT OR IGNORE)
    try:
        from ml_engine.learning.feedback_loop import backfill_all_state_files
        backfill_all_state_files()
    except Exception as _e:
        logger.debug(f"Pattern DB backfill skipped: {_e}")

    # Start background EOD scheduler (fires at 20:00 UTC after GFT NY close)
    _start_eod_scheduler()

    runner = _PROFILE_MAP[args.profile]
    try:
        runner()
    except KeyboardInterrupt:
        logger.info(f"[{args.profile}] Forex engine stopped by user")
    except Exception as e:
        logger.error(f"[{args.profile}] Fatal error: {e}")
        raise


if __name__ == '__main__':
    main()
