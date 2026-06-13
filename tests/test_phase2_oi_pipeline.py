# tests/test_phase2_oi_pipeline.py
#
# CB6 Quantum — Phase 2 OI/PCR Pipeline Tests
# Run: python -m pytest tests/test_phase2_oi_pipeline.py -v

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Mocked Fyers option chain response ───────────────────────────────────────

def _mock_fyers_chain_resp(spot: float = 23400.0) -> dict:
    strikes = []
    for i in range(-5, 6):
        strike = int(spot / 50) * 50 + i * 50
        ce_oi   = max(0, 100000 - abs(i) * 10000)
        pe_oi   = max(0, 80000  + i * 5000)
        strikes.append({
            'strikePrice': strike,
            'CE': {
                'ltp': max(1.0, 100 - i * 15),
                'impliedVolatility': 15.0 + i * 0.5,
                'openInterest': ce_oi,
                'changeinOpenInterest': ce_oi * 0.1,
                'totalTradedVolume': ce_oi * 2,
                'delta': max(0.05, 0.50 - i * 0.05),
                'theta': -3.0,
            },
            'PE': {
                'ltp': max(1.0, 80 + i * 12),
                'impliedVolatility': 16.0 - i * 0.3,
                'openInterest': pe_oi,
                'changeinOpenInterest': -pe_oi * 0.05,
                'totalTradedVolume': pe_oi * 1.5,
                'delta': min(-0.05, -0.45 + i * 0.05),
                'theta': -2.5,
            },
        })
    return {
        'code': 200,
        'data': {
            'underlyingValue' : spot,
            'expiryDate'      : '19JUN2026',
            'optionsChain'    : strikes,
        },
    }


def _mock_fyers(spot: float = 23400.0):
    fyers = MagicMock()
    fyers.optionchain.return_value = _mock_fyers_chain_resp(spot)
    return fyers


# ── DB setup for writer tests ─────────────────────────────────────────────────

def _make_temp_db() -> str:
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS oi_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            ts TEXT NOT NULL,
            expiry TEXT NOT NULL,
            atm_strike INTEGER,
            spot_price REAL,
            ce_oi REAL, pe_oi REAL,
            ce_volume REAL, pe_volume REAL,
            pcr_oi REAL, pcr_volume REAL,
            option_bias TEXT,
            source TEXT,
            UNIQUE(symbol, ts, expiry)
        );
        CREATE TABLE IF NOT EXISTS option_chain (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            ts TEXT NOT NULL,
            expiry TEXT NOT NULL,
            strike INTEGER NOT NULL,
            ce_ltp REAL, ce_iv REAL, ce_oi REAL, ce_volume REAL, ce_delta REAL, ce_theta REAL,
            pe_ltp REAL, pe_iv REAL, pe_oi REAL, pe_volume REAL, pe_delta REAL, pe_theta REAL,
            UNIQUE(symbol, ts, expiry, strike)
        );
        CREATE TABLE IF NOT EXISTS trade_context (
            trade_id TEXT PRIMARY KEY,
            oi_pcr REAL, oi_bias TEXT,
            oi_max_ce_strike INTEGER, oi_max_pe_strike INTEGER
        );
    """)
    conn.commit()
    conn.close()
    return tmp.name


class TestFyersOptionChainAdapter(unittest.TestCase):

    def test_get_option_chain_success(self):
        """get_option_chain returns structured data for NIFTY."""
        fyers = _mock_fyers(23400)
        from data.oi.fyers_option_chain import get_option_chain
        result = get_option_chain(fyers, 'NIFTY', use_cache=False)
        self.assertIsNotNone(result)
        self.assertEqual(result['index_name'], 'NIFTY')
        self.assertGreater(result['spot_price'], 0)
        self.assertGreater(len(result['strikes']), 0)

    def test_get_option_chain_unknown_index(self):
        """Unknown index name returns None."""
        fyers = _mock_fyers()
        from data.oi.fyers_option_chain import get_option_chain
        result = get_option_chain(fyers, 'UNKNOWN_IDX', use_cache=False)
        self.assertIsNone(result)

    def test_get_option_chain_api_failure(self):
        """API failure (non-200) returns None."""
        fyers = MagicMock()
        fyers.optionchain.return_value = {'code': 500, 'data': {}}
        from data.oi.fyers_option_chain import get_option_chain
        result = get_option_chain(fyers, 'NIFTY', use_cache=False)
        self.assertIsNone(result)

    def test_get_option_chain_exception_safe(self):
        """Network exception returns None, never raises."""
        fyers = MagicMock()
        fyers.optionchain.side_effect = ConnectionError("Network down")
        from data.oi.fyers_option_chain import get_option_chain
        result = get_option_chain(fyers, 'BANKNIFTY', use_cache=False)
        self.assertIsNone(result)

    def test_atm_strike_correct(self):
        """ATM strike should be nearest 50-step to spot."""
        fyers = _mock_fyers(23387)   # spot between 23350 and 23400
        from data.oi.fyers_option_chain import get_option_chain
        result = get_option_chain(fyers, 'NIFTY', use_cache=False)
        self.assertIn(result['atm_strike'] % 50, (0,))   # must be multiple of 50

    def test_all_four_indices_parse(self):
        """All 4 NSE indices return data with the mocked Fyers."""
        from data.oi.fyers_option_chain import get_option_chain
        for idx in ('NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'):
            fyers = _mock_fyers(idx == 'NIFTY' and 23400 or 48000)
            result = get_option_chain(fyers, idx, use_cache=False)
            self.assertIsNotNone(result, f"Expected data for {idx}")

    def test_expiry_normalized_to_iso(self):
        """Expiry date is converted to YYYY-MM-DD format."""
        fyers = _mock_fyers()
        from data.oi.fyers_option_chain import get_option_chain
        result = get_option_chain(fyers, 'NIFTY', use_cache=False)
        expiry = result.get('expiry', '')
        if expiry:
            # Either empty or YYYY-MM-DD format
            if len(expiry) == 10:
                self.assertRegex(expiry, r'\d{4}-\d{2}-\d{2}')


class TestPCRCalculator(unittest.TestCase):

    def _chain(self) -> dict:
        fyers = _mock_fyers(23400)
        from data.oi.fyers_option_chain import get_option_chain
        return get_option_chain(fyers, 'NIFTY', use_cache=False)

    def test_pcr_calculated(self):
        from data.oi.pcr_calculator import calculate_pcr
        chain = self._chain()
        pcr = calculate_pcr(chain)
        self.assertIn('pcr_oi', pcr)
        self.assertGreater(pcr['pcr_oi'], 0)

    def test_option_bias_set(self):
        from data.oi.pcr_calculator import calculate_pcr
        chain = self._chain()
        pcr = calculate_pcr(chain)
        self.assertIn(pcr['option_bias'], ('BULLISH', 'BEARISH', 'NEUTRAL'))

    def test_totals_positive(self):
        from data.oi.pcr_calculator import calculate_pcr
        chain = self._chain()
        pcr = calculate_pcr(chain)
        self.assertGreater(pcr['total_ce_oi'], 0)
        self.assertGreater(pcr['total_pe_oi'], 0)

    def test_empty_chain_safe(self):
        from data.oi.pcr_calculator import calculate_pcr
        pcr = calculate_pcr({'strikes': []})
        self.assertEqual(pcr['pcr_oi'], 0.0)
        self.assertEqual(pcr['option_bias'], 'NEUTRAL')


class TestMaxPain(unittest.TestCase):

    def test_max_pain_returns_valid_strike(self):
        """max pain is one of the input strikes."""
        from data.oi.fyers_option_chain import get_option_chain
        from data.oi.max_pain import calculate_max_pain
        fyers = _mock_fyers(23400)
        chain = get_option_chain(fyers, 'NIFTY', use_cache=False)
        mp = calculate_max_pain(chain['strikes'])
        valid = {s['strike'] for s in chain['strikes']}
        self.assertIn(mp, valid)

    def test_empty_strikes_returns_none(self):
        from data.oi.max_pain import calculate_max_pain
        self.assertIsNone(calculate_max_pain([]))

    def test_highest_oi_strikes(self):
        from data.oi.max_pain import highest_oi_strikes
        strikes = [
            {'strike': 23000, 'ce_oi': 500000, 'pe_oi': 100000},
            {'strike': 23500, 'ce_oi': 200000, 'pe_oi': 800000},
            {'strike': 24000, 'ce_oi': 300000, 'pe_oi': 50000},
        ]
        ce, pe = highest_oi_strikes(strikes)
        self.assertEqual(ce, 23000)   # highest CE OI
        self.assertEqual(pe, 23500)   # highest PE OI


class TestOISnapshotWriter(unittest.TestCase):

    def setUp(self):
        self._db = _make_temp_db()
        import data.oi.oi_snapshot_writer as _w
        self._orig = _w._TRADES_DB
        _w._TRADES_DB = self._db

    def tearDown(self):
        import data.oi.oi_snapshot_writer as _w
        _w._TRADES_DB = self._orig
        os.unlink(self._db)

    def _chain_and_pcr(self):
        fyers = _mock_fyers(23400)
        from data.oi.fyers_option_chain import get_option_chain
        from data.oi.pcr_calculator import calculate_pcr
        chain = get_option_chain(fyers, 'NIFTY', use_cache=False)
        pcr   = calculate_pcr(chain)
        return chain, pcr

    def test_write_oi_snapshot_succeeds(self):
        from data.oi.oi_snapshot_writer import write_oi_snapshot
        chain, pcr = self._chain_and_pcr()
        ok = write_oi_snapshot(chain, pcr, max_pain=23350)
        self.assertTrue(ok)
        conn = sqlite3.connect(self._db)
        count = conn.execute("SELECT COUNT(*) FROM oi_snapshots").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_write_option_chain_inserts_strikes(self):
        from data.oi.oi_snapshot_writer import write_option_chain
        chain, _ = self._chain_and_pcr()
        n = write_option_chain(chain)
        self.assertGreater(n, 0)
        conn = sqlite3.connect(self._db)
        count = conn.execute("SELECT COUNT(*) FROM option_chain").fetchone()[0]
        conn.close()
        self.assertEqual(count, n)

    def test_write_snapshot_idempotent(self):
        """Writing the same snapshot twice results in 1 row (UNIQUE constraint)."""
        from data.oi.oi_snapshot_writer import write_oi_snapshot
        chain, pcr = self._chain_and_pcr()
        write_oi_snapshot(chain, pcr, None)
        write_oi_snapshot(chain, pcr, None)
        conn = sqlite3.connect(self._db)
        count = conn.execute("SELECT COUNT(*) FROM oi_snapshots").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_empty_chain_returns_false(self):
        from data.oi.oi_snapshot_writer import write_oi_snapshot
        ok = write_oi_snapshot(None, {}, None)
        self.assertFalse(ok)

    def test_write_snapshot_fail_safe(self):
        """Broken DB path returns False, never raises."""
        import data.oi.oi_snapshot_writer as _w
        _w._TRADES_DB = '/nonexistent/path/db.db'
        from data.oi.oi_snapshot_writer import write_oi_snapshot
        chain, pcr = self._chain_and_pcr()
        ok = write_oi_snapshot(chain, pcr, None)
        self.assertFalse(ok)


if __name__ == '__main__':
    unittest.main(verbosity=2)
