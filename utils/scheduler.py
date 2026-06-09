# utils/scheduler.py — Time-based task scheduler for CB6 Bot
import os, sys, threading, time
from datetime import datetime
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger

# Repeating tasks only fire during market hours (IST)
_MARKET_OPEN_MIN  = 9 * 60 + 15   # 09:15
_MARKET_CLOSE_MIN = 15 * 60 + 30  # 15:30

def _in_market_hours() -> bool:
    now = datetime.now()
    m   = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        return False
    return _MARKET_OPEN_MIN <= m <= _MARKET_CLOSE_MIN

_tasks      = []
_repeating  = []   # [{interval_sec, callback, name, last_run_ts}]
_lock       = threading.Lock()


def schedule_daily(hour, minute, callback, name="task"):
    """Fire callback every calendar day at hour:minute."""
    with _lock:
        _tasks.append({
            'hour': hour, 'minute': minute,
            'weekday': None,
            'callback': callback, 'name': name, 'last_run': None
        })


def schedule_weekly(weekday, hour, minute, callback, name="task"):
    """Fire callback on weekday (0=Mon…6=Sun) at hour:minute."""
    with _lock:
        _tasks.append({
            'hour': hour, 'minute': minute,
            'weekday': weekday,
            'callback': callback, 'name': name, 'last_run': None
        })


def schedule_repeating(interval_minutes, callback, name="repeating"):
    """Fire callback every interval_minutes (float OK) throughout the trading day."""
    with _lock:
        _repeating.append({
            'interval_sec': interval_minutes * 60.0,
            'callback'    : callback,
            'name'        : name,
            'last_run_ts' : 0.0,
        })


def schedule_repeating_seconds(interval_seconds, callback, name="repeating"):
    """Fire callback every interval_seconds (minimum 15s) throughout the trading day."""
    with _lock:
        _repeating.append({
            'interval_sec': max(15.0, float(interval_seconds)),
            'callback'    : callback,
            'name'        : name,
            'last_run_ts' : 0.0,
        })


def _loop():
    while True:
        now      = datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        now_ts   = time.monotonic()

        with _lock:
            for task in _tasks:
                if task['last_run'] == date_str:
                    continue
                if now.hour != task['hour'] or now.minute != task['minute']:
                    continue
                if task['weekday'] is not None and now.weekday() != task['weekday']:
                    continue
                task['last_run'] = date_str
                try:
                    logger.info(f"Scheduler: firing '{task['name']}'")
                    threading.Thread(
                        target=task['callback'], daemon=True,
                        name=f"CB6-{task['name']}"
                    ).start()
                except Exception as e:
                    logger.error(f"Scheduler error ({task['name']}): {e}")

            if _in_market_hours():
                for rtask in _repeating:
                    elapsed = now_ts - rtask['last_run_ts']
                    if elapsed >= rtask['interval_sec']:
                        rtask['last_run_ts'] = now_ts
                        try:
                            logger.debug(f"Scheduler: repeating '{rtask['name']}'")
                            threading.Thread(
                                target=rtask['callback'], daemon=True,
                                name=f"CB6-{rtask['name']}"
                            ).start()
                        except Exception as e:
                            logger.error(f"Scheduler repeating error ({rtask['name']}): {e}")

        time.sleep(15)   # 15s heartbeat — supports 15-second scan cadence


def start_scheduler():
    t = threading.Thread(target=_loop, daemon=True, name="CB6-Scheduler")
    t.start()
    logger.info("Scheduler started")
    return t
