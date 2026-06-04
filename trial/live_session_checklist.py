"""
CB6 Quantum — TrueData Live Market Session Validator
Run this at 09:10 IST on the first live day after activating TrueData.

Usage:
    python trial/live_session_checklist.py          # runs all checks
    python trial/live_session_checklist.py --watch  # watches ticks for 5 min
"""
from __future__ import annotations
import sys, time, argparse, logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_IST_OPEN  = (9, 15)   # 09:15 IST
_IST_CLOSE = (15, 30)  # 15:30 IST


def _ist_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata"))


def check_auth():
    print("\n[1] Auth check...")
    from data.truedata_feed import get_manager
    td = get_manager()
    ok = td.connect_hist()
    status = "PASS" if ok else "FAIL"
    print(f"    Historical connect: {status}")
    return ok


def check_historical():
    print("\n[2] Historical data check (NIFTY-I 5min, last 5 days)...")
    from data.truedata_feed import get_manager
    td = get_manager()
    if not td.is_hist_ready:
        td.connect_hist()
    df = td.get_historical_bars("NIFTY-I", "5min", days=5)
    if df is None or len(df) < 10:
        print("    FAIL — no data returned")
        return False
    last_ts = df["timestamp"].max()
    print(f"    PASS — {len(df)} bars, latest: {last_ts}")
    return True


def check_live_connection():
    print("\n[3] Live WebSocket connection...")
    from data.truedata_feed import get_manager
    td = get_manager()
    symbols = ["NIFTY-I", "BANKNIFTY-I", "FINNIFTY-I", "MIDCPNIFTY-I"]
    ok = td.connect_live(symbols)
    print(f"    connect_live: {'PASS' if ok else 'FAIL'}")
    return ok


def check_tick_symbols(wait_seconds: int = 15):
    """
    After connecting live, verify that ticks arrive under Fyers-format keys.
    This confirms the _TD_TO_FYERS mapping is working in _dispatch_tick().
    """
    print(f"\n[4] Tick symbol mapping check ({wait_seconds}s observation)...")
    from data.truedata_feed import get_manager, _TD_TO_FYERS
    from scanner import websocket_feed

    td = get_manager()
    if not td.is_live_ready:
        print("    SKIP — live feed not connected")
        return None

    # Wait for ticks to arrive
    time.sleep(wait_seconds)

    results = {}
    for td_sym, fyers_sym in _TD_TO_FYERS.items():
        if "FUT" not in fyers_sym:
            continue
        fyers_tick = websocket_feed.get_latest_tick(fyers_sym)
        td_tick     = websocket_feed.get_latest_tick(td_sym)
        has_fyers   = bool(fyers_tick.get("ltp"))
        has_td      = bool(td_tick.get("ltp"))
        results[td_sym] = {
            "fyers_sym": fyers_sym,
            "fyers_ltp": fyers_tick.get("ltp"),
            "td_ltp":    td_tick.get("ltp"),
        }
        status = "PASS" if has_fyers else ("STALE" if has_td else "MISSING")
        print(f"    {td_sym:15s} -> {fyers_sym:25s}  LTP={fyers_tick.get('ltp','—'):>10}  [{status}]")

    all_ok = all(v["fyers_ltp"] is not None for v in results.values())
    print(f"\n    Tick mapping: {'ALL PASS' if all_ok else 'SOME MISSING — check market hours'}")
    return results


def check_trigger_firing():
    """
    Register a dummy TickWatcher trigger and verify it fires when a tick arrives.
    """
    print("\n[5] TickWatcher trigger test...")
    from core.tick_watcher import get_watcher, TRIGGER_TP_LONG
    from scanner import websocket_feed

    fired = []
    watcher = get_watcher()

    # Get current NIFTY price
    tick = websocket_feed.get_latest_tick("NSE:NIFTY50-FUT")
    ltp = tick.get("ltp")
    if not ltp:
        print("    SKIP — no NIFTY tick yet (check market hours)")
        return None

    # Register trigger 1pt above current price
    level = ltp + 1
    watcher.watch(
        "TEST_TRIGGER_001",
        "NSE:NIFTY50-FUT",
        TRIGGER_TP_LONG,
        level,
        lambda p: fired.append(p),
    )
    print(f"    Registered trigger: NIFTY >= {level:.1f} (current LTP={ltp:.1f})")
    print("    Waiting 30s for price to move 1pt...")
    time.sleep(30)

    if fired:
        print(f"    PASS — trigger fired! payload={fired[0]}")
    else:
        watcher.cancel("TEST_TRIGGER_001")
        print("    INCONCLUSIVE — price didn't move 1pt in 30s (try again during volatile session)")

    return bool(fired)


def watch_ticks(duration_s: int = 300):
    """Stream live ticks for duration_s seconds and print each one."""
    from data.truedata_feed import get_manager, _TD_TO_FYERS
    from scanner import websocket_feed

    td = get_manager()
    if not td.is_live_ready:
        print("Connecting live feed...")
        td.connect_live(list(_TD_TO_FYERS.keys()))
        time.sleep(3)

    print(f"\nWatching ticks for {duration_s}s (Ctrl+C to stop)...\n")
    deadline = time.monotonic() + duration_s
    last_ltps = {}
    try:
        while time.monotonic() < deadline:
            time.sleep(0.5)
            for fyers_sym in ["NSE:NIFTY50-FUT", "NSE:NIFTYBANK-FUT", "NSE:FINNIFTY-FUT", "NSE:MIDCPNIFTY-FUT"]:
                t = websocket_feed.get_latest_tick(fyers_sym)
                ltp = t.get("ltp")
                if ltp and ltp != last_ltps.get(fyers_sym):
                    last_ltps[fyers_sym] = ltp
                    ts = _ist_now().strftime("%H:%M:%S")
                    print(f"  {ts}  {fyers_sym:25s}  {ltp:>10.2f}")
    except KeyboardInterrupt:
        pass
    print("\nWatch ended.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="Stream live ticks")
    parser.add_argument("--duration", type=int, default=300, help="Watch duration seconds")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    now = _ist_now()
    print(f"CB6 Quantum — TrueData Live Session Validator")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"Market {'OPEN' if _IST_OPEN <= (now.hour, now.minute) <= _IST_CLOSE else 'CLOSED (results may be inconclusive)'}")

    if args.watch:
        check_auth()
        check_live_connection()
        watch_ticks(args.duration)
        return

    ok_auth  = check_auth()
    ok_hist  = check_historical()
    ok_live  = check_live_connection()
    ok_ticks = check_tick_symbols(15)
    ok_trig  = check_trigger_firing()

    print("\n" + "=" * 50)
    print("LIVE SESSION CHECKLIST SUMMARY")
    print("=" * 50)
    print(f"  Auth + historical connect : {'✅' if ok_auth and ok_hist else '❌'}")
    print(f"  Live WS connected          : {'✅' if ok_live else '❌'}")
    print(f"  Tick symbol mapping        : {'✅' if ok_ticks else '⚠️  check output above'}")
    print(f"  TickWatcher trigger fires  : {'✅' if ok_trig else '⚠️  inconclusive'}")
    print()
    if ok_auth and ok_hist and ok_live:
        print("  ✅ TrueData is live and healthy. Data layer is stable.")
    else:
        print("  ❌ Issues found — review output above before trading.")


if __name__ == "__main__":
    main()
