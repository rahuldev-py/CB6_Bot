"""
CB6 Quantum — Combined Backtest Report
Period : May 2024 → May 2026  (2 full years)
Data   : bt_1y_trades.csv (May 2024–May 2025) +
         bt_365d_trades.csv (May 2025–May 2026)
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import pandas as pd
import numpy as np

# ── Load & merge ───────────────────────────────────────────────────────────────
bt1 = pd.read_csv('ml/training_data/bt_1y_trades.csv')   # 2024-2025
bt2 = pd.read_csv('ml/training_data/bt_365d_trades.csv') # 2025-2026

# Align columns (bt_365d has extra 'target_hit' — drop it for merge)
for col in ['target_hit']:
    if col in bt2.columns and col not in bt1.columns:
        bt2 = bt2.drop(columns=[col])

# Add source tag
bt1['period'] = '2024-25'
bt2['period'] = '2025-26'

df = pd.concat([bt1, bt2], ignore_index=True)
df['date'] = pd.to_datetime(df['date'])
df = df.sort_values('date').reset_index(drop=True)
df['month'] = df['date'].dt.to_period('M').astype(str)
df['year']  = df['date'].dt.year

TOTAL = len(df)

# ── Helper ─────────────────────────────────────────────────────────────────────
def stats(sub):
    if sub.empty: return {}
    n   = len(sub)
    w   = (sub['r'] > 0).sum()
    l   = n - w
    wr  = round(w/n*100, 1)
    tr  = round(sub['r'].sum(), 2)
    ar  = round(tr/n, 3)
    wr_ = sub[sub['r']>0]['r'].sum()
    lr_ = abs(sub[sub['r']<=0]['r'].sum())
    pf  = round(wr_/lr_, 2) if lr_ else float('inf')
    ah  = round(sub['hold_mins'].mean())
    return dict(n=n, w=int(w), l=int(l), wr=wr, tr=tr, ar=ar, pf=pf, ah=ah)

SEP  = '═' * 65
SEP2 = '─' * 65

print(SEP)
print('  CB6 Quantum — Combined Backtest Report')
print('  Period  : May 2024 → May 2026  (2 years)')
print('  Strategy: ICT Silver Bullet · 3-min TF · Real Market Data')
print('  Indices : NIFTY · BANKNIFTY · MIDCPNIFTY · FINNIFTY')
print(SEP)

# ── MASTER SUMMARY ─────────────────────────────────────────────────────────────
s = stats(df)
wins   = df[df['r'] > 0]
losses = df[df['r'] <= 0]
longs  = df[df['dir'] == 'LONG']
shorts = df[df['dir'] == 'SHORT']

sw = sl2 = cw = cl = 0
for _, row in df.iterrows():
    if row['r'] > 0: cw+=1; cl=0; sw=max(sw,cw)
    else:             cl+=1; cw=0; sl2=max(sl2,cl)

trading_days = df['date'].dt.date.nunique()

print(f'\n{"MASTER SUMMARY":^65}')
print(SEP2)
print(f'  Total Trades    : {TOTAL}  (Period: {df.date.min().date()} → {df.date.max().date()})')
print(f'  Win Rate        : {s["wr"]}%  ({s["w"]}W / {s["l"]}L)')
print(f'  Total R         : {s["tr"]:+.2f}R')
print(f'  Avg R / Trade   : {s["ar"]:+.3f}R')
print(f'  Profit Factor   : {s["pf"]}')
print(f'  Avg Hold Time   : {s["ah"]} min')
print(f'  Max Win Streak  : {sw}  |  Max Loss Streak : {sl2}')
print(f'  Trading Days    : {trading_days}')
print(f'  Trades/Day      : {round(TOTAL/trading_days,1)}')
print()

# ── LONG vs SHORT ─────────────────────────────────────────────────────────────
sl = stats(longs);  ss = stats(shorts)
print(f'  {"Direction":10}  {"Trades":>6}  {"WR%":>5}  {"Total R":>9}  {"Avg R":>7}  {"PF":>6}')
print(f'  {SEP2[:60]}')
print(f'  {"LONG":10}  {sl["n"]:>6}  {sl["wr"]:>5.1f}%  {sl["tr"]:>+9.2f}  {sl["ar"]:>+7.3f}  {sl["pf"]:>6.2f}')
print(f'  {"SHORT":10}  {ss["n"]:>6}  {ss["wr"]:>5.1f}%  {ss["tr"]:>+9.2f}  {ss["ar"]:>+7.3f}  {ss["pf"]:>6.2f}')
print()

# ── BY INDEX ──────────────────────────────────────────────────────────────────
print(f'{"BY INDEX":^65}')
print(SEP2)
print(f'  {"Index":12}  {"Trades":>6}  {"WR%":>5}  {"Total R":>9}  {"Avg R":>7}  {"PF":>6}  {"Avgh":>5}')
print(f'  {SEP2[:60]}')
for idx in ['NIFTY','BANKNIFTY','MIDCPNIFTY','FINNIFTY']:
    sub = df[df['index'] == idx]
    if sub.empty: continue
    si = stats(sub)
    print(f'  {idx:12}  {si["n"]:>6}  {si["wr"]:>5.1f}%  {si["tr"]:>+9.2f}  {si["ar"]:>+7.3f}  {si["pf"]:>6.2f}  {si["ah"]:>5}m')
print()

# ── BY YEAR ───────────────────────────────────────────────────────────────────
print(f'{"BY YEAR":^65}')
print(SEP2)
print(f'  {"Year":6}  {"Trades":>6}  {"WR%":>5}  {"Total R":>9}  {"Avg R":>7}  {"PF":>6}')
print(f'  {SEP2[:55]}')
for yr in sorted(df['year'].unique()):
    sub = df[df['year']==yr]
    sy = stats(sub)
    print(f'  {yr:6}  {sy["n"]:>6}  {sy["wr"]:>5.1f}%  {sy["tr"]:>+9.2f}  {sy["ar"]:>+7.3f}  {sy["pf"]:>6.2f}')
print()

# ── BY PERIOD ─────────────────────────────────────────────────────────────────
print(f'{"BY PERIOD":^65}')
print(SEP2)
for p, label in [('2024-25','May 2024 → May 2025'), ('2025-26','May 2025 → May 2026')]:
    sub = df[df['period']==p]
    sp = stats(sub)
    idxs = sub['index'].unique()
    print(f'  {label}  ({", ".join(idxs)})')
    print(f'    Trades={sp["n"]}  WR={sp["wr"]}%  TotalR={sp["tr"]:+.2f}  PF={sp["pf"]}  AvgR={sp["ar"]:+.3f}')
print()

# ── BY MSS TYPE ───────────────────────────────────────────────────────────────
print(f'{"BY MSS TYPE":^65}')
print(SEP2)
print(f'  {"MSS":8}  {"Trades":>6}  {"WR%":>5}  {"Total R":>9}  {"Avg R":>7}  {"PF":>6}')
print(f'  {SEP2[:55]}')
for mss in ['CHOCH','BOS']:
    sub = df[df['mss']==mss]
    if sub.empty: continue
    sm = stats(sub)
    print(f'  {mss:8}  {sm["n"]:>6}  {sm["wr"]:>5.1f}%  {sm["tr"]:>+9.2f}  {sm["ar"]:>+7.3f}  {sm["pf"]:>6.2f}')
print()

# ── BY EXIT TYPE ─────────────────────────────────────────────────────────────
print(f'{"BY EXIT TYPE":^65}')
print(SEP2)
t3c = (df['outcome']=='T3').sum(); t2c = (df['outcome']=='T2').sum()
t1c = (df['outcome']=='T1').sum(); slc = (df['outcome']=='SL').sum()
eodc= df['outcome'].isin(['EOD','TIMEOUT']).sum()
print(f'  T3 (full target)  : {t3c:>4} trades  ({t3c/TOTAL*100:.1f}%)')
print(f'  T2 (2nd target)   : {t2c:>4} trades  ({t2c/TOTAL*100:.1f}%)')
print(f'  T1 (1st target)   : {t1c:>4} trades  ({t1c/TOTAL*100:.1f}%)')
print(f'  SL (stop loss)    : {slc:>4} trades  ({slc/TOTAL*100:.1f}%)')
print(f'  EOD (force close) : {eodc:>4} trades  ({eodc/TOTAL*100:.1f}%)')
print()

# ── BY SESSION ────────────────────────────────────────────────────────────────
df['session'] = df['hour'].apply(lambda h: 'AM (10-12)' if h < 12 else 'PM (12-15)')
print(f'{"BY SESSION":^65}')
print(SEP2)
for sess in ['AM (10-12)', 'PM (12-15)']:
    sub = df[df['session']==sess]
    if sub.empty: continue
    sv = stats(sub)
    print(f'  {sess:12}  Trades={sv["n"]}  WR={sv["wr"]}%  TotalR={sv["tr"]:+.2f}  PF={sv["pf"]}')
print()

# ── BY DAY OF WEEK ────────────────────────────────────────────────────────────
days = ['Monday','Tuesday','Wednesday','Thursday','Friday']
print(f'{"BY DAY OF WEEK":^65}')
print(SEP2)
print(f'  {"Day":10}  {"Trades":>6}  {"WR%":>5}  {"Total R":>9}  {"Avg R":>7}')
print(f'  {SEP2[:50]}')
for d_i, d_name in enumerate(days):
    sub = df[df['weekday']==d_i]
    if sub.empty: continue
    sd = stats(sub)
    print(f'  {d_name:10}  {sd["n"]:>6}  {sd["wr"]:>5.1f}%  {sd["tr"]:>+9.2f}  {sd["ar"]:>+7.3f}')
print()

# ── MONTHLY BREAKDOWN ─────────────────────────────────────────────────────────
print(f'{"MONTHLY BREAKDOWN (all indices combined)":^65}')
print(SEP2)
print(f'  {"Month":>8}  {"Trades":>6}  {"Wins":>4}  {"WR%":>5}  {"Total R":>9}  {"Avg R":>7}')
print(f'  {SEP2[:60]}')
for m in sorted(df['month'].unique()):
    sub = df[df['month']==m]
    sm = stats(sub)
    print(f'  {m:>8}  {sm["n"]:>6}  {sm["w"]:>4}  {sm["wr"]:>5.1f}%  {sm["tr"]:>+9.2f}  {sm["ar"]:>+7.3f}')
print()

# ── BEST / WORST ──────────────────────────────────────────────────────────────
best5  = df.nlargest(5, 'r')[['index','date','time','dir','r','outcome','mss']]
worst5 = df.nsmallest(5, 'r')[['index','date','time','dir','r','outcome','mss']]
print(f'{"TOP 5 TRADES (all time)":^65}')
print(SEP2)
for _, row in best5.iterrows():
    print(f'  {str(row["date"].date()):>12}  {row["time"]}  {row["index"]:12}  {row["dir"]:5}  '
          f'{row["r"]:>+7.2f}R  {row["outcome"]:6}  {row["mss"]}')
print()
print(f'{"BOTTOM 5 TRADES (all time)":^65}')
print(SEP2)
for _, row in worst5.iterrows():
    print(f'  {str(row["date"].date()):>12}  {row["time"]}  {row["index"]:12}  {row["dir"]:5}  '
          f'{row["r"]:>+7.2f}R  {row["outcome"]:6}  {row["mss"]}')
print()

# ── EQUITY CURVE SUMMARY ──────────────────────────────────────────────────────
df_sorted = df.sort_values('date').reset_index(drop=True)
df_sorted['cumR'] = df_sorted['r'].cumsum()
peak = df_sorted['cumR'].cummax()
dd   = df_sorted['cumR'] - peak
max_dd = round(dd.min(), 2)
max_r  = round(df_sorted['cumR'].max(), 2)
final_r = round(df_sorted['cumR'].iloc[-1], 2)

print(f'{"EQUITY CURVE":^65}')
print(SEP2)
print(f'  Peak Cumulative R : {max_r:+.2f}R')
print(f'  Final Cumulative R: {final_r:+.2f}R')
print(f'  Max Drawdown      : {max_dd:.2f}R')
if max_dd < 0:
    print(f'  Calmar Ratio      : {round(final_r/abs(max_dd),2)}')
print()

# ── SAVE COMBINED CSV ─────────────────────────────────────────────────────────
out_path = 'ml/training_data/bt_combined_2024_2026.csv'
df.to_csv(out_path, index=False)
print(SEP)
print(f'  ✓ Combined CSV → {out_path}  ({len(df)} rows)')
print(SEP)
