# tests/test_phase1_persistence.py
#
# CB6 Quantum — Phase 1 Persistence Tests
#
# Run: python -m pytest tests/test_phase1_persistence.py -v
#
# Tests:
#   - NSE trade insert (options + futures)
#   - GFT $1K insert
#   - GFT $5K insert
#   - GFT $10K insert
#   - Idempotency (duplicate inserts are silently ignored)
#   - DB failure audit safety (write failure must not raise)

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Test fixtures ─────────────────────────────────────────────────────────────

def _nse_trade_fixture() -> dict:
    return {
        'symbol'         : 'NSE:NIFTY2561823200PE',
        'underlying'     : 'NSE:NIFTY50-INDEX',
        'direction'      : 'BEARISH',
        'entry_price'    : 145.50,
        'current_sl'     : 115.00,
        'target1'        : 195.00,
        'target2'        : 240.00,
        'target3'        : 290.00,
        'quantity'       : 75,
        'lot_size'       : 75,
        'order_id'       : 'FYERS_TEST_001',
        'fvg_entry_time' : '2026-06-12T10:15:00',
        'strike'         : 23200,
        'expiry'         : '2026-06-19',
        'delta'          : 0.42,
        'iv'             : 0.15,
        'theta'          : -3.2,
        'confluence'     : 8,
        'targets_hit'    : ['T1'],
    }


def _nse_setup_fixture() -> dict:
    return {
        'direction'  : 'BEARISH',
        'confluence' : 8,
        'h4_bias'    : 'BEARISH',
        'mss_type'   : 'CHOCH',
        'sim_ratio'  : 0.72,
        'fvg'        : {'low': 23180.0, 'high': 23210.0, 'size': 30.0, 'body_pct': 0.65},
        'sweep'      : {'sweep_type': 'BUY_SIDE', 'candles_ago': 3},
        'entry_signal': {
            'entry': 23250.0, 'stop_loss': 23310.0, 'target1': 23180.0,
        },
    }


def _nse_exit_fixture() -> dict:
    return {
        'exit_reason' : 'TARGET1',
        'exit_price'  : 195.00,
        'pnl'         : 3712.50,
        'r_multiple'  : 1.63,
        'hold_mins'   : 28,
    }


def _gft_trade_fixture(suffix: str = '001') -> dict:
    return {
        'id'          : f'gft_trade_{suffix}',
        'symbol'      : 'XAUUSD',
        'direction'   : 'BULLISH',
        'entry_price' : 2315.50,
        'stop_loss'   : 2308.00,
        'target1'     : 2325.00,
        'target2'     : 2332.00,
        'target3'     : 2340.00,
        'lots'        : 0.05,
        'risk_usd'    : 25.00,
        'ticket'      : f'99{suffix}',
        'entry_time'  : '2026-06-12T08:30:00',
        'h4_bias'     : 'BULLISH',
        'session'     : 'London',
        'mss_type'    : 'CHOCH',
        'score'       : 13,
        'sim_ratio'   : 0.68,
    }


def _gft_exit_fixture() -> dict:
    return {
        'hit'      : 'T2',
        'close_px' : 2332.00,
        'pnl'      : 82.50,
    }


# ── Test harness: override DB paths to temp files ─────────────────────────────

class PersistenceTestCase(unittest.TestCase):

    def setUp(self):
        """Create isolated temp DBs for each test."""
        self._tmp_trades  = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp_pattern = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp_trades.close()
        self._tmp_pattern.close()

        # Bootstrap minimal schema in temp trades DB
        conn = sqlite3.connect(self._tmp_trades.name)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                ticket INTEGER,
                market TEXT, account TEXT, broker TEXT, mode TEXT,
                symbol TEXT, direction TEXT, lots REAL, risk_usd REAL, risk_mode TEXT,
                entry_price REAL, stop_loss REAL, target1 REAL, target2 REAL, target3 REAL,
                score INTEGER, mss_type TEXT, entry_time TEXT, session TEXT,
                exit_time TEXT, exit_price REAL, exit_reason TEXT,
                pnl_usd REAL, r_multiple REAL, targets_hit TEXT,
                be_triggered INTEGER, hold_time_min INTEGER, result TEXT,
                phase TEXT, source_file TEXT, mfe_r REAL, mae_r REAL, exit_type TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                sim_ratio REAL, lot_boost REAL, is_aplus INTEGER,
                utc_hour INTEGER, day_of_week INTEGER, day_name TEXT, week_of_month INTEGER
            );
            CREATE TABLE IF NOT EXISTS trade_context (
                trade_id TEXT PRIMARY KEY,
                fvg_low REAL, fvg_high REAL, fvg_size REAL,
                fvg_equilibrium REAL, fvg_in_discount INTEGER,
                sweep_type TEXT, sweep_candles_ago INTEGER,
                sweep_confirmed INTEGER, sweep_confidence REAL,
                sweep_wick_ratio REAL, sweep_volume_spike REAL, sweep_displacement REAL,
                dol_direction TEXT, dol_price REAL, ob_present INTEGER,
                h4_bias TEXT, h1_bias TEXT, h4_aligned INTEGER, h1_aligned INTEGER,
                in_kill_zone INTEGER,
                raw_entry_json TEXT, raw_outcome_json TEXT,
                regime_4h TEXT, regime_1h TEXT,
                volatility_at_entry TEXT, adx_at_entry REAL,
                corr_nifty_bank REAL, corr_silver_oil REAL,
                oi_pcr REAL, oi_bias TEXT, oi_max_ce_strike INTEGER, oi_max_pe_strike INTEGER,
                conviction_score REAL, conviction_grade TEXT,
                conviction_components TEXT, conviction_risk_mult REAL,
                conviction_hard_block INTEGER, conviction_reasons TEXT,
                FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
            );
        """)
        conn.commit()
        conn.close()

        # Bootstrap minimal schema in temp pattern DB
        conn = sqlite3.connect(self._tmp_pattern.name)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE,
                recorded_at TEXT,
                market TEXT, account TEXT, symbol TEXT, direction TEXT,
                session TEXT, h4_bias TEXT, h4_aligned INTEGER,
                setup_type TEXT, mss_type TEXT, confluence INTEGER,
                fvg_body_pct REAL, sweep_age_ca INTEGER,
                outcome TEXT, exit_reason TEXT, targets_hit TEXT,
                pnl_usd REAL, pnl_r REAL, hold_minutes INTEGER,
                notes TEXT, entry_price REAL, sl_price REAL,
                sl_distance REAL, risk_usd REAL, risk_mode TEXT
            );
        """)
        conn.commit()
        conn.close()

        # Patch DB paths in the persistence module
        import data.persistence.trade_persistence as _tp
        self._orig_trades  = _tp._TRADES_DB
        self._orig_pattern = _tp._PATTERN_DB
        self._orig_migrated = _tp._migration_done
        _tp._TRADES_DB      = self._tmp_trades.name
        _tp._PATTERN_DB     = self._tmp_pattern.name
        _tp._migration_done = False   # force migration on each test

    def tearDown(self):
        import data.persistence.trade_persistence as _tp
        _tp._TRADES_DB      = self._orig_trades
        _tp._PATTERN_DB     = self._orig_pattern
        _tp._migration_done = self._orig_migrated
        os.unlink(self._tmp_trades.name)
        os.unlink(self._tmp_pattern.name)

    def _count_trades(self, db_path: str) -> int:
        conn = sqlite3.connect(db_path)
        c = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        conn.close()
        return c

    def _get_trade(self, db_path: str, trade_id_like: str = None) -> dict:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        if trade_id_like:
            row = conn.execute(
                "SELECT * FROM trades WHERE trade_id LIKE ?",
                (f'%{trade_id_like}%',)
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM trades ORDER BY rowid DESC LIMIT 1").fetchone()
        conn.close()
        return dict(row) if row else {}


# ── Test cases ────────────────────────────────────────────────────────────────

class TestNSETradeInsert(PersistenceTestCase):

    def test_nse_options_insert_succeeds(self):
        """NSE options trade writes exactly 1 row in trades and trade_context."""
        from data.persistence.trade_persistence import write_nse_trade
        ok = write_nse_trade(_nse_trade_fixture(), _nse_setup_fixture(), _nse_exit_fixture())
        self.assertTrue(ok)
        self.assertEqual(self._count_trades(self._tmp_trades.name), 1)
        self.assertEqual(self._count_trades(self._tmp_pattern.name), 1)

    def test_nse_trade_context_written(self):
        """trade_context row is inserted alongside trades row."""
        from data.persistence.trade_persistence import write_nse_trade
        write_nse_trade(_nse_trade_fixture(), _nse_setup_fixture(), _nse_exit_fixture())
        conn = sqlite3.connect(self._tmp_trades.name)
        ctx = conn.execute("SELECT * FROM trade_context").fetchone()
        conn.close()
        self.assertIsNotNone(ctx)

    def test_nse_correct_fields(self):
        """Core fields are stored correctly."""
        from data.persistence.trade_persistence import write_nse_trade
        write_nse_trade(_nse_trade_fixture(), _nse_setup_fixture(), _nse_exit_fixture())
        row = self._get_trade(self._tmp_trades.name)
        self.assertEqual(row['market'], 'NSE')
        self.assertEqual(row['direction'], 'BEARISH')
        self.assertEqual(row['result'], 'WIN')
        self.assertAlmostEqual(row['pnl_usd'], 3712.50, places=1)

    def test_nse_index_name_extracted(self):
        """index_name is correctly extracted from underlying symbol."""
        from data.persistence.trade_persistence import write_nse_trade
        write_nse_trade(_nse_trade_fixture(), _nse_setup_fixture(), _nse_exit_fixture())
        row = self._get_trade(self._tmp_trades.name)
        self.assertEqual(row.get('index_name'), 'NIFTY')

    def test_nse_option_type_inferred(self):
        """Option type (CE/PE) is inferred from symbol."""
        from data.persistence.trade_persistence import write_nse_trade
        write_nse_trade(_nse_trade_fixture(), _nse_setup_fixture(), _nse_exit_fixture())
        row = self._get_trade(self._tmp_trades.name)
        self.assertEqual(row.get('option_type'), 'PE')

    def test_nse_without_setup(self):
        """write_nse_trade works even when setup=None (graceful degradation)."""
        from data.persistence.trade_persistence import write_nse_trade
        ok = write_nse_trade(_nse_trade_fixture(), setup=None, exit_context=_nse_exit_fixture())
        self.assertTrue(ok)
        self.assertEqual(self._count_trades(self._tmp_trades.name), 1)

    def test_nse_loss_result(self):
        """LOSS result is set correctly for negative PnL."""
        from data.persistence.trade_persistence import write_nse_trade
        exit_ctx = {'exit_reason': 'STOP_LOSS', 'exit_price': 100.0, 'pnl': -2250.0}
        write_nse_trade(_nse_trade_fixture(), _nse_setup_fixture(), exit_ctx)
        row = self._get_trade(self._tmp_trades.name)
        self.assertEqual(row['result'], 'LOSS')


class TestGFT1KInsert(PersistenceTestCase):

    def test_gft_1k_insert_succeeds(self):
        from data.persistence.trade_persistence import write_gft_trade
        ok = write_gft_trade('GFT_1K_INSTANT', _gft_trade_fixture('1k'), _gft_exit_fixture())
        self.assertTrue(ok)
        self.assertEqual(self._count_trades(self._tmp_trades.name), 1)

    def test_gft_1k_correct_account(self):
        from data.persistence.trade_persistence import write_gft_trade
        write_gft_trade('GFT_1K_INSTANT', _gft_trade_fixture('1k2'), _gft_exit_fixture())
        row = self._get_trade(self._tmp_trades.name)
        self.assertEqual(row.get('account_id'), 'GFT_1K_INSTANT')

    def test_gft_1k_magic_number(self):
        from data.persistence.trade_persistence import write_gft_trade
        write_gft_trade('GFT_1K_INSTANT', _gft_trade_fixture('1k3'), _gft_exit_fixture())
        row = self._get_trade(self._tmp_trades.name)
        self.assertEqual(row.get('magic_number'), 100061)


class TestGFT5KInsert(PersistenceTestCase):

    def test_gft_5k_insert_succeeds(self):
        from data.persistence.trade_persistence import write_gft_trade
        ok = write_gft_trade('GFT_5K', _gft_trade_fixture('5k'), _gft_exit_fixture())
        self.assertTrue(ok)
        self.assertEqual(self._count_trades(self._tmp_trades.name), 1)

    def test_gft_5k_correct_market(self):
        from data.persistence.trade_persistence import write_gft_trade
        write_gft_trade('GFT_5K', _gft_trade_fixture('5k2'), _gft_exit_fixture())
        row = self._get_trade(self._tmp_trades.name)
        self.assertEqual(row.get('market_type'), 'FOREX')

    def test_gft_5k_win_result(self):
        from data.persistence.trade_persistence import write_gft_trade
        write_gft_trade('GFT_5K', _gft_trade_fixture('5k3'), {'hit': 'T2', 'close_px': 2332.0, 'pnl': 82.50})
        row = self._get_trade(self._tmp_trades.name)
        self.assertEqual(row['result'], 'WIN')

    def test_gft_5k_loss_result(self):
        from data.persistence.trade_persistence import write_gft_trade
        write_gft_trade('GFT_5K', _gft_trade_fixture('5k4'), {'hit': 'SL', 'close_px': 2308.0, 'pnl': -25.0})
        row = self._get_trade(self._tmp_trades.name)
        self.assertEqual(row['result'], 'LOSS')


class TestGFT10KInsert(PersistenceTestCase):

    def test_gft_10k_insert_succeeds(self):
        from data.persistence.trade_persistence import write_gft_trade
        ok = write_gft_trade('GFT_10K', _gft_trade_fixture('10k'), _gft_exit_fixture())
        self.assertTrue(ok)
        self.assertEqual(self._count_trades(self._tmp_trades.name), 1)

    def test_gft_10k_magic_number(self):
        from data.persistence.trade_persistence import write_gft_trade
        write_gft_trade('GFT_10K', _gft_trade_fixture('10k2'), _gft_exit_fixture())
        row = self._get_trade(self._tmp_trades.name)
        self.assertEqual(row.get('magic_number'), 100100)

    def test_gft_10k_pattern_db_written(self):
        """Pattern DB gets a record for GFT 10K trade."""
        from data.persistence.trade_persistence import write_gft_trade
        write_gft_trade('GFT_10K', _gft_trade_fixture('10k3'), _gft_exit_fixture())
        self.assertEqual(self._count_trades(self._tmp_pattern.name), 1)


class TestIdempotency(PersistenceTestCase):

    def test_nse_duplicate_ignored(self):
        """Writing the same NSE trade twice results in only 1 row."""
        from data.persistence.trade_persistence import write_nse_trade
        trade = _nse_trade_fixture()
        write_nse_trade(trade, _nse_setup_fixture(), _nse_exit_fixture())
        write_nse_trade(trade, _nse_setup_fixture(), _nse_exit_fixture())
        self.assertEqual(self._count_trades(self._tmp_trades.name), 1)

    def test_gft_duplicate_ignored(self):
        """Writing the same GFT trade twice results in only 1 row."""
        from data.persistence.trade_persistence import write_gft_trade
        trade = _gft_trade_fixture('dup')
        write_gft_trade('GFT_5K', trade, _gft_exit_fixture())
        write_gft_trade('GFT_5K', trade, _gft_exit_fixture())
        self.assertEqual(self._count_trades(self._tmp_trades.name), 1)

    def test_pattern_db_duplicate_ignored(self):
        """Pattern DB also ignores duplicate trade_id."""
        from data.persistence.trade_persistence import write_gft_trade
        trade = _gft_trade_fixture('patdup')
        write_gft_trade('GFT_5K', trade, _gft_exit_fixture())
        write_gft_trade('GFT_5K', trade, _gft_exit_fixture())
        self.assertEqual(self._count_trades(self._tmp_pattern.name), 1)

    def test_concurrent_writes_idempotent(self):
        """Concurrent writes of same trade from multiple threads produce 1 row."""
        from data.persistence.trade_persistence import write_gft_trade
        trade = _gft_trade_fixture('concurrent')
        results = []

        def _write():
            ok = write_gft_trade('GFT_5K', trade, _gft_exit_fixture())
            results.append(ok)

        threads = [threading.Thread(target=_write) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All calls return True (or at least don't crash)
        # DB must have exactly 1 row
        self.assertEqual(self._count_trades(self._tmp_trades.name), 1)


class TestFailureSafety(PersistenceTestCase):

    def test_corrupt_db_path_returns_false(self):
        """write_nse_trade returns False (never raises) when DB is unreachable."""
        import data.persistence.trade_persistence as _tp
        _tp._TRADES_DB  = '/nonexistent/path/cb6.db'
        _tp._PATTERN_DB = '/nonexistent/path/pat.db'
        from data.persistence.trade_persistence import write_nse_trade
        ok = write_nse_trade(_nse_trade_fixture(), _nse_setup_fixture(), _nse_exit_fixture())
        self.assertFalse(ok)

    def test_corrupt_db_gft_returns_false(self):
        """write_gft_trade returns False (never raises) when DB is unreachable."""
        import data.persistence.trade_persistence as _tp
        _tp._TRADES_DB  = '/nonexistent/path/cb6.db'
        _tp._PATTERN_DB = '/nonexistent/path/pat.db'
        from data.persistence.trade_persistence import write_gft_trade
        ok = write_gft_trade('GFT_5K', _gft_trade_fixture('fail'), _gft_exit_fixture())
        self.assertFalse(ok)

    def test_db_failure_writes_audit_log(self):
        """DB failure calls audit_log.append (captured via mock)."""
        import data.persistence.trade_persistence as _tp
        _tp._TRADES_DB  = '/nonexistent/path/cb6.db'
        _tp._PATTERN_DB = '/nonexistent/path/pat.db'

        audit_calls = []
        with patch('utils.audit_log.append', side_effect=lambda *a, **kw: audit_calls.append((a, kw))):
            from data.persistence.trade_persistence import write_nse_trade
            write_nse_trade(_nse_trade_fixture(), _nse_setup_fixture(), _nse_exit_fixture())

        # At least one audit call for DB failure
        self.assertGreater(len(audit_calls), 0)

    def test_none_exit_context_safe(self):
        """Passing exit_context=None does not crash the writer."""
        from data.persistence.trade_persistence import write_nse_trade
        ok = write_nse_trade(_nse_trade_fixture(), _nse_setup_fixture(), exit_context=None)
        self.assertTrue(ok)

    def test_empty_trade_dict_safe(self):
        """write_gft_trade with empty trade dict does not raise."""
        from data.persistence.trade_persistence import write_gft_trade
        ok = write_gft_trade('GFT_5K', {}, {})
        # May succeed or fail depending on constraints — must never raise
        self.assertIn(ok, (True, False))

    def test_live_trading_continues_after_db_failure(self):
        """Simulates DB being locked — write fails gracefully, no exception propagates."""
        import data.persistence.trade_persistence as _tp
        original = _tp._connect_trades

        def _broken_connect():
            raise sqlite3.OperationalError("database is locked")

        _tp._connect_trades = _broken_connect
        try:
            from data.persistence.trade_persistence import write_nse_trade
            ok = write_nse_trade(_nse_trade_fixture(), _nse_setup_fixture(), _nse_exit_fixture())
            self.assertFalse(ok)
        finally:
            _tp._connect_trades = original


class TestAccountIsolation(PersistenceTestCase):
    """Verify GFT $1K / $5K / $10K trades are stored with separate account_ids."""

    def test_three_accounts_produce_three_rows(self):
        from data.persistence.trade_persistence import write_gft_trade
        write_gft_trade('GFT_1K_INSTANT', _gft_trade_fixture('iso1k'), _gft_exit_fixture())
        write_gft_trade('GFT_5K',         _gft_trade_fixture('iso5k'), _gft_exit_fixture())
        write_gft_trade('GFT_10K',        _gft_trade_fixture('iso10k'), _gft_exit_fixture())
        self.assertEqual(self._count_trades(self._tmp_trades.name), 3)

    def test_account_ids_are_unique(self):
        from data.persistence.trade_persistence import write_gft_trade
        write_gft_trade('GFT_1K_INSTANT', _gft_trade_fixture('aiiso1k'), _gft_exit_fixture())
        write_gft_trade('GFT_5K',         _gft_trade_fixture('aiiso5k'), _gft_exit_fixture())
        write_gft_trade('GFT_10K',        _gft_trade_fixture('aiiso10k'), _gft_exit_fixture())

        conn = sqlite3.connect(self._tmp_trades.name)
        ids = {r[0] for r in conn.execute("SELECT account_id FROM trades").fetchall()}
        conn.close()
        self.assertEqual(ids, {'GFT_1K_INSTANT', 'GFT_5K', 'GFT_10K'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
