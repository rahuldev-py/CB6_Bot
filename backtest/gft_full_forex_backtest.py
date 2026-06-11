#!/usr/bin/env python3
# backtest/gft_full_forex_backtest.py
#
# CB6 Quantum — GFT Full Forex Backtest
#
# Connects to a live GFT MT5 terminal, auto-discovers ALL available
# forex/metal/energy symbols, fetches maximum history (up to 99,000 bars),
# and runs a walk-forward ICT Silver Bullet backtest on every symbol.
#
# Filters applied (identical to live bot):
#   Kill zones : London 07-12 UTC | NY 16-20 UTC
#   H4 bias    : MANDATORY — blocks counter-trend entries
#   Sweep      : confidence ≥ 45, candles_ago ≤ 15, same direction as MSS
#   FVG gate   : price must be inside the FVG at signal candle
#   Score gate : confluence ≥ MIN_SCORE (raises +1 if H4 ranging, +1 for CHOCH)
#
# Usage (from project root):
#   python backtest/gft_full_forex_backtest.py
#   python backtest/gft_full_forex_backtest.py --account 10k
#   python backtest/gft_full_forex_backtest.py --symbols EURUSD.x,GBPUSD.x,XAUUSD.x
#   python backtest/gft_full_forex_backtest.py --min-bars 3000
#
# Output:
#   reports/gft_forex_backtest_YYYYMMDD_HHMMSS.json  — full results + per-trade log
#   reports/gft_forex_backtest_YYYYMMDD_HHMMSS.csv   — summary table (sorted by PnL)
#   Console  — ranked symbol table + tier summary

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Disable shadow ML logging before any CB6 imports ─────────────────────────
os.environ.setdefault('CB6_MEMORY_V1_ENABLED',    'false')
os.environ.setdefault('CB6_REGIME_V1_ENABLED',    'false')
os.environ.setdefault('CB6_SETUP_DNA_V1_ENABLED', 'false')

from dotenv import dotenv_values
_env = dotenv_values(ROOT / '.env')
for k, v in _env.items():
    if k not in os.environ:
        os.environ[k] = v

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 not installed — pip install MetaTrader5")
    sys.exit(1)

import pandas as pd

# ── Account configurations (credentials from .env) ───────────────────────────
ACCOUNT_CONFIGS: dict[str, dict] = {
    '5k': {
        'terminal'   : os.getenv('MT5_TERMINAL_GFT',
                                 r'C:\CB6_MT5\MT5_GFT_5K\terminal64.exe').replace('/', '\\'),
        'login'      : int(os.getenv('GFT_2STEP_LOGIN', '0')),
        'password'   : os.getenv('GFT_2STEP_PASSWORD', ''),
        'server'     : os.getenv('GFT_2STEP_SERVER', 'GoatFunded-Server3'),
        'size'       : 5000.0,
        'risk_pct'   : 0.5,
        'daily_limit': 200.0,
        'label'      : 'GFT $5K 2-Step',
    },
    '1k': {
        'terminal'   : os.getenv('GFT_1K_MT5_TERMINAL_PATH',
                                 r'C:\CB6_MT5\MT5_GFT_1K\terminal64.exe').replace('/', '\\'),
        'login'      : int(os.getenv('GFT_1K_MT5_LOGIN', '0')),
        'password'   : os.getenv('GFT_1K_MT5_PASSWORD', ''),
        'server'     : os.getenv('GFT_1K_MT5_SERVER', 'GoatFunded-Server'),
        'size'       : 1000.0,
        'risk_pct'   : 0.25,
        'daily_limit': 30.0,
        'label'      : 'GFT $1K Instant',
    },
    '10k': {
        'terminal'   : os.getenv('GFT_10K_MT5_TERMINAL_PATH',
                                 r'C:\CB6_MT5\MT5_GFT_10K\terminal64.exe').replace('/', '\\'),
        'login'      : int(os.getenv('GFT_10K_MT5_LOGIN', '0')),
        'password'   : os.getenv('GFT_10K_MT5_PASSWORD', ''),
        'server'     : os.getenv('GFT_10K_MT5_SERVER', 'GoatFunded-Server3'),
        'size'       : 10000.0,
        'risk_pct'   : 0.5,
        'daily_limit': 500.0,
        'label'      : 'GFT $10K Instant',
    },
}

# ── Strategy constants ────────────────────────────────────────────────────────
KILL_ZONES      = [(7, 12), (16, 20)]   # London + NY UTC
MAX_BARS        = 20_000                # bars to fetch per symbol (≈ 5 months 15m)
HISTORY_FROM    = datetime(2018, 1, 1, tzinfo=timezone.utc)
WINDOW          = 80                    # scanner lookback; internals cap at 80 anyway
COOLDOWN        = 25                    # post-trade cooldown (candles)
MIN_SCORE       = 11                    # confluence minimum
MIN_BARS        = 1_500                 # skip symbol if fewer bars after fetch
MAX_SCAN_BARS   = 8_000                # only walk-forward the last N bars per symbol (~4 months)
# Vectorized pre-filters (computed once via pandas rolling before the loop).
# Both must be True to call the expensive scan_setup() — eliminates ~65% of
# kill-zone candle scans with near-zero accuracy loss.
PREFILTER_BARS      = 30               # rolling window for range check
PREFILTER_ZONE_PCT  = 0.40             # top/bottom 40% of 30-bar range
DISPLACEMENT_RATIO  = 0.80             # candle body must be ≥ 80% of 20-bar avg body

# GFT symbol → canonical name (for INSTRUMENTS config lookup in the scanner)
CANONICAL_MAP = {
    'XAUUSD.x': 'XAUUSD', 'XAGUSD.x': 'XAGUSD',
    'WTI.x':    'USOIL',  'BRENT.x':  'USOIL',
    'EURUSD.x': 'EURUSD', 'AUDUSD.x': 'AUDUSD',
    'GBPUSD.x': 'EURUSD', 'USDJPY.x': 'EURUSD',   # use EURUSD defaults for unknowns
    'USDCHF.x': 'EURUSD', 'USDCAD.x': 'EURUSD',
    'NZDUSD.x': 'EURUSD',
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _in_kill_zone(utc_hour: int) -> bool:
    return any(s <= utc_hour < e for s, e in KILL_ZONES)


def _connect(cfg: dict) -> bool:
    terminal = cfg['terminal']
    if os.path.isfile(terminal):
        ok = mt5.initialize(
            path     = terminal,
            login    = cfg['login'],
            password = cfg['password'],
            server   = cfg['server'],
        )
    else:
        print(f"  Terminal not found at {terminal!r} — using system-default MT5")
        ok = mt5.initialize(
            login    = cfg['login'],
            password = cfg['password'],
            server   = cfg['server'],
        )
    if not ok:
        print(f"  MT5 init failed: {mt5.last_error()}")
        return False
    info = mt5.account_info()
    if not info:
        print("  MT5 connected but account_info() returned None")
        return False
    print(f"  Connected  login={info.login}  balance=${info.balance:,.2f}  "
          f"server={info.server}")
    return True


def _discover_symbols() -> list:
    """Return all forex/metal/energy symbols available in the terminal."""
    syms = mt5.symbols_get()
    if not syms:
        return []

    result = []
    for s in syms:
        path = (s.path or '').upper()
        name = s.name

        # Hard exclude
        if any(x in path for x in [
            'CRYPTO', 'BITCOIN', 'STOCK', 'SHARE', 'BOND',
            'INDEX', 'INDIC', 'EQUITY', 'RATE',
        ]):
            continue

        # Include forex + metals + energy
        include = s.trade_calc_mode in (0, 1) and (
            'FOREX'    in path or
            'METAL'    in path or
            'ENERG'    in path or
            'COMMODI'  in path or
            'CURRENC'  in path or
            # Fallback: GFT .x suffix pairs (e.g. EURUSD.x, XAUUSD.x)
            (name.endswith('.x') and len(name) <= 10)
        )
        if include and s.visible:
            result.append(s)

    return result


def _fetch(mt5_sym: str, tf_const: int, max_bars: int) -> pd.DataFrame:
    """
    Fetch historical OHLCV from MT5 as a timezone-aware DataFrame.

    MT5 only downloads broker history when a chart is open OR after a small
    seed request triggers a server sync.  Pattern (from mt5_history_probe.py):
      1. symbol_select  — ensures symbol is active in terminal
      2. seed 10 recent bars → wakes broker download
      3. sleep 2s → wait for broker history sync
      4. date-range pull from HISTORY_FROM → gets full available history
      5. retry once if empty (broker may still be syncing)
      6. fallback: from_pos with large count
    """
    mt5.symbol_select(mt5_sym, True)
    time.sleep(0.3)

    # Step 1 — seed to trigger broker history download
    mt5.copy_rates_from_pos(mt5_sym, tf_const, 0, 10)
    time.sleep(2.0)   # wait for broker sync

    # Step 2 — date-range pull (gets all available history up to max_bars)
    rates = mt5.copy_rates_from(mt5_sym, tf_const, HISTORY_FROM, max_bars)
    if rates is None or len(rates) < 50:
        time.sleep(2.0)  # second sync wait
        rates = mt5.copy_rates_from(mt5_sym, tf_const, HISTORY_FROM, max_bars)

    # Step 3 — fallback: most-recent from_pos (works even without full history)
    if rates is None or len(rates) < 50:
        rates = mt5.copy_rates_from_pos(mt5_sym, tf_const, 0, min(max_bars, 50_000))

    if rates is None or len(rates) == 0:
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.set_index('time').rename(columns={'tick_volume': 'volume'})
    return df[['open', 'high', 'low', 'close', 'volume']].dropna()


def _h4_bias(df_h4: pd.DataFrame, candle_dt) -> str:
    """EMA 3/8 bias on H4 — same logic as live scanner."""
    try:
        sl = df_h4[df_h4.index <= candle_dt].tail(20)
        if len(sl) < 10:
            return 'RANGING'
        c    = sl['close']
        fast = c.ewm(span=3, adjust=False).mean().iloc[-1]
        slow = c.ewm(span=8, adjust=False).mean().iloc[-1]
        if fast > slow * 1.0003:
            return 'BULLISH'
        if fast < slow * 0.9997:
            return 'BEARISH'
    except Exception:
        pass
    return 'RANGING'


def _lot_size(account_size: float, risk_pct: float,
              entry: float, sl: float, csize: float,
              min_lot: float, vol_step: float) -> float:
    risk_usd = account_size * risk_pct / 100.0
    sl_dist  = abs(entry - sl)
    if sl_dist <= 0 or csize <= 0:
        return 0.0
    lots = risk_usd / (csize * sl_dist)
    # Round to nearest vol_step
    step = vol_step if vol_step > 0 else 0.01
    lots = round(round(lots / step) * step, 4)
    return max(min_lot, lots)


def _simulate(df: pd.DataFrame, start_idx: int, sig: dict,
              direction: str, lots: float, csize: float) -> dict:
    """
    Walk-forward simulation.
    Partial booking: 1/3 at T1 (SL → breakeven), 1/3 at T2, 1/3 at T3.
    """
    entry  = sig['entry']
    sl     = sig['stop_loss']
    t1, t2, t3 = sig['target1'], sig['target2'], sig['target3']
    cur_sl = sl
    hits   = []
    result = 'TIMEOUT'
    exit_p = float(df['close'].iloc[-1])
    partial = 0.0

    def _pnl(ep: float) -> float:
        d = (ep - entry) if direction == 'BULLISH' else (entry - ep)
        return round(lots * csize * d, 2)

    for i in range(start_idx + 1, min(start_idx + 300, len(df))):
        hi = float(df['high'].iloc[i])
        lo = float(df['low'].iloc[i])
        if direction == 'BULLISH':
            if lo <= cur_sl:
                result = 'SL_HIT'; exit_p = cur_sl; break
            if 'T1' not in hits and hi >= t1:
                hits.append('T1'); partial += _pnl(t1) / 3; cur_sl = entry
            if 'T2' not in hits and hi >= t2:
                hits.append('T2'); partial += _pnl(t2) / 3
            if hi >= t3:
                hits.append('T3'); result = 'TARGET_HIT'; exit_p = t3; break
        else:
            if hi >= cur_sl:
                result = 'SL_HIT'; exit_p = cur_sl; break
            if 'T1' not in hits and lo <= t1:
                hits.append('T1'); partial += _pnl(t1) / 3; cur_sl = entry
            if 'T2' not in hits and lo <= t2:
                hits.append('T2'); partial += _pnl(t2) / 3
            if lo <= t3:
                hits.append('T3'); result = 'TARGET_HIT'; exit_p = t3; break

    remaining = 3 - len(hits)
    final_pnl = partial + _pnl(exit_p) * remaining / 3

    return {
        'result'      : result,
        'targets_hit' : hits,
        'exit_price'  : round(exit_p, 5),
        'pnl_usd'     : round(final_pnl, 2),
        'lots'        : lots,
    }


# ── Per-symbol walk-forward backtest ──────────────────────────────────────────

def run_symbol(
    mt5_sym: str,
    df_15m: pd.DataFrame,
    df_h4: pd.DataFrame,
    csize: float,
    min_lot: float,
    vol_step: float,
    account_cfg: dict,
) -> dict:
    """Walk-forward ICT Silver Bullet backtest for one symbol."""
    from forex_engine.scanner.signal_scanner import scan_setup

    canonical    = CANONICAL_MAP.get(mt5_sym, mt5_sym.replace('.x', ''))
    account_size = account_cfg['size']
    risk_pct     = account_cfg['risk_pct']
    daily_limit  = account_cfg['daily_limit']

    # ── Vectorized pre-filters — computed once in O(n), lookup is O(1) ────────
    # Filter 1: price in top/bottom 40% of 30-bar range
    h30    = df_15m['high'].rolling(PREFILTER_BARS).max()
    l30    = df_15m['low'].rolling(PREFILTER_BARS).min()
    rng30  = (h30 - l30).clip(lower=1e-10)
    cl     = df_15m['close']
    near_x = (cl >= h30 - rng30 * PREFILTER_ZONE_PCT) | (cl <= l30 + rng30 * PREFILTER_ZONE_PCT)

    # Filter 2: candle body ≥ 80% of 20-bar average body (displacement present)
    body   = (df_15m['close'] - df_15m['open']).abs()
    avg_b  = body.rolling(20).mean().clip(lower=1e-10)
    disp   = body >= avg_b * DISPLACEMENT_RATIO

    # Combined gate: both conditions required to call expensive scan_setup()
    pf_ok  = (near_x & disp).to_numpy()

    # ── Limit scan to most recent MAX_SCAN_BARS bars ──────────────────────────
    start_i = max(WINDOW, len(df_15m) - MAX_SCAN_BARS)

    trades    = []
    capital   = account_size
    peak      = account_size
    daily_pnl = 0.0
    last_date = ''
    cd_rem    = 0
    i         = start_i

    while i < len(df_15m):
        cdt = df_15m.index[i]

        # Daily reset
        cdate = str(cdt)[:10]
        if cdate != last_date:
            daily_pnl = 0.0
            last_date = cdate

        # Daily DD gate
        if daily_pnl <= -daily_limit:
            i += 1; continue

        # Kill zone
        utc_h = cdt.hour if hasattr(cdt, 'hour') else 0
        if not _in_kill_zone(utc_h):
            i += 1; continue

        if cd_rem > 0:
            cd_rem -= 1; i += 1; continue

        # Vectorized pre-filter (O(1) array lookup)
        if not pf_ok[i]:
            i += 1; continue

        # H4 bias
        bias = _h4_bias(df_h4, cdt)

        # Scanner — only reached by ~35% of kill-zone candles
        df_win = df_15m.iloc[i - WINDOW: i].copy()
        try:
            setup = scan_setup(df_win, canonical, h4_bias=bias)
        except Exception:
            i += 1; continue
        if not setup:
            i += 1; continue

        # Sweep confirmation (same as live)
        liq  = setup.get('liq_sweep')
        ok   = (
            liq is not None
            and int(liq.get('candles_ago', 999)) <= 15
            and liq.get('direction') == setup['direction']
            and liq.get('level_state') == 'SWEPT'
            and int(liq.get('confidence', 0) or 0) >= 45
        )
        if not ok:
            i += 1; continue

        # Price inside FVG
        if not setup.get('in_fvg'):
            i += 1; continue

        # H4 bias — hard block counter-trend
        if bias != 'RANGING' and bias != setup['direction']:
            i += 1; continue

        # Score gate
        mss_type  = setup.get('mss_type', 'BOS')
        eff_score = setup['confluence'] + (1 if mss_type == 'CHOCH' else 0)
        min_s     = MIN_SCORE + (1 if bias == 'RANGING' else 0)
        if eff_score < min_s:
            i += 1; continue

        sig  = setup['entry_signal']
        lots = _lot_size(account_size, risk_pct,
                         sig['entry'], sig['stop_loss'], csize, min_lot, vol_step)
        if lots <= 0:
            i += 1; continue

        out       = _simulate(df_15m, i, sig, setup['direction'], lots, csize)
        pnl       = out['pnl_usd']
        capital  += pnl
        daily_pnl += pnl
        peak      = max(peak, capital)

        trades.append({
            'time'     : str(cdt)[:16],
            'direction': setup['direction'],
            'mss_type' : mss_type,
            'score'    : setup['confluence'],
            'eff_score': eff_score,
            'h4_bias'  : bias,
            'entry'    : sig['entry'],
            'sl'       : sig['stop_loss'],
            't1'       : sig['target1'],
            't2'       : sig['target2'],
            't3'       : sig['target3'],
            'rr'       : sig['rr_ratio'],
            'targets'  : out['targets_hit'],
            'result'   : out['result'],
            'lots'     : lots,
            'pnl_usd'  : pnl,
            'capital'  : round(capital, 2),
        })
        cd_rem = COOLDOWN
        i += 1

    if not trades:
        return {}

    wins      = [t for t in trades if t['pnl_usd'] > 0]
    losses    = [t for t in trades if t['pnl_usd'] <= 0]
    gross_win = sum(t['pnl_usd'] for t in wins)
    gross_los = abs(sum(t['pnl_usd'] for t in losses)) if losses else 0
    pf        = round(gross_win / gross_los, 2) if gross_los > 0 else None

    # Max drawdown (equity curve)
    pk = account_size; mdd = 0.0
    for t in trades:
        pk  = max(pk, t['capital'])
        mdd = max(mdd, pk - t['capital'])
    mdd_pct = round(mdd / account_size * 100, 2)

    t_results = [t['result'] for t in trades]
    t3_count  = sum(1 for t in trades if 'T3' in t['targets'])

    return {
        'symbol'        : mt5_sym,
        'canonical'     : canonical,
        'contract_size' : csize,
        'total_trades'  : len(trades),
        'wins'          : len(wins),
        'losses'        : len(losses),
        'win_rate'      : round(len(wins) / len(trades) * 100, 1),
        'net_pnl'       : round(sum(t['pnl_usd'] for t in trades), 2),
        'profit_factor' : pf,
        'max_dd_pct'    : mdd_pct,
        'final_capital' : round(capital, 2),
        'growth_pct'    : round((capital - account_size) / account_size * 100, 2),
        'avg_win_usd'   : round(gross_win / len(wins), 2) if wins else 0,
        'avg_loss_usd'  : round(-gross_los / len(losses), 2) if losses else 0,
        'sl_hit_pct'    : round(t_results.count('SL_HIT') / len(trades) * 100, 1),
        't3_hit_pct'    : round(t3_count / len(trades) * 100, 1),
        'long_count'    : sum(1 for t in trades if t['direction'] == 'BULLISH'),
        'short_count'   : sum(1 for t in trades if t['direction'] == 'BEARISH'),
        'choch_count'   : sum(1 for t in trades if t['mss_type'] == 'CHOCH'),
        'period'        : f"{trades[0]['time'][:10]} → {trades[-1]['time'][:10]}",
        'bars_15m'      : len(df_15m),
        'trades'        : trades,
    }


# ── Results display ───────────────────────────────────────────────────────────

def _symbol_tier(r: dict) -> str:
    """Classify symbol as A+/B/C/D based on WR + PF."""
    wr = r['win_rate']
    pf = r['profit_factor'] or 0
    if wr >= 55 and pf >= 1.5:
        return 'A+'
    if wr >= 50 and pf >= 1.2:
        return 'B'
    if wr >= 45:
        return 'C'
    return 'D'


def _print_table(results: list, account_label: str, account_size: float):
    valid = sorted([r for r in results if r.get('total_trades', 0) > 0],
                   key=lambda x: x['net_pnl'], reverse=True)
    if not valid:
        print("No trades found across all symbols.")
        return

    print(f"\n{'='*112}")
    print(f"  CB6 QUANTUM — GFT FULL FOREX BACKTEST RESULTS")
    print(f"  Account : {account_label}  |  Size: ${account_size:,.0f}")
    print(f"  Filters : Kill Zones (London 07-12/NY 16-20 UTC) + H4 Bias + Sweep ≥45 + FVG + Score ≥{MIN_SCORE}")
    print(f"{'='*112}")
    print(f"  {'Tier':<4} {'Symbol':<14} {'Trades':>7} {'W':>4} {'L':>4} "
          f"{'WR%':>6} {'Net PnL':>10} {'Growth':>8} {'MaxDD%':>7} "
          f"{'PF':>5} {'Avg W':>7} {'Avg L':>7} {'Period'}")
    print('  ' + '-' * 108)

    for r in valid:
        tier   = _symbol_tier(r)
        pf_s   = f"{r['profit_factor']:.2f}" if r['profit_factor'] else '  N/A'
        flag   = '✅' if r['net_pnl'] > 0 else '❌'
        print(
            f"  {tier:<4} {r['symbol']:<12} {flag}"
            f"  {r['total_trades']:>5}  {r['wins']:>4}  {r['losses']:>4}"
            f"  {r['win_rate']:>5.1f}%"
            f"  ${r['net_pnl']:>+8.2f}"
            f"  {r['growth_pct']:>+7.2f}%"
            f"  {r['max_dd_pct']:>5.1f}%"
            f"  {pf_s:>5}"
            f"  ${r['avg_win_usd']:>5.2f}"
            f"  ${r['avg_loss_usd']:>5.2f}"
            f"  {r['period']}"
        )

    print('  ' + '=' * 108)
    # Aggregate totals
    tot   = sum(r['total_trades'] for r in valid)
    wins  = sum(r['wins']         for r in valid)
    pnl   = sum(r['net_pnl']      for r in valid)
    wr    = round(wins / tot * 100, 1) if tot else 0

    gw = sum(r['avg_win_usd']  * r['wins']   for r in valid)
    gl = sum(r['avg_loss_usd'] * r['losses'] for r in valid)
    g_pf = round(gw / gl, 2) if gl > 0 else None
    pf_s = f"{g_pf:.2f}" if g_pf else 'N/A'

    print(f"  {'ALL':<4} {'COMBINED':<14}   {tot:>5}  {wins:>4}  {tot-wins:>4}"
          f"  {wr:>5.1f}%  ${pnl:>+8.2f}  {'':>8}  {'':>7}"
          f"  {pf_s:>5}")
    print()

    # Tier breakdown
    tiers = {'A+': [], 'B': [], 'C': [], 'D': []}
    for r in valid:
        tiers[_symbol_tier(r)].append(r['symbol'])

    print("  Tier breakdown:")
    for tier, syms in tiers.items():
        if syms:
            label = {
                'A+': 'A+ (WR≥55% PF≥1.5) — ENABLE LIVE',
                'B':  'B  (WR≥50% PF≥1.2) — MONITOR',
                'C':  'C  (WR≥45%)         — PAPER ONLY',
                'D':  'D  (WR<45%)          — DISABLE',
            }[tier]
            print(f"    {label}: {', '.join(syms)}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='CB6 GFT Full Forex Backtest')
    ap.add_argument('--account',  default='5k', choices=['5k', '1k', '10k'],
                    help='GFT account (default: 5k)')
    ap.add_argument('--symbols',  default='',
                    help='Comma-separated MT5 symbol names (default: auto-discover all)')
    ap.add_argument('--min-bars', type=int, default=MIN_BARS,
                    help=f'Min 15m bars required (default {MIN_BARS})')
    args = ap.parse_args()

    cfg = ACCOUNT_CONFIGS[args.account]

    stamp   = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir = ROOT / 'reports'
    out_dir.mkdir(exist_ok=True)
    out_json = out_dir / f'gft_forex_backtest_{stamp}.json'
    out_csv  = out_dir / f'gft_forex_backtest_{stamp}.csv'

    print(f"\n{'='*65}")
    print(f"  CB6 QUANTUM — GFT Full Forex Backtest")
    print(f"  Account      : {cfg['label']}")
    print(f"  Size / Risk  : ${cfg['size']:,.0f}  |  {cfg['risk_pct']}% per trade")
    print(f"  Kill zones   : London 07-12 UTC | NY 16-20 UTC")
    print(f"  Filters      : H4 bias + Sweep(≥45) + FVG + Score≥{MIN_SCORE}")
    print(f"  Max bars/sym : {MAX_BARS:,} (from {HISTORY_FROM.strftime('%Y-%m-%d')})")
    print(f"{'='*65}\n")

    # ── Connect ───────────────────────────────────────────────────────────────
    print("Connecting to MT5...")
    if not _connect(cfg):
        sys.exit(1)
    print()

    # ── Symbol list ───────────────────────────────────────────────────────────
    if args.symbols:
        sym_objs = []
        for name in args.symbols.split(','):
            name = name.strip()
            info = mt5.symbol_info(name)
            if info:
                sym_objs.append(info)
            else:
                print(f"  WARNING: symbol not found → {name}")
    else:
        print("Discovering symbols...")
        sym_objs = _discover_symbols()
        print(f"  {len(sym_objs)} symbols found\n")

    if not sym_objs:
        print("No symbols to backtest. Exiting.")
        mt5.shutdown()
        sys.exit(1)

    # ── Suppress verbose INFO logging from scanner ────────────────────────────
    # logging.disable(INFO) is the only reliable way to silence the custom
    # utils.logger which has its own handlers bypassing root logger level.
    logging.disable(logging.INFO)

    # ── Per-symbol backtest ───────────────────────────────────────────────────
    all_results: list[dict] = []
    skipped:     list[tuple] = []

    for sym_info in sym_objs:
        name = sym_info.name
        csize    = sym_info.trade_contract_size
        min_lot  = sym_info.volume_min
        vol_step = max(sym_info.volume_step, 0.01)
        digits   = sym_info.digits

        print(f"  {name:<14} fetching 15m...", end='', flush=True)
        df_15m = _fetch(name, mt5.TIMEFRAME_M15, MAX_BARS)

        if len(df_15m) < args.min_bars:
            print(f" only {len(df_15m)} bars — skipped")
            skipped.append((name, f'{len(df_15m)} bars < {args.min_bars}'))
            continue

        df_h4 = _fetch(name, mt5.TIMEFRAME_H4, 3_000)
        print(f" {len(df_15m):>7,} bars (H4:{len(df_h4):>4}) ...", end='', flush=True)

        try:
            result = run_symbol(
                mt5_sym     = name,
                df_15m      = df_15m,
                df_h4       = df_h4,
                csize       = csize,
                min_lot     = min_lot,
                vol_step    = vol_step,
                account_cfg = cfg,
            )
        except Exception as exc:
            print(f" ERROR: {exc}")
            skipped.append((name, str(exc)[:80]))
            continue

        if result:
            tier = _symbol_tier(result)
            print(
                f" {result['total_trades']:>4} trades  "
                f"WR={result['win_rate']:.1f}%  "
                f"PnL=${result['net_pnl']:>+8.2f}  "
                f"PF={result['profit_factor'] or 'N/A'}  "
                f"Tier={tier}"
            )
            all_results.append(result)
        else:
            print(" 0 trades (no valid setups)")
            skipped.append((name, 'no valid setups'))

    mt5.shutdown()
    logging.disable(logging.NOTSET)   # re-enable all logging
    print(f"\nMT5 disconnected. Processed {len(all_results)} symbols.\n")

    # ── Print results ─────────────────────────────────────────────────────────
    _print_table(all_results, cfg['label'], cfg['size'])

    if skipped:
        print(f"  Skipped ({len(skipped)}):")
        for name, reason in skipped:
            print(f"    {name:<14} — {reason}")
        print()

    # ── Save JSON ─────────────────────────────────────────────────────────────
    report = {
        'generated_at' : datetime.now().isoformat(),
        'account'      : cfg['label'],
        'account_size' : cfg['size'],
        'risk_pct'     : cfg['risk_pct'],
        'kill_zones'   : KILL_ZONES,
        'min_score'    : MIN_SCORE,
        'cooldown'     : COOLDOWN,
        'window'       : WINDOW,
        'max_bars'     : MAX_BARS,
        'history_from' : str(HISTORY_FROM.date()),
        'symbols_run'  : len(all_results),
        'symbols_skip' : len(skipped),
        'skipped'      : [{'symbol': n, 'reason': r} for n, r in skipped],
        'results'      : [
            {k: v for k, v in r.items() if k != 'trades'}
            for r in all_results
        ],
        'trade_log'    : {
            r['symbol']: r['trades'] for r in all_results
        },
    }
    with open(out_json, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"  JSON saved → {out_json}")

    # ── Save CSV ──────────────────────────────────────────────────────────────
    if all_results:
        rows = sorted([{
            'tier'           : _symbol_tier(r),
            'symbol'         : r['symbol'],
            'canonical'      : r['canonical'],
            'total_trades'   : r['total_trades'],
            'wins'           : r['wins'],
            'losses'         : r['losses'],
            'win_rate_pct'   : r['win_rate'],
            'net_pnl_usd'    : r['net_pnl'],
            'profit_factor'  : r['profit_factor'],
            'growth_pct'     : r['growth_pct'],
            'max_dd_pct'     : r['max_dd_pct'],
            'avg_win_usd'    : r['avg_win_usd'],
            'avg_loss_usd'   : r['avg_loss_usd'],
            'sl_hit_pct'     : r['sl_hit_pct'],
            't3_hit_pct'     : r['t3_hit_pct'],
            'long_count'     : r['long_count'],
            'short_count'    : r['short_count'],
            'choch_count'    : r['choch_count'],
            'bars_15m'       : r['bars_15m'],
            'period'         : r['period'],
        } for r in all_results], key=lambda x: x['net_pnl_usd'], reverse=True)
        pd.DataFrame(rows).to_csv(out_csv, index=False)
        print(f"  CSV saved → {out_csv}")

    print()


if __name__ == '__main__':
    main()
