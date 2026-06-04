# utils/trade_enrichment.py — Enriched pattern library pipeline
#
# Every live trade writes a rich JSONL record to data/pattern_library_enriched.jsonl
# containing all ICT context, Greeks, regime, and outcome data.
#
# This file feeds the pattern library as sample size grows.
# Statistical minimum rules:
#   < 30 samples in subcategory  → do not trust win rate
#   30-99                        → weak confidence
#   100-199                      → usable, cautious
#   200+                         → acceptable
#   500+                         → strong (enable per-subcategory gates)
#
# Score gate is NOT reduced until sample thresholds are met.

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, Optional

_ENRICHED_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'pattern_library_enriched.jsonl'
)

# ── sample size thresholds ────────────────────────────────────────────────────

SAMPLE_THRESHOLDS = {
    'UNRELIABLE': 30,      # < 30: do not trust win rate
    'WEAK':       100,     # 30-99: weak confidence
    'USABLE':     200,     # 100-199: cautious use
    'ACCEPTABLE': 500,     # 200-499: acceptable
    'STRONG':     999999,  # 500+: strong
}


def sample_confidence_label(n: int) -> str:
    if n < 30:   return 'UNRELIABLE'
    if n < 100:  return 'WEAK'
    if n < 200:  return 'USABLE'
    if n < 500:  return 'ACCEPTABLE'
    return 'STRONG'


# ── record builder ────────────────────────────────────────────────────────────

def build_enriched_entry(trade_rec: Dict, setup: Dict, strike_info: Dict) -> Dict:
    """
    Build a rich entry record from trade data at the moment of opening a position.
    All ICT context, Greeks, and market conditions are captured here.
    Exit data is added later via enrich_exit().
    """
    sig  = setup.get('entry_signal', {})
    fvg  = setup.get('fvg', {})
    comp = setup.get('compression', {}) or {}
    liq  = setup.get('liq_sweep') or {}

    # Determine sweep type: external (EQH/EQL/PDH/PDL) vs internal (intraday)
    dol      = setup.get('dol') or {}
    dol_type = dol.get('type', '')
    sweep_external = dol.get('is_eqh_eql', False) or dol_type in ('EQH', 'EQL', 'PDH', 'PDL', 'PWH', 'PWL')

    return {
        # Identity
        'record_id'         : f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{trade_rec.get('symbol','?')[:12]}",
        'entry_time'        : datetime.now().isoformat(),
        'exit_time'         : None,

        # Instrument
        'symbol'            : trade_rec.get('symbol', ''),
        'underlying'        : trade_rec.get('underlying', setup.get('symbol', '')),
        'index_name'        : _derive_index(trade_rec.get('underlying', '')),
        'direction'         : setup.get('direction', ''),
        'instrument_type'   : trade_rec.get('instrument_type', 'OPTION'),
        'setup_type'        : setup.get('setup_type', 'SILVER_BULLET'),
        'window'            : setup.get('window', ''),

        # ICT structure
        'score'             : setup.get('confluence', 0),
        'mss_type'          : setup.get('mss_type', ''),
        'sweep_quality'     : setup.get('sweep_quality', 0),
        'sweep_type'        : liq.get('sweep_type', ''),
        'sweep_external'    : sweep_external,
        'sweep_candles_ago' : liq.get('candles_ago', 0),
        'fvg_size'          : fvg.get('size', 0),
        'fvg_body_ratio'    : round(fvg.get('body_ratio', 0), 3),
        'in_fvg'            : setup.get('in_fvg', False),
        'ob_confluence'     : setup.get('ob_confluence', False),
        'three_bar'         : setup.get('three_bar', False),
        'h1_bias'           : setup.get('h1_bias', ''),
        'h4_bias'           : setup.get('h4_bias', ''),
        'regime'            : trade_rec.get('regime', ''),
        'compression_pct'   : comp.get('compression_pct', 100.0),
        'is_compressed'     : comp.get('is_compressed', False),

        # Session context
        'is_expiry_day'     : setup.get('is_expiry_day', False),
        'is_news_day'       : _is_news_day_today(),
        'session_risk_mult' : setup.get('risk_multiplier_override', 1.0),

        # Option Greeks at entry
        'strike'            : strike_info.get('strike', ''),
        'expiry'            : strike_info.get('expiry', ''),
        'delta'             : round(float(strike_info.get('delta', 0) or 0), 4),
        'iv_entry'          : round(float(strike_info.get('iv', 0) or 0), 4),
        'theta'             : round(float(strike_info.get('theta', 0) or 0), 4),
        'vega'              : round(float(strike_info.get('vega', 0) or 0), 4),
        'dte'               : trade_rec.get('dte', 0),

        # Execution quality
        'entry_price'       : trade_rec.get('entry_price', 0),
        'stop_loss'         : trade_rec.get('current_sl', 0),
        'target1'           : trade_rec.get('target1', 0),
        'target2'           : trade_rec.get('target2', 0),
        'target3'           : trade_rec.get('target3', 0),
        'spread_at_entry'   : _get_spread(strike_info),
        'volume_at_entry'   : int(strike_info.get('volume', 0) or 0),
        'lots'              : trade_rec.get('quantity', 0) // max(trade_rec.get('lot_size', 1), 1),
        'entry_underlying'  : float(sig.get('entry', 0) or 0),

        # Outcome fields (filled at exit)
        'exit_price'        : None,
        'exit_reason'       : None,
        'realized_pnl'      : None,
        'r_multiple'        : None,
        'hold_time_mins'    : None,
        'max_favorable_excursion': None,   # MFE: best LTP during trade
        'max_adverse_excursion':  None,    # MAE: worst LTP during trade
        'iv_exit'           : None,
        'iv_crush_flag'     : False,
        'opposite_sweep_exit': False,
        'outcome'           : None,        # WIN / LOSS / BE
    }


def append_enriched_entry(record: Dict) -> str:
    """Write entry record to JSONL. Returns record_id."""
    _ensure_path()
    with open(_ENRICHED_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record) + '\n')
    return record.get('record_id', '')


def enrich_exit(record_id: str, exit_data: Dict) -> bool:
    """
    Update the matching JSONL record with exit fields.
    Reads the file, updates the matching record, rewrites.
    Returns True if found and updated.
    """
    if not record_id:
        return False
    _ensure_path()
    lines = []
    updated = False
    try:
        with open(_ENRICHED_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get('record_id') == record_id:
                        rec.update(exit_data)
                        # Compute outcome
                        pnl = exit_data.get('realized_pnl', 0) or 0
                        if pnl > 0:
                            rec['outcome'] = 'WIN'
                        elif pnl < 0:
                            rec['outcome'] = 'LOSS'
                        else:
                            rec['outcome'] = 'BE'
                        updated = True
                    lines.append(json.dumps(rec))
                except Exception:
                    lines.append(line)
        with open(_ENRICHED_PATH, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    except Exception as e:
        from utils.logger import logger
        logger.debug(f"trade_enrichment.enrich_exit error: {e}")
    return updated


# ── sample size analysis ──────────────────────────────────────────────────────

def get_sample_stats() -> Dict:
    """
    Return sample counts by subcategory:
      index × direction × window → {total, wins, losses, win_rate, confidence}
    """
    _ensure_path()
    stats: Dict = {}
    try:
        with open(_ENRICHED_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get('outcome') is None:
                        continue  # open trade, skip
                    key = f"{rec.get('index_name','?')}_{rec.get('direction','?')}_{rec.get('window','?')[:10]}"
                    if key not in stats:
                        stats[key] = {'total': 0, 'wins': 0, 'losses': 0}
                    stats[key]['total'] += 1
                    if rec['outcome'] == 'WIN':
                        stats[key]['wins'] += 1
                    elif rec['outcome'] == 'LOSS':
                        stats[key]['losses'] += 1
                except Exception:
                    continue
        for k, v in stats.items():
            t = v['total']
            v['win_rate']   = round(v['wins'] / t * 100, 1) if t else 0
            v['confidence'] = sample_confidence_label(t)
    except Exception:
        pass
    return stats


def format_sample_report() -> str:
    """Format sample stats for Telegram /library_status command."""
    stats = get_sample_stats()
    if not stats:
        return "Pattern library: no completed trades yet."

    total = sum(v['total'] for v in stats.values())
    lines = [f"<b>PATTERN LIBRARY — {total} total completed trades</b>\n"]
    for key, v in sorted(stats.items(), key=lambda x: -x[1]['total']):
        bar = '=' * min(v['total'] // 5, 20)
        lines.append(
            f"<code>{key:<35}</code> n={v['total']:3d} "
            f"WR={v['win_rate']:4.1f}% [{v['confidence']}]\n"
            f"  {bar}"
        )

    # Scale-up gate status
    min_n = min(v['total'] for v in stats.values()) if stats else 0
    if min_n < 30:
        gate_msg = "Score gate LOCKED at 12 (insufficient data)"
    elif min_n < 100:
        gate_msg = "Score gate at 12 — weak confidence"
    elif min_n < 200:
        gate_msg = "Score gate may lower to 11 at 200+ samples"
    else:
        gate_msg = "Approaching scale-up threshold (500+)"
    lines.append(f"\n<i>{gate_msg}</i>")
    return '\n'.join(lines)


# ── private helpers ───────────────────────────────────────────────────────────

def _ensure_path():
    os.makedirs(os.path.dirname(os.path.abspath(_ENRICHED_PATH)), exist_ok=True)
    if not os.path.exists(_ENRICHED_PATH):
        open(_ENRICHED_PATH, 'w').close()


def _derive_index(underlying: str) -> str:
    u = underlying.upper()
    for idx in ('BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTY'):
        if idx in u:
            return idx
    return underlying.split(':')[-1].split('-')[0] if underlying else '?'


def _get_spread(strike_info: Dict) -> float:
    bid = float(strike_info.get('bid', 0) or 0)
    ask = float(strike_info.get('ask', 0) or 0)
    if bid > 0 and ask > 0:
        return round((ask - bid) / bid, 4)
    return 0.0


def _is_news_day_today() -> bool:
    try:
        from data.news_calendar import is_news_day
        is_news, _ = is_news_day()
        return is_news
    except Exception:
        return False
