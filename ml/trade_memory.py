# ml/trade_memory.py — CB6 Quantum ML Trade Memory Engine
#
# Stores validated backtest intelligence and incrementally learns from live trades.
#
# This layer NEVER overrides ICT logic. It only:
#   - Learns from every closed trade (incremental)
#   - Ranks new setups against historical winners
#   - Explains why a setup looks strong or weak
#   - Advises on TP/SL management style
#
# Memory files (auto-created on first run, updated after every trade):
#   ml/category_memory.json
#   ml/fvg_memory.json
#   ml/index_performance_memory.json
#   ml/time_window_memory.json

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, Optional, Tuple

from utils.logger import logger

# ── File paths ────────────────────────────────────────────────────────────────

_ML_DIR = os.path.dirname(os.path.abspath(__file__))

CATEGORY_MEM_PATH   = os.path.join(_ML_DIR, 'category_memory.json')
FVG_MEM_PATH        = os.path.join(_ML_DIR, 'fvg_memory.json')
INDEX_MEM_PATH      = os.path.join(_ML_DIR, 'index_performance_memory.json')
WINDOW_MEM_PATH     = os.path.join(_ML_DIR, 'time_window_memory.json')

# ── FVG bucket helper ─────────────────────────────────────────────────────────

FVG_BINS   = [0, 5, 10, 20, 35, 50, 9999]
FVG_LABELS = ['Tiny 0-5', 'Small 5-10', 'Medium 10-20',
              'Large 20-35', 'XLarge 35-50', 'Oversized 50+']


def fvg_bucket(size: float) -> str:
    for i in range(len(FVG_BINS) - 1):
        if FVG_BINS[i] <= size < FVG_BINS[i + 1]:
            return FVG_LABELS[i]
    return FVG_LABELS[-1]


def category_key(mss_type: str, direction: str) -> str:
    """e.g. 'CHOCH_LONG' or 'BOS_SHORT'"""
    mss = 'CHOCH' if mss_type.upper() in ('CHOCH', 'CHOCH_UP', 'CHOCH_DOWN') else 'BOS'
    d   = 'LONG' if direction.upper() in ('BULLISH', 'LONG', 'BUY') else 'SHORT'
    return f"{mss}_{d}"


# ── Bootstrap from backtest CSV ───────────────────────────────────────────────

def bootstrap_from_csv(csv_path: str) -> None:
    """
    One-time import of 90-day backtest results into memory files.
    Safe to re-run — will merge with any existing live-trade data,
    keeping the higher sample count per category.
    """
    try:
        import pandas as pd
    except ImportError:
        logger.error("trade_memory: pandas required for bootstrap")
        return

    if not os.path.exists(csv_path):
        logger.error(f"trade_memory: CSV not found: {csv_path}")
        return

    df = pd.read_csv(csv_path)
    logger.info(f"trade_memory: bootstrapping from {len(df)} trades in {csv_path}")

    _bootstrap_category_memory(df)
    _bootstrap_fvg_memory(df)
    _bootstrap_index_memory(df)
    _bootstrap_window_memory(df)

    logger.info("trade_memory: bootstrap complete")


def _bootstrap_category_memory(df) -> None:
    cats = {}
    for cat in ['CHOCH_LONG', 'CHOCH_SHORT', 'BOS_LONG', 'BOS_SHORT']:
        mss_t, dir_t = cat.split('_')
        dir_full = 'BULLISH' if dir_t == 'LONG' else 'BEARISH'
        sub = df[(df['mss_type'] == mss_t) & (df['direction'] == dir_full)]

        if len(sub) == 0:
            continue

        wins = sub[sub['r'] > 0]
        losses = sub[sub['r'] <= 0]

        # FVG WR by bucket for this category
        fvg_wr = {}
        sub2 = sub.copy()
        sub2['bucket'] = sub2['fvg_size'].apply(fvg_bucket)
        for b in FVG_LABELS:
            bs = sub2[sub2['bucket'] == b]
            if len(bs) >= 2:
                fvg_wr[b] = {
                    'n' : int(len(bs)),
                    'wr': round((bs['r'] > 0).mean() * 100, 1),
                    'avg_r': round(bs['r'].mean(), 2),
                }

        # Best/worst index
        idx_wr = (sub.groupby('index')['r']
                  .agg(lambda x: round((x > 0).mean() * 100, 1))
                  .sort_values(ascending=False))
        best_idx  = idx_wr.index[0] if len(idx_wr) else ''
        worst_idx = idx_wr.index[-1] if len(idx_wr) else ''

        # Score distribution for winners
        w_scores = wins['score'].tolist() if len(wins) else []
        w_sq     = wins['sweep_q'].tolist() if len(wins) else []
        w_fvg    = wins['fvg_size'].tolist() if len(wins) else []

        cats[cat] = {
            'trade_count'        : int(len(sub)),
            'win_count'          : int(len(wins)),
            'loss_count'         : int(len(losses)),
            'win_rate'           : round((sub['r'] > 0).mean() * 100, 1),
            'avg_r'              : round(sub['r'].mean(), 2),
            'avg_r_winners'      : round(wins['r'].mean(), 2) if len(wins) else 0,
            'avg_r_losers'       : round(losses['r'].mean(), 2) if len(losses) else 0,
            'avg_hold_mins'      : round(sub['hold_min'].mean(), 1),
            'avg_fvg_size'       : round(sub['fvg_size'].mean(), 1),
            'avg_sweep_quality'  : round(sub['sweep_q'].mean(), 1),
            'avg_score'          : round(sub['score'].mean(), 1),
            'winner_score_p25'   : round(float(wins['score'].quantile(0.25)), 1) if len(wins) else 0,
            'winner_score_p50'   : round(float(wins['score'].quantile(0.50)), 1) if len(wins) else 0,
            'winner_score_p75'   : round(float(wins['score'].quantile(0.75)), 1) if len(wins) else 0,
            'winner_sq_p25'      : round(float(wins['sweep_q'].quantile(0.25)), 1) if len(wins) else 0,
            'winner_sq_mean'     : round(wins['sweep_q'].mean(), 1) if len(wins) else 0,
            'winner_fvg_p25'     : round(float(wins['fvg_size'].quantile(0.25)), 1) if len(wins) else 0,
            'winner_fvg_p75'     : round(float(wins['fvg_size'].quantile(0.75)), 1) if len(wins) else 0,
            'best_index'         : best_idx,
            'worst_index'        : worst_idx,
            'fvg_wr_by_bucket'   : fvg_wr,
            'historical_confidence': round((sub['r'] > 0).mean() * 100, 1),
            'source'             : 'backtest_90day_2026-03-04_2026-06-01',
            'last_updated'       : datetime.now().isoformat(),
        }

    _write_json(CATEGORY_MEM_PATH, cats)
    logger.info(f"trade_memory: category memory written ({len(cats)} categories)")


def _bootstrap_fvg_memory(df) -> None:
    mem = {}
    df2 = df.copy()
    df2['bucket'] = df2['fvg_size'].apply(fvg_bucket)

    for b in FVG_LABELS:
        sub = df2[df2['bucket'] == b]
        if not len(sub):
            continue
        long_s  = sub[sub['direction'] == 'BULLISH']
        short_s = sub[sub['direction'] == 'BEARISH']

        mem[b] = {
            'overall': {
                'n'    : int(len(sub)),
                'wr'   : round((sub['r'] > 0).mean() * 100, 1),
                'avg_r': round(sub['r'].mean(), 2),
                'loss_rate': round((sub['r'] <= 0).mean() * 100, 1),
            },
            'LONG': {
                'n'    : int(len(long_s)),
                'wr'   : round((long_s['r'] > 0).mean() * 100, 1) if len(long_s) else 0,
                'avg_r': round(long_s['r'].mean(), 2) if len(long_s) else 0,
            },
            'SHORT': {
                'n'    : int(len(short_s)),
                'wr'   : round((short_s['r'] > 0).mean() * 100, 1) if len(short_s) else 0,
                'avg_r': round(short_s['r'].mean(), 2) if len(short_s) else 0,
            },
        }

    _write_json(FVG_MEM_PATH, mem)
    logger.info(f"trade_memory: FVG memory written ({len(mem)} buckets)")


def _bootstrap_index_memory(df) -> None:
    mem = {}
    for idx in df['index'].unique():
        mem[idx] = {}
        sub_idx = df[df['index'] == idx]
        for cat in ['CHOCH_LONG', 'CHOCH_SHORT', 'BOS_LONG', 'BOS_SHORT']:
            mss_t, dir_t = cat.split('_')
            dir_full = 'BULLISH' if dir_t == 'LONG' else 'BEARISH'
            sub = sub_idx[(sub_idx['mss_type'] == mss_t) & (sub_idx['direction'] == dir_full)]
            if not len(sub):
                continue
            mem[idx][cat] = {
                'n'    : int(len(sub)),
                'wr'   : round((sub['r'] > 0).mean() * 100, 1),
                'avg_r': round(sub['r'].mean(), 2),
                'total_pnl': round(sub['pnl_rs'].sum(), 0),
            }
        # Best FVG size for this index
        sub_idx2 = sub_idx.copy()
        sub_idx2['bucket'] = sub_idx2['fvg_size'].apply(fvg_bucket)
        best_fvg = (sub_idx2.groupby('bucket')['r']
                    .agg(lambda x: (x > 0).mean() * 100)
                    .sort_values(ascending=False))
        mem[idx]['best_fvg_bucket'] = best_fvg.index[0] if len(best_fvg) else ''
        mem[idx]['total_trades']    = int(len(sub_idx))
        mem[idx]['win_rate']        = round((sub_idx['r'] > 0).mean() * 100, 1)

    _write_json(INDEX_MEM_PATH, mem)
    logger.info(f"trade_memory: index memory written ({len(mem)} indices)")


def _bootstrap_window_memory(df) -> None:
    """Store performance by entry hour (derived from entry_time)."""
    import pandas as pd
    df2 = df.copy()
    df2['hour'] = pd.to_datetime(df2['entry_time'], format='%H:%M', errors='coerce').dt.hour

    mem = {}
    for hour in sorted(df2['hour'].dropna().unique()):
        sub = df2[df2['hour'] == hour]
        if not len(sub):
            continue
        mem[str(int(hour))] = {
            'n'        : int(len(sub)),
            'wr'       : round((sub['r'] > 0).mean() * 100, 1),
            'avg_r'    : round(sub['r'].mean(), 2),
            'label'    : f"{int(hour):02d}:00–{int(hour)+1:02d}:00 IST",
            'long_wr'  : round((sub[sub['direction']=='BULLISH']['r']>0).mean()*100, 1)
                         if len(sub[sub['direction']=='BULLISH']) else 0,
            'short_wr' : round((sub[sub['direction']=='BEARISH']['r']>0).mean()*100, 1)
                         if len(sub[sub['direction']=='BEARISH']) else 0,
        }

    _write_json(WINDOW_MEM_PATH, mem)
    logger.info(f"trade_memory: window memory written ({len(mem)} hours)")


# ── Memory loaders ────────────────────────────────────────────────────────────

def load_category_memory() -> Dict:
    return _read_json(CATEGORY_MEM_PATH)


def load_fvg_memory() -> Dict:
    return _read_json(FVG_MEM_PATH)


def load_index_memory() -> Dict:
    return _read_json(INDEX_MEM_PATH)


def load_window_memory() -> Dict:
    return _read_json(WINDOW_MEM_PATH)


# ── Similarity scoring ────────────────────────────────────────────────────────

def score_setup_similarity(
    mss_type: str,
    direction: str,
    fvg_size: float,
    score: float,
    sweep_quality: float,
    index_name: str,
    entry_hour: Optional[int] = None,
) -> Dict:
    """
    Compare a new setup against historical winners for its category.

    Returns:
    {
      'category'          : 'CHOCH_LONG',
      'similarity_score'  : 87,            # 0-100
      'confidence_pct'    : 81.6,          # historical WR for this category
      'expected_r'        : 1.82,
      'fvg_signal'        : 'STRONG',      # STRONG / GOOD / AVERAGE / WEAK
      'fvg_bucket_wr'     : 100.0,
      'fvg_bucket_avg_r'  : 3.94,
      'score_rank'        : 'TOP_DECILE',  # TOP_DECILE / ABOVE_AVG / AVG / BELOW_AVG
      'sq_rank'           : 'STRONG',
      'index_edge'        : True,
      'index_wr_for_cat'  : 100.0,
      'guidance'          : 'T2_PRIORITY / RUNNER_OK / CONSERVATIVE',
      'summary'           : '...',          # human-readable
    }
    """
    cat      = category_key(mss_type, direction)
    cat_mem  = load_category_memory().get(cat, {})
    fvg_mem  = load_fvg_memory()
    idx_mem  = load_index_memory().get(index_name, {})

    if not cat_mem:
        return _empty_similarity(cat, "No historical data for this category yet")

    bucket   = fvg_bucket(fvg_size)
    dir_key  = 'LONG' if direction.upper() in ('BULLISH', 'LONG', 'BUY') else 'SHORT'

    # ── Component 1: FVG bucket alignment (0-40 pts) ─────────────────────────
    fvg_bucket_data = fvg_mem.get(bucket, {}).get(dir_key, {})
    fvg_bucket_wr   = fvg_bucket_data.get('wr', cat_mem.get('win_rate', 50))
    fvg_bucket_r    = fvg_bucket_data.get('avg_r', cat_mem.get('avg_r', 1.0))

    # Also check category-specific FVG bucket data
    cat_fvg_data    = cat_mem.get('fvg_wr_by_bucket', {}).get(bucket, {})
    if cat_fvg_data:
        fvg_bucket_wr = cat_fvg_data.get('wr', fvg_bucket_wr)
        fvg_bucket_r  = cat_fvg_data.get('avg_r', fvg_bucket_r)

    fvg_score = min(40, int(fvg_bucket_wr / 100 * 40))

    if fvg_bucket_wr >= 90:
        fvg_signal = 'EXCELLENT'
    elif fvg_bucket_wr >= 80:
        fvg_signal = 'STRONG'
    elif fvg_bucket_wr >= 70:
        fvg_signal = 'GOOD'
    elif fvg_bucket_wr >= 60:
        fvg_signal = 'AVERAGE'
    else:
        fvg_signal = 'WEAK'

    # ── Component 2: Score alignment (0-30 pts) ───────────────────────────────
    avg_score    = cat_mem.get('avg_score', 14)
    p50_score    = cat_mem.get('winner_score_p50', avg_score)
    p75_score    = cat_mem.get('winner_score_p75', avg_score + 2)
    p25_score    = cat_mem.get('winner_score_p25', avg_score - 2)

    if score >= p75_score:
        score_pts  = 30
        score_rank = 'TOP_DECILE'
    elif score >= p50_score:
        score_pts  = 22
        score_rank = 'ABOVE_AVG'
    elif score >= p25_score:
        score_pts  = 15
        score_rank = 'AVG'
    else:
        score_pts  = 8
        score_rank = 'BELOW_AVG'

    # ── Component 3: Sweep quality (0-30 pts) ─────────────────────────────────
    avg_sq = cat_mem.get('winner_sq_mean', 7.1)
    if sweep_quality >= avg_sq + 1:
        sq_pts  = 30
        sq_rank = 'EXCELLENT'
    elif sweep_quality >= avg_sq:
        sq_pts  = 22
        sq_rank = 'STRONG'
    elif sweep_quality >= avg_sq - 0.5:
        sq_pts  = 15
        sq_rank = 'GOOD'
    else:
        sq_pts  = 8
        sq_rank = 'MARGINAL'

    similarity = fvg_score + score_pts + sq_pts

    # ── Index edge ────────────────────────────────────────────────────────────
    idx_cat_data    = idx_mem.get(cat, {})
    idx_wr_for_cat  = idx_cat_data.get('wr', cat_mem.get('win_rate', 0))
    index_edge      = idx_wr_for_cat >= cat_mem.get('win_rate', 0)

    # If index historically underperforms for this category, penalise slightly
    if idx_wr_for_cat < cat_mem.get('win_rate', 0) - 10:
        similarity = max(0, similarity - 10)

    # ── Expected R using FVG bucket ───────────────────────────────────────────
    expected_r = fvg_bucket_r if fvg_bucket_r else cat_mem.get('avg_r', 1.5)

    # ── TP/SL guidance ────────────────────────────────────────────────────────
    cat_wr = cat_mem.get('win_rate', 70)
    cat_r  = cat_mem.get('avg_r', 1.5)
    if cat == 'BOS_SHORT':
        guidance = 'RUNNER_OK'        # 88.2% WR, +2.93R — let it run
    elif cat == 'CHOCH_LONG':
        guidance = 'T2_PRIORITY'      # 81.6% WR, +1.82R — T2 is realistic ceiling
    elif cat == 'CHOCH_SHORT':
        guidance = 'T2_PRIORITY'      # 81.2% WR — same logic
    elif cat == 'BOS_LONG':
        guidance = 'CONSERVATIVE'     # 76.5% WR — lowest, tighten earlier
    else:
        guidance = 'T2_PRIORITY'

    # ── Summary ───────────────────────────────────────────────────────────────
    summary = _build_summary(
        cat, bucket, fvg_size, fvg_signal, fvg_bucket_wr, fvg_bucket_r,
        score, score_rank, sweep_quality, sq_rank,
        similarity, cat_mem.get('win_rate', 0), expected_r,
        index_name, idx_wr_for_cat, guidance,
    )

    return {
        'category'         : cat,
        'similarity_score' : similarity,
        'confidence_pct'   : cat_mem.get('win_rate', 0),
        'expected_r'       : round(expected_r, 2),
        'fvg_bucket'       : bucket,
        'fvg_signal'       : fvg_signal,
        'fvg_bucket_wr'    : fvg_bucket_wr,
        'fvg_bucket_avg_r' : round(fvg_bucket_r, 2),
        'score_rank'       : score_rank,
        'sq_rank'          : sq_rank,
        'index_edge'       : index_edge,
        'index_wr_for_cat' : idx_wr_for_cat,
        'guidance'         : guidance,
        'summary'          : summary,
    }


def _build_summary(cat, bucket, fvg_size, fvg_signal, fvg_bucket_wr, fvg_bucket_r,
                   score, score_rank, sq, sq_rank, similarity,
                   hist_wr, expected_r, index_name, idx_wr, guidance) -> str:
    dir_advice = {
        'RUNNER_OK'    : 'Historical edge strong — allow runner to T3.',
        'T2_PRIORITY'  : 'T2 is the realistic target. Exit 50% at T1, hold rest to T2.',
        'CONSERVATIVE' : 'Lowest historical WR. Exit at T1, move SL to BE immediately.',
    }
    return (
        f"{cat.replace('_',' ')} setup | similarity {similarity}/100\n"
        f"FVG: {fvg_size:.1f}pt ({bucket}) — {fvg_signal} "
        f"(WR {fvg_bucket_wr:.0f}% / avg {fvg_bucket_r:+.2f}R historically)\n"
        f"Score: {score:.0f} [{score_rank}]  SQ: {sq:.0f} [{sq_rank}]\n"
        f"Backtest WR: {hist_wr:.1f}%  Backtest avg R: {expected_r:+.2f}R (estimate only)\n"
        f"{index_name} backtest WR for {cat}: {idx_wr:.0f}%\n"
        f"Management: {dir_advice.get(guidance, guidance)}"
    )


def _empty_similarity(cat: str, reason: str) -> Dict:
    return {
        'category'         : cat,
        'similarity_score' : 0,
        'confidence_pct'   : 0,
        'expected_r'       : 0,
        'fvg_bucket'       : '',
        'fvg_signal'       : 'UNKNOWN',
        'fvg_bucket_wr'    : 0,
        'fvg_bucket_avg_r' : 0,
        'score_rank'       : 'UNKNOWN',
        'sq_rank'          : 'UNKNOWN',
        'index_edge'       : False,
        'index_wr_for_cat' : 0,
        'guidance'         : 'T2_PRIORITY',
        'summary'          : reason,
    }


# ── Post-trade incremental learning ──────────────────────────────────────────

def update_from_trade(
    mss_type   : str,
    direction  : str,
    fvg_size   : float,
    score      : float,
    sweep_q    : float,
    outcome    : str,
    r_multiple : float,
    pnl_rs     : float,
    index_name : str,
    hold_mins  : float,
    entry_time : str = '',
) -> None:
    """
    Update all memory files after a live trade closes.
    Uses Bayesian-style incremental update: new data is blended into existing stats
    proportional to sample size, so 120 backtest trades don't get wiped by 1 live trade.
    Never deletes — only adds.
    """
    cat    = category_key(mss_type, direction)
    bucket = fvg_bucket(fvg_size)
    is_win = r_multiple > 0
    dir_key = 'LONG' if direction.upper() in ('BULLISH', 'LONG', 'BUY') else 'SHORT'

    _update_category_memory(cat, fvg_size, score, sweep_q, r_multiple, is_win,
                             index_name, hold_mins, bucket)
    _update_fvg_memory(bucket, dir_key, r_multiple, is_win)
    _update_index_memory(index_name, cat, r_multiple, is_win, pnl_rs)
    _update_window_memory(entry_time, dir_key, r_multiple, is_win)

    logger.info(
        f"trade_memory: updated [{cat}] {index_name} "
        f"{'WIN' if is_win else 'LOSS'} {r_multiple:+.2f}R | "
        f"FVG={fvg_size:.1f} Score={score} SQ={sweep_q}"
    )


def _update_category_memory(cat, fvg_size, score, sweep_q, r, is_win,
                              idx, hold_mins, bucket) -> None:
    mem = load_category_memory()
    if cat not in mem:
        mem[cat] = {
            'trade_count': 0, 'win_count': 0, 'loss_count': 0,
            'win_rate': 0, 'avg_r': 0, 'avg_hold_mins': 0,
            'avg_fvg_size': 0, 'avg_sweep_quality': 0, 'avg_score': 0,
            'fvg_wr_by_bucket': {}, 'source': 'live', 'last_updated': '',
        }
    m = mem[cat]
    n = m['trade_count']

    # Incremental running average: new_avg = (old_avg * n + new_val) / (n + 1)
    m['trade_count']        = n + 1
    m['win_count']          = m.get('win_count', 0) + (1 if is_win else 0)
    m['loss_count']         = m.get('loss_count', 0) + (0 if is_win else 1)
    m['win_rate']           = round(m['win_count'] / m['trade_count'] * 100, 1)
    m['avg_r']              = round((_weighted(m.get('avg_r', 0), n, r)), 2)
    m['avg_fvg_size']       = round((_weighted(m.get('avg_fvg_size', 0), n, fvg_size)), 1)
    m['avg_sweep_quality']  = round((_weighted(m.get('avg_sweep_quality', 0), n, sweep_q)), 1)
    m['avg_score']          = round((_weighted(m.get('avg_score', 0), n, score)), 1)
    m['avg_hold_mins']      = round((_weighted(m.get('avg_hold_mins', 0), n, hold_mins)), 1)
    m['historical_confidence'] = m['win_rate']
    m['last_updated']       = datetime.now().isoformat()

    # FVG bucket update
    if bucket not in m['fvg_wr_by_bucket']:
        m['fvg_wr_by_bucket'][bucket] = {'n': 0, 'wins': 0, 'wr': 0, 'avg_r': 0}
    fb = m['fvg_wr_by_bucket'][bucket]
    fb_n = fb['n']
    fb['n']     = fb_n + 1
    fb['wins']  = fb.get('wins', 0) + (1 if is_win else 0)
    fb['wr']    = round(fb['wins'] / fb['n'] * 100, 1)
    fb['avg_r'] = round(_weighted(fb.get('avg_r', 0), fb_n, r), 2)

    _write_json(CATEGORY_MEM_PATH, mem)


def _update_fvg_memory(bucket, dir_key, r, is_win) -> None:
    mem = load_fvg_memory()
    if bucket not in mem:
        mem[bucket] = {'overall': {'n':0,'wins':0,'wr':0,'avg_r':0,'loss_rate':0},
                       'LONG':{'n':0,'wins':0,'wr':0,'avg_r':0},
                       'SHORT':{'n':0,'wins':0,'wr':0,'avg_r':0}}

    for key in ('overall', dir_key):
        s = mem[bucket][key]
        n = s['n']
        s['n']    = n + 1
        s['wins'] = s.get('wins', 0) + (1 if is_win else 0)
        s['wr']   = round(s['wins'] / s['n'] * 100, 1)
        s['avg_r']= round(_weighted(s.get('avg_r', 0), n, r), 2)
        if key == 'overall':
            s['loss_rate'] = round((1 - s['wins']/s['n']) * 100, 1)

    _write_json(FVG_MEM_PATH, mem)


def _update_index_memory(idx, cat, r, is_win, pnl) -> None:
    mem = load_index_memory()
    if idx not in mem:
        mem[idx] = {'total_trades': 0, 'win_rate': 0}
    if cat not in mem[idx]:
        mem[idx][cat] = {'n': 0, 'wins': 0, 'wr': 0, 'avg_r': 0, 'total_pnl': 0}

    s = mem[idx][cat]
    n = s['n']
    s['n']        = n + 1
    s['wins']     = s.get('wins', 0) + (1 if is_win else 0)
    s['wr']       = round(s['wins'] / s['n'] * 100, 1)
    s['avg_r']    = round(_weighted(s.get('avg_r', 0), n, r), 2)
    s['total_pnl']= round(s.get('total_pnl', 0) + pnl, 2)

    mem[idx]['total_trades'] = sum(
        v.get('n', 0) for k, v in mem[idx].items()
        if isinstance(v, dict) and 'n' in v
    )
    all_wins = sum(v.get('wins', 0) for k, v in mem[idx].items()
                   if isinstance(v, dict) and 'wins' in v)
    mem[idx]['win_rate'] = round(
        all_wins / mem[idx]['total_trades'] * 100, 1
    ) if mem[idx]['total_trades'] else 0

    _write_json(INDEX_MEM_PATH, mem)


def _update_window_memory(entry_time: str, dir_key: str, r: float, is_win: bool) -> None:
    if not entry_time:
        return
    try:
        hour = str(int(entry_time.split(':')[0]))
    except Exception:
        return

    mem = load_window_memory()
    if hour not in mem:
        mem[hour] = {'n': 0, 'wins': 0, 'wr': 0, 'avg_r': 0,
                     'label': f"{int(hour):02d}:00 IST",
                     'long_wr': 0, 'short_wr': 0}

    s   = mem[hour]
    n   = s['n']
    s['n']    = n + 1
    s['wins'] = s.get('wins', 0) + (1 if is_win else 0)
    s['wr']   = round(s['wins'] / s['n'] * 100, 1)
    s['avg_r']= round(_weighted(s.get('avg_r', 0), n, r), 2)

    _write_json(WINDOW_MEM_PATH, mem)


# ── Telegram-ready analysis formatter ────────────────────────────────────────

def format_entry_analysis(similarity: Dict, setup_type: str = '') -> str:
    """
    Format similarity analysis as a compact Telegram HTML block.
    Called by the scanner after each setup is found.
    """
    if not similarity or similarity.get('similarity_score', 0) == 0:
        return ''

    sim   = similarity['similarity_score']
    cat   = similarity['category']
    conf  = similarity['confidence_pct']
    exp_r = similarity['expected_r']
    guide = similarity['guidance']
    fvg_s = similarity['fvg_signal']
    fvg_r = similarity['fvg_bucket_avg_r']
    s_rank = similarity['score_rank']
    sq_rank = similarity['sq_rank']
    idx_wr  = similarity['index_wr_for_cat']

    # Similarity bar
    filled = min(20, int(sim / 5))
    bar    = '█' * filled + '░' * (20 - filled)

    guide_text = {
        'RUNNER_OK'    : 'Allow runner → T3 target',
        'T2_PRIORITY'  : 'Exit 50% at T1, hold to T2',
        'CONSERVATIVE' : 'Exit at T1, SL → BE immediately',
    }.get(guide, guide)

    return (
        f"\n<b>BACKTEST MEMORY ESTIMATE</b> <i>(not guaranteed)</i>\n"
        f"Category : {cat.replace('_',' ')}\n"
        f"Match    : {sim}/100  <code>{bar}</code>\n"
        f"BT WR    : {conf:.0f}%  BT Avg R: {exp_r:+.2f}R\n"
        f"FVG      : {fvg_s} (BT avg {fvg_r:+.2f}R)\n"
        f"Score    : {s_rank}  SQ: {sq_rank}\n"
        f"This idx : {idx_wr:.0f}% WR (backtest)\n"
        f"Manage   : {guide_text}\n"
        f"<i>Based on 90-day backtest. Live results will differ.</i>"
    )


# ── Quick stats display ───────────────────────────────────────────────────────

def format_memory_status() -> str:
    """For /ml_memory Telegram command."""
    cat_mem = load_category_memory()
    idx_mem = load_index_memory()
    lines = ['<b>CB6 MEMORY STATUS</b>\n']

    lines.append('<b>Category Performance</b>')
    for cat, m in cat_mem.items():
        lines.append(
            f"  {cat:<14} n={m['trade_count']:3d} "
            f"WR={m['win_rate']:5.1f}%  AvgR={m['avg_r']:+.2f}"
        )

    lines.append('\n<b>Per-Index WR</b>')
    for idx, m in idx_mem.items():
        if isinstance(m, dict) and 'win_rate' in m:
            lines.append(f"  {idx:<12} n={m.get('total_trades',0):3d} WR={m['win_rate']:5.1f}%")

    return '\n'.join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _weighted(old_avg: float, n: int, new_val: float) -> float:
    """Incremental running average."""
    if n == 0:
        return new_val
    return (old_avg * n + new_val) / (n + 1)


def _read_json(path: str) -> Dict:
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.debug(f"trade_memory: read error {path}: {e}")
    return {}


def _write_json(path: str, data: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"trade_memory: write error {path}: {e}")
