# backtest/crypto_backtest.py
# CB6 Quantum Crypto Backtester — ETH/USDT Binance Perpetual Futures
# Fetches public Binance klines (no API key needed), runs the same ICT scanner.
#
# Usage:
#   python backtest/crypto_backtest.py
#   python backtest/crypto_backtest.py --days 30

import os
import sys
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

from utils.logger import logger

CAPITAL    = float(os.getenv('CRYPTO_CAPITAL', '8.4'))   # USDT — match live config
RISK_PCT   = 5.0    # 5% per trade
INTERVAL   = '5m'
MIN_SCORE  = 7
SYMBOL     = 'ETHUSDT'
LOT_STEP   = 0.001
MIN_QTY    = 0.001
MIN_FVG    = 8.0
FVG_BUF    = 1.5
LEVERAGE   = 20     # 20x cross margin (Binance default)

REST_BASE  = 'https://fapi.binance.com'


def _fetch_klines(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Fetch historical klines from Binance public REST API."""
    limit    = min(days * 288, 1500)   # 288 x 5m = 1 day; Binance max 1500/req
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 86400 * 1000

    all_rows = []
    batch_start = start_ms

    while batch_start < end_ms and len(all_rows) < days * 288:
        try:
            r = requests.get(
                f"{REST_BASE}/fapi/v1/klines",
                params={
                    'symbol'   : symbol,
                    'interval' : interval,
                    'startTime': batch_start,
                    'limit'    : 1000,
                },
                timeout=15,
            )
            r.raise_for_status()
            rows = r.json()
            if not rows:
                break
            all_rows.extend(rows)
            batch_start = int(rows[-1][0]) + 1
            time.sleep(0.2)   # rate limit friendly
        except Exception as e:
            logger.error(f"Binance klines fetch error: {e}")
            break

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_vol', 'trades', 'taker_base', 'taker_quote', 'ignore'
    ])
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].copy()
    df = df.sort_values('timestamp').reset_index(drop=True)
    return df


def _simulate(df: pd.DataFrame, setup_idx: int, entry: float, sl: float,
              t1: float, t2: float, t3: float, direction: str) -> dict:
    """Walk-forward simulation. Partial booking: 1/3 at T1, 1/3 at T2, 1/3 at T3."""
    targets_hit = []
    current_sl  = sl
    result      = 'TIMEOUT'
    exit_price  = float(df['close'].iloc[-1])

    for i in range(setup_idx + 1, min(setup_idx + 200, len(df))):
        high = float(df['high'].iloc[i])
        low  = float(df['low'].iloc[i])

        if direction == 'BULLISH':
            if low <= current_sl:
                result     = 'SL_HIT'
                exit_price = current_sl
                break
            if 'T1' not in targets_hit and high >= t1:
                targets_hit.append('T1')
                current_sl  = entry
            if 'T2' not in targets_hit and high >= t2:
                targets_hit.append('T2')
                current_sl  = round(t1 + (t2 - t1) * 0.5, 2)
            if high >= t3:
                targets_hit.append('T3')
                result      = 'TARGET_HIT'
                exit_price  = t3
                break
        else:
            if high >= current_sl:
                result     = 'SL_HIT'
                exit_price = current_sl
                break
            if 'T1' not in targets_hit and low <= t1:
                targets_hit.append('T1')
                current_sl  = entry
            if 'T2' not in targets_hit and low <= t2:
                targets_hit.append('T2')
                current_sl  = round(t1 - (t1 - t2) * 0.5, 2)
            if low <= t3:
                targets_hit.append('T3')
                result      = 'TARGET_HIT'
                exit_price  = t3
                break

    # PnL calculation (USDT): 1/3 position at each level
    remaining = 1.0
    pnl_usdt  = 0.0
    risk_dist = abs(entry - sl)

    def _partial(ep):
        return (ep - entry) if direction == 'BULLISH' else (entry - ep)

    if 'T1' in targets_hit:
        pnl_usdt  += 0.33 * _partial(t1)
        remaining -= 0.33
    if 'T2' in targets_hit:
        pnl_usdt  += 0.33 * _partial(t2)
        remaining -= 0.33
    pnl_usdt += remaining * _partial(exit_price)

    # Scale by position size (qty based on 5% risk)
    if risk_dist > 0:
        qty = max(LOT_STEP, int(CAPITAL * RISK_PCT / 100 / risk_dist / LOT_STEP) * LOT_STEP)
    else:
        qty = LOT_STEP

    pnl_usdt = round(pnl_usdt * qty, 4)
    r_mult   = round(pnl_usdt / (risk_dist * qty), 2) if risk_dist * qty > 0 else 0

    return {
        'result'     : result,
        'exit_price' : exit_price,
        'targets_hit': targets_hit,
        'pnl_usdt'   : pnl_usdt,
        'r_multiple' : r_mult,
        'qty'        : qty,
        'is_win'     : pnl_usdt > 0,
    }


def run_crypto_backtest(days: int = 30) -> dict:
    print(f"\n{'='*60}")
    print(f"  CB6 QUANTUM — CRYPTO BACKTEST")
    print(f"  Symbol   : {SYMBOL} (Binance Perp)")
    print(f"  Period   : {days} days | Timeframe: {INTERVAL}")
    print(f"  Capital  : ${CAPITAL} USDT | Risk: {RISK_PCT}%/trade | Leverage: {LEVERAGE}x")
    print(f"{'='*60}")

    print(f"  Fetching {days}d {INTERVAL} data from Binance...")
    df = _fetch_klines(SYMBOL, INTERVAL, days)
    if df.empty or len(df) < 200:
        print(f"  ERROR: Insufficient data ({len(df)} candles).")
        return {}
    print(f"  {len(df)} candles loaded. Scanning...")

    from scanner.silver_bullet import (
        find_draw_on_liquidity, detect_sb_mss, detect_sb_fvg,
    )
    from scanner.ut_bot import get_ut_signal

    trades      = []
    capital     = CAPITAL
    WINDOW      = 200
    STEP        = 5       # check every 5 candles (~25 min)
    COOLDOWN    = 10      # candles between trades on same symbol
    cooldown    = 0
    dedup       = set()

    for end_idx in range(WINDOW, len(df) - 20, STEP):
        if cooldown > 0:
            cooldown -= 1
            continue

        window = df.iloc[:end_idx + 1].copy().reset_index(drop=True)

        dol = find_draw_on_liquidity(window, lookback=80, wick_sweep=True)
        if dol is None:
            continue

        mss = detect_sb_mss(window, lookback=40)
        if mss is None:
            continue
        direction = mss['direction']

        fvg = detect_sb_fvg(window, direction, lookback=25,
                            displacement_mult=1.0, use_range=True)
        if fvg is None:
            continue

        fvg_size = fvg.get('size', 0)
        if fvg_size < 1.0:
            continue

        last_low   = float(window['low'].iloc[-1])
        last_high  = float(window['high'].iloc[-1])
        last_close = float(window['close'].iloc[-1])
        fvg_low    = fvg['fvg_low']
        fvg_high   = fvg['fvg_high']
        fvg_mid    = fvg['mid']

        in_fvg  = last_low <= fvg_high and last_high >= fvg_low
        near_fvg = abs(last_close - fvg_mid) / fvg_mid <= 0.02
        if not (in_fvg or near_fvg):
            continue

        # Scoring
        dol_agrees = dol['direction'] == direction
        mss_type   = mss.get('type', 'BOS')
        try:
            ut = get_ut_signal(window)
            ut_aligned = ut.get('trend') == direction
        except Exception:
            ut_aligned = None

        score  = 5 if dol_agrees else 4
        score += 2 if mss_type == 'CHOCH' else 1
        score += 1 if in_fvg else 0
        score += 1 if fvg.get('displacement') else 0
        score += 1 if fvg_size >= MIN_FVG else 0
        score += 2 if ut_aligned else 0

        if score < MIN_SCORE:
            continue

        actual_fvg = max(fvg_size, MIN_FVG)
        if direction == 'BULLISH':
            entry = round(fvg_low + FVG_BUF, 2)
            sl    = round(fvg_low - actual_fvg, 2)
            risk  = round(entry - sl, 2)
            if risk <= 0: continue
            t1 = round(entry + risk * 2.0, 2)
            t2 = round(entry + risk * 3.0, 2)
            dol_l = dol['level']
            t3 = round(max(dol_l if dol_l > t2 else entry + risk * 4.0, t2), 2)
        else:
            entry = round(fvg_high - FVG_BUF, 2)
            sl    = round(fvg_high + actual_fvg, 2)
            risk  = round(sl - entry, 2)
            if risk <= 0: continue
            t1 = round(entry - risk * 2.0, 2)
            t2 = round(entry - risk * 3.0, 2)
            dol_l = dol['level']
            t3 = round(min(dol_l if dol_l < t2 else entry - risk * 4.0, t2), 2)

        ts  = window['timestamp'].iloc[-1]
        date_str = str(ts)[:10]
        zone_key = round(fvg_low / 50) * 50
        dedup_k  = (date_str, direction, zone_key)
        if dedup_k in dedup:
            continue
        dedup.add(dedup_k)

        outcome = _simulate(df, end_idx, entry, sl, t1, t2, t3, direction)
        capital += outcome['pnl_usdt']

        trades.append({
            'date'      : date_str,
            'time'      : str(ts)[11:16],
            'direction' : direction,
            'score'     : score,
            'mss_type'  : mss_type,
            'entry'     : entry,
            'sl'        : sl,
            'risk'      : risk,
            't1'        : t1,
            't2'        : t2,
            't3'        : t3,
            'qty'       : outcome['qty'],
            'result'    : outcome['result'],
            'targets'   : ','.join(outcome['targets_hit']),
            'pnl_usdt'  : outcome['pnl_usdt'],
            'r_multiple': outcome['r_multiple'],
            'capital'   : round(capital, 4),
            'is_win'    : outcome['is_win'],
        })

        cooldown = COOLDOWN

    if not trades:
        print("  No trades found in period.")
        return {}

    wins   = [t for t in trades if t['is_win']]
    losses = [t for t in trades if not t['is_win']]
    total  = len(trades)
    wr     = round(len(wins) / total * 100, 1)
    net    = round(sum(t['pnl_usdt'] for t in trades), 4)
    avg_w  = round(sum(t['pnl_usdt'] for t in wins) / len(wins), 4) if wins else 0
    avg_l  = round(sum(t['pnl_usdt'] for t in losses) / len(losses), 4) if losses else 0
    growth = round((capital - CAPITAL) / CAPITAL * 100, 2)

    caps   = [t['capital'] for t in trades]
    peak   = CAPITAL
    max_dd = 0.0
    for c in caps:
        if c > peak:
            peak = c
        dd = peak - c
        if dd > max_dd:
            max_dd = dd

    r_vals  = [t['r_multiple'] for t in trades]
    total_r = round(sum(r_vals), 2)
    avg_r   = round(sum(r_vals) / total, 2)

    print(f"\n{'─'*60}")
    print(f"  CRYPTO RESULTS — {SYMBOL} | {days}d")
    print(f"{'─'*60}")
    print(f"  Trades       : {total}  (W:{len(wins)} L:{len(losses)})")
    print(f"  Win Rate     : {wr}%")
    print(f"  Net PnL      : ${net:+.4f} USDT")
    print(f"  Growth       : {growth:+.2f}%  (${CAPITAL} → ${capital:.4f})")
    print(f"  Avg R/trade  : {avg_r}R  |  Total R: {total_r}R")
    print(f"  Avg Win      : ${avg_w:.4f} USDT")
    print(f"  Avg Loss     : ${avg_l:.4f} USDT")
    print(f"  Max Drawdown : ${max_dd:.4f} USDT")
    print(f"{'─'*60}")

    # Monthly breakdown
    monthly: dict = {}
    for t in trades:
        m = t['date'][:7]
        if m not in monthly:
            monthly[m] = {'w': 0, 'l': 0, 'pnl': 0.0}
        monthly[m]['w' if t['is_win'] else 'l'] += 1
        monthly[m]['pnl'] += t['pnl_usdt']
    print(f"\n  {'Month':<10}  {'W':>4}  {'L':>4}  {'WR%':>5}  {'PnL':>10}")
    for m in sorted(monthly):
        ms = monthly[m]; mt = ms['w'] + ms['l']
        print(f"  {m:<10}  {ms['w']:>4}  {ms['l']:>4}  {round(ms['w']/mt*100):>4}%  ${ms['pnl']:>+8.4f}")
    print(f"{'='*60}\n")

    return {
        'total': total, 'wins': len(wins), 'losses': len(losses),
        'win_rate': wr, 'net_pnl': net, 'growth_pct': growth,
        'avg_win': avg_w, 'avg_loss': avg_l,
        'max_dd_usdt': round(max_dd, 4),
        'total_r': total_r, 'avg_r': avg_r,
        'final_capital': round(capital, 4),
        'trades': trades,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=30)
    args = parser.parse_args()
    run_crypto_backtest(args.days)
