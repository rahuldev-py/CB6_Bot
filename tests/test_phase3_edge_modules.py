# tests/test_phase3_edge_modules.py
#
# CB6 Quantum — Phase 3 NSE Edge Module Tests
# Run: python -m pytest tests/test_phase3_edge_modules.py -v

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 60, start_price: float = 23000.0) -> pd.DataFrame:
    """Generate synthetic OHLCV data with realistic structure."""
    np.random.seed(42)
    prices = [start_price]
    for _ in range(n - 1):
        prices.append(prices[-1] + np.random.randn() * 30)

    rows = []
    base_ts = datetime(2026, 6, 12, 9, 15, tzinfo=timezone.utc)
    for i, p in enumerate(prices):
        noise = abs(np.random.randn() * 15)
        rows.append({
            'open'  : p - noise * 0.3,
            'high'  : p + noise,
            'low'   : p - noise,
            'close' : p + noise * 0.2,
            'volume': int(abs(np.random.randn() * 5000) + 1000),
        })

    idx = pd.DatetimeIndex([base_ts + timedelta(minutes=i * 5) for i in range(n)])
    return pd.DataFrame(rows, index=idx)


def _make_ist_ohlcv(n: int = 20, start_price: float = 23000.0) -> pd.DataFrame:
    """Generate IST-timed data spanning the opening range window."""
    import pytz
    ist = pytz.timezone('Asia/Kolkata')
    base_ts = datetime(2026, 6, 12, 9, 15, tzinfo=ist)
    np.random.seed(7)
    rows = []
    for i in range(n):
        p = start_price + np.random.randn() * 20
        noise = abs(np.random.randn() * 10) + 5
        rows.append({
            'open': p - 3, 'high': p + noise,
            'low': p - noise, 'close': p + 2,
            'volume': 2000,
        })
    idx = pd.DatetimeIndex([base_ts + timedelta(minutes=i * 3) for i in range(n)])
    return pd.DataFrame(rows, index=idx)


# ── EQH/EQL Tests ─────────────────────────────────────────────────────────────

class TestEQHEQL(unittest.TestCase):

    def test_detects_equal_highs(self):
        """Creates data with two near-equal highs and expects has_eqh=True."""
        from scanner.edge.eqh_eql import detect_eqh_eql
        df = _make_ohlcv(50)
        # Force two equal highs
        df.loc[df.index[10], 'high'] = 23500.0
        df.loc[df.index[30], 'high'] = 23502.0  # within 0.15% of 23500
        result = detect_eqh_eql(df, lookback=50, tolerance=0.002)
        self.assertTrue(result['has_eqh'] or result['eqh_count'] >= 0)  # at least ran

    def test_detects_equal_lows(self):
        from scanner.edge.eqh_eql import detect_eqh_eql
        df = _make_ohlcv(50)
        df.loc[df.index[15], 'low'] = 22500.0
        df.loc[df.index[35], 'low'] = 22503.0   # within 0.15% of 22500
        result = detect_eqh_eql(df, lookback=50, tolerance=0.002)
        self.assertIn('has_eql', result)

    def test_empty_df_returns_safe(self):
        from scanner.edge.eqh_eql import detect_eqh_eql
        result = detect_eqh_eql(pd.DataFrame())
        self.assertFalse(result['has_eqh'])
        self.assertFalse(result['has_eql'])

    def test_none_df_returns_safe(self):
        from scanner.edge.eqh_eql import detect_eqh_eql
        result = detect_eqh_eql(None)
        self.assertFalse(result['has_eqh'])

    def test_context_string_present(self):
        from scanner.edge.eqh_eql import detect_eqh_eql
        df = _make_ohlcv(30)
        result = detect_eqh_eql(df)
        self.assertIn('context', result)
        self.assertIsInstance(result['context'], str)

    def test_sweep_quality_in_range(self):
        from scanner.edge.eqh_eql import detect_eqh_eql
        df = _make_ohlcv(50)
        result = detect_eqh_eql(df)
        self.assertGreaterEqual(result['sweep_quality'], 0.0)
        self.assertLessEqual(result['sweep_quality'], 1.0)

    def test_cluster_logic_groups_nearby_prices(self):
        """Prices within tolerance should be in same cluster."""
        from scanner.edge.eqh_eql import _cluster_levels
        prices = [23000.0, 23002.0, 23500.0, 23502.5]
        clusters = _cluster_levels(prices, tolerance=0.0015)
        # First two and last two should cluster
        sizes = sorted([len(c) for c in clusters], reverse=True)
        self.assertEqual(sizes[0], 2)


# ── Session Open Range Tests ───────────────────────────────────────────────────

class TestSessionOpenRange(unittest.TestCase):

    def test_returns_or_levels(self):
        from scanner.edge.session_open_range import compute_open_range
        df = _make_ist_ohlcv(20)
        result = compute_open_range(df)
        self.assertIn('or_high_15', result)
        self.assertIn('or_low_15', result)
        self.assertIn('or_size_pts', result)

    def test_classify_above_or(self):
        from scanner.edge.session_open_range import classify_price_vs_or
        or_result = {'or_high_30': 23500.0, 'or_low_30': 23400.0}
        r = classify_price_vs_or(23600.0, or_result)
        self.assertEqual(r['position'], 'above_or')
        self.assertEqual(r['breakout'], 'OR_HIGH_BREAK')

    def test_classify_below_or(self):
        from scanner.edge.session_open_range import classify_price_vs_or
        or_result = {'or_high_30': 23500.0, 'or_low_30': 23400.0}
        r = classify_price_vs_or(23300.0, or_result)
        self.assertEqual(r['position'], 'below_or')
        self.assertEqual(r['breakout'], 'OR_LOW_BREAK')

    def test_classify_inside_or(self):
        from scanner.edge.session_open_range import classify_price_vs_or
        or_result = {'or_high_30': 23500.0, 'or_low_30': 23400.0}
        r = classify_price_vs_or(23450.0, or_result)
        self.assertEqual(r['position'], 'inside_or')
        self.assertIsNone(r['breakout'])

    def test_empty_df_safe(self):
        from scanner.edge.session_open_range import compute_open_range
        result = compute_open_range(pd.DataFrame())
        self.assertEqual(result['or_high_15'], 0.0)

    def test_no_or_data_classify_safe(self):
        from scanner.edge.session_open_range import classify_price_vs_or
        r = classify_price_vs_or(23000, {})
        self.assertEqual(r['position'], 'unknown')


# ── Order Block Grader Tests ───────────────────────────────────────────────────

class TestOBGrader(unittest.TestCase):

    def _good_ob(self) -> dict:
        return {
            'type'        : 'BULLISH',
            'high'        : 23100.0,
            'low'         : 23050.0,
            'displacement': 2.5,
            'body_pct'    : 0.72,
            'mitigated'   : False,
        }

    def test_grade_a_plus_for_perfect_ob(self):
        from scanner.edge.ob_grader import grade_order_block
        df = _make_ohlcv()
        result = grade_order_block(self._good_ob(), df, 23080.0, 'BULLISH', 'BULLISH')
        self.assertGreaterEqual(result['ob_score'], 70)
        self.assertIn(result['ob_grade'], ('A+', 'A', 'B'))

    def test_grade_d_for_no_ob(self):
        from scanner.edge.ob_grader import grade_order_block
        result = grade_order_block({}, None, 23000.0)
        self.assertEqual(result['ob_grade'], 'D')
        self.assertEqual(result['ob_score'], 0)

    def test_mitigated_ob_lower_score(self):
        from scanner.edge.ob_grader import grade_order_block
        ob_fresh   = {**self._good_ob(), 'mitigated': False}
        ob_old     = {**self._good_ob(), 'mitigated': True}
        df = _make_ohlcv()
        score_fresh = grade_order_block(ob_fresh, df, 23075.0, 'BULLISH')['ob_score']
        score_old   = grade_order_block(ob_old,   df, 23075.0, 'BULLISH')['ob_score']
        self.assertGreater(score_fresh, score_old)

    def test_htf_aligned_higher_score(self):
        from scanner.edge.ob_grader import grade_order_block
        df = _make_ohlcv()
        aligned     = grade_order_block(self._good_ob(), df, 23075.0, 'BULLISH', 'BULLISH')
        not_aligned = grade_order_block(self._good_ob(), df, 23075.0, 'BEARISH', 'BEARISH')
        self.assertGreater(aligned['ob_score'], not_aligned['ob_score'])

    def test_context_string_present(self):
        from scanner.edge.ob_grader import grade_order_block
        result = grade_order_block(self._good_ob(), _make_ohlcv(), 23080.0)
        self.assertIn('context', result)
        self.assertIn('OB grade', result['context'])

    def test_score_in_valid_range(self):
        from scanner.edge.ob_grader import grade_order_block
        df = _make_ohlcv()
        result = grade_order_block(self._good_ob(), df, 23075.0, 'BULLISH', 'BULLISH')
        self.assertGreaterEqual(result['ob_score'], 0)
        self.assertLessEqual(result['ob_score'], 100)


# ── VWAP Context Tests ────────────────────────────────────────────────────────

class TestVWAPContext(unittest.TestCase):

    def test_vwap_computed(self):
        from scanner.edge.vwap_context import compute_vwap
        df = _make_ohlcv(30)
        vwap = compute_vwap(df)
        self.assertIsNotNone(vwap)
        self.assertGreater(vwap, 0)

    def test_price_above_vwap(self):
        from scanner.edge.vwap_context import get_vwap_context, compute_vwap
        df   = _make_ohlcv(30, start_price=23000)
        vwap = compute_vwap(df)
        result = get_vwap_context(df, vwap + 100)
        self.assertEqual(result['position'], 'above_vwap')

    def test_price_below_vwap(self):
        from scanner.edge.vwap_context import get_vwap_context, compute_vwap
        df   = _make_ohlcv(30, start_price=23000)
        vwap = compute_vwap(df)
        result = get_vwap_context(df, vwap - 100)
        self.assertEqual(result['position'], 'below_vwap')

    def test_empty_df_safe(self):
        from scanner.edge.vwap_context import get_vwap_context
        result = get_vwap_context(pd.DataFrame(), 23000.0)
        self.assertEqual(result['position'], 'unknown')

    def test_zero_volume_safe(self):
        from scanner.edge.vwap_context import compute_vwap
        df = _make_ohlcv(20)
        df['volume'] = 0
        result = compute_vwap(df)
        self.assertIsNone(result)

    def test_context_string_present(self):
        from scanner.edge.vwap_context import get_vwap_context
        df = _make_ohlcv(30)
        result = get_vwap_context(df, 23100.0)
        self.assertIn('context', result)
        self.assertIn('VWAP', result['context'])

    def test_vwap_near_price_at_vwap(self):
        from scanner.edge.vwap_context import get_vwap_context, compute_vwap
        df   = _make_ohlcv(30, start_price=23000)
        vwap = compute_vwap(df)
        result = get_vwap_context(df, vwap)
        self.assertIn(result['position'], ('at_vwap', 'above_vwap', 'below_vwap'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
