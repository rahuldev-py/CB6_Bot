"""
ml_engine/inference/inference_router.py

InferenceRouter: top-level entry point for all ML shadow inference.

Responsibilities:
  - Determine which engine (nse/forex) a signal belongs to
  - Route to the correct ShadowPredictor instance
  - Aggregate multi-model predictions (DNN + LSTM ensemble, if both trained)
  - Enforce the global ML kill switch (ml_enabled flag)
  - Never raise — all errors return neutral output

Public API (one function):
    from ml_engine.inference.inference_router import route
    result = route(signal_dict)

Signal dict expected fields (CB6 output format):
    symbol       str     e.g. "NIFTY", "BANKNIFTY", "XAUUSD"
    direction    str     "BULLISH" | "BEARISH"
    score        float   CB6 rule confluence score
    entry_price  float
    stop_loss    float
    ...any other feature keys...

Result dict (SHADOW ONLY — never act on risk-related fields):
    win_probability      float
    expected_r           float
    final_bucket         str    A+/A/B/C
    composite_score      float
    suggested_risk_mult  float  ← SHADOW ONLY, never pass to risk engine
    model_type           str
    model_version        str
    engine_routed        str    nse | forex
    ml_enabled           bool
    shadow_logged        bool
    ensemble_used        bool
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("cb6.ml.inference_router")

CONFIG_PATH = Path("ml_engine/config/ml_config.json")

# Symbols that route to NSE engine
NSE_SYMBOLS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "NIFTY50", "NIFTY BANK", "NIFTY FIN SERVICE",
}

# Symbols that route to Forex engine
FOREX_SYMBOLS = {
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "USOIL", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF",
}


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {"ml_enabled": False, "ml_shadow_mode": True}


def _detect_engine(signal: dict) -> str:
    """Detect engine from symbol. Default: nse."""
    sym = str(signal.get("symbol", "")).upper().replace("/", "").replace(" ", "")
    if any(s.replace(" ", "") in sym for s in FOREX_SYMBOLS):
        return "forex"
    return "nse"


def _neutral_result(engine: str) -> dict:
    return {
        "win_probability"    : 0.5,
        "expected_r"         : 0.0,
        "confidence_score"   : 0.0,
        "final_bucket"       : "C",
        "composite_score"    : 0.0,
        "suggested_risk_mult": 1.0,   # SHADOW ONLY
        "model_type"         : "none",
        "model_version"      : "none",
        "engine_routed"      : engine,
        "ml_enabled"         : False,
        "shadow_logged"      : False,
        "ensemble_used"      : False,
    }


def route(signal: dict, trade_id: Optional[str] = None) -> dict:
    """
    Main entry point. Routes a CB6 signal to the correct shadow predictor.

    Parameters
    ----------
    signal   : CB6 signal dict (from scanner / MarketBrain)
    trade_id : optional trade ID for audit trail

    Returns
    -------
    Shadow result dict. NEVER act on suggested_risk_mult.
    """
    cfg = _load_config()
    if not cfg.get("ml_enabled", False):
        engine = _detect_engine(signal)
        return {**_neutral_result(engine), "ml_enabled": False}

    try:
        from ml_engine.inference.shadow_predictor import ShadowPredictor

        engine = _detect_engine(signal)
        result = ShadowPredictor.score_signal(signal, engine=engine)
        result["engine_routed"] = engine
        result["ensemble_used"] = False
        return result

    except Exception as e:
        logger.error(f"InferenceRouter error: {e}")
        return _neutral_result(_detect_engine(signal))


def route_with_market_context(signal: dict, trade_id: Optional[str] = None) -> dict:
    """
    Read-only variant of route() that also fetches internet market context.

    IMPORTANT:
      - Adds context for analysis/logging only.
      - Never modifies execution, risk, SL, TP, lots, or trade decisions.
    """
    base = route(signal, trade_id=trade_id)
    engine = base.get("engine_routed", _detect_engine(signal))
    symbol = str(signal.get("symbol", ""))

    try:
        from ml_engine.inference.internet_context import build_market_context, append_context_log

        ctx = build_market_context(symbol=symbol, engine=engine)
        append_context_log(
            {
                "ts": ctx.get("ts_utc"),
                "engine": engine,
                "symbol": symbol,
                "trade_id": trade_id,
                "context_status": ctx.get("status", {}),
                "headline_counts": {
                    "google": len(ctx.get("sources", {}).get("google_headlines", [])),
                    "yahoo": len(ctx.get("sources", {}).get("yahoo_headlines", [])),
                    "macro": len(ctx.get("sources", {}).get("macro_calendar", [])),
                },
            }
        )
        base["market_context"] = ctx
        base["market_context_read_only"] = True
    except Exception as e:
        logger.warning("route_with_market_context context fetch failed: %s", e)
        base["market_context"] = {
            "status": {"google_ok": False, "yahoo_ok": False, "macro_ok": False, "read_only": True},
            "error": str(e),
        }
        base["market_context_read_only"] = True

    return base


def route_ensemble(signal: dict, trade_id: Optional[str] = None) -> dict:
    """
    Ensemble mode: average DNN + LSTM predictions if both are trained.
    Falls back to single-model if only one is available.

    Returns same dict format as route(), plus:
        ensemble_used  bool
        dnn_win_prob   float (if ensemble)
        lstm_win_prob  float (if ensemble)
    """
    cfg = _load_config()
    if not cfg.get("ml_enabled", False):
        engine = _detect_engine(signal)
        return {**_neutral_result(engine), "ml_enabled": False}

    try:
        from ml_engine.inference.predictor import MLPredictor
        from ml_engine.inference.confidence_engine import ConfidenceEngine
        from ml_engine.inference.shadow_predictor import ShadowPredictor

        engine = _detect_engine(signal)

        # Load both model types explicitly
        dnn_pred  = None
        lstm_pred = None

        try:
            from ml_engine.models.dnn_trade_scorer import DNNTradeScorer
            registry_path = Path("ml_engine/config/model_registry.json")
            with open(registry_path) as f:
                registry = json.load(f)

            dnn_key  = f"dnn_trade_scorer_{engine}"
            rnn_key  = f"rnn_trade_scorer_{engine}"

            def _get_path(key):
                versions = registry.get("models", {}).get(key, {}).get("versions", [])
                for v in reversed(versions):
                    p = v.get("model_path", "")
                    if p and Path(p).exists():
                        return Path(p)
                return None

            dnn_path = _get_path(dnn_key)
            rnn_path = _get_path(rnn_key)

            if dnn_path:
                dnn = DNNTradeScorer.load(dnn_path)
                feat_names = dnn.feature_names
                if feat_names:
                    X = _signal_to_array(signal, feat_names)
                    dnn_pred = dnn.predict(X)

            if rnn_path:
                from ml_engine.models.rnn_sequence_model import RNNTradeScorer
                lstm = RNNTradeScorer.load(rnn_path)
                feat_names = lstm.feature_names or (feat_names if dnn_pred else [])
                if feat_names:
                    X = _signal_to_array(signal, feat_names)
                    lstm_pred = lstm.predict(X)

        except Exception as e:
            logger.warning(f"Ensemble model load error: {e}")

        # Aggregate
        if dnn_pred is not None and lstm_pred is not None:
            wp = (float(dnn_pred["win_probability"][0]) + float(lstm_pred["win_probability"][0])) / 2
            er = (float(dnn_pred["expected_r"][0]) + float(lstm_pred["expected_r"][0])) / 2
            rule_score = float(signal.get("score") or signal.get("confluence_score") or 0.0)
            conf = ConfidenceEngine.compute(wp, er, rule_score)

            result = {
                "win_probability"    : round(wp, 4),
                "expected_r"         : round(er, 3),
                "confidence_score"   : conf["win_prob_score"],
                "final_bucket"       : conf["final_bucket"],
                "composite_score"    : conf["composite_score"],
                "suggested_risk_mult": conf["suggested_risk_mult"],   # SHADOW ONLY
                "model_type"         : "ensemble_dnn+lstm",
                "model_version"      : "ensemble",
                "engine_routed"      : engine,
                "ml_enabled"         : True,
                "shadow_logged"      : False,
                "ensemble_used"      : True,
                "dnn_win_prob"       : round(float(dnn_pred["win_probability"][0]), 4),
                "lstm_win_prob"      : round(float(lstm_pred["win_probability"][0]), 4),
            }

            # Log the ensemble prediction
            shadow = ShadowPredictor(engine=engine)
            shadow._write_log({
                "ts"             : __import__("datetime").datetime.now().isoformat(),
                "engine"         : engine,
                "symbol"         : str(signal.get("symbol", "")),
                "direction"      : str(signal.get("direction", "")),
                "trade_id"       : trade_id,
                "rule_score_raw" : float(signal.get("score") or 0),
                **result,
                "actual_outcome" : None,
                "actual_r"       : None,
            })
            shadow._prediction_count += 1
            shadow._save_counter()
            result["shadow_logged"]     = True
            result["prediction_number"] = shadow.prediction_count
            return result

        # Fall back to single model
        result = route(signal, trade_id=trade_id)
        result["ensemble_used"] = False
        return result

    except Exception as e:
        logger.error(f"route_ensemble error: {e}")
        return _neutral_result(_detect_engine(signal))


def _signal_to_array(signal: dict, feat_names: list) -> "np.ndarray":
    import numpy as np
    return np.array(
        [float(signal.get(f, 0.0) or 0.0) for f in feat_names],
        dtype=np.float32,
    ).reshape(1, -1)


def get_shadow_stats(engine: str = "nse") -> dict:
    """
    Return current shadow prediction statistics for a given engine.
    Reads from shadow_counter.json and the last N lines of the log.
    """
    from pathlib import Path as _Path
    counter_path = _Path("ml_engine/logs/shadow_counter.json")
    log_path     = _Path("ml_engine/logs/shadow_predictions.jsonl")

    count = 0
    try:
        with open(counter_path) as f:
            count = int(json.load(f).get(engine, 0))
    except Exception:
        pass

    last_pred = None
    try:
        with open(log_path) as f:
            lines = f.readlines()
        for line in reversed(lines):
            entry = json.loads(line.strip())
            if entry.get("engine") == engine:
                last_pred = entry
                break
    except Exception:
        pass

    return {
        "engine"          : engine,
        "prediction_count": count,
        "last_prediction" : last_pred,
    }
