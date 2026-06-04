"""
CB6 Quantum — Real Fyers 3m Candle Backtest  |  365 Days
Indices : NIFTY, MIDCPNIFTY, BANKNIFTY, FINNIFTY
Period  : May 24 2025 → May 25 2026
Data    : Fyers API  resolution=3  cont_flag=1 (continuous contract)
Chunking: get_historical_data auto-splits into 90-day windows internally
          (Fyers intraday hard limit = 100 days → we use 90-day chunks)
Output  : Console report + ml/training_data/bt_365d_trades.csv  (for ML)
"""
import sys, os, warnings, time, csv
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(__file__))

# ── Load env ───────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(override=True)

import pandas as pd
from datetime import datetime, timedelta
from fyers_apiv3 import fyersModel
from scanner.silver_bullet import scan_silver_bullet
from scanner.data_fetcher  import get_historical_data   # ← handles chunking
from utils.logger import logger

# ── Auth ───────────────────────────────────────────────────────────────────────
ACCESS_TOKEN = os.getenv('ACCESS_TOKEN', '')
CLIENT_ID    = os.getenv('CLIENT_ID', 'ILRAADDBFV-200')

if ':' in ACCESS_TOKEN:
    TOKEN_VALUE = ACCESS_TOKEN.split(':', 1)[1]
else:
    TOKEN_VALUE = ACCESS_TOKEN

fyers = fyersModel.FyersModel(
    client_id=CLIENT_ID, token=TOKEN_VALUE,
    is_async=False, log_path='logs'
)

prof = fyers.get_profile()
if prof.get('code') != 200:
    print(f"AUTH FAILED: {prof}")
    sys.exit(1)
print(f"Auth OK — {prof.get('data',{}).get('name','?')} | {prof.get('data',{}).get('fy_id','?')}")
print()

# ── Config ─────────────────────────────────────────────────────────────────────
INDICES = {
    'NIFTY'      : 'NSE:NIFTY26MAYFUT',
    'MIDCPNIFTY' : 'NSE:MIDCPNIFTY26MAYFUT',
    'BANKNIFTY'  : 'NSE:BANKNIFTY26MAYFUT',
    'FINNIFTY'   : 'NSE:FINNIFTY26MAYFUT',
}

DAYS     = 365    # get_historical_data will auto-chunk into 90d windows
WINDOW   = 150    # rolling lookback bars  (150×3min = 7.5h)
COOLDOWN = 10     # bars cooldown after exit (~30 min)
MAX_HOLD = 120    # max bars to hold (~6h)
EOD_H    = 15     # force-close hour
EOD_M    = 20     # force-close minute  (15:20 IST)
SCAN_H   = 10     # start scanning after 10:00 IST (skip Judas Swing)

END_DATE   = datetime.now()
START_DATE = END_DATE - timedelta(days=DAYS)

# ML export path
ML_CSV = os.path.join(os.path.dirname(__file__), 'ml', 'training_data', 'bt_365d_trades.csv')
os.makedirs(os.path.dirname(ML_CSV), exist_ok=True)


# ── Data fetch (auto-chunked) ──────────────────────────────────────────────────
def fetch_3m(symbol: str, name: str) -> pd.DataFrame:
    """
    get_historical_data with days=365 internally calls _fetch_single_range
    in 90-day windows (4 × 90d + 1 × 5d = 365d).  Handles rate-limit backoff.
    """
    print(f"  Fetching {name} 3m candles — 365 days in 90d chunks ...")
    t0  = time.time()
    df  = get_historical_data(fyers, symbol, '3', days=DAYS, max_retries=3)
    if df is None or len(df) == 0:
        print(f"  ERROR: No data returned for {name}")
        return pd.DataFrame()

    df = df.copy()
    df['ts'] = pd.to_datetime(df['timestamp'])

    # Market hours filter: 9:15–15:30 IST
    df = df[
        ((df.ts.dt.hour > 9) | ((df.ts.dt.hour == 9) & (df.ts.dt.minute >= 15))) &
        (df.ts.dt.hour < 16)
    ].reset_index(drop=True)

    days_n = df.ts.dt.date.nunique()
    print(f"  Got {len(df)} bars  |  {days_n} trading days  "
          f"({df.ts.iloc[0].strftime('%b %d %Y')} → {df.ts.iloc[-1].strftime('%b %d %Y')})  "
          f"[{time.time()-t0:.1f}s]")
    return df


# ── Walk-forward simulation ────────────────────────────────────────────────────
def simulate(df3: pd.DataFrame, symbol: str, name: str) -> list:
    trades = []
    i = skip = 0
    bar_count = len(df3)

    while i < bar_count:
        if i < skip:
            i += 1
            continue

        ts = df3['ts'].iloc[i]

        # Scan window: 10:00 – 15:20 IST
        if ts.hour < SCAN_H or ts.hour >= EOD_H:
            i += 1
            continue

        # Rolling 150-bar window
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
        dirn     = setup['direction']
        if risk <= 0:
            i += 1
            continue

        # ── Forward walk: check SL / T1 / T2 / T3 bar by bar ──────────────────
        outcome = 'TIMEOUT'
        exit_p  = None
        r_act   = 0.0
        exit_i  = min(i + MAX_HOLD, bar_count - 1)

        for j in range(i + 1, min(i + MAX_HOLD, bar_count)):
            row = df3.iloc[j]
            h, l = float(row['high']), float(row['low'])
            tsj  = row['ts']

            # EOD force-close at 15:20
            if tsj.hour >= EOD_H and tsj.minute >= EOD_M:
                exit_p = float(row['close'])
                r_act  = round((exit_p - entry) / risk if dirn == 'BULLISH'
                               else (entry - exit_p) / risk, 2)
                outcome = 'EOD'; exit_i = j; break

            if dirn == 'BULLISH':
                if l <= sl:  outcome='SL'; exit_p=sl;  r_act=-1.0;                      exit_i=j; break
                if h >= t3:  outcome='T3'; exit_p=t3;  r_act=round((t3-entry)/risk,2);  exit_i=j; break
                if h >= t2:  outcome='T2'; exit_p=t2;  r_act=round((t2-entry)/risk,2);  exit_i=j; break
                if h >= t1:  outcome='T1'; exit_p=t1;  r_act=round((t1-entry)/risk,2);  exit_i=j; break
            else:
                if h >= sl:  outcome='SL'; exit_p=sl;  r_act=-1.0;                      exit_i=j; break
                if l <= t3:  outcome='T3'; exit_p=t3;  r_act=round((entry-t3)/risk,2);  exit_i=j; break
                if l <= t2:  outcome='T2'; exit_p=t2;  r_act=round((entry-t2)/risk,2);  exit_i=j; break
                if l <= t1:  outcome='T1'; exit_p=t1;  r_act=round((entry-t1)/risk,2);  exit_i=j; break

        if outcome == 'TIMEOUT' or exit_p is None:
            exit_p = float(df3.iloc[exit_i]['close'])
            r_act  = round((exit_p - entry) / risk if dirn == 'BULLISH'
                           else (entry - exit_p) / risk, 2)

        hold_mins = (exit_i - i) * 3

        # ── Rich feature row (for ML CSV) ──────────────────────────────────────
        fvg_info  = setup.get('fvg', {})
        trades.append({
            # identity
            'index'      : name,
            'date'       : ts.strftime('%Y-%m-%d'),
            'time'       : ts.strftime('%H:%M'),
            'exit_time'  : df3['ts'].iloc[exit_i].strftime('%H:%M'),
            'hold_mins'  : hold_mins,
            # trade params
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
            # setup features (ML inputs)
            'score'      : setup.get('confluence', 0),
            'mss'        : setup.get('mss_type', '?'),
            'regime'     : setup.get('regime',   '?'),
            'fvg_size'   : round(fvg_info.get('size',   0), 2),
            'fvg_top'    : round(fvg_info.get('top',    0), 2),
            'fvg_bottom' : round(fvg_info.get('bottom', 0), 2),
            'hour'       : ts.hour,
            'minute'     : ts.minute,
            'weekday'    : ts.weekday(),   # 0=Mon … 4=Fri
            # label (ML target)
            'win'        : 1 if r_act > 0 else 0,
            'target_hit' : outcome,        # SL / T1 / T2 / T3 / EOD
        })

        skip = exit_i + COOLDOWN
        i    = exit_i + 1

    return trades


# ── Stats calculator ───────────────────────────────────────────────────────────
def calc_stats(trades: list, name: str) -> dict:
    if not trades:
        return {'index': name, 'total': 0}

    wins   = [t for t in trades if t['r'] > 0]
    losses = [t for t in trades if t['r'] <= 0]
    longs  = [t for t in trades if t['dir'] == 'LONG']
    shorts = [t for t in trades if t['dir'] == 'SHORT']
    total  = len(trades)

    wr      = round(len(wins)/total*100, 1)
    total_r = round(sum(t['r'] for t in trades), 2)
    avg_w   = round(sum(t['r'] for t in wins)  /len(wins),   2) if wins   else 0
    avg_l   = round(sum(t['r'] for t in losses)/len(losses), 2) if losses else 0
    win_r   = sum(t['r'] for t in wins)
    loss_r  = abs(sum(t['r'] for t in losses))
    pf      = round(win_r/loss_r, 2) if loss_r > 0 else float('inf')

    avg_h   = round(sum(t['hold_mins'] for t in trades)/total)
    max_h   = max(t['hold_mins'] for t in trades)
    min_h   = min(t['hold_mins'] for t in trades)
    l_wr    = round(len([t for t in longs  if t['r']>0])/len(longs) *100,1) if longs  else 0
    s_wr    = round(len([t for t in shorts if t['r']>0])/len(shorts)*100,1) if shorts else 0

    best_l = max(longs,  key=lambda x: x['r'], default=None)
    best_s = max(shorts, key=lambda x: x['r'], default=None)
    worst  = min(trades, key=lambda x: x['r'])

    t3c  = sum(1 for t in trades if t['outcome']=='T3')
    t2c  = sum(1 for t in trades if t['outcome']=='T2')
    t1c  = sum(1 for t in trades if t['outcome']=='T1')
    slc  = sum(1 for t in trades if t['outcome']=='SL')
    eodc = sum(1 for t in trades if t['outcome'] in ('EOD','TIMEOUT'))

    # Streaks
    streak_w = streak_l = cur_w = cur_l = 0
    for t in trades:
        if t['r'] > 0:
            cur_w += 1; cur_l = 0; streak_w = max(streak_w, cur_w)
        else:
            cur_l += 1; cur_w = 0; streak_l = max(streak_l, cur_l)

    trading_days = len(set(t['date'] for t in trades))
    tpd = round(total / max(trading_days, 1), 1)

    # Monthly breakdown
    months = {}
    for t in trades:
        m = t['date'][:7]   # YYYY-MM
        months.setdefault(m, {'t':0,'w':0,'r':0.0})
        months[m]['t'] += 1
        months[m]['w'] += 1 if t['r'] > 0 else 0
        months[m]['r']  = round(months[m]['r'] + t['r'], 2)

    return {
        'index':name,'total':total,'wins':len(wins),'losses':len(losses),
        'wr':wr,'total_r':total_r,'avg_w':avg_w,'avg_l':avg_l,'pf':pf,
        'avg_h':avg_h,'max_h':max_h,'min_h':min_h,
        'longs':len(longs),'shorts':len(shorts),'l_wr':l_wr,'s_wr':s_wr,
        'best_l':best_l,'best_s':best_s,'worst':worst,
        't3c':t3c,'t2c':t2c,'t1c':t1c,'slc':slc,'eodc':eodc,
        'streak_w':streak_w,'streak_l':streak_l,'tpd':tpd,
        'months':months,'trades':trades,
    }


# ── Print report ───────────────────────────────────────────────────────────────
def print_report(s: dict):
    name = s['index']
    SEP  = '=' * 68
    if s['total'] == 0:
        print(f"\n{SEP}\n  {name}  —  NO TRADES FOUND\n{SEP}")
        return

    pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else 'inf'

    print(f"\n{SEP}")
    print(f"  {name}  ·  3m Fyers Data  ·  365 Days  (May 24 2025 → May 25 2026)")
    print(SEP)
    print(f"  Total Trades    : {s['total']}  ({s['wins']}W / {s['losses']}L)")
    print(f"  Win Rate        : {s['wr']}%")
    print(f"  Total R         : {s['total_r']:+.2f}R")
    print(f"  Profit Factor   : {pf_s}")
    print(f"  Avg Win         : +{s['avg_w']}R      Avg Loss : {s['avg_l']}R")
    print()
    print(f"  Avg Hold        : {s['avg_h']}m   Min:{s['min_h']}m   Max:{s['max_h']}m")
    print(f"  Trades/Day      : ~{s['tpd']}")
    print(f"  Win Streak      : {s['streak_w']}    Loss Streak : {s['streak_l']}")
    print()
    print(f"  LONG  {s['longs']:3d} trades  WR: {s['l_wr']}%")
    print(f"  SHORT {s['shorts']:3d} trades  WR: {s['s_wr']}%")
    print()
    print(f"  T3={s['t3c']}  T2={s['t2c']}  T1={s['t1c']}  SL={s['slc']}  EOD={s['eodc']}")
    print()

    # Monthly breakdown
    print(f"  Monthly Breakdown:")
    for m, v in sorted(s['months'].items()):
        mwr = round(v['w']/v['t']*100,1) if v['t'] else 0
        print(f"    {m}  {v['t']:3d} trades  WR:{mwr:5.1f}%  R:{v['r']:+7.2f}R")
    print()

    if s['best_l']:
        b = s['best_l']
        print(f"  Best LONG  : {b['date']} {b['time']}-{b['exit_time']}  "
              f"E:{b['entry']} SL:{b['sl']} Risk:{b['risk_pts']}pts  "
              f"{b['outcome']} {b['r']:+.2f}R  {b['hold_mins']}m  [#{b['score']} {b['mss']}]")
    if s['best_s']:
        b = s['best_s']
        print(f"  Best SHORT : {b['date']} {b['time']}-{b['exit_time']}  "
              f"E:{b['entry']} SL:{b['sl']} Risk:{b['risk_pts']}pts  "
              f"{b['outcome']} {b['r']:+.2f}R  {b['hold_mins']}m  [#{b['score']} {b['mss']}]")
    w = s['worst']
    print(f"  Worst Trade: {w['date']} {w['time']}  {w['dir']}  "
          f"{w['outcome']} {w['r']:+.2f}R  {w['hold_mins']}m")

    # Full trade log
    print(f"\n  TRADE LOG ({s['total']} trades):")
    print(f"  {'Date':10s} {'Time':9s} {'Dir':5s} {'Entry':8s} {'SL':8s} "
          f"{'Risk':5s} {'Exit':8s} {'Out':7s} {'R':7s} {'Hold':5s} Sc/MSS/Regime/FVG")
    print(f"  {'-'*100}")
    for t in s['trades']:
        icon = 'W' if t['r'] > 0 else 'L'
        print(f"  [{icon}] {t['date']} {t['time']}-{t['exit_time']:5s}  "
              f"{t['dir']:5s}  {t['entry']:8.1f}  {t['sl']:8.1f}  "
              f"{t['risk_pts']:5.1f}  {t['exit_price']:8.1f}  "
              f"{t['outcome']:7s}  {t['r']:+5.2f}R  {t['hold_mins']:3d}m  "
              f"[#{t['score']} {t['mss']} {t['regime']} FVG:{t['fvg_size']}]")


# ── CSV export for ML ──────────────────────────────────────────────────────────
def export_csv(all_trades: list):
    if not all_trades:
        return
    fieldnames = list(all_trades[0].keys())
    with open(ML_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_trades)
    print(f"\n  ML CSV saved → {ML_CSV}  ({len(all_trades)} rows)")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN RUN
# ══════════════════════════════════════════════════════════════════════════════
print(f"CB6 QUANTUM — 365-Day 3m Backtest  |  "
      f"{START_DATE.strftime('%b %d %Y')} → {END_DATE.strftime('%b %d %Y')}")
print(f"Indices: {' | '.join(INDICES)}  |  Scan: 10:00–15:20 IST")
print(f"Chunking: auto 90-day windows (Fyers 100-day limit)\n")

all_stats  = {}
all_trades = []

for name, symbol in INDICES.items():
    print(f"\n{'─'*55}")
    print(f"  {name}  [{symbol}]")
    print(f"{'─'*55}")

    df3 = fetch_3m(symbol, name)
    if df3.empty:
        all_stats[name] = {'index': name, 'total': 0}
        continue

    t0     = time.time()
    trades = simulate(df3, symbol, name)
    elapsed = time.time() - t0
    print(f"  Walk-forward done in {elapsed:.1f}s  →  {len(trades)} setups fired")

    s = calc_stats(trades, name)
    all_stats[name]  = s
    all_trades.extend(trades)

# ── Per-index reports ──────────────────────────────────────────────────────────
for name in INDICES:
    print_report(all_stats[name])

# ── Combined summary ───────────────────────────────────────────────────────────
print(f"\n{'#'*68}")
print(f"  COMBINED — NIFTY + MIDCPNIFTY + BANKNIFTY + FINNIFTY — 365d 3m")
print(f"{'#'*68}")

total_all = sum(s.get('total', 0) for s in all_stats.values())
if total_all > 0:
    total_wins  = sum(s.get('wins',    0) for s in all_stats.values())
    total_r_all = round(sum(s.get('total_r', 0) for s in all_stats.values()), 2)
    comb_wr     = round(total_wins / total_all * 100, 1)
    win_r_all   = sum(t['r'] for t in all_trades if t['r'] > 0)
    loss_r_all  = abs(sum(t['r'] for t in all_trades if t['r'] <= 0))
    comb_pf     = round(win_r_all / loss_r_all, 2) if loss_r_all > 0 else float('inf')
    avg_h_all   = round(sum(t['hold_mins'] for t in all_trades) / total_all)

    print(f"\n  Total Trades : {total_all}")
    print(f"  Win Rate     : {comb_wr}%")
    print(f"  Total R      : {total_r_all:+.2f}R")
    print(f"  Profit Factor: {comb_pf}")
    print(f"  Avg Hold     : {avg_h_all} min")
    print(f"\n  {'Index':12s}  {'Trades':6s}  {'WR':7s}  {'TotalR':9s}  {'PF':6s}  {'LongWR':7s}  {'ShortWR':7s}  {'AvgH':5s}  MaxH")
    print(f"  {'-'*90}")
    for name, s in all_stats.items():
        if s.get('total', 0) == 0:
            print(f"  {name:12s}  {'—':>6s}  {'—':>7s}  {'—':>9s}  {'—':>6s}  {'—':>7s}  {'—':>7s}  {'—':>5s}  —")
        else:
            pf_s = f"{s['pf']:.2f}" if s['pf'] != float('inf') else 'inf'
            print(f"  {name:12s}  {s['total']:6d}  {s['wr']:6.1f}%  "
                  f"{s['total_r']:+9.2f}R  {pf_s:>6s}  "
                  f"{s['l_wr']:6.1f}%  {s['s_wr']:6.1f}%  "
                  f"{s['avg_h']:4d}m  {s['max_h']}m")

# ── ML CSV export ──────────────────────────────────────────────────────────────
export_csv(all_trades)

print(f"\n  Data   : Fyers API  |  Resolution: 3-min  |  cont_flag=1")
print(f"  Chunks : 90-day windows (4× 90d + 1× remainder = 365d)")
print(f"  H1/H4  : RANGING in backtest — live bot applies H4 hard gate")
print(f"  DTE    : Uses today's expiry — historical trades had different DTE")
print(f"  ML CSV : {ML_CSV}")
