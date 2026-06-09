# ml_engine/learning/feedback_loop.py
#
# Closed-trade learning loop — Hermes "skills self-improving during use" pattern.
#
# After every trade closes, this module:
#   1. Records the trade in trade_pattern_db (searchable history)
#   2. Recalculates feature correlations if ≥10 trades on this pattern
#   3. Emits scorer weight nudges to scanner/setup_scorer.py (if evidence is clear)
#
# SAFETY: never writes to state.json, never touches order placement code,
# never changes risk_pct beyond official limits. Read-only on all live state.

import os
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger('cb6.feedback_loop')

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Minimum trades before a pattern is eligible for weight adjustment
MIN_TRADES_FOR_NUDGE = 10

# Maximum single-nudge magnitude — prevents runaway drift
MAX_WEIGHT_NUDGE = 0.05

# WR delta threshold that must exist between two conditions before we act
WR_DELTA_THRESHOLD = 0.12  # 12 percentage points


def process_closed_trade(trade: dict, market: str, account: str,
                          session: str = '', h4_bias: str = '',
                          setup_type: str = 'DOL_SWEEP_OB_BOS_FVG',
                          fvg_body_pct: float = 0.0,
                          sweep_age_ca: int = 0,
                          notes: str = ''):
    """
    Entry point — called by the forex/NSE engine after any trade closes.
    Writes to pattern DB, then triggers weight analysis if enough data exists.
    """
    from ml_engine.memory.trade_pattern_db import record_trade, get_stats

    # Step 1: Write to searchable DB
    record_trade(
        trade=trade, market=market, account=account,
        session=session, h4_bias=h4_bias, setup_type=setup_type,
        fvg_body_pct=fvg_body_pct, sweep_age_ca=sweep_age_ca, notes=notes
    )
    logger.info(f"feedback_loop: trade {trade.get('id')} recorded in pattern DB")

    # Step 2: Pull stats for this symbol
    symbol = trade.get('symbol', '')
    if not symbol:
        return

    stats = get_stats(symbol=symbol)

    # Step 3: Find any patterns with enough data for weight analysis
    eligible = [s for s in stats if s['total'] >= MIN_TRADES_FOR_NUDGE]
    if not eligible:
        logger.debug(f"feedback_loop: {symbol} has <{MIN_TRADES_FOR_NUDGE} trades in any pattern — skipping weight analysis")
        return

    # Step 4: Analyse FVG body% split (the most impactful feature from backtest)
    _analyse_fvg_body_threshold(symbol, market)

    # Step 5: Analyse confluence score split
    _analyse_confluence_threshold(symbol, market)


def _analyse_fvg_body_threshold(symbol: str, market: str):
    """
    Splits trades by FVG body% < 0.45 vs >= 0.45 and checks if there's a
    statistically meaningful WR difference. If so, emits a nudge report.
    The threshold 0.45 comes from the May 2026 backtest (validated benchmark).
    """
    from ml_engine.memory.trade_pattern_db import query

    low  = query(symbol=symbol, fts_text='')  # all trades for symbol
    # filter manually since query() doesn't do BETWEEN
    all_trades = [t for t in low]

    low_body  = [t for t in all_trades if (t.get('fvg_body_pct') or 0) < 0.45]
    high_body = [t for t in all_trades if (t.get('fvg_body_pct') or 0) >= 0.45]

    if len(low_body) < 5 or len(high_body) < 5:
        return  # not enough data in both buckets

    wr_low  = _wr(low_body)
    wr_high = _wr(high_body)
    delta   = wr_high - wr_low

    if abs(delta) < WR_DELTA_THRESHOLD:
        logger.debug(f"feedback_loop [{symbol}] FVG body split: delta {delta:.1%} < threshold — no nudge")
        return

    direction = 'RAISE' if delta > 0 else 'LOWER'
    _emit_nudge_report(
        symbol=symbol, market=market,
        feature='MIN_FVG_BODY_PCT',
        current_val=0.45,
        direction=direction,
        evidence=(
            f"{len(low_body)} trades with body<45% → WR {wr_low:.1%} | "
            f"{len(high_body)} trades with body≥45% → WR {wr_high:.1%}"
        )
    )


def _analyse_confluence_threshold(symbol: str, market: str):
    """
    Splits trades by confluence < 10 vs >= 10. Checks if the gap is significant.
    """
    from ml_engine.memory.trade_pattern_db import query

    all_trades = query(symbol=symbol)

    low_conf  = [t for t in all_trades if (t.get('confluence') or 0) < 10]
    high_conf = [t for t in all_trades if (t.get('confluence') or 0) >= 10]

    if len(low_conf) < 5 or len(high_conf) < 5:
        return

    wr_low  = _wr(low_conf)
    wr_high = _wr(high_conf)
    delta   = wr_high - wr_low

    if abs(delta) < WR_DELTA_THRESHOLD:
        return

    direction = 'RAISE' if delta > 0 else 'LOWER'
    _emit_nudge_report(
        symbol=symbol, market=market,
        feature='MIN_CONFLUENCE_SCORE',
        current_val=10,
        direction=direction,
        evidence=(
            f"{len(low_conf)} trades with confluence<10 → WR {wr_low:.1%} | "
            f"{len(high_conf)} trades with confluence≥10 → WR {wr_high:.1%}"
        )
    )


def _wr(trades: list) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.get('outcome') == 'WIN')
    return wins / len(trades)


def _emit_nudge_report(symbol: str, market: str, feature: str,
                        current_val, direction: str, evidence: str):
    """
    Writes a nudge proposal to ml_engine/learning/nudge_proposals.jsonl.
    Does NOT modify any code — the parameter-optimizer agent reads this file
    and presents proposals to the user for approval.
    """
    proposal = {
        'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'symbol': symbol,
        'market': market,
        'feature': feature,
        'current_val': current_val,
        'direction': direction,  # 'RAISE' or 'LOWER'
        'suggested_delta': MAX_WEIGHT_NUDGE,
        'evidence': evidence,
        'status': 'PENDING'  # PENDING → APPROVED/REJECTED by user
    }

    out_path = os.path.join(_ROOT, 'ml_engine', 'learning', 'nudge_proposals.jsonl')
    with open(out_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(proposal) + '\n')

    logger.info(
        f"feedback_loop: NUDGE PROPOSAL [{symbol}] {direction} {feature} "
        f"(currently {current_val}) — {evidence}"
    )


def backfill_all_state_files():
    """
    One-time startup backfill: import all closed trades from existing state files
    into the pattern DB so historical data is queryable from day one.
    """
    from ml_engine.memory.trade_pattern_db import backfill_from_state

    sources = [
        ('data/gft_5k/state.json',        'forex', 'gft_5k'),
        ('data/gft_1k_instant/state.json', 'forex', 'gft_1k'),
        ('data/gft_10k/state.json',        'forex', 'gft_10k'),
    ]

    for rel_path, market, account in sources:
        abs_path = os.path.join(_ROOT, rel_path)
        if os.path.exists(abs_path):
            backfill_from_state(abs_path, market=market, account=account)
        else:
            logger.debug(f"feedback_loop: no state file at {rel_path} — skip backfill")

    # NSE trade journal (CSV)
    _backfill_nse_journal()


def _backfill_nse_journal():
    """Import NSE trades from data/trade_journal.csv into pattern DB."""
    import csv
    from ml_engine.memory.trade_pattern_db import record_trade

    csv_path = os.path.join(_ROOT, 'data', 'trade_journal.csv')
    if not os.path.exists(csv_path):
        return

    count = 0
    try:
        with open(csv_path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                trade = {
                    'id':          row.get('trade_id', ''),
                    'symbol':      row.get('symbol', ''),
                    'direction':   row.get('direction', ''),
                    'entry_price': _safe_float(row.get('entry_price')),
                    'stop_loss':   _safe_float(row.get('stop_loss')),
                    'pnl_usd':     _safe_float(row.get('pnl')),
                    'risk_usd':    _safe_float(row.get('risk_amount')),
                    'entry_time':  row.get('entry_time', ''),
                    'exit_time':   row.get('exit_time', ''),
                    'exit_reason': row.get('exit_reason', ''),
                    'confluence':  _safe_int(row.get('confluence', '0')),
                    'mss_type':    row.get('mss_type', ''),
                    'risk_mode':   row.get('risk_mode', 'normal'),
                }
                record_trade(
                    trade=trade, market='nse', account='nse_fyers',
                    session=row.get('session', ''),
                    h4_bias=row.get('h4_bias', ''),
                    fvg_body_pct=_safe_float(row.get('fvg_body_pct', '0')),
                )
                count += 1
        logger.info(f"feedback_loop: backfilled {count} NSE trades from trade_journal.csv")
    except Exception as e:
        logger.warning(f"feedback_loop: NSE journal backfill error: {e}")


def _safe_float(val) -> float:
    try:
        return float(val) if val not in (None, '', 'nan') else 0.0
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val) -> int:
    try:
        return int(val) if val not in (None, '', 'nan') else 0
    except (ValueError, TypeError):
        return 0


def get_pending_nudges() -> list:
    """Return all PENDING nudge proposals — used by parameter-optimizer agent."""
    out_path = os.path.join(_ROOT, 'ml_engine', 'learning', 'nudge_proposals.jsonl')
    if not os.path.exists(out_path):
        return []
    proposals = []
    with open(out_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                p = json.loads(line)
                if p.get('status') == 'PENDING':
                    proposals.append(p)
            except json.JSONDecodeError:
                pass
    return proposals
