"""
NIFTY live LTP monitor — polls Fyers API every 15 seconds permanently.
Tracks: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY (active futures contracts)
Run:  python scripts/nifty_ltp_monitor.py
Stop: Ctrl+C

Requires valid Fyers ACCESS_TOKEN in .env  (run: python auto_token.py)
"""
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import dotenv_values
from fyers_apiv3 import fyersModel
from scanner.index_futures import get_active_futures

INTERVAL = 15   # seconds
IST      = timedelta(hours=5, minutes=30)

# NSE market hours (IST)
MARKET_OPEN  = (9, 15)   # 9:15 AM
MARKET_CLOSE = (15, 30)  # 3:30 PM

# Silver Bullet windows IST
SB_WINDOWS = [
    ((10,  0), (11,  0), "SB-1"),
    ((13,  0), (14,  0), "SB-2"),
    ((15,  0), (15, 30), "SB-3"),
]


def _ist_now():
    return datetime.now(timezone.utc) + IST


def _market_open(ist: datetime) -> bool:
    t = (ist.hour, ist.minute)
    return MARKET_OPEN <= t < MARKET_CLOSE


def _active_sb_window(ist: datetime) -> str | None:
    t = (ist.hour, ist.minute)
    for start, end, label in SB_WINDOWS:
        if start <= t < end:
            return label
    return None


def _time_to_next_window(ist: datetime) -> str:
    t = (ist.hour, ist.minute)
    for start, end, label in SB_WINDOWS:
        if t < start:
            mins = (start[0] - ist.hour) * 60 + (start[1] - ist.minute)
            return f"{label} opens in {mins}m"
    return "No more SB windows today"


def init_fyers():
    env        = dotenv_values(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
    token      = env.get('ACCESS_TOKEN', '')
    client_id  = env.get('CLIENT_ID', '')
    if not token or not client_id:
        print("[ERROR] ACCESS_TOKEN or CLIENT_ID missing in .env — run: python auto_token.py")
        sys.exit(1)
    fyers = fyersModel.FyersModel(client_id=client_id, token=token, is_async=False, log_path='')
    # Quick validation
    resp = fyers.quotes({'symbols': 'NSE:NIFTY26JUNFUT'})
    if resp.get('code') == -15 or resp.get('s') == 'error':
        print(f"[ERROR] Fyers token invalid — run: python auto_token.py\n  Response: {resp.get('message')}")
        sys.exit(1)
    print(f"[Fyers] Connected — client: {client_id}")
    return fyers


def fetch_quotes(fyers, symbols: dict) -> dict:
    """Returns {index: {ltp, bid, ask, chg, chg_pct, high, low}} or empty on error."""
    sym_str = ','.join(symbols.values())
    try:
        resp = fyers.quotes({'symbols': sym_str})
        if resp.get('code') not in (200, None) and resp.get('s') != 'ok':
            return {}
        results = {}
        for item in resp.get('d', []):
            name = item.get('n', '')
            v    = item.get('v', {})
            # Resolve which index this symbol belongs to
            for idx, sym in symbols.items():
                if sym in name or name in sym:
                    results[idx] = {
                        'ltp'     : v.get('lp',         0),
                        'bid'     : v.get('bid',        v.get('lp', 0)),
                        'ask'     : v.get('ask',        v.get('lp', 0)),
                        'high'    : v.get('high_price', 0),
                        'low'     : v.get('low_price',  0),
                        'chg'     : v.get('ch',         0),
                        'chg_pct' : v.get('chp',        0),
                        'volume'  : v.get('volume',     0),
                    }
                    break
        return results
    except Exception as e:
        print(f"[ERROR] Quote fetch failed: {e}")
        return {}


def _arrow(chg: float) -> str:
    if chg > 0:
        return f"▲ +{chg:.2f}"
    elif chg < 0:
        return f"▼ {chg:.2f}"
    return "—"


def main():
    print("=" * 70)
    print("  CB6 Quantum — NIFTY Live Monitor  |  15s polls  |  Ctrl+C to stop")
    print("=" * 70)

    fyers   = init_fyers()
    symbols = get_active_futures()
    # Only track the 4 active indices
    symbols = {k: v for k, v in symbols.items() if k in ('NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY')}
    print(f"[Tracking] {', '.join(symbols.values())}\n")

    prev_ltp = {}

    while True:
        try:
            ist  = _ist_now()
            utc  = datetime.now(timezone.utc)
            ts   = f"[{utc.strftime('%H:%M:%S')} UTC | {ist.strftime('%H:%M:%S')} IST]"

            if not _market_open(ist):
                print(f"{ts}  Market CLOSED  ({_time_to_next_window(ist)})")
                time.sleep(INTERVAL)
                continue

            sb   = _active_sb_window(ist)
            ctx  = f"  *** {sb} ACTIVE ***" if sb else f"  ({_time_to_next_window(ist)})"
            quotes = fetch_quotes(fyers, symbols)

            if not quotes:
                print(f"{ts}  No data returned — retrying...")
                time.sleep(INTERVAL)
                continue

            print(f"{ts}{ctx}")
            print(f"  {'INDEX':<14} {'LTP':>10} {'BID':>10} {'ASK':>10}  {'CHANGE':>12}  {'H':>10} {'L':>10}")
            print(f"  {'-'*80}")
            for idx in ('NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'):
                if idx not in quotes:
                    continue
                q    = quotes[idx]
                ltp  = q['ltp']
                chg  = ltp - prev_ltp.get(idx, ltp)
                tick = f"▲ +{chg:.2f}" if chg > 0 else (f"▼ {chg:.2f}" if chg < 0 else " —")
                print(
                    f"  {idx:<14} {ltp:>10.2f} {q['bid']:>10.2f} {q['ask']:>10.2f}"
                    f"  {_arrow(q['chg']):>12}  {q['high']:>10.2f} {q['low']:>10.2f}"
                    f"  {tick}"
                )
                prev_ltp[idx] = ltp
            print()

            time.sleep(INTERVAL)

        except KeyboardInterrupt:
            print("\n[STOPPED] NIFTY monitor shut down.")
            break
        except Exception as e:
            print(f"[ERROR] {e} — retrying in {INTERVAL}s")
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
