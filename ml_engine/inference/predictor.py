"""
ml_engine/inference/predictor.py

MLPredictor: loads the best trained model from the registry and runs
single-trade inference from a feature dict.

Usage:
    predictor = MLPredictor(engine="nse")
    result = predictor.predict(feature_dict)
    # result: {"win_probability", "expected_r", "confidence_score",
    #          "confidence_bucket", "trade_grade", "suggested_risk_mult",
    #          "model_version", "model_type", "is_trained"}

SHADOW ONLY — caller must never act on result["suggested_risk_mult"].
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("cb6.ml.predictor")

REGISTRY_PATH   = Path("ml_engine/config/model_registry.json")
MODEL_SAVE_BASE = Path("ml_engine/models/saved")


class MLPredictor:
    """
    Loads the active trained model for a given engine and runs inference.
    Falls back to neutral output if no trained model exists.

    Model priority: DNN > LSTM > (CNN excluded from live shadow — research only)
    """

    def __init__(self, engine: str = "nse"):
        self.engine      = engine
        self._dnn        = None   # DNNTradeScorer instance
        self._lstm       = None   # RNNTradeScorer instance
        self._model_type = None   # "dnn" | "lstm" | "none"
        self._version    = None
        self._feat_names: list[str] = []
        self._load_best_model()

    # ── Model loading ─────────────────────────────────────────────────────

    def _load_best_model(self) -> None:
        """Load DNN first, then LSTM as fallback. Reads from model_registry."""
        registry = self._read_registry()
        if registry is None:
            logger.warning("Model registry not found — predictor running without a model")
            self._model_type = "none"
            return

        # Try DNN
        dnn_key = f"dnn_trade_scorer_{self.engine}"
        dnn_path = self._best_model_path(registry, dnn_key)
        if dnn_path:
            try:
                from ml_engine.models.dnn_trade_scorer import DNNTradeScorer
                self._dnn        = DNNTradeScorer.load(dnn_path)
                self._model_type = "dnn"
                self._version    = dnn_path.name
                self._feat_names = self._dnn.feature_names
                logger.info(f"Loaded DNN model: {dnn_path}")
                return
            except Exception as e:
                logger.warning(f"DNN load failed ({dnn_path}): {e}")

        # Try LSTM
        rnn_key = f"rnn_trade_scorer_{self.engine}"
        rnn_path = self._best_model_path(registry, rnn_key)
        if rnn_path:
            try:
                from ml_engine.models.rnn_sequence_model import RNNTradeScorer
                self._lstm       = RNNTradeScorer.load(rnn_path)
                self._model_type = "lstm"
                self._version    = rnn_path.name
                self._feat_names = self._lstm.feature_names
                logger.info(f"Loaded LSTM model: {rnn_path}")
                return
            except Exception as e:
                logger.warning(f"LSTM load failed ({rnn_path}): {e}")

        self._model_type = "none"
        logger.warning(f"No trained model found for engine={self.engine} — returning neutral output")

    def _best_model_path(self, registry: dict, model_key: str) -> Optional[Path]:
        """Return Path to the most recent trained model, or None."""
        model_info = registry.get("models", {}).get(model_key)
        if not model_info:
            return None
        versions = model_info.get("versions", [])
        # Pick latest version with a saved path
        for v in reversed(versions):
            p = v.get("model_path", "")
            if p and Path(p).exists():
                return Path(p)
        return None

    @staticmethod
    def _read_registry() -> Optional[dict]:
        try:
            with open(REGISTRY_PATH) as f:
                return json.load(f)
        except Exception:
            return None

    # ── Feature preparation ───────────────────────────────────────────────

    def _feature_dict_to_array(self, feature_dict: dict) -> Optional[np.ndarray]:
        """
        Convert a flat feature dict → numpy array aligned to model's feature_names.
        Missing features are imputed to 0.0.
        """
        if not self._feat_names:
            return None
        arr = np.array(
            [float(feature_dict.get(f, 0.0) or 0.0) for f in self._feat_names],
            dtype=np.float32,
        ).reshape(1, -1)
        return arr

    # ── Inference ─────────────────────────────────────────────────────────

    def predict(self, feature_dict: dict) -> dict:
        """
        Run inference from a flat feature dict (keys = feature names).

        Returns
        -------
        dict with:
            win_probability     float  0-1
            expected_r          float
            confidence_score    float  0-1
            confidence_bucket   str    A+/A/B/C
            trade_grade         str    (DNN only, else "—")
            suggested_risk_mult float  SHADOW ONLY
            model_version       str
            model_type          str
            is_trained          bool
        """
        base = {
            "win_probability"    : 0.5,
            "expected_r"         : 0.0,
            "confidence_score"   : 0.0,
            "confidence_bucket"  : "C",
            "trade_grade"        : "--",
            "suggested_risk_mult": 1.0,
            "model_version"      : self._version or "none",
            "model_type"         : self._model_type,
            "is_trained"         : self._model_type != "none",
        }

        if self._model_type == "none":
            return base

        X = self._feature_dict_to_array(feature_dict)
        if X is None:
            return base

        try:
            if self._model_type == "dnn":
                pred = self._dnn.predict(X)
                base.update({
                    "win_probability"    : float(pred["win_probability"][0]),
                    "expected_r"         : float(pred["expected_r"][0]),
                    "confidence_score"   : float(pred["confidence_score"][0]),
                    "confidence_bucket"  : str(pred["confidence_bucket"][0]),
                    "trade_grade"        : str(pred["trade_grade"][0]),
                    "suggested_risk_mult": float(pred["suggested_risk_mult"][0]),
                })

            elif self._model_type == "lstm":
                pred = self._lstm.predict(X)
                base.update({
                    "win_probability"    : float(pred["win_probability"][0]),
                    "expected_r"         : float(pred["expected_r"][0]),
                    "confidence_score"   : float(pred["confidence_score"][0]),
                    "confidence_bucket"  : str(pred["confidence_bucket"][0]),
                    "suggested_risk_mult": float(pred["suggested_risk_mult"][0]),
                })

        except Exception as e:
            logger.error(f"Inference error (engine={self.engine}, type={self._model_type}): {e}")

        return base

    @property
    def is_ready(self) -> bool:
        return self._model_type != "none"

    @property
    def model_type(self) -> str:
        return self._model_type or "none"

    @property
    def version(self) -> Optional[str]:
        return self._version
