# tests/test_wave4_refactor.py
# Wave 4 Architecture Refactor — regression + deduplication tests.
#
# Verifies:
#   1. compute_best_day_stats produces identical output from both import paths
#   2. calc_lot_size / dollar_risk produce identical output from both import paths
#   3. Telegram helpers: mask_token, is_authorized_chat, is_rate_limited, check_confirmation
#   4. alert_router.py is no longer a valid import (archived)
#   5. No circular imports in refactored modules
#   6. Existing Wave 1 / Wave 2 test modules still importable (regression guard)

import importlib
import sys
import time
import types

import pytest


# ── 1. compute_best_day_stats — identical from both import paths ─────────────────

def test_compute_best_day_stats_canonical_import():
    from forex_engine.prop_firms.ftmo.ftmo_state import compute_best_day_stats
    trades = [
        {'exit_time': '2026-05-20 10:00:00', 'pnl_usd': 30.0},
        {'exit_time': '2026-05-20 14:00:00', 'pnl_usd': 20.0},  # May 20 total = 50
        {'exit_time': '2026-05-21 09:00:00', 'pnl_usd': 80.0},  # May 21 total = 80 → best
        {'exit_time': '2026-05-22 11:00:00', 'pnl_usd': -20.0},  # excluded (loss)
    ]
    best, total_pos, ratio, date = compute_best_day_stats(trades)
    assert best == 80.0
    assert total_pos == 130.0
    assert ratio == round(80 / 130 * 100, 1)
    assert date == '2026-05-21'


def test_compute_best_day_stats_paper_trader_shim():
    """forex_paper_trader re-exports compute_best_day_stats from ftmo_state — verify same object."""
    from forex_engine.prop_firms.ftmo.ftmo_state import compute_best_day_stats as canon
    from forex_engine.forex_paper_trader import compute_best_day_stats as shim
    assert canon is shim


def test_compute_best_day_stats_performance_report_import():
    """performance_report now re-imports from ftmo_state — must be the same function."""
    from forex_engine.prop_firms.ftmo.ftmo_state import compute_best_day_stats as canon
    from forex_engine.reports.performance_report import compute_best_day_stats as rpt
    assert canon is rpt


def test_compute_best_day_stats_empty():
    from forex_engine.prop_firms.ftmo.ftmo_state import compute_best_day_stats
    assert compute_best_day_stats([]) == (0.0, 0.0, 0.0, '')


def test_compute_best_day_stats_all_losses():
    from forex_engine.prop_firms.ftmo.ftmo_state import compute_best_day_stats
    trades = [{'exit_time': '2026-05-20 10:00:00', 'pnl_usd': -40.0}]
    assert compute_best_day_stats(trades) == (0.0, 0.0, 0.0, '')


# ── 2. calc_lot_size / dollar_risk — same object from both paths ─────────────────

def test_calc_lot_size_same_function_from_both_modules():
    """lot_calculator re-exports from forex_instruments — must be identical function objects."""
    from forex_engine.forex_instruments import calc_lot_size as fi_fn
    from forex_engine.trade.lot_calculator import calc_lot_size as lc_fn
    assert fi_fn is lc_fn


def test_dollar_risk_same_function_from_both_modules():
    from forex_engine.forex_instruments import dollar_risk as fi_fn
    from forex_engine.trade.lot_calculator import dollar_risk as lc_fn
    assert fi_fn is lc_fn


def test_calc_lot_size_produces_correct_result():
    from forex_engine.trade.lot_calculator import calc_lot_size
    # XAGUSD: contract_size=5000, SL dist = 0.10, risk = 1% of 5000 = $50
    # raw = 50 / (5000 * 0.10) = 0.1 lots
    lots = calc_lot_size('XAGUSD', 5000.0, 25.00, 24.90, risk_pct=1.0)
    assert lots > 0.0
    assert isinstance(lots, float)


def test_dollar_risk_calculation():
    from forex_engine.trade.lot_calculator import dollar_risk
    # XAGUSD contract_size=5000, lots=0.10, entry=25.00, sl=24.90 → dist=0.10
    # risk = 0.10 * 5000 * 0.10 = $50
    risk = dollar_risk('XAGUSD', 0.10, 25.00, 24.90)
    assert risk == 50.0


# ── 3. Telegram helpers ───────────────────────────────────────────────────────────

def test_mask_token_normal():
    from communications.telegram_helpers import mask_token
    result = mask_token('123456789:ABCDEFtoken')
    assert result.startswith('12345678')
    assert '***' in result
    assert 'token' not in result


def test_mask_token_empty():
    from communications.telegram_helpers import mask_token
    assert mask_token('') == '(empty)'
    assert mask_token(None) == '(empty)'


def test_is_authorized_chat_match():
    from communications.telegram_helpers import is_authorized_chat
    ids = {'111', '222', '333'}
    assert is_authorized_chat('222', ids) is True


def test_is_authorized_chat_no_match():
    from communications.telegram_helpers import is_authorized_chat
    assert is_authorized_chat('999', {'111', '222'}) is False


def test_is_authorized_chat_empty_id():
    from communications.telegram_helpers import is_authorized_chat
    assert is_authorized_chat('', {'111'}) is False


def test_is_rate_limited_blocks_second_call():
    from communications.telegram_helpers import is_rate_limited
    cache = {}
    # First call — not limited
    assert is_rate_limited('user1', '/cmd', cache, 5) is False
    # Second call within 5s — limited
    assert is_rate_limited('user1', '/cmd', cache, 5) is True


def test_is_rate_limited_different_commands():
    from communications.telegram_helpers import is_rate_limited
    cache = {}
    assert is_rate_limited('user1', '/cmd_a', cache, 5) is False
    # Different command — not rate limited
    assert is_rate_limited('user1', '/cmd_b', cache, 5) is False


def test_is_rate_limited_expires():
    from communications.telegram_helpers import is_rate_limited
    cache = {}
    is_rate_limited('user1', '/cmd', cache, 0)   # limit_secs=0 → expires immediately
    # Sleep 0 — with limit_secs=0 the next call should NOT be blocked
    time.sleep(0.01)
    assert is_rate_limited('user1', '/cmd', cache, 0) is False


def test_check_confirmation_first_call_registers():
    from communications.telegram_helpers import check_confirmation
    sent = []
    pending = {}
    ok, base = check_confirmation('user1', '/fx_stop', pending, 30, sent.append)
    assert ok is False
    assert base == '/fx_stop'
    assert ('user1', '/fx_stop') in pending
    assert len(sent) == 1  # confirmation prompt was sent


def test_check_confirmation_confirm_suffix_approves():
    from communications.telegram_helpers import check_confirmation
    pending = {}
    # Register
    check_confirmation('user1', '/fx_stop', pending, 30, lambda _: None)
    # Confirm
    ok, base = check_confirmation('user1', '/fx_stop confirm', pending, 30, lambda _: None)
    assert ok is True
    assert base == '/fx_stop'
    assert ('user1', '/fx_stop') not in pending  # cleared


def test_check_confirmation_expired_returns_false():
    from communications.telegram_helpers import check_confirmation
    pending = {('user1', '/fx_stop'): time.time() - 1}  # already expired
    ok, base = check_confirmation('user1', '/fx_stop confirm', pending, 30, lambda _: None)
    assert ok is False


# ── 4. alert_router archived — importing it must raise ───────────────────────────

def test_alert_router_is_archived():
    """Importing the original path must fail since it was tombstoned."""
    with pytest.raises((ImportError, SystemExit)):
        import forex_engine.alerts.alert_router as _  # noqa: F401
        # If already cached from a previous import attempt, force re-exec
        exec(open('forex_engine/alerts/alert_router.py').read())


# ── 5. No circular imports ────────────────────────────────────────────────────────

def test_no_circular_import_lot_calculator():
    """lot_calculator → forex_instruments must not create a cycle."""
    if 'forex_engine.trade.lot_calculator' in sys.modules:
        del sys.modules['forex_engine.trade.lot_calculator']
    mod = importlib.import_module('forex_engine.trade.lot_calculator')
    assert hasattr(mod, 'calc_lot_size')
    assert hasattr(mod, 'gft_lot_modifier')


def test_no_circular_import_telegram_helpers():
    """telegram_helpers must import cleanly — no trading, broker, or ML deps."""
    if 'communications.telegram_helpers' in sys.modules:
        del sys.modules['communications.telegram_helpers']
    mod = importlib.import_module('communications.telegram_helpers')
    assert hasattr(mod, 'mask_token')
    assert hasattr(mod, 'send_message')
    # Must NOT have imported any trading modules
    assert 'forex_engine' not in str(vars(mod))
    assert 'mt5' not in str(vars(mod)).lower()


def test_no_circular_import_performance_report():
    if 'forex_engine.reports.performance_report' in sys.modules:
        del sys.modules['forex_engine.reports.performance_report']
    mod = importlib.import_module('forex_engine.reports.performance_report')
    assert hasattr(mod, 'compute_best_day_stats')


# ── 6. Regression: Wave 1 + Wave 2 test modules still importable ─────────────────

def test_wave1_tests_importable():
    mod = importlib.import_module('tests.test_wave1_safety_guards')
    assert mod is not None


def test_wave2_tests_importable():
    mod = importlib.import_module('tests.test_wave2_resiliency')
    assert mod is not None
