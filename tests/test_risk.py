# tests/test_risk.py — Position sizing + trade-gating decision tests.
import unittest
from datetime import datetime
from core.risk import position_size, daily_loss_used, can_enter


class TestPositionSize(unittest.TestCase):
    def test_basic_sizing(self):
        # Capital 200000, 1% risk = 2000. Entry 100, SL 95 → risk/share=5 → 400 shares
        self.assertEqual(position_size(200000, 100, 95, 1.0), 400)

    def test_sl_above_entry_returns_zero(self):
        self.assertEqual(position_size(200000, 100, 105, 1.0), 0)

    def test_zero_entry_returns_zero(self):
        self.assertEqual(position_size(200000, 0, 95, 1.0), 0)

    def test_uses_strategy_default_when_pct_none(self):
        # Should not raise; uses STRATEGY.risk_per_trade_pct (1.0)
        result = position_size(200000, 100, 95)
        self.assertGreater(result, 0)


class TestDailyLossUsed(unittest.TestCase):
    def test_only_today_losses_count(self):
        today = datetime.now().strftime('%Y-%m-%d')
        trades = [
            {'pnl': -100, 'exit_time': f'{today} 10:00:00'},
            {'pnl': -50,  'exit_time': f'{today} 11:00:00'},
            {'pnl': -200, 'exit_time': '2020-01-01 10:00:00'},  # old, ignored
            {'pnl':  300, 'exit_time': f'{today} 12:00:00'},     # win, ignored
        ]
        self.assertEqual(daily_loss_used(trades), 150)

    def test_no_trades(self):
        self.assertEqual(daily_loss_used([]), 0)


class TestCanEnter(unittest.TestCase):
    def test_clean_slate_allowed(self):
        ok, reason = can_enter(
            {'daily_trades': 0, 'daily_losses': 0, 'closed_trades': []},
            capital=200000
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "OK")

    def test_blocks_on_max_trades(self):
        # STRATEGY.max_trades_per_day = 5; use exactly 5 to hit the limit.
        from config.strategy import STRATEGY
        ok, _ = can_enter(
            {'daily_trades': STRATEGY.max_trades_per_day,
             'daily_losses': 0, 'closed_trades': []},
            capital=200000
        )
        self.assertFalse(ok)

    def test_blocks_on_max_losses(self):
        # STRATEGY.max_loss_per_day = 3; use exactly 3 to hit the limit.
        from config.strategy import STRATEGY
        ok, _ = can_enter(
            {'daily_trades': 1,
             'daily_losses': STRATEGY.max_loss_per_day,
             'closed_trades': []},
            capital=200000
        )
        self.assertFalse(ok)

    def test_blocks_on_dd_limit(self):
        today = datetime.now().strftime('%Y-%m-%d')
        # can_enter() applies the absolute Rs 1,000 hard cap before the
        # percentage cap.  A -5000 loss hits the hard cap, so both checks
        # return False; we assert on the shared "Daily loss" prefix.
        ok, reason = can_enter(
            {
                'daily_trades': 1, 'daily_losses': 1,
                'closed_trades': [{'pnl': -5000, 'exit_time': f'{today} 10:00:00'}],
            },
            capital=200000
        )
        self.assertFalse(ok)
        self.assertIn("Daily loss", reason)


if __name__ == '__main__':
    unittest.main()
