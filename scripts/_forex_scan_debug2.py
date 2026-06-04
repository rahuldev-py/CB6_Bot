"""Find how many bars have DOL/MSS alignment across full XAUUSD dataset."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv; load_dotenv()
import logging; logging.disable(logging.CRITICAL)

import MetaTrader5 as mt5, pandas as pd

mt5.initialize(path=os.getenv('MT5_TERMINAL_GFT',''), login=int(os.getenv('GFT_2STEP_LOGIN',0)),
               password=os.getenv('GFT_2STEP_PASSWORD',''), server=os.getenv('GFT_2STEP_SERVER',''))
mt5.symbol_select('XAUUSD.x', True)
rates = mt5.copy_rates_from_pos('XAUUSD.x', mt5.TIMEFRAME_M3, 0, 50000)
mt5.shutdown()

df = pd.DataFrame(rates)
df['timestamp'] = pd.to_datetime(df['time'], unit='s', utc=True)
df['ts'] = df['timestamp'].dt.tz_localize(None)
if 'tick_volume' in df.columns: df = df.rename(columns={'tick_volume':'volume'})
df = df[['timestamp','ts','open','high','low','close','volume']].copy()
df = df[(df.ts.dt.hour >= 7) & (df.ts.dt.hour < 20)].reset_index(drop=True)
print(f"Full dataset: {len(df)} bars | {df.ts.iloc[0].date()} -> {df.ts.iloc[-1].date()}")

from scanner.silver_bullet import find_draw_on_liquidity, detect_sb_mss, get_day_extremes, detect_sb_fvg

WINDOW = 150
aligned = 0
dol_none = 0
mss_none = 0
mismatch = 0
no_fvg   = 0
setups   = 0

# Sample every 10 bars to speed up
sample_step = 5
for i in range(WINDOW, len(df), sample_step):
    win = df.iloc[i-WINDOW:i+1][['timestamp','open','high','low','close','volume']].copy()
    win.index = pd.to_datetime(win['timestamp'])
    win.index = win.index.tz_convert('Asia/Kolkata')

    day_ext = get_day_extremes(win)
    dol = find_draw_on_liquidity(win, wick_sweep=True)
    if dol is None and day_ext:
        last_c = float(win['close'].iloc[-1])
        if day_ext['high'] > last_c:
            dol = {'type':'HOD','level':day_ext['high'],'direction':'BULLISH'}
        elif day_ext['low'] < last_c:
            dol = {'type':'LOD','level':day_ext['low'],'direction':'BEARISH'}

    if dol is None: dol_none += 1; continue

    mss = detect_sb_mss(win)
    if mss is None: mss_none += 1; continue

    if mss['direction'] != dol['direction']: mismatch += 1; continue

    aligned += 1
    # Check for FVG
    fvg = detect_sb_fvg(win, direction=dol['direction'])
    if fvg is None: no_fvg += 1
    else: setups += 1

total_sampled = (len(df) - WINDOW) // sample_step
print(f"\nSampled {total_sampled} windows (every {sample_step} bars):")
print(f"  DOL=None      : {dol_none} ({dol_none/total_sampled*100:.1f}%)")
print(f"  MSS=None      : {mss_none} ({mss_none/total_sampled*100:.1f}%)")
print(f"  Dir mismatch  : {mismatch} ({mismatch/total_sampled*100:.1f}%)")
print(f"  Aligned(DOL+MSS): {aligned} ({aligned/total_sampled*100:.1f}%)")
print(f"  Aligned but no FVG: {no_fvg}")
print(f"  Full setups (DOL+MSS+FVG): {setups} ({setups/total_sampled*100:.1f}%)")
