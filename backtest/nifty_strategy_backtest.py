# backtest/nifty_strategy_backtest.py
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Silver Bullet backtest on NSE:NIFTY50-INDEX â€” up to 100 days of Fyers data.
# (Fyers intraday API hard cap: max 100 days per request for 5-min resolution)
#
# Chain: DOL â†’ MSS â†’ FVG (displacement check) â†’ trade
# Windows: 10:00-11:00 IST  and  13:30-14:30 IST  (5-min candles)
# SL = below full FVG size  |  T1=2R  T2=3R  T3=DOL (validated direction)
#
# Usage:
#   python backtest/nifty_strategy_backtest.py
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

from __future__ import annotations

import os
import sys
import csv
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv()


# â”€â”€â”€ Fyers client â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _get_fyers():
    token = os.getenv('ACCESS_TOKEN', '')
    if not token or ':' not in token:
        print("ERROR: ACCESS_TOKEN missing in .env â€” run: python broker/web_token.py")
        sys.exit(1)
    try:
        from fyers_apiv3 import fyersModel
        client_id = token.split(':')[0]
        return fyersModel.FyersModel(
            client_id=client_id, token=token, is_async=False, log_path=''
        )
    except ImportError:
        print("ERROR: fyers_apiv3 not installed â€” pip install fyers-apiv3")
        sys.exit(1)


# â”€â”€â”€ Silver Bullet windows (IST minutes) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_SB_WINDOWS = [
    (10 * 60,      11 * 60),       # 10:00â€“11:00
    (13 * 60 + 30, 14 * 60 + 30),  # 13:30â€“14:30
]

def _in_sb_window(ts: datetime) -> bool:
    m = ts.hour * 60 + ts.minute
    return any(start <= m < end for start, end in _SB_WINDOWS)


# â”€â”€â”€ Silver Bullet backtest â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_sb_backtest(fyers, symbol: str, days: int = 100) -> list:
    """Walk-forward Silver Bullet backtest on 5-min Nifty data (max ~100 days, Fyers limit)."""
    from scanner.data_fetcher import get_historical_data
    from scanner.silver_bullet import (
        find_draw_on_liquidity, detect_sb_mss, detect_sb_fvg, FVG_BUFFER,
    )
    from backtest.backtester import simulate_trade_outcome

    print(f"  [SB] Fetching {days}-day 5-min data for {symbol}...")
    df = get_historical_data(fyers, symbol, timeframe='5', days=days)
    if df is None or len(df) < 100:
        print(f"  [SB] Insufficient data ({0 if df is None else len(df)} candles).")
        return []

    print(f"  [SB] {len(df)} candles loaded. Scanning...")

    results = []
    min_window   = 80
    step         = 3
    tracked_entries = set()

    for end_idx in range(min_window, len(df) - 10, step):
        ts = df['timestamp'].iloc[end_idx]

        if not _in_sb_window(ts):
            continue

        window = df.iloc[:end_idx + 1].copy().reset_index(drop=True)

        dol = find_draw_on_liquidity(window)
        if dol is None:
            continue
        direction = dol['direction']

        mss = detect_sb_mss(window)
        if mss is None or mss['direction'] != direction:
            continue

        fvg = detect_sb_fvg(window, direction)
        if fvg is None:
            continue

        last_close = float(window['close'].iloc[-1])
        fvg_low  = fvg['fvg_low']
        fvg_high = fvg['fvg_high']
        fvg_mid  = fvg['mid']

        in_fvg   = fvg_low <= last_close <= fvg_high
        near_fvg = abs(last_close - fvg_mid) / fvg_mid <= 0.008
        if not (in_fvg or near_fvg):
            continue

        # Risk = full FVG size so SL is below the entire gap
        fvg_size  = max(fvg.get('size', 1.0), 2.0)
        dol_level = dol['level']

        if direction == 'BULLISH':
            entry     = round(fvg_low + FVG_BUFFER, 2)
            stop_loss = round(fvg_low - fvg_size, 2)
            risk      = round(entry - stop_loss, 2)
            if risk <= 0:
                continue
            t1        = round(entry + risk * 2.0, 2)
            t2        = round(entry + risk * 3.0, 2)
            t3        = round(dol_level if dol_level > entry else entry + risk * 4.0, 2)
            trade_dir = 'BUY'
        else:
            entry     = round(fvg_high - FVG_BUFFER, 2)
            stop_loss = round(fvg_high + fvg_size, 2)
            risk      = round(stop_loss - entry, 2)
            if risk <= 0:
                continue
            t1        = round(entry - risk * 2.0, 2)
            t2        = round(entry - risk * 3.0, 2)
            t3        = round(dol_level if dol_level < entry else entry - risk * 4.0, 2)
            trade_dir = 'SELL'

        # One setup per day per direction (dedup)
        dedup_key = (str(ts)[:10], direction, round(entry, 0))
        if dedup_key in tracked_entries:
            continue
        tracked_entries.add(dedup_key)

        outcome = simulate_trade_outcome(df, end_idx, entry, stop_loss, t1, t2, t3, trade_dir)

        score = 5
        if in_fvg:                             score += 1
        if fvg.get('displacement'):            score += 2
        if (t2 - entry if trade_dir == 'BUY' else entry - t2) / risk >= 3.0:
            score += 1
        if fvg.get('size', 0) / fvg_mid > 0.0008:
            score += 1

        results.append({
            'strategy'        : 'SILVER_BULLET',
            'direction'       : trade_dir,
            'date'            : str(ts)[:10],
            'time'            : str(ts)[11:16],
            'hour'            : ts.hour,
            'entry'           : entry,
            'stop_loss'       : stop_loss,
            'risk_pts'        : risk,
            'target1'         : t1,
            'target2'         : t2,
            'target3'         : t3,
            'score'           : score,
            'fvg_displacement': fvg.get('displacement', False),
            **outcome,
        })

    return results


# â”€â”€â”€ Stats â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _stats(results: list) -> dict:
    total = len(results)
    if total == 0:
        return {}
    wins   = sum(1 for r in results if r['is_win'])
    losses = total - wins
    r_vals = [r['r_multiple'] for r in results]
    avg_r  = round(sum(r_vals) / total, 2)
    total_r = round(sum(r_vals), 2)
    win_r  = round(sum(r for r in r_vals if r > 0) / max(wins,   1), 2)
    loss_r = round(sum(r for r in r_vals if r <= 0) / max(losses, 1), 2)

    hour_stats: dict = {}
    for r in results:
        h = r['hour']
        if h not in hour_stats: hour_stats[h] = {'w': 0, 'l': 0}
        hour_stats[h]['w' if r['is_win'] else 'l'] += 1
    best_hours = sorted(
        [h for h, s in hour_stats.items() if s['w'] + s['l'] >= 2],
        key=lambda h: hour_stats[h]['w'] / (hour_stats[h]['w'] + hour_stats[h]['l']),
        reverse=True,
    )[:3]

    monthly: dict = {}
    for r in results:
        m = r['date'][:7]
        if m not in monthly: monthly[m] = {'w': 0, 'l': 0}
        monthly[m]['w' if r['is_win'] else 'l'] += 1

    disp    = [r for r in results if r.get('fvg_displacement')]
    disp_wr = round(sum(1 for r in disp if r['is_win']) / max(len(disp), 1) * 100, 1)

    score7  = [r for r in results if r.get('score', 0) >= 7]
    s7_wr   = round(sum(1 for r in score7 if r['is_win']) / max(len(score7), 1) * 100, 1)

    running = 0.0; peak = 0.0; max_dd = 0.0
    for v in r_vals:
        running += v
        if running > peak: peak = running
        dd = peak - running
        if dd > max_dd: max_dd = dd

    return dict(
        total=total, wins=wins, losses=losses,
        win_rate=round(wins / total * 100, 1),
        avg_r=avg_r, total_r=total_r, win_r=win_r, loss_r=loss_r,
        best_hours=best_hours, monthly=monthly,
        disp_wr=disp_wr, disp_cnt=len(disp),
        score7_wr=s7_wr, score7_cnt=len(score7),
        max_dd=round(max_dd, 2),
    )


# â”€â”€â”€ Terminal report â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _print_report(results: list, days: int):
    s    = _stats(results)
    line = 'â”€' * 54
    print(f"\n{line}")
    print(f"  SILVER BULLET  |  NIFTY  |  {days}d  (~3 months)")
    print(line)
    if not s:
        print("  No setups found.")
        return
    print(f"  Setups       : {s['total']:>5}")
    print(f"  Wins/Losses  : {s['wins']:>5} / {s['losses']}")
    print(f"  Win Rate     : {s['win_rate']:>5}%")
    print(f"  Avg R/trade  : {s['avg_r']:>5}R")
    print(f"  Total R      : {s['total_r']:>5}R")
    print(f"  Avg Win R    : {s['win_r']:>5}R")
    print(f"  Avg Loss R   : {s['loss_r']:>5}R")
    print(f"  Max Drawdown : {s['max_dd']:>5}R")
    if s['disp_cnt']:
        print(f"  Displaced FVG: {s['disp_wr']:>5}%  WR  ({s['disp_cnt']} setups)")
    if s['score7_cnt']:
        print(f"  Score 7+ WR  : {s['score7_wr']:>5}%  ({s['score7_cnt']} setups)")
    print(f"  Best Hours   : {s['best_hours']}")
    print(f"\n  Monthly:")
    print(f"  {'Month':<10}  {'W':>4}  {'L':>4}  {'WR%':>5}  Bar")
    for m in sorted(s['monthly']):
        ms  = s['monthly'][m]; mt = ms['w'] + ms['l']
        bar = 'â–ˆ' * ms['w'] + 'â–‘' * ms['l']
        print(f"  {m:<10}  {ms['w']:>4}  {ms['l']:>4}  {round(ms['w']/mt*100):>4}%  {bar}")


# â”€â”€â”€ CSV â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _save_csv(results: list, filename: str):
    if not results:
        return
    os.makedirs('data', exist_ok=True)
    path = os.path.join('data', filename)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"  Saved â†’ {path}")


# â”€â”€â”€ Entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

if __name__ == '__main__':
    SYMBOL = 'NSE:NIFTY50-INDEX'
    DAYS   = 100   # Fyers intraday API hard cap â€” requesting more returns same 90-100 days

    print('=' * 54)
    print('  CB6 QUANTUM â€” SILVER BULLET BACKTEST')
    print(f'  Symbol : {SYMBOL}')
    print(f'  Period : {DAYS} days  (~3 months, Fyers intraday limit)')
    print('=' * 54)

    fyers      = _get_fyers()
    sb_results = run_sb_backtest(fyers, SYMBOL, DAYS)

    _print_report(sb_results, DAYS)

    print('\nSaving...')
    _save_csv(sb_results, 'backtest_silver_bullet_nifty.csv')
    print('Done.')

