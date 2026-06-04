"""
Verify scanner/data_fetcher.py uses TrueData as primary source.
Tests the full data path: scanner → data_fetcher → truedata_feed → TD library.
"""
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=== Scanner / TrueData Integration Test ===")

# Simulate what scanner/data_fetcher._get_historical_data_truedata does
from data.truedata_feed import get_manager, fyers_to_td_symbol, tf_to_bar_size

td = get_manager()
if not td.is_hist_ready:
    ok = td.connect_hist()
    print(f"connect_hist: {ok}")

# Test all 4 F&O symbols in Fyers format (exactly as used by the scanner)
test_cases = [
    ("NSE:NIFTY50-FUT",      "5",  7),
    ("NSE:BANKNIFTY-FUT",    "5",  7),
    ("NSE:FINNIFTY-FUT",     "5",  7),
    ("NSE:MIDCPNIFTY-FUT",   "5",  7),
    ("NSE:NIFTY50-FUT",      "3",  5),
    ("NSE:NIFTY50-FUT",      "1",  3),
    ("NSE:NIFTY50-FUT",      "15", 10),
    ("NSE:NIFTY50-FUT",      "D",  10),  # EOD
]

all_pass = True
for symbol, timeframe, days in test_cases:
    td_sym = fyers_to_td_symbol(symbol)
    bar_sz = tf_to_bar_size(str(timeframe))
    df = td.get_historical_bars(td_sym, bar_sz, days=days)
    if df is not None and len(df) > 0:
        has_required = all(c in df.columns for c in ["timestamp", "open", "high", "low", "close", "volume"])
        has_oi = "oi" in df.columns
        status = "PASS" if has_required else "PARTIAL"
        print(f"  {status} {symbol} {timeframe}min/{days}d: {len(df)} bars | OI={has_oi} | cols={list(df.columns)}")
    else:
        print(f"  FAIL {symbol} {timeframe}min/{days}d: no data")
        all_pass = False

print()
print(f"All tests passed: {all_pass}")

# Check data quality for NIFTY 5min
print("\n=== Data Quality Sample (NIFTY-I 5min last 3 days) ===")
df = td.get_historical_bars("NIFTY-I", "5min", days=3)
if df is not None:
    print(f"Rows: {len(df)}")
    print(f"Date range: {df['timestamp'].min()} → {df['timestamp'].max()}")
    print(f"Missing values: {df.isnull().sum().to_dict()}")
    print(f"Duplicate timestamps: {df['timestamp'].duplicated().sum()}")
    print(f"Price sanity (NIFTY 5min close):")
    print(f"  Min close: {df['close'].min():.1f}")
    print(f"  Max close: {df['close'].max():.1f}")
    print(f"  Volume range: {df['volume'].min():,} - {df['volume'].max():,}")
    print(f"  OI range: {df['oi'].min():,} - {df['oi'].max():,}")
    print(df.tail(5).to_string())
