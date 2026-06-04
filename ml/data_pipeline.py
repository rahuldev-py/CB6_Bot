# ml/data_pipeline.py
#
# Feature extraction and dataset preparation for CB6 ML models.
# Reads JSONL trade logs, joins ENTRY + OUTCOME by trade_id,
# returns clean feature matrices for DNN / CNN / RNN training.
#
# RULES:
#   - Never imports from trader/, forex_engine/ — data only
#   - Never places or modifies any orders
#   - NSE and Forex data handled separately throughout

from __future__ import annotations
import os, json
import numpy as np
import pandas as pd
from typing import Tuple, Optional
from utils.logger import logger

# ── Paths ──────────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _jsonl_path(market: str, account: str = '') -> str:
    fname = f"{account}_trades.jsonl" if account else "trades.jsonl"
    return os.path.join(_ROOT, 'data', 'ml', market, fname)


# ── Categorical encodings ──────────────────────────────────────────────────────
_DIRECTION  = {'BULLISH': 1, 'BUY': 1, 'BEARISH': -1, 'SELL': -1, 'UNKNOWN': 0}
_MSS        = {'CHOCH': 1,  'BOS': 0,  'UNKNOWN': -1}
_BIAS       = {'BULLISH': 1, 'BEARISH': -1, 'RANGING': 0, 'UNKNOWN': 0}
_SWEEP      = {'HIGH_SWEEP': 1, 'LOW_SWEEP': -1, 'NONE': 0, 'UNKNOWN': 0}
_SESSION_NSE= {'morning_open': 0, 'mid_morning': 1, 'afternoon': 2, 'pre_close': 3, 'other': -1}
_SESSION_FX = {'london_open': 0, 'london_mid': 1, 'between_sessions': 2,
               'ny_open': 3, 'ny_mid': 4, 'rollover': -1, 'asia': -2, 'off_hours': -3}


def _enc(val, mapping: dict, default=0) -> float:
    return float(mapping.get(str(val).upper(), mapping.get(val, default)))


# ── Raw JSONL loader ───────────────────────────────────────────────────────────

def load_raw(market: str, account: str = '') -> pd.DataFrame:
    """
    Load all records from a JSONL file.
    Returns a DataFrame with one row per line (both ENTRY and OUTCOME types).
    """
    path = _jsonl_path(market, account)
    if not os.path.exists(path):
        return pd.DataFrame()
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def join_trades(market: str, account: str = '') -> pd.DataFrame:
    """
    Join ENTRY and OUTCOME records by trade_id.
    Returns one row per completed trade with all features + outcome.
    Trades without an OUTCOME record are dropped (still open or data missing).
    """
    raw = load_raw(market, account)
    if raw.empty:
        return pd.DataFrame()

    entries  = raw[raw['_type'] == 'ENTRY'].copy()
    outcomes = raw[raw['_type'] == 'OUTCOME'].copy()

    if entries.empty or outcomes.empty:
        return pd.DataFrame()

    # Expand outcome dict into columns
    outcomes = outcomes[['trade_id', 'outcome']].copy()
    outcomes['outcome'] = outcomes['outcome'].apply(
        lambda x: x if isinstance(x, dict) else {}
    )
    out_exp = pd.json_normalize(outcomes['outcome'])
    out_exp['trade_id'] = outcomes['trade_id'].values

    merged = entries.merge(out_exp, on='trade_id', how='inner')
    logger.info(f"ML pipeline [{market}/{account or 'all'}]: "
                f"{len(merged)} completed trades loaded")
    return merged


# ── NSE Feature matrix ─────────────────────────────────────────────────────────

NSE_FEATURES = [
    # ICT structure
    'direction_enc', 'mss_type_enc', 'sweep_type_enc', 'sweep_confirmed',
    'sweep_candles_ago', 'sweep_confidence', 'sweep_wick_ratio',
    'sweep_volume_spike', 'sweep_atr_expansion', 'sweep_displacement',
    'violated_liquidity_count', 'fvg_size', 'fvg_in_discount', 'dol_mss_match',
    # Score
    'score',
    # HTF
    'h1_bias_enc', 'h4_bias_enc',
    # UT Bot
    'ut_bot_aligned',
    # Brain
    'brain_score', 'brain_confidence', 'brain_gate',
    # RR geometry
    'sl_distance', 'rr_t1', 'rr_t2', 'rr_t3',
    # Time
    'ist_hour', 'day_of_week', 'session_enc',
    # in_fvg / in_ote
    'in_fvg', 'in_ote',
]

def build_nse_features(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns (X, y_class, y_r) where:
      X        — (N, F) float32 feature matrix
      y_class  — (N,) binary  1=WIN  0=LOSS
      y_r      — (N,) float32 R-multiple achieved
    """
    d = df.copy()

    d['direction_enc']  = d['direction'].apply(lambda v: _enc(v, _DIRECTION))
    d['mss_type_enc']   = d['mss_type'].apply(lambda v: _enc(v, _MSS))
    d['sweep_type_enc'] = d['sweep_type'].apply(lambda v: _enc(v, _SWEEP))
    d['h1_bias_enc']    = d['h1_bias'].apply(lambda v: _enc(v, _BIAS))
    d['h4_bias_enc']    = d['h4_bias'].apply(lambda v: _enc(v, _BIAS))
    d['session_enc']    = d['session'].apply(lambda v: _enc(v, _SESSION_NSE))

    bool_cols = ['sweep_confirmed','fvg_in_discount','dol_mss_match',
                 'ut_bot_aligned','in_fvg','in_ote']
    for c in bool_cols:
        if c in d.columns:
            d[c] = d[c].fillna(False).astype(float)
        else:
            d[c] = 0.0

    for c in NSE_FEATURES:
        if c not in d.columns:
            d[c] = 0.0
        d[c] = pd.to_numeric(d[c], errors='coerce').fillna(0.0)

    X       = d[NSE_FEATURES].values.astype(np.float32)
    y_class = (d['result'].str.upper() == 'WIN').astype(np.float32).values
    y_r     = pd.to_numeric(d.get('r_multiple', pd.Series([0]*len(d))),
                             errors='coerce').fillna(0.0).values.astype(np.float32)
    return X, y_class, y_r


# ── Forex Feature matrix ───────────────────────────────────────────────────────

FOREX_FEATURES = [
    # ICT structure
    'direction_enc', 'mss_type_enc', 'sweep_type_enc', 'sweep_confirmed',
    'sweep_candles_ago', 'fvg_size', 'fvg_in_discount', 'dol_mss_match',
    # Score
    'score',
    # HTF
    'h1_bias_enc', 'h4_bias_enc', 'h1_aligned', 'h4_aligned', 'both_htf_aligned',
    # UT Bot + OB
    'ut_bot_aligned', 'ob_present',
    # A+ template
    'aplus_sim_ratio', 'aplus_lot_boost', 'is_aplus',
    # RR geometry
    'sl_distance', 'rr_t1', 'rr_t2', 'rr_t3',
    # Session
    'session_enc', 'in_kill_zone', 'utc_hour', 'day_of_week',
    # Risk context
    'spread_at_entry',
    # in_fvg
    'in_fvg',
]

def build_forex_features(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    d = df.copy()

    d['direction_enc']  = d['direction'].apply(lambda v: _enc(v, _DIRECTION))
    d['mss_type_enc']   = d['mss_type'].apply(lambda v: _enc(v, _MSS))
    d['sweep_type_enc'] = d['sweep_type'].apply(lambda v: _enc(v, _SWEEP))
    d['h1_bias_enc']    = d['h1_bias'].apply(lambda v: _enc(v, _BIAS))
    d['h4_bias_enc']    = d['h4_bias'].apply(lambda v: _enc(v, _BIAS))
    d['session_enc']    = d['session'].apply(lambda v: _enc(v, _SESSION_FX))

    bool_cols = ['sweep_confirmed','fvg_in_discount','dol_mss_match',
                 'ut_bot_aligned','ob_present','h1_aligned','h4_aligned',
                 'both_htf_aligned','is_aplus','in_kill_zone','in_fvg']
    for c in bool_cols:
        if c in d.columns:
            d[c] = d[c].fillna(False).astype(float)
        else:
            d[c] = 0.0

    for c in FOREX_FEATURES:
        if c not in d.columns:
            d[c] = 0.0
        d[c] = pd.to_numeric(d[c], errors='coerce').fillna(0.0)

    X       = d[FOREX_FEATURES].values.astype(np.float32)
    y_class = (d['result'].str.upper() == 'WIN').astype(np.float32).values
    y_r     = pd.to_numeric(d.get('r_multiple', pd.Series([0]*len(d))),
                             errors='coerce').fillna(0.0).values.astype(np.float32)
    return X, y_class, y_r


# ── Price series collector (for CNN/RNN) ───────────────────────────────────────

def save_price_series(trade_id: str, market: str, account: str,
                      candles: list, n_before: int = 50) -> bool:
    """
    Save last N candles before trade entry as raw JSON for CNN/RNN training.
    candles: list of dicts with keys open/high/low/close/volume
    """
    try:
        out_dir = os.path.join(_ROOT, 'data', 'ml', market,
                               'price_series', account or 'all')
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{trade_id}.json")
        series = candles[-n_before:] if len(candles) > n_before else candles
        with open(path, 'w') as f:
            json.dump({'trade_id': trade_id, 'candles': series}, f)
        return True
    except Exception as e:
        logger.error(f"ML price_series save error: {e}")
        return False


def load_price_series(trade_id: str, market: str, account: str = '') -> Optional[np.ndarray]:
    """
    Load saved price series for a trade.
    Returns (N, 5) array: open/high/low/close/volume  or None.
    """
    try:
        path = os.path.join(_ROOT, 'data', 'ml', market,
                            'price_series', account or 'all', f"{trade_id}.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            data = json.load(f)
        candles = data.get('candles', [])
        arr = np.array([[c.get('open',0), c.get('high',0), c.get('low',0),
                         c.get('close',0), c.get('volume',0)] for c in candles],
                       dtype=np.float32)
        return arr
    except Exception as e:
        logger.error(f"ML price_series load error: {e}")
        return None


def get_dataset_stats(market: str, account: str = '') -> dict:
    """Return quick stats about available training data."""
    df = join_trades(market, account)
    if df.empty:
        return {'total': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0}
    wins   = int((df['result'].str.upper() == 'WIN').sum())
    losses = int(len(df) - wins)
    return {
        'total'   : len(df),
        'wins'    : wins,
        'losses'  : losses,
        'win_rate': round(wins / len(df) * 100, 1) if len(df) else 0.0,
        'avg_r'   : round(float(pd.to_numeric(df.get('r_multiple', pd.Series()),
                                               errors='coerce').mean()), 3),
    }
