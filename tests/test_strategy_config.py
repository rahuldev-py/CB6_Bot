# tests/test_strategy_config.py — Validates STRATEGY invariants. If these change, you broke risk policy.
import unittest
from datetime import time
from config.strategy import STRATEGY


class TestStrategyInvariants(unittest.TestCase):
    def test_risk_never_above_one_percent(self):
        # Hard cap — pro discipline. If you raise this, you raise it deliberately.
        self.assertLessEqual(STRATEGY.risk_per_trade_pct, 1.0)

    def test_min_rr_is_at_least_three(self):
        self.assertGreaterEqual(STRATEGY.min_rr_ratio, 3.0)

    def test_morning_kz_skips_judas(self):
        # Must start at or after 10:00 — first 30-45 min is Judas swing window
        self.assertGreaterEqual(STRATEGY.morning_kz_start, time(10, 0))

    def test_only_60min_executes(self):
        # 15min killed — too noisy for Indian equity. Only 60min runs setups.
        self.assertEqual(STRATEGY.timeframes, ('60',))
        self.assertNotIn('15', STRATEGY.timeframes)

    def test_no_entry_before_market_open_plus_buffer(self):
        self.assertGreaterEqual(STRATEGY.no_entry_before, STRATEGY.market_open)

    def test_square_off_before_close(self):
        self.assertLess(STRATEGY.square_off_time, STRATEGY.market_close)

    def test_max_loss_caps_max_trades(self):
        # If we max out losses, we shouldn't be allowed more trades — sanity check the gate
        self.assertLessEqual(STRATEGY.max_loss_per_day, STRATEGY.max_trades_per_day)


if __name__ == '__main__':
    unittest.main()
