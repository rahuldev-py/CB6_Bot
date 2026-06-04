"""
ml_engine/inference/confidence_engine.py

ConfidenceEngine: converts raw ML outputs to CB6 confidence grades.

Composite score formula:
    composite = 0.6 * win_prob_score + 0.3 * r_score + 0.1 * rule_score

where:
    win_prob_score = abs(win_prob - 0.5) * 2          (0=uncertain, 1=certain)
    r_score        = clip(expected_r / 3.0, 0, 1)     (3R+ = max confidence)
    rule_score     = rule_confluence / 7.0             (0-7 CB6 checks)

Final bucket:
    A+: composite >= 0.60 AND win_prob >= 0.70
    A : composite >= 0.40 AND win_prob >= 0.60
    B : composite >= 0.20
    C : else

SHADOW ONLY — bucket label is logged but never gates a live trade.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("cb6.ml.confidence_engine")

# Thresholds (composite score)
COMPOSITE_THRESHOLDS = {"A+": 0.60, "A": 0.40, "B": 0.20, "C": 0.00}
WIN_PROB_FLOOR       = {"A+": 0.70, "A": 0.60, "B": 0.55, "C": 0.00}

# Weights for composite score
W_WIN_PROB = 0.60
W_R_SCORE  = 0.30
W_RULE     = 0.10


class ConfidenceEngine:
    """
    Stateless converter: ML output dict → CB6 confidence grade.

    Can be called with just a win_prob (DNN output) or with full context
    (win_prob + expected_r + rule confluence score).
    """

    @staticmethod
    def compute(
        win_prob: float,
        expected_r: float = 0.0,
        rule_confluence: float = 0.0,
        max_rule_checks: int = 7,
    ) -> dict:
        """
        Compute composite confidence from ML + rule engine outputs.

        Parameters
        ----------
        win_prob        : 0-1 probability from ML model
        expected_r      : predicted R-multiple from ML model (0.0 if unavailable)
        rule_confluence : number of CB6 checks passing (0-7)
        max_rule_checks : denominator for rule score normalisation

        Returns
        -------
        dict:
            win_prob_score  float  0-1
            r_score         float  0-1
            rule_score      float  0-1
            composite_score float  0-1
            ml_bucket       str    A+/A/B/C  (ML-only, from win_prob distance)
            final_bucket    str    A+/A/B/C  (composite, recommended label)
            suggested_risk_mult float  SHADOW ONLY
        """
        # Component scores
        win_prob_score = abs(float(win_prob) - 0.5) * 2.0
        r_score        = min(max(float(expected_r) / 3.0, 0.0), 1.0) if expected_r else 0.0
        rule_score     = min(float(rule_confluence) / max(max_rule_checks, 1), 1.0)

        composite = (
            W_WIN_PROB * win_prob_score +
            W_R_SCORE  * r_score +
            W_RULE     * rule_score
        )

        # ML-only bucket (purely from win_prob, for transparency)
        if win_prob_score >= 0.60:
            ml_bucket = "A+"
        elif win_prob_score >= 0.40:
            ml_bucket = "A"
        elif win_prob_score >= 0.20:
            ml_bucket = "B"
        else:
            ml_bucket = "C"

        # Composite bucket (win_prob floor + composite threshold)
        wp = float(win_prob)
        final_bucket = "C"
        if composite >= COMPOSITE_THRESHOLDS["A+"] and wp >= WIN_PROB_FLOOR["A+"]:
            final_bucket = "A+"
        elif composite >= COMPOSITE_THRESHOLDS["A"] and wp >= WIN_PROB_FLOOR["A"]:
            final_bucket = "A"
        elif composite >= COMPOSITE_THRESHOLDS["B"] and wp >= WIN_PROB_FLOOR["B"]:
            final_bucket = "B"

        # Shadow risk multiplier (NEVER applied to live risk)
        risk_map = {"A+": 1.5, "A": 1.0, "B": 0.75, "C": 0.5}
        suggested_risk_mult = risk_map[final_bucket]

        return {
            "win_prob_score"     : round(win_prob_score, 4),
            "r_score"            : round(r_score, 4),
            "rule_score"         : round(rule_score, 4),
            "composite_score"    : round(composite, 4),
            "ml_bucket"          : ml_bucket,
            "final_bucket"       : final_bucket,
            "suggested_risk_mult": suggested_risk_mult,   # SHADOW ONLY
        }

    @staticmethod
    def from_prediction(pred: dict, rule_confluence: float = 0.0) -> dict:
        """
        Convenience wrapper — pass the full prediction dict from MLPredictor.predict().

        Parameters
        ----------
        pred            : dict from MLPredictor.predict()
        rule_confluence : CB6 rule score (0-7) for the same trade signal
        """
        return ConfidenceEngine.compute(
            win_prob=pred.get("win_probability", 0.5),
            expected_r=pred.get("expected_r", 0.0),
            rule_confluence=rule_confluence,
        )

    @staticmethod
    def grade_label(bucket: str) -> str:
        """Human-readable description for each confidence bucket."""
        return {
            "A+": "High confidence — strong ML + rule alignment",
            "A" : "Good confidence — ML agrees with rule signal",
            "B" : "Moderate — ML signal present, rule score lower",
            "C" : "Low / uncertain — ML near 50/50",
        }.get(bucket, "Unknown")
