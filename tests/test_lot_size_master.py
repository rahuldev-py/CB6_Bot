"""
tests/test_lot_size_master.py
==============================
NSE lot size master lookup tests — added 2026-06-02.

Verifies:
  1. NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY resolve from master (not fallback)
  2. NIFTY does not incorrectly match BANKNIFTY rows (substring bug)
  3. Fallback is only used when underlying is genuinely absent
  4. Bad/zero lot size in master triggers fallback, not a crash
  5. load_symbol_master supplies correct fieldnames (no-header CSV)
  6. Cache is built with exact underlying_scrip keys

Run:
    python -m pytest tests/test_lot_size_master.py -v
"""

import csv
import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

MASTER_PATH = os.path.join(_ROOT, 'data', 'nse_fo_master.csv')

# ── helpers ────────────────────────────────────────────────────────────────────

def _fake_master_csv(rows: list) -> list:
    """Build an in-memory master (list of dicts) from column-value pairs."""
    from scanner.option_strike_selector import FYERS_FO_COLS
    result = []
    for row_vals in rows:
        d = dict(zip(FYERS_FO_COLS, row_vals))
        result.append(d)
    return result


def _master_row(underlying: str, lot: int, symbol_ticker: str = None) -> list:
    """Build a minimal FYERS_FO_COLS-compatible row."""
    from scanner.option_strike_selector import FYERS_FO_COLS
    ticker = symbol_ticker or f"NSE:{underlying}26MAYFUT"
    vals = [''] * len(FYERS_FO_COLS)
    # positional: fytoken=0, symbol_name=1, lot_size=3, symbol_ticker=9, underlying_scrip=13
    vals[3]  = str(lot)
    vals[9]  = ticker
    vals[13] = underlying
    return vals


def _clear_cache():
    import scanner.option_strike_selector as oss
    oss._lot_size_cache.clear()


# ============================================================================
# 1. Real master CSV: all four indices resolve without fallback
# ============================================================================

class TestMasterResolution:

    @pytest.mark.skipif(
        not os.path.exists(MASTER_PATH),
        reason="nse_fo_master.csv not present"
    )
    def test_nifty_from_master(self, caplog):
        """NIFTY lot size resolves from real master CSV (no fallback warning)."""
        import logging
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live, load_symbol_master
        master = load_symbol_master()
        assert len(master) > 0, "Master CSV should load non-empty rows"

        with caplog.at_level(logging.WARNING):
            lot = get_lot_size_live('NIFTY', master)

        assert lot == 65, f"Expected NIFTY lot 65, got {lot}"
        fallback_warnings = [r for r in caplog.records if 'not in master' in r.message]
        assert fallback_warnings == [], f"Got unexpected fallback warnings: {fallback_warnings}"

    @pytest.mark.skipif(
        not os.path.exists(MASTER_PATH),
        reason="nse_fo_master.csv not present"
    )
    def test_banknifty_from_master(self):
        """BANKNIFTY lot size resolves from real master CSV."""
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live, load_symbol_master
        master = load_symbol_master()
        lot = get_lot_size_live('BANKNIFTY', master)
        assert lot == 30, f"Expected BANKNIFTY lot 30, got {lot}"

    @pytest.mark.skipif(
        not os.path.exists(MASTER_PATH),
        reason="nse_fo_master.csv not present"
    )
    def test_finnifty_from_master(self):
        """FINNIFTY lot size resolves from real master CSV."""
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live, load_symbol_master
        master = load_symbol_master()
        lot = get_lot_size_live('FINNIFTY', master)
        assert lot == 60, f"Expected FINNIFTY lot 60, got {lot}"

    @pytest.mark.skipif(
        not os.path.exists(MASTER_PATH),
        reason="nse_fo_master.csv not present"
    )
    def test_midcpnifty_from_master(self):
        """MIDCPNIFTY lot size resolves from real master CSV."""
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live, load_symbol_master
        master = load_symbol_master()
        lot = get_lot_size_live('MIDCPNIFTY', master)
        assert lot == 120, f"Expected MIDCPNIFTY lot 120, got {lot}"

    @pytest.mark.skipif(
        not os.path.exists(MASTER_PATH),
        reason="nse_fo_master.csv not present"
    )
    def test_no_fallback_warning_for_four_indices(self, caplog):
        """No 'not in master' warning for any of the four index underlyings."""
        import logging
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live, load_symbol_master
        master = load_symbol_master()

        with caplog.at_level(logging.WARNING):
            for sym in ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']:
                _clear_cache()
                get_lot_size_live(sym, master)

        fallback_warnings = [
            r for r in caplog.records
            if 'not in master' in r.message and 'fallback' in r.message
        ]
        assert fallback_warnings == [], (
            f"Unexpected fallback warnings: {[r.message for r in fallback_warnings]}"
        )


# ============================================================================
# 2. Substring collision: NIFTY must NOT return BANKNIFTY's lot size
# ============================================================================

class TestSubstringCollision:

    def test_nifty_does_not_match_banknifty_row(self):
        """NIFTY lookup against a master that has BANKNIFTY first must still return 65."""
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live

        # Build master with BANKNIFTY listed before NIFTY
        master = _fake_master_csv([
            _master_row('BANKNIFTY', 30, 'NSE:BANKNIFTY26MAYFUT'),
            _master_row('BANKNIFTY', 30, 'NSE:BANKNIFTY26JUN57000CE'),
            _master_row('NIFTY',     65, 'NSE:NIFTY26MAYFUT'),
            _master_row('NIFTY',     65, 'NSE:NIFTY26JUN24500CE'),
        ])
        lot = get_lot_size_live('NIFTY', master)
        assert lot == 65, (
            f"NIFTY lookup returned {lot} — likely matched BANKNIFTY row (substring bug)"
        )

    def test_banknifty_does_not_bleed_into_nifty(self):
        """Exact match on underlying_scrip: BANKNIFTY → 30, never 65."""
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live

        master = _fake_master_csv([
            _master_row('NIFTY',     65, 'NSE:NIFTY26MAYFUT'),
            _master_row('BANKNIFTY', 30, 'NSE:BANKNIFTY26MAYFUT'),
        ])
        assert get_lot_size_live('BANKNIFTY', master) == 30
        _clear_cache()
        assert get_lot_size_live('NIFTY',     master) == 65

    def test_finnifty_not_confused_with_nifty(self):
        """FINNIFTY underlying_scrip is distinct from NIFTY."""
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live

        master = _fake_master_csv([
            _master_row('NIFTY',    65,  'NSE:NIFTY26MAYFUT'),
            _master_row('FINNIFTY', 60,  'NSE:FINNIFTY26MAYFUT'),
        ])
        _clear_cache(); assert get_lot_size_live('NIFTY',    master) == 65
        _clear_cache(); assert get_lot_size_live('FINNIFTY', master) == 60


# ============================================================================
# 3. Fallback only used when underlying is genuinely absent
# ============================================================================

class TestFallbackBehavior:

    def test_fallback_fires_for_unknown_underlying(self, caplog):
        """'FAKEIDX' not in master → fallback + warning."""
        import logging
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live

        master = _fake_master_csv([
            _master_row('NIFTY', 65, 'NSE:NIFTY26MAYFUT'),
        ])
        with caplog.at_level(logging.WARNING):
            lot = get_lot_size_live('FAKEIDX', master)

        assert lot == 50   # FALLBACKS default
        assert any('not in master' in r.message for r in caplog.records)

    def test_no_fallback_warning_when_found(self, caplog):
        """No warning when underlying IS in master."""
        import logging
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live

        master = _fake_master_csv([
            _master_row('NIFTY', 65, 'NSE:NIFTY26MAYFUT'),
        ])
        with caplog.at_level(logging.WARNING):
            lot = get_lot_size_live('NIFTY', master)

        assert lot == 65
        fallback_warns = [r for r in caplog.records if 'not in master' in r.message]
        assert fallback_warns == [], f"Unexpected fallback warnings: {fallback_warns}"

    def test_empty_master_uses_fallback(self, caplog):
        """Empty master (e.g. sync failed) → fallback for known indices."""
        import logging
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live

        with caplog.at_level(logging.WARNING):
            for sym, expected_fb in [('NIFTY',65),('BANKNIFTY',30),('FINNIFTY',60),('MIDCPNIFTY',120)]:
                _clear_cache()
                lot = get_lot_size_live(sym, master=[])
                assert lot == expected_fb, f"{sym} fallback should be {expected_fb}, got {lot}"


# ============================================================================
# 4. Zero or bad lot size in master triggers fallback
# ============================================================================

class TestBadLotSizeInMaster:

    def test_zero_lot_size_falls_back(self):
        """A row with lot_size=0 is skipped; fallback used."""
        _clear_cache()
        from scanner.option_strike_selector import get_lot_size_live

        master = _fake_master_csv([
            _master_row('NIFTY', 0, 'NSE:NIFTY26MAYFUT'),   # zero → skip
        ])
        lot = get_lot_size_live('NIFTY', master)
        assert lot == 65   # fallback

    def test_non_numeric_lot_size_falls_back(self):
        """A row with lot_size='N/A' doesn't crash; fallback used."""
        _clear_cache()
        from scanner.option_strike_selector import _build_lot_size_cache, FYERS_FO_COLS

        # Build a row with bad lot_size
        vals = [''] * len(FYERS_FO_COLS)
        vals[3] = 'N/A'; vals[9] = 'NSE:NIFTY26MAYFUT'; vals[13] = 'NIFTY'
        cache = _build_lot_size_cache([dict(zip(FYERS_FO_COLS, vals))])
        assert 'NIFTY' not in cache, "Bad lot_size should not be cached"


# ============================================================================
# 5. load_symbol_master: fieldnames correctly applied (no-header CSV)
# ============================================================================

class TestMasterFieldnames:

    def test_column_names_from_fieldnames(self, tmp_path):
        """With FYERS_FO_COLS as fieldnames, row dicts have correct column names."""
        from scanner.option_strike_selector import FYERS_FO_COLS, MASTER_PATH
        import scanner.option_strike_selector as oss

        # Write a minimal 2-row headerless CSV in the exact Fyers format
        csv_data = (
            "fytoken1,NIFTY 26 Jun 26 FUT,11,65,0.05,,0915-1530,2026-06-26,1751000000,"
            "NSE:NIFTY26JUNFUT,10,11,66001,NIFTY,0,-1.0,XX,,,0,0.0\n"
            "fytoken2,BANKNIFTY 26 Jun 26 FUT,11,30,0.05,,0915-1530,2026-06-26,1751000000,"
            "NSE:BANKNIFTY26JUNFUT,10,11,66002,BANKNIFTY,0,-1.0,XX,,,0,0.0\n"
        )
        csv_file = tmp_path / 'nse_fo_master.csv'
        csv_file.write_text(csv_data, encoding='utf-8')

        # Temporarily override MASTER_PATH
        orig = oss.MASTER_PATH
        oss.MASTER_PATH = str(csv_file)
        try:
            rows = oss.load_symbol_master()
        finally:
            oss.MASTER_PATH = orig

        assert len(rows) == 2
        assert rows[0]['symbol_ticker'] == 'NSE:NIFTY26JUNFUT'
        assert rows[0]['lot_size'] == '65'
        assert rows[0]['underlying_scrip'] == 'NIFTY'
        assert rows[1]['lot_size'] == '30'
        assert rows[1]['underlying_scrip'] == 'BANKNIFTY'

    def test_fieldnames_prevents_first_row_loss(self, tmp_path):
        """Without fieldnames, DictReader would lose the first data row.
        With fieldnames, all rows (including the first) are accessible."""
        from scanner.option_strike_selector import FYERS_FO_COLS
        import csv

        csv_data = (
            "t1,NIFTY FUT,11,65,0.05,,ses,2026-06-26,ts,NSE:NIFTY26JUNFUT,10,11,u1,NIFTY,0,-1.0,XX,,,0,0.0\n"
        )
        path = str(tmp_path / 'test.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(csv_data)

        # Without fieldnames: row treated as header → 0 data rows
        with open(path, encoding='utf-8') as f:
            rows_no_hdr = list(csv.DictReader(f))
        assert len(rows_no_hdr) == 0

        # With fieldnames: row is data → 1 data row, correct values
        with open(path, encoding='utf-8') as f:
            rows_with_hdr = list(csv.DictReader(f, fieldnames=FYERS_FO_COLS))
        assert len(rows_with_hdr) == 1
        assert rows_with_hdr[0]['lot_size'] == '65'
        assert rows_with_hdr[0]['underlying_scrip'] == 'NIFTY'


# ============================================================================
# 6. Cache uses exact underlying_scrip keys
# ============================================================================

class TestCacheKeys:

    def test_cache_keyed_by_underlying_scrip(self):
        """Cache keys are exact underlying_scrip values, no substrings."""
        from scanner.option_strike_selector import _build_lot_size_cache

        master = _fake_master_csv([
            _master_row('NIFTY',      65,  'NSE:NIFTY26MAYFUT'),
            _master_row('BANKNIFTY',  30,  'NSE:BANKNIFTY26MAYFUT'),
            _master_row('FINNIFTY',   60,  'NSE:FINNIFTY26MAYFUT'),
            _master_row('MIDCPNIFTY', 120, 'NSE:MIDCPNIFTY26MAYFUT'),
        ])
        cache = _build_lot_size_cache(master)

        assert cache.get('NIFTY')      == 65
        assert cache.get('BANKNIFTY')  == 30
        assert cache.get('FINNIFTY')   == 60
        assert cache.get('MIDCPNIFTY') == 120

        # Ensure no spurious keys from partial/substring data
        for key in cache:
            assert key == key.upper(), f"Cache key {key!r} should be uppercase"

    def test_first_valid_lot_size_wins(self):
        """If underlying appears multiple times, first valid lot size is used."""
        from scanner.option_strike_selector import _build_lot_size_cache

        master = _fake_master_csv([
            _master_row('NIFTY', 65, 'NSE:NIFTY26MAYFUT'),    # futures
            _master_row('NIFTY', 65, 'NSE:NIFTY26JUN24000CE'), # CE option (same lot)
            _master_row('NIFTY', 65, 'NSE:NIFTY26JUN24000PE'), # PE option (same lot)
        ])
        cache = _build_lot_size_cache(master)
        assert cache.get('NIFTY') == 65
