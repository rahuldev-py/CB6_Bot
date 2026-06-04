import warnings
warnings.filterwarnings('ignore')
import pandas as pd
import yfinance as yf

sym = '^NSEI'

h1  = yf.download(sym, period='10d', interval='1h',  progress=False, auto_adjust=True)
m15 = yf.download(sym, period='5d',  interval='15m', progress=False, auto_adjust=True)
m5  = yf.download(sym, period='2d',  interval='5m',  progress=False, auto_adjust=True)

def flatten(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    return df.dropna()

h1  = flatten(h1).tail(30)
m15 = flatten(m15).tail(50)
m5  = flatten(m5).tail(50)

cmp = float(m5['close'].iloc[-1])
print(f"\nNIFTY CMP : {cmp:.2f}")

# ── 1H levels ──────────────────────────────────────────────────────────────────
h1_high10 = float(h1['high'].tail(10).max())
h1_low10  = float(h1['low'].tail(10).min())
h1_high5  = float(h1['high'].tail(5).max())
h1_low5   = float(h1['low'].tail(5).min())

print(f"\n{'─'*50}")
print(f"1H  (last 10 bars)  High : {h1_high10:.2f}  Low : {h1_low10:.2f}")
print(f"1H  (last 5 bars)   High : {h1_high5:.2f}  Low : {h1_low5:.2f}")
print("\n1H  Last 5 candles:")
for ts, row in h1.tail(5).iterrows():
    bias = "BULL" if row['close'] > row['open'] else "BEAR"
    print(f"  {ts.strftime('%d-%b %H:%M')}  O={row['open']:.0f}  H={row['high']:.0f}  L={row['low']:.0f}  C={row['close']:.0f}  [{bias}]")

# ── 15m levels ─────────────────────────────────────────────────────────────────
m15_high20 = float(m15['high'].tail(20).max())
m15_low20  = float(m15['low'].tail(20).min())
m15_high8  = float(m15['high'].tail(8).max())
m15_low8   = float(m15['low'].tail(8).min())

print(f"\n{'─'*50}")
print(f"15m (last 20 bars)  High : {m15_high20:.2f}  Low : {m15_low20:.2f}")
print(f"15m (last 8 bars)   High : {m15_high8:.2f}  Low : {m15_low8:.2f}")
print("\n15m Last 6 candles:")
for ts, row in m15.tail(6).iterrows():
    bias = "BULL" if row['close'] > row['open'] else "BEAR"
    print(f"  {ts.strftime('%d-%b %H:%M')}  O={row['open']:.0f}  H={row['high']:.0f}  L={row['low']:.0f}  C={row['close']:.0f}  [{bias}]")

# ── 5m levels ──────────────────────────────────────────────────────────────────
m5_high20 = float(m5['high'].tail(20).max())
m5_low20  = float(m5['low'].tail(20).min())
m5_high6  = float(m5['high'].tail(6).max())
m5_low6   = float(m5['low'].tail(6).min())

print(f"\n{'─'*50}")
print(f"5m  (last 20 bars)  High : {m5_high20:.2f}  Low : {m5_low20:.2f}")
print(f"5m  (last 6 bars)   High : {m5_high6:.2f}  Low : {m5_low6:.2f}")
print("\n5m  Last 8 candles:")
for ts, row in m5.tail(8).iterrows():
    bias = "BULL" if row['close'] > row['open'] else "BEAR"
    print(f"  {ts.strftime('%d-%b %H:%M')}  O={row['open']:.0f}  H={row['high']:.0f}  L={row['low']:.0f}  C={row['close']:.0f}  [{bias}]")

# ── Key levels summary ─────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print("KEY LEVELS SUMMARY")
print(f"{'='*50}")
print(f"RESISTANCE (SELL above) :")
print(f"  R1 (1H swing high)   : {h1_high5:.2f}")
print(f"  R2 (1H 10-bar high)  : {h1_high10:.2f}")
print(f"  R3 (15m 20-bar high) : {m15_high20:.2f}")
print(f"\nSUPPORT (BUY below) :")
print(f"  S1 (1H swing low)    : {h1_low5:.2f}")
print(f"  S2 (1H 10-bar low)   : {h1_low10:.2f}")
print(f"  S3 (15m 20-bar low)  : {m15_low20:.2f}")
print(f"\nCMP {cmp:.2f}  |  Range (5m 20-bar): {m5_low20:.2f} – {m5_high20:.2f}  ({m5_high20-m5_low20:.0f} pts)")
if cmp > (m5_high20 + m5_low20) / 2:
    print("BIAS : Upper half of range → watch for SELL from resistance or BUY on pullback to mid")
else:
    print("BIAS : Lower half of range → watch for BUY from support or SELL on bounce to mid")
