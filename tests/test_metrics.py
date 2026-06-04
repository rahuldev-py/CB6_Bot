# tests/test_metrics.py — Critical math sanity checks.
# If these break, every dashboard/journal/Telegram stat is lying.
import unittest
from core.metrics import (
    calc_metrics, calc_drawdown_series, calc_daily_pnl,
    calc_symbol_breakdown, r_histogram, r_multiple,
)


def t(symbol, pnl, risk=10, qty=10, entry='2026-04-23 10:00:00',
      exit='2026-04-23 11:00:00'):
    return {
        'symbol': symbol, 'pnl': pnl, 'risk': risk, 'quantity': qty,
        'original_quantity': qty, 'entry_time': entry, 'exit_time': exit,
    }


class TestRMultiple(unittest.TestCase):
    def test_winning_trade(self):
        # Risk 10×10=100, P&L 200 → 2R
        self.assertEqual(r_multiple(t('X', 200, 10, 10)), 2.0)

    def test_losing_trade(self):
        self.assertEqual(r_multiple(t('X', -100, 10, 10)), -1.0)

    def test_zero_risk_returns_zero(self):
        self.assertEqual(r_multiple(t('X', 100, 0, 10)), 0.0)

    def test_missing_quantity(self):
        self.assertEqual(r_multiple({'pnl': 100, 'risk': 10}), 0.0)


class TestMetrics(unittest.TestCase):
    def test_empty_returns_zeros(self):
        m = calc_metrics([])
        self.assertEqual(m['count'], 0)
        self.assertEqual(m['win_rate'], 0)
        self.assertEqual(m['profit_factor'], 0)

    def test_basic_winrate(self):
        trades = [t('A', 100), t('B', -50), t('C', 200), t('D', -75)]
        m = calc_metrics(trades)
        self.assertEqual(m['count'], 4)
        self.assertEqual(m['wins'], 2)
        self.assertEqual(m['losses'], 2)
        self.assertEqual(m['win_rate'], 50.0)

    def test_profit_factor(self):
        # Wins 300, Losses 100 → PF = 3.0
        trades = [t('A', 200), t('B', 100), t('C', -100)]
        m = calc_metrics(trades)
        self.assertEqual(m['profit_factor'], 3.0)

    def test_max_drawdown(self):
        # Curve: +100, -200, +50 → cum 100, -100, -50 → peak 100, max DD 200
        trades = [t('A', 100), t('B', -200), t('C', 50)]
        m = calc_metrics(trades)
        self.assertEqual(m['max_dd_rs'], 200.0)

    def test_consecutive_streaks(self):
        trades = [t('A', 100), t('B', 50), t('C', 75),
                  t('D', -50), t('E', -100), t('F', 200)]
        m = calc_metrics(trades)
        self.assertEqual(m['max_consec_w'], 3)
        self.assertEqual(m['max_consec_l'], 2)

    def test_breakeven_excluded_from_wins(self):
        trades = [t('A', 0), t('B', 100), t('C', -50)]
        m = calc_metrics(trades)
        self.assertEqual(m['wins'], 1)
        self.assertEqual(m['losses'], 1)
        self.assertEqual(m['breakevens'], 1)

    def test_expectancy_positive_system(self):
        # 60% WR, avg win 200, 40% LR, avg loss 100 → expectancy = 0.6*200 - 0.4*100 = 80
        trades = [t('A', 200), t('B', 200), t('C', 200), t('D', -100), t('E', -100)]
        # WR = 3/5 = 0.6, avg_win=200, avg_loss=100 → exp = 120 - 40 = 80
        m = calc_metrics(trades)
        self.assertAlmostEqual(m['expectancy'], 80.0, places=1)


class TestDrawdownSeries(unittest.TestCase):
    def test_includes_zero_anchor(self):
        eq, dd = calc_drawdown_series([t('A', 100)])
        self.assertEqual(eq[0], 0)
        self.assertEqual(dd[0], 0)

    def test_drawdown_only_when_below_peak(self):
        # +100 → peak 100, dd 0 | -50 → cum 50, dd 50 | +200 → cum 250, peak 250, dd 0
        trades = [t('A', 100), t('B', -50), t('C', 200)]
        eq, dd = calc_drawdown_series(trades)
        self.assertEqual(eq, [0, 100, 50, 250])
        self.assertEqual(dd, [0, 0, 50, 0])


class TestSymbolBreakdown(unittest.TestCase):
    def test_best_and_worst_split(self):
        trades = [
            t('NSE:RELIANCE-EQ', 500), t('NSE:TCS-EQ', 300),
            t('NSE:INFY-EQ', -200), t('NSE:WIPRO-EQ', -100),
        ]
        best, worst = calc_symbol_breakdown(trades, top_n=2)
        self.assertEqual(best[0][0], 'RELIANCE')
        self.assertEqual(worst[0][0], 'INFY')


class TestRHistogram(unittest.TestCase):
    def test_empty(self):
        labels, counts = r_histogram([])
        self.assertEqual(labels, [])
        self.assertEqual(counts, [])

    def test_bucketing(self):
        # -1.5R → -2R..-1R bucket (idx 2), 2.5R → 2R..3R (idx 6), 6R → >5R (last)
        labels, counts = r_histogram([-1.5, 2.5, 6.0])
        self.assertEqual(counts[2], 1)   # -2R..-1R
        self.assertEqual(counts[6], 1)   # 2R..3R
        self.assertEqual(counts[-1], 1)  # >5R
        self.assertEqual(sum(counts), 3)


if __name__ == '__main__':
    unittest.main()
