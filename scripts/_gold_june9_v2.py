"""
XAUUSD June 9 — full session, wide fetch to capture all bars including 4236.74 low.
"""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone, timedelta

IST = timedelta(hours=5, minutes=30)
SYMBOL = 'XAUUSD.x'

mt5.initialize()
mt5.symbol_select(SYMBOL, True)

# Fetch wide — June 9 00:00 to June 10 06:00 server time (covers any timezone offset)
rates = mt5.copy_rates_range(
    SYMBOL,
    mt5.TIMEFRAME_M5,
    datetime(2026, 6, 9, 0, 0),
    datetime(2026, 6, 10, 6, 0),
)
mt5.shutdown()

df = pd.DataFrame(rates)
df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
df['ist']  = df['time'] + IST
df = df.set_index('time')

# Show full day range
print(f"Full data: {df['ist'].iloc[0].strftime('%d-%b %H:%M IST')} → {df['ist'].iloc[-1].strftime('%d-%b %H:%M IST')}")
print(f"Total bars: {len(df)}")
print(f"Day high: {df['high'].max():.2f}  Day low: {df['low'].min():.2f}")

# Find the 4236.74 low
low_bar = df[df['low'] <= 4240]
if not low_bar.empty:
    print(f"\nBars below 4240:")
    for i, row in low_bar.iterrows():
        print(f"  {row['ist'].strftime('%d-%b %H:%M IST')}  Low:{row['low']:.2f}  Close:{row['close']:.2f}")

# Filter to user's window: 11:30 AM IST to 7:30 PM IST on June 9
win_start = datetime(2026, 6, 9, 6, 0, tzinfo=timezone.utc)   # 11:30 IST
win_end   = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)  # 19:30 IST
mask = (df.index >= win_start) & (df.index <= win_end)
df_win = df[mask].copy()

if df_win.empty:
    # Try with IST offset applied differently
    win_start2 = pd.Timestamp('2026-06-09 11:30:00') + pd.Timedelta(hours=-5, minutes=-30)
    win_end2   = pd.Timestamp('2026-06-09 19:30:00') + pd.Timedelta(hours=-5, minutes=-30)
    mask2 = (df['ist'] >= pd.Timestamp('2026-06-09 11:30:00')) & \
            (df['ist'] <= pd.Timestamp('2026-06-09 19:30:00'))
    df_win = df[mask2].copy()

print(f"\nWindow bars (11:30–19:30 IST): {len(df_win)}")
if not df_win.empty:
    print(f"Window: {df_win['ist'].iloc[0].strftime('%H:%M IST')} → {df_win['ist'].iloc[-1].strftime('%H:%M IST')}")
    print(f"High: {df_win['high'].max():.2f}  Low: {df_win['low'].min():.2f}")
    print()

    # Key structural events
    print("── KEY PRICE ACTION (5m bars, condensed) ──")
    print(f"  {'IST':<8} {'High':>8} {'Low':>8} {'Close':>8}  Notes")
    day_high = df_win['high'].max()
    day_low  = df_win['low'].min()

    for i in range(len(df_win)):
        row = df_win.iloc[i]
        ist_t = row['ist'].strftime('%H:%M')
        note = ''
        if row['high'] == day_high: note = ' ◄ SESSION HIGH (buy-side swept)'
        if row['low'] == day_low:   note = f' ◄ SESSION LOW {row["low"]:.2f}'
        # Flag large bearish candles
        body = abs(row['close'] - row['open'])
        if body > 5 and row['close'] < row['open']: note += ' [BEAR impulse]'
        if body > 5 and row['close'] > row['open']: note += ' [BULL impulse]'
        print(f"  {ist_t:<8} {row['high']:>8.2f} {row['low']:>8.2f} {row['close']:>8.2f}  {note}")

    # Swing highs in window (buy-side liquidity)
    print("\n── BUY-SIDE LIQUIDITY (Swing Highs) ──")
    for i in range(3, len(df_win)-3):
        h = df_win['high'].iloc[i]
        w = df_win['high'].iloc[max(0,i-3):i+4]
        if h == w.max():
            swept = any(df_win['high'].iloc[i+1:] > h)
            ist_t = df_win['ist'].iloc[i].strftime('%H:%M')
            tag   = '✅ SWEPT (trap)' if swept else '⚠️ UNSWEPT'
            print(f"  {ist_t} IST  {h:.2f}  {tag}")

    # Liquidity sweeps: wick above prior swing high, close back below
    print("\n── BUY-SIDE SWEEPS (wick above high, close below) ──")
    sweeps = []
    swing_highs = []
    for i in range(3, len(df_win)-3):
        h = df_win['high'].iloc[i]
        w = df_win['high'].iloc[max(0,i-3):i+4]
        if h == w.max():
            swing_highs.append((i, h))

    for i in range(1, len(df_win)):
        for (si, sh) in swing_highs:
            if i <= si: continue
            if df_win['high'].iloc[i] > sh and df_win['close'].iloc[i] < sh:
                ist_t = df_win['ist'].iloc[i].strftime('%H:%M')
                print(f"  {ist_t} IST  swept {sh:.2f}  wick→{df_win['high'].iloc[i]:.2f}  close→{df_win['close'].iloc[i]:.2f}  ← SHORT trigger")
                sweeps.append(i)
                break

    # CHoCH bearish
    print("\n── BEARISH CHoCH (SHORT confirmation) ──")
    for i in range(5, len(df_win)-1):
        prev_lows = [df_win['low'].iloc[j] for j in range(max(0,i-8),i)]
        if not prev_lows: continue
        swing_low = min(prev_lows)
        cl = df_win['close'].iloc[i]
        pc = df_win['close'].iloc[i-1]
        if pc > swing_low and cl < swing_low:
            ist_t = df_win['ist'].iloc[i].strftime('%H:%M')
            print(f"  {ist_t} IST  broke {swing_low:.2f}  close={cl:.2f}")

    # Bearish FVGs
    print("\n── BEARISH FVGs (SHORT entry zones) ──")
    for i in range(1, len(df_win)-1):
        c1l = df_win['low'].iloc[i-1]
        c3h = df_win['high'].iloc[i+1]
        if c1l > c3h and (c1l - c3h) >= 0.80:
            ist_t = df_win['ist'].iloc[i].strftime('%H:%M')
            size  = round(c1l - c3h, 2)
            mid   = round((c1l + c3h)/2, 2)
            print(f"  {ist_t} IST  gap: {c3h:.2f}–{c1l:.2f}  size={size:.2f}  mid={mid:.2f}  ← SHORT entry fill")
