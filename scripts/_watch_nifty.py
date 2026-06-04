"""
CB6 Quantum — NIFTY Setup Watcher
Scans every 3 minutes, sends Telegram alert the moment a setup fires.
Run: python _watch_nifty.py
Stop: Ctrl+C
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import dotenv_values
from fyers_apiv3 import fyersModel
from scanner.index_futures import get_active_futures
from scanner.data_fetcher import get_historical_data, clear_cache
from scanner.silver_bullet import scan_silver_bullet, format_sb_alert
from utils.telegram_alerts import send_message

env = dotenv_values(os.path.join(os.path.dirname(__file__), '.env'))
t = env.get('ACCESS_TOKEN', '')
if ':' in t:
    t = t.split(':', 1)[1]
fyers = fyersModel.FyersModel(
    client_id=env.get('CLIENT_ID', ''),
    token=t,
    is_async=False,
    log_path=os.path.join(os.path.dirname(__file__), 'logs', '')
)

futures   = get_active_futures()
NIFTY_SYM = futures['NIFTY']

SCAN_INTERVAL = 180   # seconds (3 min candle)
alerted_keys  = set() # avoid duplicate alerts for same zone

print("CB6 Quantum — NIFTY Watcher LIVE")
print(f"Symbol  : {NIFTY_SYM}")
print(f"Interval: every {SCAN_INTERVAL}s (3m)")
print("Waiting for Silver Bullet setup...\n")
print("Press Ctrl+C to stop.\n")

send_message("CB6 Quantum NIFTY Watcher started. Scanning every 3 min for Silver Bullet setup.")

while True:
    try:
        clear_cache()
        df = get_historical_data(fyers, NIFTY_SYM, '3', days=3)

        if df is None or len(df) < 30:
            print(f"[{time.strftime('%H:%M:%S')}] No data — retrying...")
            time.sleep(SCAN_INTERVAL)
            continue

        ltp   = round(float(df['close'].iloc[-1]), 2)
        setup = scan_silver_bullet(df, NIFTY_SYM, tf='3', fyers=fyers)

        if not setup:
            print(f"[{time.strftime('%H:%M:%S')}] NIFTY {ltp} — no setup yet")
            time.sleep(SCAN_INTERVAL)
            continue

        # Dedup key: direction + FVG zone (avoid alerting same setup repeatedly)
        sig       = setup.get('entry_signal', {})
        direction = setup.get('direction', '')
        entry     = sig.get('entry', 0)
        dedup_key = f"{direction}_{round(entry, -1)}"   # round to nearest 10

        if dedup_key in alerted_keys:
            print(f"[{time.strftime('%H:%M:%S')}] NIFTY {ltp} — setup already alerted ({dedup_key})")
            time.sleep(SCAN_INTERVAL)
            continue

        alerted_keys.add(dedup_key)

        # Build alert
        mss_type   = setup.get('mss_type', 'BOS')
        score      = setup.get('confluence', 0)
        sl         = sig.get('stop_loss', 0)
        t1         = sig.get('target1', 0)
        t2         = sig.get('target2', 0)
        t3         = sig.get('target3', 0)
        option_info = setup.get('option_info')
        opt_line   = ''
        if option_info:
            opt_line = (f"\nOption : {option_info.get('symbol','')} "
                        f"LTP {option_info.get('ltp','')}")

        msg = (
            f"CB6 Quantum SETUP ALERT\n"
            f"{'='*30}\n"
            f"Index     : NIFTY\n"
            f"Direction : {direction}\n"
            f"MSS Type  : {mss_type}\n"
            f"Score     : {score}/20\n"
            f"LTP       : {ltp}\n"
            f"{'─'*30}\n"
            f"Entry     : {entry}\n"
            f"SL        : {sl}\n"
            f"T1        : {t1}\n"
            f"T2        : {t2}\n"
            f"T3        : {t3}"
            f"{opt_line}\n"
            f"{'='*30}\n"
            f"TF: 3min | CB6 Quantum"
        )

        send_message(msg)
        print(f"\n[{time.strftime('%H:%M:%S')}] ALERT SENT — {direction} setup at {entry}\n")

    except KeyboardInterrupt:
        print("\nWatcher stopped.")
        send_message("CB6 Quantum NIFTY Watcher stopped.")
        break
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] Error: {e}")

    time.sleep(SCAN_INTERVAL)
