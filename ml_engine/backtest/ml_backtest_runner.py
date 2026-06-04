"""
ml_engine/backtest/ml_backtest_runner.py

CB6 Quantum — ML-Enhanced Strategy Backtest
============================================
Runs the full Silver Bullet ICT scanner over historical data and reports
comprehensive performance metrics, with optional ML confidence filtering.

DATA SOURCES
  NSE  : Fyers 5-min — max 100 days (broker API hard cap)
  Forex: MT5 15-min  — up to 2-3 years

THREE FILTER LAYERS (shown side by side)
  1. ALL  — every setup the scanner fires (base rule engine)
  2. HIGH — setups with confluence score ≥ 14/26 (A/B quality rule filter)
  3. ML   — setups in DNN/LSTM confidence A or A+ bucket (loads trained models)

METRICS REPORTED
  total trades | win rate | long count+WR | short count+WR
  avg profit(R) | avg loss(R) | avg RR | profit factor
  max consecutive losses | max drawdown (R) | which direction is better

CNN NOTE
  CNN is research-only — it needs real OHLCV candle images linked to trade
  outcomes which don't exist yet. CNN is excluded from this backtest.

Usage:
  python -m ml_engine.backtest.ml_backtest_runner
  python -m ml_engine.backtest.ml_backtest_runner --engine nse --days 100
  python -m ml_engine.backtest.ml_backtest_runner --engine forex --days 500
  python -m ml_engine.backtest.ml_backtest_runner --engine all --telegram
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
import pandas as pd

REPORT_DIR = Path("ml_engine/backtest/reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ── NSE symbols (index futures, options-only mode) ────────────────────────────
NSE_SYMBOLS = {
    'NSE:NIFTY50-INDEX' : 'NIFTY',
    'NSE:NIFTYBANK-INDEX': 'BANKNIFTY',
    'NSE:FINNIFTY-INDEX' : 'FINNIFTY',
}

# ── Forex symbols (MT5) ───────────────────────────────────────────────────────
FOREX_SYMBOLS = ['XAUUSD', 'XAGUSD', 'EURUSD', 'GBPUSD', 'USOIL', 'USDJPY']

# ── Score gate (rule-based HIGH filter) ──────────────────────────────────────
RULE_SCORE_GATE = 14      # ≥14/26 = A/B quality
ML_BUCKET_GATE  = {'A+', 'A'}   # only A and A+ bucket trades


# ═══════════════════════════════════════════════════════════════════════════════
# Trade outcome simulation (T1/T2/T3 partial booking)
# ═══════════════════════════════════════════════════════════════════════════════

def _simulate_outcome(df: pd.DataFrame, entry_idx: int, sig: dict,
                      direction: str, max_bars: int = 200) -> dict:
    """
    Walk-forward from entry_idx.  Partial booking: 1/3 at T1, 1/3 at T2, 1/3 at T3.
    SL trails to breakeven after T1 hit.
    Returns dict: result, r_multiple, targets_hit, exit_price, pnl_pts, is_win.
    """
    entry     = sig['entry']
    sl        = sig['stop_loss']
    t1        = sig['target1']
    t2        = sig['target2']
    t3        = sig['target3']
    risk      = abs(entry - sl)
    if risk <= 0:
        return {'result': 'INVALID', 'r_multiple': 0.0, 'targets_hit': [],
                'exit_price': entry, 'pnl_pts': 0.0, 'is_win': False}

    current_sl  = sl
    targets_hit = []
    result      = 'TIMEOUT'
    pnl_pts     = 0.0
    remaining   = 1.0
    exit_price  = float(df['close'].iloc[-1])

    end = min(entry_idx + max_bars, len(df))
    for i in range(entry_idx + 1, end):
        high = float(df['high'].iloc[i])
        low  = float(df['low'].iloc[i])

        if direction == 'BULLISH':
            if low <= current_sl:
                result     = 'SL_HIT'
                exit_price = current_sl
                break
            if 'T1' not in targets_hit and high >= t1:
                targets_hit.append('T1')
                pnl_pts   += 0.333 * (t1 - entry)
                remaining -= 0.333
                current_sl = entry
            if 'T2' not in targets_hit and high >= t2:
                targets_hit.append('T2')
                pnl_pts   += 0.333 * (t2 - entry)
                remaining -= 0.333
            if high >= t3:
                targets_hit.append('T3')
                pnl_pts   += remaining * (t3 - entry)
                remaining   = 0.0
                result      = 'TARGET_HIT'
                exit_price  = t3
                break
        else:   # BEARISH
            if high >= current_sl:
                result     = 'SL_HIT'
                exit_price = current_sl
                break
            if 'T1' not in targets_hit and low <= t1:
                targets_hit.append('T1')
                pnl_pts   += 0.333 * (entry - t1)
                remaining -= 0.333
                current_sl = entry
            if 'T2' not in targets_hit and low <= t2:
                targets_hit.append('T2')
                pnl_pts   += 0.333 * (entry - t2)
                remaining -= 0.333
            if low <= t3:
                targets_hit.append('T3')
                pnl_pts   += remaining * (entry - t3)
                remaining   = 0.0
                result      = 'TARGET_HIT'
                exit_price  = t3
                break

    # TIMEOUT — close at market
    if remaining > 0:
        move     = (exit_price - entry) if direction == 'BULLISH' else (entry - exit_price)
        pnl_pts += remaining * move

    r_multiple = round(pnl_pts / risk, 3)
    return {
        'result'      : result,
        'r_multiple'  : r_multiple,
        'targets_hit' : targets_hit,
        'exit_price'  : exit_price,
        'pnl_pts'     : round(pnl_pts, 2),
        'is_win'      : pnl_pts > 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ML confidence scoring (setup metadata → model inference)
# ═══════════════════════════════════════════════════════════════════════════════

_ml_models_cache: dict = {}


def _load_ml_models(engine: str) -> dict:
    """Load DNN + LSTM for the given engine from registry. Returns {} if not ready."""
    if engine in _ml_models_cache:
        return _ml_models_cache[engine]

    models = {}
    try:
        registry_path = Path("ml_engine/config/model_registry.json")
        if not registry_path.exists():
            return {}
        with open(registry_path) as f:
            registry = json.load(f)

        for model_type, cls_name, module in [
            ('dnn', 'DNNTradeScorer',  'ml_engine.models.dnn_trade_scorer'),
            ('lstm', 'RNNTradeScorer', 'ml_engine.models.rnn_sequence_model'),
        ]:
            # Find best matching registry key
            best_key  = None
            best_auc  = 0.0
            best_path = None
            for key, entry in registry.get('models', {}).items():
                if engine not in key or model_type not in key:
                    continue
                for ver in reversed(entry.get('versions', [])):
                    auc  = ver.get('auc', 0.0) or 0.0
                    path = ver.get('model_path', '')
                    if auc >= 0.50 and path and Path(path).exists():
                        if auc > best_auc:
                            best_auc  = auc
                            best_path = path
                            best_key  = key
                        break

            if best_path:
                import importlib
                mod  = importlib.import_module(module)
                cls  = getattr(mod, cls_name)
                m    = cls.__new__(cls)
                m.device = 'cpu'
                loaded   = cls.load(best_path)
                models[model_type] = {
                    'model': loaded,
                    'auc'  : best_auc,
                    'key'  : best_key,
                }
                print(f"  ML {model_type.upper()} loaded: AUC={best_auc:.4f}  [{best_key}]")
            else:
                print(f"  ML {model_type.upper()}: no trained model with AUC≥0.50 for {engine}")
    except Exception as e:
        print(f"  ML model load error: {e}")

    _ml_models_cache[engine] = models
    return models


def _setup_to_features(setup: dict, _seq_history: Optional[np.ndarray] = None) -> np.ndarray:
    """
    Extract a fixed-length feature vector from a scanner setup dict.
    Maps setup metadata to the same feature space used during training.
    Missing/unknown fields default to 0 or neutral value.
    Returns shape (N_FEATURES,).
    """
    d   = setup.get('direction', 'BULLISH')
    mss = setup.get('mss_type', 'BOS')
    fvg = setup.get('fvg', {})
    sig = setup.get('entry_signal', {})
    dol = setup.get('dol', {})

    bull = 1.0 if d == 'BULLISH' else 0.0

    h1  = setup.get('h1_bias', 'RANGING')
    h4  = setup.get('h4_bias', 'RANGING')
    h1a = 1.0 if h1 == d else (0.5 if h1 == 'RANGING' else 0.0)
    h4a = 1.0 if h4 == d else (0.5 if h4 == 'RANGING' else 0.0)

    reg = setup.get('regime', 'NEUTRAL')
    reg_num = {'TRENDING': 1.0, 'NEUTRAL': 0.5, 'CHOPPY': 0.0}.get(reg, 0.5)

    dte = min(float(setup.get('dte', 7)), 30.0) / 30.0
    rr  = min(float(sig.get('rr_ratio', 2.0)), 10.0) / 10.0
    score_norm = float(setup.get('confluence', 10)) / 26.0

    fvg_size = float(fvg.get('size', 10.0))
    fvg_mid  = float(fvg.get('mid', 1.0)) or 1.0
    fvg_norm = min(fvg_size / fvg_mid, 0.01) / 0.01   # normalise to 0-1

    feat = np.array([
        bull,                                           # direction
        1.0 if mss == 'CHOCH' else 0.0,                # mss_choch
        1.0 if fvg.get('displacement') else 0.0,       # fvg_displacement
        1.0 if setup.get('sweep_confirmed') else 0.0,  # liquidity_sweep
        1.0 if setup.get('ob_confluence') else 0.0,    # ob_present
        1.0 if dol.get('is_eqh_eql') else 0.0,        # dol_eqh_eql
        1.0 if setup.get('three_bar') else 0.0,        # three_bar_reversal
        1.0 if setup.get('double_ob_test') else 0.0,   # double_ob
        1.0 if setup.get('in_fvg') else 0.0,           # in_fvg
        h1a,                                            # h1_alignment
        h4a,                                            # h4_alignment
        reg_num,                                        # regime
        dte,                                            # dte_normalised
        rr,                                             # rr_normalised
        score_norm,                                     # confluence_norm
        fvg_norm,                                       # fvg_size_norm
    ], dtype=np.float32)

    return feat


def _ml_score_setup(setup: dict, models: dict) -> dict:
    """
    Score a setup through DNN + LSTM. Returns:
        {'dnn_wp': float, 'lstm_wp': float, 'ensemble_wp': float, 'bucket': str}
    """
    feat   = _setup_to_features(setup)
    scores = {}

    for mtype in ['dnn', 'lstm']:
        if mtype not in models:
            continue
        try:
            m      = models[mtype]['model']
            X_fake = np.tile(feat, (1, 1))     # (1, F) for DNN
            pred   = m.predict(X_fake)
            wp     = float(pred.get('win_probability', [0.5])[0])
            scores[f'{mtype}_wp'] = round(wp, 4)
        except Exception:
            pass

    if not scores:
        return {'dnn_wp': None, 'lstm_wp': None, 'ensemble_wp': None, 'bucket': 'C'}

    wps = list(scores.values())
    ens = float(np.mean(wps))
    scores['ensemble_wp'] = round(ens, 4)

    # Confidence bucket (same as live confidence_engine.py)
    conf = abs(ens - 0.5) * 2
    if conf >= 0.60:
        bucket = 'A+'
    elif conf >= 0.40:
        bucket = 'A'
    elif conf >= 0.20:
        bucket = 'B'
    else:
        bucket = 'C'

    # Win probability floor per bucket
    if bucket == 'A+' and ens < 0.70:
        bucket = 'A'
    if bucket == 'A' and ens < 0.60:
        bucket = 'B'

    scores['bucket'] = bucket
    return scores


# ═══════════════════════════════════════════════════════════════════════════════
# Statistics computation
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_stats(trades: List[dict], label: str) -> dict:
    """Full metrics for a list of trade dicts (each has r_multiple, direction, is_win)."""
    if not trades:
        return {'label': label, 'n': 0}

    n        = len(trades)
    wins     = [t for t in trades if t['is_win']]
    losses   = [t for t in trades if not t['is_win']]
    longs    = [t for t in trades if t['direction'] == 'BULLISH']
    shorts   = [t for t in trades if t['direction'] == 'BEARISH']
    long_w   = [t for t in longs  if t['is_win']]
    short_w  = [t for t in shorts if t['is_win']]

    r_vals    = [t['r_multiple'] for t in trades]
    r_wins    = [t['r_multiple'] for t in wins]
    r_losses  = [t['r_multiple'] for t in losses]

    wr         = len(wins) / n * 100
    avg_profit = float(np.mean(r_wins))   if r_wins   else 0.0
    avg_loss   = float(np.mean(r_losses)) if r_losses else 0.0
    avg_rr     = float(np.mean(r_vals))
    gross_p    = sum(r for r in r_vals if r > 0)
    gross_l    = abs(sum(r for r in r_vals if r < 0))
    pf         = round(gross_p / gross_l, 3) if gross_l > 0 else float('inf')

    # Max consecutive losses
    max_consec_loss = 0
    consec = 0
    for t in trades:
        if not t['is_win']:
            consec += 1
            max_consec_loss = max(max_consec_loss, consec)
        else:
            consec = 0

    # Max drawdown in R
    cum_r    = np.cumsum(r_vals)
    peak_r   = np.maximum.accumulate(cum_r)
    drawdown = peak_r - cum_r
    max_dd_r = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0

    # Direction comparison
    long_wr  = len(long_w)  / len(longs)  * 100 if longs  else 0.0
    short_wr = len(short_w) / len(shorts) * 100 if shorts else 0.0
    long_avg_r  = float(np.mean([t['r_multiple'] for t in longs]))  if longs  else 0.0
    short_avg_r = float(np.mean([t['r_multiple'] for t in shorts])) if shorts else 0.0
    better_dir  = 'LONG' if long_avg_r >= short_avg_r else 'SHORT'

    return {
        'label'         : label,
        'n'             : n,
        'wins'          : len(wins),
        'losses'        : len(losses),
        'wr'            : round(wr, 1),
        'avg_profit_r'  : round(avg_profit, 3),
        'avg_loss_r'    : round(avg_loss, 3),
        'avg_rr'        : round(avg_rr, 3),
        'profit_factor' : pf,
        'total_r'       : round(sum(r_vals), 2),
        'max_consec_loss': max_consec_loss,
        'max_dd_r'      : round(max_dd_r, 2),
        'long_n'        : len(longs),
        'long_wins'     : len(long_w),
        'long_wr'       : round(long_wr, 1),
        'long_avg_r'    : round(long_avg_r, 3),
        'short_n'       : len(shorts),
        'short_wins'    : len(short_w),
        'short_wr'      : round(short_wr, 1),
        'short_avg_r'   : round(short_avg_r, 3),
        'better_dir'    : better_dir,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# NSE Backtest
# ═══════════════════════════════════════════════════════════════════════════════

def run_nse_backtest(days: int = 100, ml_models: Optional[dict] = None) -> List[dict]:
    """
    Run Silver Bullet backtest on NSE indices.
    Fetches Fyers 5-min data (max 100 days per Fyers API limit).
    Returns list of trade dicts with outcome + ML scores.
    """
    from dotenv import load_dotenv
    load_dotenv()

    token = os.getenv('ACCESS_TOKEN', '')
    if not token or ':' not in token:
        print("  NSE: No ACCESS_TOKEN — skipping (run broker/web_token.py first)")
        return []

    try:
        from fyers_apiv3 import fyersModel
        client_id = token.split(':')[0]
        fyers = fyersModel.FyersModel(
            client_id=client_id, token=token, is_async=False, log_path=''
        )
    except Exception as e:
        print(f"  NSE: Fyers init failed: {e}")
        return []

    from scanner.silver_bullet import scan_silver_bullet
    from scanner.index_futures  import get_active_futures
    from scanner.data_fetcher   import get_historical_data

    try:
        futures = get_active_futures()
        symbols = {
            futures['NIFTY']      : 'NIFTY',
            futures['BANKNIFTY']  : 'BANKNIFTY',
            futures['FINNIFTY']   : 'FINNIFTY',
            futures['MIDCPNIFTY'] : 'MIDCPNIFTY',
        }
    except Exception:
        symbols = NSE_SYMBOLS

    all_trades: List[dict] = []
    days_capped = min(days, 100)

    for symbol, name in symbols.items():
        print(f"\n  [{name}] fetching {days_capped}d × 5-min...")
        df = get_historical_data(fyers, symbol, '5', days=days_capped)
        if df is None or len(df) < 80:
            print(f"  [{name}] insufficient data ({0 if df is None else len(df)} bars) — skip")
            continue

        print(f"  [{name}] {len(df)} bars | scanning setups...")
        seen = set()
        trades_this = 0

        for end_idx in range(80, len(df) - 20, 3):
            window = df.iloc[:end_idx + 1].copy().reset_index(drop=True)
            try:
                setup = scan_silver_bullet(window, symbol, tf='5',
                                           fyers=fyers, force=True)
            except Exception:
                continue
            if not setup:
                continue

            sig = setup.get('entry_signal', {})
            if not sig.get('entry'):
                continue

            direction = setup.get('direction', '')
            fvg_key   = round(sig.get('fvg_low', 0) / 50) * 50
            ts_str    = str(df['timestamp'].iloc[end_idx])[:10]
            dedup     = (ts_str, symbol, direction, fvg_key)
            if dedup in seen:
                continue
            seen.add(dedup)

            outcome = _simulate_outcome(df, end_idx, sig, direction)
            if outcome['result'] == 'INVALID':
                continue

            ml_scores = _ml_score_setup(setup, ml_models or {})

            rec = {
                'market'      : 'NSE',
                'symbol'      : name,
                'date'        : ts_str,
                'time'        : str(df['timestamp'].iloc[end_idx])[11:16],
                'direction'   : direction,
                'score'       : setup.get('confluence', 0),
                'mss_type'    : setup.get('mss_type', 'BOS'),
                'h1_bias'     : setup.get('h1_bias', 'RANGING'),
                'h4_bias'     : setup.get('h4_bias', 'RANGING'),
                'sweep'       : setup.get('sweep_confirmed', False),
                'eqh_eql'     : setup.get('dol_is_eqh_eql', False),
                'ob'          : setup.get('ob_confluence', False),
                'entry'       : sig.get('entry'),
                'sl'          : sig.get('stop_loss'),
                'risk_pts'    : sig.get('risk'),
                'rr_planned'  : sig.get('rr_ratio'),
                **outcome,
                **{f'ml_{k}': v for k, v in ml_scores.items()},
            }
            all_trades.append(rec)
            trades_this += 1

        print(f"  [{name}] found {trades_this} setups")

    print(f"\n  NSE total: {len(all_trades)} trades across {len(symbols)} symbols")
    return all_trades


# ═══════════════════════════════════════════════════════════════════════════════
# Forex Backtest
# ═══════════════════════════════════════════════════════════════════════════════

def run_forex_backtest(days: int = 500, ml_models: Optional[dict] = None) -> List[dict]:
    """
    Run Silver Bullet backtest on Forex symbols via MT5.
    MT5 holds 2-3 years of 15-min data.
    """
    try:
        from forex_engine.mt5_adapter import MT5Adapter
        from forex_engine.forex_worker import scan_forex_setup
    except ImportError as e:
        print(f"  Forex: import error: {e}")
        return []

    adapter = MT5Adapter(paper=True)
    all_trades: List[dict] = []

    for symbol in FOREX_SYMBOLS:
        print(f"\n  [{symbol}] fetching {days}d × 15-min...")
        bars_needed = days * 96  # 96 × 15-min bars per day
        df = adapter.get_klines(symbol, '15m', min(bars_needed, 30000))
        if df is None or df.empty or len(df) < 200:
            print(f"  [{symbol}] no data — skip")
            continue

        df_h1 = adapter.get_klines(symbol, '1h',  min(days * 24 + 100, 5000))
        df_h4 = adapter.get_klines(symbol, '4h',  min(days * 6  + 50,  2000))
        print(f"  [{symbol}] {len(df)} bars | H1:{len(df_h1) if df_h1 is not None else 0} | H4:{len(df_h4) if df_h4 is not None else 0} | scanning...")

        from forex_engine.forex_worker import _get_h1_bias, _get_h4_bias
        seen = set()
        trades_this = 0

        for end_idx in range(100, len(df) - 20, 5):
            candle_dt = df.index[end_idx] if isinstance(df.index, pd.DatetimeIndex) \
                        else pd.Timestamp(df.index[end_idx])
            window = df.iloc[:end_idx + 1].copy()

            try:
                setup = scan_forex_setup(window, symbol)
            except Exception:
                continue
            if not setup:
                continue

            sig       = setup.get('entry_signal', {})
            direction = setup.get('direction', '')
            if not sig.get('entry'):
                continue

            # Compute H1/H4 bias from historical slices
            h1_bias = 'RANGING'
            h4_bias = 'RANGING'
            if df_h1 is not None and not df_h1.empty:
                try:
                    h1_slice = df_h1[df_h1.index <= candle_dt].tail(20)
                    if len(h1_slice) >= 8:
                        c = h1_slice['close'].astype(float)
                        fast = float(c.ewm(span=3, adjust=False).mean().iloc[-1])
                        slow = float(c.ewm(span=8, adjust=False).mean().iloc[-1])
                        if fast > slow * 1.0002:   h1_bias = 'BULLISH'
                        elif fast < slow * 0.9998: h1_bias = 'BEARISH'
                except Exception:
                    pass
            if df_h4 is not None and not df_h4.empty:
                try:
                    h4_slice = df_h4[df_h4.index <= candle_dt].tail(20)
                    if len(h4_slice) >= 8:
                        c = h4_slice['close'].astype(float)
                        fast = float(c.ewm(span=3, adjust=False).mean().iloc[-1])
                        slow = float(c.ewm(span=8, adjust=False).mean().iloc[-1])
                        if fast > slow * 1.0003:   h4_bias = 'BULLISH'
                        elif fast < slow * 0.9997: h4_bias = 'BEARISH'
                except Exception:
                    pass

            # Hard gate: H4 counter-trend → skip
            if h4_bias != 'RANGING' and h4_bias != direction:
                continue
            # Hard gate: H1 counter-trend → skip
            if h1_bias != 'RANGING' and h1_bias != direction:
                continue

            setup['h1_bias'] = h1_bias
            setup['h4_bias'] = h4_bias

            date_str = str(candle_dt)[:10]
            fvg_key  = round(sig.get('fvg_low', 0) * 10) / 10
            dedup    = (date_str, symbol, direction, fvg_key)
            if dedup in seen:
                continue
            seen.add(dedup)

            outcome = _simulate_outcome(df, end_idx, sig, direction)
            if outcome['result'] == 'INVALID':
                continue

            ml_scores = _ml_score_setup(setup, ml_models or {})

            rec = {
                'market'      : 'FOREX',
                'symbol'      : symbol,
                'date'        : date_str,
                'time'        : str(candle_dt)[11:16],
                'direction'   : direction,
                'score'       : setup.get('confluence', 0) or setup.get('score', 0),
                'mss_type'    : setup.get('mss_type', 'BOS'),
                'h1_bias'     : h1_bias,
                'h4_bias'     : h4_bias,
                'sweep'       : setup.get('sweep_confirmed', False),
                'eqh_eql'     : setup.get('dol_is_eqh_eql', False),
                'ob'          : setup.get('ob_confluence', False),
                'entry'       : sig.get('entry'),
                'sl'          : sig.get('stop_loss'),
                'risk_pts'    : sig.get('risk'),
                'rr_planned'  : sig.get('rr_ratio'),
                **outcome,
                **{f'ml_{k}': v for k, v in ml_scores.items()},
            }
            all_trades.append(rec)
            trades_this += 1

        print(f"  [{symbol}] found {trades_this} setups")

    print(f"\n  Forex total: {len(all_trades)} trades across {len(FOREX_SYMBOLS)} symbols")
    return all_trades


# ═══════════════════════════════════════════════════════════════════════════════
# Report formatting
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt_stats(s: dict) -> str:
    if s.get('n', 0) == 0:
        return f"  {s.get('label','?'):<20} : No trades"
    pf = s['profit_factor']
    pf_str = f"{pf:.3f}" if isinstance(pf, float) and pf != float('inf') else "inf"
    return (
        f"  {s['label']:<20} | "
        f"N={s['n']:>4}  WR={s['wr']:>5.1f}%  "
        f"AvgP={s['avg_profit_r']:>+6.3f}R  AvgL={s['avg_loss_r']:>+6.3f}R  "
        f"AvgRR={s['avg_rr']:>+6.3f}R  PF={pf_str:>6}  "
        f"TotalR={s['total_r']:>+6.2f}  MaxDD={s['max_dd_r']:.2f}R\n"
        f"  {'':<20} | "
        f"LONG  N={s['long_n']:>3}  WR={s['long_wr']:>5.1f}%  AvgR={s['long_avg_r']:>+6.3f}  "
        f"|| SHORT N={s['short_n']:>3}  WR={s['short_wr']:>5.1f}%  AvgR={s['short_avg_r']:>+6.3f}  "
        f">> BETTER: {s['better_dir']}"
    )


def _full_report(market: str, all_trades: List[dict]) -> str:
    if not all_trades:
        return f"\n{market}: No trades found.\n"

    # Three filter layers
    base_trades  = all_trades
    high_trades  = [t for t in all_trades if t['score'] >= RULE_SCORE_GATE]
    ml_trades    = [t for t in all_trades
                    if t.get('ml_bucket') in ML_BUCKET_GATE]

    stats_base  = _compute_stats(base_trades,  'ALL (rule-based)')
    stats_high  = _compute_stats(high_trades,  f'SCORE≥{RULE_SCORE_GATE} (A/B quality)')
    stats_ml    = _compute_stats(ml_trades,    'ML A/A+ bucket')

    # Per-symbol breakdown
    sym_lines = []
    symbols   = sorted(set(t['symbol'] for t in all_trades))
    for sym in symbols:
        st = _compute_stats([t for t in all_trades if t['symbol'] == sym], sym)
        if st['n'] >= 3:
            sym_lines.append(f"    {sym:<12} N={st['n']:>4}  WR={st['wr']:>5.1f}%  "
                             f"TotalR={st['total_r']:>+6.2f}  Better={st['better_dir']}")

    date_range = f"{min(t['date'] for t in all_trades)}  to  {max(t['date'] for t in all_trades)}"

    lines = [
        f"\n{'='*95}",
        f"  CB6 QUANTUM — {market} BACKTEST RESULTS",
        f"  Period : {date_range}   |   Total setups: {len(all_trades)}",
        f"{'='*95}",
        f"",
        f"  LAYER 1 — ALL rule-based setups (no ML filter)",
        _fmt_stats(stats_base),
        f"",
        f"  LAYER 2 — Rule score≥{RULE_SCORE_GATE}/26  (A/B quality filter only)",
        _fmt_stats(stats_high),
        f"",
        f"  LAYER 3 — ML confidence A or A+ bucket  (DNN+LSTM ensemble)",
    ]
    if not ml_trades:
        lines.append("  ML A/A+: No trades — models may not be trained yet (need AUC≥0.50)")
    else:
        lines.append(_fmt_stats(stats_ml))

    lines += [
        f"",
        f"  PER-SYMBOL BREAKDOWN (rule-based, N≥3):",
    ] + sym_lines + [
        f"",
        f"{'='*95}",
    ]
    return '\n'.join(lines)


def _save_csv(trades: List[dict], market: str) -> str:
    ts   = datetime.now().strftime('%Y%m%d_%H%M')
    path = REPORT_DIR / f"backtest_{market.lower()}_{ts}.csv"
    if not trades:
        return str(path)
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=trades[0].keys())
        writer.writeheader()
        writer.writerows(trades)
    return str(path)


# ═══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='CB6 ML-Enhanced Strategy Backtest')
    parser.add_argument('--engine',   default='all',
                        choices=['nse', 'forex', 'all'],
                        help='Which market to backtest')
    parser.add_argument('--days',     type=int, default=100,
                        help='Days of history (NSE capped at 100 by Fyers API)')
    parser.add_argument('--telegram', action='store_true',
                        help='Send summary report to Telegram')
    parser.add_argument('--no-ml',    action='store_true',
                        help='Skip ML model loading (rule-based only)')
    args = parser.parse_args()

    print("\n" + "="*70)
    print("  CB6 QUANTUM — ML-ENHANCED STRATEGY BACKTEST")
    print(f"  Engine: {args.engine.upper()}  |  Days: {args.days}")
    print("="*70)

    ts_start = datetime.now()
    all_reports = []
    all_csv_paths = []

    run_nse   = args.engine in ('nse',   'all')
    run_forex = args.engine in ('forex', 'all')

    # Load ML models
    nse_models   = {}
    forex_models = {}
    if not args.no_ml:
        print("\nLoading ML models...")
        nse_models   = _load_ml_models('nse')
        forex_models = _load_ml_models('forex')

    # NSE backtest
    if run_nse:
        nse_days = min(args.days, 100)
        if nse_days < args.days:
            print(f"\nNSE: days capped at 100 (Fyers 5-min API hard limit). "
                  f"Requested {args.days}d.")
        print(f"\nRunning NSE backtest ({nse_days} days)...")
        nse_trades = run_nse_backtest(days=nse_days, ml_models=nse_models)
        report_nse = _full_report('NSE', nse_trades)
        all_reports.append(report_nse)
        print(report_nse)
        if nse_trades:
            path = _save_csv(nse_trades, 'NSE')
            all_csv_paths.append(path)
            print(f"  CSV saved: {path}")

    # Forex backtest
    if run_forex:
        print(f"\nRunning Forex backtest ({args.days} days — MT5 data)...")
        fx_trades  = run_forex_backtest(days=args.days, ml_models=forex_models)
        report_fx  = _full_report('FOREX', fx_trades)
        all_reports.append(report_fx)
        print(report_fx)
        if fx_trades:
            path = _save_csv(fx_trades, 'FOREX')
            all_csv_paths.append(path)
            print(f"  CSV saved: {path}")

    # Combined NSE + Forex
    if run_nse and run_forex:
        combined = []
        if 'nse_trades' in dir():
            combined += nse_trades
        if 'fx_trades' in dir():
            combined += fx_trades
        if combined:
            report_combined = _full_report('NSE + FOREX COMBINED', combined)
            all_reports.append(report_combined)
            print(report_combined)

    elapsed = round((datetime.now() - ts_start).total_seconds(), 1)
    footer  = f"\n  Backtest completed in {elapsed}s\n  CSV files: {', '.join(all_csv_paths)}"
    print(footer)

    # Save full report to file
    ts_str   = datetime.now().strftime('%Y%m%d_%H%M')
    rep_path = REPORT_DIR / f"backtest_report_{ts_str}.txt"
    with open(rep_path, 'w') as f:
        f.write('\n'.join(all_reports))
        f.write(footer)
    print(f"  Report saved: {rep_path}")

    # Telegram
    if args.telegram and all_reports:
        try:
            from dotenv import dotenv_values
            env   = dotenv_values('.env')
            token = env.get('TELEGRAM_BOT_TOKEN', '')
            chat  = env.get('TELEGRAM_CHAT_ID', '')
            if token and chat:
                import requests
                full_text = '\n'.join(all_reports) + footer
                chunk     = full_text[:4000]
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={'chat_id': chat, 'text': chunk},
                    timeout=15,
                )
                print("  Telegram: report sent")
        except Exception as te:
            print(f"  Telegram error: {te}")


if __name__ == '__main__':
    main()
