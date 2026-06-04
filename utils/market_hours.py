# utils/market_hours.py — Market Hours Checker
import os
import sys
from datetime import datetime, time
import pytz
import requests
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger

IST = pytz.timezone('Asia/Kolkata')

# NSE Market Hours (IST)
MARKET_OPEN    = time(9, 15)
MARKET_CLOSE   = time(15, 30)
NO_ENTRY_AFTER  = time(15, 0)    # No new entries after 15:00 — covers full afternoon SB window
NO_ENTRY_BEFORE = time(9, 45)    # Skip Judas Swing window (9:15-9:45)

# NSE Trading Holidays — verify against NSE annual calendar each year:
# https://www.nseindia.com/resources/exchange-communication-holidays
# Festival dates depend on lunar calendar and are confirmed by NSE annually.
# Dates that fall on Sat/Sun are auto-skipped (weekend check) — listed for reference.

NSE_HOLIDAYS_2026 = [
    "2026-01-26",  # Republic Day               (Mon)
    "2026-02-15",  # Mahashivratri              (Sun - weekend)
    "2026-03-03",  # Holi                       (Tue)
    "2026-03-21",  # Id-Ul-Fitr                 (Sat - weekend)
    "2026-03-26",  # Ram Navami                 (Thu)
    "2026-03-31",  # Mahavir Jayanti            (Tue)
    "2026-04-03",  # Good Friday                (Fri)
    "2026-04-14",  # Dr Ambedkar Jayanti        (Tue)
    "2026-05-01",  # Maharashtra Day            (Fri)
    "2026-05-28",  # Eid-ul-Adha (Bakri Id)     (Thu)
    "2026-06-26",  # Muharram                   (Fri)
    "2026-08-15",  # Independence Day           (Sat — weekend)
    "2026-09-14",  # Ganesh Chaturthi           (Mon)
    "2026-10-02",  # Gandhi Jayanti             (Fri)
    "2026-10-20",  # Dussehra                   (Tue)
    "2026-11-08",  # Diwali Laxmi Pujan         (Sun — weekend)
    "2026-11-10",  # Diwali Balipratipada       (Tue)
    "2026-11-24",  # Guru Nanak Jayanti         (Tue)
    "2026-12-25",  # Christmas                  (Fri)
]

NSE_HOLIDAYS_2027 = [
    "2027-01-26",  # Republic Day               (Tue)
    "2027-02-08",  # Mahashivratri              (Mon)
    "2027-03-22",  # Holi                       (Mon)
    "2027-03-26",  # Good Friday                (Fri)
    "2027-04-14",  # Ram Navami / Ambedkar      (Wed)
    "2027-05-01",  # Maharashtra Day            (Sat — weekend)
    "2027-05-17",  # Eid-ul-Fitr                (Mon)
    "2027-08-15",  # Independence Day           (Sun — weekend)
    "2027-09-03",  # Ganesh Chaturthi           (Fri)
    "2027-10-02",  # Gandhi Jayanti             (Sat — weekend)
    "2027-10-11",  # Dussehra                   (Mon)
    "2027-10-29",  # Diwali Laxmi Pujan         (Fri)
    "2027-11-19",  # Guru Nanak Jayanti         (Fri)
    "2027-12-25",  # Christmas                  (Sat — weekend)
]

# Combined holiday set used by all checks
NSE_HOLIDAYS = set(NSE_HOLIDAYS_2026 + NSE_HOLIDAYS_2027)

def is_market_open():
    """Check if market is currently open"""
    now     = datetime.now(IST)
    today   = now.strftime('%Y-%m-%d')
    current = now.time().replace(tzinfo=None)

    # Check weekend
    if now.weekday() >= 5:
        logger.info("Market closed: Weekend")
        return False

    # Check holiday
    if today in NSE_HOLIDAYS:
        logger.info(f"Market closed: Holiday {today}")
        return False

    # Check market hours (>= MARKET_CLOSE because market is closed at 15:30:00 exactly)
    if current < MARKET_OPEN or current >= MARKET_CLOSE:
        logger.info(f"Market closed: Outside hours {current}")
        return False

    return True

def can_enter_trade():
    """Check if we can enter new trades now"""
    now     = datetime.now(IST)
    today   = now.strftime('%Y-%m-%d')
    current = now.time().replace(tzinfo=None)

    # Check weekend
    if now.weekday() >= 5:
        return False, "Weekend"

    # Check holiday
    if today in NSE_HOLIDAYS:
        return False, f"Holiday"

    # Check entry window — skip Judas Swing (9:15-9:45) and last hour
    if current < NO_ENTRY_BEFORE:
        return False, "Too early (Judas Swing window)"

    if current > NO_ENTRY_AFTER:
        return False, "Too late (overnight risk)"

    return True, "OK"

def is_weekday():
    """Check if today is a weekday"""
    return datetime.now(IST).weekday() < 5

def time_to_market_open():
    """Get minutes until market opens"""
    now    = datetime.now(IST)
    open_t = now.replace(hour=9, minute=15, second=0, microsecond=0)
    if now < open_t:
        diff = int((open_t - now).total_seconds()) // 60
        return diff
    return 0

def get_market_status():
    """Get current market status string"""
    now     = datetime.now(IST)
    current = now.time().replace(tzinfo=None)

    if not is_weekday():
        return "CLOSED - Weekend"

    if now.strftime('%Y-%m-%d') in NSE_HOLIDAYS:
        return "CLOSED - Holiday"

    if current < MARKET_OPEN:
        mins = time_to_market_open()
        return f"CLOSED - Opens in {mins} mins"

    if current >= MARKET_CLOSE:
        return "CLOSED - Market ended for today"

    if MARKET_OPEN <= current < MARKET_CLOSE:
        return "OPEN"

    return "CLOSED"
