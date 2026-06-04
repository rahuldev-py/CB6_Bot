# run_backtest.py — CB6 Quantum Full NSE Backtest
#
# Runs ICT Silver Bullet strategy on all 4 indices using Fyers 3-min data.
#
# Capital:          Rs 33,000
# Risk per trade:   Rs 1,000 max (hard cap — size down if needed)
# Data:             Max available from Fyers (~100 days)
# Timeframe:        3-minute bars
# Indices:          NIFTY | BANKNIFTY | FINNIFTY | MIDCPNIFTY
#
# Usage:
#   python run_backtest.py
#   python run_backtest.py --days 90
#   python run_backtest.py --index NIFTY
#   python run_backtest.py --from 2026-03-01 --to 2026-06-01

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.logger import logger

# ── Constants ──────────────────────────────────────────────────────────────────

CAPITAL          = 33_000.0    # Rs
RISK_PER_TRADE   = 1_000.0     # Rs — hard cap, never exceed this per trade
MAX_FYERS_DAYS   = 100         # Fyers intraday data limit
WINDOW_BARS      = 150         # rolling lookback (7.5 h at 3-min)
COOLDOWN_BARS    = 10          # bars to skip after a trade (~30 min)
MAX_HOLD_BARS    = 100         # force-close after 5 h
EOD_HOUR         = 15
EOD_MIN          = 20
SCAN_START_H     = 10          # skip 9:15 Judas Swing window

LOT_SIZES = {
    'NIFTY'      : 75,
    'BANKNIFTY'  : 30,
    'FINNIFTY'   : 65,
    'MIDCPNIFTY' : 120,
}

ALL_INDICES = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']

RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'data', 'backtest_results')

# ── Position Sizing ────────────────────────────────────────────────────────────

def _calc_lots(risk_pts: float, index_name: str) -> int:
    """
    Lots = floor(RISK_PER_TRADE / (risk_pts x lot_size)).
    Minimum 1 lot always.
    Caps at a sensible maximum to avoid artificial inflation.
    """
    lot_size = LOT_SIZES.get(index_name.upper(), 75)
    if risk_pts <= 0 or lot_size <= 0:
        return 1
    risk_per_lot = risk_pts * lot_size
    lots = max(1, int(RISK_PER_TRADE / risk_per_lot))
    return lots


def _calc_pnl(r_multiple: float, risk_pts: float, lots: int, index_name: str) -> float:
    """Actual Rs PnL from R multiple x risk amount."""
    lot_size   = LOT_SIZES.get(index_name.upper(), 75)
    actual_risk = risk_pts * lots * lot_size
    return round(r_multiple * actual_risk, 2)


# ── Data Fetch ─────────────────────────────────────────────────────────────────

def _load_data_fyers_direct(fyers, symbol: str, days: int) -> Optional[pd.DataFrame]:
    """
    Fetch historical 3-min data directly from Fyers — bypasses TrueData.
    Uses cont_flag=1 for continuous contract (handles futures rollovers).
    Chunks automatically if days > 90 (Fyers intraday limit ~100 days/request).

    Verified: 90 days = ~7,250 candles per index. 180 days = ~15,000 candles.
    """
    from datetime import datetime, timedelta
    import time

    CHUNK_DAYS = 90
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=days)
    chunks     = []

    cur_start = start_date
    while cur_start < end_date:
        cur_end = min(cur_start + timedelta(days=CHUNK_DAYS), end_date)
        payload = {
            "symbol"      : symbol,
            "resolution"  : "3",
            "date_format" : "1",
            "range_from"  : cur_start.strftime("%Y-%m-%d"),
            "range_to"    : cur_end.strftime("%Y-%m-%d"),
            "cont_flag"   : "1",
        }
        for attempt in range(3):
            try:
                resp = fyers.history(data=payload)
                code = resp.get("code", resp.get("s", 0))
                if code == 200 or resp.get("s") == "ok":
                    candles = resp.get("candles", [])
                    if candles:
                        df_chunk = pd.DataFrame(candles,
                                                columns=["timestamp","open","high","low","close","volume"])
                        df_chunk["timestamp"] = (
                            pd.to_datetime(df_chunk["timestamp"], unit="s")
                            + pd.Timedelta(hours=5, minutes=30)
                        )
                        chunks.append(df_chunk)
                        logger.info(f"Fyers chunk {cur_start.strftime('%Y-%m-%d')} to "
                                    f"{cur_end.strftime('%Y-%m-%d')}: {len(candles)} candles")
                    break
                if code == 429:
                    time.sleep(2 ** attempt)
                    continue
                logger.error(f"Fyers history error: {resp}")
                break
            except Exception as e:
                logger.error(f"Fyers history exception: {e}")
                time.sleep(0.5)
        cur_start = cur_end + timedelta(days=1)

    if not chunks:
        return None

    df = pd.concat(chunks, ignore_index=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    df["ts"] = df["timestamp"]
    return df


def _load_data(fyers, index_name: str, days: int) -> Optional[pd.DataFrame]:
    try:
        from scanner.index_futures import get_active_futures
        symbol = get_active_futures().get(index_name.upper())
        if not symbol:
            logger.error(f"Backtest: no symbol for {index_name}")
            return None

        logger.info(f"Backtest: fetching {days}d of 3m data for {index_name} ({symbol}) via Fyers direct")
        df = _load_data_fyers_direct(fyers, symbol, days)
        if df is None or len(df) < WINDOW_BARS + 50:
            logger.error(f"Backtest: insufficient data — {len(df) if df is not None else 0} bars")
            return None

        logger.info(f"Backtest: {len(df)} bars for {index_name} "
                    f"({df['ts'].iloc[0].strftime('%Y-%m-%d')} to {df['ts'].iloc[-1].strftime('%Y-%m-%d')})")
        return df
    except Exception as e:
        logger.error(f"Backtest: data load failed for {index_name}: {e}")
        return None


# ── Outcome Simulator ─────────────────────────────────────────────────────────

def _simulate(df: pd.DataFrame, entry_i: int,
              entry: float, sl: float,
              t1: float, t2: float, t3: float,
              risk: float, direction: str) -> Dict:
    """
    Walk-forward bar-by-bar simulation.
    Returns: {outcome, exit_price, r, exit_i, bars_held}

    Partial booking model (mirrors live SL monitor):
      T1 hit → 50% exit, SL → breakeven
      T2 hit → remaining exit
      T3 hit → full exit (if T1+T2 not yet hit)
    """
    outcome    = 'TIMEOUT'
    exit_price = None
    r_actual   = 0.0
    end_i      = min(entry_i + MAX_HOLD_BARS, len(df) - 1)
    sl_live    = sl      # SL moves to BE after T1

    t1_done   = False
    partial_r = 0.0      # R already booked at T1

    for j in range(entry_i + 1, end_i + 1):
        row  = df.iloc[j]
        h    = float(row['high'])
        l    = float(row['low'])
        ts_j = row['ts']

        # Force EOD close
        if ts_j.hour > EOD_HOUR or (ts_j.hour == EOD_HOUR and ts_j.minute >= EOD_MIN):
            exit_price = float(row['close'])
            rem_r = ((exit_price - entry) / risk if direction == 'BULLISH'
                     else (entry - exit_price) / risk)
            r_actual = round(partial_r * 0.5 + rem_r * 0.5, 2) if t1_done else round(rem_r, 2)
            outcome  = 'EOD'
            end_i    = j
            break

        if direction == 'BULLISH':
            # SL check
            if l <= sl_live:
                exit_price = sl_live
                rem_r = (sl_live - entry) / risk
                r_actual = round(partial_r * 0.5 + rem_r * 0.5, 2) if t1_done else -1.0
                outcome  = 'SL'
                end_i    = j
                break
            # T3 — full exit
            if h >= t3:
                t3_r = (t3 - entry) / risk
                r_actual = (round(partial_r * 0.5 + t3_r * 0.5, 2)
                            if t1_done else round(t3_r, 2))
                exit_price = t3
                outcome  = 'T3'
                end_i    = j
                break
            # T2 — remaining 50%
            if h >= t2 and t1_done:
                t2_r = (t2 - entry) / risk
                r_actual  = round(partial_r * 0.5 + t2_r * 0.5, 2)
                exit_price = t2
                outcome   = 'T2'
                end_i     = j
                break
            # T1 — 50% exit, SL → BE
            if h >= t1 and not t1_done:
                partial_r = (t1 - entry) / risk
                sl_live   = entry   # breakeven
                t1_done   = True

        else:  # BEARISH
            if h >= sl_live:
                exit_price = sl_live
                rem_r = (entry - sl_live) / risk
                r_actual = round(partial_r * 0.5 + rem_r * 0.5, 2) if t1_done else -1.0
                outcome  = 'SL'
                end_i    = j
                break
            if l <= t3:
                t3_r = (entry - t3) / risk
                r_actual = (round(partial_r * 0.5 + t3_r * 0.5, 2)
                            if t1_done else round(t3_r, 2))
                exit_price = t3
                outcome  = 'T3'
                end_i    = j
                break
            if l <= t2 and t1_done:
                t2_r = (entry - t2) / risk
                r_actual  = round(partial_r * 0.5 + t2_r * 0.5, 2)
                exit_price = t2
                outcome   = 'T2'
                end_i     = j
                break
            if l <= t1 and not t1_done:
                partial_r = (entry - t1) / risk
                sl_live   = entry
                t1_done   = True

    if exit_price is None:
        exit_price = float(df.iloc[end_i]['close'])
        rem_r = ((exit_price - entry) / risk if direction == 'BULLISH'
                 else (entry - exit_price) / risk)
        r_actual = round(partial_r * 0.5 + rem_r * 0.5, 2) if t1_done else round(rem_r, 2)

    return {
        'outcome'   : outcome,
        'exit_price': round(exit_price, 2),
        'r'         : r_actual,
        'exit_i'    : end_i,
        'bars_held' : end_i - entry_i,
    }


# ── Single Index Backtest ──────────────────────────────────────────────────────

def _run_index(fyers, index_name: str, days: int,
               from_date: Optional[str], to_date: Optional[str]) -> Dict:
    from scanner.silver_bullet import scan_silver_bullet
    from scanner.index_futures import get_active_futures

    symbol   = get_active_futures().get(index_name.upper(), '')
    lot_size = LOT_SIZES.get(index_name.upper(), 75)

    df = _load_data(fyers, index_name, days)
    if df is None:
        return {'error': f'No data for {index_name}', 'index': index_name, 'trades': []}

    # Date filter
    if from_date:
        t_from = pd.Timestamp(from_date + ' 09:00:00')
        df_bt  = df[df['ts'] >= t_from]
    else:
        # Use the full dataset minus 7 days pre-history
        history_cutoff = df['ts'].iloc[0] + pd.Timedelta(days=7)
        df_bt = df[df['ts'] >= history_cutoff]

    if to_date:
        t_to  = pd.Timestamp(to_date + ' 15:30:00')
        df_bt = df_bt[df_bt['ts'] <= t_to]

    # Actual backtest range
    actual_from = df_bt['ts'].iloc[0].strftime('%Y-%m-%d') if len(df_bt) else '?'
    actual_to   = df_bt['ts'].iloc[-1].strftime('%Y-%m-%d') if len(df_bt) else '?'

    bt_idx = df_bt.index.tolist()
    if len(bt_idx) < WINDOW_BARS + 20:
        return {'error': f'Too few bars ({len(bt_idx)}) in window', 'index': index_name, 'trades': []}

    logger.info(f"Backtest [{index_name}]: {len(bt_idx)} bars | "
                f"{actual_from} to {actual_to}")

    trades: List[Dict] = []
    i       = bt_idx[0]
    end_idx = bt_idx[-1]
    skip_to = 0

    while i <= end_idx:
        if i < skip_to:
            i += 1
            continue

        ts_now = df['ts'].iloc[i]
        if ts_now.hour < SCAN_START_H or ts_now.hour >= EOD_HOUR:
            i += 1
            continue

        # Rolling lookback window
        win_start = max(0, i - WINDOW_BARS)
        window    = df.iloc[win_start:i + 1][
            ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        ].copy()

        try:
            setup = scan_silver_bullet(window, symbol, tf='3',
                                       fyers=None, force=True)
        except Exception as e:
            logger.debug(f"Backtest [{index_name}] bar {i}: scan error: {e}")
            i += 1
            continue

        if setup is None:
            i += 1
            continue

        sig       = setup['entry_signal']
        entry     = float(sig['entry'])
        sl        = float(sig['stop_loss'])
        t1        = float(sig['target1'])
        t2        = float(sig['target2'])
        t3        = float(sig['target3'])
        risk_pts  = float(sig['risk'])
        direction = setup['direction']

        if risk_pts <= 0:
            i += 1
            continue

        # Position sizing: Rs 1,000 max risk
        lots        = _calc_lots(risk_pts, index_name)
        risk_rs     = round(risk_pts * lots * lot_size, 2)

        result = _simulate(df, i, entry, sl, t1, t2, t3, risk_pts, direction)

        pnl_rs  = _calc_pnl(result['r'], risk_pts, lots, index_name)
        hold_min = result['bars_held'] * 3

        exit_ts = df['ts'].iloc[result['exit_i']].strftime('%H:%M')
        trade = {
            'index'     : index_name,
            'date'      : ts_now.strftime('%Y-%m-%d'),
            'entry_time': ts_now.strftime('%H:%M'),
            'exit_time' : exit_ts,
            'direction' : direction,
            'entry'     : round(entry, 1),
            'sl'        : round(sl, 1),
            't1'        : round(t1, 1),
            't2'        : round(t2, 1),
            't3'        : round(t3, 1),
            'risk_pts'  : round(risk_pts, 1),
            'lots'      : lots,
            'lot_size'  : lot_size,
            'risk_rs'   : risk_rs,
            'outcome'   : result['outcome'],
            'exit_price': result['exit_price'],
            'r'         : result['r'],
            'pnl_rs'    : pnl_rs,
            'hold_min'  : hold_min,
            'score'     : setup.get('confluence', 0),
            'mss_type'  : setup.get('mss_type', '?'),
            'regime'    : setup.get('regime', '?'),
            'fvg_size'  : round(setup.get('fvg', {}).get('size', 0), 1),
            'sweep_q'   : setup.get('sweep_quality', 0),
            'sweep_type': setup.get('liq_sweep', {}).get('sweep_type', '') if setup.get('liq_sweep') else '',
            'window'    : setup.get('window', ''),
            'is_expiry' : setup.get('is_expiry_day', False),
        }
        trades.append(trade)

        logger.info(
            f"[{index_name}] {ts_now.strftime('%b%d %H:%M')} "
            f"{'LONG' if direction=='BULLISH' else 'SHORT'} "
            f"E:{entry:.0f} SL:{sl:.0f} | "
            f"{result['outcome']} {result['r']:+.2f}R = Rs {pnl_rs:+,.0f}"
        )

        skip_to = result['exit_i'] + COOLDOWN_BARS
        i = result['exit_i'] + 1

    # Per-index statistics
    return _build_stats(index_name, actual_from, actual_to, trades)


# ── Statistics Builder ─────────────────────────────────────────────────────────

def _build_stats(index_name: str, from_date: str, to_date: str,
                 trades: List[Dict]) -> Dict:
    if not trades:
        return {'index': index_name, 'from': from_date, 'to': to_date,
                'total_trades': 0, 'trades': []}

    wins   = [t for t in trades if t['r'] > 0]
    losses = [t for t in trades if t['r'] <= 0]
    longs  = [t for t in trades if t['direction'] == 'BULLISH']
    shorts = [t for t in trades if t['direction'] == 'BEARISH']

    total_r   = round(sum(t['r']      for t in trades), 2)
    total_pnl = round(sum(t['pnl_rs'] for t in trades), 2)
    wr        = round(len(wins) / len(trades) * 100, 1)

    avg_w_r   = round(sum(t['r'] for t in wins)   / len(wins),   2) if wins   else 0.0
    avg_l_r   = round(sum(t['r'] for t in losses) / len(losses), 2) if losses else 0.0
    avg_w_rs  = round(sum(t['pnl_rs'] for t in wins)   / len(wins),   0) if wins   else 0.0
    avg_l_rs  = round(sum(t['pnl_rs'] for t in losses) / len(losses), 0) if losses else 0.0

    tot_win_r  = sum(t['r'] for t in wins)
    tot_loss_r = abs(sum(t['r'] for t in losses))
    pf         = round(tot_win_r / tot_loss_r, 2) if tot_loss_r > 0 else float('inf')

    # Drawdown: running equity curve
    equity      = CAPITAL
    peak        = CAPITAL
    max_dd_rs   = 0.0
    equity_curve = []
    for t in trades:
        equity += t['pnl_rs']
        equity_curve.append(round(equity, 2))
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd_rs:
            max_dd_rs = dd
    max_dd_pct = round(max_dd_rs / CAPITAL * 100, 1)
    final_equity = round(equity, 2)

    # Outcome breakdown
    t3_n = sum(1 for t in trades if t['outcome'] == 'T3')
    t2_n = sum(1 for t in trades if t['outcome'] == 'T2')
    t1_n = sum(1 for t in trades if t['outcome'] == 'T1')
    sl_n = sum(1 for t in trades if t['outcome'] == 'SL')

    # Consecutive loss streak (max)
    max_consec_loss = 0
    cur_streak = 0
    for t in trades:
        if t['r'] <= 0:
            cur_streak += 1
            max_consec_loss = max(max_consec_loss, cur_streak)
        else:
            cur_streak = 0

    return {
        'index'          : index_name,
        'from'           : from_date,
        'to'             : to_date,
        'total_trades'   : len(trades),
        'wins'           : len(wins),
        'losses'         : len(losses),
        'win_rate'       : wr,
        'total_r'        : total_r,
        'total_pnl_rs'   : total_pnl,
        'final_equity'   : final_equity,
        'return_pct'     : round((final_equity - CAPITAL) / CAPITAL * 100, 1),
        'avg_win_r'      : avg_w_r,
        'avg_loss_r'     : avg_l_r,
        'avg_win_rs'     : avg_w_rs,
        'avg_loss_rs'    : avg_l_rs,
        'profit_factor'  : pf,
        't3_count'       : t3_n,
        't2_count'       : t2_n,
        't1_count'       : t1_n,
        'sl_count'       : sl_n,
        'longs'          : len(longs),
        'shorts'         : len(shorts),
        'long_wr'        : round(len([t for t in longs  if t['r']>0]) / len(longs)  * 100, 1) if longs  else 0,
        'short_wr'       : round(len([t for t in shorts if t['r']>0]) / len(shorts) * 100, 1) if shorts else 0,
        'max_drawdown_rs': round(max_dd_rs, 2),
        'max_drawdown_pct': max_dd_pct,
        'max_consec_loss': max_consec_loss,
        'equity_curve'   : equity_curve,
        'trades'         : trades,
    }


# ── Combined Report ────────────────────────────────────────────────────────────

def _combined_report(results: List[Dict]) -> Dict:
    """Merge all index results into a single portfolio summary."""
    all_trades = []
    for r in results:
        all_trades.extend(r.get('trades', []))

    # Sort by date + time
    all_trades.sort(key=lambda t: t['date'] + t['entry_time'])
    from_date = all_trades[0]['date']  if all_trades else '?'
    to_date   = all_trades[-1]['date'] if all_trades else '?'

    return _build_stats('ALL_INDICES', from_date, to_date, all_trades)


# ── CSV Export ─────────────────────────────────────────────────────────────────

def _export_csv(all_trades: List[Dict], filename: str) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, filename)
    if not all_trades:
        return path

    fieldnames = [
        'index', 'date', 'entry_time', 'exit_time', 'direction',
        'entry', 'sl', 't1', 't2', 't3', 'exit_price',
        'risk_pts', 'lots', 'lot_size', 'risk_rs',
        'outcome', 'r', 'pnl_rs', 'hold_min',
        'score', 'mss_type', 'regime', 'fvg_size',
        'sweep_q', 'sweep_type', 'window', 'is_expiry',
    ]
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        w.writerows(all_trades)
    return path


# ── Console + Telegram Formatter ──────────────────────────────────────────────

def _fmt_index_block(r: Dict) -> str:
    if 'error' in r or r['total_trades'] == 0:
        return f"  {r['index']}: No trades  ({r.get('error', 'no setups')})"

    wr   = r['win_rate']
    pnl  = r['total_pnl_rs']
    pf   = r['profit_factor']
    dd   = r['max_drawdown_rs']
    ret  = r['return_pct']
    pf_s = f"{pf:.2f}" if pf != float('inf') else "inf"

    grade = ('EXCELLENT' if wr >= 62 and pnl > 0
             else 'GOOD'     if wr >= 55 and pnl > 0
             else 'MARGINAL' if wr >= 45
             else 'WEAK')

    return (
        f"  {r['index']:<12} "
        f"T:{r['total_trades']:3d} "
        f"WR:{wr:5.1f}% "
        f"PnL:Rs {pnl:+8,.0f} "
        f"PF:{pf_s:>5} "
        f"MaxDD:Rs {dd:,.0f} "
        f"Ret:{ret:+.1f}% "
        f"[{grade}]"
    )


def _print_report(results: List[Dict], combined: Dict, csv_path: str) -> None:
    sep = "=" * 90
    print(f"\n{sep}")
    print("  CB6 QUANTUM — NSE BACKTEST REPORT")
    print(f"  Capital: Rs {CAPITAL:,.0f}  |  Risk/trade: Rs {RISK_PER_TRADE:,.0f}  |  TF: 3-min")
    print(sep)

    # Per-index
    for r in results:
        if 'error' not in r and r['total_trades'] > 0:
            _print_index_detail(r)

    # Combined
    print(f"\n{sep}")
    print("  COMBINED PORTFOLIO  (all 4 indices)")
    print(sep)
    _print_summary_table(combined)
    print(f"\n  Detailed CSV: {csv_path}")
    print(sep + "\n")


def _print_index_detail(r: Dict) -> None:
    sep2 = "-" * 70
    print(f"\n{'='*70}")
    print(f"  {r['index']}  |  {r['from']} to {r['to']}  |  3-minute TF")
    print(sep2)
    print(_fmt_index_block(r))
    print(sep2)
    print(f"  Outcome breakdown:")
    print(f"    T3:{r['t3_count']}  T2:{r['t2_count']}  T1:{r['t1_count']}  "
          f"SL:{r['sl_count']}  Other:{r['total_trades']-r['t1_count']-r['t2_count']-r['t3_count']-r['sl_count']}")
    print(f"  Direction: LONG {r['longs']} trades WR:{r['long_wr']}%  "
          f"| SHORT {r['shorts']} trades WR:{r['short_wr']}%")
    print(f"  Avg win: Rs {r['avg_win_rs']:+,.0f} ({r['avg_win_r']:+.2f}R)  "
          f"Avg loss: Rs {r['avg_loss_rs']:+,.0f} ({r['avg_loss_r']:+.2f}R)")
    print(f"  Max consec losses: {r['max_consec_loss']}")
    print(sep2)
    print("  TRADE LOG:")
    for t in r['trades']:
        icon = 'WIN ' if t['r'] > 0 else ('LOSS' if t['r'] < 0 else 'BE  ')
        print(f"    {icon} {t['date']} {t['entry_time']} "
              f"{'LONG ' if t['direction']=='BULLISH' else 'SHORT'} "
              f"E:{t['entry']:>8.1f} SL:{t['sl']:>8.1f} "
              f"| {t['outcome']:<7} {t['r']:+.2f}R "
              f"Rs {t['pnl_rs']:+8,.0f}  "
              f"[Sc:{t['score']} {t['mss_type']} SQ:{t['sweep_q']}]")


def _print_summary_table(c: Dict) -> None:
    print(f"\n  Trades   : {c['total_trades']}  ({c['wins']}W / {c['losses']}L)")
    print(f"  Win Rate : {c['win_rate']}%")
    print(f"  Total R  : {c['total_r']:+.2f}R")
    print(f"  PnL      : Rs {c['total_pnl_rs']:+,.0f}")
    print(f"  Capital  : Rs {CAPITAL:,.0f}  =>  Rs {c['final_equity']:,.0f}  ({c['return_pct']:+.1f}%)")
    print(f"  Max DD   : Rs {c['max_drawdown_rs']:,.0f}  ({c['max_drawdown_pct']:.1f}%)")
    print(f"  PF       : {c['profit_factor']:.2f}" if c['profit_factor'] != float('inf') else "  PF       : inf")
    print(f"  Avg Win  : Rs {c['avg_win_rs']:+,.0f}  Avg Loss: Rs {c['avg_loss_rs']:+,.0f}")
    print(f"  Long WR  : {c['long_wr']}%   Short WR: {c['short_wr']}%")
    print(f"  Max consec losses: {c['max_consec_loss']}")
    print()
    print(f"  Outcome: T3:{c['t3_count']}  T2:{c['t2_count']}  T1:{c['t1_count']}  "
          f"SL:{c['sl_count']}")


# ── Entry Point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='CB6 Quantum NSE Backtest')
    parser.add_argument('--days',  type=int, default=MAX_FYERS_DAYS,
                        help=f'Days of history (max {MAX_FYERS_DAYS})')
    parser.add_argument('--index', type=str, default='ALL',
                        help='Index to test: NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY/ALL')
    parser.add_argument('--from',  dest='from_date', type=str, default=None,
                        help='Start date YYYY-MM-DD')
    parser.add_argument('--to',    dest='to_date',   type=str, default=None,
                        help='End date YYYY-MM-DD')
    args = parser.parse_args()

    days = min(args.days, MAX_FYERS_DAYS)
    indices = ALL_INDICES if args.index.upper() == 'ALL' else [args.index.upper()]

    print(f"\nCB6 Quantum NSE Backtest")
    print(f"  Indices   : {', '.join(indices)}")
    print(f"  Days      : {days} (max available from Fyers)")
    print(f"  Timeframe : 3-minute")
    print(f"  Capital   : Rs {CAPITAL:,.0f}")
    print(f"  Max risk  : Rs {RISK_PER_TRADE:,.0f} per trade")
    print(f"  Sizing    : auto (lots = floor({RISK_PER_TRADE}/risk_pts/lot_size))")
    print()

    # Initialise Fyers
    print("Initialising Fyers connection...")
    try:
        from main import initialize_fyers, test_connection
        fyers = initialize_fyers()
        if not test_connection(fyers):
            print("ERROR: Fyers connection failed. Run python auto_token.py first.")
            sys.exit(1)
        print("Fyers connected.\n")
    except Exception as e:
        print(f"ERROR: Could not init Fyers: {e}")
        sys.exit(1)

    # Run per-index
    results = []
    for idx in indices:
        print(f"Running backtest: {idx}...")
        r = _run_index(fyers, idx, days, args.from_date, args.to_date)
        results.append(r)
        if 'error' in r:
            print(f"  {idx}: {r['error']}")
        else:
            print(f"  {idx}: {r['total_trades']} trades, WR={r['win_rate']}%, "
                  f"PnL=Rs {r['total_pnl_rs']:+,.0f}")

    # Combined
    print("\nBuilding combined report...")
    combined = _combined_report(results)

    # Export CSV
    ts_tag   = datetime.now().strftime('%Y%m%d_%H%M')
    csv_name = f"backtest_{ts_tag}_{'_'.join(i[:3] for i in indices)}.csv"
    all_trades = [t for r in results for t in r.get('trades', [])]
    all_trades.sort(key=lambda t: t['date'] + t['entry_time'])
    csv_path = _export_csv(all_trades, csv_name)
    print(f"CSV saved: {csv_path}")

    # Print full report
    _print_report(results, combined, csv_path)


if __name__ == '__main__':
    main()
