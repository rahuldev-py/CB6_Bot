"""
ml_engine/inference/shadow_predictor.py

ShadowPredictor: the live shadow layer of the CB6 ML engine.

Responsibilities:
  1. Receive a trade signal (feature dict) from MarketBrain / scanner
  2. Run MLPredictor.predict() to get ML output
  3. Run ConfidenceEngine.compute() for composite score
  4. Log the full prediction to ml_engine/logs/shadow_predictions.jsonl
  5. Return the prediction dict to the caller

HARD CONSTRAINTS (enforced by this class, not just by config):
  - Never modifies SL, TP, lots, risk, direction, or entry
  - Never calls any execution or broker function
  - Never raises exceptions that propagate to the execution thread
  - All I/O errors are swallowed — a log write failure is silent
  - Returns neutral dict if ML_ENABLED=false or any error occurs

The caller (MarketBrain / scanner) must:
  - Ignore result["suggested_risk_mult"] — it is shadow only
  - Use the prediction ONLY for logging / reporting
  - Never branch on result["final_bucket"] to change execution

Shadow log format (one JSON line per prediction):
  {
    "ts": "2026-05-23T21:00:00.123",
    "engine": "nse",
    "symbol": "NIFTY",
    "direction": "BULLISH",
    "rule_score": 12,
    "win_probability": 0.73,
    "expected_r": 2.1,
    "confidence_score": 0.46,
    "ml_bucket": "A",
    "final_bucket": "A",
    "composite_score": 0.52,
    "suggested_risk_mult": 1.0,
    "model_type": "dnn",
    "model_version": "dnn_nse_v1_20260523_2144",
    "actual_outcome": null    <- filled in later by PredictionAuditor
  }
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("cb6.ml.shadow_predictor")

LOG_PATH        = Path("ml_engine/logs/shadow_predictions.jsonl")
CONFIG_PATH     = Path("ml_engine/config/ml_config.json")
COUNTER_PATH    = Path("ml_engine/logs/shadow_counter.json")


def _load_ml_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {
            "ml_enabled"         : False,
            "ml_shadow_mode"     : True,
            "ml_can_trade"       : False,
            "ml_can_modify_risk" : False,
            "ml_can_block_trades": False,
            "ml_can_close_trades": False,
        }


def _neutral_shadow_result(engine: str = "nse") -> dict:
    return {
        "win_probability"    : 0.5,
        "expected_r"         : 0.0,
        "confidence_score"   : 0.0,
        "ml_bucket"          : "C",
        "final_bucket"       : "C",
        "composite_score"    : 0.0,
        "suggested_risk_mult": 1.0,   # SHADOW ONLY
        "model_type"         : "none",
        "model_version"      : "none",
        "is_trained"         : False,
        "shadow_logged"      : False,
        "ml_enabled"         : False,
    }


class ShadowPredictor:
    """
    Thread-safe, failure-safe shadow inference layer.

    Typical call from MarketBrain:
        shadow = ShadowPredictor(engine="nse")
        result = shadow.score(features, symbol="NIFTY", direction="BULLISH", rule_score=12)
        # result is logged; caller ignores result["suggested_risk_mult"]
    """

    _instances: dict[str, "ShadowPredictor"] = {}

    def __new__(cls, engine: str = "nse"):
        # One singleton per engine to avoid repeated model reloading
        if engine not in cls._instances:
            inst = super().__new__(cls)
            inst._engine     = engine
            inst._predictor  = None
            inst._ready      = False
            inst._prediction_count = cls._load_counter(engine)
            cls._instances[engine] = inst
        return cls._instances[engine]

    def _ensure_loaded(self) -> None:
        if self._predictor is not None:
            return
        try:
            from ml_engine.inference.predictor import MLPredictor
            self._predictor = MLPredictor(engine=self._engine)
            self._ready     = self._predictor.is_ready
            logger.info(
                f"ShadowPredictor ready: engine={self._engine} "
                f"model_type={self._predictor.model_type} "
                f"version={self._predictor.version}"
            )
        except Exception as e:
            logger.error(f"ShadowPredictor init error: {e}")
            self._ready = False

    # ── Public API ────────────────────────────────────────────────────────

    def score(
        self,
        feature_dict: dict,
        symbol: str = "",
        direction: str = "",
        rule_score: float = 0.0,
        trade_id: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> dict:
        """
        Run shadow inference and log the result.

        Parameters
        ----------
        feature_dict : flat dict of feature_name → value (from feature pipeline)
        symbol       : e.g. "NIFTY", "XAUUSD"
        direction    : "BULLISH" | "BEARISH"
        rule_score   : CB6 rule engine confluence score (0-7+)
        trade_id     : optional trade ID for later audit matching
        extra        : optional extra fields to include in log line

        Returns
        -------
        Shadow result dict (NEVER act on suggested_risk_mult).
        """
        cfg = _load_ml_config()

        # Hard gate: ML_ENABLED must be true in config
        if not cfg.get("ml_enabled", False):
            return {**_neutral_shadow_result(self._engine), "ml_enabled": False}

        # Ensure model is loaded
        self._ensure_loaded()

        if not self._ready:
            return {**_neutral_shadow_result(self._engine), "ml_enabled": True, "is_trained": False}

        try:
            from ml_engine.inference.confidence_engine import ConfidenceEngine

            # Run ML inference
            pred = self._predictor.predict(feature_dict)

            # Compute composite confidence
            conf = ConfidenceEngine.from_prediction(pred, rule_confluence=rule_score)

            result = {
                "win_probability"    : pred["win_probability"],
                "expected_r"         : pred["expected_r"],
                "confidence_score"   : pred["confidence_score"],
                "trade_grade"        : pred.get("trade_grade", "--"),
                "ml_bucket"          : conf["ml_bucket"],
                "final_bucket"       : conf["final_bucket"],
                "composite_score"    : conf["composite_score"],
                "win_prob_score"     : conf["win_prob_score"],
                "r_score"            : conf["r_score"],
                "rule_score_norm"    : conf["rule_score"],
                "suggested_risk_mult": conf["suggested_risk_mult"],   # SHADOW ONLY
                "model_type"         : pred["model_type"],
                "model_version"      : pred["model_version"],
                "is_trained"         : pred["is_trained"],
                "ml_enabled"         : True,
                "shadow_logged"      : False,
            }

            # Log to JSONL
            self._prediction_count += 1
            self._save_counter()
            result["shadow_logged"]     = True
            result["prediction_number"] = self._prediction_count

            log_entry = {
                "ts"                 : datetime.now().isoformat(),
                "engine"             : self._engine,
                "symbol"             : symbol,
                "direction"          : direction,
                "trade_id"           : trade_id,
                "rule_score_raw"     : rule_score,
                **result,
                "actual_outcome"     : None,   # filled in by PredictionAuditor
                "actual_r"           : None,
            }
            if extra:
                log_entry.update(extra)

            self._write_log(log_entry)

        except Exception as e:
            logger.error(f"ShadowPredictor.score error: {e}")
            result = _neutral_shadow_result(self._engine)

        return result

    # ── Helpers ───────────────────────────────────────────────────────────

    def _write_log(self, entry: dict) -> None:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.warning(f"Shadow log write failed: {e}")

    def _save_counter(self) -> None:
        try:
            COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(COUNTER_PATH, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data[self._engine] = self._prediction_count
        try:
            with open(COUNTER_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Counter save failed: {e}")

    @staticmethod
    def _load_counter(engine: str) -> int:
        try:
            with open(COUNTER_PATH) as f:
                return int(json.load(f).get(engine, 0))
        except Exception:
            return 0

    @property
    def prediction_count(self) -> int:
        return self._prediction_count

    @property
    def is_ready(self) -> bool:
        return self._ready

    def reset(self) -> None:
        """Force reload on next call (use after retraining)."""
        self._predictor = None
        self._ready     = False
        if self._engine in ShadowPredictor._instances:
            del ShadowPredictor._instances[self._engine]

    # ── Convenience: score from a CB6 signal dict ─────────────────────────

    @classmethod
    def score_signal(
        cls,
        signal: dict,
        engine: str = "nse",
    ) -> dict:
        """
        Convenience method: score a CB6 signal dict directly.

        The signal dict is the output from MarketBrain / scanner
        (contains direction, symbol, confluence_score, etc.)
        Features are extracted in-place — missing keys default to 0.
        """
        predictor = cls(engine=engine)
        rule_score = float(signal.get("confluence_score") or signal.get("score") or 0.0)

        return predictor.score(
            feature_dict=signal,
            symbol=str(signal.get("symbol", "")),
            direction=str(signal.get("direction", "")),
            rule_score=rule_score,
            trade_id=str(signal.get("trade_id") or signal.get("id") or ""),
        )
