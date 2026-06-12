"""
python stocks.py
Scans all 10 Nifty50 watchlist stocks on 5m/15m/30m/1H.
Sends every setup found to NSE Telegram bot.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from utils.logger import logger
from utils.telegram_alerts import send_message

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

TIMEFRAMES = [("60","1H")]


def get_fyers():
    from settings import CLIENT_ID
    from fyers_apiv3 import fyersModel
    from dotenv import dotenv_values
    import os
    env  = dotenv_values(os.path.join(os.path.dirname(__file__), ".env"))
    token = env.get("ACCESS_TOKEN", "")
    if ":" in token:
        token = token.split(":", 1)[1]
    return fyersModel.FyersModel(client_id=CLIENT_ID, token=token,
                                 is_async=False, log_path="")


def grade(setup):
    g = setup.get("grade", "")
    if g:
        return str(g).strip()
    try:
        s = float(setup.get("confluence_score", setup.get("score", 0)))
    except Exception:
        s = 0
    if s >= 12: return "A+"
    if s >= 10: return "A"
    if s >= 8:  return "B"
    return "C"


def scan_all():
    from scanner.data_fetcher import get_historical_data
    from scanner.silver_bullet import scan_silver_bullet

    fyers   = get_fyers()
    found   = 0

    send_message("🔍 <b>Watchlist scan started — 10 stocks × 4 TFs</b>", parse_mode="HTML")

    for display, symbol, weight in WATCHLIST:
        for tf_code, tf_label in TIMEFRAMES:
            try:
                days = 3 if tf_code in ("5","15") else 10
                df   = get_historical_data(fyers, symbol, tf_code, days=days)
                if df is None or len(df) < 30:
                    continue

                setup = scan_silver_bullet(df, symbol, tf=tf_code, fyers=fyers, force=True)
                if not setup:
                    continue

                g         = grade(setup)
                direction = setup.get("direction", "?")
                sig       = setup.get("entry_signal", {})
                entry     = sig.get("entry", 0)
                sl        = sig.get("stop_loss", 0)
                t1        = sig.get("target1", 0)
                t2        = sig.get("target2", 0)
                rr        = sig.get("rr_ratio", 0)
                score     = setup.get("confluence_score", setup.get("score", "?"))
                mss       = setup.get("mss_type", "MSS")

                badge    = "⭐A+" if g == "A+" else ("🅰 A" if g == "A" else f"[{g}]")
                dir_ico  = "🟢 LONG" if direction == "BULLISH" else "🔴 SHORT"

                msg = (
                    f"<b>📊 {display} [{tf_label}] {badge}</b>  {weight}% of Nifty50\n"
                    f"{dir_ico}  |  Score: {score}/15  |  {mss}\n"
                    f"Entry : <b>₹{entry:.2f}</b>\n"
                    f"SL    : ₹{sl:.2f}  |  RR: {rr:.1f}R\n"
                    f"T1    : ₹{t1:.2f}  |  T2: ₹{t2:.2f}\n"
                    f"<i>Analysis only — no auto-trade</i>"
                )
                send_message(msg, parse_mode="HTML")
                print(f"  {display} [{tf_label}] {direction} {g} score={score}")
                found += 1

            except Exception as exc:
                print(f"  {display} [{tf_label}] error: {exc}")

    summary = f"✅ <b>Scan complete</b> — {found} setup(s) found across 10 stocks" if found else \
              "✅ <b>Scan complete</b> — No setups found right now"
    send_message(summary, parse_mode="HTML")
    print(f"\nDone. {found} setup(s) sent to Telegram.")


if __name__ == "__main__":
    scan_all()
