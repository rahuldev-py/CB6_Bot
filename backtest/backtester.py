# backtest/backtester.py â€” CB6 QUANTUM ICT Strategy Backtester
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger


def simulate_trade_outcome(df, setup_idx, entry, stop_loss, t1, t2, t3, direction='BUY'):
    """
    Walk forward from setup_idx to see which level price hits first.
    Returns dict with result, exit_price, and targets_hit list.
    """
    targets_hit = []
    current_sl  = stop_loss
    result      = 'TIMEOUT'
    exit_price  = df['close'].iloc[-1]

    # Partial booking simulation: SL trails after T1/T2
    for i in range(setup_idx + 1, len(df)):
        high = df['high'].iloc[i]
        low  = df['low'].iloc[i]

        if direction == 'BUY':
            if low <= current_sl:
                result     = 'SL_HIT'
                exit_price = current_sl
                break
            if 'T1' not in targets_hit and high >= t1:
                targets_hit.append('T1')
                current_sl = entry              # trail to break-even
            if 'T2' not in targets_hit and high >= t2:
                targets_hit.append('T2')
                current_sl = round(t1 + (t2 - t1) * 0.5, 2)
            if high >= t3:
                targets_hit.append('T3')
                result     = 'TARGET_HIT'
                exit_price = t3
                break
        else:  # SELL
            if high >= current_sl:
                result     = 'SL_HIT'
                exit_price = current_sl
                break
            if 'T1' not in targets_hit and low <= t1:
                targets_hit.append('T1')
                current_sl = entry
            if 'T2' not in targets_hit and low <= t2:
                targets_hit.append('T2')
                current_sl = round(t1 - (t1 - t2) * 0.5, 2)
            if low <= t3:
                targets_hit.append('T3')
                result     = 'TARGET_HIT'
                exit_price = t3
                break

    # Compute P&L based on targets hit + final exit
    pnl_pts = 0
    orig_qty_weight = 1.0
    remaining = orig_qty_weight
    if 'T1' in targets_hit:
        pnl_pts   += 0.33 * (t1 - entry if direction == 'BUY' else entry - t1)
        remaining -= 0.33
    if 'T2' in targets_hit:
        pnl_pts   += 0.33 * (t2 - entry if direction == 'BUY' else entry - t2)
        remaining -= 0.33
    final_move = exit_price - entry if direction == 'BUY' else entry - exit_price
    pnl_pts   += remaining * final_move

    risk = abs(entry - stop_loss)
    r_multiple = round(pnl_pts / risk, 2) if risk > 0 else 0

    return {
        'result'      : result,
        'exit_price'  : exit_price,
        'targets_hit' : targets_hit,
        'pnl_pts'     : round(pnl_pts, 2),
        'r_multiple'  : r_multiple,
        'is_win'      : pnl_pts > 0
    }


def run_backtest(fyers, symbol, timeframe='15', days=90):
    """
    Run ICT strategy backtest on `days` of historical data.
    Returns a stats dict with win rate, avg R, breakdown by hour.
    """
    try:
        from scanner.data_fetcher  import get_historical_data
        from scanner.silver_bullet import scan_silver_bullet

        df = get_historical_data(fyers, symbol, timeframe, days=days)
        if df is None or len(df) < 100:
            return None

        results  = []
        min_window = 60
        step       = 5   # check every 5 candles for performance

        for end_idx in range(min_window, len(df) - 10, step):
            window = df.iloc[:end_idx].copy()

            setup = scan_silver_bullet(window, symbol, tf=timeframe, fyers=fyers)
            if not setup:
                continue

            sig  = setup.get('entry_signal', {})
            if not sig:
                continue

            entry = sig.get('entry')
            sl    = sig.get('stop_loss')
            t1    = sig.get('target1')
            t2    = sig.get('target2')
            t3    = sig.get('target3')
            if None in (entry, sl, t1, t2, t3):
                continue

            is_bull = setup['direction'] == 'BULLISH'
            direction_label = 'BUY' if is_bull else 'SELL'
            risk = (entry - sl) if is_bull else (sl - entry)
            rr_check = (t1 - entry) / risk if is_bull else (entry - t1) / risk

            if risk > 0 and rr_check >= 1.5:
                outcome = simulate_trade_outcome(
                    df, end_idx, entry, sl, t1, t2, t3, direction_label
                )
                results.append({
                    'direction': direction_label,
                    'symbol'   : symbol,
                    'timeframe': timeframe,
                    'score'    : setup.get('confluence', 0),
                    'hour'     : df['timestamp'].iloc[end_idx].hour,
                    'date'     : str(df['timestamp'].iloc[end_idx])[:10],
                    **outcome
                })

        if not results:
            return {'symbol': symbol, 'timeframe': timeframe, 'total': 0}

        total   = len(results)
        wins    = sum(1 for r in results if r['is_win'])
        losses  = total - wins
        win_rate = round(wins / total * 100, 1)
        avg_r   = round(sum(r['r_multiple'] for r in results) / total, 2)
        total_r = round(sum(r['r_multiple'] for r in results), 2)

        # Breakdown by hour
        hour_stats = {}
        for r in results:
            h = r['hour']
            if h not in hour_stats:
                hour_stats[h] = {'w': 0, 'l': 0}
            if r['is_win']:
                hour_stats[h]['w'] += 1
            else:
                hour_stats[h]['l'] += 1

        best_hours = sorted(
            [h for h, s in hour_stats.items() if s['w'] + s['l'] >= 2],
            key=lambda h: hour_stats[h]['w'] / max(hour_stats[h]['w'] + hour_stats[h]['l'], 1),
            reverse=True
        )[:3]

        # Best scoring setups
        high_score = [r for r in results if r['score'] >= 7]
        hs_wins    = sum(1 for r in high_score if r['is_win'])
        hs_wr      = round(hs_wins / max(len(high_score), 1) * 100, 1)

        return {
            'symbol'    : symbol,
            'timeframe' : timeframe,
            'days'      : days,
            'total'     : total,
            'wins'      : wins,
            'losses'    : losses,
            'win_rate'  : win_rate,
            'avg_r'     : avg_r,
            'total_r'   : total_r,
            'best_hours': best_hours,
            'score7_wr' : hs_wr,
            'score7_cnt': len(high_score),
            'results'   : results
        }

    except Exception as e:
        logger.error(f"Backtest error {symbol}: {e}")
        return None


def format_backtest_report(stats):
    """Format backtest results as a Telegram message."""
    if not stats or stats.get('total', 0) == 0:
        return "No valid setups found in backtest period."

    lines = [
        f"CB6 QUANTUM - BACKTEST RESULTS\n",
        f"Symbol    : {stats['symbol']}",
        f"Timeframe : {stats['timeframe']}min",
        f"Period    : Last {stats['days']} days\n",
        f"Total Setups : {stats['total']}",
        f"Wins         : {stats['wins']}",
        f"Losses       : {stats['losses']}",
        f"Win Rate     : {stats['win_rate']}%",
        f"Avg R        : {stats['avg_r']}R",
        f"Total R      : {stats['total_r']}R\n",
        f"Score 7+ WR  : {stats['score7_wr']}% ({stats['score7_cnt']} setups)",
        f"Best Hours   : {stats['best_hours']}\n",
        "Score 7+ setups perform better â€” use as filter!"
    ]
    return "\n".join(lines)

