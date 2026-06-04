# forex_engine/reports/performance_report.py
# Performance metrics computed from journal records or live state.

import json
import os
from typing import Optional

from forex_engine.prop_firms.ftmo.ftmo_state import compute_best_day_stats  # noqa: F401

_JOURNAL_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'forex_journal.json'
)


def load_journal(journal_path: str = None) -> list:
    path = journal_path or _JOURNAL_JSON
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def summary(records: list) -> dict:
    if not records:
        return {}

    total  = len(records)
    wins   = [r for r in records if r.get('win')]
    losses = [r for r in records if not r.get('win')]
    pnls   = [r.get('pnl_usd', 0.0) for r in records]
    rs     = [r.get('r_multiple', 0.0) for r in records]

    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss   = abs(sum(p for p in pnls if p < 0))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')

    win_rs  = [r for r in rs if r > 0]
    loss_rs = [r for r in rs if r < 0]
    avg_win_r  = round(sum(win_rs) / len(win_rs), 2) if win_rs else 0
    avg_loss_r = round(sum(loss_rs) / len(loss_rs), 2) if loss_rs else 0

    return {
        'total_trades'  : total,
        'wins'          : len(wins),
        'losses'        : len(losses),
        'win_rate_pct'  : round(len(wins) / total * 100, 1),
        'net_pnl'       : round(sum(pnls), 2),
        'gross_profit'  : round(gross_profit, 2),
        'gross_loss'    : round(gross_loss, 2),
        'profit_factor' : profit_factor,
        'avg_win_r'     : avg_win_r,
        'avg_loss_r'    : avg_loss_r,
        'avg_r_per_trade': round(sum(rs) / total, 2),
        'expectancy_r'  : round((len(wins)/total * avg_win_r) + (len(losses)/total * avg_loss_r), 3),
        'best_trade'    : max(pnls),
        'worst_trade'   : min(pnls),
    }


def by_symbol(records: list) -> dict:
    symbols = {r.get('symbol') for r in records}
    out = {}
    for sym in symbols:
        recs = [r for r in records if r.get('symbol') == sym]
        out[sym] = summary(recs)
    return out


def by_session(records: list) -> dict:
    sessions = {r.get('session') for r in records}
    out = {}
    for sess in sessions:
        recs = [r for r in records if r.get('session') == sess]
        out[sess] = summary(recs)
    return out


def by_direction(records: list) -> dict:
    out = {}
    for dire in ('BULLISH', 'BEARISH'):
        recs = [r for r in records if r.get('direction') == dire]
        if recs:
            out[dire] = summary(recs)
    return out


def target_breakdown(records: list) -> dict:
    total = len(records)
    if total == 0:
        return {}
    out = {}
    for tgt in ('T1', 'T2', 'T3'):
        hit = [r for r in records if tgt in str(r.get('targets_hit', ''))]
        out[tgt] = {'count': len(hit), 'pct': round(len(hit) / total * 100, 1)}
    return out


def drawdown_series(records: list) -> list:
    """Return equity curve as list of cumulative PnL values."""
    cumulative = 0.0
    curve = []
    for r in records:
        cumulative += r.get('pnl_usd', 0.0)
        curve.append(round(cumulative, 2))
    return curve


def max_drawdown(records: list) -> float:
    """Max drawdown from peak equity in dollar terms."""
    curve  = drawdown_series(records)
    peak   = float('-inf')
    max_dd = 0.0
    for val in curve:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def full_report(records: list = None, journal_path: str = None) -> dict:
    if records is None:
        records = load_journal(journal_path)
    return {
        'summary'          : summary(records),
        'by_symbol'        : by_symbol(records),
        'by_session'       : by_session(records),
        'by_direction'     : by_direction(records),
        'target_breakdown' : target_breakdown(records),
        'max_drawdown_usd' : max_drawdown(records),
    }


def print_report(records: list = None, journal_path: str = None):
    if records is None:
        records = load_journal(journal_path)
    r = full_report(records)
    s = r['summary']
    if not s:
        print("No records.")
        return

    print(f"\n{'='*60}")
    print("CB6 QUANTUM — PERFORMANCE REPORT")
    print(f"{'='*60}")
    print(f"Trades      : {s['total_trades']}  (W:{s['wins']} L:{s['losses']})")
    print(f"Win Rate    : {s['win_rate_pct']}%")
    print(f"Net PnL     : ${s['net_pnl']:+.2f}")
    print(f"Profit Factor: {s['profit_factor']}")
    print(f"Expectancy  : {s['expectancy_r']}R/trade")
    print(f"Max DD      : ${r['max_drawdown_usd']:.2f}")

    print("\n--- BY SYMBOL ---")
    for sym, ms in r['by_symbol'].items():
        print(f"  {sym:<8}: WR {ms['win_rate_pct']}% | Net ${ms['net_pnl']:+.2f} | PF {ms['profit_factor']}")

    print("\n--- BY SESSION ---")
    for sess, ms in r['by_session'].items():
        print(f"  {sess:<24}: WR {ms['win_rate_pct']}% | {ms['total_trades']} trades")

    print("\n--- TARGETS ---")
    for tgt, td in r['target_breakdown'].items():
        print(f"  {tgt}: {td['count']} hits ({td['pct']}%)")
    print('='*60)
