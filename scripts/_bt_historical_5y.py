"""
CB6 Quantum — 5-Year Historical 3m Backtest Pipeline
======================================================
Period  : 2021-01-01 → 2025-05-20  (4.38 years)
Indices : NIFTY, BANKNIFTY
TF      : 3-minute (Fyers resolution='3', cont_flag=1)
Chunks  : 16 × ~100-day windows (Fyers intraday hard limit = 100 days)

Pipeline per chunk:
  1. Fetch with 5-cal-day warmup prefix (guarantees ≥150 bar context at chunk start)
  2. Normalize OHLCV (open/high/low/close z-scored per chunk)
  3. Deduplicate timestamps
  4. Save raw OHLCV → Parquet  (ml/training_data/raw_ohlcv/)
  5. Walk-forward ICT simulation  (no lookahead — rolling 150-bar window)
  6. Label each trade with regime epoch
  7. Append to master CSV  (ml/training_data/bt_5y_trades.csv)

Regime labels (for post-hoc segmentation):
  2021-01 → 2022-06  : VOLATILE   (COVID recovery + rate shock)
  2022-07 → 2023-06  : TRENDING   (sustained bull run)
  2023-07 → 2024-06  : MIXED      (chop with swings)
  2024-07 → 2025-05  : RECENT     (recent structure)

Critical safeguards:
  - hold_mins NOT included in feature CSV (post-trade lookahead)
  - Warmup bars excluded from trade generation
  - Bad-request chunks logged and skipped gracefully
  - Each chunk saved separately (resume-safe: skip if Parquet exists)
"""
import sys, os, warnings, time, csv, json
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(override=True)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from scanner.silver_bullet import scan_silver_bullet
from utils.logger import logger

# ── TrueData Auth ──────────────────────────────────────────────────────────────
# TrueData has full intraday history back to 2015 (Fyers only keeps ~1 year).
# truedata-ws v5 REST historical API — no WebSocket needed for batch pulls.
import requests as _req

TD_USER = os.getenv('TRUEDATA_USER', 'true11449')
TD_PASS = os.getenv('TRUEDATA_PASSWORD', 'rahul1449')

_td_session   = None   # requests.Session with auth cookie
_td_auth_time = None

def _td_login() -> bool:
    """Login to TrueData REST API and cache session cookie."""
    global _td_session, _td_auth_time
    try:
        s = _req.Session()
        r = s.post(
            'https://history.truedata.in/login',
            json={'user_n': TD_USER, 'password': TD_PASS},
            timeout=15
        )
        if r.status_code == 200 and r.json().get('status') in ('True', True, 'true'):
            _td_session   = s
            _td_auth_time = time.time()
            print(f"TrueData login OK")
            return True
        print(f"TrueData login failed: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"TrueData login error: {e}")
        return False

def _td_fetch(symbol_td: str, start_str: str, end_str: str,
              bar_size: int = 3) -> pd.DataFrame:
    """
    Fetch historical bars from TrueData REST API.
    symbol_td : 'NIFTY-I' or 'BANKNIFTY-I'  (continuous futures)
    bar_size  : minutes per bar (3 for 3m)
    Returns DataFrame with open/high/low/close/volume/timestamp or empty.
    """
    global _td_session, _td_auth_time

    # Re-login if session expired (>3h)
    if _td_session is None or (time.time() - (_td_auth_time or 0)) > 10800:
        if not _td_login():
            return pd.DataFrame()

    try:
        url = 'https://history.truedata.in/getbars'
        params = {
            'symbol'   : symbol_td,
            'starttime': start_str + ' 09:00:00',
            'endtime'  : end_str   + ' 16:00:00',
            'duration' : f'{bar_size}min',
            'bidask'   : 'false',
        }
        r = _td_session.get(url, params=params, timeout=60)
        if r.status_code != 200:
            print(f"    TrueData HTTP {r.status_code}: {r.text[:200]}")
            return pd.DataFrame()

        data = r.json()
        records = data.get('Records', data.get('data', []))
        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        # Normalise column names (TrueData may vary)
        col_map = {}
        for c in df.columns:
            lc = c.lower()
            if 'time'   in lc or 'date' in lc: col_map[c] = 'timestamp'
            elif 'open'  == lc:                 col_map[c] = 'open'
            elif 'high'  == lc:                 col_map[c] = 'high'
            elif 'low'   == lc:                 col_map[c] = 'low'
            elif 'close' == lc:                 col_map[c] = 'close'
            elif 'vol'   in lc:                 col_map[c] = 'volume'
        df = df.rename(columns=col_map)
        for req_col in ['open','high','low','close']:
            if req_col not in df.columns:
                return pd.DataFrame()
        if 'volume' not in df.columns:
            df['volume'] = 0
        if 'timestamp' not in df.columns:
            return pd.DataFrame()

        df['timestamp'] = pd.to_datetime(df['timestamp'])
        for c in ['open','high','low','close','volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
        return df.sort_values('timestamp').reset_index(drop=True)

    except Exception as e:
        print(f"    TrueData fetch error: {e}")
        return pd.DataFrame()

# TrueData continuous futures symbols
TD_SYMBOLS = {
    'NIFTY'    : 'NIFTY-I',
    'BANKNIFTY': 'BANKNIFTY-I',
}

print(f"TrueData credentials loaded — user={TD_USER}\n")

# ── Output paths ───────────────────────────────────────────────────────────────
_ROOT       = os.path.dirname(__file__)
_RAW_DIR    = os.path.join(_ROOT, 'ml', 'training_data', 'raw_ohlcv')
_MASTER_CSV = os.path.join(_ROOT, 'ml', 'training_data', 'bt_5y_trades.csv')
_CHUNK_LOG  = os.path.join(_ROOT, 'ml', 'training_data', 'bt_5y_chunk_log.json')
os.makedirs(_RAW_DIR, exist_ok=True)

# ── Indices ────────────────────────────────────────────────────────────────────
INDICES = {
    'NIFTY'    : 'NSE:NIFTY26MAYFUT',
    'BANKNIFTY': 'NSE:BANKNIFTY26MAYFUT',
}

# ── 16 Chunks: (label, start, end) ────────────────────────────────────────────
# Each chunk ≤ 100 calendar days (Fyers limit).
# Regime set at chunk level — individual trade date overrides if desired.
CHUNKS = [
    # ── Volatile: COVID recovery / rate shock ─────────────────────────────────
    ('C01', '2021-01-01', '2021-04-10', 'VOLATILE'),
    ('C02', '2021-04-11', '2021-07-19', 'VOLATILE'),
    ('C03', '2021-07-20', '2021-10-27', 'VOLATILE'),
    ('C04', '2021-10-28', '2022-02-04', 'VOLATILE'),
    ('C05', '2022-02-05', '2022-05-16', 'VOLATILE'),
    # ── Trending: sustained bull run ──────────────────────────────────────────
    ('C06', '2022-05-17', '2022-08-24', 'TRENDING'),
    ('C07', '2022-08-25', '2022-12-02', 'TRENDING'),
    ('C08', '2022-12-03', '2023-03-12', 'TRENDING'),
    ('C09', '2023-03-13', '2023-06-20', 'TRENDING'),
    # ── Mixed: chop with swings ───────────────────────────────────────────────
    ('C10', '2023-06-21', '2023-09-28', 'MIXED'),
    ('C11', '2023-09-29', '2024-01-06', 'MIXED'),
    ('C12', '2024-01-07', '2024-04-15', 'MIXED'),
    # ── Recent: 2024-2025 structure ───────────────────────────────────────────
    ('C13', '2024-04-16', '2024-07-24', 'RECENT'),
    ('C14', '2024-07-25', '2024-11-02', 'RECENT'),
    ('C15', '2024-11-03', '2025-02-10', 'RECENT'),
    ('C16', '2025-02-11', '2025-05-20', 'RECENT'),
]

# ── Backtest config ────────────────────────────────────────────────────────────
WARMUP_DAYS = 5    # calendar days before chunk_start to fetch for rolling context
WINDOW      = 150  # rolling lookback bars (150×3min = 7.5h)
COOLDOWN    = 10   # bars cooldown after exit
MAX_HOLD    = 120  # max bars to hold
EOD_H, EOD_M = 15, 20
SCAN_H      = 10

# ── Load existing chunk log (for resume) ──────────────────────────────────────
try:
    with open(_CHUNK_LOG) as f:
        chunk_log = json.load(f)
except Exception:
    chunk_log = {}


def _save_chunk_log():
    with open(_CHUNK_LOG, 'w') as f:
        json.dump(chunk_log, f, indent=2)


# ── Regime from date ──────────────────────────────────────────────────────────
def _regime_from_date(date_str: str) -> str:
    y, m = int(date_str[:4]), int(date_str[5:7])
    if   (y == 2021) or (y == 2022 and m <= 5): return 'VOLATILE'
    elif (y == 2022 and m >= 6) or (y == 2023 and m <= 6): return 'TRENDING'
    elif (y == 2023 and m >= 7) or (y == 2024 and m <= 6): return 'MIXED'
    else: return 'RECENT'


# ── Market hours filter ────────────────────────────────────────────────────────
def _mkt_filter(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Ensure timestamp column exists as 'timestamp' (datetime)
    ts_col = 'timestamp' if 'timestamp' in df.columns else df.columns[0]
    df['timestamp'] = pd.to_datetime(df[ts_col])
    df['ts'] = df['timestamp']
    return df[
        ((df.ts.dt.hour > 9) | ((df.ts.dt.hour == 9) & (df.ts.dt.minute >= 15))) &
        (df.ts.dt.hour < 16)
    ].reset_index(drop=True)


# ── OHLCV z-score normalisation (per-chunk, for CNN/raw storage) ───────────────
def _normalise_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ['open', 'high', 'low', 'close']:
        mu  = df[col].mean()
        sig = df[col].std() + 1e-8
        df[f'{col}_z'] = ((df[col] - mu) / sig).round(6)
    vol_mu  = df['volume'].mean()
    vol_sig = df['volume'].std() + 1e-8
    df['volume_z'] = ((df['volume'] - vol_mu) / vol_sig).round(6)
    return df


# ── Fetch one chunk ────────────────────────────────────────────────────────────
def fetch_chunk(symbol_unused: str, idx_name: str, chunk_id: str,
                start_str: str, end_str: str) -> pd.DataFrame:
    """
    Fetch via TrueData REST (full history back to 2015).
    [start - warmup_days, end] fetched; warmup bars used for rolling context only.
    Saved to Parquet for resume support.
    """
    parquet_path = os.path.join(_RAW_DIR, f"{idx_name}_{chunk_id}.parquet")

    # Resume: load from disk if already fetched
    if os.path.exists(parquet_path):
        df = pd.read_parquet(parquet_path)
        df['ts'] = pd.to_datetime(df['timestamp'])
        print(f"    [CACHE] {len(df)} bars from {os.path.basename(parquet_path)}")
        return df

    td_symbol    = TD_SYMBOLS.get(idx_name)
    if not td_symbol:
        print(f"    [SKIP] No TrueData symbol for {idx_name}")
        return pd.DataFrame()

    warmup_start = (datetime.strptime(start_str, '%Y-%m-%d') - timedelta(days=WARMUP_DAYS))
    warmup_str   = warmup_start.strftime('%Y-%m-%d')

    t0  = time.time()
    df  = _td_fetch(td_symbol, warmup_str, end_str, bar_size=3)
    elapsed = time.time() - t0

    if df is None or len(df) == 0:
        print(f"    [SKIP] {idx_name} {chunk_id}: TrueData returned no data")
        return pd.DataFrame()

    # Align timestamp column
    df = df.rename(columns={'timestamp': 'timestamp'})
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = _mkt_filter(df)
    df = _normalise_ohlcv(df)
    df = df.drop_duplicates(subset='timestamp').sort_values('ts').reset_index(drop=True)

    # Save
    df.to_parquet(parquet_path, index=False)
    days_n = df.ts.dt.date.nunique()
    print(f"    [FETCH] {len(df)} bars  {days_n}d  "
          f"({df.ts.iloc[0].strftime('%Y-%m-%d')} → {df.ts.iloc[-1].strftime('%Y-%m-%d')})  "
          f"[{elapsed:.1f}s]  → {os.path.basename(parquet_path)}")
    return df


# ── Walk-forward simulation ────────────────────────────────────────────────────
def simulate_chunk(df: pd.DataFrame, symbol: str, idx_name: str,
                   chunk_id: str, chunk_start_str: str, regime: str) -> list:
    """
    Walk-forward ICT simulation.
    Warmup bars (before chunk_start) feed the rolling window but produce no trades.
    """
    chunk_start = datetime.strptime(chunk_start_str, '%Y-%m-%d')
    trades = []
    i = skip = 0
    bar_count = len(df)

    while i < bar_count:
        if i < skip:
            i += 1
            continue

        ts = df['ts'].iloc[i]

        # Skip warmup bars — only trade from official chunk start
        if ts.date() < chunk_start.date():
            i += 1
            continue

        # Scan window: 10:00–15:20 IST
        if ts.hour < SCAN_H or ts.hour >= EOD_H:
            i += 1
            continue

        # Rolling 150-bar window (includes warmup bars as context ✓)
        ws  = max(0, i - WINDOW)
        win = df.iloc[ws:i+1][['timestamp','open','high','low','close','volume']].copy()

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

        # ── Forward walk ───────────────────────────────────────────────────────
        outcome = 'TIMEOUT'
        exit_p  = None
        r_act   = 0.0
        exit_i  = min(i + MAX_HOLD, bar_count - 1)

        for j in range(i + 1, min(i + MAX_HOLD, bar_count)):
            row = df.iloc[j]
            h, l = float(row['high']), float(row['low'])
            tsj  = row['ts']

            if tsj.hour >= EOD_H and tsj.minute >= EOD_M:
                exit_p = float(row['close'])
                r_act  = round((exit_p-entry)/risk if dirn=='BULLISH'
                               else (entry-exit_p)/risk, 2)
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
            exit_p = float(df.iloc[exit_i]['close'])
            r_act  = round((exit_p-entry)/risk if dirn=='BULLISH'
                           else (entry-exit_p)/risk, 2)

        hold_mins = (exit_i - i) * 3
        fvg = setup.get('fvg', {})

        # ── Feature row — ENTRY-TIME ONLY (no lookahead) ──────────────────────
        trade_date = ts.strftime('%Y-%m-%d')
        trades.append({
            # Identity
            'chunk'      : chunk_id,
            'index'      : idx_name,
            'regime'     : regime,
            'date'       : trade_date,
            'time'       : ts.strftime('%H:%M'),
            'exit_time'  : df['ts'].iloc[exit_i].strftime('%H:%M'),
            # Trade params (known at entry)
            'dir'        : 'LONG' if dirn == 'BULLISH' else 'SHORT',
            'entry'      : round(entry, 1),
            'sl'         : round(sl,    1),
            't1'         : round(t1,    1),
            't2'         : round(t2,    1),
            't3'         : round(t3,    1),
            'risk_pts'   : round(risk,  1),
            # ICT features (known at entry)
            'score'      : setup.get('confluence', 0),
            'mss'        : setup.get('mss_type',   '?'),
            'fvg_size'   : round(fvg.get('size',   0), 2),
            'fvg_top'    : round(fvg.get('top',    0), 2),
            'fvg_bottom' : round(fvg.get('bottom', 0), 2),
            'hour'       : ts.hour,
            'minute'     : ts.minute,
            'weekday'    : ts.weekday(),
            # Derived entry-time features
            'rr_t1'      : round(abs(t1-entry)/max(risk,0.01), 3),
            'rr_t2'      : round(abs(t2-entry)/max(risk,0.01), 3),
            'rr_t3'      : round(abs(t3-entry)/max(risk,0.01), 3),
            'fvg_ratio'  : round(fvg.get('size',0)/max(risk,0.01), 3),
            # Outcome (labels)
            'outcome'    : outcome,
            'exit_price' : round(exit_p, 1) if exit_p else 0,
            'r'          : r_act,
            'win'        : 1 if r_act > 0 else 0,
            # Post-trade (NOT used as model input — analysis only)
            'hold_mins'  : hold_mins,
        })

        skip = exit_i + COOLDOWN
        i    = exit_i + 1

    return trades


# ── Per-chunk summary ──────────────────────────────────────────────────────────
def chunk_summary(trades: list, chunk_id: str, idx_name: str, regime: str) -> str:
    if not trades:
        return f"    {idx_name} {chunk_id} [{regime}]: 0 trades"
    wins   = sum(1 for t in trades if t['r'] > 0)
    total  = len(trades)
    wr     = round(wins/total*100, 1)
    total_r = round(sum(t['r'] for t in trades), 2)
    return (f"    {idx_name} {chunk_id} [{regime}]: "
            f"{total} trades  WR:{wr}%  R:{total_r:+.2f}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

print("=" * 68)
print("  CB6 QUANTUM — 5-Year Historical 3m Backtest Pipeline")
print("  Period: 2021-01-01 → 2025-05-20  |  NIFTY + BANKNIFTY")
print("  16 Chunks × 2 Indices  |  Warmup: 5 cal-days per chunk")
print("=" * 68)

# Open master CSV (append mode)
csv_exists  = os.path.exists(_MASTER_CSV)
csv_file    = open(_MASTER_CSV, 'a', newline='', encoding='utf-8')
all_fieldnames = [
    'chunk','index','regime','date','time','exit_time',
    'dir','entry','sl','t1','t2','t3','risk_pts',
    'score','mss','fvg_size','fvg_top','fvg_bottom',
    'hour','minute','weekday','rr_t1','rr_t2','rr_t3','fvg_ratio',
    'outcome','exit_price','r','win',
    'hold_mins',   # ← analysis only; excluded from ML features
]
csv_writer = csv.DictWriter(csv_file, fieldnames=all_fieldnames)
if not csv_exists:
    csv_writer.writeheader()

total_trades = 0
chunk_results = {}

for chunk_id, start_str, end_str, regime in CHUNKS:
    print(f"\n{'─'*68}")
    print(f"  Chunk {chunk_id}  [{start_str} → {end_str}]  regime={regime}")
    print(f"{'─'*68}")

    chunk_trades_all = []

    for idx_name, symbol in INDICES.items():
        log_key = f"{idx_name}_{chunk_id}"

        # Skip if already processed (resume support)
        if chunk_log.get(log_key, {}).get('done'):
            cached_n = chunk_log[log_key].get('n_trades', 0)
            print(f"    [SKIP] {idx_name} {chunk_id} — already done ({cached_n} trades)")
            continue

        print(f"  ▶ {idx_name}  [{symbol}]")

        # 1. Fetch (with warmup)
        df3 = fetch_chunk(symbol, idx_name, chunk_id, start_str, end_str)
        if df3.empty:
            chunk_log[log_key] = {'done': True, 'n_trades': 0, 'status': 'NO_DATA'}
            _save_chunk_log()
            continue

        # 2. Walk-forward simulation
        t0     = time.time()
        trades = simulate_chunk(df3, symbol, idx_name, chunk_id, start_str, regime)
        elapsed = time.time() - t0
        print(chunk_summary(trades, chunk_id, idx_name, regime) + f"  [{elapsed:.0f}s]")

        # 3. Write to master CSV
        if trades:
            csv_writer.writerows(trades)
            csv_file.flush()

        # 4. Update log
        chunk_log[log_key] = {
            'done'    : True,
            'n_trades': len(trades),
            'wr'      : round(sum(1 for t in trades if t['r']>0)/max(len(trades),1)*100,1),
            'status'  : 'OK',
        }
        _save_chunk_log()

        chunk_trades_all.extend(trades)
        total_trades += len(trades)

    chunk_results[chunk_id] = chunk_trades_all

csv_file.close()

# ── Final summary ──────────────────────────────────────────────────────────────
print(f"\n{'#'*68}")
print(f"  PIPELINE COMPLETE")
print(f"{'#'*68}")

# Reload full CSV for stats
if os.path.exists(_MASTER_CSV):
    df_all = pd.read_csv(_MASTER_CSV)
    total  = len(df_all)
    wins   = df_all['win'].sum()
    wr     = round(wins/total*100, 1) if total else 0
    total_r = round(df_all['r'].sum(), 2)
    win_r   = df_all[df_all['r']>0]['r'].sum()
    loss_r  = df_all[df_all['r']<=0]['r'].abs().sum()
    pf      = round(win_r/loss_r, 2) if loss_r > 0 else float('inf')

    print(f"\n  Total Trades : {total}")
    print(f"  Win Rate     : {wr}%")
    print(f"  Total R      : {total_r:+.2f}R")
    print(f"  Profit Factor: {pf}")
    print(f"\n  By Regime:")
    for regime in ['VOLATILE','TRENDING','MIXED','RECENT']:
        sub = df_all[df_all['regime']==regime]
        if len(sub) == 0: continue
        sw  = sub['win'].sum()
        sr  = round(sub['r'].sum(), 2)
        swr = round(sw/len(sub)*100, 1)
        print(f"    {regime:10s}  {len(sub):4d} trades  WR:{swr:5.1f}%  R:{sr:+8.2f}")
    print(f"\n  By Index:")
    for idx in ['NIFTY','BANKNIFTY']:
        sub = df_all[df_all['index']==idx]
        if len(sub) == 0: continue
        sw  = sub['win'].sum()
        sr  = round(sub['r'].sum(), 2)
        swr = round(sw/len(sub)*100, 1)
        print(f"    {idx:12s}  {len(sub):4d} trades  WR:{swr:5.1f}%  R:{sr:+8.2f}")
    print(f"\n  By Chunk:")
    for cid in [c[0] for c in CHUNKS]:
        sub = df_all[df_all['chunk']==cid]
        if len(sub) == 0: continue
        sw  = sub['win'].sum()
        swr = round(sw/len(sub)*100, 1)
        sr  = round(sub['r'].sum(), 2)
        reg = sub['regime'].iloc[0]
        dates = f"{sub['date'].min()} → {sub['date'].max()}"
        print(f"    {cid}  {reg:8s}  {len(sub):3d}  WR:{swr:5.1f}%  R:{sr:+7.2f}  {dates}")

print(f"\n  Master CSV : {_MASTER_CSV}")
print(f"  Raw Parquet: {_RAW_DIR}/")
print(f"  Chunk log  : {_CHUNK_LOG}")
print(f"\n  ⚡ Ready for bt_trainer.py — run with the 5y CSV to retrain DNN/CNN/RNN")
print(f"     python ml/bt_trainer.py --csv ml/training_data/bt_5y_trades.csv")
