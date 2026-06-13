# tests/test_phase4_gft_stability.py
#
# CB6 Quantum — Phase 4 GFT Stability Tests
# Run: python -m pytest tests/test_phase4_gft_stability.py -v

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _state(open_trades=None, capital=5000.0, daily_pnl=0.0):
    return {
        'capital'    : capital,
        'daily_pnl'  : daily_pnl,
        'open_trades': open_trades or [],
    }


def _trade(symbol='XAUUSD', direction='BUY', risk_usd=25.0, ticket=1001):
    return {
        'symbol'   : symbol,
        'direction': direction,
        'risk_usd' : risk_usd,
        'mt5_ticket': ticket,
        'id'       : ticket,
    }


# ── FTMO Registry Deactivation ─────────────────────────────────────────────────

class TestFTMODeactivated(unittest.TestCase):

    def test_ftmo_disabled_in_registry(self):
        """mt5_accounts.json must have FTMO_10K enabled=false."""
        registry_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'config', 'mt5_accounts.json',
        )
        with open(registry_path, encoding='utf-8') as f:
            registry = json.load(f)
        ftmo = registry.get('FTMO_10K', {})
        self.assertFalse(
            ftmo.get('enabled', True),
            "FTMO_10K must have enabled=false in mt5_accounts.json",
        )

    def test_gft_accounts_still_enabled(self):
        """All three GFT accounts must remain enabled."""
        registry_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'config', 'mt5_accounts.json',
        )
        with open(registry_path, encoding='utf-8') as f:
            registry = json.load(f)
        for account_id in ('GFT_5K', 'GFT_1K_INSTANT', 'GFT_10K'):  # noqa
            acc = registry.get(account_id, {})
            self.assertTrue(
                acc.get('enabled', False),
                f"{account_id} must remain enabled",
            )

    def test_gft_magic_numbers_unique(self):
        """Magic numbers must be unique across all active GFT accounts."""
        registry_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'config', 'mt5_accounts.json',
        )
        with open(registry_path, encoding='utf-8') as f:
            registry = json.load(f)
        active_magics = []
        for account_id, acc in registry.items():
            if not isinstance(acc, dict):
                continue
            if acc.get('enabled') and account_id.startswith('GFT'):
                active_magics.append(acc.get('magic'))
        self.assertEqual(
            len(active_magics),
            len(set(active_magics)),
            f"GFT magic numbers must be unique, got: {active_magics}",
        )

    def test_expected_magic_numbers(self):
        """Each GFT account must have the expected magic number."""
        registry_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'config', 'mt5_accounts.json',
        )
        with open(registry_path, encoding='utf-8') as f:
            registry = json.load(f)
        expected = {
            'GFT_5K'        : 62001,
            'GFT_1K_INSTANT': 100061,
            'GFT_10K'       : 100100,
        }
        for account_id, magic in expected.items():
            self.assertEqual(
                registry[account_id]['magic'],
                magic,
                f"{account_id} magic must be {magic}",
            )


# ── Correlation Tracker ────────────────────────────────────────────────────────

class TestCorrelationTracker(unittest.TestCase):

    def test_no_trades_clean(self):
        from forex_engine.risk.correlation_tracker import get_correlated_exposure
        r = get_correlated_exposure(_state(), 'GFT_5K')
        self.assertFalse(r['has_correlated'])
        self.assertEqual(r['correlation_level'], 'NONE')
        self.assertEqual(r['total_corr_risk_usd'], 0.0)

    def test_single_xauusd_no_correlation(self):
        from forex_engine.risk.correlation_tracker import get_correlated_exposure
        s = _state([_trade('XAUUSD', 'BUY', 25.0, 1)])
        r = get_correlated_exposure(s, 'GFT_5K')
        self.assertFalse(r['has_correlated'])
        self.assertEqual(r['correlation_level'], 'NONE')

    def test_xauusd_plus_xagusd_same_direction_high_correlated(self):
        from forex_engine.risk.correlation_tracker import get_correlated_exposure
        s = _state([
            _trade('XAUUSD', 'BUY', 25.0, 1),
            _trade('XAGUSD', 'BUY', 20.0, 2),
        ])
        r = get_correlated_exposure(s, 'GFT_5K')
        self.assertTrue(r['has_correlated'])
        self.assertEqual(r['correlation_level'], 'HIGH_SAME_DIR')
        self.assertAlmostEqual(r['total_corr_risk_usd'], 45.0)

    def test_xauusd_plus_xagusd_opposite_direction_hedged(self):
        from forex_engine.risk.correlation_tracker import get_correlated_exposure
        s = _state([
            _trade('XAUUSD', 'BUY',  25.0, 1),
            _trade('XAGUSD', 'SELL', 20.0, 2),
        ])
        r = get_correlated_exposure(s, 'GFT_5K')
        self.assertTrue(r['has_correlated'])
        self.assertEqual(r['correlation_level'], 'HEDGED')

    def test_check_new_trade_blocks_same_dir(self):
        from forex_engine.risk.correlation_tracker import check_new_trade_correlation
        s = _state([_trade('XAUUSD', 'BUY', 25.0, 1)])
        ok, reason = check_new_trade_correlation(s, 'XAGUSD', 'BUY', 'GFT_5K')
        self.assertFalse(ok)
        self.assertIn('HIGH_CORRELATED', reason)

    def test_check_new_trade_allows_opposite_dir(self):
        from forex_engine.risk.correlation_tracker import check_new_trade_correlation
        s = _state([_trade('XAUUSD', 'BUY', 25.0, 1)])
        ok, reason = check_new_trade_correlation(s, 'XAGUSD', 'SELL', 'GFT_5K')
        self.assertTrue(ok)
        self.assertIn('HEDGED', reason)

    def test_check_new_trade_usoil_not_blocked(self):
        from forex_engine.risk.correlation_tracker import check_new_trade_correlation
        s = _state([_trade('XAUUSD', 'BUY', 25.0, 1)])
        ok, reason = check_new_trade_correlation(s, 'USOIL', 'BUY', 'GFT_5K')
        # USOIL is moderate, not hard-blocked
        self.assertTrue(ok)

    def test_context_string_present(self):
        from forex_engine.risk.correlation_tracker import get_correlated_exposure
        s = _state([
            _trade('XAUUSD', 'SELL', 25.0, 1),
            _trade('XAGUSD', 'SELL', 20.0, 2),
        ])
        r = get_correlated_exposure(s, 'GFT_1K_INSTANT')
        self.assertIn('context', r)
        self.assertIn('GFT_1K_INSTANT', r['context'])


# ── Position Reconciler ────────────────────────────────────────────────────────

class TestPositionReconciler(unittest.TestCase):

    def _make_connector(self, positions):
        conn = MagicMock()
        conn.get_open_positions.return_value = positions
        return conn

    def test_clean_match_no_mismatch(self):
        from forex_engine.risk.position_reconciler import reconcile_account
        internal = _state([_trade('XAUUSD', 'BUY', 25.0, ticket=1001)])
        mt5_pos = [{'ticket': 1001, 'symbol': 'XAUUSD', 'volume': 0.05}]
        conn = self._make_connector(mt5_pos)
        r = reconcile_account('GFT_5K', internal, conn, magic=62001)
        self.assertTrue(r['ok'])
        self.assertEqual(len(r['phantom_in_mt5']), 0)
        self.assertEqual(len(r['ghost_in_state']), 0)
        self.assertIn(1001, r['matched'])

    def test_phantom_detected(self):
        """MT5 has a position not in internal state → phantom."""
        from forex_engine.risk.position_reconciler import reconcile_account
        internal = _state([])    # empty internal state
        mt5_pos = [{'ticket': 9999, 'symbol': 'XAUUSD', 'volume': 0.05}]
        conn = self._make_connector(mt5_pos)
        r = reconcile_account('GFT_5K', internal, conn, magic=62001)
        self.assertFalse(r['ok'])
        self.assertEqual(len(r['phantom_in_mt5']), 1)
        self.assertEqual(r['phantom_in_mt5'][0]['ticket'], 9999)

    def test_ghost_detected(self):
        """Internal state has a trade not in MT5 → ghost."""
        from forex_engine.risk.position_reconciler import reconcile_account
        internal = _state([_trade('XAGUSD', 'SELL', 12.0, ticket=5555)])
        mt5_pos = []   # MT5 shows nothing
        conn = self._make_connector(mt5_pos)
        r = reconcile_account('GFT_5K', internal, conn, magic=62001)
        self.assertFalse(r['ok'])
        self.assertEqual(len(r['ghost_in_state']), 1)
        self.assertEqual(r['ghost_in_state'][0]['ticket'], 5555)

    def test_connector_failure_safe(self):
        """If MT5 connector raises, reconciler returns error without crashing."""
        from forex_engine.risk.position_reconciler import reconcile_account
        conn = MagicMock()
        conn.get_open_positions.side_effect = RuntimeError("MT5 not connected")
        r = reconcile_account('GFT_5K', _state(), conn, magic=62001)
        self.assertIsNotNone(r['error'])

    def test_telegram_fn_called_on_mismatch(self):
        """Telegram alert function should be called when mismatch is found."""
        from forex_engine.risk.position_reconciler import reconcile_account
        internal = _state([])
        mt5_pos = [{'ticket': 7777, 'symbol': 'USOIL', 'volume': 0.10}]
        conn = self._make_connector(mt5_pos)
        alerts = []
        r = reconcile_account('GFT_5K', internal, conn, magic=62001,
                              telegram_fn=alerts.append)
        self.assertFalse(r['ok'])
        self.assertEqual(len(alerts), 1)
        self.assertIn('PHANTOM', alerts[0])

    def test_account_isolation_different_magic(self):
        """
        Reconciler must use the magic number provided — connector mock
        should receive the correct magic number.
        """
        from forex_engine.risk.position_reconciler import reconcile_account
        conn = self._make_connector([])
        reconcile_account('GFT_1K_INSTANT', _state(), conn, magic=100061)
        conn.get_open_positions.assert_called_once_with(magic=100061)

    def test_mismatch_details_populated(self):
        from forex_engine.risk.position_reconciler import reconcile_account
        internal = _state([])
        mt5_pos = [{'ticket': 3333, 'symbol': 'XAUUSD', 'volume': 0.05}]
        conn = self._make_connector(mt5_pos)
        r = reconcile_account('GFT_10K', internal, conn, magic=100100)
        self.assertTrue(len(r['mismatch_details']) > 0)
        self.assertIn('PHANTOM', r['mismatch_details'][0])


# ── Max Open Positions Config Sanity ──────────────────────────────────────────

class TestMaxOpenPositionsConfig(unittest.TestCase):

    def test_gft_5k_max_open_positions(self):
        from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE
        self.assertIn('max_open_positions', GFT_2STEP_PROFILE)
        self.assertGreaterEqual(GFT_2STEP_PROFILE['max_open_positions'], 1)

    def test_gft_1k_max_open_positions(self):
        from forex_engine.gft_1k_instant.config import GFT_1K_INSTANT_PROFILE
        self.assertIn('max_open_positions', GFT_1K_INSTANT_PROFILE)
        self.assertGreaterEqual(GFT_1K_INSTANT_PROFILE['max_open_positions'], 1)

    def test_gft_10k_max_open_positions(self):
        from forex_engine.gft_10k.config import GFT_10K_PROFILE
        self.assertIn('max_open_positions', GFT_10K_PROFILE)
        self.assertGreaterEqual(GFT_10K_PROFILE['max_open_positions'], 1)

    def test_exposure_guard_blocks_when_full(self):
        from forex_engine.risk.exposure_guard import check_max_open_positions
        state = _state([_trade('XAUUSD', ticket=1), _trade('XAGUSD', ticket=2)])
        ok, reason = check_max_open_positions(state, max_positions=1)
        self.assertFalse(ok)
        self.assertIn('Max open positions', reason)

    def test_exposure_guard_allows_when_free(self):
        from forex_engine.risk.exposure_guard import check_max_open_positions
        ok, _ = check_max_open_positions(_state([]), max_positions=2)
        self.assertTrue(ok)


if __name__ == '__main__':
    unittest.main(verbosity=2)
