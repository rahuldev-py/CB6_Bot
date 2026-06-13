# scanner/edge/ob_grader.py
#
# CB6 Quantum — Order Block (OB) Quality Grader
#
# Grades order block quality using 5 criteria:
#   1. Displacement (size of move away from OB)
#   2. Body size (OB candle body as % of range)
#   3. Mitigation (has price re-tested OB before?)
#   4. Proximity (how close is current price to OB?)
#   5. HTF alignment (does OB align with H4/H1 bias?)
#
# Shadow/context only. Stores OB score in trade context — never blocks entries.
# Toggle: EDGE_OB_GRADER_ENABLED env var.

from __future__ import annotations

import os
from typing import Dict, Optional

import pandas as pd

_ENABLED = os.getenv('EDGE_OB_GRADER_ENABLED', 'true').lower() != 'false'


def grade_order_block(
    ob: Dict,
    df: pd.DataFrame,
    current_price: float,
    h4_bias: str = '',
    h1_bias: str = '',
) -> Dict:
    """
    Grade an order block dict on 5 criteria (0-20 points each, max 100).

    Args:
        ob            : order block dict {type, high, low, displacement, body_pct, ...}
        df            : OHLCV DataFrame (for mitigation check)
        current_price : current market price
        h4_bias       : 'BULLISH' | 'BEARISH' | ''
        h1_bias       : 'BULLISH' | 'BEARISH' | ''

    Returns dict:
        ob_score      — int 0-100
        ob_grade      — 'A+' | 'A' | 'B' | 'C' | 'D'
        score_breakdown — dict of component scores
        context       — string summary
    """
    empty = _empty_result()
    if not _ENABLED or not ob:
        return empty

    try:
        scores = {
            'displacement' : _score_displacement(ob),
            'body_size'    : _score_body_size(ob),
            'mitigation'   : _score_mitigation(ob, df),
            'proximity'    : _score_proximity(ob, current_price),
            'htf_alignment': _score_htf_alignment(ob, h4_bias, h1_bias),
        }
        total = sum(scores.values())
        grade = _grade(total)
        return {
            'ob_score'       : total,
            'ob_grade'       : grade,
            'score_breakdown': scores,
            'context'        : (
                f"OB grade={grade} ({total}/100) | "
                f"disp={scores['displacement']} body={scores['body_size']} "
                f"mit={scores['mitigation']} prox={scores['proximity']} "
                f"htf={scores['htf_alignment']}"
            ),
        }
    except Exception:
        return empty


def _score_displacement(ob: Dict) -> int:
    """Large displacement away from OB = higher quality."""
    d = float(ob.get('displacement', 0) or 0)
    # Normalized: 0 = 0, ≥3σ = 20
    if d <= 0:
        return 0
    if d >= 3:
        return 20
    return int(d / 3 * 20)


def _score_body_size(ob: Dict) -> int:
    """Large body candle = clean institutional order."""
    body_pct = float(ob.get('body_pct', ob.get('fvg_body_pct', 0)) or 0)
    if body_pct >= 0.70:
        return 20
    if body_pct >= 0.50:
        return 15
    if body_pct >= 0.30:
        return 10
    return 5


def _score_mitigation(ob: Dict, df: pd.DataFrame) -> int:
    """
    Fresh OB (not yet mitigated) scores higher.
    Mitigated = price has traded through the OB body since formation.
    """
    mitigated = ob.get('mitigated', False)
    if mitigated:
        return 5  # already tested — lower quality
    return 20


def _score_proximity(ob: Dict, current_price: float) -> int:
    """Price closer to OB entry = higher urgency / better fill."""
    ob_high = float(ob.get('high', 0) or 0)
    ob_low  = float(ob.get('low', 0) or 0)
    if not ob_high or not ob_low or not current_price:
        return 5
    ob_mid  = (ob_high + ob_low) / 2
    ob_size = max(ob_high - ob_low, 1)
    distance_pct = abs(current_price - ob_mid) / ob_mid if ob_mid > 0 else 1
    if distance_pct < 0.003:
        return 20   # within 0.3% of OB
    if distance_pct < 0.006:
        return 15
    if distance_pct < 0.01:
        return 10
    return 5


def _score_htf_alignment(ob: Dict, h4_bias: str, h1_bias: str) -> int:
    """OB aligned with H4 AND H1 bias = full score."""
    ob_type = str(ob.get('type', ob.get('ob_type', '')) or '').upper()
    is_bullish_ob = 'BULL' in ob_type or 'BUY' in ob_type or 'DEMAND' in ob_type
    is_bearish_ob = 'BEAR' in ob_type or 'SELL' in ob_type or 'SUPPLY' in ob_type

    h4_aligned = (is_bullish_ob and h4_bias == 'BULLISH') or \
                 (is_bearish_ob and h4_bias == 'BEARISH')
    h1_aligned = (is_bullish_ob and h1_bias == 'BULLISH') or \
                 (is_bearish_ob and h1_bias == 'BEARISH')

    if h4_aligned and h1_aligned:
        return 20
    if h4_aligned:
        return 12
    if h1_aligned:
        return 8
    return 0


def _grade(score: int) -> str:
    if score >= 80:
        return 'A+'
    if score >= 65:
        return 'A'
    if score >= 50:
        return 'B'
    if score >= 35:
        return 'C'
    return 'D'


def _empty_result() -> Dict:
    return {
        'ob_score': 0, 'ob_grade': 'D',
        'score_breakdown': {}, 'context': 'OB grader disabled or no OB data',
    }
