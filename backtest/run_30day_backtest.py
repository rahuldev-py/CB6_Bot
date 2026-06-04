# backtest/run_30day_backtest.py
#
# 30-day walk-forward backtest: NIFTY, BANKNIFTY, FINNIFTY
# Uses the live scanner (scan_silver_bullet) on real historical 5-min data.
# No lookahead — scanner only sees candles up to bar[i] at each step.
#
# Run: python backtest/run_30day_backtest.py

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import dotenv_values

env = dotenv_values(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
os.environ.setdefault('CLIENT_ID',    env.get('CLIENT_ID', ''))
os.environ.setdefault('ACCESS_TOKEN', env.get('ACCESS_TOKEN', ''))

from fyers_apiv3 import fyersModel
from scanner.data_fetcher import get_historical_data
from scanner.silver_bullet import scan_silver_bullet
from backtest.backtester    import simulate_trade_outcome

# ── Config ────────────────────────────────────────────────────────────────────
MARKETS = {
    'NIFTY'     : 'NSE:NIFTY50-INDEX',
    'BANKNIFTY' : 'NSE:NIFTYBANK-INDEX',
    'FINNIFTY'  : 'NSE:FINNIFTY-INDEX',
}
TIMEFRAME  = '5'        # 5-min candles
DAYS       = 30
MIN_CANDLES_FOR_SCAN = 60    # need enough history for DOL/MSS detection
COOLDOWN_BARS = 6            # skip next 6 bars after a setup fires (30 min)
TRADE_TIMEOUT_BARS = 78      # max 390 min (full session) to hold a trade

# ── Fyers session ─────────────────────────────────────────────────────────────
def _make_fyers():
    client_id    = os.getenv('CLIENT_ID', '')
    access_token = os.getenv('ACCESS_TOKEN', '')
    if not client_id or not access_token:
        print("ERROR: CLIENT_ID / ACCESS_TOKEN not set in .env")
        sys.exit(1)
    # Strip "CLIENT_ID:" prefix that web_token.py saves into .env
    token = access_token.replace(client_id + ':', '') if access_token.startswith(client_id) else access_token
    fyers = fyersModel.FyersModel(
        client_id=client_id,
        token=token,
        is_async=False,
        log_path=''
    )
    return fyers

# ── Walk-forward simulation ───────────────────────────────────────────────────
def backtest_symbol(fyers, name: str, symbol: str) -> list:
    print(f"\n{'='*60}")
    print(f"Fetching {name} ({symbol}) — {DAYS} days @ {TIMEFRAME}min ...")
    df = get_historical_data(fyers, symbol, TIMEFRAME, days=DAYS)
    if df is None or len(df) < MIN_CANDLES_FOR_SCAN + 10:
        print(f"  ERROR: insufficient data for {name}")
        return []

    print(f"  {len(df)} candles from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")

    trades  = []
    cooldown = 0

    for i in range(MIN_CANDLES_FOR_SCAN, len(df) - TRADE_TIMEOUT_BARS):
        if cooldown > 0:
            cooldown -= 1
            continue

        # Only scan during market hours (9:15–15:25 IST)
        ts   = df['timestamp'].iloc[i]
        hour = ts.hour
        mins = ts.minute
        t_min = hour * 60 + mins
        if t_min < 9 * 60 + 15 or t_min > 15 * 60 + 25:
            continue

        # Scanner sees only candles up to bar i (no lookahead)
        window = df.iloc[:i + 1].reset_index(drop=True)
        setup  = scan_silver_bullet(window, symbol, tf=TIMEFRAME, fyers=None, force=True)

        if setup is None:
            continue

        sig   = setup['entry_signal']
        entry = sig['entry']
        sl    = sig['stop_loss']
        t1    = sig['target1']
        t2    = sig['target2']
        t3    = sig['target3']
        score = setup['confluence']
        dirn  = 'BUY' if setup['direction'] == 'BULLISH' else 'SELL'
        risk  = sig['risk']

        # Walk forward from bar i+1 to simulate outcome
        future = df.iloc[i:].reset_index(drop=True)
        outcome = simulate_trade_outcome(
            future, 0, entry, sl, t1, t2, t3,
            direction=dirn
        )
        outcome['timeout_bars'] = TRADE_TIMEOUT_BARS

        trade = {
            'market'      : name,
            'symbol'      : symbol,
            'date'        : ts.strftime('%Y-%m-%d'),
            'time'        : ts.strftime('%H:%M'),
            'direction'   : dirn,
            'score'       : score,
            'mss_type'    : setup.get('mss_type', '?'),
            'in_fvg'      : setup.get('in_fvg', False),
            'ob_confluence': setup.get('ob_confluence', False),
            'double_ob'   : setup.get('double_ob_test', False),
            'three_bar'   : setup.get('three_bar', False),
            'regime'      : setup.get('regime', '?'),
            'entry'       : entry,
            'sl'          : sl,
            'risk_pts'    : risk,
            't1'          : t1, 't2': t2, 't3': t3,
            **outcome,
        }
        trades.append(trade)
        cooldown = COOLDOWN_BARS

        print(f"  {ts.strftime('%m-%d %H:%M')} | {dirn:4s} | score={score:2d} | "
              f"{outcome['result']:10s} | R={outcome['r_multiple']:+.2f} | "
              f"targets={outcome['targets_hit']}")

    return trades


# ── Report ────────────────────────────────────────────────────────────────────
def print_report(all_trades: list):
    if not all_trades:
        print("\nNo trades found in backtest period.")
        return

    def stats(subset, label):
        if not subset:
            return
        n     = len(subset)
        wins  = [t for t in subset if t['is_win']]
        losses= [t for t in subset if not t['is_win']]
        wr    = len(wins) / n * 100
        avg_r = sum(t['r_multiple'] for t in subset) / n
        avg_win_r  = sum(t['r_multiple'] for t in wins)  / len(wins)  if wins   else 0
        avg_los_r  = sum(t['r_multiple'] for t in losses)/ len(losses) if losses else 0
        t3_hits    = sum(1 for t in wins if 'T3' in t['targets_hit'])
        t2_hits    = sum(1 for t in subset if 'T2' in t['targets_hit'])
        t1_hits    = sum(1 for t in subset if 'T1' in t['targets_hit'])
        sl_hits    = sum(1 for t in subset if t['result'] == 'SL_HIT')
        total_r    = sum(t['r_multiple'] for t in subset)
        print(f"\n{label}")
        print(f"  Trades   : {n}  ({len(wins)}W / {len(losses)}L)  WR: {wr:.0f}%")
        print(f"  Avg R    : {avg_r:+.2f}R  |  Total R: {total_r:+.2f}R")
        print(f"  Win avg  : {avg_win_r:+.2f}R  |  Loss avg: {avg_los_r:.2f}R")
        print(f"  T1 hit   : {t1_hits} ({t1_hits/n*100:.0f}%)  "
              f"T2: {t2_hits} ({t2_hits/n*100:.0f}%)  "
              f"T3: {t3_hits} ({t3_hits/n*100:.0f}%)")
        print(f"  SL hits  : {sl_hits} ({sl_hits/n*100:.0f}%)")

    SEP = '=' * 65
    print(f"\n{SEP}")
    print("  CB6 QUANTUM — 30-DAY WALK-FORWARD BACKTEST REPORT")
    print(f"  Markets: NIFTY · BANKNIFTY · FINNIFTY   TF: 5-min")
    print(f"  Period : last 30 days   Scorer: silver_bullet v2 (score/15)")
    print(SEP)

    # ── Overall ──────────────────────────────────────────────────────────────
    stats(all_trades, "=== OVERALL ===")

    # ── Per market ───────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("=== BY MARKET ===")
    by_mkt = defaultdict(list)
    for t in all_trades:
        by_mkt[t['market']].append(t)
    for mkt, ts in sorted(by_mkt.items()):
        stats(ts, f"  {mkt}")

    # ── By direction ─────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("=== BY DIRECTION ===")
    for d in ['BUY', 'SELL']:
        sub = [t for t in all_trades if t['direction'] == d]
        stats(sub, f"  {d}")

    # ── By MSS type ───────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("=== BY MSS TYPE ===")
    for m in ['CHOCH', 'BOS']:
        sub = [t for t in all_trades if t['mss_type'] == m]
        stats(sub, f"  {m}")

    # ── By score ──────────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("=== BY SCORE THRESHOLD ===")
    for min_sc in [8, 9, 10, 11, 12]:
        sub = [t for t in all_trades if t['score'] >= min_sc]
        n   = len(sub)
        if not sub:
            continue
        wins = sum(1 for t in sub if t['is_win'])
        avg_r= sum(t['r_multiple'] for t in sub) / n
        print(f"  score>={min_sc}: {wins}/{n} = WR {wins/n*100:.0f}% | "
              f"avg R {avg_r:+.2f} | total R {sum(t['r_multiple'] for t in sub):+.2f}")

    # ── Confluence flags ───────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("=== CONFLUENCE FLAGS ===")
    for flag, label in [('ob_confluence','OB present'), ('double_ob','Double OB'),
                         ('three_bar','3-Bar Rev'), ('in_fvg','In FVG')]:
        y = [t for t in all_trades if t.get(flag)]
        n_y= len(y)
        if n_y == 0:
            continue
        wins_y = sum(1 for t in y if t['is_win'])
        avg_r_y= sum(t['r_multiple'] for t in y) / n_y
        print(f"  {label:15s}: {wins_y}/{n_y} = WR {wins_y/n_y*100:.0f}%  avg R {avg_r_y:+.2f}")

    # ── By regime ─────────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("=== BY MARKET REGIME ===")
    for r in ['TRENDING', 'NEUTRAL', 'CHOPPY']:
        sub = [t for t in all_trades if t['regime'] == r]
        if not sub:
            continue
        wins = sum(1 for t in sub if t['is_win'])
        avg_r= sum(t['r_multiple'] for t in sub) / len(sub)
        print(f"  {r:10s}: {wins}/{len(sub)} = WR {wins/len(sub)*100:.0f}%  avg R {avg_r:+.2f}")

    # ── Best / worst trades ────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("=== TOP 5 WINNERS ===")
    top5 = sorted(all_trades, key=lambda t: t['r_multiple'], reverse=True)[:5]
    for t in top5:
        print(f"  {t['date']} {t['time']} | {t['market']:10s} {t['direction']} "
              f"| score={t['score']} {t['mss_type']:5s} "
              f"| R={t['r_multiple']:+.2f} | {t['targets_hit']}")

    print(f"\n=== TOP 5 LOSERS ===")
    bot5 = sorted(all_trades, key=lambda t: t['r_multiple'])[:5]
    for t in bot5:
        print(f"  {t['date']} {t['time']} | {t['market']:10s} {t['direction']} "
              f"| score={t['score']} {t['mss_type']:5s} "
              f"| R={t['r_multiple']:+.2f} | result={t['result']}")

    print(f"\n{SEP}")

    # Save CSV
    import csv
    out_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'data', 'backtest_30day_report.csv')
    fields = ['market','date','time','direction','score','mss_type','regime',
              'ob_confluence','double_ob','three_bar','in_fvg',
              'entry','sl','risk_pts','t1','t2','t3',
              'result','exit_price','targets_hit','pnl_pts','r_multiple','is_win']
    with open(out_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(all_trades)
    print(f"  CSV saved → {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("CB6 QUANTUM — 30-Day Walk-Forward Backtest")
    print(f"Markets: {', '.join(MARKETS.keys())}  |  TF: {TIMEFRAME}min  |  Days: {DAYS}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    fyers = _make_fyers()
    all_trades = []

    for name, symbol in MARKETS.items():
        trades = backtest_symbol(fyers, name, symbol)
        all_trades.extend(trades)

    print_report(all_trades)
    print(f"\nFinished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
