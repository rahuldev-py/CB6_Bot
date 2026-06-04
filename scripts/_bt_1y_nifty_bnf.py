"""
CB6 Quantum — 1-Year Fyers 3m Backtest
Indices : NIFTY + BANKNIFTY
Period  : 24 May 2024 → 25 May 2025  (fixed dates)
TF      : 3 min  (cont_flag=1 → continuous contract, auto-rolled)
Chunking: 4 × 90-day windows (Fyers hard limit 100 days per request)
Output  : Console results + ml/training_data/bt_1y_trades.csv
"""
import sys, os, warnings, time, csv
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(override=True)

import pandas as pd
from datetime import datetime, timedelta
from fyers_apiv3 import fyersModel
from scanner.silver_bullet import scan_silver_bullet
from utils.logger import logger

# ── Auth ───────────────────────────────────────────────────────────────────────
ACCESS_TOKEN = os.getenv('ACCESS_TOKEN', '')
CLIENT_ID    = os.getenv('CLIENT_ID', 'ILRAADDBFV-200')
TOKEN_VALUE  = ACCESS_TOKEN.split(':', 1)[1] if ':' in ACCESS_TOKEN else ACCESS_TOKEN

fyers = fyersModel.FyersModel(
    client_id=CLIENT_ID, token=TOKEN_VALUE,
    is_async=False, log_path='logs'
)

prof = fyers.get_profile()
if prof.get('code') != 200:
    print(f"AUTH FAILED: {prof}")
    sys.exit(1)
print(f"✓ Auth OK — {prof.get('data',{}).get('name','?')} | {prof.get('data',{}).get('fy_id','?')}")
print()

# ── Config ─────────────────────────────────────────────────────────────────────
INDICES = {
    'NIFTY'    : 'NSE:NIFTY26MAYFUT',
    'BANKNIFTY': 'NSE:BANKNIFTY26MAYFUT',
}

START_DATE = datetime(2024, 5, 24)
END_DATE   = datetime(2025, 5, 25)
CHUNK_DAYS = 90      # Fyers intraday hard limit = 100d; stay under at 90

WINDOW   = 150       # rolling lookback bars  (150×3min = 7.5h)
COOLDOWN = 10        # bars cooldown after exit (~30 min)
MAX_HOLD = 120       # max bars to hold (~6h)
EOD_H    = 15        # force-close hour
EOD_M    = 20        # force-close minute
SCAN_H   = 10        # start scanning after 10:00

ML_CSV = os.path.join(os.path.dirname(__file__),
                      'ml', 'training_data', 'bt_1y_trades.csv')
os.makedirs(os.path.dirname(ML_CSV), exist_ok=True)


# ── Fetch — direct chunked calls over exact date range ─────────────────────────
def fetch_3m(symbol: str, idx_name: str) -> pd.DataFrame:
    total_days = (END_DATE - START_DATE).days
    chunks_n = (total_days + CHUNK_DAYS - 1) // CHUNK_DAYS
    print(f"  Fetching {idx_name} — {total_days}d in {chunks_n} × {CHUNK_DAYS}d chunks …")
    t0 = time.time()

    all_candles = []
    chunk_start = START_DATE
    chunk_num   = 0
    while chunk_start < END_DATE:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), END_DATE)
        chunk_num += 1
        rf = chunk_start.strftime('%Y-%m-%d')
        rt = chunk_end.strftime('%Y-%m-%d')
        print(f"    [{chunk_num}/{chunks_n}] {rf} → {rt}", end='  ', flush=True)

        success = False
        for attempt in range(3):
            r = fyers.history({
                'symbol'     : symbol,
                'resolution' : '3',
                'date_format': '1',
                'range_from' : rf,
                'range_to'   : rt,
                'cont_flag'  : '1',
            })
            if r.get('code') == 200 and r.get('candles'):
                all_candles.extend(r['candles'])
                print(f"✓ {len(r['candles'])} bars")
                success = True
                break
            else:
                if attempt < 2:
                    time.sleep(1.5)
        if not success:
            print(f"✗ no data ({r.get('message','?')})")

        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(0.4)  # rate-limit buffer

    if not all_candles:
        print(f"  ERROR: No candles returned for {idx_name}")
        return pd.DataFrame()

    # Fyers candle format: [epoch_sec, open, high, low, close, volume]
    df = pd.DataFrame(all_candles, columns=['ts_epoch','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['ts_epoch'], unit='s', utc=True)\
                        .dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
    df['ts'] = df['timestamp']
    df = df.sort_values('ts').drop_duplicates('ts').reset_index(drop=True)

    # Market hours 9:15 – 15:30 IST only
    df = df[
        ((df.ts.dt.hour > 9) | ((df.ts.dt.hour == 9) & (df.ts.dt.minute >= 15))) &
        (df.ts.dt.hour < 16)
    ].reset_index(drop=True)

    days_n = df.ts.dt.date.nunique()
    elapsed = time.time() - t0
    print(f"  ✓ {idx_name}: {len(df):,} bars  |  {days_n} trading days  "
          f"({df.ts.iloc[0].strftime('%b %d %Y')} → {df.ts.iloc[-1].strftime('%b %d %Y')})  "
          f"[{elapsed:.1f}s]")
    return df


# ── Walk-forward simulation ─────────────────────────────────────────────────────
def simulate(df3: pd.DataFrame, symbol: str, name: str) -> list:
    trades   = []
    i        = 0
    skip     = 0
    bar_count = len(df3)

    while i < bar_count:
        if i < skip:
            i += 1
            continue

        ts = df3['ts'].iloc[i]

        # Only scan 10:00 – 15:20
        if ts.hour < SCAN_H or (ts.hour >= EOD_H and ts.minute >= EOD_M):
            i += 1
            continue

        # Rolling window
        ws  = max(0, i - WINDOW)
        win = df3.iloc[ws:i+1][['timestamp','open','high','low','close','volume']].copy()

        try:
            setup = scan_silver_bullet(win, symbol, tf='3', fyers=None, force=True)
        except Exception:
            i += 1
            continue

        if setup is None:
            i += 1
            continue

        sig      = setup['entry_signal']
        entry    = sig['entry']
        sl       = sig['stop_loss']
        t1,t2,t3 = sig['target1'], sig['target2'], sig['target3']
        risk     = sig['risk']
        dirn     = setup['direction']   # 'BULLISH' or 'BEARISH'
        if risk <= 0:
            i += 1
            continue

        # Forward walk: SL / T1 / T2 / T3 bar-by-bar
        outcome = 'TIMEOUT'
        exit_p  = None
        r_act   = 0.0
        exit_i  = min(i + MAX_HOLD, bar_count - 1)

        for j in range(i + 1, min(i + MAX_HOLD, bar_count)):
            row  = df3.iloc[j]
            h, l = float(row['high']), float(row['low'])
            tsj  = row['ts']

            # EOD force-close
            if tsj.hour >= EOD_H and tsj.minute >= EOD_M:
                exit_p = float(row['close'])
                r_act  = round((exit_p - entry) / risk if dirn == 'BULLISH'
                               else (entry - exit_p) / risk, 2)
                outcome = 'EOD'; exit_i = j; break

            if dirn == 'BULLISH':
                if l <= sl:  outcome='SL'; exit_p=sl;  r_act=-1.0;                     exit_i=j; break
                if h >= t3:  outcome='T3'; exit_p=t3;  r_act=round((t3-entry)/risk,2); exit_i=j; break
                if h >= t2:  outcome='T2'; exit_p=t2;  r_act=round((t2-entry)/risk,2); exit_i=j; break
                if h >= t1:  outcome='T1'; exit_p=t1;  r_act=round((t1-entry)/risk,2); exit_i=j; break
            else:
                if h >= sl:  outcome='SL'; exit_p=sl;  r_act=-1.0;                     exit_i=j; break
                if l <= t3:  outcome='T3'; exit_p=t3;  r_act=round((entry-t3)/risk,2); exit_i=j; break
                if l <= t2:  outcome='T2'; exit_p=t2;  r_act=round((entry-t2)/risk,2); exit_i=j; break
                if l <= t1:  outcome='T1'; exit_p=t1;  r_act=round((entry-t1)/risk,2); exit_i=j; break

        if outcome == 'TIMEOUT' or exit_p is None:
            exit_p = float(df3.iloc[exit_i]['close'])
            r_act  = round((exit_p - entry) / risk if dirn == 'BULLISH'
                           else (entry - exit_p) / risk, 2)

        hold_mins = (exit_i - i) * 3
        fvg_info  = setup.get('fvg', {})

        trades.append({
            'index'      : name,
            'date'       : ts.strftime('%Y-%m-%d'),
            'time'       : ts.strftime('%H:%M'),
            'exit_time'  : df3['ts'].iloc[exit_i].strftime('%H:%M'),
            'hold_mins'  : hold_mins,
            'dir'        : 'LONG' if dirn == 'BULLISH' else 'SHORT',
            'entry'      : round(entry,  1),
            'sl'         : round(sl,     1),
            't1'         : round(t1,     1),
            't2'         : round(t2,     1),
            't3'         : round(t3,     1),
            'risk_pts'   : round(risk,   1),
            'exit_price' : round(exit_p, 1) if exit_p else 0,
            'outcome'    : outcome,
            'r'          : r_act,
            'score'      : setup.get('confluence', 0),
            'mss'        : setup.get('mss_type', '?'),
            'regime'     : setup.get('regime', '?'),
            'fvg_size'   : round(fvg_info.get('size',   0), 2),
            'fvg_top'    : round(fvg_info.get('top',    0), 2),
            'fvg_bottom' : round(fvg_info.get('bottom', 0), 2),
            'hour'       : ts.hour,
            'minute'     : ts.minute,
            'weekday'    : ts.weekday(),
            'win'        : 1 if r_act > 0 else 0,
        })

        skip = exit_i + COOLDOWN
        i    = exit_i + 1

    return trades


# ── Stats ───────────────────────────────────────────────────────────────────────
def print_stats(trades: list, name: str) -> None:
    if not trades:
        print(f"\n  {name}: No trades found\n")
        return

    total = len(trades)
    wins   = [t for t in trades if t['r'] > 0]
    losses = [t for t in trades if t['r'] <= 0]
    longs  = [t for t in trades if t['dir'] == 'LONG']
    shorts = [t for t in trades if t['dir'] == 'SHORT']

    wr      = round(len(wins)/total*100, 1)
    total_r = round(sum(t['r'] for t in trades), 2)
    avg_r   = round(total_r / total, 3)
    win_r   = sum(t['r'] for t in wins)
    loss_r  = abs(sum(t['r'] for t in losses))
    pf      = round(win_r/loss_r, 2) if loss_r else float('inf')
    avg_h   = round(sum(t['hold_mins'] for t in trades)/total)

    l_wr = round(len([t for t in longs  if t['r']>0])/max(len(longs),1)*100,1)
    s_wr = round(len([t for t in shorts if t['r']>0])/max(len(shorts),1)*100,1)

    best_l = max((t for t in longs),  key=lambda x: x['r'], default=None)
    best_s = max((t for t in shorts), key=lambda x: x['r'], default=None)
    worst  = min(trades, key=lambda x: x['r'])

    t3c = sum(1 for t in trades if t['outcome']=='T3')
    t2c = sum(1 for t in trades if t['outcome']=='T2')
    t1c = sum(1 for t in trades if t['outcome']=='T1')
    slc = sum(1 for t in trades if t['outcome']=='SL')
    eod = sum(1 for t in trades if t['outcome'] in ('EOD','TIMEOUT'))

    # Streaks
    sw = sl2 = cw = cl = 0
    for t in trades:
        if t['r'] > 0: cw+=1; cl=0; sw=max(sw,cw)
        else:           cl+=1; cw=0; sl2=max(sl2,cl)

    trading_days = len(set(t['date'] for t in trades))

    # Monthly breakdown
    months: dict = {}
    for t in trades:
        m = t['date'][:7]
        months.setdefault(m, {'t':0,'w':0,'r':0.0})
        months[m]['t'] += 1
        months[m]['w'] += 1 if t['r'] > 0 else 0
        months[m]['r']  = round(months[m]['r']+t['r'], 2)

    print(f"\n{'═'*60}")
    print(f"  {name}  |  3-min  |  May 24 2024 → May 25 2025")
    print(f"{'═'*60}")
    print(f"  Total Trades : {total}  (L={len(longs)}  S={len(shorts)})")
    print(f"  Win Rate     : {wr}%   ({len(wins)}W / {len(losses)}L)")
    print(f"  Long WR      : {l_wr}%  |  Short WR : {s_wr}%")
    print(f"  Total R      : {total_r:+.2f}")
    print(f"  Avg R/trade  : {avg_r:+.3f}")
    print(f"  Profit Factor: {pf:.2f}")
    print(f"  Avg Hold     : {avg_h} min")
    print(f"  Exit dist    : T3={t3c}  T2={t2c}  T1={t1c}  SL={slc}  EOD={eod}")
    print(f"  Max W-streak : {sw}  |  Max L-streak : {sl2}")
    print(f"  Trading days : {trading_days}")
    if best_l:
        print(f"  Best LONG    : +{best_l['r']}R  ({best_l['date']}  {best_l['time']} → {best_l['exit_time']})")
    if best_s:
        print(f"  Best SHORT   : +{best_s['r']}R  ({best_s['date']}  {best_s['time']} → {best_s['exit_time']})")
    print(f"  Worst trade  : {worst['r']}R  ({worst['date']})")

    print(f"\n  Monthly breakdown:")
    print(f"  {'Month':>8}  {'Trades':>6}  {'Wins':>4}  {'WR%':>5}  {'R':>7}")
    for m in sorted(months):
        d = months[m]
        mwr = round(d['w']/d['t']*100,1) if d['t'] else 0
        print(f"  {m:>8}  {d['t']:>6}  {d['w']:>4}  {mwr:>5.1f}%  {d['r']:>+7.2f}")
    print()


# ── MAIN ───────────────────────────────────────────────────────────────────────
print("="*60)
print("  CB6 Quantum — 1-Year Backtest  |  NIFTY + BANKNIFTY")
print(f"  Period : 24 May 2024 → 25 May 2025  |  3-min TF")
print("="*60)
print()

all_trades = []
all_stats  = []

for idx_name, symbol in INDICES.items():
    print(f"{'─'*60}")
    print(f"  [{idx_name}]  {symbol}")
    df3 = fetch_3m(symbol, idx_name)
    if df3.empty:
        print(f"  ✗ Skipping {idx_name} — no data\n")
        continue
    print(f"  Running walk-forward scan (WINDOW={WINDOW} bars) …")
    t0 = time.time()
    trades = simulate(df3, symbol, idx_name)
    print(f"  ✓ Simulation done in {time.time()-t0:.1f}s → {len(trades)} trades")
    print_stats(trades, idx_name)
    all_trades.extend(trades)

# ── Save ML CSV ────────────────────────────────────────────────────────────────
if all_trades:
    fieldnames = [
        'index','date','time','exit_time','hold_mins',
        'dir','entry','sl','t1','t2','t3','risk_pts','exit_price',
        'outcome','r','score','mss','regime',
        'fvg_size','fvg_top','fvg_bottom',
        'hour','minute','weekday','win',
    ]
    with open(ML_CSV, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        w.writerows(all_trades)
    print(f"\n✓ ML CSV saved → {ML_CSV}  ({len(all_trades)} rows)")

# ── Grand summary ──────────────────────────────────────────────────────────────
if all_trades:
    total = len(all_trades)
    wins  = [t for t in all_trades if t['r'] > 0]
    loss  = [t for t in all_trades if t['r'] <= 0]
    wr    = round(len(wins)/total*100, 1)
    tr    = round(sum(t['r'] for t in all_trades), 2)
    pf    = round(sum(t['r'] for t in wins)/max(abs(sum(t['r'] for t in loss)),0.001), 2)
    print(f"\n{'═'*60}")
    print(f"  COMBINED  |  NIFTY + BANKNIFTY")
    print(f"  Trades : {total}  |  WR : {wr}%")
    print(f"  Total R: {tr:+.2f}  |  PF : {pf}")
    print(f"  Avg R  : {round(tr/total,3):+.3f}")
    print(f"{'═'*60}")
