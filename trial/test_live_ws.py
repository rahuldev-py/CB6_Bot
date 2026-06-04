"""Quick test for TrueData live WebSocket connection."""
import logging, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from truedata_ws.websocket.TD import TD

print("Testing live WebSocket connection to port 8086...")
td = TD("Trial119", "rahul119", live_port=8086, log_level=logging.INFO)

time.sleep(5)
ws = td.live_websocket
print(f"live_websocket connected: {ws is not None}")
if ws:
    print(f"  remaining_symbols: {getattr(ws, 'remaining_symbols', 'N/A')}")
    print(f"  max_symbols:       {getattr(ws, 'max_symbols', 'N/A')}")
    print(f"  segments:          {getattr(ws, 'segments', 'N/A')}")
    print(f"  valid_until:       {getattr(ws, 'valid_until', 'N/A')}")
    print(f"  subscription_type: {getattr(ws, 'subscription_type', 'N/A')}")

    print("\nSubscribing to 4 indices...")
    req_ids = td.start_live_data(["NIFTY-I", "BANKNIFTY-I", "FINNIFTY-I", "MIDCPNIFTY-I"])
    print(f"  req_ids: {req_ids}")
    time.sleep(3)
    print(f"  live_data after sub: {td.live_data}")
else:
    print("  WS did not connect (expected after market hours)")

print("\nOption chain test...")
try:
    chain = td.get_option_chain("NIFTY", expiry="2026-06-05", num_strikes=5)
    if chain:
        print(f"  Option chain PASS: {len(chain)} rows")
        print(f"  Sample: {chain[0] if chain else None}")
    else:
        print(f"  Option chain: empty/unavailable (after hours)")
except Exception as e:
    print(f"  Option chain error: {e}")
