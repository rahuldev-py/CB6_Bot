"""
CB6 Quantum - Forex MT5 Backtest (XAUUSD / XAGUSD / USOIL)
============================================================
Data source : FTMO MT5 terminal — real broker 15m OHLCV bars
Period      : 2024-05-27 to 2026-05-27 (2 years)
Strategy    : ICT Silver Bullet — same CHoCH+BOS+FVG chain as live engine
Session     : London 07-12 UTC  |  NY 16-20 UTC  (same as live forex_worker.py)
Timeframe   : 15m

Output:
  data/backtests/forex_mt5/results/bt_forex_mt5_<date>.csv
  data/backtests/forex_mt5/results/bt_forex_mt5_<date>_summary.txt

Usage:
  python _bt_forex_mt5.py
  python _bt_forex_mt5.py --symbol XAUUSD   # single symbol
  python _bt_forex_mt5.py --from 2025-01-01  # custom start date
"""
from __future__ import annotations
import argparse, os, sys, time, warnings, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

warnings.filterwarnings('ignore')
# Suppress verbose scanner INFO logs — only show WARNING+ during backtest
logging.disable(logging.INFO)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
_env = dotenv_values(ROOT / '.env')
for k, v in _env.items():
    if k not in os.environ:
        os.environ[k] = v

import MetaTrader5 as mt5
import pandas as pd

# ── Output dirs ─────────────────────────────────────────────────────────────────
RESULTS_DIR = ROOT / 'data' / 'backtests' / 'forex_mt5' / 'results'
ML_DIR      = ROOT / 'ml' / 'training_data'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
ML_DIR.mkdir(parents=True, exist_ok=True)

# ── Strategy params (mirrors live forex_worker.py) ──────────────────────────────
SESSION_WINDOWS_UTC = [(7, 12), (16, 20)]   # London + NY kill zones
MIN_RR    = 3.0
MIN_SCORE = 11
COOLDOWN  = 10    # candles (10 × 15m = 150min cooldown after trade)
MAX_HOLD  = 32    # candles (32 × 15m = 8 hours max hold)
WINDOW    = 120   # lookback candles for structure detection

# ── Symbol config (FTMO MT5 names + instrument specs) ──────────────────────────
SYMBOL_CFG = {
    'XAUUSD': {
        'mt5_sym'   : 'XAUUSD',
        'min_sl'    : 3.00,
        'min_fvg'   : 2.00,
        'fvg_buf'   : 0.50,
        'max_spread': 2.00,
    },
    'XAGUSD': {
        'mt5_sym'   : 'XAGUSD',
        'min_sl'    : 0.05,
        'min_fvg'   : 0.025,
        'fvg_buf'   : 0.02,
        'max_spread': 0.20,
    },
    'USOIL': {
        'mt5_sym'   : 'USOIL.cash',
        'min_sl'    : 0.50,
        'min_fvg'   : 0.25,
        'fvg_buf'   : 0.05,
        'max_spread': 0.30,
    },
}


# ── Session filter ───────────────────────────────────────────────────────────────

def _in_session(utc_hour: int) -> bool:
    return any(s <= utc_hour < e for s, e in SESSION_WINDOWS_UTC)


# ── MT5 data loader ──────────────────────────────────────────────────────────────

def _load_mt5(mt5_sym: str, since: datetime, until: datetime) -> pd.DataFrame:
    mt5.symbol_select(mt5_sym, True)
    time.sleep(0.3)
    rates = mt5.copy_rates_range(mt5_sym, mt5.TIMEFRAME_M15, since, until)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.set_index('time')
    df = df.rename(columns={'tick_volume': 'volume'})
    cols = [c for c in ['open','high','low','close','volume','spread'] if c in df.columns]
    return df[cols].astype(float)


# ── Scanner (same logic as signal_scanner.py) ───────────────────────────────────

def _scan(df: pd.DataFrame, symbol: str) -> dict | None:
    """Run ICT Silver Bullet chain on a 15m slice."""
    try:
        from scanner.silver_bullet import (
            find_draw_on_liquidity, detect_sb_mss, detect_sb_fvg,
            detect_liquidity_sweep, detect_order_block,
            premium_discount_context, premium_discount_aligned,
        )
        from scanner.ut_bot import get_ut_signal
        from forex_engine.scanner.signal_scanner import scan_setup
        return scan_setup(df, symbol, min_rr=MIN_RR)
    except Exception as e:
        return None


# ── Single-candle trade simulator ────────────────────────────────────────────────

def _simulate(setup: dict, df_future: pd.DataFrame) -> dict:
    """
    Walk forward candle-by-candle and determine exit (T1/T2/T3/SL/TIMEOUT).
    Returns trade result dict.
    """
    sig  = setup['entry_signal']
    entry  = sig['entry']
    sl     = sig['stop_loss']
    t1, t2, t3 = sig['target1'], sig['target2'], sig['target3']
    risk   = abs(entry - sl)
    direct = setup['direction']   # BULLISH / BEARISH

    current_sl = sl
    t1_hit = False
    exit_price  = None
    exit_reason = 'TIMEOUT'
    hold_bars   = 0

    for i, (ts, row) in enumerate(df_future.iterrows()):
        if i >= MAX_HOLD:
            exit_price  = float(row['close'])
            exit_reason = 'TIMEOUT'
            hold_bars   = i
            break

        hi = float(row['high'])
        lo = float(row['low'])

        if direct == 'BULLISH':
            if lo <= current_sl:
                exit_price  = current_sl
                exit_reason = 'SL' if not t1_hit else 'SL_AFTER_T1'
                hold_bars   = i
                break
            if not t1_hit and hi >= t1:
                t1_hit     = True
                current_sl = entry   # move SL to BE
            # Check T3 before T2 — if price blows through both in one candle, credit T3
            if t1_hit and hi >= t3:
                exit_price  = t3
                exit_reason = 'T3'
                hold_bars   = i
                break
            if t1_hit and hi >= t2:
                exit_price  = t2
                exit_reason = 'T2'
                hold_bars   = i
                break
        else:  # BEARISH
            if hi >= current_sl:
                exit_price  = current_sl
                exit_reason = 'SL' if not t1_hit else 'SL_AFTER_T1'
                hold_bars   = i
                break
            if not t1_hit and lo <= t1:
                t1_hit     = True
                current_sl = entry
            # Check T3 before T2 — if price blows through both in one candle, credit T3
            if t1_hit and lo <= t3:
                exit_price  = t3
                exit_reason = 'T3'
                hold_bars   = i
                break
            if t1_hit and lo <= t2:
                exit_price  = t2
                exit_reason = 'T2'
                hold_bars   = i
                break
    else:
        exit_price  = float(df_future['close'].iloc[-1]) if len(df_future) > 0 else entry
        exit_reason = 'TIMEOUT'
        hold_bars   = len(df_future)

    if exit_price is None:
        exit_price  = float(df_future['close'].iloc[-1]) if len(df_future) > 0 else entry
        exit_reason = 'TIMEOUT'
        hold_bars   = len(df_future)

    r_actual = (exit_price - entry) / risk if direct == 'BULLISH' else (entry - exit_price) / risk
    r_actual = round(r_actual, 3)

    return {
        'exit_price' : round(exit_price, 5),
        'exit_reason': exit_reason,
        'r'          : r_actual,
        'hold_bars'  : hold_bars,
        'hold_mins'  : hold_bars * 15,
    }


# ── Per-symbol backtest ──────────────────────────────────────────────────────────

def run_symbol(symbol: str, df: pd.DataFrame, cfg: dict) -> list[dict]:
    trades = []
    cooldown_left = 0

    for i in range(WINDOW, len(df) - MAX_HOLD - 1):
        row = df.iloc[i]
        ts  = df.index[i]
        utc_hour = ts.hour

        # Session gate
        if not _in_session(utc_hour):
            continue

        # Cooldown
        if cooldown_left > 0:
            cooldown_left -= 1
            continue

        # Spread filter (use spread column if available, else skip check)
        if 'spread' in df.columns:
            raw_spread = float(df['spread'].iloc[i])
            pt_size    = 0.01 if symbol == 'XAUUSD' else 0.001
            spread_val = raw_spread * pt_size
            if spread_val > cfg['max_spread']:
                continue

        # Run scanner on lookback window
        slice_df = df.iloc[i - WINDOW : i + 1].copy()
        setup    = _scan(slice_df, symbol)
        if setup is None:
            continue

        # Score gate
        if setup['confluence'] < MIN_SCORE:
            continue

        # Hard blocks: sweep + inFVG required
        if not setup.get('sweep_confirmed'):
            continue
        if not setup.get('in_fvg'):
            continue

        # Simulate trade on next MAX_HOLD candles
        future_df = df.iloc[i + 1 : i + 1 + MAX_HOLD].copy()
        if len(future_df) < 3:
            continue

        result = _simulate(setup, future_df)
        sig    = setup['entry_signal']

        # Session label
        if 7 <= utc_hour < 12:
            session = 'London (07-12)'
        elif 16 <= utc_hour < 20:
            session = 'NY (16-20)'
        else:
            session = 'Other'

        trades.append({
            'symbol'   : symbol,
            'date'     : ts.strftime('%Y-%m-%d'),
            'time'     : ts.strftime('%H:%M'),
            'hour'     : utc_hour,
            'weekday'  : ts.weekday(),
            'dir'      : 'LONG' if setup['direction'] == 'BULLISH' else 'SHORT',
            'mss'      : setup.get('mss_type', 'BOS'),
            'score'    : setup['confluence'],
            'entry'    : round(sig['entry'], 5),
            'stop_loss': round(sig['stop_loss'], 5),
            'target1'  : round(sig['target1'], 5),
            'target2'  : round(sig['target2'], 5),
            'target3'  : round(sig['target3'], 5),
            'risk_pts' : round(abs(sig['entry'] - sig['stop_loss']), 5),
            'rr_t2'    : round(sig['rr_ratio'], 2),
            'exit_price': result['exit_price'],
            'r'        : result['r'],
            'outcome'  : result['exit_reason'],
            'hold_mins': result['hold_mins'],
            'session'  : session,
        })

        cooldown_left = COOLDOWN

    return trades


# ── Summary printer ──────────────────────────────────────────────────────────────

def _print_summary(df_trades: pd.DataFrame, symbol: str) -> str:
    sub   = df_trades[df_trades['symbol'] == symbol] if symbol != 'ALL' else df_trades
    if sub.empty:
        return f"  {symbol}: no trades"

    total   = len(sub)
    wins    = sub[sub['r'] > 0]
    wr      = len(wins) / total * 100
    total_r = sub['r'].sum()
    avg_r   = sub['r'].mean()
    pf_num  = wins['r'].sum()
    pf_den  = abs(sub[sub['r'] <= 0]['r'].sum())
    pf      = round(pf_num / pf_den, 2) if pf_den > 0 else float('inf')

    outcomes = sub['outcome'].value_counts().to_dict()
    t1  = outcomes.get('T1', 0)
    t2  = outcomes.get('T2', 0)
    t3  = outcomes.get('T3', 0)
    sl  = outcomes.get('SL', 0) + outcomes.get('SL_AFTER_T1', 0)
    to  = outcomes.get('TIMEOUT', 0)
    avg_hold = sub['hold_mins'].mean()

    lines = [
        f"  {symbol}:",
        f"    Trades    : {total}",
        f"    Win Rate  : {wr:.1f}%",
        f"    Total R   : {total_r:+.2f}",
        f"    Profit F  : {pf}",
        f"    Avg R     : {avg_r:+.3f}",
        f"    Avg Hold  : {avg_hold:.0f} min",
        f"    Outcomes  : T1={t1}  T2={t2}  T3={t3}  SL={sl}  TIMEOUT={to}",
    ]
    return '\n'.join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='ALL', help='XAUUSD / XAGUSD / USOIL / ALL')
    parser.add_argument('--from',   dest='from_date', default='2024-05-27')
    parser.add_argument('--to',     dest='to_date',   default=None)
    args = parser.parse_args()

    since = datetime.strptime(args.from_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    until = (datetime.now(timezone.utc) if args.to_date is None
             else datetime.strptime(args.to_date, '%Y-%m-%d').replace(tzinfo=timezone.utc))

    symbols = list(SYMBOL_CFG.keys()) if args.symbol == 'ALL' else [args.symbol.upper()]

    print("=" * 62)
    print("  CB6 Quantum — Forex MT5 Backtest")
    print("=" * 62)
    print(f"  Symbols  : {', '.join(symbols)}")
    print(f"  Period   : {since.date()} to {until.date()}")
    print(f"  TF       : 15m  |  Session: London 07-12 + NY 16-20 UTC")
    print(f"  Min RR   : {MIN_RR}  |  Min Score: {MIN_SCORE}")
    print("=" * 62)

    # Init MT5
    ok = mt5.initialize()
    if not ok:
        print("MT5 init failed:", mt5.last_error()); sys.exit(1)
    print(f"  MT5: {mt5.account_info().company}  account {mt5.account_info().login}\n")

    all_trades = []

    for symbol in symbols:
        cfg    = SYMBOL_CFG[symbol]
        mt5_s  = cfg['mt5_sym']
        print(f"[{symbol}] Loading {mt5_s} 15m data {since.date()} to {until.date()}...", end=' ', flush=True)
        df = _load_mt5(mt5_s, since, until)
        if df.empty:
            print("NO DATA"); continue
        print(f"{len(df):,} bars")

        print(f"[{symbol}] Running backtest...", end=' ', flush=True)
        trades = run_symbol(symbol, df, cfg)
        print(f"{len(trades)} trades found")
        all_trades.extend(trades)

    mt5.shutdown()

    if not all_trades:
        print("\nNo trades generated."); return

    df_all = pd.DataFrame(all_trades)
    ts_str = datetime.now().strftime('%Y%m%d_%H%M')

    # Save CSV
    csv_path = RESULTS_DIR / f'bt_forex_mt5_{ts_str}.csv'
    df_all.to_csv(csv_path, index=False)

    # Save ML training copy
    ml_path  = ML_DIR / 'bt_forex_mt5.csv'
    df_all.to_csv(ml_path, index=False)

    # Print summary
    print()
    print("=" * 62)
    print("  BACKTEST RESULTS")
    print("=" * 62)
    for sym in symbols:
        print(_print_summary(df_all, sym))
        print()

    if len(symbols) > 1:
        print(_print_summary(df_all, 'ALL').replace('ALL:', 'COMBINED:'))

    print()
    print(f"  CSV saved : {csv_path}")
    print(f"  ML copy   : {ml_path}")
    print("=" * 62)


if __name__ == '__main__':
    main()
