# ml/predictor.py
#
# Unified ML inference engine for CB6 Quantum.
# Runs DNN + CNN + RNN in ensemble; logs every prediction for accuracy tracking.
#
# NSE: Live gate mode — AVOID confidence blocks trade, HIGH confidence boosts lots.
# Forex: Shadow mode — predictions logged, no order impact (models not yet trained).
# Always fails open on errors — ICT logic is the fallback.

from __future__ import annotations
import os, json
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from utils.logger import logger

_ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ML_DIR  = os.path.join(_ROOT, 'data', 'ml')


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _log_prediction(market: str, account: str, record: dict) -> None:
    """Append prediction record to JSONL log."""
    try:
        out_dir = os.path.join(_ML_DIR, market)
        os.makedirs(out_dir, exist_ok=True)
        tag  = f"{account}_" if account else ""
        path = os.path.join(out_dir, f"{tag}predictions.jsonl")
        line = json.dumps(record, default=str) + '\n'
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception as e:
        logger.error(f"ML predictor log error: {e}")


def _ensemble(preds: list[dict]) -> dict:
    """
    Weighted average ensemble across available models.
    Weights: DNN=0.40, CNN=0.35, RNN=0.35 (CNN+RNN share when both present).
    Falls back to equal weighting when only subset available.
    """
    if not preds:
        return {}
    weights = {'DNN': 0.40, 'CNN': 0.35, 'RNN': 0.25}
    total_w = sum(weights.get(p['model'], 0.30) for p in preds)
    wp = sum(p['win_prob'] * weights.get(p['model'], 0.30) for p in preds) / total_w
    rh = sum(p['r_hat']   * weights.get(p['model'], 0.30) for p in preds) / total_w

    if wp >= 0.70:
        conf = 'HIGH'
    elif wp >= 0.55:
        conf = 'MEDIUM'
    elif wp <= 0.35:
        conf = 'AVOID'
    else:
        conf = 'LOW'

    return {
        'win_prob'    : round(wp, 4),
        'r_hat'       : round(rh, 3),
        'confidence'  : conf,
        'models_used' : [p['model'] for p in preds],
        'model_detail': {p['model']: {'win_prob': p['win_prob'], 'r_hat': p['r_hat']}
                         for p in preds},
    }


# ── NSE Shadow Predict ─────────────────────────────────────────────────────────

def predict_nse(trade_id: str, setup: dict,
                candles: Optional[np.ndarray] = None,
                index_name: str = 'NIFTY',
                gate_only: bool = False) -> Optional[dict]:
    """
    ML prediction for an NSE trade signal.

    Args:
        trade_id   : unique trade identifier (matches ML log)
        setup      : dict with ICT fields (direction, mss_type, score, etc.)
        candles    : optional (N, 5) OHLCV array for CNN/RNN; if None those are skipped
        index_name : 'NIFTY' | 'BANKNIFTY' | 'MIDCPNIFTY' | 'FINNIFTY'
        gate_only  : True = skip logging (used by live gate in main.py to avoid
                     duplicate log; paper_trader logs the authoritative record with
                     the real trade_id so outcome linking works correctly)

    Returns dict with ensemble result, or None if no models loaded.
    """
    individual: list[dict] = []

    # ── BT-Trained DNN (primary — 16 features, 768-trade dataset) ─────────────
    try:
        from ml.bt_trainer import predict_from_setup
        res = predict_from_setup(setup, index_name=index_name)
        if res:
            individual.append({'model': 'DNN', **res})
    except Exception as e:
        logger.debug(f"NSE bt_trainer predict skip: {e}")

    # ── Legacy DNN fallback (24-feature JSONL-trained model) ──────────────────
    if not individual:
        try:
            import pandas as pd
            from ml.data_pipeline import build_nse_features, NSE_FEATURES
            from ml.trainer import dnn_trainer
            row = {f: setup.get(f, 0) for f in NSE_FEATURES}
            df  = pd.DataFrame([row])
            X, _, _ = build_nse_features(df)
            res = dnn_trainer.predict(X[0], market='nse')
            if res:
                individual.append(res)
        except Exception as e:
            logger.debug(f"NSE DNN legacy predict skip: {e}")

    # ── CNN ────────────────────────────────────────────────────────────────────
    if candles is not None:
        try:
            from ml.trainer import cnn_trainer
            res = cnn_trainer.predict(candles, market='nse')
            if res:
                individual.append(res)
        except Exception as e:
            logger.debug(f"NSE CNN predict skip: {e}")

        # ── RNN ────────────────────────────────────────────────────────────────
        try:
            from ml.trainer import rnn_trainer
            res = rnn_trainer.predict(candles, market='nse')
            if res:
                individual.append(res)
        except Exception as e:
            logger.debug(f"NSE RNN predict skip: {e}")

    if not individual:
        return None

    result = _ensemble(individual)
    result.update({
        '_type'      : 'PREDICTION',
        'trade_id'   : trade_id,
        'market'     : 'nse',
        'account'    : '',
        'predicted_at': _now_utc(),
        'outcome'    : None,
    })
    if not gate_only:
        _log_prediction('nse', '', result)
    logger.info(
        f"ML NSE {'[GATE]' if gate_only else '[LOG]'} [{trade_id}] "
        f"win={result['win_prob']:.1%} R={result['r_hat']:+.2f} "
        f"conf={result['confidence']} models={result['models_used']}"
    )
    return result


# ── Forex Shadow Predict ───────────────────────────────────────────────────────

def predict_forex(trade_id: str, setup: dict, account: str,
                  candles: Optional[np.ndarray] = None) -> Optional[dict]:
    """
    Shadow prediction for a Forex trade signal.

    Args:
        trade_id : unique trade identifier
        setup    : dict with ICT fields + A+ context
        account  : 'ftmo' | 'gft'
        candles  : optional (N, 5) OHLCV array for CNN/RNN

    Returns dict with ensemble result, or None if no models loaded.
    NEVER places or modifies any order.
    """
    from ml.data_pipeline import build_forex_features, FOREX_FEATURES

    individual: list[dict] = []

    # ── DNN ────────────────────────────────────────────────────────────────────
    try:
        import pandas as pd
        from ml.trainer import dnn_trainer
        row = {f: setup.get(f, 0) for f in FOREX_FEATURES}
        df  = pd.DataFrame([row])
        X, _, _ = build_forex_features(df)
        res = dnn_trainer.predict(X[0], market='forex', account=account)
        if res:
            individual.append(res)
    except Exception as e:
        logger.debug(f"Forex DNN predict skip [{account}]: {e}")

    # ── CNN ────────────────────────────────────────────────────────────────────
    if candles is not None:
        try:
            from ml.trainer import cnn_trainer
            res = cnn_trainer.predict(candles, market='forex', account=account)
            if res:
                individual.append(res)
        except Exception as e:
            logger.debug(f"Forex CNN predict skip [{account}]: {e}")

        # ── RNN ────────────────────────────────────────────────────────────────
        try:
            from ml.trainer import rnn_trainer
            res = rnn_trainer.predict(candles, market='forex', account=account)
            if res:
                individual.append(res)
        except Exception as e:
            logger.debug(f"Forex RNN predict skip [{account}]: {e}")

    if not individual:
        return None

    result = _ensemble(individual)
    result.update({
        '_type'      : 'PREDICTION',
        'trade_id'   : trade_id,
        'market'     : 'forex',
        'account'    : account,
        'predicted_at': _now_utc(),
        'outcome'    : None,
    })
    _log_prediction('forex', account, result)
    logger.info(
        f"ML FOREX SHADOW [{account}/{trade_id}] "
        f"win={result['win_prob']:.1%} R={result['r_hat']:+.2f} "
        f"conf={result['confidence']} models={result['models_used']}"
    )
    return result


# ── Outcome patch (called when trade closes) ───────────────────────────────────

def record_actual_outcome(trade_id: str, market: str, account: str,
                          actual_result: str, actual_r: float) -> None:
    """
    Patch the prediction record with the actual trade outcome.
    Called by shadow_monitor when an OUTCOME is logged.
    SHADOW MODE — read/write to data files only, never to broker.
    """
    try:
        tag  = f"{account}_" if account else ""
        path = os.path.join(_ML_DIR, market, f"{tag}predictions.jsonl")
        if not os.path.exists(path):
            return

        # Rewrite matching prediction with outcome filled in
        lines_out = []
        patched   = False
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get('trade_id') == trade_id and rec.get('outcome') is None:
                        rec['outcome'] = {
                            'result'  : actual_result.upper(),
                            'r_actual': round(actual_r, 3),
                            'closed_at': _now_utc(),
                        }
                        patched = True
                    lines_out.append(json.dumps(rec, default=str))
                except Exception:
                    lines_out.append(line)

        if patched:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines_out) + '\n')
            logger.debug(f"ML predictor: outcome patched for {trade_id}")
    except Exception as e:
        logger.error(f"ML predictor outcome patch error: {e}")


# ── Quick stats (for /ml_status) ──────────────────────────────────────────────

def get_prediction_accuracy(market: str, account: str = '') -> dict:
    """
    Read predictions.jsonl and compute accuracy of completed predictions.
    Returns dict with accuracy metrics.
    """
    try:
        tag  = f"{account}_" if account else ""
        path = os.path.join(_ML_DIR, market, f"{tag}predictions.jsonl")
        if not os.path.exists(path):
            return {'total': 0, 'accuracy': None}

        total = correct = high_conf = high_correct = 0
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get('_type') != 'PREDICTION':
                        continue
                    outcome = rec.get('outcome')
                    if not outcome:
                        continue   # still open

                    predicted_win = rec['win_prob'] >= 0.5
                    actual_win    = outcome['result'] == 'WIN'
                    total  += 1
                    if predicted_win == actual_win:
                        correct += 1
                    if rec['confidence'] in ('HIGH', 'MEDIUM'):
                        high_conf += 1
                        if predicted_win == actual_win:
                            high_correct += 1
                except Exception:
                    pass

        return {
            'total'          : total,
            'accuracy'       : round(correct / total * 100, 1) if total else None,
            'high_conf_total': high_conf,
            'high_conf_acc'  : round(high_correct / high_conf * 100, 1)
                               if high_conf else None,
        }
    except Exception as e:
        logger.error(f"ML accuracy stats error: {e}")
        return {'total': 0, 'accuracy': None}
