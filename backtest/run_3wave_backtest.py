"""
3-Wave Backtest — CB6 Quantum
Compares old code (H4 hard gate) vs new code (3-wave filter only)
over the last 15 trading days for Forex + NSE.

Output: per-symbol table showing
  - setups found by new code
  - setups OLD code would have blocked (H4 mismatch)
  - wave count distribution at those blocked setups
  - estimated missed RR
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings
warnings.filterwarnings('ignore')

from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd
import numpy as np

# ── Config ────────────────────────────────────────────────────────────────────
LOOKBACK_DAYS = 15
WINDOW        = 100     # bars of rolling context for scanner
WAVE_LB       = 80      # count_impulse_waves lookback
BASE_LB       = 8       # detect_wave_base lookback

FOREX_SYMBOLS = {
    'XAGUSD': 'SI=F',
    'USOIL' : 'CL=F',
    'XAUUSD': 'GC=F',
}
NSE_SYMBOLS = {
    'NIFTY'    : '^NSEI',
    'BANKNIFTY': '^NSEBANK',
    'FINNIFTY' : 'NIFTY_FIN_SERVICE.NS',
}

# Kill zone / window checks (UTC for Forex, IST for NSE)
FOREX_KZ = [(7, 12), (16, 20)]

def _in_forex_kz(dt_utc: datetime) -> bool:
    h = dt_utc.hour
    return any(s <= h < e for s, e in FOREX_KZ)

NSE_WINDOWS_IST = [(10, 0, 11, 0), (13, 0, 14, 0), (15, 0, 15, 30)]
def _in_nse_window(dt_ist: datetime) -> bool:
    h, m = dt_ist.hour, dt_ist.minute
    for wh, wm, eh, em in NSE_WINDOWS_IST:
        if (h * 60 + m) >= (wh * 60 + wm) and (h * 60 + m) < (eh * 60 + em):
            return True
    return False

# ── Data fetch ────────────────────────────────────────────────────────────────
def fetch_yfinance(ticker: str, interval: str = '15m', days: int = 20) -> pd.DataFrame:
    try:
        import yfinance as yf
        period = f'{days}d'
        df = yf.download(ticker, period=period, interval=interval,
                         auto_adjust=True, progress=False)
        if df.empty:
            return pd.DataFrame()
        df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
        df = df.rename(columns={'open':'open','high':'high','low':'low',
                                'close':'close','volume':'volume'})
        df = df[['open','high','low','close','volume']].dropna()
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    except Exception as e:
        print(f"  yfinance fetch error for {ticker}: {e}")
        return pd.DataFrame()

def get_h4_bias(df_15m: pd.DataFrame) -> str:
    """Resample 15m → 4H and compute EMA(3) vs EMA(8)."""
    try:
        h4 = df_15m['close'].resample('4h').last().dropna()
        if len(h4) < 10:
            return 'RANGING'
        e3 = h4.ewm(span=3, adjust=False).mean().iloc[-1]
        e8 = h4.ewm(span=8, adjust=False).mean().iloc[-1]
        if e3 > e8 * 1.0003:
            return 'BULLISH'
        elif e3 < e8 * 0.9997:
            return 'BEARISH'
        return 'RANGING'
    except Exception:
        return 'RANGING'

# ── Core pattern detection (standalone, no broker deps) ───────────────────────
def count_waves(df: pd.DataFrame, trend_dir: str, lookback: int = 80) -> int:
    """Count impulse legs in trend_dir direction. Mirror of mtf_dol_scanner logic."""
    try:
        from forex_engine.scanner.mtf_dol_scanner import count_impulse_waves
        return count_impulse_waves(df, trend_dir, lookback)
    except Exception:
        pass
    # Fallback: simplified swing-high/low counter
    sl = df['low'].values[-lookback:]
    sh = df['high'].values[-lookback:]
    waves = 0
    if trend_dir == 'BEARISH':
        prev_low = sl[0]
        had_pb   = False
        for i in range(1, len(sl)):
            if sh[i] > sh[max(0, i-3):i].max() * 1.001:
                had_pb = True
            if sl[i] < prev_low * 0.9995 and had_pb:
                waves += 1
                prev_low = sl[i]
                had_pb   = False
    else:
        prev_high = sh[0]
        had_pb    = False
        for i in range(1, len(sh)):
            if sl[i] < sl[max(0, i-3):i].min() * 0.9995:
                had_pb = True
            if sh[i] > prev_high * 1.0005 and had_pb:
                waves += 1
                prev_high = sh[i]
                had_pb    = False
    return min(waves, 8)

def detect_sweep(df: pd.DataFrame, direction: str, lookback: int = 30) -> bool:
    """Simple: price made new extreme in trend direction then reversed."""
    sub = df.iloc[-lookback:]
    if direction == 'BEARISH':
        recent_low = sub['low'].min()
        prev_low   = df.iloc[-lookback-20:-lookback]['low'].min() if len(df) > lookback + 20 else sub['low'].min()
        new_extreme = recent_low < prev_low * 0.9995
        recovered   = df['close'].iloc[-1] > recent_low * 1.001
        return new_extreme and recovered
    else:
        recent_high = sub['high'].max()
        prev_high   = df.iloc[-lookback-20:-lookback]['high'].max() if len(df) > lookback + 20 else sub['high'].max()
        new_extreme = recent_high > prev_high * 1.0005
        recovered   = df['close'].iloc[-1] < recent_high * 0.999
        return new_extreme and recovered

def detect_choch(df: pd.DataFrame, direction: str, lookback: int = 20) -> bool:
    """Detect CHoCH: first break of structure in opposite direction after trend."""
    sub   = df.iloc[-lookback:]
    close = sub['close'].values
    high  = sub['high'].values
    low   = sub['low'].values
    if direction == 'BULLISH':
        recent_high = high[:-3].max()
        return close[-1] > recent_high
    else:
        recent_low = low[:-3].min()
        return close[-1] < recent_low

def detect_fvg(df: pd.DataFrame, direction: str) -> bool:
    """3-candle FVG pattern."""
    if len(df) < 3:
        return False
    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if direction == 'BULLISH':
        return c3['low'] > c1['high']
    else:
        return c3['high'] < c1['low']

def detect_ob(df: pd.DataFrame, direction: str, lookback: int = 20) -> bool:
    """Order block: last opposite-direction candle before impulse move."""
    sub = df.iloc[-lookback:]
    if direction == 'BULLISH':
        bearish = sub[sub['close'] < sub['open']]
        return len(bearish) > 0
    else:
        bullish = sub[sub['close'] > sub['open']]
        return len(bullish) > 0

# ── Scan single bar ───────────────────────────────────────────────────────────
def scan_bar(df_window: pd.DataFrame, h4_bias: str, min_score: int = 11,
             market: str = 'FOREX') -> Optional[dict]:
    """
    Scan one bar. Returns setup dict or None.
    Checks both BULLISH and BEARISH directions.
    """
    results = []
    for direction in ('BULLISH', 'BEARISH'):
        fade_dir = 'BEARISH' if direction == 'BULLISH' else 'BULLISH'

        wave_count = count_waves(df_window, fade_dir, WAVE_LB)
        sweep_ok   = detect_sweep(df_window, fade_dir, lookback=25)
        choch_ok   = detect_choch(df_window, direction, lookback=20)
        fvg_ok     = detect_fvg(df_window, direction)
        ob_ok      = detect_ob(df_window, direction, lookback=20)

        # Score: each component = points
        score = 0
        score += 3 if sweep_ok   else 0
        score += 3 if choch_ok   else 0
        score += 2 if fvg_ok     else 0
        score += 1 if ob_ok      else 0
        score += 2 if wave_count >= 3 else (1 if wave_count == 2 else 0)

        if score < min_score:
            continue
        if not (sweep_ok and choch_ok):
            continue

        h4_agrees     = h4_bias == direction or h4_bias == 'RANGING'
        old_blocked   = not h4_agrees          # H4 hard gate would have blocked this
        new_passes    = wave_count >= 3 and sweep_ok   # 3-wave filter allows it

        results.append({
            'direction'   : direction,
            'wave_count'  : wave_count,
            'score'       : score,
            'sweep'       : sweep_ok,
            'choch'       : choch_ok,
            'fvg'         : fvg_ok,
            'ob'          : ob_ok,
            'h4_bias'     : h4_bias,
            'h4_agrees'   : h4_agrees,
            'old_blocked' : old_blocked,
            'new_passes'  : new_passes,
        })

    if not results:
        return None
    return max(results, key=lambda x: x['score'])

# ── Run backtest for one symbol ───────────────────────────────────────────────
def run_symbol(symbol: str, ticker: str, market: str) -> list[dict]:
    print(f"  Fetching {symbol} ({ticker}) ...")
    df_all = fetch_yfinance(ticker, interval='15m', days=LOOKBACK_DAYS + 5)
    if df_all.empty or len(df_all) < WINDOW + 10:
        print(f"  {symbol}: insufficient data ({len(df_all)} bars)")
        return []

    # Only keep last LOOKBACK_DAYS
    cutoff = pd.Timestamp.now(tz='UTC') - timedelta(days=LOOKBACK_DAYS)
    df_all = df_all[df_all.index >= cutoff]

    rows = []
    for i in range(WINDOW, len(df_all)):
        ts      = df_all.index[i]
        df_win  = df_all.iloc[i - WINDOW: i + 1]
        h4_bias = get_h4_bias(df_all.iloc[max(0, i - 200): i + 1])

        # Session/window gate
        if market == 'FOREX':
            if not _in_forex_kz(ts.to_pydatetime()):
                continue
        else:
            ts_ist = ts.tz_convert('Asia/Kolkata') if ts.tzinfo else ts
            if not _in_nse_window(ts_ist):
                continue

        setup = scan_bar(df_win, h4_bias, min_score=9, market=market)
        if setup is None:
            continue

        rows.append({
            'timestamp'  : ts,
            'symbol'     : symbol,
            'market'     : market,
            'direction'  : setup['direction'],
            'wave_count' : setup['wave_count'],
            'score'      : setup['score'],
            'sweep'      : setup['sweep'],
            'choch'      : setup['choch'],
            'fvg'        : setup['fvg'],
            'ob'         : setup['ob'],
            'h4_bias'    : setup['h4_bias'],
            'h4_agrees'  : setup['h4_agrees'],
            'old_blocked': setup['old_blocked'],
            'new_passes' : setup['new_passes'],
        })

    print(f"  {symbol}: {len(rows)} setups found in window")
    return rows

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("CB6 QUANTUM — 3-Wave Backtest (last 15 days)")
    print(f"Period : {(datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')} → {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 65)

    all_rows: list[dict] = []

    print("\n--- FOREX ---")
    for sym, ticker in FOREX_SYMBOLS.items():
        rows = run_symbol(sym, ticker, 'FOREX')
        all_rows.extend(rows)

    print("\n--- NSE ---")
    for sym, ticker in NSE_SYMBOLS.items():
        rows = run_symbol(sym, ticker, 'NSE')
        all_rows.extend(rows)

    if not all_rows:
        print("\nNo setups found. Check data connectivity.")
        return

    df = pd.DataFrame(all_rows)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("RESULTS SUMMARY")
    print("=" * 65)

    total        = len(df)
    old_blocked  = df['old_blocked'].sum()
    new_passes   = df['new_passes'].sum()
    both         = df[df['old_blocked'] & df['new_passes']]
    wave3_plus   = (df['wave_count'] >= 3).sum()

    print(f"\nTotal valid setups (new code)   : {total}")
    print(f"Old code would have blocked     : {old_blocked}  ({old_blocked/total*100:.0f}%)")
    print(f"3-wave filter passes            : {new_passes}   ({new_passes/total*100:.0f}%)")
    print(f"Blocked by H4 but wave≥3        : {len(both)}   ← missed by old code, valid by 3-wave rule")

    print("\n--- By Symbol ---")
    grp = df.groupby('symbol').agg(
        total    =('score',       'count'),
        blocked  =('old_blocked', 'sum'),
        wave3    =('wave_count',  lambda x: (x >= 3).sum()),
        avg_wave =('wave_count',  'mean'),
        avg_score=('score',       'mean'),
    ).sort_values('blocked', ascending=False)
    print(grp.to_string())

    print("\n--- Wave Count Distribution (blocked setups) ---")
    if len(both) > 0:
        print(both['wave_count'].value_counts().sort_index().to_string())

    print("\n--- H4 Bias at Blocked Setups ---")
    if old_blocked > 0:
        blocked_df = df[df['old_blocked']]
        print(blocked_df.groupby(['symbol','h4_bias','direction']).size()
              .rename('count').reset_index().to_string(index=False))

    print("\n--- Setups by Direction ---")
    print(df.groupby(['market','direction']).agg(
        total   =('score',       'count'),
        blocked =('old_blocked', 'sum'),
        wave3   =('wave_count',  lambda x: (x >= 3).sum()),
    ).to_string())

    print("\n--- Top 10 Highest-Score Missed Setups (blocked by old, passes new) ---")
    if len(both) > 0:
        top = both.sort_values('score', ascending=False).head(10)
        cols = ['timestamp','symbol','direction','wave_count','score','h4_bias','fvg','ob']
        print(top[cols].to_string(index=False))

    # Save to CSV
    out = 'data/backtest_results/3wave_backtest_15d.csv'
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nFull results saved to: {out}")
    print("=" * 65)

if __name__ == '__main__':
    main()
