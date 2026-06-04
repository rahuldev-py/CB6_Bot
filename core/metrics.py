# core/metrics.py — Pure trade math. Zero I/O. Zero globals.
# Used by dashboard, telegram /stats, journal, backtest. Single source of truth.
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Any


def r_multiple(trade: Dict[str, Any]) -> float:
    """R = realized P&L / total risk. Returns 0 if risk unknown."""
    risk_per = trade.get('risk', 0) or 0
    qty = trade.get('original_quantity', trade.get('quantity', 0)) or 0
    total_risk = risk_per * qty
    if total_risk <= 0:
        return 0.0
    return round(trade.get('pnl', 0) / total_risk, 2)


def calc_metrics(closed: List[Dict], capital: float = 200000) -> Dict[str, Any]:
    """Return all advanced performance metrics for a list of closed trades."""
    n = len(closed)
    out = {
        'count'         : n,
        'wins'          : 0,
        'losses'        : 0,
        'breakevens'    : 0,
        'win_rate'      : 0.0,
        'gross_pnl'     : 0.0,
        'max_dd_rs'     : 0.0,
        'max_dd_pct'    : 0.0,
        'profit_factor' : 0.0,
        'avg_r'         : 0.0,
        'expectancy'    : 0.0,
        'max_consec_w'  : 0,
        'max_consec_l'  : 0,
        'avg_win'       : 0.0,
        'avg_loss'      : 0.0,
        'largest_win'   : 0.0,
        'largest_loss'  : 0.0,
        'sharpe'        : 0.0,
        'r_values'      : [],
    }
    if n == 0:
        return out

    wins = [t.get('pnl', 0) for t in closed if t.get('pnl', 0) > 0]
    losses = [abs(t.get('pnl', 0)) for t in closed if t.get('pnl', 0) < 0]
    out['wins']       = len(wins)
    out['losses']     = len(losses)
    out['breakevens'] = sum(1 for t in closed if t.get('pnl', 0) == 0)
    _wr_denom         = len(wins) + len(losses)   # exclude breakevens
    out['win_rate']   = round(len(wins) / _wr_denom * 100, 1) if _wr_denom > 0 else 0
    out['gross_pnl']  = round(sum(t.get('pnl', 0) for t in closed), 2)

    # Max drawdown
    cum = peak = max_dd = 0
    for t in closed:
        cum += t.get('pnl', 0)
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    out['max_dd_rs']  = round(max_dd, 2)
    out['max_dd_pct'] = round(max_dd / capital * 100, 2) if capital else 0

    # Profit factor + win/loss avgs
    gw = sum(wins)
    gl = sum(losses)
    out['profit_factor'] = round(gw / gl, 2) if gl > 0 else (gw if gw > 0 else 0)
    out['avg_win']       = round(gw / len(wins), 2) if wins else 0
    out['avg_loss']      = round(gl / len(losses), 2) if losses else 0
    out['largest_win']   = round(max(wins), 2) if wins else 0
    out['largest_loss']  = round(max(losses), 2) if losses else 0

    # R-multiples
    rs = [r_multiple(t) for t in closed]
    rs = [r for r in rs if r != 0]    # filter trades with no risk data
    out['r_values'] = rs
    out['avg_r']    = round(sum(rs) / len(rs), 2) if rs else 0

    # Expectancy = WR×avgWin − LR×avgLoss  (use breakeven-excluded denominator)
    wr = len(wins) / _wr_denom if _wr_denom > 0 else 0
    out['expectancy'] = round((wr * out['avg_win']) - ((1 - wr) * out['avg_loss']), 2)

    # Max consecutive W/L
    cw = cl = mcw = mcl = 0
    for t in closed:
        if t.get('pnl', 0) > 0:
            cw += 1; cl = 0; mcw = max(mcw, cw)
        elif t.get('pnl', 0) < 0:
            cl += 1; cw = 0; mcl = max(mcl, cl)
    out['max_consec_w'] = mcw
    out['max_consec_l'] = mcl

    # Per-trade Sharpe (no annualization — comparable across periods, not absolute)
    if n >= 2:
        pnls = [t.get('pnl', 0) for t in closed]
        m = sum(pnls) / n
        var = sum((p - m) ** 2 for p in pnls) / n
        sd = var ** 0.5
        out['sharpe'] = round(m / sd, 2) if sd > 0 else 0

    return out


def calc_drawdown_series(closed: List[Dict]) -> Tuple[List[float], List[float]]:
    """Returns (cumulative equity curve, drawdown-from-peak series). Both prefixed with 0."""
    cum = peak = 0
    eq, dd = [0], [0]
    for t in closed:
        cum += t.get('pnl', 0)
        peak = max(peak, cum)
        eq.append(round(cum, 2))
        dd.append(round(peak - cum, 2))
    return eq, dd


def calc_daily_pnl(closed: List[Dict], days: int = 30) -> List[Tuple[str, float]]:
    """P&L bucketed by exit date — last N days. Returns sorted [(date, pnl), ...]."""
    bucket = defaultdict(float)
    for t in closed:
        exit_t = t.get('exit_time', '')
        if not exit_t:
            continue
        bucket[exit_t[:10]] += t['pnl']
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for d in sorted(bucket.keys()):
        try:
            if datetime.strptime(d, '%Y-%m-%d') >= cutoff:
                out.append((d, round(bucket[d], 2)))
        except Exception:
            continue
    return out


def calc_symbol_breakdown(closed: List[Dict], top_n: int = 5
                          ) -> Tuple[List[Tuple[str, Dict]], List[Tuple[str, Dict]]]:
    """Return (best_n, worst_n) symbols by total P&L. Each entry is (symbol, stats)."""
    bucket = defaultdict(lambda: {'pnl': 0, 'w': 0, 'l': 0, 'n': 0})
    for t in closed:
        sym = t['symbol'].replace('NSE:', '').replace('-EQ', '')
        bucket[sym]['pnl'] += t['pnl']
        bucket[sym]['n']   += 1
        if t['pnl'] > 0:   bucket[sym]['w'] += 1
        elif t['pnl'] < 0: bucket[sym]['l'] += 1
    rows = sorted(bucket.items(), key=lambda x: x[1]['pnl'], reverse=True)
    best  = rows[:top_n]
    worst = rows[-top_n:][::-1] if len(rows) >= top_n else rows[::-1]
    return best, worst


def r_histogram(r_values: List[float]) -> Tuple[List[str], List[int]]:
    """Bucket R-multiples into named bins for histogram display."""
    if not r_values:
        return [], []
    bins = [-3, -2, -1, 0, 1, 2, 3, 4, 5]
    labels = ['<-3R', '-3R..-2R', '-2R..-1R', '-1R..0R', '0..1R',
              '1R..2R', '2R..3R', '3R..4R', '4R..5R', '>5R']
    counts = [0] * len(labels)
    for r in r_values:
        placed = False
        for i, b in enumerate(bins):
            if r < b:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    return labels, counts
