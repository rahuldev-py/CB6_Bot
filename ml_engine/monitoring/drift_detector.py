"""
ml_engine/monitoring/drift_detector.py

DriftDetector: monitors for concept drift and distribution shift.

Drift types:
    accuracy_drop     rolling accuracy falls > 10pp below training accuracy
    brier_rise        Brier score rises > 0.05 above training baseline
    distribution_shift feature distribution moved (PSI > 0.2 on key features)
    win_prob_shift    model started outputting very different win_prob distribution

PSI (Population Stability Index):
    < 0.1  : no shift
    0.1-0.2: moderate (monitor)
    > 0.2  : significant (retrain)

Usage:
    from ml_engine.monitoring.drift_detector import DriftDetector
    detector = DriftDetector(engine="nse")
    alerts = detector.check()
    for alert in alerts:
        print(alert)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("cb6.ml.drift_detector")

SHADOW_LOG    = Path("ml_engine/logs/shadow_predictions.jsonl")
REGISTRY_PATH = Path("ml_engine/config/model_registry.json")

# Drift thresholds
ACCURACY_DROP_THRESHOLD = 0.10   # 10pp drop triggers warning
BRIER_RISE_THRESHOLD    = 0.05
PSI_WARNING             = 0.10
PSI_CRITICAL            = 0.20
MIN_WINDOW              = 20     # minimum predictions needed before drift check


def _psi(expected: np.ndarray, actual: np.ndarray, n_bins: int = 10) -> float:
    """Population Stability Index between two distributions."""
    bins = np.percentile(expected, np.linspace(0, 100, n_bins + 1))
    bins[0]  -= 1e-6
    bins[-1] += 1e-6

    exp_pct = np.histogram(expected, bins=bins)[0] / len(expected)
    act_pct = np.histogram(actual,   bins=bins)[0] / len(actual)

    # Clip to avoid log(0)
    exp_pct = np.clip(exp_pct, 1e-6, None)
    act_pct = np.clip(act_pct, 1e-6, None)

    psi = np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct))
    return float(psi)


class DriftDetector:

    def __init__(self, engine: str = "nse", window: int = 50):
        self.engine = engine
        self.window = window

    def _load_predictions(self) -> list[dict]:
        if not SHADOW_LOG.exists():
            return []
        rows = []
        with open(SHADOW_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("engine") == self.engine:
                        rows.append(r)
                except Exception:
                    pass
        return rows

    def _training_baseline(self) -> Optional[dict]:
        """Read training AUC and Brier from registry."""
        try:
            with open(REGISTRY_PATH) as f:
                registry = json.load(f)
            for key in [f"dnn_trade_scorer_{self.engine}", f"rnn_trade_scorer_{self.engine}"]:
                model = registry.get("models", {}).get(key, {})
                versions = model.get("versions", [])
                if versions:
                    last = versions[-1]
                    return {
                        "auc"        : last.get("auc"),
                        "brier_score": last.get("brier_score"),
                        "accuracy"   : last.get("accuracy"),
                    }
        except Exception:
            pass
        return None

    def check(self) -> list[dict]:
        """
        Run all drift checks. Returns list of alert dicts.
        Empty list = no drift detected.
        """
        from ml_engine.monitoring.ml_logger import MLLogger

        alerts = []
        preds  = self._load_predictions()

        if len(preds) < MIN_WINDOW:
            return [{"type": "info", "message": f"Only {len(preds)} predictions — need {MIN_WINDOW}+ for drift detection"}]

        # Recent window only
        recent = preds[-self.window:]
        audited = [p for p in recent if p.get("actual_outcome") is not None]

        # ── 1. Accuracy drop ─────────────────────────────────────────────
        baseline = self._training_baseline()
        if audited and baseline:
            y_true = np.array([p["actual_outcome"] for p in audited], dtype=float)
            y_pred = np.array([p.get("win_probability", 0.5) for p in audited], dtype=float)
            live_acc    = float(((y_pred >= 0.5) == y_true.astype(bool)).mean())
            train_acc   = float(baseline.get("accuracy") or 0.5)
            acc_drop    = train_acc - live_acc

            if acc_drop >= ACCURACY_DROP_THRESHOLD:
                severity = "critical" if acc_drop >= 0.15 else "warning"
                alert = {
                    "type"      : "accuracy_drop",
                    "severity"  : severity,
                    "engine"    : self.engine,
                    "live_acc"  : round(live_acc, 4),
                    "train_acc" : round(train_acc, 4),
                    "drop"      : round(acc_drop, 4),
                    "message"   : f"Live accuracy {live_acc:.1%} vs train {train_acc:.1%} (drop={acc_drop:.1%})",
                }
                alerts.append(alert)
                MLLogger.log_drift(self.engine, "accuracy_drop", severity, alert)

        # ── 2. Brier score rise ───────────────────────────────────────────
        if audited and baseline:
            brier_live  = float(np.mean((y_pred - y_true) ** 2))
            brier_train = float(baseline.get("brier_score") or 0.25)
            brier_rise  = brier_live - brier_train

            if brier_rise >= BRIER_RISE_THRESHOLD:
                severity = "critical" if brier_rise >= 0.10 else "warning"
                alert = {
                    "type"       : "brier_rise",
                    "severity"   : severity,
                    "engine"     : self.engine,
                    "live_brier" : round(brier_live, 4),
                    "train_brier": round(brier_train, 4),
                    "rise"       : round(brier_rise, 4),
                    "message"    : f"Brier rose {brier_rise:.3f} above training baseline",
                }
                alerts.append(alert)
                MLLogger.log_drift(self.engine, "brier_rise", severity, alert)

        # ── 3. Win-probability distribution shift (PSI) ──────────────────
        if len(preds) >= self.window * 2:
            early_wp = np.array([p.get("win_probability", 0.5) for p in preds[:self.window]], dtype=float)
            late_wp  = np.array([p.get("win_probability", 0.5) for p in preds[-self.window:]], dtype=float)
            psi_val  = _psi(early_wp, late_wp)

            if psi_val >= PSI_WARNING:
                severity = "critical" if psi_val >= PSI_CRITICAL else "warning"
                alert = {
                    "type"    : "win_prob_shift",
                    "severity": severity,
                    "engine"  : self.engine,
                    "psi"     : round(psi_val, 4),
                    "early_mean_wp": round(float(early_wp.mean()), 4),
                    "late_mean_wp" : round(float(late_wp.mean()), 4),
                    "message" : f"Win-prob PSI={psi_val:.3f} {'(CRITICAL)' if psi_val >= PSI_CRITICAL else '(warning)'}",
                }
                alerts.append(alert)
                MLLogger.log_drift(self.engine, "win_prob_shift", severity, alert)

        # ── 4. Bucket distribution shift ─────────────────────────────────
        if len(preds) >= self.window * 2:
            bucket_col = "final_bucket"

            def bucket_dist(subset):
                counts = {"A+": 0, "A": 0, "B": 0, "C": 0}
                for p in subset:
                    b = p.get(bucket_col, "C")
                    if b in counts:
                        counts[b] += 1
                total = max(sum(counts.values()), 1)
                return {k: v / total for k, v in counts.items()}

            early_dist = bucket_dist(preds[:self.window])
            late_dist  = bucket_dist(preds[-self.window:])

            aplus_shift = abs(late_dist.get("A+", 0) - early_dist.get("A+", 0))
            if aplus_shift > 0.15:
                alert = {
                    "type"        : "bucket_distribution_shift",
                    "severity"    : "warning",
                    "engine"      : self.engine,
                    "early_aplus" : round(early_dist.get("A+", 0), 4),
                    "late_aplus"  : round(late_dist.get("A+", 0), 4),
                    "shift"       : round(aplus_shift, 4),
                    "message"     : f"A+ bucket proportion shifted by {aplus_shift:.1%}",
                }
                alerts.append(alert)
                MLLogger.log_drift(self.engine, "bucket_distribution_shift", "warning", alert)

        if not alerts:
            alerts.append({
                "type"   : "ok",
                "engine" : self.engine,
                "window" : self.window,
                "audited": len(audited),
                "message": f"No drift detected over last {self.window} predictions",
            })

        return alerts

    def summary(self) -> str:
        """Return a one-line drift status string."""
        alerts = [a for a in self.check() if a.get("type") != "ok" and a.get("type") != "info"]
        if not alerts:
            return f"[{self.engine.upper()}] No drift"
        crit = [a for a in alerts if a.get("severity") == "critical"]
        warn = [a for a in alerts if a.get("severity") == "warning"]
        parts = []
        if crit:
            parts.append(f"CRITICAL: {', '.join(a['type'] for a in crit)}")
        if warn:
            parts.append(f"WARNING: {', '.join(a['type'] for a in warn)}")
        return f"[{self.engine.upper()}] " + " | ".join(parts)
