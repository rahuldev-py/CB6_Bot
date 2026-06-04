# forex_engine/forex_trade_journal.py
#
# CB6 Quantum — Forex Trade Journal
# Captures every trade in full detail for future market analysis.
# Saved as: data/forex_journal.csv  (human readable)
#           data/forex_journal.json (machine readable)
#
# Each record stores:
#   - Symbol, direction, session, time
#   - Full market structure: DOL, MSS, FVG, OB, UT Bot, 3BR
#   - Entry candle OHLCV + 3 candles before (context)
#   - Trade plan: entry, SL, T1/T2/T3, lots, margin, risk
#   - Result: targets hit, exit price, PnL, R-multiple

import csv
import json
import os
from datetime import datetime
from typing import Optional

JOURNAL_CSV  = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'forex_journal.csv')
JOURNAL_JSON = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'forex_journal.json')

CSV_FIELDS = [
    # Identity
    'id', 'symbol', 'label', 'date', 'entry_time', 'exit_time', 'session',
    # Direction & Structure
    'direction', 'mss_type', 'score',
    'dol_direction', 'dol_level',
    'mss_level',
    'fvg_low', 'fvg_high', 'fvg_size', 'fvg_displacement', 'price_in_fvg',
    'ob_present', 'ob_type', 'ob_low', 'ob_high', 'ob_confluence',
    'three_bar_reversal',
    'ut_bot_trend', 'ut_bot_aligned',
    # Entry candle OHLCV
    'ec_open', 'ec_high', 'ec_low', 'ec_close', 'ec_volume',
    # Pre-entry context (candle -1, -2, -3)
    'c1_open', 'c1_high', 'c1_low', 'c1_close', 'c1_volume',
    'c2_open', 'c2_high', 'c2_low', 'c2_close', 'c2_volume',
    'c3_open', 'c3_high', 'c3_low', 'c3_close', 'c3_volume',
    # Trade plan
    'entry', 'stop_loss', 'target1', 'target2', 'target3',
    'risk_price', 'rr_ratio',
    # Position sizing
    'lots', 'contract_size', 'risk_usd', 'margin_usd', 'leverage',
    # Result
    'result', 'targets_hit', 'exit_price', 'pnl_usd', 'r_multiple', 'win',
    'capital_after',
    # FTMO context
    'daily_pnl_before', 'daily_pnl_after', 'mode',
]


def _get_session(utc_hour: int) -> str:
    if 7 <= utc_hour < 13:
        return 'London'
    if 13 <= utc_hour < 16:
        return 'London_NY_Overlap'
    if 16 <= utc_hour < 21:
        return 'NY'
    if 0 <= utc_hour < 7:
        return 'Asia'
    return 'OffHours'


def build_record(
    trade_id     : str,
    symbol       : str,
    setup        : dict,
    outcome      : dict,
    lots         : float,
    risk_usd     : float,
    capital_after: float,
    daily_pnl_before: float,
    daily_pnl_after : float,
    df_context   : object,   # DataFrame slice (last 4 candles before entry)
    mode         : str = 'free_trial',
) -> dict:
    """Build a complete journal record from a backtest trade."""
    from forex_engine.forex_instruments import INSTRUMENTS

    sig       = setup['entry_signal']
    direction = setup['direction']
    fvg       = setup.get('fvg', {})
    dol       = setup.get('dol', {})
    mss       = setup.get('mss', {})
    ob        = setup.get('ob')
    ut        = setup.get('ut_bot', {})
    cfg       = INSTRUMENTS.get(symbol, {})

    # Timing
    now         = datetime.now()
    entry_time  = str(df_context.index[-1])[:16] if df_context is not None else ''
    utc_hour    = df_context.index[-1].hour if df_context is not None and hasattr(df_context.index[-1], 'hour') else 0
    date_str    = str(df_context.index[-1])[:10] if df_context is not None else ''

    # Entry candle
    def _row(df, idx):
        if df is None or len(df) <= abs(idx):
            return {}
        r = df.iloc[idx]
        return {
            'open': round(float(r['open']), 5),
            'high': round(float(r['high']), 5),
            'low' : round(float(r['low']), 5),
            'close': round(float(r['close']), 5),
            'volume': int(float(r['volume'])),
        }

    ec = _row(df_context, -1)   # entry candle
    c1 = _row(df_context, -2)   # 1 candle before
    c2 = _row(df_context, -3)   # 2 candles before
    c3 = _row(df_context, -4)   # 3 candles before

    # Risk calculations
    contract_size = cfg.get('contract_size', 100)
    margin_usd    = round(lots * contract_size * sig['entry'] / 100, 2)
    r_multiple    = round(outcome['pnl_usd'] / risk_usd, 2) if risk_usd > 0 else 0

    record = {
        # Identity
        'id'             : trade_id,
        'symbol'         : symbol,
        'label'          : cfg.get('label', symbol),
        'date'           : date_str,
        'entry_time'     : entry_time,
        'exit_time'      : outcome.get('exit_time', ''),
        'session'        : _get_session(utc_hour),
        # Direction & Structure
        'direction'      : direction,
        'mss_type'       : setup.get('mss_type', 'BOS'),
        'score'          : setup['confluence'],
        'dol_direction'  : dol.get('direction', ''),
        'dol_level'      : round(dol.get('level', 0), 5),
        'mss_level'      : round(mss.get('level', 0), 5),
        'fvg_low'        : round(fvg.get('fvg_low', sig.get('fvg_low', 0)), 5),
        'fvg_high'       : round(fvg.get('fvg_high', sig.get('fvg_high', 0)), 5),
        'fvg_size'       : round(fvg.get('size', 0), 5),
        'fvg_displacement': bool(fvg.get('displacement', False)),
        'price_in_fvg'   : bool(setup.get('in_fvg', False)),
        'ob_present'     : ob is not None,
        'ob_type'        : ob.get('type', '') if ob else '',
        'ob_low'         : round(ob.get('ob_low', 0), 5) if ob else 0,
        'ob_high'        : round(ob.get('ob_high', 0), 5) if ob else 0,
        'ob_confluence'  : bool(setup.get('ob_confluence', False)),
        'three_bar_reversal': bool(setup.get('three_bar', False)),
        'ut_bot_trend'   : ut.get('trend', ''),
        'ut_bot_aligned' : bool(ut.get('aligned', False)),
        # Entry candle OHLCV
        'ec_open'   : ec.get('open', 0),
        'ec_high'   : ec.get('high', 0),
        'ec_low'    : ec.get('low', 0),
        'ec_close'  : ec.get('close', 0),
        'ec_volume' : ec.get('volume', 0),
        # Context candles
        'c1_open': c1.get('open', 0), 'c1_high': c1.get('high', 0),
        'c1_low' : c1.get('low', 0),  'c1_close': c1.get('close', 0),
        'c1_volume': c1.get('volume', 0),
        'c2_open': c2.get('open', 0), 'c2_high': c2.get('high', 0),
        'c2_low' : c2.get('low', 0),  'c2_close': c2.get('close', 0),
        'c2_volume': c2.get('volume', 0),
        'c3_open': c3.get('open', 0), 'c3_high': c3.get('high', 0),
        'c3_low' : c3.get('low', 0),  'c3_close': c3.get('close', 0),
        'c3_volume': c3.get('volume', 0),
        # Trade plan
        'entry'    : sig['entry'],
        'stop_loss': sig['stop_loss'],
        'target1'  : sig['target1'],
        'target2'  : sig['target2'],
        'target3'  : sig['target3'],
        'risk_price': round(sig['risk'], 5),
        'rr_ratio' : sig['rr_ratio'],
        # Position sizing
        'lots'         : lots,
        'contract_size': contract_size,
        'risk_usd'     : risk_usd,
        'margin_usd'   : margin_usd,
        'leverage'     : 100,
        # Result
        'result'      : outcome['result'],
        'targets_hit' : ','.join(outcome['targets_hit']),
        'exit_price'  : outcome['exit_price'],
        'pnl_usd'     : round(outcome['pnl_usd'], 2),
        'r_multiple'  : r_multiple,
        'win'         : outcome['pnl_usd'] > 0,
        'capital_after': round(capital_after, 2),
        # FTMO
        'daily_pnl_before': round(daily_pnl_before, 2),
        'daily_pnl_after' : round(daily_pnl_after, 2),
        'mode'            : mode,
    }
    return record


def save_records(records: list):
    """Append records to CSV and rewrite JSON."""
    os.makedirs(os.path.dirname(JOURNAL_CSV), exist_ok=True)

    # CSV — append
    write_header = not os.path.exists(JOURNAL_CSV)
    with open(JOURNAL_CSV, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction='ignore')
        if write_header:
            writer.writeheader()
        writer.writerows(records)

    # JSON — load existing + append + rewrite
    existing = []
    if os.path.exists(JOURNAL_JSON):
        try:
            with open(JOURNAL_JSON, 'r') as f:
                existing = json.load(f)
        except Exception:
            existing = []
    existing.extend(records)
    with open(JOURNAL_JSON, 'w') as f:
        json.dump(existing, f, indent=2, default=str)


def print_analysis(records: list):
    """Print pattern analysis from a list of journal records."""
    if not records:
        print("No records to analyse.")
        return

    symbols   = list({r['symbol'] for r in records})
    wins      = [r for r in records if r['win']]
    losses    = [r for r in records if not r['win']]
    total     = len(records)
    wr        = round(len(wins) / total * 100, 1)
    net       = round(sum(r['pnl_usd'] for r in records), 2)
    avg_r     = round(sum(r['r_multiple'] for r in records) / total, 2)

    print(f"\n{'='*60}")
    print(f"CB6 QUANTUM — TRADE JOURNAL ANALYSIS")
    print(f"{'='*60}")
    print(f"Symbols   : {', '.join(symbols)}")
    print(f"Total     : {total} trades  (W:{len(wins)} L:{len(losses)})")
    print(f"Win Rate  : {wr}%")
    print(f"Net PnL   : ${net:+.2f}")
    print(f"Avg R     : {avg_r}R per trade")

    print(f"\n--- BY SYMBOL ---")
    for sym in symbols:
        s_recs = [r for r in records if r['symbol'] == sym]
        s_wins = [r for r in s_recs if r['win']]
        s_wr   = round(len(s_wins) / len(s_recs) * 100, 1) if s_recs else 0
        s_net  = round(sum(r['pnl_usd'] for r in s_recs), 2)
        print(f"  {sym:<8}: {len(s_recs)} trades | WR {s_wr}% | Net ${s_net:+.2f}")

    print(f"\n--- BY SETUP TYPE ---")
    for mss in ['CHOCH', 'BOS']:
        m_recs = [r for r in records if r['mss_type'] == mss]
        if not m_recs:
            continue
        m_wins = [r for r in m_recs if r['win']]
        m_wr   = round(len(m_wins) / len(m_recs) * 100, 1)
        m_net  = round(sum(r['pnl_usd'] for r in m_recs), 2)
        print(f"  {mss:<8}: {len(m_recs)} trades | WR {m_wr}% | Net ${m_net:+.2f}")

    print(f"\n--- BY SESSION ---")
    for sess in ['London', 'London_NY_Overlap', 'NY']:
        s_recs = [r for r in records if r['session'] == sess]
        if not s_recs:
            continue
        s_wins = [r for r in s_recs if r['win']]
        s_wr   = round(len(s_wins) / len(s_recs) * 100, 1)
        s_net  = round(sum(r['pnl_usd'] for r in s_recs), 2)
        print(f"  {sess:<22}: {len(s_recs)} trades | WR {s_wr}% | Net ${s_net:+.2f}")

    print(f"\n--- BY SCORE ---")
    for score in sorted({r['score'] for r in records}):
        s_recs = [r for r in records if r['score'] == score]
        s_wins = [r for r in s_recs if r['win']]
        s_wr   = round(len(s_wins) / len(s_recs) * 100, 1)
        print(f"  Score {score}/12 : {len(s_recs)} trades | WR {s_wr}%")

    print(f"\n--- TARGET BREAKDOWN ---")
    for tgt in ['T1', 'T2', 'T3']:
        hit = [r for r in records if tgt in r['targets_hit']]
        print(f"  {tgt} hit: {len(hit)}/{total} ({round(len(hit)/total*100,1)}%)")

    print(f"\n--- BEST TRADES ---")
    top3 = sorted(records, key=lambda r: r['pnl_usd'], reverse=True)[:3]
    for r in top3:
        print(f"  {r['symbol']} {r['date']} {r['direction'][:4]} "
              f"score={r['score']} | +${r['pnl_usd']} | {r['targets_hit']}")

    print(f"\n--- WORST TRADES ---")
    bot3 = sorted(records, key=lambda r: r['pnl_usd'])[:3]
    for r in bot3:
        print(f"  {r['symbol']} {r['date']} {r['direction'][:4]} "
              f"score={r['score']} | ${r['pnl_usd']} | SL hit")
    print('='*60)
