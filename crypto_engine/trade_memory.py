# crypto_engine/trade_memory.py
#
# Trade memory: records ICT setup features at open, outcome at close.
# Derives per-pattern win rates; gives scan_crypto_setup a ±1 score boost
# once a pattern has >= MIN_SAMPLE closed trades.
#
# Captured features:
#   mss_type    — 'CHOCH' | 'BOS'
#   has_ob      — Order Block detected
#   ob_in_fvg   — OB overlaps FVG (institutional confluence)
#   ut_aligned  — UT Bot trend agrees with direction
#   has_3br     — Three Bar Reversal present
#   vol_ratio   — displacement candle volume / 20-bar avg vol
#   disp_ratio  — displacement candle size / avg size (from FVG data)
#   fvg_size    — FVG gap in price points
#   score       — confluence score at entry
#
# Outcome (filled on close):
#   outcome     — 'WIN' | 'PARTIAL' | 'LOSS'
#   exit_reason — 'T3' | 'T2' | 'T1' | 'SL'
#   rr_achieved — actual RR realised
#   pnl_usd     — USDT PnL

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger     = logging.getLogger(__name__)
MEMORY_FILE = Path(__file__).parent.parent / 'data' / 'crypto_memory.json'
MIN_SAMPLE  = 10    # trades needed before pattern affects scoring
WIN_THRESH  = 0.58  # win rate above which pattern gets +1
LOSS_THRESH = 0.38  # win rate below which pattern gets -1


# ── I/O ────────────────────────────────────────────────────────────────────────

def _load() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text())
        except Exception:
            pass
    return {'trades': []}


def _save(mem: dict):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(mem, indent=2))


# ── Record open ────────────────────────────────────────────────────────────────

def record_trade_open(trade_id: str, setup: dict,
                      vol_ratio: Optional[float] = None,
                      sweep_type: str = 'WICK'):
    """
    Snapshot ICT setup features when a trade is opened.
    Call from _run_scan immediately after open_crypto_trade() succeeds.
    """
    try:
        mem = _load()
        fvg = setup.get('fvg', {})
        ob  = setup.get('ob')
        ut  = setup.get('ut_bot', {})

        disp_ratio = None
        if fvg.get('mid_candle_sz') and fvg.get('avg_candle_sz') and fvg['avg_candle_sz'] > 0:
            disp_ratio = round(fvg['mid_candle_sz'] / fvg['avg_candle_sz'], 2)

        record = {
            'id'         : trade_id,
            'opened_at'  : datetime.now().isoformat(timespec='seconds'),
            'symbol'     : setup.get('symbol'),
            'direction'  : setup.get('direction'),
            'mss_type'   : setup.get('mss_type', 'BOS'),
            'has_ob'     : ob is not None,
            'ob_in_fvg'  : bool(setup.get('ob_confluence')),
            'ut_aligned' : bool(ut.get('aligned')) if isinstance(ut, dict) else False,
            'has_3br'    : bool(setup.get('three_bar')),
            'sweep_type' : sweep_type,
            'vol_ratio'  : vol_ratio,
            'disp_ratio' : disp_ratio,
            'fvg_size'   : round(fvg.get('fvg_high', 0) - fvg.get('fvg_low', 0), 2),
            'score'      : setup.get('confluence', 0),
            # outcome filled on close
            'outcome'    : None,
            'exit_reason': None,
            'rr_achieved': None,
            'pnl_usd'    : None,
            'closed_at'  : None,
        }
        mem['trades'].append(record)
        _save(mem)
        logger.info(
            f"Memory.open {trade_id} | mss={record['mss_type']} ob={record['has_ob']} "
            f"ut={record['ut_aligned']} 3br={record['has_3br']} "
            f"disp={disp_ratio} vol={vol_ratio} sweep={sweep_type}"
        )
    except Exception as e:
        logger.error(f"TradeMemory record_open error: {e}")


# ── Record close ───────────────────────────────────────────────────────────────

def record_trade_close(trade_id: str, outcome: str, exit_reason: str,
                       rr_achieved: float, pnl_usd: float):
    """
    Fill outcome fields for an existing memory record.
    outcome   : 'WIN' | 'PARTIAL' | 'LOSS'
    exit_reason: 'T3' | 'T2' | 'T1' | 'SL'
    """
    try:
        mem = _load()
        for t in mem['trades']:
            if t['id'] == trade_id:
                t['outcome']     = outcome
                t['exit_reason'] = exit_reason
                t['rr_achieved'] = round(rr_achieved, 2)
                t['pnl_usd']     = round(pnl_usd, 2)
                t['closed_at']   = datetime.now().isoformat(timespec='seconds')
                _save(mem)
                logger.info(
                    f"Memory.close {trade_id} | outcome={outcome} "
                    f"rr={rr_achieved:.2f} pnl=${pnl_usd:.2f}"
                )
                return
        logger.warning(f"TradeMemory: {trade_id} not found for close update")
    except Exception as e:
        logger.error(f"TradeMemory record_close error: {e}")


# ── Pattern stats ──────────────────────────────────────────────────────────────

def _pattern_key(t: dict) -> str:
    """Group trades by the four structural features that matter most."""
    return (
        f"mss={t.get('mss_type','?')}"
        f"|ob={t.get('has_ob','?')}"
        f"|ut={t.get('ut_aligned','?')}"
        f"|3br={t.get('has_3br','?')}"
    )


def _compute_stats(closed: list) -> dict:
    stats = {}
    for t in closed:
        key = _pattern_key(t)
        s   = stats.setdefault(key, {'wins': 0, 'total': 0, 'rr_sum': 0.0})
        s['total'] += 1
        if t.get('outcome') == 'WIN':
            s['wins'] += 1
        s['rr_sum'] += t.get('rr_achieved') or 0.0
    for s in stats.values():
        n = s['total']
        s['win_rate'] = round(s['wins'] / n, 2) if n else 0.0
        s['avg_rr']   = round(s['rr_sum'] / n, 2) if n else 0.0
    return stats


# ── Score boost ────────────────────────────────────────────────────────────────

def get_memory_score_boost(setup: dict) -> int:
    """
    Returns +1, 0, or -1 based on historical win rate for this pattern.
    Neutral (0) until MIN_SAMPLE closed trades exist for the pattern.
    """
    try:
        mem    = _load()
        closed = [t for t in mem['trades'] if t.get('outcome')]
        if not closed:
            return 0

        probe = {
            'mss_type'  : setup.get('mss_type', 'BOS'),
            'has_ob'    : setup.get('ob') is not None,
            'ut_aligned': bool(setup.get('ut_bot', {}).get('aligned')),
            'has_3br'   : bool(setup.get('three_bar')),
        }
        key   = _pattern_key(probe)
        stats = _compute_stats(closed)

        if key not in stats:
            return 0
        s = stats[key]
        if s['total'] < MIN_SAMPLE:
            return 0
        if s['win_rate'] >= WIN_THRESH:
            return 1
        if s['win_rate'] <= LOSS_THRESH:
            return -1
        return 0

    except Exception as e:
        logger.error(f"TradeMemory score_boost error: {e}")
        return 0


# ── Volume insight ─────────────────────────────────────────────────────────────

def _vol_insight(closed: list) -> str:
    """Compare average vol_ratio and disp_ratio between winners and losers."""
    lines = []
    for field, label in [('vol_ratio', 'Volume ratio'), ('disp_ratio', 'Displacement ratio')]:
        data   = [(t[field], t['outcome']) for t in closed if t.get(field) is not None]
        if not data:
            continue
        w_vals = [v for v, o in data if o == 'WIN']
        l_vals = [v for v, o in data if o == 'LOSS']
        w_avg  = round(sum(w_vals) / len(w_vals), 2) if w_vals else None
        l_avg  = round(sum(l_vals) / len(l_vals), 2) if l_vals else None
        lines.append(
            f"{label}: Winners {w_avg} | Losers {l_avg}"
            + (" ← winners had stronger displacement" if w_avg and l_avg and w_avg > l_avg else "")
        )
    return '\n'.join(lines) if lines else ''


# ── Telegram summary ───────────────────────────────────────────────────────────

def memory_summary() -> str:
    """Human-readable summary for /memory Telegram command."""
    mem     = _load()
    all_t   = mem['trades']
    closed  = [t for t in all_t if t.get('outcome')]
    open_t  = [t for t in all_t if not t.get('outcome')]

    if not all_t:
        return "📚 Trade memory is empty — no trades recorded yet."

    if not closed:
        return (f"📚 *TRADE MEMORY*\n"
                f"Recorded : {len(all_t)} trade(s) — none closed yet\n"
                f"Open now : {len(open_t)}")

    wins    = [t for t in closed if t['outcome'] == 'WIN']
    losses  = [t for t in closed if t['outcome'] == 'LOSS']
    partial = [t for t in closed if t['outcome'] == 'PARTIAL']
    wr      = round(len(wins) / len(closed) * 100, 1)
    avg_rr  = round(sum(t.get('rr_achieved') or 0 for t in closed) / len(closed), 2)

    lines = [
        "📚 *TRADE MEMORY*",
        f"Total : {len(all_t)} ({len(open_t)} open)",
        f"Closed: {len(closed)} — W:{len(wins)} L:{len(losses)} P:{len(partial)}",
        f"Win rate : {wr}%   Avg RR : {avg_rr}",
        "",
        "*Pattern breakdown* (mss|ob|ut|3br):",
    ]

    stats = _compute_stats(closed)
    for key, s in sorted(stats.items(), key=lambda x: -x[1]['total']):
        if s['total'] < 3:
            continue
        boost = ("▲ " if s['win_rate'] >= WIN_THRESH
                 else ("▼ " if s['win_rate'] <= LOSS_THRESH else "— "))
        lines.append(
            f"{boost}{key}  WR={int(s['win_rate']*100)}%"
            f"  RR={s['avg_rr']}  n={s['total']}"
        )

    # Sweep type split
    for stype in ('WICK', 'CLOSE'):
        group = [t for t in closed if t.get('sweep_type') == stype]
        if len(group) >= 3:
            gr_wins = sum(1 for t in group if t['outcome'] == 'WIN')
            lines.append(f"Sweep={stype}: WR={round(gr_wins/len(group)*100)}%  n={len(group)}")

    vol = _vol_insight(closed)
    if vol:
        lines += ["", "*Displacement strength:*", vol]

    # Minimum score filter note
    best_score_wins = [t['score'] for t in wins] if wins else []
    if best_score_wins:
        median_win_score = sorted(best_score_wins)[len(best_score_wins) // 2]
        lines.append(f"\nMedian winning score: {median_win_score}/14")

    return '\n'.join(lines)
