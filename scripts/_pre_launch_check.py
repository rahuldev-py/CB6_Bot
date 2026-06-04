"""Pre-launch sanity check — run before starting the bot."""
import os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
env = dotenv_values(ROOT / ".env")
for k, v in env.items():
    if k not in os.environ:
        os.environ[k] = v

IST = timezone(timedelta(hours=5, minutes=30))
now_utc = datetime.now(timezone.utc)
now_ist = datetime.now(IST)

print("=" * 55)
print("  CB6 QUANTUM — PRE-LAUNCH CHECKLIST")
print("=" * 55)
print(f"  UTC  : {now_utc.strftime('%H:%M')}  |  IST: {now_ist.strftime('%H:%M')}")
print()

# ── 1. Forex config ────────────────────────────────────────────────────────────
print("[FOREX]")
from forex_engine.forex_worker import (
    ACTIVE_SYMBOLS, INTERVAL, KILL_ZONE_WINDOWS,
    MIN_SCORE, MIN_RR, PAPER, CANDLE_LIMIT
)
print(f"  Symbols    : {ACTIVE_SYMBOLS}")
print(f"  Interval   : {INTERVAL}  |  Candles: {CANDLE_LIMIT}")
print(f"  Sessions   : {KILL_ZONE_WINDOWS}")
print(f"  Min Score  : {MIN_SCORE}  |  Min RR: {MIN_RR}")
print(f"  PAPER (FTMO): {PAPER}  {'⚠️ PAPER MODE' if PAPER else '✅ LIVE'}")

gft_paper = env.get("GFT_2STEP_PAPER", "true").lower() == "true"
print(f"  PAPER (GFT) : {gft_paper}  {'⚠️ PAPER MODE' if gft_paper else '✅ LIVE'}")

in_session = any(s <= now_utc.hour < e for s, e in KILL_ZONE_WINDOWS)
print(f"  In session now: {'✅ YES' if in_session else f'No — next: London {KILL_ZONE_WINDOWS[0][0]}:00 UTC'}")

# ── 2. Forex paper_mode in main trade paths ────────────────────────────────────
print()
print("[NSE paper_mode check]")
main_src = (ROOT / "main.py").read_text(encoding="utf-8", errors="replace")
if "paper_mode=True" in main_src:
    count = main_src.count("paper_mode=True")
    print(f"  ❌ paper_mode=True found {count}x in main.py — orders will NOT fire")
else:
    print("  ✅ No paper_mode=True in live execution paths")

# ── 3. Fyers token ─────────────────────────────────────────────────────────────
print()
print("[FYERS TOKEN]")
try:
    from fyers_apiv3 import fyersModel
    client_id    = env.get("CLIENT_ID", "")
    access_token = env.get("ACCESS_TOKEN", "")
    token_val    = access_token.split(":", 1)[1] if ":" in access_token else access_token
    fy = fyersModel.FyersModel(client_id=client_id, token=token_val, is_async=False, log_path="")
    profile = fy.get_profile()
    if isinstance(profile, dict) and profile.get("code") == 200:
        name = profile.get("data", {}).get("name", "?")
        print(f"  ✅ Token valid — {name}")
    else:
        print(f"  ❌ Token invalid: {profile}")
except Exception as e:
    print(f"  ❌ Fyers error: {e}")

# ── 4. NSE SB window ───────────────────────────────────────────────────────────
print()
print("[NSE SB WINDOWS]")
try:
    from scanner.silver_bullet import get_window_status, is_silver_bullet_window
    status = get_window_status()
    in_win, win_name = is_silver_bullet_window()
    print(f"  Status: {status}")
    print(f"  In window now: {'✅ ' + win_name if in_win else 'No'}")
except Exception as e:
    print(f"  ⚠ Could not check: {e}")

# ── 5. MT5 connection ──────────────────────────────────────────────────────────
print()
print("[MT5 — FTMO]")
try:
    import MetaTrader5 as mt5
    ok = mt5.initialize()
    if ok:
        ai = mt5.account_info()
        print(f"  ✅ Connected: {ai.company}  acc={ai.login}")
        print(f"     Balance: ${ai.balance:,.2f}  Equity: ${ai.equity:,.2f}")
        positions = mt5.positions_get()
        print(f"     Open positions: {len(positions) if positions else 0}")
        mt5.shutdown()
    else:
        print(f"  ❌ MT5 init failed: {mt5.last_error()}")
except Exception as e:
    print(f"  ❌ MT5 error: {e}")

print()
print("=" * 55)
