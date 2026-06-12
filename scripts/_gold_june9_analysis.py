"""
XAUUSD June 9 analysis: 11:30 AM IST to 7:30 PM IST
IST 11:30 = UTC 06:00 | IST 19:30 = UTC 14:00
"""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone, timedelta

IST_OFFSET = timedelta(hours=5, minutes=30)
SYMBOL = 'XAUUSD.x'

# Window in UTC
start_utc = datetime(2026, 6, 9, 6, 0, tzinfo=timezone.utc)
end_utc   = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)

mt5.initialize()
mt5.symbol_select(SYMBOL, True)

# Fetch 5m bars — pull wider window for context (4:00-16:00 UTC)
rates = mt5.copy_rates_range(
    SYMBOL,
    mt5.TIMEFRAME_M5,
    datetime(2026, 6, 9, 4, 0),
    datetime(2026, 6, 9, 16, 0),
)
mt5.shutdown()

if rates is None or len(rates) == 0:
    print("No data returned from MT5")
    exit()

df = pd.DataFrame(rates)
df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
df['ist']  = df['time'] + IST_OFFSET
df = df.set_index('time')

# Filter to analysis window
mask = (df.index >= start_utc) & (df.index <= end_utc)
df_win = df[mask].copy()

print(f"Total bars in window: {len(df_win)}")
print(f"Window: {df_win['ist'].iloc[0].strftime('%H:%M IST')} → {df_win['ist'].iloc[-1].strftime('%H:%M IST')}")
print(f"\nOHLC Summary:")
print(f"  Open:  {df_win['open'].iloc[0]:.2f}")
print(f"  High:  {df_win['high'].max():.2f}  @ {df_win.loc[df_win['high'].idxmax(), 'ist'].strftime('%H:%M IST')}")
print(f"  Low:   {df_win['low'].min():.2f}  @ {df_win.loc[df_win['low'].idxmin(), 'ist'].strftime('%H:%M IST')}")
print(f"  Close: {df_win['close'].iloc[-1]:.2f}")

# ── Swing highs (buy-side liquidity) ──────────────────────────────────────────
print("\n── BUY-SIDE LIQUIDITY (Swing Highs) ──")
swing_highs = []
for i in range(3, len(df_win) - 3):
    h = df_win['high'].iloc[i]
    window = df_win['high'].iloc[max(0,i-3):i+4]
    if h == window.max():
        # Check if swept later
        swept = any(df_win['high'].iloc[i+1:] > h)
        swing_highs.append({
            'time_ist': df_win['ist'].iloc[i].strftime('%H:%M'),
            'level': round(h, 2),
            'swept': swept,
        })

for sh in swing_highs:
    tag = '✅ SWEPT' if sh['swept'] else '⚠️  UNSWEPT'
    print(f"  {sh['time_ist']} IST  High: {sh['level']}  {tag}")

# ── Swing lows (sell-side liquidity) ──────────────────────────────────────────
print("\n── SELL-SIDE LIQUIDITY (Swing Lows) ──")
swing_lows = []
for i in range(3, len(df_win) - 3):
    l = df_win['low'].iloc[i]
    window = df_win['low'].iloc[max(0,i-3):i+4]
    if l == window.min():
        swept = any(df_win['low'].iloc[i+1:] < l)
        swing_lows.append({
            'time_ist': df_win['ist'].iloc[i].strftime('%H:%M'),
            'level': round(l, 2),
            'swept': swept,
        })

for sl in swing_lows:
    tag = '✅ SWEPT' if sl['swept'] else '⚠️  UNSWEPT'
    print(f"  {sl['time_ist']} IST  Low:  {sl['level']}  {tag}")

# ── Liquidity sweeps (wick beyond swing, close back inside) ────────────────────
print("\n── LIQUIDITY SWEEPS DETECTED ──")
for i in range(3, len(df_win) - 1):
    # Buy-side sweep: wick above prior high, close back below
    for sh in swing_highs:
        if df_win['ist'].iloc[i].strftime('%H:%M') <= sh['time_ist']:
            continue
        h  = sh['level']
        hi = df_win['high'].iloc[i]
        cl = df_win['close'].iloc[i]
        if hi > h and cl < h:
            print(f"  BUY-SIDE SWEEP @ {sh['level']}  candle: {df_win['ist'].iloc[i].strftime('%H:%M IST')}  "
                  f"wick→{hi:.2f}  close→{cl:.2f}  (trap!)")

    # Sell-side sweep: wick below prior low, close back above
    for sl in swing_lows:
        if df_win['ist'].iloc[i].strftime('%H:%M') <= sl['time_ist']:
            continue
        l  = sl['level']
        lo = df_win['low'].iloc[i]
        cl = df_win['close'].iloc[i]
        if lo < l and cl > l:
            print(f"  SELL-SIDE SWEEP @ {sl['level']}  candle: {df_win['ist'].iloc[i].strftime('%H:%M IST')}  "
                  f"wick→{lo:.2f}  close→{cl:.2f}  (reversal long signal)")

# ── CHoCH detection (structure shifts) ────────────────────────────────────────
print("\n── CHoCH / BOS EVENTS ──")
for i in range(5, len(df_win)-1):
    # Bearish CHoCH: prior bullish swing high broken to downside by close
    prev_lows  = [df_win['low'].iloc[j] for j in range(max(0,i-10), i)]
    prev_highs = [df_win['high'].iloc[j] for j in range(max(0,i-10), i)]
    if not prev_lows or not prev_highs:
        continue

    recent_swing_low = min(prev_lows)
    cl = df_win['close'].iloc[i]
    prev_cl = df_win['close'].iloc[i-1]

    if prev_cl > recent_swing_low and cl < recent_swing_low:
        print(f"  BEARISH CHoCH @ {df_win['ist'].iloc[i].strftime('%H:%M IST')}  "
              f"broke below {recent_swing_low:.2f}  close={cl:.2f}  → SHORT signal")

# ── FVG detection ──────────────────────────────────────────────────────────────
print("\n── FVGs (Fair Value Gaps) ──")
for i in range(1, len(df_win)-1):
    c1_high = df_win['high'].iloc[i-1]
    c1_low  = df_win['low'].iloc[i-1]
    c3_high = df_win['high'].iloc[i+1]
    c3_low  = df_win['low'].iloc[i+1]
    c2_high = df_win['high'].iloc[i]
    c2_low  = df_win['low'].iloc[i]
    ist_t   = df_win['ist'].iloc[i].strftime('%H:%M')

    # Bearish FVG: c1 low > c3 high (gap above)
    if c1_low > c3_high:
        size = round(c1_low - c3_high, 2)
        mid  = round((c1_low + c3_high) / 2, 2)
        if size >= 0.50:
            print(f"  BEARISH FVG @ {ist_t} IST  gap: {c3_high:.2f}–{c1_low:.2f}  size={size:.2f}  mid={mid:.2f}")

    # Bullish FVG: c3 low > c1 high (gap below)
    if c3_low > c1_high:
        size = round(c3_low - c1_high, 2)
        mid  = round((c3_low + c1_high) / 2, 2)
        if size >= 0.50:
            print(f"  BULLISH FVG @ {ist_t} IST  gap: {c1_high:.2f}–{c3_low:.2f}  size={size:.2f}  mid={mid:.2f}")

# ── Candle-by-candle key moments ───────────────────────────────────────────────
print("\n── KEY PRICE ACTION (all 5m bars) ──")
print(f"  {'IST':<8} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8}  {'Dir'}")
prev_close = None
for i, row in df_win.iterrows():
    ist_t = row['ist'].strftime('%H:%M')
    direction = '▲' if row['close'] >= row['open'] else '▼'
    wick_up   = round(row['high'] - max(row['open'], row['close']), 2)
    wick_dn   = round(min(row['open'], row['close']) - row['low'], 2)
    note = ''
    if row['high'] == df_win['high'].max(): note = ' ← DAY HIGH / BUY-SIDE SWEPT'
    if row['low'] == df_win['low'].min():   note = ' ← LOW 4236.74'
    print(f"  {ist_t:<8} {row['open']:>8.2f} {row['high']:>8.2f} {row['low']:>8.2f} {row['close']:>8.2f}  {direction}{note}")
