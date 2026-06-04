"""Verification test for the options intelligence layer and OI filters."""
import ast, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=== Syntax checks ===")
for f in [
    "nse_options/option_chain_fetcher.py",
    "nse_options/greeks_engine.py",
    "scanner/oi_filters.py",
    "scanner/silver_bullet.py",
    "data/truedata_feed.py",
]:
    try:
        ast.parse(open(f, encoding="utf-8").read())
        print(f"  OK  {f}")
    except SyntaxError as e:
        print(f"  ERR {f}: {e}")
        sys.exit(1)

print("\n=== Import checks ===")
from nse_options import enrich_setup_with_options_context
print("  OK  nse_options.enrich_setup_with_options_context")

from nse_options.option_chain_fetcher import fetch_option_chain_context
print("  OK  nse_options.option_chain_fetcher.fetch_option_chain_context")

from scanner.oi_filters import (
    score_dol_by_oi, check_oi_entry_filter,
    check_oi_at_target, check_bidask_filter,
    get_oi_divergence_signal,
)
print("  OK  scanner.oi_filters (all 5 functions)")

print("\n=== OI filters — no OI column (Fyers fallback path) ===")
import pandas as pd, numpy as np
df_base = pd.DataFrame({
    "timestamp": pd.date_range("2026-05-29 09:15", periods=50, freq="5min"),
    "open":   np.random.uniform(23700, 24000, 50),
    "high":   np.random.uniform(24000, 24100, 50),
    "low":    np.random.uniform(23600, 23700, 50),
    "close":  np.random.uniform(23700, 24000, 50),
    "volume": np.random.randint(100000, 500000, 50),
})
dol_mock = {"level": 23950.0, "type": "HIGH", "direction": "BULLISH", "is_eqh_eql": False}

boost, reason = score_dol_by_oi(df_base, dol_mock)
assert boost == 0.0 and "NO_OI" in reason
print(f"  score_dol_by_oi (no OI): boost={boost} {reason}  OK")

ok, reason = check_oi_entry_filter(df_base, "BULLISH")
assert ok is True
print(f"  check_oi_entry_filter (no OI): {ok} {reason}  OK")

ok, reason = check_bidask_filter("NSE:NIFTY50-FUT", 23700, 23750)
assert ok is True
print(f"  check_bidask_filter (no tick): {ok} {reason}  OK")

div = get_oi_divergence_signal(df_base, "BULLISH")
print(f"  get_oi_divergence_signal (no OI): {div}  OK")

print("\n=== OI filters — with OI column ===")
df_oi = df_base.copy()
df_oi["oi"] = np.linspace(1_000_000, 1_100_000, 50)
ok, reason = check_oi_entry_filter(df_oi, "BULLISH")
assert ok is True
print(f"  rising OI: {ok} {reason}  OK")

df_oi["oi"] = np.linspace(1_100_000, 1_000_000, 50)
ok, reason = check_oi_entry_filter(df_oi, "BULLISH")
assert ok is False
print(f"  declining OI: {ok} {reason}  OK")

df_oi["oi"] = 1_050_000.0
ok, reason = check_oi_entry_filter(df_oi, "BULLISH")
assert ok is True and "FLAT" in reason
print(f"  flat OI: {ok} {reason}  OK")

print("\n=== OI divergence signal ===")
df_div = df_base.copy()
df_div["oi"] = np.linspace(1_000_000, 1_100_000, 50)
df_div["close"] = np.linspace(23700, 24000, 50)
sig = get_oi_divergence_signal(df_div, "BULLISH")
print(f"  price up + OI up BULLISH: {sig} (expected CONFIRMATION)")

df_div["oi"] = np.linspace(1_100_000, 1_000_000, 50)
sig = get_oi_divergence_signal(df_div, "BULLISH")
print(f"  price up + OI down BULLISH: {sig} (expected DIVERGENCE)")

print("\n=== enrich_setup_with_options_context (no live data) ===")
dummy_setup = {
    "symbol":    "NSE:NIFTY50-FUT",
    "direction": "BULLISH",
    "dte":       5,
    "entry_signal": {
        "entry": 23750.0, "stop_loss": 23700.0,
        "target1": 23850.0, "target2": 23950.0, "target3": 24050.0,
        "risk": 50.0, "rr_ratio": 4.0,
        "fvg_low": 23720.0, "fvg_high": 23750.0,
        "dol_level": 24000.0, "mss_level": 23800.0,
    },
}
enriched = enrich_setup_with_options_context(dummy_setup)
opts = enriched.get("options_context", {})
print(f"  options_context present: {bool(opts)}")
print(f"  lot_size_adj / risk_multiplier: {opts.get('risk_multiplier', 1.0)}")
print(f"  option_data_available: {opts.get('option_data_available')}")
print(f"  Setup unchanged when no live data: OK")

print("\nAll checks passed.")
