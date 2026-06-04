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
    # Skip weekends
    if now.weekday() >= 5:
        return False
    return _MARKET_OPEN_MIN <= m <= _MARKET_CLOSE_MIN

_tasks      = []
_repeating  = []   # [{interval_min, callback, name, last_run_minute}]
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
    """Fire callback every interval_minutes throughout the trading day.
    Does not enforce market-hours — use can_enter_trade() inside the callback."""
    with _lock:
        _repeating.append({
            'interval': interval_minutes,
            'callback': callback,
            'name'    : name,
            'last_run_minute': -1,
        })


def _loop():
    while True:
        now      = datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        # Absolute minute-of-day — used for interval tasks
        now_minute = now.hour * 60 + now.minute

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
                    last = rtask['last_run_minute']
                    if last < 0 or (now_minute - last) >= rtask['interval']:
                        rtask['last_run_minute'] = now_minute
                        try:
                            logger.debug(f"Scheduler: repeating '{rtask['name']}'")
                            threading.Thread(
                                target=rtask['callback'], daemon=True,
                                name=f"CB6-{rtask['name']}"
                            ).start()
                        except Exception as e:
                            logger.error(f"Scheduler repeating error ({rtask['name']}): {e}")

        time.sleep(30)


def start_scheduler():
    t = threading.Thread(target=_loop, daemon=True, name="CB6-Scheduler")
    t.start()
    logger.info("Scheduler started")
    return t
