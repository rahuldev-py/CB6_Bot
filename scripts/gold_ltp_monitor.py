"""
XAUUSD live LTP monitor — polls MT5 every 15 seconds permanently.
Run: python scripts/gold_ltp_monitor.py
Stop: Ctrl+C
"""
import time
import MetaTrader5 as mt5
from datetime import datetime, timezone, timedelta

SYMBOL = "XAUUSD.x"
INTERVAL = 15  # seconds
IST = timedelta(hours=5, minutes=30)

def init_mt5():
    if not mt5.initialize():
        print(f"[ERROR] MT5 init failed: {mt5.last_error()}")
        return False
    mt5.symbol_select(SYMBOL, True)
    info = mt5.account_info()
    if info:
        print(f"[MT5] Connected — Account: {info.login} | Server: {info.server}")
    return True

def get_ltp():
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        return None, None, None
    return tick.bid, tick.ask, (tick.bid + tick.ask) / 2

def main():
    print(f"{'='*55}")
    print(f"  XAUUSD Live Monitor  |  Interval: {INTERVAL}s  |  Ctrl+C to stop")
    print(f"{'='*55}")

    if not init_mt5():
        return

    prev_mid = None
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            now_ist = now_utc + IST
            bid, ask, mid = get_ltp()

            if mid is None:
                print(f"[{now_utc.strftime('%H:%M:%S')} UTC] XAUUSD tick unavailable — retrying...")
            else:
                arrow = ""
                if prev_mid is not None:
                    diff = mid - prev_mid
                    arrow = f"  ▲ +{diff:.2f}" if diff > 0 else (f"  ▼ {diff:.2f}" if diff < 0 else "  —")
                print(
                    f"[{now_utc.strftime('%H:%M:%S')} UTC | {now_ist.strftime('%H:%M:%S')} IST]  "
                    f"XAUUSD  Bid: {bid:.2f}  Ask: {ask:.2f}  Mid: {mid:.2f}{arrow}"
                )
                prev_mid = mid

            time.sleep(INTERVAL)

        except KeyboardInterrupt:
            print("\n[STOPPED] Gold monitor shut down.")
            break
        except Exception as e:
            print(f"[ERROR] {e} — retrying in {INTERVAL}s")
            time.sleep(INTERVAL)

    mt5.shutdown()

if __name__ == "__main__":
    main()
