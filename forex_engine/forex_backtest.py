# forex_engine/forex_backtest.py
#
# CB6 Quantum Forex Backtester
# Fetches historical OHLCV via yfinance, runs the same ICT scanner,
# simulates trade outcomes with partial booking (T1/T2/T3).
#
# Usage:
#   python -m forex_engine.forex_backtest
#   python -m forex_engine.forex_backtest --symbol XAUUSD --days 90

import os
import sys
import argparse
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import uuid
import pandas as pd

from utils.logger import logger
from forex_engine.forex_instruments import INSTRUMENTS, FTMO_RULES, calc_lot_size, dollar_risk
from forex_engine.mt5_adapter import MT5Adapter
from forex_engine.forex_worker import scan_forex_setup, _in_kill_zone, _is_prime_kz, SYMBOL_MIN_SCORE
from forex_engine.forex_trade_journal import build_record, save_records, print_analysis

ACCOUNT_SIZE     = 10000.0   # FTMO $10K
MODE             = 'free_trial'  # 'free_trial' or 'challenge'
RULES            = FTMO_RULES[MODE]
RISK_PCT         = FTMO_RULES['risk_per_trade_pct']   # 0.5% = $50/trade
DAILY_LOSS_LIMIT = ACCOUNT_SIZE * RULES['max_daily_loss_pct'] / 100   # $300
TOTAL_DD_LIMIT   = ACCOUNT_SIZE * RULES['max_total_dd_pct'] / 100      # $1,000
PROFIT_TARGET    = ACCOUNT_SIZE * RULES['profit_target_pct'] / 100     # $500
BEST_DAY_LIMIT   = PROFIT_TARGET * RULES['best_day_rule_pct'] / 100    # $250
MIN_SCORE        = 11   # matches live bot — score 7 was pre-filter era
INTERVAL         = '15m'


def simulate_outcome(df: pd.DataFrame, setup_idx: int, sig: dict,
                     direction: str, lots: float, symbol: str) -> dict:
    """
    Walk-forward simulation from setup candle.
    Partial booking: 1/3 at T1, 1/3 at T2, 1/3 at T3.
    SL trails to breakeven after T1.
    """
    cfg           = INSTRUMENTS.get(symbol, {})
    contract_size = cfg.get('contract_size', 100000)

    entry   = sig['entry']
    sl      = sig['stop_loss']
    current_sl = sl
    t1, t2, t3 = sig['target1'], sig['target2'], sig['target3']

    targets_hit = []
    result      = 'TIMEOUT'
    exit_price  = df['close'].iloc[-1]
    partial_pnl = 0.0

    def _pnl_full(ep):
        dist = (ep - entry) if direction == 'BULLISH' else (entry - ep)
        return round(lots * contract_size * dist, 2)

    for i in range(setup_idx + 1, len(df)):
        high = float(df['high'].iloc[i])
        low  = float(df['low'].iloc[i])

        if direction == 'BULLISH':
            if low <= current_sl:
                result     = 'SL_HIT'
                exit_price = current_sl
                break
            if 'T1' not in targets_hit and high >= t1:
                targets_hit.append('T1')
                partial_pnl += round(_pnl_full(t1) / 3, 2)
                current_sl   = entry            # trail to breakeven
            if 'T2' not in targets_hit and high >= t2:
                targets_hit.append('T2')
                partial_pnl += round(_pnl_full(t2) / 3, 2)
                current_sl   = entry            # trail to breakeven (same as T1)
            if high >= t3:
                targets_hit.append('T3')
                result     = 'TARGET_HIT'
                exit_price = t3
                break
        else:
            if high >= current_sl:
                result     = 'SL_HIT'
                exit_price = current_sl
                break
            if 'T1' not in targets_hit and low <= t1:
                targets_hit.append('T1')
                partial_pnl += round(_pnl_full(t1) / 3, 2)
                current_sl   = entry
            if 'T2' not in targets_hit and low <= t2:
                targets_hit.append('T2')
                partial_pnl += round(_pnl_full(t2) / 3, 2)
                current_sl   = entry            # trail to breakeven (same as T1)
            if low <= t3:
                targets_hit.append('T3')
                result     = 'TARGET_HIT'
                exit_price = t3
                break

    if result == 'TARGET_HIT':
        final_pnl = partial_pnl + round(_pnl_full(exit_price) / 3, 2)
    elif result == 'SL_HIT':
        remaining = 3 - len(targets_hit)
        final_pnl = partial_pnl + round(_pnl_full(exit_price) * remaining / 3, 2)
    else:
        final_pnl = partial_pnl + round(_pnl_full(exit_price) / 3, 2)

    return {
        'result'     : result,
        'targets_hit': targets_hit,
        'exit_price' : exit_price,
        'pnl_usd'   : final_pnl,
        'lots'       : lots,
    }


def run_backtest(symbol: str, days: int = 60) -> dict:
    logger.info(f"\n{'='*55}")
    logger.info(f"CB6 Quantum Forex Backtest — {symbol} | {days}d | {INTERVAL}")
    logger.info(f"Account: ${ACCOUNT_SIZE} | Risk: {RISK_PCT}% | Min score: {MIN_SCORE}")
    logger.info(f"Hard filters: KZ-only | sweep_confirmed | in_fvg | H4+H1 bias")
    logger.info('='*55)

    adapter = MT5Adapter(paper=True)
    df_full = adapter.get_klines(symbol, INTERVAL, min(days * 96, 5000))  # 96 x 15m = 1 day

    if df_full is None or df_full.empty:
        logger.error(f"No data for {symbol} — check yfinance ticker in forex_instruments.py")
        return {}

    logger.info(f"Loaded {len(df_full)} candles for {symbol}")

    # H1 data for HTF bias filter (same as live bot)
    df_h1 = adapter.get_klines(symbol, '1h', min(days * 24 + 50, 720))
    if df_h1 is not None and not df_h1.empty:
        logger.info(f"Loaded {len(df_h1)} H1 candles for H1 bias filter")
    else:
        logger.warning(f"No H1 data for {symbol} — H1 bias filter disabled for this run")

    # H4 data for trend bias filter (primary trend filter — H1 alone flips in chop)
    df_h4 = adapter.get_klines(symbol, '4h', min(days * 6 + 20, 200))
    if df_h4 is not None and not df_h4.empty:
        logger.info(f"Loaded {len(df_h4)} H4 candles for H4 bias filter")
    else:
        logger.warning(f"No H4 data for {symbol} — H4 bias filter disabled for this run")
        df_h4 = None

    trades             = []
    journal_records    = []
    capital            = ACCOUNT_SIZE
    peak               = ACCOUNT_SIZE
    daily_pnl          = 0.0
    best_day_pnl       = 0.0
    last_date          = ''
    account_breached   = False
    WINDOW             = 150
    COOLDOWN           = 20
    cooldown_remaining = 0
    i                  = WINDOW

    while i < len(df_full):
        if account_breached:
            break

        # Daily reset
        try:
            candle_dt  = df_full.index[i]
            candle_date = str(candle_dt)[:10]
            if candle_date != last_date:
                daily_pnl    = 0.0
                best_day_pnl = 0.0
                last_date    = candle_date
        except Exception:
            pass

        # FTMO daily loss gate: $300/day
        if daily_pnl <= -DAILY_LOSS_LIMIT:
            i += 1
            continue

        # FTMO best day gate: $250/day max profit (free trial)
        if best_day_pnl >= BEST_DAY_LIMIT:
            i += 1
            continue

        # FTMO total DD gate: $1,000
        if (ACCOUNT_SIZE - capital) >= TOTAL_DD_LIMIT:
            logger.info(f"FTMO total DD breached at candle {i} — simulation ends")
            account_breached = True
            break

        # Profit target reached — challenge passed
        if (capital - ACCOUNT_SIZE) >= PROFIT_TARGET:
            logger.info(f"PROFIT TARGET HIT: ${capital - ACCOUNT_SIZE:.2f} — challenge passed!")
            break

        # Kill zone — HARD BLOCK outside 07-12 UTC (London) and 16-20 UTC (NY)
        try:
            utc_hour = candle_dt.hour if hasattr(candle_dt, 'hour') else 0
            _in_kz   = _in_kill_zone(utc_hour)
        except Exception:
            utc_hour = 0
            _in_kz   = False

        if not _in_kz:
            i += 1
            continue   # hard block — no trades outside kill zones

        if cooldown_remaining > 0:
            cooldown_remaining -= 1
            i += 1
            continue

        df_window = df_full.iloc[i - WINDOW: i].copy()
        setup = scan_forex_setup(df_window, symbol)

        if not setup:
            i += 1
            continue

        # HARD BLOCK 1: Sweep required — same direction sweep in last 15 candles
        liq_sweep = setup.get('liq_sweep')
        sweep_ok  = (
            liq_sweep is not None
            and liq_sweep.get('candles_ago', 999) <= 15
            and liq_sweep.get('direction') == setup['direction']
            and liq_sweep.get('level_state') == 'SWEPT'
            and int(liq_sweep.get('confidence', 0) or 0) >= 45
        )
        if not sweep_ok:
            i += 1
            continue

        # HARD BLOCK 2: Price must be inside the FVG for entry
        if not setup.get('in_fvg'):
            i += 1
            continue

        # Score gate — eff_score (confluence + 1 if CHoCH) must meet symbol minimum
        sym_min   = SYMBOL_MIN_SCORE.get(symbol, MIN_SCORE)
        mss_type  = setup.get('mss_type', 'BOS')
        eff_score = setup['confluence'] + (1 if mss_type == 'CHOCH' else 0)
        min_score_now = sym_min
        if not _is_prime_kz(utc_hour):
            min_score_now += 1    # off-peak KZ: slight raise
        if eff_score < min_score_now:
            i += 1
            continue

        # H4 bias filter — PRIMARY trend filter (H1 flips in chop; H4 filters noise)
        h4_bias = 'RANGING'
        if df_h4 is not None and not df_h4.empty:
            try:
                h4_slice = df_h4[df_h4.index <= candle_dt].tail(20)
                if len(h4_slice) >= 10:
                    c    = h4_slice['close']
                    fast = c.ewm(span=3, adjust=False).mean().iloc[-1]
                    slow = c.ewm(span=8, adjust=False).mean().iloc[-1]
                    if fast > slow * 1.0003:
                        h4_bias = 'BULLISH'
                    elif fast < slow * 0.9997:
                        h4_bias = 'BEARISH'
            except Exception:
                pass
        h4_ranging = (h4_bias == 'RANGING')
        if not h4_ranging and h4_bias != setup['direction']:
            i += 1
            continue   # counter-trend on H4 — hard block
        if h4_ranging:
            min_score_now += 1   # RANGING needs A+ quality
        if eff_score < min_score_now:
            i += 1
            continue

        # H1 bias filter — confirms entry-level trend alignment
        h1_bias = 'RANGING'
        if df_h1 is not None and not df_h1.empty:
            try:
                h1_slice = df_h1[df_h1.index <= candle_dt].tail(20)
                if len(h1_slice) >= 10:
                    c    = h1_slice['close']
                    fast = c.ewm(span=3, adjust=False).mean().iloc[-1]
                    slow = c.ewm(span=8, adjust=False).mean().iloc[-1]
                    if fast > slow * 1.0002:
                        h1_bias = 'BULLISH'
                    elif fast < slow * 0.9998:
                        h1_bias = 'BEARISH'
            except Exception:
                pass
        h1_ranging = (h1_bias == 'RANGING')
        if not h1_ranging and h1_bias != setup['direction']:
            i += 1
            continue   # counter-trend on H1 — blocked
        if h1_ranging:
            min_score_now += 1
        if eff_score < min_score_now:
            i += 1
            continue

        sig     = setup['entry_signal']
        # Fixed base capital for lot sizing — FTMO rule: don't compound during challenge
        lots    = calc_lot_size(symbol, ACCOUNT_SIZE, sig['entry'], sig['stop_loss'], RISK_PCT)
        risk_usd = dollar_risk(symbol, lots, sig['entry'], sig['stop_loss'])

        cfg     = INSTRUMENTS.get(symbol, {})
        min_lot = cfg.get('min_lot', 0.01)
        if lots < min_lot:
            i += 1
            continue

        # Simulate from candle i onward — returns exit candle offset
        outcome = simulate_outcome(df_full, i, sig, setup['direction'], lots, symbol)
        pnl     = outcome['pnl_usd']
        daily_pnl_before = daily_pnl
        capital          += pnl
        daily_pnl        += pnl
        best_day_pnl      = max(best_day_pnl, daily_pnl)
        peak              = max(peak, capital)

        candle_time = df_full.index[i]
        trades.append({
            'time'    : str(candle_time)[:16],
            'symbol'  : symbol,
            'direction': setup['direction'],
            'score'   : setup['confluence'],
            'mss_type': setup.get('mss_type', 'BOS'),
            'entry'   : sig['entry'],
            'sl'      : sig['stop_loss'],
            'targets' : outcome['targets_hit'],
            'result'  : outcome['result'],
            'lots'    : lots,
            'risk_usd': risk_usd,
            'pnl_usd' : pnl,
            'capital' : round(capital, 2),
        })

        # Build and store full journal record
        df_context = df_full.iloc[max(0, i-4): i+1]
        rec = build_record(
            trade_id         = str(uuid.uuid4())[:8],
            symbol           = symbol,
            setup            = setup,
            outcome          = outcome,
            lots             = lots,
            risk_usd         = risk_usd,
            capital_after    = capital,
            daily_pnl_before = daily_pnl_before,
            daily_pnl_after  = daily_pnl,
            df_context       = df_context,
            mode             = MODE,
        )
        journal_records.append(rec)

        win_emoji = '✅' if pnl > 0 else '❌'
        logger.info(
            f"{win_emoji} {str(candle_time)[:16]} | {setup['direction'][:4]} "
            f"score={setup['confluence']} | {outcome['result']} "
            f"targets={outcome['targets_hit']} | PnL ${pnl:+.2f} | Capital ${capital:.2f}"
        )

        cooldown_remaining = COOLDOWN
        i += 1

    # ── Summary ────────────────────────────────────────────────────────────────
    if not trades:
        logger.info(f"No trades found for {symbol} in {days}d window")
        return {}

    # Save journal records for this symbol
    if journal_records:
        save_records(journal_records)
        logger.info(f"Saved {len(journal_records)} records to forex_journal")

    wins   = [t for t in trades if t['pnl_usd'] > 0]
    losses = [t for t in trades if t['pnl_usd'] <= 0]
    total  = len(trades)
    wr     = round(len(wins) / total * 100, 1)
    net    = round(sum(t['pnl_usd'] for t in trades), 2)
    avg_w  = round(sum(t['pnl_usd'] for t in wins) / len(wins), 2) if wins else 0
    avg_l  = round(sum(t['pnl_usd'] for t in losses) / len(losses), 2) if losses else 0
    max_dd = round((peak - min(t['capital'] for t in trades)) / peak * 100, 2)
    growth = round((capital - ACCOUNT_SIZE) / ACCOUNT_SIZE * 100, 2)

    logger.info(f"\n{'='*55}")
    logger.info(f"BACKTEST RESULTS — {symbol} ({days}d)")
    logger.info(f"{'='*55}")
    logger.info(f"Total trades : {total}  (W:{len(wins)} L:{len(losses)})")
    logger.info(f"Win rate     : {wr}%")
    logger.info(f"Net PnL      : ${net:+.2f}")
    logger.info(f"Growth       : {growth:+.2f}%")
    logger.info(f"Avg Win      : ${avg_w:.2f}")
    logger.info(f"Avg Loss     : ${avg_l:.2f}")
    logger.info(f"Max Drawdown : {max_dd}%  (FTMO limit: 10%)")
    logger.info(f"Final Capital: ${capital:.2f}")
    logger.info(f"Profit Target: ${PROFIT_TARGET:.0f}  ({'HIT ✅' if (capital-ACCOUNT_SIZE) >= PROFIT_TARGET else 'NOT YET'})")
    logger.info(f"Daily Loss   : $300 limit  |  Risk/trade: ${ACCOUNT_SIZE*RISK_PCT/100:.0f}")
    logger.info(f"Best Day Rule: $250 max/day")
    logger.info(f"FTMO Status  : {'BREACHED ❌' if account_breached else 'SAFE ✅' if max_dd <= 10 else 'BREACHED ❌'}")
    logger.info('='*55)

    return {
        'symbol'     : symbol,
        'days'       : days,
        'total'      : total,
        'wins'       : len(wins),
        'losses'     : len(losses),
        'win_rate'   : wr,
        'net_pnl'    : net,
        'growth_pct' : growth,
        'avg_win'    : avg_w,
        'avg_loss'   : avg_l,
        'max_dd_pct' : max_dd,
        'final_cap'  : round(capital, 2),
        'trades'         : trades,
        'journal_records': journal_records,
    }


def run_commodities(days: int = 30):
    """Run backtest for Gold, Silver, Oil only."""
    COMMODITY_SYMBOLS = ['XAUUSD', 'XAGUSD', 'USOIL']
    all_results  = {}
    all_records  = []

    # Clear old journal before fresh run
    from forex_engine.forex_trade_journal import JOURNAL_CSV, JOURNAL_JSON
    for f in [JOURNAL_CSV, JOURNAL_JSON]:
        if os.path.exists(f):
            os.remove(f)

    for sym in COMMODITY_SYMBOLS:
        result = run_backtest(sym, days)
        if result:
            all_results[sym] = result
            all_records.extend(result.get('journal_records', []))

    if not all_results:
        return

    print(f"\n{'='*65}")
    print(f"CB6 QUANTUM — GOLD | SILVER | OIL BACKTEST ({days}d)")
    print(f"Mode: {MODE} | Risk: ${ACCOUNT_SIZE*RISK_PCT/100}/trade | Daily limit: ${DAILY_LOSS_LIMIT}")
    print(f"{'='*65}")
    print(f"{'Symbol':<10} {'Trades':>6} {'WR%':>6} {'Net PnL':>10} {'Growth':>8} {'MaxDD':>7} {'Status':>10}")
    print('-'*65)
    for sym, r in all_results.items():
        status = 'PASSED ✅' if r.get('net_pnl', 0) >= PROFIT_TARGET else 'pending'
        print(
            f"{sym:<10} {r['total']:>6} {r['win_rate']:>5.1f}% "
            f"${r['net_pnl']:>+8.2f} {r['growth_pct']:>+7.2f}% "
            f"{r['max_dd_pct']:>6.2f}% {status:>10}"
        )
    print('='*65)

    # Combined pattern analysis across all 3 symbols
    if all_records:
        print_analysis(all_records)


def run_all(days: int = 30):
    """Run backtest across all active symbols."""
    from forex_engine.forex_worker import ACTIVE_SYMBOLS
    all_results = {}
    all_records = []
    for sym in ACTIVE_SYMBOLS:
        result = run_backtest(sym, days)
        if result:
            all_results[sym] = result
            all_records.extend(result.get('journal_records', []))

    if not all_results:
        return

    print(f"\n{'='*65}")
    print(f"CB6 QUANTUM — COMBINED FOREX BACKTEST SUMMARY ({days}d)")
    print(f"{'='*65}")
    print(f"{'Symbol':<12} {'Trades':>6} {'WR%':>6} {'Net PnL':>10} {'Growth%':>9} {'MaxDD%':>8}")
    print('-'*65)
    for sym, r in all_results.items():
        print(
            f"{sym:<12} {r['total']:>6} {r['win_rate']:>5.1f}% "
            f"${r['net_pnl']:>+9.2f} {r['growth_pct']:>+8.2f}% {r['max_dd_pct']:>7.2f}%"
        )
    print('='*65)
    if all_records:
        print_analysis(all_records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CB6 Quantum Forex Backtest')
    parser.add_argument('--symbol', default='ALL',
                        help='Symbol to backtest (XAUUSD, EURUSD, etc.) or ALL')
    parser.add_argument('--days', type=int, default=30,
                        help='Lookback period in calendar days')
    args = parser.parse_args()

    sym = args.symbol.upper()
    if sym == 'ALL':
        run_all(args.days)
    elif sym == 'COMMODITIES':
        run_commodities(args.days)
    else:
        run_backtest(sym, args.days)
