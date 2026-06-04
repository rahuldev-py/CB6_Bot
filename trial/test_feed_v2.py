"""Test the rewritten data/truedata_feed.py with official truedata_ws library."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.truedata_feed import get_manager, get_historical_bars, fyers_to_td_symbol, tf_to_bar_size

print("=== Testing rewritten data/truedata_feed.py ===")
td = get_manager()

ok = td.connect_hist()
print(f"connect_hist(): {ok}  is_hist_ready={td.is_hist_ready}")

symbols = ["NIFTY-I", "BANKNIFTY-I", "FINNIFTY-I", "MIDCPNIFTY-I"]
for sym in symbols:
    df = td.get_historical_bars(sym, "5min", days=5)
    if df is not None and len(df) > 0:
        cols = list(df.columns)
        first_ts = df.iloc[0]["timestamp"]
        last_ts = df.iloc[-1]["timestamp"]
        print(f"  PASS {sym} 5min: {len(df)} bars | cols={cols} | {first_ts} → {last_ts}")
    else:
        print(f"  FAIL {sym} 5min: NO DATA")

print()
print("=== Fyers-format wrapper test ===")
fyers_tests = [
    ("NSE:NIFTY50-FUT", "3"),
    ("NSE:BANKNIFTY-FUT", "5"),
    ("NSE:FINNIFTY-FUT", "1"),
    ("NSE:MIDCPNIFTY-FUT", "15"),
]
for fsym, tf in fyers_tests:
    df = get_historical_bars(fsym, tf, days=3)
    if df is not None and len(df) > 0:
        print(f"  PASS {fsym} {tf}min: {len(df)} bars via wrapper")
    else:
        print(f"  FAIL {fsym} {tf}min via wrapper")

print()
print("=== Symbol map check ===")
fyers_syms = list([
    "NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX",
    "NSE:FINNIFTY-INDEX", "NSE:MIDCPNIFTY-INDEX",
    "NSE:NIFTY50-FUT", "NSE:BANKNIFTY-FUT",
])
for s in fyers_syms:
    print(f"  {s} -> {fyers_to_td_symbol(s)}")
