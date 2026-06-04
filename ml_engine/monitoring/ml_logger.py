"""
ml_engine/monitoring/ml_logger.py

Structured event logger for the CB6 ML engine.
Writes to ml_engine/logs/ml_events.jsonl — one JSON line per event.

Event types:
    model_trained       : a model finished training
    model_loaded        : a model was loaded for inference
    prediction_made     : shadow prediction logged
    prediction_audited  : actual outcome matched to a prediction
    drift_detected      : drift detector fired an alert
    gate_check          : activation gate evaluated
    error               : any ML subsystem error

Usage:
    from ml_engine.monitoring.ml_logger import MLLogger
    MLLogger.log_train(engine="nse", metrics=result)
    MLLogger.log_prediction(engine="nse", pred=shadow_result)
    MLLogger.log_error(source="train_dnn", error=e)
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("cb6.ml.logger")

LOG_PATH = Path("ml_engine/logs/ml_events.jsonl")


def _write(event: dict) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception as e:
        logger.warning(f"MLLogger write failed: {e}")


class MLLogger:
    """Static event logger — all methods are class-level, no instantiation needed."""

    @staticmethod
    def log_train(
        engine: str,
        metrics: dict,
        model_type: str = "dnn",
    ) -> None:
        _write({
            "event"            : "model_trained",
            "ts"               : datetime.now().isoformat(),
            "engine"           : engine,
            "model_type"       : model_type,
            "version_id"       : metrics.get("version_id"),
            "auc"              : metrics.get("auc"),
            "f1"               : metrics.get("f1"),
            "brier_score"      : metrics.get("brier_score"),
            "train_samples"    : metrics.get("train_samples"),
            "val_samples"      : metrics.get("val_samples"),
            "activation_gate"  : metrics.get("activation_gate_passed"),
            "monotonic_wr"     : metrics.get("monotonic_wr"),
            "all_pos_expectancy": metrics.get("all_positive_expectancy"),
        })

    @staticmethod
    def log_loaded(engine: str, model_type: str, version: str) -> None:
        _write({
            "event"      : "model_loaded",
            "ts"         : datetime.now().isoformat(),
            "engine"     : engine,
            "model_type" : model_type,
            "version"    : version,
        })

    @staticmethod
    def log_prediction(
        engine: str,
        pred: dict,
        symbol: str = "",
        direction: str = "",
    ) -> None:
        _write({
            "event"          : "prediction_made",
            "ts"             : datetime.now().isoformat(),
            "engine"         : engine,
            "symbol"         : symbol,
            "direction"      : direction,
            "win_probability": pred.get("win_probability"),
            "final_bucket"   : pred.get("final_bucket"),
            "composite_score": pred.get("composite_score"),
            "model_type"     : pred.get("model_type"),
            "model_version"  : pred.get("model_version"),
        })

    @staticmethod
    def log_audit(
        engine: str,
        prediction_id: str,
        predicted_bucket: str,
        actual_win: int,
        actual_r: Optional[float],
        win_prob: float,
    ) -> None:
        correct = int((win_prob >= 0.5) == bool(actual_win))
        _write({
            "event"           : "prediction_audited",
            "ts"              : datetime.now().isoformat(),
            "engine"          : engine,
            "prediction_id"   : prediction_id,
            "predicted_bucket": predicted_bucket,
            "actual_win"      : actual_win,
            "actual_r"        : actual_r,
            "win_prob"        : win_prob,
            "correct"         : correct,
        })

    @staticmethod
    def log_drift(
        engine: str,
        drift_type: str,
        severity: str,
        detail: dict,
    ) -> None:
        _write({
            "event"      : "drift_detected",
            "ts"         : datetime.now().isoformat(),
            "engine"     : engine,
            "drift_type" : drift_type,   # "accuracy_drop" | "distribution_shift" | "brier_rise"
            "severity"   : severity,     # "warning" | "critical"
            **detail,
        })

    @staticmethod
    def log_gate(
        engine: str,
        model_type: str,
        passed: bool,
        reason: str,
        metrics: dict,
    ) -> None:
        _write({
            "event"      : "gate_check",
            "ts"         : datetime.now().isoformat(),
            "engine"     : engine,
            "model_type" : model_type,
            "passed"     : passed,
            "reason"     : reason,
            "auc"        : metrics.get("auc"),
            "monotonic"  : metrics.get("monotonic_wr"),
            "pos_exp"    : metrics.get("all_positive_expectancy"),
        })

    @staticmethod
    def log_error(source: str, error: Exception, extra: Optional[dict] = None) -> None:
        _write({
            "event"  : "error",
            "ts"     : datetime.now().isoformat(),
            "source" : source,
            "error"  : str(error),
            "trace"  : traceback.format_exc()[-500:],   # last 500 chars
            **(extra or {}),
        })

    @staticmethod
    def tail(n: int = 20, event_type: Optional[str] = None) -> list[dict]:
        """Return last N events from the log, optionally filtered by type."""
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                lines = f.readlines()
            events = [json.loads(l.strip()) for l in lines if l.strip()]
            if event_type:
                events = [e for e in events if e.get("event") == event_type]
            return events[-n:]
        except Exception:
            return []
