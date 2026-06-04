# ml/backtest_3m.py
#
# CB6 Quantum — ICT Silver Bullet 3-Minute Backtest Engine
#
# Walk-forward simulation of the full ICT chain on historical 3m NIFTY data.
# No look-ahead bias — each bar only sees candles up to that point.
#
# Steps:
#   1. Fetch 3m data from Fyers (needs live token)
#   2. Filter to requested date range; keep 7 days pre-history for lookback
#   3. Scan every bar from 10:00 IST (skip Judas Swing)
#   4. When DOL→MSS→FVG chain fires, simulate trade outcome bar-by-bar
#   5. Jump past exit + cooldown → next trade
#
# H1/H4 bias: RANGING in backtest (fyers=None skips live API calls).
# Opening range (9:15–10:00) is always in the 150-bar rolling window.

from __future__ import annotations

import os
from typing import Dict, List, Optional

import pandas as pd

from utils.logger import logger

# ── Config ─────────────────────────────────────────────────────────────────────
WINDOW_BARS  = 150    # 150 × 3 min = 7.5 h lookback — opening range always included
COOLDOWN     = 10     # bars after trade close before next scan (~30 min)
MAX_HOLD     = 100    # max bars to hold a trade (~5 h) before force-exit
EOD_HOUR     = 15     # force-close any open trade at this hour (3 PM IST)
EOD_MIN      = 20    # force-close at 15:20 IST
SCAN_START_H = 10     # start scanning at 10:00 IST (skip 9:15 Judas Swing)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve_symbol(index_name: str) -> Optional[str]:
    """Index name → active Fyers futures symbol."""
    try:
        from scanner.index_futures import get_active_futures
        return get_active_futures().get(index_name.upper())
    except Exception as e:
        logger.error(f"Backtest3m: symbol resolve error: {e}")
        return None


def _load_3m_data(fyers, symbol: str, days: int = 20) -> Optional[pd.DataFrame]:
    """Fetch 3-minute NIFTY historical data from Fyers."""
    try:
        from scanner.data_fetcher import get_historical_data
        df = get_historical_data(fyers, symbol, '3', days=days)
        if df is None or len(df) < 100:
            logger.error(f"Backtest3m: insufficient 3m data ({len(df) if df is not None else 0} bars)")
            return None
        df = df.copy()
        df['ts'] = pd.to_datetime(df['timestamp'])
        logger.info(f"Backtest3m: loaded {len(df)} candles of 3m data for {symbol}")
        return df
    except Exception as e:
        logger.error(f"Backtest3m: data load error: {e}")
        return None


def _simulate_outcome(df_all: pd.DataFrame, entry_i: int,
                       entry: float, sl: float, t1: float, t2: float, t3: float,
                       risk: float, direction: str) -> Dict:
    """
    Walk forward from entry_i+1, check each bar's H/L against SL/T1/T2/T3.
    Returns outcome dict: {outcome, exit_price, r, exit_i}.
    """
    outcome    = 'TIMEOUT'
    exit_price = None
    r_actual   = 0.0
    exit_i     = min(entry_i + MAX_HOLD, len(df_all) - 1)

    for j in range(entry_i + 1, min(entry_i + MAX_HOLD, len(df_all))):
        row  = df_all.iloc[j]
        h    = float(row['high'])
        l    = float(row['low'])
        ts_j = row['ts']

        # Force close at EOD
        if ts_j.hour >= EOD_HOUR and ts_j.minute >= EOD_MIN:
            exit_price = float(row['close'])
            r_actual   = round(
                (exit_price - entry) / risk if direction == 'BULLISH'
                else (entry - exit_price) / risk, 2
            )
            outcome = 'EOD'
            exit_i  = j
            break

        if direction == 'BULLISH':
            if l <= sl:
                outcome = 'SL';  exit_price = sl;  r_actual = -1.0;                          exit_i = j; break
            if h >= t3:
                outcome = 'T3';  exit_price = t3;  r_actual = round((t3 - entry) / risk, 2); exit_i = j; break
            if h >= t2:
                outcome = 'T2';  exit_price = t2;  r_actual = round((t2 - entry) / risk, 2); exit_i = j; break
            if h >= t1:
                outcome = 'T1';  exit_price = t1;  r_actual = round((t1 - entry) / risk, 2); exit_i = j; break
        else:   # BEARISH
            if h >= sl:
                outcome = 'SL';  exit_price = sl;  r_actual = -1.0;                           exit_i = j; break
            if l <= t3:
                outcome = 'T3';  exit_price = t3;  r_actual = round((entry - t3) / risk, 2);  exit_i = j; break
            if l <= t2:
                outcome = 'T2';  exit_price = t2;  r_actual = round((entry - t2) / risk, 2);  exit_i = j; break
            if l <= t1:
                outcome = 'T1';  exit_price = t1;  r_actual = round((entry - t1) / risk, 2);  exit_i = j; break

    if outcome == 'TIMEOUT' or exit_price is None:
        exit_price = float(df_all.iloc[exit_i]['close'])
        r_actual   = round(
            (exit_price - entry) / risk if direction == 'BULLISH'
            else (entry - exit_price) / risk, 2
        )

    return {'outcome': outcome, 'exit_price': round(exit_price, 1),
            'r': r_actual, 'exit_i': exit_i}


# ── Main backtest ───────────────────────────────────────────────────────────────

def run_backtest_3m(fyers,
                    index_name : str = 'NIFTY',
                    from_date  : str = '2026-05-18',
                    to_date    : str = '2026-05-25') -> Dict:
    """
    Walk-forward backtest of the ICT Silver Bullet chain on 3-minute NIFTY data.

    Args:
        fyers      : authenticated Fyers session (required for data fetch)
        index_name : 'NIFTY' | 'BANKNIFTY' | 'FINNIFTY' | 'MIDCPNIFTY'
        from_date  : 'YYYY-MM-DD' — first day to scan
        to_date    : 'YYYY-MM-DD' — last day to scan (inclusive)

    Returns:
        {index, tf, from, to, total_trades, wins, losses, win_rate,
         total_r, avg_win_r, avg_loss_r, profit_factor, trades[]}
    """
    symbol = _resolve_symbol(index_name)
    if not symbol:
        return {'error': f'No futures symbol found for {index_name}'}

    # ── Load data ──────────────────────────────────────────────────────────────
    df_raw = _load_3m_data(fyers, symbol, days=20)
    if df_raw is None:
        return {'error': 'Failed to fetch 3m data — check Fyers token'}

    # ── Filter ─────────────────────────────────────────────────────────────────
    t_from        = pd.Timestamp(from_date + ' 09:00:00')
    t_to          = pd.Timestamp(to_date   + ' 15:30:00')
    history_start = pd.Timestamp(from_date + ' 00:00:00') - pd.Timedelta(days=7)

    df_all = df_raw[df_raw['ts'] >= history_start].reset_index(drop=True)
    if len(df_all) < WINDOW_BARS + 20:
        return {'error': f'Not enough data ({len(df_all)} bars) for backtest'}

    # Identify backtest range indices
    bt_mask = (df_all['ts'] >= t_from) & (df_all['ts'] <= t_to)
    bt_idx  = df_all.index[bt_mask].tolist()
    if not bt_idx:
        return {'error': f'No candles in {from_date} – {to_date}'}

    logger.info(f"Backtest3m: {len(bt_idx)} bars in scan window ({from_date} → {to_date})")

    # ── Walk-forward simulation ────────────────────────────────────────────────
    from scanner.silver_bullet import scan_silver_bullet

    trades: List[Dict] = []
    i         = bt_idx[0]
    end_i     = bt_idx[-1]
    skip_to   = 0       # jump past exit + cooldown

    while i <= end_i:
        if i < skip_to:
            i += 1
            continue

        ts_now = df_all['ts'].iloc[i]

        # Skip outside scan hours (before 10:00 AM or at/after 3:00 PM)
        if ts_now.hour < SCAN_START_H or ts_now.hour >= EOD_HOUR:
            i += 1
            continue

        # ── Build rolling window ───────────────────────────────────────────────
        win_start = max(0, i - WINDOW_BARS)
        window    = df_all.iloc[win_start:i + 1][
            ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        ].copy()

        # ── Run ICT chain ──────────────────────────────────────────────────────
        # fyers=None → H1/H4 bias = RANGING (no live API in backtest)
        # force=True → skips Silver Bullet window time gate
        try:
            setup = scan_silver_bullet(window, symbol, tf='3',
                                       fyers=None, force=True)
        except Exception as e:
            logger.debug(f"Backtest3m bar {i} scan error: {e}")
            i += 1
            continue

        if setup is None:
            i += 1
            continue

        # ── Setup found ────────────────────────────────────────────────────────
        sig       = setup['entry_signal']
        entry     = sig['entry']
        sl        = sig['stop_loss']
        t1, t2, t3 = sig['target1'], sig['target2'], sig['target3']
        risk      = sig['risk']
        direction = setup['direction']

        if risk <= 0:
            i += 1
            continue

        logger.info(
            f"Backtest3m: setup @ {ts_now.strftime('%b%d %H:%M')}  "
            f"{direction}  E={entry:.1f}  SL={sl:.1f}  T3={t3:.1f}  R={risk:.1f}pts"
        )

        # ── Simulate outcome ───────────────────────────────────────────────────
        result = _simulate_outcome(
            df_all, i, entry, sl, t1, t2, t3, risk, direction
        )

        exit_ts = df_all['ts'].iloc[result['exit_i']].strftime('%H:%M')

        trades.append({
            'date'      : ts_now.strftime('%b %d'),
            'time'      : ts_now.strftime('%H:%M'),
            'exit_time' : exit_ts,
            'dir'       : '↑' if direction == 'BULLISH' else '↓',
            'dir_full'  : direction,
            'entry'     : round(entry, 1),
            'sl'        : round(sl, 1),
            't1'        : round(t1, 1),
            't2'        : round(t2, 1),
            't3'        : round(t3, 1),
            'risk_pts'  : round(risk, 1),
            'outcome'   : result['outcome'],
            'exit'      : result['exit_price'],
            'r'         : result['r'],
            'score'     : setup.get('confluence', 0),
            'mss'       : setup.get('mss_type', '?'),
            'regime'    : setup.get('regime', '?'),
            'fvg_sz'    : round(setup.get('fvg', {}).get('size', 0), 1),
        })

        logger.info(
            f"Backtest3m: result → {result['outcome']}  "
            f"{result['r']:+.2f}R @ {result['exit_price']}"
        )

        # Jump past exit + cooldown
        skip_to = result['exit_i'] + COOLDOWN
        i = result['exit_i'] + 1

    # ── Summary statistics ─────────────────────────────────────────────────────
    if not trades:
        return {
            'index'       : index_name, 'tf': '3m',
            'from'        : from_date,  'to': to_date,
            'total_trades': 0, 'trades': [],
        }

    wins    = [t for t in trades if t['r'] > 0]
    losses  = [t for t in trades if t['r'] <= 0]
    total_r = round(sum(t['r'] for t in trades), 2)
    wr_pct  = round(len(wins) / len(trades) * 100, 1)
    avg_w   = round(sum(t['r'] for t in wins)   / len(wins),   2) if wins   else 0.0
    avg_l   = round(sum(t['r'] for t in losses) / len(losses), 2) if losses else 0.0
    tot_win_r  = sum(t['r'] for t in wins)
    tot_loss_r = abs(sum(t['r'] for t in losses))
    pf         = round(tot_win_r / tot_loss_r, 2) if tot_loss_r > 0 else float('inf')

    t3_n = sum(1 for t in trades if t['outcome'] == 'T3')
    t2_n = sum(1 for t in trades if t['outcome'] == 'T2')
    t1_n = sum(1 for t in trades if t['outcome'] == 'T1')
    sl_n = sum(1 for t in trades if t['outcome'] == 'SL')

    # By-direction breakdown
    longs  = [t for t in trades if t['dir_full'] == 'BULLISH']
    shorts = [t for t in trades if t['dir_full'] == 'BEARISH']
    long_wr  = round(len([t for t in longs  if t['r'] > 0]) / len(longs)  * 100, 1) if longs  else 0
    short_wr = round(len([t for t in shorts if t['r'] > 0]) / len(shorts) * 100, 1) if shorts else 0

    return {
        'index'        : index_name,
        'tf'           : '3m',
        'from'         : from_date,
        'to'           : to_date,
        'total_trades' : len(trades),
        'wins'         : len(wins),
        'losses'       : len(losses),
        'win_rate'     : wr_pct,
        'total_r'      : total_r,
        'avg_win_r'    : avg_w,
        'avg_loss_r'   : avg_l,
        'profit_factor': pf,
        't3_count'     : t3_n,
        't2_count'     : t2_n,
        't1_count'     : t1_n,
        'sl_count'     : sl_n,
        'longs'        : len(longs),
        'shorts'       : len(shorts),
        'long_wr'      : long_wr,
        'short_wr'     : short_wr,
        'trades'       : trades,
    }


# ── Telegram formatter ─────────────────────────────────────────────────────────

def format_backtest_message(result: Dict) -> str:
    """Format backtest results as a clean Telegram HTML message."""

    if 'error' in result:
        return f"❌ Backtest 3m failed:\n{result['error']}"

    idx    = result['index']
    fr     = result['from']
    to     = result['to']
    total  = result['total_trades']

    if total == 0:
        return (
            f"📊 <b>BACKTEST 3m — {idx}</b>\n"
            f"{fr} → {to}\n\n"
            "❌ No setups found in this period.\n\n"
            "Possible reasons:\n"
            "• 3m FVG displacement threshold not met (body &lt; 70%)\n"
            "• Opening range sweep check failed\n"
            "• CHOPPY regime blocked all setups\n"
            "• H1 bias counter-trend (RANGING in BT mode, shouldn't block)\n\n"
            "<i>Try /backtest3m NIFTY with a longer range.</i>"
        )

    wr     = result['win_rate']
    tr     = result['total_r']
    pf     = result['profit_factor']
    avgw   = result['avg_win_r']
    avgl   = result['avg_loss_r']
    wins   = result['wins']
    losses = result['losses']
    t3c    = result['t3_count']
    t2c    = result['t2_count']
    t1c    = result['t1_count']
    slc    = result['sl_count']
    longs  = result['longs']
    shorts = result['shorts']
    lwp    = result['long_wr']
    swp    = result['short_wr']

    grade = (
        '🔥 EXCELLENT' if wr >= 60 and tr > 0 else
        '✅ GOOD'      if wr >= 50 and tr > 0 else
        '⚠️ MARGINAL'  if wr >= 40             else
        '❌ AVOID'
    )

    pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"
    tr_str = f"{tr:+.2f}R"

    lines = [
        f"📊 <b>BACKTEST — {idx} 3m TF</b>",
        f"Period : {fr}  →  {to}",
        f"Scan   : 10:00–15:20 IST  |  All days",
        "",
        "━━━ SUMMARY ━━━",
        f"Trades     : <b>{total}</b>  ({wins}W / {losses}L)",
        f"Win Rate   : <b>{wr}%</b>  {grade}",
        f"Total R    : <b>{tr_str}</b>",
        f"Profit Factor: {pf_str}",
        f"Avg Win    : +{avgw}R   |   Avg Loss : {avgl}R",
        "",
        "━━━ TARGETS ━━━",
        f"T3 hits : {t3c}   T2 hits : {t2c}   T1 hits : {t1c}",
        f"SL hits : {slc}   Other   : {total - t1c - t2c - t3c - slc}",
        "",
        "━━━ DIRECTION ━━━",
        f"LONG  ({longs} trades) WR: {lwp}%",
        f"SHORT ({shorts} trades) WR: {swp}%",
        "",
        "━━━ TRADE LOG ━━━",
    ]

    for t in result['trades']:
        icon = '✅' if t['r'] > 0 else ('❌' if t['r'] < 0 else '➡️')
        lines.append(
            f"{icon} <b>{t['date']} {t['time']}</b> {t['dir']}  "
            f"E:{t['entry']} SL:{t['sl']}  "
            f"<b>{t['outcome']}</b> {t['r']:+.2f}R  "
            f"[Score:{t['score']} {t['mss']} {t['regime']}]"
        )

    lines += [
        "",
        "<i>Note: H1/H4 bias = RANGING (backtest mode, no live API)</i>",
        "<i>3m ICT chain: DOL → MSS → Displaced FVG → FVG touch</i>",
        "<i>Targets scale with regime + DTE at scan time (today's expiry used)</i>",
    ]

    return '\n'.join(lines)
