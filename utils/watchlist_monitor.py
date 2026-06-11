# utils/watchlist_monitor.py
# Nifty 50 Top-10 Watchlist Background Monitor
#
# ANALYSIS ONLY — no trade execution, no equity orders ever.
# Runs 3 scheduled scans per day (9:30 / 12:00 / 15:00 IST) on 5m/15m/30m/1H TFs.
# Alerts only on A+ or A grade setups.
# Sends ICT setup alerts to NSE Telegram bot.
# Completely isolated from the 15s NSE futures scan cycle.

import threading
import time
from datetime import datetime, timedelta, timezone, date as date_type

from utils.logger import logger
from utils.telegram_alerts import send_message

# ── Watchlist ────────────────────────────────────────────────────────────────
# Update this list when NSE rebalances (March / September each year).
# Also update: data/watchlists/nifty50_top10_weighted.md + memory file.

WATCHLIST = [
    ("RELIANCE",   "NSE:RELIANCE-EQ",   9.24),
    ("HDFCBANK",   "NSE:HDFCBANK-EQ",   6.14),
    ("BHARTIARTL", "NSE:BHARTIARTL-EQ", 5.98),
    ("SBIN",       "NSE:SBIN-EQ",       4.90),
    ("ICICIBANK",  "NSE:ICICIBANK-EQ",  4.84),
    ("TCS",        "NSE:TCS-EQ",        4.21),
    ("BAJFINANCE", "NSE:BAJFINANCE-EQ", 2.93),
    ("LT",         "NSE:LT-EQ",         2.88),
    ("HINDUNILVR", "NSE:HINDUNILVR-EQ", 2.68),
    ("INFY",       "NSE:INFY-EQ",       2.60),
]

# Fyers TF codes → display labels
TIMEFRAMES = [
    ("5",  "5m"),
    ("15", "15m"),
    ("30", "30m"),
    ("60", "1H"),
]

# IST timezone
_IST = timezone(timedelta(hours=5, minutes=30))

# Three scan windows: (hour, minute, label)
_SCAN_WINDOWS = [
    (9,  30, "9:30 Open Scan"),
    (12,  0, "12:00 Midday Scan"),
    (15,  0, "15:00 Close Scan"),
]
_FIRE_WINDOW_MINUTES = 10    # fire if within 10 min after scheduled time

SCAN_INTERVAL_SECS  = 60     # check interval between scheduler ticks
ALERT_COOLDOWN_SECS = 900    # same symbol+TF+direction suppressed for 15 min

# ── Alert dedup cache ─────────────────────────────────────────────────────────
# key: (symbol, tf_label, direction) → monotonic timestamp of last alert
_alert_cache: dict = {}
_cache_lock  = threading.Lock()


def _is_suppressed(symbol: str, tf: str, direction: str) -> bool:
    key = (symbol, tf, direction)
    now = time.monotonic()
    with _cache_lock:
        ts = _alert_cache.get(key, 0)
        if now - ts < ALERT_COOLDOWN_SECS:
            return True
        _alert_cache[key] = now
        return False


# ── Market hours gate ─────────────────────────────────────────────────────────
def _market_open() -> bool:
    """True during NSE cash market hours 09:15–15:30 IST Mon–Fri."""
    now_ist = datetime.now(_IST)
    if now_ist.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    t = now_ist.time()
    from datetime import time as dtime
    return dtime(9, 15) <= t <= dtime(15, 30)


# ── ML shadow prediction (optional, non-blocking) ────────────────────────────
def _ml_tag(symbol: str, direction: str) -> str:
    """Return a short ML confidence tag if available, else empty string."""
    try:
        from ml_engine.shadow_predictor import predict as _pred
        result = _pred(symbol, direction)
        if result and isinstance(result, dict):
            conf = result.get("confidence", 0)
            if conf >= 0.65:
                return f"  🤖 ML {conf*100:.0f}%"
    except Exception:
        pass
    return ""


# ── Grade resolution ─────────────────────────────────────────────────────────
def _resolve_grade(setup: dict) -> str:
    """
    Return grade string from setup dict.
    Checks explicit 'grade' field first; falls back to numeric score.
    Grade mapping: score>=12 → A+, >=10 → A, >=8 → B, else C
    """
    grade = setup.get("grade", "")
    if grade:
        return str(grade).strip()

    score_val = setup.get("confluence_score", setup.get("score", 0))
    try:
        s = float(score_val)
    except (TypeError, ValueError):
        s = 0

    if s >= 12:
        return "A+"
    elif s >= 10:
        return "A"
    elif s >= 8:
        return "B"
    else:
        return "C"


def _grade_badge(grade: str) -> str:
    """Return an emoji badge for the grade to use in alert headers."""
    if grade == "A+":
        return "⭐A+"
    elif grade == "A":
        return "🅰 A"
    else:
        return grade


# ── Single symbol / TF scan ──────────────────────────────────────────────────
def _scan_symbol(fyers_instance, symbol: str, display: str, weight: float,
                 tf_code: str, tf_label: str, window_label: str):
    try:
        from scanner.data_fetcher import get_historical_data
        from scanner.silver_bullet import scan_silver_bullet

        days = 3 if tf_code in ("5", "15") else 10
        df   = get_historical_data(fyers_instance, symbol, tf_code, days=days)

        if df is None or len(df) < 30:
            return

        setup = scan_silver_bullet(df, symbol, tf=tf_code, fyers=fyers_instance, force=True)
        if not setup:
            return

        # ── Grade filter: only A+ and A ──────────────────────────────────────
        grade = _resolve_grade(setup)
        if grade not in ("A+", "A"):
            return   # skip B and C setups silently

        direction = setup.get("direction", "?")
        sig       = setup.get("entry_signal", {})
        entry     = sig.get("entry", 0)
        sl        = sig.get("stop_loss", 0)
        t1        = sig.get("target1", 0)
        t2        = sig.get("target2", 0)
        score     = setup.get("confluence_score", setup.get("score", "?"))
        mss       = setup.get("mss_type", "MSS")
        sweep     = setup.get("sweep_type", "")
        rr        = sig.get("rr_ratio", 0)

        if _is_suppressed(display, tf_label, direction):
            return

        ml_tag  = _ml_tag(symbol, direction)
        dir_ico = "🟢 LONG" if direction == "BULLISH" else "🔴 SHORT"
        badge   = _grade_badge(grade)

        msg = (
            f"<b>📊 WATCHLIST SETUP — {display} [{tf_label}] {badge}</b>\n"
            f"Scan: {window_label} | Weight: {weight}% of Nifty50\n"
            f"\n"
            f"{dir_ico}  |  Score: {score}/15  |  {mss} {sweep}\n"
            f"Entry : <b>₹{entry:.2f}</b>\n"
            f"SL    : ₹{sl:.2f}\n"
            f"T1    : ₹{t1:.2f}  |  T2: ₹{t2:.2f}\n"
            f"RR    : {rr:.1f}R{ml_tag}\n"
            f"\n"
            f"<i>⚠️ Analysis only — no auto-trade on equities</i>"
        )

        send_message(msg, parse_mode="HTML")
        logger.info(
            f"WATCHLIST {display} [{tf_label}]: {direction} {grade} setup score={score} "
            f"— alert sent [{window_label}]"
        )

        # Start live tracking for this setup
        try:
            from utils.trade_tracker import register_trade
            register_trade(symbol, display, setup, grade, tf_label, window_label)
        except Exception as _te:
            logger.debug(f"WATCHLIST tracker register error: {_te}")

    except Exception as exc:
        logger.debug(f"WATCHLIST {display} [{tf_label}]: scan error — {exc}")


# ── Full scan cycle ───────────────────────────────────────────────────────────
def _run_scan_cycle(fyers_instance, window_label: str):
    if not _market_open():
        return

    logger.debug(
        f"WATCHLIST: starting scan cycle [{window_label}] "
        f"({len(WATCHLIST)} stocks × {len(TIMEFRAMES)} TFs)"
    )

    for display, symbol, weight in WATCHLIST:
        for tf_code, tf_label in TIMEFRAMES:
            try:
                _scan_symbol(fyers_instance, symbol, display, weight,
                             tf_code, tf_label, window_label)
                time.sleep(0.3)   # small gap to avoid Fyers rate-limit bursts
            except Exception as exc:
                logger.debug(f"WATCHLIST {display} [{tf_label}]: {exc}")


# ── Background thread entry ───────────────────────────────────────────────────
def _monitor_loop(fyers_getter):
    """
    fyers_getter: callable that returns the current fyers_instance.
    Fires 3 scheduled scans per day at 9:30, 12:00, 15:00 IST (±10 min window).
    Resets fired-state at midnight IST when the calendar date changes.
    Only A+ or A grade setups generate alerts.
    """
    logger.info(
        "WATCHLIST monitor started — 3 scheduled scans/day (9:30/12:00/15:00 IST), A+/A only"
    )
    # Stagger startup by 60s so the main 15s scan cycle is fully warm first
    time.sleep(60)

    last_fired: dict = {}   # key: (date_ist, window_label) → True

    while True:
        try:
            now_ist = datetime.now(_IST)
            today   = now_ist.date()

            for (wh, wm, label) in _SCAN_WINDOWS:
                fire_key = (today, label)
                if fire_key in last_fired:
                    continue   # already ran this window today

                # Compute minutes elapsed since window open time
                window_dt = now_ist.replace(hour=wh, minute=wm, second=0, microsecond=0)
                delta_min = (now_ist - window_dt).total_seconds() / 60

                if 0 <= delta_min <= _FIRE_WINDOW_MINUTES:
                    last_fired[fire_key] = True
                    logger.info(f"WATCHLIST: firing [{label}] at {now_ist.strftime('%H:%M:%S IST')}")
                    fyers = fyers_getter()
                    if fyers is not None:
                        _run_scan_cycle(fyers, window_label=label)

        except Exception as exc:
            logger.debug(f"WATCHLIST monitor loop error: {exc}")

        time.sleep(SCAN_INTERVAL_SECS)   # check every 60 seconds


def start(fyers_getter) -> threading.Thread:
    """
    Start the watchlist monitor as a background daemon thread.

    fyers_getter: zero-argument callable returning the live fyers_instance.
    Example in main.py:
        from utils.watchlist_monitor import start as start_watchlist
        start_watchlist(lambda: fyers_instance)
    """
    t = threading.Thread(
        target=_monitor_loop,
        args=(fyers_getter,),
        daemon=True,
        name="WatchlistMonitor",
    )
    t.start()
    return t
