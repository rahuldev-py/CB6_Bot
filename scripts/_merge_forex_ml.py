"""
Merge forex MT5 backtest trades into NSE ML training CSV.
- Reads the latest full forex backtest CSV from data/backtests/forex_mt5/results/
- Normalises column names to match bt_combined_2024_2026.csv schema
- Appends to bt_combined_2024_2026.csv → bt_combined_2024_2026_with_forex.csv
- Also overwrites ml/training_data/bt_forex_mt5.csv with the latest full run

Usage: python _merge_forex_ml.py
"""
import pandas as pd
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "data" / "backtests" / "forex_mt5" / "results"
NSE_CSV     = ROOT / "ml" / "training_data" / "bt_combined_2024_2026.csv"
ML_DIR      = ROOT / "ml" / "training_data"
OUT_CSV     = ML_DIR / "bt_combined_2024_2026_with_forex.csv"

# ── Find latest full forex CSV (largest file = all 3 symbols) ─────────────────
candidates = sorted(RESULTS_DIR.glob("bt_forex_mt5_*.csv"),
                    key=lambda p: p.stat().st_size, reverse=True)
if not candidates:
    print("ERROR: No forex backtest CSVs found in", RESULTS_DIR); exit(1)

forex_csv = candidates[0]
print(f"Using forex CSV: {forex_csv.name}  ({forex_csv.stat().st_size//1024}KB)")

forex = pd.read_csv(forex_csv)
print(f"Forex trades loaded: {len(forex)}")
print(f"Symbols: {forex['symbol'].value_counts().to_dict()}")

# ── Normalise forex columns to NSE schema ────────────────────────────────────
forex_ml = pd.DataFrame()

# Direct mappings
forex_ml["index"]      = forex["symbol"]          # XAUUSD/XAGUSD/USOIL
forex_ml["date"]       = forex["date"]
forex_ml["time"]       = forex["time"]
forex_ml["hold_mins"]  = forex["hold_mins"]
forex_ml["dir"]        = forex["dir"]              # LONG / SHORT
forex_ml["entry"]      = forex["entry"]
forex_ml["sl"]         = forex["stop_loss"]
forex_ml["t1"]         = forex["target1"]
forex_ml["t2"]         = forex["target2"]
forex_ml["t3"]         = forex["target3"]
forex_ml["risk_pts"]   = forex["risk_pts"]
forex_ml["exit_price"] = forex["exit_price"]
forex_ml["outcome"]    = forex["outcome"]
forex_ml["r"]          = forex["r"]
forex_ml["score"]      = forex["score"]
forex_ml["mss"]        = forex["mss"]
forex_ml["hour"]       = forex["hour"]
forex_ml["weekday"]    = forex["weekday"]
forex_ml["session"]    = forex["session"]

# Derived columns
forex_ml["win"]        = (forex["r"] > 0).astype(int)
forex_ml["minute"]     = pd.to_datetime(forex["time"], format="%H:%M").dt.minute
forex_ml["year"]       = pd.to_datetime(forex["date"]).dt.year
forex_ml["month"]      = pd.to_datetime(forex["date"]).dt.month

# Columns that don't exist in forex data — fill with sensible defaults
forex_ml["regime"]     = "NEUTRAL"       # no H4 regime stored in forex backtest
forex_ml["fvg_size"]   = 0.0             # not stored per-trade in bt output
forex_ml["fvg_top"]    = forex["entry"]  # proxy: entry price
forex_ml["fvg_bottom"] = forex["stop_loss"]
forex_ml["exit_time"]  = ""
forex_ml["period"]     = "MT5_15m"

# Ensure column order matches NSE CSV
nse = pd.read_csv(NSE_CSV)
all_cols = list(nse.columns)
for c in all_cols:
    if c not in forex_ml.columns:
        forex_ml[c] = None
forex_ml = forex_ml[all_cols]

print(f"\nForex ML rows after mapping: {len(forex_ml)}")
print(f"Win rate: {forex_ml['win'].mean()*100:.1f}%  |  Avg R: {forex_ml['r'].mean():.3f}")

# ── Fix the ml/training_data/bt_forex_mt5.csv to have the full run ──────────
forex.to_csv(ML_DIR / "bt_forex_mt5.csv", index=False)
print(f"Updated ml/training_data/bt_forex_mt5.csv  ({len(forex)} rows)")

# ── Merge NSE + Forex ────────────────────────────────────────────────────────
combined = pd.concat([nse, forex_ml], ignore_index=True)
combined.to_csv(OUT_CSV, index=False)
print(f"\nCombined CSV saved: {OUT_CSV.name}")
print(f"  NSE rows   : {len(nse)}")
print(f"  Forex rows : {len(forex_ml)}")
print(f"  Total rows : {len(combined)}")
print(f"  Overall WR : {combined['win'].mean()*100:.1f}%")
print(f"  Symbols    : {combined['index'].value_counts().to_dict()}")
