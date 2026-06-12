#!/usr/bin/env python3
"""
backtest/week_backtest_june8_11.py
CB6 Quantum — Week validation backtest June 8-11 2026.

Uses CURRENT live scanner logic:
  - Real H4 bias (EMA3 vs EMA8 on historical H4 bars) — NOT forced RANGING
  - MTF cascade fallback when 3m/15m scan returns None
  - All scanner changes from this week applied

Breakdown output:
  - NSE vs Forex
  - Long vs Short
  - CHoCH vs BOS
  - Per-day (Jun 8/9/10/11)
  - Avg RR, WR, best category

Usage (from project root):
  python backtest/week_backtest_june8_11.py
  python backtest/week_backtest_june8_11.py --no-cascade   (3m scanner only)
  python backtest/week_backtest_june8_11.py --no-h4        (force H4=RANGING, baseline)
"""

from __future__ import annotations
import argparse, os, sys, json, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault('CB6_MEMORY_V1_ENABLED',    'false')
os.environ.setdefault('CB6_REGIME_V1_ENABLED',    'false')
os.environ.setdefault('CB6_SETUP_DNA_V1_ENABLED', 'false')

from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / '.env').items():
    if k not in os.environ:
        os.environ[k] = v

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger('week_bt')
logger.setLevel(logging.INFO)

import pandas as pd

# ── CLI args ──────────────────────────────────────────────────────────────────
ap = argparse.ArgumentParser()
ap.add_argument('--no-cascade', action='store_true', help='Disable MTF cascade fallback')
ap.add_argument('--no-h4',      action='store_true', help='Force H4=RANGING (baseline comparison)')
ARGS = ap.parse_args()

USE_CASCADE = not ARGS.no_cascade
USE_H4      = not ARGS.no_h4

# ── Date window ───────────────────────────────────────────────────────────────
START_DATE = datetime(2026, 6,  8,  0,  0, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 6, 11, 23, 59, tzinfo=timezone.utc)
DAYS_BACK  = 10  # extra history for scanner context

# ── Imports ───────────────────────────────────────────────────────────────────
from scanner.silver_bullet import scan_silver_bullet
from forex_engine.scanner.signal_scanner import scan_setup

# ─────────────────────────────────────────────────────────────────────────────
#   HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _h4_bias_from_df(df_h4: pd.DataFrame, as_of_ts) -> str:
    """Compute H4 EMA(3) vs EMA(8) bias at a given timestamp (historical slice)."""
    try:
        slice_df = df_h4[df_h4.index <= as_of_ts].tail(20)
        if len(slice_df) < 8:
            return 'RANGING'
        c    = slice_df['close']
        fast = c.ewm(span=3, adjust=False).mean().iloc[-1]
        slow = c.ewm(span=8, adjust=False).mean().iloc[-1]
        band = 0.0003
        if fast > slow * (1 + band):
            return 'BULLISH'
        if fast < slow * (1 - band):
            return 'BEARISH'
        return 'RANGING'
    except Exception:
        return 'RANGING'


def simulate_trade(df: pd.DataFrame, setup: dict, entry_idx: int) -> dict:
    """Walk-forward sim. Partial exit: 1/3 at T1, 1/3 at T2, 1/3 at T3. BE after T1."""
    sig       = setup['entry_signal']
    direction = setup['direction']
    entry     = sig['entry']
    sl        = sig['stop_loss']
    t1        = sig.get('target1', sig.get('tp1', entry))
    t2        = sig.get('target2', sig.get('tp2', entry))
    t3        = sig.get('target3', sig.get('tp3', entry))
    risk_pts  = abs(entry - sl)
    if risk_pts < 1e-9:
        return {'result': 'ZERO_RISK', 'targets_hit': [], 'total_r': 0.0, 'exit_price': entry, 'risk_pts': 0}

    current_sl  = sl
    targets_hit = []
    partial_r   = 0.0
    result      = 'TIMEOUT'
    exit_price  = float(df['close'].iloc[-1]) if len(df) > 0 else entry

    for i in range(entry_idx + 1, min(entry_idx + 200, len(df))):
        h  = float(df['high'].iloc[i])
        lo = float(df['low'].iloc[i])

        if direction == 'BULLISH':
            if lo <= current_sl:
                result = 'SL_HIT'; exit_price = current_sl; break
            if 'T1' not in targets_hit and h >= t1:
                targets_hit.append('T1')
                partial_r   += (t1 - entry) / risk_pts / 3
                current_sl   = entry  # BE
            if 'T2' not in targets_hit and h >= t2:
                targets_hit.append('T2')
                partial_r += (t2 - entry) / risk_pts / 3
            if 'T3' not in targets_hit and h >= t3:
                targets_hit.append('T3')
                partial_r += (t3 - entry) / risk_pts / 3
                result = 'T3_HIT'; exit_price = t3; break
        else:
            if h >= current_sl:
                result = 'SL_HIT'; exit_price = current_sl; break
            if 'T1' not in targets_hit and lo <= t1:
                targets_hit.append('T1')
                partial_r   += (entry - t1) / risk_pts / 3
                current_sl   = entry
            if 'T2' not in targets_hit and lo <= t2:
                targets_hit.append('T2')
                partial_r += (entry - t2) / risk_pts / 3
            if 'T3' not in targets_hit and lo <= t3:
                targets_hit.append('T3')
                partial_r += (entry - t3) / risk_pts / 3
                result = 'T3_HIT'; exit_price = t3; break

    if result == 'TIMEOUT' and targets_hit:
        result = f"PARTIAL({','.join(targets_hit)})"

    remaining = 1.0 - len(targets_hit) / 3
    if result == 'SL_HIT':
        total_r = partial_r - remaining
    elif 'T3' in result:
        total_r = partial_r
    else:
        final_dist = (exit_price - entry) if direction == 'BULLISH' else (entry - exit_price)
        total_r = partial_r + remaining * final_dist / risk_pts

    return {
        'result'     : result,
        'targets_hit': targets_hit,
        'total_r'    : round(total_r, 2),
        'exit_price' : round(exit_price, 5),
        'risk_pts'   : round(risk_pts, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
#   MT5 DATA FETCH
# ─────────────────────────────────────────────────────────────────────────────
FOREX_SYMBOLS = ['XAUUSD', 'XAGUSD', 'USOIL']

TF_MAP_STR = {
    '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
    '1h': 16385, '4h': 16388, '1d': 16408,
}

def _get_mt5_df(symbol: str, tf_str: str = '15m', bars: int = 3000) -> tuple:
    try:
        import MetaTrader5 as mt5
        TF_MAP = {
            '1m' : mt5.TIMEFRAME_M1,  '3m' : mt5.TIMEFRAME_M3,
            '5m' : mt5.TIMEFRAME_M5,  '15m': mt5.TIMEFRAME_M15,
            '30m': mt5.TIMEFRAME_M30, '1h' : mt5.TIMEFRAME_H1,
            '4h' : mt5.TIMEFRAME_H4,  '1d' : mt5.TIMEFRAME_D1,
        }
        tf = TF_MAP.get(tf_str, mt5.TIMEFRAME_M15)
        for s in [symbol, symbol + '.x', symbol.replace('USOIL', 'WTI') + '.x']:
            mt5.symbol_select(s, True)
            rates = mt5.copy_rates_from_pos(s, tf, 0, bars)
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df['timestamp'] = pd.to_datetime(df['time'], unit='s', utc=True)
                df = df.set_index('timestamp').sort_index()
                logger.info(f"MT5 {s} [{tf_str}]: {len(df)} bars")
                return df, s
        return None, symbol
    except Exception as e:
        logger.warning(f"MT5 fetch {symbol}: {e}")
        return None, symbol


def _init_mt5(account: str = '5k') -> bool:
    try:
        import MetaTrader5 as mt5
        configs = {
            '5k': (os.getenv('MT5_TERMINAL_GFT',          r'C:\CB6_MT5\MT5_GFT_5K\terminal64.exe'),
                   int(os.getenv('GFT_2STEP_LOGIN',   '0')),
                   os.getenv('GFT_2STEP_PASSWORD',     ''),
                   os.getenv('GFT_2STEP_SERVER',       'GoatFunded-Server3')),
            '1k': (os.getenv('GFT_1K_MT5_TERMINAL_PATH', r'C:\CB6_MT5\MT5_GFT_1K\terminal64.exe'),
                   int(os.getenv('GFT_1K_MT5_LOGIN',  '0')),
                   os.getenv('GFT_1K_MT5_PASSWORD',   ''),
                   os.getenv('GFT_1K_MT5_SERVER',     'GoatFunded-Server')),
        }
        path, login, pwd, server = configs.get(account, configs['5k'])
        ok = mt5.initialize(path=path, login=login, password=pwd, server=server, timeout=15000)
        if not ok:
            logger.warning(f"MT5 init failed: {mt5.last_error()}")
        return ok
    except Exception as e:
        logger.warning(f"MT5 init error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#   FOREX BACKTEST
# ─────────────────────────────────────────────────────────────────────────────
def run_forex_backtest(df_15m: pd.DataFrame, df_h4: pd.DataFrame,
                       symbol: str) -> list[dict]:
    trades = []
    df_window = df_15m[
        (df_15m.index >= START_DATE) & (df_15m.index <= END_DATE)
    ]
    if len(df_window) < 5:
        return trades

    seen_entries: set = set()

    for pos in range(40, len(df_window)):
        signal_ts = df_window.index[pos]
        utc_hour  = signal_ts.hour

        # Kill zone gate
        in_kz = any(s <= utc_hour < e for s, e in [(7, 12), (16, 20)])
        if not in_kz:
            continue

        # Build context from full df (need look-back for DOL/MSS)
        full_idx  = df_15m.index.get_loc(signal_ts)
        ctx_start = max(0, full_idx - 100)
        df_ctx    = df_15m.iloc[ctx_start : full_idx + 1].copy()
        if len(df_ctx) < 40:
            continue

        # H4 bias at this exact moment
        h4_bias = 'RANGING' if not USE_H4 else _h4_bias_from_df(df_h4, signal_ts)

        # ── Primary scan (15m = signal_scanner default) ──
        setup = scan_setup(df_ctx, symbol, min_rr=2.0, h4_bias=h4_bias)

        # ── MTF cascade fallback ──
        cascade_used = False
        if not setup and USE_CASCADE:
            try:
                from forex_engine.scanner.mtf_scanner import _run_cascade
                df_1h_ctx  = df_15m.iloc[max(0, full_idx - 160) : full_idx + 1].resample('1h').agg(
                    {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'tick_volume': 'sum'}
                ).dropna()
                df_5m_ctx  = df_15m.iloc[max(0, full_idx - 80)  : full_idx + 1]
                setup = _run_cascade(symbol, df_1h_ctx, df_ctx, df_5m_ctx,
                                     h4_bias=h4_bias, min_rr=2.0, source='MTF-BT')
                if setup:
                    cascade_used = True
            except Exception as exc:
                logger.debug(f"Cascade error {symbol}: {exc}")

        if not setup:
            continue

        direction = setup['direction']
        entry     = setup['entry_signal']['entry']
        _raw_score = setup.get('confluence', setup.get('score', 0))
        score     = _raw_score.get('score', 0) if isinstance(_raw_score, dict) else int(_raw_score or 0)
        mss_type  = setup.get('mss_type', '?')

        dedup = (signal_ts.date(), symbol, direction, round(entry, 1))
        if dedup in seen_entries:
            continue
        seen_entries.add(dedup)

        sim_df  = df_15m.iloc[full_idx:]
        outcome = simulate_trade(sim_df, setup, 0)

        trades.append({
            'market'      : 'FOREX',
            'symbol'      : symbol,
            'date'        : signal_ts.strftime('%Y-%m-%d'),
            'ts'          : signal_ts.strftime('%Y-%m-%d %H:%M UTC'),
            'direction'   : direction,
            'score'       : score,
            'mss_type'    : mss_type,
            'h4_bias'     : h4_bias,
            'cascade'     : cascade_used,
            'entry'       : round(entry, 5),
            'sl'          : round(setup['entry_signal']['stop_loss'], 5),
            'result'      : outcome['result'],
            'targets'     : ','.join(outcome['targets_hit']) or '-',
            'total_r'     : outcome['total_r'],
            'risk_pts'    : outcome['risk_pts'],
        })
        _tag = '⚡MTF' if cascade_used else '   '
        logger.info(
            f"  FOREX {_tag} {symbol} {signal_ts.strftime('%m-%d %H:%M')} "
            f"{direction} {mss_type} H4={h4_bias} score={score} "
            f"→ {outcome['result']} {outcome['total_r']:+.2f}R"
        )

    return trades


# ─────────────────────────────────────────────────────────────────────────────
#   NSE BACKTEST
# ─────────────────────────────────────────────────────────────────────────────
import pytz
IST = pytz.timezone('Asia/Kolkata')
SB_WINDOWS_IST = [(10, 0, 11, 0), (13, 0, 14, 0), (15, 0, 15, 30)]

def _in_sb_window(ts) -> bool:
    try:
        t    = ts.astimezone(IST) if ts.tzinfo else IST.localize(ts)
        mins = t.hour * 60 + t.minute
        return any(sh * 60 + sm <= mins < eh * 60 + em
                   for sh, sm, eh, em in SB_WINDOWS_IST)
    except Exception:
        return False

NSE_SYMBOLS = {
    'NSE:NIFTY26JUNFUT'    : 'NIFTY',
    'NSE:BANKNIFTY26JUNFUT': 'BANKNIFTY',
    'NSE:FINNIFTY26JUNFUT' : 'FINNIFTY',
    'NSE:MIDCPNIFTY26JUNFUT': 'MIDCPNIFTY',
}


def run_nse_backtest(df_full: pd.DataFrame, symbol: str, label: str,
                     fyers_inst) -> list[dict]:
    trades = []

    # Normalise index to IST DatetimeIndex
    if 'timestamp' in df_full.columns and not isinstance(df_full.index, pd.DatetimeIndex):
        df_full = df_full.copy()
        df_full['timestamp'] = pd.to_datetime(df_full['timestamp'])
        df_full = df_full.set_index('timestamp').sort_index()
    try:
        if df_full.index.tz is None:
            df_full.index = df_full.index.tz_localize('Asia/Kolkata')
        else:
            df_full.index = df_full.index.tz_convert('Asia/Kolkata')
    except Exception:
        pass

    start_ist = datetime(2026, 6,  8,  9, 15, tzinfo=IST)
    end_ist   = datetime(2026, 6, 11, 15, 30, tzinfo=IST)
    df_window = df_full[(df_full.index >= start_ist) & (df_full.index <= end_ist)]

    if len(df_window) < 5:
        return trades

    seen_entries: set = set()
    # Pass fyers=fyers_inst for real H4, or None if --no-h4
    _fyers_arg = None if not USE_H4 else fyers_inst

    for pos in range(30, len(df_window)):
        signal_ts = df_window.index[pos]
        if not _in_sb_window(signal_ts):
            continue

        full_idx  = df_full.index.get_loc(signal_ts)
        ctx_start = max(0, full_idx - 100)
        df_ctx    = df_full.iloc[ctx_start : full_idx + 1].copy()
        if len(df_ctx) < 30:
            continue

        # Primary scan
        setup = scan_silver_bullet(df_ctx, symbol, tf='3', fyers=_fyers_arg)

        # MTF cascade fallback (NSE)
        cascade_used = False
        if not setup and USE_CASCADE:
            try:
                from scanner.silver_bullet import scan_silver_bullet_mtf as _mtf
                setup = _mtf(_fyers_arg, symbol, h4_bias=None, min_rr=2.5)
                if setup:
                    cascade_used = True
            except Exception as exc:
                logger.debug(f"NSE cascade error {label}: {exc}")

        if not setup:
            continue

        direction  = setup['direction']
        entry      = setup['entry_signal']['entry']
        _raw_score = setup.get('confluence', setup.get('score', 0))
        score      = _raw_score.get('score', 0) if isinstance(_raw_score, dict) else int(_raw_score or 0)
        mss_type   = setup.get('mss_type', '?')

        dedup = (signal_ts.date(), symbol, direction, round(entry))
        if dedup in seen_entries:
            continue
        seen_entries.add(dedup)

        sim_df  = df_full.iloc[full_idx:]
        outcome = simulate_trade(sim_df, setup, 0)

        trades.append({
            'market'  : 'NSE',
            'symbol'  : label,
            'date'    : signal_ts.strftime('%Y-%m-%d'),
            'ts'      : signal_ts.strftime('%Y-%m-%d %H:%M IST'),
            'direction': direction,
            'score'   : score,
            'mss_type': mss_type,
            'h4_bias' : 'live',
            'cascade' : cascade_used,
            'entry'   : round(entry, 1),
            'sl'      : round(setup['entry_signal']['stop_loss'], 1),
            'result'  : outcome['result'],
            'targets' : ','.join(outcome['targets_hit']) or '-',
            'total_r' : outcome['total_r'],
            'risk_pts': outcome['risk_pts'],
        })
        _tag = '⚡MTF' if cascade_used else '   '
        logger.info(
            f"  NSE  {_tag} {label} {signal_ts.strftime('%m-%d %H:%M')} "
            f"{direction} {mss_type} score={score} "
            f"→ {outcome['result']} {outcome['total_r']:+.2f}R"
        )

    return trades


# ─────────────────────────────────────────────────────────────────────────────
#   ANALYSIS HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _stats(trades: list[dict]) -> dict:
    if not trades:
        return {'n': 0, 'wr': 0.0, 'total_r': 0.0, 'avg_r': 0.0, 'best_r': 0.0, 'worst_r': 0.0}
    r_vals  = [t['total_r'] for t in trades]
    wins    = sum(1 for r in r_vals if r > 0)
    return {
        'n'      : len(trades),
        'wr'     : wins / len(trades) * 100,
        'total_r': round(sum(r_vals), 2),
        'avg_r'  : round(sum(r_vals) / len(trades), 2),
        'best_r' : round(max(r_vals), 2),
        'worst_r': round(min(r_vals), 2),
    }

def _print_stats(label: str, trades: list[dict]):
    s = _stats(trades)
    if s['n'] == 0:
        print(f"    {label:<22}: —  no setups")
        return
    print(f"    {label:<22}: n={s['n']:>2}  WR={s['wr']:>5.1f}%  "
          f"ΣR={s['total_r']:>+6.2f}  avgR={s['avg_r']:>+5.2f}  "
          f"best={s['best_r']:>+5.2f}  worst={s['worst_r']:>+5.2f}")

def _section(title: str):
    print(f"\n  {'─'*54}")
    print(f"  {title}")
    print(f"  {'─'*54}")


def print_full_report(all_trades: list[dict]):
    nse_t   = [t for t in all_trades if t['market'] == 'NSE']
    forex_t = [t for t in all_trades if t['market'] == 'FOREX']

    cascade_t = [t for t in all_trades if t.get('cascade')]
    scanner_t = [t for t in all_trades if not t.get('cascade')]

    choch_t = [t for t in all_trades if 'CHOCH' in t.get('mss_type','').upper() or 'CHOCH' in t.get('mss_type','')]
    bos_t   = [t for t in all_trades if 'BOS'   in t.get('mss_type','').upper()]
    long_t  = [t for t in all_trades if t['direction'] == 'BULLISH']
    short_t = [t for t in all_trades if t['direction'] == 'BEARISH']

    print(f"\n{'═'*56}")
    h4_label = 'H4=LIVE' if USE_H4 else 'H4=RANGING(off)'
    cas_label = '+MTF-CASCADE' if USE_CASCADE else 'no-cascade'
    print(f"  CB6 Quantum — Week Backtest June 8-11 2026")
    print(f"  Scanner: {h4_label}  {cas_label}")
    print(f"{'═'*56}")

    # ── Per-symbol ────────────────────────────────────────────────────────────
    _section("BY SYMBOL")
    all_syms = sorted(set(t['symbol'] for t in all_trades))
    for sym in all_syms:
        sym_t = [t for t in all_trades if t['symbol'] == sym]
        mkt   = sym_t[0]['market'] if sym_t else '?'
        _print_stats(f"[{mkt}] {sym}", sym_t)

    # ── Market split ──────────────────────────────────────────────────────────
    _section("MARKET SPLIT")
    _print_stats("NSE (all indices)",   nse_t)
    _print_stats("FOREX (all symbols)", forex_t)
    _print_stats("TOTAL",               all_trades)

    # ── Direction split ───────────────────────────────────────────────────────
    _section("DIRECTION SPLIT")
    _print_stats("LONG  (BULLISH)", long_t)
    _print_stats("SHORT (BEARISH)", short_t)

    # ── MSS type split ────────────────────────────────────────────────────────
    _section("MSS TYPE SPLIT")
    _print_stats("CHoCH setups", choch_t)
    _print_stats("BOS   setups", bos_t)

    # ── Scanner vs MTF cascade ────────────────────────────────────────────────
    _section("SOURCE SPLIT")
    _print_stats("Primary scanner (3m/15m)", scanner_t)
    _print_stats("MTF cascade fallback",     cascade_t)

    # ── Per-day ───────────────────────────────────────────────────────────────
    _section("PER DAY")
    for day in ['2026-06-08', '2026-06-09', '2026-06-10', '2026-06-11']:
        day_t = [t for t in all_trades if t['date'] == day]
        _print_stats(day, day_t)

    # ── Result distribution ───────────────────────────────────────────────────
    _section("RESULT DISTRIBUTION")
    from collections import Counter
    rc = Counter(t['result'] for t in all_trades)
    for res, cnt in sorted(rc.items(), key=lambda x: -x[1]):
        r_vals = [t['total_r'] for t in all_trades if t['result'] == res]
        avg    = sum(r_vals) / len(r_vals) if r_vals else 0
        print(f"    {res:<28} : {cnt:>3}x  avgR={avg:>+5.2f}")

    # ── Best trades ───────────────────────────────────────────────────────────
    _section("TOP 5 TRADES BY R")
    top5 = sorted(all_trades, key=lambda t: -t['total_r'])[:5]
    for t in top5:
        cas = '⚡' if t.get('cascade') else ' '
        print(f"    {cas}{t['ts']:<26} [{t['market']}] {t['symbol']:<12} "
              f"{t['direction'][:4]} {t['mss_type']:<6} → {t['total_r']:>+5.2f}R")

    _section("BOTTOM 5 TRADES BY R")
    bot5 = sorted(all_trades, key=lambda t: t['total_r'])[:5]
    for t in bot5:
        cas = '⚡' if t.get('cascade') else ' '
        print(f"    {cas}{t['ts']:<26} [{t['market']}] {t['symbol']:<12} "
              f"{t['direction'][:4]} {t['mss_type']:<6} → {t['total_r']:>+5.2f}R")

    print(f"\n{'═'*56}\n")


# ─────────────────────────────────────────────────────────────────────────────
#   MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    all_trades = []
    mt5_ok     = False
    fyers_inst = None

    h4_label  = 'LIVE (EMA3/EMA8 on H4 bars)' if USE_H4 else 'DISABLED (forced RANGING)'
    cas_label = 'ENABLED' if USE_CASCADE else 'DISABLED'

    print(f"\n{'═'*56}")
    print(f"  CB6 Quantum — Week Backtest  |  June 8-11 2026")
    print(f"  H4 bias : {h4_label}")
    print(f"  MTF cascade fallback: {cas_label}")
    print(f"{'═'*56}")

    # ── Init MT5 ──────────────────────────────────────────────────────────────
    print("\n[1/2] Connecting MT5 for Forex data...")
    try:
        import MetaTrader5 as _mt5
        mt5_ok = _init_mt5('5k')
        if mt5_ok:
            info = _mt5.account_info()
            print(f"      MT5 OK — {info.company if info else 'connected'}")
        else:
            print("      MT5 not available — Forex skipped")
    except Exception as e:
        print(f"      MT5 error: {e} — Forex skipped")

    # ── Init Fyers ────────────────────────────────────────────────────────────
    print("\n[2/2] Connecting Fyers for NSE data...")
    try:
        from fyers_apiv3 import fyersModel
        from dotenv import dotenv_values as _dv
        _env2      = _dv(ROOT / '.env')
        _client_id = _env2.get('CLIENT_ID', '')
        _token_str = _env2.get('ACCESS_TOKEN', '')
        if ':' in _token_str:
            _token_str = _token_str.split(':', 1)[1]
        if _token_str and _client_id:
            (ROOT / 'logs').mkdir(exist_ok=True)
            fyers_inst = fyersModel.FyersModel(
                client_id=_client_id, token=_token_str,
                is_async=False, log_path=str(ROOT / 'logs' / '')
            )
            test = fyers_inst.get_profile()
            if test and test.get('s') == 'ok':
                print(f"      Fyers OK — {test.get('data', {}).get('name', 'OK')}")
            else:
                print(f"      Fyers token stale (code={test.get('code')}), H4 will default RANGING")
        else:
            print("      Fyers creds missing — NSE skipped")
            fyers_inst = None
    except Exception as e:
        print(f"      Fyers error: {e} — NSE skipped")
        fyers_inst = None

    # ── FOREX ─────────────────────────────────────────────────────────────────
    if mt5_ok:
        print(f"\n{'─'*56}")
        print("  FOREX  |  XAUUSD / XAGUSD / USOIL")
        print(f"{'─'*56}")
        for sym in FOREX_SYMBOLS:
            print(f"\n  Scanning {sym} ...")
            df_15m, actual_sym = _get_mt5_df(sym, '15m', 3000)
            df_h4,  _          = _get_mt5_df(sym, '4h',   500)
            if df_15m is None:
                print(f"    {sym}: no 15m data"); continue
            if df_h4 is None:
                print(f"    {sym}: no H4 data — H4 bias will default RANGING")
                df_h4 = pd.DataFrame(columns=['close'])

            trades = run_forex_backtest(df_15m, df_h4, actual_sym)
            all_trades.extend(trades)
            _print_symbol_trades(actual_sym, trades)

    # ── NSE ───────────────────────────────────────────────────────────────────
    if fyers_inst is not None:
        print(f"\n{'─'*56}")
        print("  NSE  |  NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY")
        print(f"{'─'*56}")
        for sym, label in NSE_SYMBOLS.items():
            print(f"\n  Scanning {label} ...")
            try:
                from scanner.data_fetcher import get_historical_data
                df = get_historical_data(fyers_inst, sym, '3', days=DAYS_BACK + 4)
            except Exception as e:
                print(f"    {label}: fetch error — {e}"); continue
            if df is None:
                print(f"    {label}: no data"); continue

            trades = run_nse_backtest(df, sym, label, fyers_inst)
            all_trades.extend(trades)
            _print_symbol_trades(label, trades)

    # ── Full report ───────────────────────────────────────────────────────────
    print_full_report(all_trades)

    # ── Save ──────────────────────────────────────────────────────────────────
    out_json = ROOT / 'reports' / 'week_backtest_june8_11.json'
    out_json.parent.mkdir(exist_ok=True)
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'window'      : 'June 8-11 2026',
            'h4_enabled'  : USE_H4,
            'cascade'     : USE_CASCADE,
            'total_trades': len(all_trades),
            'trades'      : all_trades,
        }, f, indent=2)
    print(f"  Saved → {out_json}\n")


def _print_symbol_trades(symbol: str, trades: list[dict]):
    if not trades:
        print(f"    {symbol}: 0 setups in window"); return
    wins    = sum(1 for t in trades if t['total_r'] > 0)
    total_r = sum(t['total_r'] for t in trades)
    wr      = wins / len(trades) * 100
    print(f"    {symbol}: {len(trades)} setups | WR {wr:.0f}% | ΣR {total_r:+.1f}R")
    for t in trades:
        ico = '✅' if t['total_r'] > 0 else '❌' if t['total_r'] < 0 else '➖'
        cas = '⚡' if t.get('cascade') else ' '
        print(f"      {ico}{cas} {t['ts']:<26} {t['direction'][:4]} "
              f"{t['mss_type']:<6} H4={t['h4_bias']:<8} "
              f"score={t['score']:<3} → {t['result']:<20} {t['total_r']:>+5.2f}R")


if __name__ == '__main__':
    main()
