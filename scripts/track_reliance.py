# scripts/track_reliance.py
# Manually trigger a RELIANCE scan and start live tracking.
# Run from project root: python scripts/track_reliance.py
#
# Usage:
#   python scripts/track_reliance.py           <- scan all 4 TFs, send best setup
#   python scripts/track_reliance.py --tf 15   <- scan specific TF only

import sys
import os
import argparse
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.logger import logger
from utils.telegram_alerts import send_message

SYMBOL  = "NSE:RELIANCE-EQ"
DISPLAY = "RELIANCE"
WEIGHT  = 9.24

TIMEFRAMES = [
    ("5",  "5m"),
    ("15", "15m"),
    ("30", "30m"),
    ("60", "1H"),
]


def _resolve_grade(setup: dict) -> str:
    grade = setup.get("grade", "")
    if grade:
        return str(grade).strip()
    try:
        s = float(setup.get("confluence_score", setup.get("score", 0)))
    except (TypeError, ValueError):
        s = 0
    if s >= 12:  return "A+"
    if s >= 10:  return "A"
    if s >= 8:   return "B"
    return "C"


def _get_fyers():
    """Load Fyers instance from .env token."""
    from settings import FYERS_CLIENT_ID, FYERS_ACCESS_TOKEN
    from fyers_apiv3 import fyersModel
    token_str = FYERS_ACCESS_TOKEN or ""
    if ":" in token_str:
        token_str = token_str.split(":", 1)[1]
    return fyersModel.FyersModel(
        client_id=FYERS_CLIENT_ID,
        token=token_str,
        is_async=False,
        log_path="",
    )


def scan_reliance(tf_codes: list = None, manual_window: str = "Manual Scan"):
    fyers = _get_fyers()

    from scanner.data_fetcher import get_historical_data
    from scanner.silver_bullet import scan_silver_bullet
    from utils.trade_tracker import register_trade

    tfs = [(c, l) for c, l in TIMEFRAMES if tf_codes is None or c in tf_codes]
    best_setup  = None
    best_grade  = "C"
    best_tf     = None

    grade_order = {"A+": 3, "A": 2, "B": 1, "C": 0}

    for tf_code, tf_label in tfs:
        try:
            days = 3 if tf_code in ("5", "15") else 10
            df   = get_historical_data(fyers, SYMBOL, tf_code, days=days)
            if df is None or len(df) < 30:
                print(f"  [{tf_label}] Not enough data — skipped")
                continue

            setup = scan_silver_bullet(df, SYMBOL, tf=tf_code, fyers=fyers, force=True)
            if not setup:
                print(f"  [{tf_label}] No setup found")
                continue

            grade = _resolve_grade(setup)
            score = setup.get("confluence_score", setup.get("score", "?"))
            direction = setup.get("direction", "?")
            print(f"  [{tf_label}] {direction} grade={grade} score={score}")

            if grade_order.get(grade, 0) > grade_order.get(best_grade, 0):
                best_setup = setup
                best_grade = grade
                best_tf    = tf_label

        except Exception as exc:
            print(f"  [{tf_label}] Error: {exc}")

    if best_setup and best_grade in ("A+", "A"):
        print(f"\n✅ Best setup: [{best_tf}] grade={best_grade} — sending alert + starting tracker")
        register_trade(SYMBOL, DISPLAY, best_setup, best_grade, best_tf, manual_window)
    elif best_setup:
        print(f"\n⚠️  Best setup grade={best_grade} (below A threshold). Sending info alert only.")
        sig   = best_setup.get("entry_signal", {})
        score = best_setup.get("confluence_score", best_setup.get("score", "?"))
        msg = (
            f"<b>📊 RELIANCE Manual Scan — {best_grade} setup [{best_tf}]</b>\n"
            f"Direction: {best_setup.get('direction','?')}\n"
            f"Score: {score}/15  |  Grade: {best_grade}\n"
            f"Entry: ₹{sig.get('entry',0):.2f}  |  SL: ₹{sig.get('stop_loss',0):.2f}\n"
            f"T1: ₹{sig.get('target1',0):.2f}  |  T2: ₹{sig.get('target2',0):.2f}\n"
            f"\n<i>Grade below A — not auto-tracked. Run again closer to entry.</i>"
        )
        send_message(msg, parse_mode="HTML")
    else:
        print("\n❌ No valid setup found on any timeframe.")
        send_message(
            "<b>📊 RELIANCE Manual Scan</b>\nNo valid ICT setup found on 5m/15m/30m/1H right now.",
            parse_mode="HTML"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual RELIANCE scan + tracker")
    parser.add_argument("--tf", nargs="+", choices=["5", "15", "30", "60"],
                        help="Timeframes to scan (default: all)")
    args = parser.parse_args()

    print(f"Scanning RELIANCE on {'all TFs' if not args.tf else args.tf}...")
    scan_reliance(tf_codes=args.tf)
    print("Done.")
