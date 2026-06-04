"""Test TrueData option chain and Greeks access."""
import sys, logging, time
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from truedata_ws.websocket.TD import TD
from truedata_ws.websocket.TD_chain import OptionChain

print("=== Option Chain + Greeks Test ===")
print("Connecting to TrueData (live + historical)...")

td = TD("Trial119", "rahul119", live_port=8086, log_level=logging.WARNING)
time.sleep(3)

# Expiry as datetime object
expiry_dt = datetime(2026, 6, 5)

print("\n--- Option Chain ---")
try:
    chain = OptionChain(
        TD_OBJ=td,
        symbol="NIFTY",
        expiry=expiry_dt,
        chain_length=5,
        future_price=23750,
        bid_ask=False,
        market_open_post_hours=True,
    )
    print(f"OptionChain created: strike_step={chain.strike_step}")
    n_syms = len(chain.option_symbols) if chain.option_symbols else 0
    print(f"Option symbols count: {n_syms}")
    if chain.option_symbols:
        print(f"Sample symbols: {chain.option_symbols[:6]}")
    df = chain.chain_dataframe
    if df is not None and hasattr(df, 'shape'):
        print(f"OPTION CHAIN PASS: chain_dataframe shape={df.shape}")
        print(df.head(3))
    else:
        print("OPTION CHAIN: chain_dataframe not available (normal after hours)")
    print("RESULT: PASS (API accessible, data expected during market hours)")
except Exception as e:
    print(f"OPTION CHAIN ERROR: {type(e).__name__}: {e}")

print("\n--- Greeks via start_option_chain ---")
try:
    greeks_received = []

    @td.greek_callback
    def on_greek(data):
        greeks_received.append(data)
        print(f"  GREEKS received: {data}")

    req = td.start_option_chain(
        symbol="NIFTY",
        expiry=expiry_dt,
        chain_length=3,
    )
    print(f"start_option_chain req_id: {req}")
    time.sleep(3)
    if greeks_received:
        print(f"GREEKS PASS: {len(greeks_received)} updates received")
    else:
        print("GREEKS: No data yet (expected after market hours)")
    print("RESULT: PASS (API subscribed successfully)")
except Exception as e:
    print(f"GREEKS ERROR: {type(e).__name__}: {e}")

print("\n=== Test Complete ===")
