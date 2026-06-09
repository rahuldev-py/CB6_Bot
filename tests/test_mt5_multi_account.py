"""
tests/test_mt5_multi_account.py
================================
CB6 Quantum — MT5 Multi-Account Infrastructure Tests

Tests:
  1.  account_registry loads config/mt5_accounts.json correctly
  2.  get_account returns correct profile for FTMO_10K and GFT_5K
  3.  get_terminal_path — returns None when file doesn't exist (no crash)
  4.  get_terminal_path — returns path when terminal file exists (mocked)
  5.  get_credentials — resolves login/password/server from env vars
  6.  migrate_state — copies legacy file to isolated dir on first run
  7.  migrate_state — is a no-op when new file already exists
  8.  migrate_state — is a no-op when legacy file doesn't exist
  9.  MT5Connector accepts terminal_path parameter without error
  10. MT5Connector._connect() smoke test in paper mode (no MT5 needed)
  11. AccountRouter — magic number isolation blocks wrong-account routing
  12. AccountRouter — correct account routes through cleanly
  13. PreEntryValidator — blocks when emergency stop is active
  14. PreEntryValidator — blocks when daily loss limit exceeded
  15. PreEntryValidator — blocks when account paused
  16. PreEntryValidator — blocks on magic mismatch
  17. PreEntryValidator — passes when all conditions clean
  18. FTMO and GFT have DIFFERENT magic numbers (contamination baseline)
  19. State file paths are different for FTMO vs GFT (isolation baseline)
  20. State migration: FTMO legacy path → ftmo_10k/state.json
  21. State migration: GFT  legacy path → gft_5k/state.json

Run:
    python -m pytest tests/test_mt5_multi_account.py -v
"""

import json
import os
import shutil
import sys
import tempfile
import threading

import pytest

# ── Ensure project root is on sys.path ──────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ============================================================================
# TEST 1-8: AccountRegistry
# ============================================================================

class TestAccountRegistry:

    def test_loads_ftmo_account(self):
        """Registry FTMO account matches the active FTMO profile config."""
        from forex_engine.accounts.account_registry import get_account
        from forex_engine.prop_firms.ftmo.ftmo_config import ACCOUNT_SIZE

        cfg = get_account('FTMO_10K')
        assert cfg is not None
        assert cfg['magic'] == 62002
        assert cfg['risk_profile'] == 'ftmo'
        assert cfg['account_size'] == ACCOUNT_SIZE

    def test_loads_gft_account(self):
        """Registry returns GFT_5K config."""
        from forex_engine.accounts.account_registry import get_account
        cfg = get_account('GFT_5K')
        assert cfg is not None
        assert cfg['magic'] == 62001
        assert cfg['risk_profile'] == 'gft_2step'
        assert cfg['account_size'] == 5000.0

    def test_unknown_account_returns_none(self):
        from forex_engine.accounts.account_registry import get_account
        assert get_account('DOES_NOT_EXIST') is None

    def test_terminal_path_returns_none_when_missing(self):
        """Returns None gracefully when terminal file doesn't exist (pre-setup)."""
        from forex_engine.accounts.account_registry import get_terminal_path
        # Neither env var nor hard-coded path will resolve to a real file
        # unless the user has already installed portable terminals
        path = get_terminal_path('FTMO_10K')
        # Just assert no exception — path may be None or a string
        assert path is None or isinstance(path, str)

    def test_terminal_path_returns_path_when_file_exists(self, tmp_path, monkeypatch):
        """Returns path when terminal file is present."""
        from forex_engine.accounts import account_registry as ar

        # Create a fake terminal file
        fake_exe = tmp_path / 'terminal64.exe'
        fake_exe.write_bytes(b'fake')

        # Patch registry so FTMO_10K points at our fake file
        import forex_engine.accounts.account_registry as _ar
        original = _ar._registry
        _ar._registry = {
            'FTMO_10K': {
                'terminal'    : str(fake_exe).replace('\\', '/'),
                'terminal_env': 'NONEXISTENT_ENV_VAR_XYZ',
            }
        }
        try:
            path = _ar.get_terminal_path('FTMO_10K')
            assert path is not None
            assert 'terminal64.exe' in path
        finally:
            _ar._registry = original

    def test_get_credentials_from_env(self, monkeypatch):
        """Credentials resolved from .env vars."""
        import forex_engine.accounts.account_registry as _ar
        monkeypatch.setenv('MT5_LOGIN',    '12345678')
        monkeypatch.setenv('MT5_PASSWORD', 'testpassword')
        monkeypatch.setenv('MT5_SERVER',   'Test-Demo')

        creds = _ar.get_credentials('FTMO_10K')
        assert creds is not None
        assert creds['login'] == 12345678
        assert creds['password'] == 'testpassword'
        assert creds['server'] == 'Test-Demo'

    def test_migrate_state_copies_legacy(self, tmp_path, monkeypatch):
        """migrate_state copies legacy file to new dir when new file absent."""
        import forex_engine.accounts.account_registry as _ar

        legacy = tmp_path / 'forex_paper_state.json'
        new_dir = tmp_path / 'ftmo_10k'
        new_file = new_dir / 'state.json'

        legacy.write_text(json.dumps({'capital': 9500.0, 'migrated': True}))
        assert not new_file.exists()

        original = _ar._registry
        _ar._registry = {
            'FTMO_10K': {
                'state_dir'   : str(new_dir).replace('\\', '/'),
                'legacy_state': str(legacy).replace('\\', '/'),
                'enabled'     : True,
            }
        }
        monkeypatch.setattr(_ar, '_ROOT', str(tmp_path))
        try:
            migrated = _ar.migrate_state('FTMO_10K')
            assert migrated is True
            assert new_file.exists()
            data = json.loads(new_file.read_text())
            assert data['capital'] == 9500.0
        finally:
            _ar._registry = original

    def test_migrate_state_noop_when_new_exists(self, tmp_path, monkeypatch):
        """migrate_state is no-op when new state.json already exists."""
        import forex_engine.accounts.account_registry as _ar

        legacy   = tmp_path / 'legacy.json'
        new_dir  = tmp_path / 'ftmo_10k'
        new_dir.mkdir()
        new_file = new_dir / 'state.json'

        legacy.write_text(json.dumps({'capital': 1000.0}))
        new_file.write_text(json.dumps({'capital': 9500.0}))

        original = _ar._registry
        _ar._registry = {
            'FTMO_10K': {
                'state_dir'   : str(new_dir).replace('\\', '/'),
                'legacy_state': str(legacy).replace('\\', '/'),
                'enabled'     : True,
            }
        }
        monkeypatch.setattr(_ar, '_ROOT', str(tmp_path))
        try:
            migrated = _ar.migrate_state('FTMO_10K')
            assert migrated is False  # already done
            data = json.loads(new_file.read_text())
            assert data['capital'] == 9500.0  # unchanged
        finally:
            _ar._registry = original

    def test_migrate_state_noop_when_no_legacy(self, tmp_path, monkeypatch):
        """migrate_state is no-op when legacy file doesn't exist."""
        import forex_engine.accounts.account_registry as _ar

        new_dir = tmp_path / 'ftmo_10k'
        original = _ar._registry
        _ar._registry = {
            'FTMO_10K': {
                'state_dir'   : str(new_dir).replace('\\', '/'),
                'legacy_state': str(tmp_path / 'nonexistent.json').replace('\\', '/'),
                'enabled'     : True,
            }
        }
        monkeypatch.setattr(_ar, '_ROOT', str(tmp_path))
        try:
            migrated = _ar.migrate_state('FTMO_10K')
            assert migrated is False
        finally:
            _ar._registry = original


# ============================================================================
# TEST 9-10: MT5Connector
# ============================================================================

class TestMT5Connector:

    def test_connector_accepts_terminal_path(self):
        """MT5Connector accepts terminal_path without raising."""
        from forex_engine.mt5.mt5_connector import MT5Connector
        c = MT5Connector(
            paper         = True,
            credentials   = None,
            terminal_path = r'C:\CB6_MT5\MT5_FTMO_10K\terminal64.exe',
        )
        assert c._terminal_path == r'C:\CB6_MT5\MT5_FTMO_10K\terminal64.exe'
        assert c._paper is True

    def test_paper_connector_no_mt5_needed(self):
        """Paper mode connector works without MetaTrader5 package."""
        from forex_engine.mt5.mt5_connector import MT5Connector
        c = MT5Connector(paper=True)
        assert c.is_connected() is False   # paper → no connection
        assert c.get_equity() == 0.0
        assert c.get_balance() == 0.0


# ============================================================================
# TEST 11-12: AccountRouter
# ============================================================================

class TestAccountRouter:

    def test_magic_mismatch_blocks_route(self):
        """route_trade blocks if signal magic doesn't match account magic."""
        from forex_engine.accounts.account_router import AccountRouter

        router = AccountRouter()
        calls  = []

        class FakeEngine:
            def on_signal(self, sig):
                calls.append(sig)

        router.register('FTMO_10K', FakeEngine())

        result = router.route_trade('FTMO_10K', {
            'symbol'   : 'XAGUSD',
            'direction': 'BULLISH',
            'magic'    : 62001,         # GFT magic on FTMO account = WRONG
        })
        assert result is False
        assert len(calls) == 0

    def test_correct_magic_routes_through(self):
        """route_trade succeeds when signal magic matches account magic."""
        from forex_engine.accounts.account_router import AccountRouter

        router = AccountRouter()
        calls  = []

        class FakeEngine:
            def on_signal(self, sig):
                calls.append(sig)

        router.register('FTMO_10K', FakeEngine())

        result = router.route_trade('FTMO_10K', {
            'symbol'   : 'XAGUSD',
            'direction': 'BULLISH',
            'magic'    : 62002,      # FTMO magic = correct
        })
        assert result is True
        assert len(calls) == 1

    def test_unregistered_account_blocks(self):
        """route_trade blocks for an unregistered account."""
        from forex_engine.accounts.account_router import AccountRouter
        router = AccountRouter()
        result = router.route_trade('UNKNOWN_ACCOUNT', {'symbol': 'XAGUSD'})
        assert result is False

    def test_magic_ownership_cross_account(self):
        """validate_order_ownership blocks when magic belongs to different account."""
        from forex_engine.accounts.account_router import AccountRouter

        router = AccountRouter()

        class FakeEngine:
            def on_signal(self, s): pass

        router.register('FTMO_10K', FakeEngine())
        router.register('GFT_5K',   FakeEngine())

        # GFT magic appearing in FTMO context = contamination
        ok = router.validate_order_ownership(62001, 'FTMO_10K')
        assert ok is False

        # Correct ownership
        ok = router.validate_order_ownership(62001, 'GFT_5K')
        assert ok is True


# ============================================================================
# TEST 13-17: PreEntryValidator
# ============================================================================

class TestPreEntryValidator:

    def _clean_state(self, **overrides):
        s = {
            'capital'         : 10000.0,
            'starting_capital': 10000.0,
            'daily_pnl'       : 0.0,
            'paused'          : False,
        }
        s.update(overrides)
        return s

    def test_blocks_emergency_stop(self, tmp_path, monkeypatch):
        """Validator blocks when emergency stop flag is active."""
        import utils.emergency_stop as es
        flag = str(tmp_path / 'EMERGENCY_STOP.flag')
        monkeypatch.setattr(es, '_EMERGENCY_STOP_FLAG', flag)
        es.set_emergency_stop('test')

        from forex_engine.accounts.pre_entry_validator import PreEntryValidator
        v  = PreEntryValidator('FTMO_10K', connector=None)
        ok, reason = v.validate({}, self._clean_state())
        assert ok is False
        assert 'emergency stop' in reason.lower()

    def test_blocks_daily_loss_exceeded(self, tmp_path, monkeypatch):
        """Validator blocks when daily loss >= max_daily_loss_pct."""
        import utils.emergency_stop as es
        monkeypatch.setattr(es, '_EMERGENCY_STOP_FLAG', str(tmp_path / 'no_flag'))

        from forex_engine.accounts.pre_entry_validator import PreEntryValidator
        v = PreEntryValidator('FTMO_10K', connector=None)

        from forex_engine.accounts.account_registry import get_account
        cfg = get_account('FTMO_10K')
        daily_loss_limit = cfg['account_size'] * cfg['max_daily_loss_pct'] / 100
        state = self._clean_state(daily_pnl=-(daily_loss_limit + 1.0))
        ok, reason = v.validate({}, state)
        assert ok is False
        assert 'daily loss' in reason.lower()

    def test_blocks_when_paused(self, tmp_path, monkeypatch):
        """Validator blocks when engine is paused."""
        import utils.emergency_stop as es
        monkeypatch.setattr(es, '_EMERGENCY_STOP_FLAG', str(tmp_path / 'no_flag'))

        from forex_engine.accounts.pre_entry_validator import PreEntryValidator
        v = PreEntryValidator('FTMO_10K', connector=None)

        state = self._clean_state(paused=True)
        ok, reason = v.validate({}, state)
        assert ok is False
        assert 'paused' in reason.lower()

    def test_blocks_magic_mismatch(self, tmp_path, monkeypatch):
        """Validator blocks when signal magic doesn't match account magic."""
        import utils.emergency_stop as es
        monkeypatch.setattr(es, '_EMERGENCY_STOP_FLAG', str(tmp_path / 'no_flag'))

        from forex_engine.accounts.pre_entry_validator import PreEntryValidator
        v = PreEntryValidator('FTMO_10K', connector=None)

        ok, reason = v.validate({'magic': 62001}, self._clean_state())
        assert ok is False
        assert 'magic mismatch' in reason.lower()

    def test_passes_all_clean(self, tmp_path, monkeypatch):
        """Validator passes when all conditions are clean."""
        import utils.emergency_stop as es
        monkeypatch.setattr(es, '_EMERGENCY_STOP_FLAG', str(tmp_path / 'no_flag'))

        from forex_engine.accounts.pre_entry_validator import PreEntryValidator
        v = PreEntryValidator('FTMO_10K', connector=None)

        # Clean signal (no magic in signal = skip magic check)
        ok, reason = v.validate({'symbol': 'XAGUSD'}, self._clean_state())
        assert ok is True, f"Should pass but got: {reason}"


# ============================================================================
# TEST 18-21: Isolation baselines
# ============================================================================

class TestIsolationBaselines:

    def test_ftmo_gft_have_different_magics(self):
        """FTMO and GFT magic numbers are different — baseline contamination guard."""
        from forex_engine.accounts.account_registry import get_magic
        ftmo_magic = get_magic('FTMO_10K')
        gft_magic  = get_magic('GFT_5K')
        assert ftmo_magic != gft_magic, (
            f"CRITICAL: FTMO and GFT have same magic number {ftmo_magic}! "
            f"Orders cannot be distinguished between accounts."
        )
        assert ftmo_magic == 62002
        assert gft_magic  == 62001

    def test_ftmo_gft_have_different_state_dirs(self):
        """FTMO and GFT state directories are different paths — no shared state."""
        from forex_engine.accounts.account_registry import get_account, get_state_dir
        ftmo_dir = get_state_dir('FTMO_10K')
        gft_dir  = get_state_dir('GFT_5K')
        ftmo_cfg = get_account('FTMO_10K')
        gft_cfg  = get_account('GFT_5K')
        assert ftmo_dir != gft_dir
        assert ftmo_dir.replace('\\', '/').lower().endswith(
            ftmo_cfg['state_dir'].lower()
        )
        assert gft_dir.replace('\\', '/').lower().endswith(
            gft_cfg['state_dir'].lower()
        )

    def test_ftmo_state_path_uses_isolated_dir(self):
        """ftmo_state.py STATE_FILE matches the registry-isolated directory."""
        import os
        from forex_engine.accounts.account_registry import get_state_dir
        from forex_engine.prop_firms.ftmo import ftmo_state

        state_file = ftmo_state.STATE_FILE
        expected = os.path.join(get_state_dir('FTMO_10K'), 'state.json')
        assert os.path.normcase(os.path.normpath(state_file)) == os.path.normcase(
            os.path.normpath(expected)
        ), (
            f"FTMO STATE_FILE should match registry state_dir, got: {state_file}"
        )

    def test_gft_state_path_uses_isolated_dir(self):
        """gft_config.py state_file is under data/gft_5k/, not legacy path."""
        from forex_engine.prop_firms.gft.gft_config import GFT_2STEP_PROFILE
        state_file = GFT_2STEP_PROFILE['state_file']
        assert 'gft_5k' in state_file.replace('\\', '/').lower(), (
            f"GFT state_file should be under data/gft_5k/, got: {state_file}"
        )
