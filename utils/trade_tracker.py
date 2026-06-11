# utils/trade_tracker.py
# Live trade tracker — monitors a watchlist setup (entry/SL/T1/T2) and sends
# Telegram updates as price moves. Designed for analysis-only equity setups.
# No orders placed. Works with NSE equity symbols.

import threading
import time
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from utils.logger import logger
from utils.telegram_alerts import send_message

_IST        = timezone(timedelta(hours=5, minutes=30))
_TRACK_FILE = Path(__file__).parent.parent / "data" / "watchlist_trades.json"

# How often to check price during market hours (seconds)
POLL_SECS = 900   # 15 minutes

# % proximity to a level that triggers a "approaching" alert
APPROACH_PCT = 0.003   # 0.3% away


# ── Persistent storage ────────────────────────────────────────────────────────

def _load_trades() -> dict:
    try:
        if _TRACK_FILE.exists():
            return json.loads(_TRACK_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_trades(trades: dict):
    try:
        _TRACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TRACK_FILE.write_text(json.dumps(trades, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.debug(f"trade_tracker save error: {exc}")


# ── Active trades registry ────────────────────────────────────────────────────
# key: symbol string (e.g. "NSE:RELIANCE-EQ")
# value: trade dict with entry, sl, t1, t2, direction, grade, tf, window,
#        t1_hit, t2_hit, sl_hit, start_price, start_time, alerts_sent[]

_active: dict = _load_trades()
_lock   = threading.Lock()


def register_trade(symbol: str, display: str, setup: dict,
                   grade: str, tf_label: str, window_label: str):
    """
    Register a new trade to track. Called by watchlist_monitor when A+/A found.
    Sends the initial alert immediately.
    """
    sig       = setup.get("entry_signal", {})
    entry     = sig.get("entry", 0)
    sl        = sig.get("stop_loss", 0)
    t1        = sig.get("target1", 0)
    t2        = sig.get("target2", 0)
    rr        = sig.get("rr_ratio", 0)
    direction = setup.get("direction", "UNKNOWN")
    score     = setup.get("confluence_score", setup.get("score", "?"))
    mss       = setup.get("mss_type", "MSS")
    sweep     = setup.get("sweep_type", "")

    if not entry or not sl:
        return

    now_ist = datetime.now(_IST)

    trade = {
        "symbol"      : symbol,
        "display"     : display,
        "direction"   : direction,
        "grade"       : grade,
        "tf_label"    : tf_label,
        "window_label": window_label,
        "entry"       : entry,
        "sl"          : sl,
        "t1"          : t1,
        "t2"          : t2,
        "rr"          : rr,
        "score"       : score,
        "mss"         : mss,
        "sweep"       : sweep,
        "start_time"  : now_ist.isoformat(),
        "t1_hit"      : False,
        "t2_hit"      : False,
        "sl_hit"      : False,
        "closed"      : False,
        "alerts_sent" : [],
    }

    with _lock:
        _active[symbol] = trade
        _save_trades(_active)

    _send_setup_alert(trade)
    logger.info(f"TRACKER: registered {display} [{tf_label}] {direction} {grade} entry={entry}")


def _send_setup_alert(t: dict):
    dir_ico = "🟢 LONG" if t["direction"] == "BULLISH" else "🔴 SHORT"
    badge   = "⭐A+" if t["grade"] == "A+" else "🅰 A"

    msg = (
        f"<b>🎯 TRADE TRACKING STARTED — {t['display']} [{t['tf_label']}] {badge}</b>\n"
        f"Scan: {t['window_label']}\n"
        f"\n"
        f"{dir_ico}  |  Score: {t['score']}/15  |  {t['mss']} {t['sweep']}\n"
        f"Entry  : <b>₹{t['entry']:.2f}</b>\n"
        f"SL     : ₹{t['sl']:.2f}\n"
        f"T1     : ₹{t['t1']:.2f}\n"
        f"T2     : ₹{t['t2']:.2f}\n"
        f"RR     : {t['rr']:.1f}R\n"
        f"\n"
        f"📡 Tracking live — updates every 15 min\n"
        f"<i>Analysis only — no auto-trade on equities</i>"
    )
    send_message(msg, parse_mode="HTML")


# ── Price update logic ────────────────────────────────────────────────────────

def _pct(current, ref) -> float:
    if not ref:
        return 0.0
    return (current - ref) / ref * 100


def _check_trade(t: dict, ltp: float) -> bool:
    """
    Evaluate current price vs trade levels. Send alerts for key events.
    Returns True if the trade should be closed (SL or T2 hit).
    """
    if t.get("closed"):
        return True

    direction = t["direction"]
    entry     = t["entry"]
    sl        = t["sl"]
    t1        = t["t1"]
    t2        = t["t2"]
    display   = t["display"]
    tf        = t["tf_label"]
    alerts    = t.get("alerts_sent", [])

    is_long  = direction == "BULLISH"
    pnl_pct  = _pct(ltp, entry) if is_long else -_pct(ltp, entry)

    # ── SL hit ───────────────────────────────────────────────────────────────
    sl_hit = (ltp <= sl) if is_long else (ltp >= sl)
    if sl_hit and "sl_hit" not in alerts:
        loss_pts = abs(ltp - entry)
        msg = (
            f"<b>❌ SL HIT — {display} [{tf}]</b>\n"
            f"Price: ₹{ltp:.2f}  |  SL: ₹{sl:.2f}\n"
            f"Loss: ₹{loss_pts:.2f} ({pnl_pct:.1f}%)\n"
            f"Trade closed."
        )
        send_message(msg, parse_mode="HTML")
        t["sl_hit"] = True
        t["closed"] = True
        alerts.append("sl_hit")
        return True

    # ── T1 hit ────────────────────────────────────────────────────────────────
    t1_hit = (ltp >= t1) if is_long else (ltp <= t1)
    if t1_hit and not t.get("t1_hit") and "t1_hit" not in alerts:
        gain_pts = abs(ltp - entry)
        msg = (
            f"<b>✅ T1 HIT — {display} [{tf}]</b>\n"
            f"Price: ₹{ltp:.2f}  |  T1: ₹{t1:.2f}\n"
            f"Gain: ₹{gain_pts:.2f} ({pnl_pct:.1f}%)\n"
            f"T2 target: ₹{t2:.2f}  |  Move SL to breakeven ✔"
        )
        send_message(msg, parse_mode="HTML")
        t["t1_hit"] = True
        alerts.append("t1_hit")

    # ── T2 hit ────────────────────────────────────────────────────────────────
    t2_hit = (ltp >= t2) if is_long else (ltp <= t2)
    if t2_hit and not t.get("t2_hit") and "t2_hit" not in alerts:
        gain_pts = abs(ltp - entry)
        msg = (
            f"<b>🏆 T2 HIT — {display} [{tf}] FULL TARGET</b>\n"
            f"Price: ₹{ltp:.2f}  |  T2: ₹{t2:.2f}\n"
            f"Gain: ₹{gain_pts:.2f} ({pnl_pct:.1f}%)\n"
            f"Trade complete. 🎉"
        )
        send_message(msg, parse_mode="HTML")
        t["t2_hit"] = True
        t["closed"] = True
        alerts.append("t2_hit")
        return True

    # ── Approaching T1 (if not yet hit) ───────────────────────────────────────
    if not t.get("t1_hit") and "approach_t1" not in alerts:
        dist = abs(ltp - t1) / t1 if t1 else 1
        if dist <= APPROACH_PCT:
            msg = (
                f"<b>📈 Approaching T1 — {display} [{tf}]</b>\n"
                f"Price: ₹{ltp:.2f}  →  T1: ₹{t1:.2f}  ({dist*100:.2f}% away)\n"
                f"PnL so far: {pnl_pct:+.1f}%"
            )
            send_message(msg, parse_mode="HTML")
            alerts.append("approach_t1")

    # ── 15-min periodic update ─────────────────────────────────────────────────
    last_update_key = "last_update_ts"
    now_ts = time.monotonic()
    last_ts = t.get(last_update_key, 0)
    if now_ts - last_ts >= POLL_SECS:
        t[last_update_key] = now_ts
        status = "🟢 IN PROFIT" if pnl_pct > 0 else "🔴 IN LOSS"
        t1_tag  = "✅T1" if t.get("t1_hit") else f"T1 ₹{t1:.2f}"
        msg = (
            f"<b>📡 TRACK UPDATE — {display} [{tf}]</b>\n"
            f"LTP: ₹{ltp:.2f}  |  Entry: ₹{entry:.2f}\n"
            f"PnL: {pnl_pct:+.1f}%  |  {status}\n"
            f"{t1_tag}  |  T2 ₹{t2:.2f}  |  SL ₹{sl:.2f}"
        )
        send_message(msg, parse_mode="HTML")

    return False


# ── Market hours gate ─────────────────────────────────────────────────────────

def _market_open() -> bool:
    now_ist = datetime.now(_IST)
    if now_ist.weekday() >= 5:
        return False
    t = now_ist.time()
    from datetime import time as dtime
    return dtime(9, 15) <= t <= dtime(15, 30)


# ── Background monitor loop ───────────────────────────────────────────────────

def _tracker_loop(fyers_getter):
    logger.info("TRADE TRACKER: background monitor started")
    time.sleep(90)   # wait for main engine to fully start

    while True:
        try:
            if _market_open() and _active:
                fyers = fyers_getter()
                if fyers:
                    from scanner.live_price import get_live_price
                    to_close = []

                    with _lock:
                        open_trades = {k: v for k, v in _active.items() if not v.get("closed")}

                    for symbol, trade in open_trades.items():
                        try:
                            ltp = get_live_price(fyers, symbol)
                            if ltp:
                                done = _check_trade(trade, ltp)
                                if done:
                                    to_close.append(symbol)
                        except Exception as exc:
                            logger.debug(f"TRACKER {symbol}: {exc}")

                    if to_close:
                        with _lock:
                            for sym in to_close:
                                if sym in _active:
                                    _active[sym]["closed"] = True
                            _save_trades(_active)

        except Exception as exc:
            logger.debug(f"TRACKER loop error: {exc}")

        time.sleep(60)   # check every minute; _check_trade handles own 15-min throttle


def start(fyers_getter) -> threading.Thread:
    """Start the trade tracker background thread."""
    t = threading.Thread(
        target=_tracker_loop,
        args=(fyers_getter,),
        daemon=True,
        name="TradeTracker",
    )
    t.start()
    return t


# ── Manual helpers ────────────────────────────────────────────────────────────

def list_active() -> list:
    """Return list of currently tracked (open) trades."""
    with _lock:
        return [v for v in _active.values() if not v.get("closed")]


def close_all():
    """Mark all trades closed (call on market close or manual reset)."""
    with _lock:
        for t in _active.values():
            t["closed"] = True
        _save_trades(_active)
