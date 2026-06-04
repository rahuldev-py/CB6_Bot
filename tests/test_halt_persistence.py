"""
tests/test_halt_persistence.py
================================
Daily halt + emergency stop recovery tests — added 2026-06-02.

Verifies:
  1. can_take_trade blocks when daily_halted is set
  2. reset_daily_counters clears halt flags on new day
  3. _is_halted_today reads safely without paper_trader import
  4. _mark_daily_halt survives paper_trader import failures
  5. Halt persists across restart on the same day
  6. Halt is cleared on the next trading day
  7. Close order failure (IP whitelist) is detected as failure, not success
  8. Zero successful closes logs at CRITICAL and sends Telegram
  9. Live and paper halt state paths are isolated

Run:
    python -m pytest tests/test_halt_persistence.py -v
"""

import json
import os
import sys
import threading
import time
import copy
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ============================================================================
# helpers
# ============================================================================

def _halted_state(capital: float = 200_000, date: str = None) -> dict:
    return {
        'capital'          : capital,
        'available_capital': capital,
        'open_trades'      : [],
        'closed_trades'    : [],
        'daily_trades'     : 0,
        'daily_losses'     : 0,
        'daily_option_strikes': {},
        'paused'           : False,
        'date'             : date or datetime.now().strftime('%Y-%m-%d'),
        'daily_halted'     : True,
        'daily_halt_reason': 'Daily loss cap Rs 1000 hit',
        'daily_halt_time'  : datetime.now().isoformat(),
    }


def _clean_state(capital: float = 200_000, date: str = None) -> dict:
    return {
        'capital'          : capital,
        'available_capital': capital,
        'open_trades'      : [],
        'closed_trades'    : [],
        'daily_trades'     : 0,
        'daily_losses'     : 0,
        'daily_option_strikes': {},
        'paused'           : False,
        'date'             : date or datetime.now().strftime('%Y-%m-%d'),
    }


# ============================================================================
# 1. can_take_trade blocks on daily_halted
# ============================================================================

class TestCanTakeTradeDailyHalt:

    def test_halted_state_blocks_trade(self):
        """daily_halted=True must block can_take_trade regardless of PnL."""
        from trader.paper_trader import can_take_trade
        state = _halted_state()
        ok, reason = can_take_trade(state)
        assert not ok
        assert 'halt' in reason.lower() or 'halted' in reason.lower()

    def test_halted_blocks_before_other_checks(self):
        """daily_halted takes priority — checked before paused, trades, capital."""
        from trader.paper_trader import can_take_trade
        state = _halted_state()
        state['paused'] = False
        state['daily_trades'] = 0
        state['available_capital'] = 200_000
        ok, reason = can_take_trade(state)
        assert not ok
        assert 'halt' in reason.lower() or 'halted' in reason.lower()

    def test_no_halt_flag_allows_trade(self):
        """Without daily_halted, a clean state passes can_take_trade."""
        from trader.paper_trader import can_take_trade
        state = _clean_state()
        ok, reason = can_take_trade(state)
        assert ok, f"Expected OK, got: {reason}"

    def test_halt_flag_false_allows_trade(self):
        """daily_halted=False is the same as absent — does not block."""
        from trader.paper_trader import can_take_trade
        state = _clean_state()
        state['daily_halted'] = False
        ok, reason = can_take_trade(state)
        assert ok, f"Expected OK, got: {reason}"


# ============================================================================
# 2. reset_daily_counters clears halt flags on new day
# ============================================================================

class TestResetDailyCountersClearsHalt:

    def test_halt_cleared_on_new_day(self):
        """daily_halted and related fields are removed when date changes."""
        from trader.paper_trader import reset_daily_counters
        yesterday = '2026-06-01'
        state = _halted_state(date=yesterday)
        assert state.get('daily_halted') is True

        result = reset_daily_counters(state)

        assert result.get('daily_halted') is None or result.get('daily_halted') is False, \
            "daily_halted must be cleared when the trading day rolls over"
        assert 'daily_halt_reason' not in result or not result.get('daily_halt_reason')
        assert result['date'] == datetime.now().strftime('%Y-%m-%d')

    def test_halt_preserved_same_day(self):
        """If date has NOT changed, daily_halted must remain set."""
        from trader.paper_trader import reset_daily_counters
        today = datetime.now().strftime('%Y-%m-%d')
        state = _halted_state(date=today)

        result = reset_daily_counters(state)

        assert result.get('daily_halted') is True, \
            "daily_halted must persist when still the same trading day"

    def test_counters_also_reset_on_new_day(self):
        """Verify normal counter reset still works alongside halt clear."""
        from trader.paper_trader import reset_daily_counters
        yesterday = '2026-06-01'
        state = _halted_state(date=yesterday)
        state['daily_trades']  = 5
        state['daily_losses']  = 3

        result = reset_daily_counters(state)

        assert result['daily_trades'] == 0
        assert result['daily_losses'] == 0
        assert result.get('daily_halted') is None or not result.get('daily_halted')


# ============================================================================
# 3. _is_halted_today reads safely without paper_trader
# ============================================================================

class TestIsHaltedToday:

    def _write_state(self, tmp_path, data: dict) -> str:
        """Write state to tmp_path/data/paper_state.json (mirrors production path)."""
        d = tmp_path / 'data'
        d.mkdir(parents=True, exist_ok=True)
        p = d / 'paper_state.json'
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        return str(p)

    def test_returns_true_when_flag_set(self, tmp_path):
        """_is_halted_today returns True when paper_state has daily_halted=True."""
        from core.daily_loss_monitor import _is_halted_today
        self._write_state(tmp_path, _halted_state())
        with patch('core.daily_loss_monitor._ROOT', str(tmp_path)):
            result = _is_halted_today()
        assert result is True

    def test_returns_false_when_flag_absent(self, tmp_path):
        """_is_halted_today returns False when paper_state has no halt flag."""
        from core.daily_loss_monitor import _is_halted_today
        self._write_state(tmp_path, _clean_state())
        with patch('core.daily_loss_monitor._ROOT', str(tmp_path)):
            result = _is_halted_today()
        assert result is False

    def test_returns_false_when_file_absent(self, tmp_path):
        """_is_halted_today returns False (safe default) if state file is missing."""
        from core.daily_loss_monitor import _is_halted_today
        with patch('core.daily_loss_monitor._ROOT', str(tmp_path)):
            result = _is_halted_today()
        assert result is False

    def test_returns_false_on_corrupt_file(self, tmp_path):
        """_is_halted_today returns False safely when state file is corrupt."""
        from core.daily_loss_monitor import _is_halted_today
        d = tmp_path / 'data'; d.mkdir(parents=True, exist_ok=True)
        with open(d / 'paper_state.json', 'w') as f:
            f.write('{CORRUPT_JSON}}}')
        with patch('core.daily_loss_monitor._ROOT', str(tmp_path)):
            result = _is_halted_today()  # must not raise
        assert result is False


# ============================================================================
# 4. _mark_daily_halt survives paper_trader import failure
# ============================================================================

class TestMarkDailyHalt:

    def _setup_state(self, tmp_path, data: dict):
        d = tmp_path / 'data'; d.mkdir(parents=True, exist_ok=True)
        p = d / 'paper_state.json'
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        return p

    def test_writes_halt_flag_directly(self, tmp_path):
        """_mark_daily_halt writes daily_halted to paper_state using state_io."""
        from core.daily_loss_monitor import _mark_daily_halt
        state_file = self._setup_state(tmp_path, _clean_state())

        with patch('core.daily_loss_monitor._ROOT', str(tmp_path)):
            _mark_daily_halt()

        with open(state_file, encoding='utf-8') as f:
            saved = json.load(f)
        assert saved.get('daily_halted') is True
        assert 'daily_halt_reason' in saved
        assert 'daily_halt_time' in saved

    def test_survives_paper_trader_import_failure(self, tmp_path):
        """_mark_daily_halt must write state even if paper_trader can't be imported."""
        from core.daily_loss_monitor import _mark_daily_halt
        state_file = self._setup_state(tmp_path, _clean_state())

        with patch.dict('sys.modules', {'trader.paper_trader': None}):
            with patch('core.daily_loss_monitor._ROOT', str(tmp_path)):
                _mark_daily_halt()  # must not raise, must write state

        with open(state_file, encoding='utf-8') as f:
            saved = json.load(f)
        assert saved.get('daily_halted') is True


# ============================================================================
# 5 & 6. Halt persists on restart (same day), clears on new day
# ============================================================================

class TestHaltPersistence:

    def test_same_day_restart_still_blocked(self):
        """Simulates restart: state loaded fresh — halt flag is still there."""
        from trader.paper_trader import can_take_trade
        # This is what happens when main.py restarts and paper_trader.load_state()
        # reads the halted state from disk.
        today = datetime.now().strftime('%Y-%m-%d')
        persisted_state = _halted_state(date=today)

        ok, reason = can_take_trade(persisted_state)
        assert not ok
        assert 'halt' in reason.lower() or 'halted' in reason.lower()

    def test_next_day_restart_allows_trading(self):
        """Simulates next-day restart: reset_daily_counters clears halt."""
        from trader.paper_trader import can_take_trade, reset_daily_counters
        yesterday    = '2026-06-01'
        halted_state = _halted_state(date=yesterday)

        fresh_state = reset_daily_counters(halted_state)

        ok, reason = can_take_trade(fresh_state)
        assert ok, f"Expected trading allowed next day, got: {reason}"

    def test_monitor_loop_skips_on_same_day_halt(self, tmp_path):
        """_is_halted_today blocks the monitor loop from re-triggering on restart."""
        from core.daily_loss_monitor import _is_halted_today
        today = datetime.now().strftime('%Y-%m-%d')
        d = tmp_path / 'data'; d.mkdir(parents=True, exist_ok=True)
        with open(d / 'paper_state.json', 'w', encoding='utf-8') as f:
            json.dump(_halted_state(date=today), f)

        with patch('core.daily_loss_monitor._ROOT', str(tmp_path)):
            assert _is_halted_today() is True, \
                "Monitor must see halted state on same-day restart"


# ============================================================================
# 7 & 8. Fyers close order failure handling
# ============================================================================

class TestCloseOrderFailure:

    def _make_fyers(self, position_code=200, order_result=None):
        fyers = MagicMock()
        fyers.positions.return_value = {
            'code'        : position_code,
            'netPositions': [
                {'symbol': 'NSE:ONGC26JUN270CE', 'netQty': -2250},
            ],
        }
        fyers.place_order.return_value = order_result or {'code': 200, 's': 'ok'}
        fyers.orderbook.return_value   = {'code': 200, 'orderBook': []}
        return fyers

    def test_successful_close_counted(self):
        """Fyers code=200 → position counted as successfully closed."""
        from core.daily_loss_monitor import _close_all_positions
        fyers = self._make_fyers(order_result={'code': 200, 's': 'ok', 'id': 'ORD001'})
        attempted, succeeded = _close_all_positions(fyers)
        assert attempted == 1
        assert succeeded == 1

    def test_ip_whitelist_rejection_counted_as_failure(self):
        """Fyers code=-50 (IP whitelist) → counted as failure, not success."""
        from core.daily_loss_monitor import _close_all_positions
        ip_reject = {
            'code'   : -50,
            'message': 'Orders only from whitelisted IP addresses.',
            's'      : 'error',
        }
        fyers = self._make_fyers(order_result=ip_reject)
        attempted, succeeded = _close_all_positions(fyers)
        assert attempted == 1
        assert succeeded == 0, "IP whitelist rejection must not be counted as success"

    def test_failed_close_triggers_critical_log(self, caplog):
        """When any close fails, a CRITICAL log entry is produced."""
        import logging
        from core.daily_loss_monitor import _close_all_positions
        ip_reject = {'code': -50, 'message': 'IP rejected', 's': 'error'}
        fyers = self._make_fyers(order_result=ip_reject)

        with caplog.at_level(logging.CRITICAL):
            _close_all_positions(fyers)

        assert any('CRITICAL' in r.levelname for r in caplog.records), \
            "A CRITICAL log must be emitted when close orders fail"

    def test_failed_close_sends_telegram(self):
        """Failed close triggers a Telegram alert."""
        from core.daily_loss_monitor import _close_all_positions
        ip_reject = {'code': -50, 'message': 'IP rejected', 's': 'error'}
        fyers = self._make_fyers(order_result=ip_reject)

        with patch('utils.telegram_alerts.send_message') as mock_tg:
            _close_all_positions(fyers)

        mock_tg.assert_called_once()
        call_args = mock_tg.call_args[0][0]
        assert 'FAILED' in call_args.upper() or 'fail' in call_args.lower()

    def test_halt_fires_even_when_close_fails(self, tmp_path):
        """The system stays halted even if Fyers position close fails."""
        from core.daily_loss_monitor import _execute_halt
        d = tmp_path / 'data'; d.mkdir(parents=True, exist_ok=True)
        state_file = d / 'paper_state.json'
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(_clean_state(), f)

        ip_reject = {'code': -50, 'message': 'IP rejected', 's': 'error'}
        fyers = self._make_fyers(order_result=ip_reject)

        with patch('core.daily_loss_monitor._fyers_ref', fyers), \
             patch('core.daily_loss_monitor._ROOT', str(tmp_path)), \
             patch('core.daily_loss_monitor._set_nse_stop', lambda *a: None), \
             patch('core.daily_loss_monitor._send_halt_alert', lambda *a: None), \
             patch('utils.telegram_alerts.send_message', lambda *a, **kw: None):
            _execute_halt(1200.0)

        with open(state_file, encoding='utf-8') as f:
            saved = json.load(f)
        assert saved.get('daily_halted') is True, \
            "daily_halted must be True even when position close failed"


# ============================================================================
# 9. Live and paper halt state isolation
# ============================================================================

class TestHaltStateIsolation:

    def test_nse_halt_uses_paper_state_not_forex_state(self, tmp_path):
        """_mark_daily_halt writes to data/paper_state.json, not forex state files."""
        from core.daily_loss_monitor import _mark_daily_halt

        d = tmp_path / 'data'; d.mkdir(parents=True, exist_ok=True)
        paper_state_file = d / 'paper_state.json'
        with open(paper_state_file, 'w', encoding='utf-8') as f:
            json.dump(_clean_state(), f)

        with patch('core.daily_loss_monitor._ROOT', str(tmp_path)):
            _mark_daily_halt()

        with open(paper_state_file, encoding='utf-8') as f:
            paper = json.load(f)
        assert paper.get('daily_halted') is True

        ftmo_file = tmp_path / 'data' / 'ftmo_10k' / 'state.json'
        assert not ftmo_file.exists(), \
            "_mark_daily_halt must not touch FTMO state file"

    def test_nse_flag_file_is_separate_from_forex_flag(self, tmp_path, monkeypatch):
        """NSE_EMERGENCY_STOP.flag is separate from the shared EMERGENCY_STOP.flag."""
        import core.daily_loss_monitor as dlm
        import utils.emergency_stop as es

        nse_flag    = str(tmp_path / 'NSE_EMERGENCY_STOP.flag')
        shared_flag = str(tmp_path / 'EMERGENCY_STOP.flag')
        monkeypatch.setattr(dlm, '_NSE_STOP_FLAG', nse_flag)
        monkeypatch.setattr(es,  '_EMERGENCY_STOP_FLAG', shared_flag)

        # Write NSE-specific flag
        dlm._set_nse_stop("test halt")

        assert os.path.exists(nse_flag), "NSE stop flag must be written"
        assert not os.path.exists(shared_flag), \
            "NSE halt must NOT touch the shared EMERGENCY_STOP.flag (forex engines)"
