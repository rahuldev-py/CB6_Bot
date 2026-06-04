"""Debug why forex scanner returns 0 trades."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv; load_dotenv()
import logging
logging.basicConfig(level=logging.INFO)

import MetaTrader5 as mt5
import pandas as pd

mt5.initialize(path=os.getenv('MT5_TERMINAL_GFT',''), login=int(os.getenv('GFT_2STEP_LOGIN',0)), password=os.getenv('GFT_2STEP_PASSWORD',''), server=os.getenv('GFT_2STEP_SERVER',''))
mt5.symbol_select('XAUUSD.x', True)

rates = mt5.copy_rates_from_pos('XAUUSD.x', mt5.TIMEFRAME_M3, 0, 300)
df = pd.DataFrame(rates)
df['timestamp'] = pd.to_datetime(df['time'], unit='s', utc=True)
df['ts'] = df['timestamp'].dt.tz_localize(None)
if 'tick_volume' in df.columns: df = df.rename(columns={'tick_volume':'volume'})
df = df[['timestamp','ts','open','high','low','close','volume']].copy()
df = df[(df.ts.dt.hour >= 7) & (df.ts.dt.hour < 20)].reset_index(drop=True)
mt5.shutdown()

print(f"Sample: {len(df)} bars | {df.ts.iloc[0]} -> {df.ts.iloc[-1]}")
print(f"OHLC range: {df.low.min():.2f} - {df.high.max():.2f}")
print()

# Try different window sizes and max_fvg_pts values
from scanner.silver_bullet import scan_silver_bullet, find_draw_on_liquidity, detect_sb_mss, get_day_extremes

for wi in [150, 200, 250]:
    if wi >= len(df): continue
    win = df.iloc[-wi:][['timestamp','open','high','low','close','volume']].copy()
    print(f"=== Window {wi} bars ===")

    # Test individual components
    try:
        import pandas as pd2
        ww = win.copy()
        ww.index = pd.to_datetime(ww['timestamp'])
        if ww.index.tz is None:
            ww.index = ww.index.tz_localize('Asia/Kolkata')
        else:
            ww.index = ww.index.tz_convert('Asia/Kolkata')

        day_ext = get_day_extremes(ww)
        print(f"  day_ext: {day_ext}")

        dol = find_draw_on_liquidity(ww, wick_sweep=True)
        print(f"  DOL: {dol}")

        mss = detect_sb_mss(ww)
        print(f"  MSS: {mss}")
    except Exception as e:
        print(f"  Component test error: {e}")

    result = scan_silver_bullet(win, 'XAUUSD.x', tf='3', force=True, max_fvg_pts=500.0)
    print(f"  Full scan result: {result is not None}")
    print()
