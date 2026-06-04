"""
ml_engine/reports/model_scorecard.py

ModelScorecard: per-model summary of training metrics + live shadow performance.
Reads from model_registry.json + shadow_predictions.jsonl.

Usage:
    from ml_engine.reports.model_scorecard import ModelScorecard
    card = ModelScorecard(engine="nse")
    print(card.render())
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

REGISTRY_PATH = Path("ml_engine/config/model_registry.json")
SHADOW_LOG    = Path("ml_engine/logs/shadow_predictions.jsonl")


class ModelScorecard:

    def __init__(self, engine: str = "nse"):
        self.engine   = engine
        self._registry = self._load_registry()

    @staticmethod
    def _load_registry() -> dict:
        try:
            with open(REGISTRY_PATH) as f:
                return json.load(f)
        except Exception:
            return {"models": {}}

    def _get_latest_version(self, model_key: str) -> Optional[dict]:
        model = self._registry.get("models", {}).get(model_key, {})
        versions = model.get("versions", [])
        return versions[-1] if versions else None

    def _shadow_stats(self) -> dict:
        from ml_engine.monitoring.performance_tracker import PerformanceTracker
        try:
            tracker = PerformanceTracker(engine=self.engine)
            return tracker.compute(window=100)
        except Exception:
            return {}

    def data(self) -> dict:
        """Return structured scorecard data dict."""
        dnn_key  = f"dnn_trade_scorer_{self.engine}"
        rnn_key  = f"rnn_trade_scorer_{self.engine}"

        dnn_v  = self._get_latest_version(dnn_key)
        rnn_v  = self._get_latest_version(rnn_key)
        shadow = self._shadow_stats()

        return {
            "engine"    : self.engine,
            "as_of"     : datetime.now().isoformat(),
            "dnn"       : dnn_v,
            "lstm"      : rnn_v,
            "shadow"    : shadow,
        }

    def render(self) -> str:
        """Return a plain-text scorecard string."""
        d       = self.data()
        engine  = d["engine"].upper()
        lines   = [
            f"",
            f"{'='*55}",
            f"  CB6 QUANTUM -- ML SCORECARD ({engine})",
            f"  {d['as_of'][:19]}",
            f"{'='*55}",
        ]

        def _model_block(label: str, v: Optional[dict]) -> list[str]:
            if v is None:
                return [f"  {label}: NOT TRAINED"]
            gate = "PASS" if v.get("activation_gate_passed") else "NOT YET"
            return [
                f"  {label}: {v.get('version_id', 'n/a')}",
                f"    AUC={v.get('auc', '?'):.4f}  F1={v.get('f1', '?'):.4f}  Brier={v.get('brier_score', '?'):.4f}",
                f"    Train={v.get('train_samples', '?')}  Val={v.get('val_samples', '?')}",
                f"    Gate: {gate}",
            ]

        lines += _model_block("DNN", d["dnn"])
        lines += [""]
        lines += _model_block("LSTM", d["lstm"])
        lines += [""]

        shadow = d.get("shadow", {})
        n_total   = shadow.get("n_total", 0)
        n_audited = shadow.get("n_audited", 0)
        acc       = shadow.get("accuracy")
        auc       = shadow.get("auc")
        brier     = shadow.get("brier")
        ready     = shadow.get("shadow_ready", False)

        lines += [
            f"  Shadow Predictions: {n_total} total / {n_audited} audited",
            f"  Live Accuracy : {'n/a' if acc is None else f'{acc:.1%}'}",
            f"  Live AUC      : {'n/a' if auc is None else f'{auc:.4f}'}",
            f"  Live Brier    : {'n/a' if brier is None else f'{brier:.4f}'}",
            f"  Shadow Gate   : {'READY (100+ audited)' if ready else f'NOT YET ({n_audited}/100 audited)'}",
            f"",
        ]

        # Bucket stats
        bstats = shadow.get("bucket_stats", {})
        if bstats:
            lines.append("  Bucket Performance (live):")
            lines.append(f"  {'Bucket':<5} {'N':>5} {'WinRate':>9} {'Accuracy':>10}")
            for b in ["A+", "A", "B", "C"]:
                bs = bstats.get(b, {})
                n  = bs.get("n", 0)
                wr = bs.get("win_rate")
                ac = bs.get("accuracy")
                wr_s = f"{wr:.1%}" if wr is not None else "--"
                ac_s = f"{ac:.1%}" if ac is not None else "--"
                lines.append(f"  {b:<5} {n:>5} {wr_s:>9} {ac_s:>10}")

        lines.append(f"{'='*55}")
        return "\n".join(lines)
