"""
tests/test_ml_capital_allocator.py

Fail-safety tests for utils/ml_capital_allocator.py

Run with:  pytest tests/test_ml_capital_allocator.py -v
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Stub out emergency_stop so tests don't need it
import unittest.mock as mock

from utils.ml_capital_allocator import (
    calculate_ml_capital_allocation,
    safe_calculate_alloc,
    MAX_RISK_PER_TRADE,
    MAX_CAPITAL_PCT,
    BOT_CAPITAL,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

def _good_signal(entry=24000, sl=23900, score=13, win_prob=0.60, r_hat=1.5):
    """Minimal valid signal that passes all gates."""
    return {
        "entry_signal": {
            "entry"     : entry,
            "stop_loss" : sl,
            "target1"   : entry + (entry - sl) * 2,
            "target2"   : entry + (entry - sl) * 3,
        },
        "confluence"   : score,
        "in_fvg"       : True,
        "mss_type"     : "CHOCH",
        "direction"    : "BULLISH",
        "symbol"       : "NSE:NIFTY26JUNFUT",
        "ml_prediction": {
            "win_prob"   : win_prob,
            "confidence" : "HIGH",
            "r_hat"      : r_hat,
        },
    }


def _account(capital=20_000, available=20_000, daily_pnl=0, halted=False):
    return {
        "capital"          : capital,
        "available_capital": available,
        "daily_pnl"        : daily_pnl,
        "daily_halted"     : halted,
        "paused"           : False,
        "closed_trades"    : [],
    }


# ── 1. Allocator exception: live mode → fail-closed ─────────────────────────

class TestLiveFailClosed:
    def test_exception_in_live_mode_blocks_trade(self, monkeypatch):
        """
        When calculate_ml_capital_allocation raises, safe_calculate_alloc must
        return blocked=True in live mode (paper_mode=False).
        """
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated internal error")

        monkeypatch.setattr(
            "utils.ml_capital_allocator.calculate_ml_capital_allocation",
            _boom,
        )

        result = safe_calculate_alloc(
            signal        = _good_signal(),
            account_state = _account(),
            paper_mode    = False,
        )

        assert result["blocked"] is True
        assert "ALLOC_EXCEPTION_FAIL_CLOSED" in result["block_reason"]
        assert result["risk_amount"] == 0
        assert result["capital_to_use"] == 0

    def test_exception_in_paper_mode_allows_1lot(self, monkeypatch):
        """
        When calculate_ml_capital_allocation raises, safe_calculate_alloc must
        return blocked=False in paper mode (paper_mode=True), enabling 1-lot test trades.
        """
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated internal error")

        monkeypatch.setattr(
            "utils.ml_capital_allocator.calculate_ml_capital_allocation",
            _boom,
        )

        result = safe_calculate_alloc(
            signal        = _good_signal(),
            account_state = _account(),
            paper_mode    = True,
        )

        assert result["blocked"] is False
        assert "ALLOC_EXCEPTION_PAPER_FALLBACK" in result["reason"]


# ── 2. SL = 0 blocks unconditionally ────────────────────────────────────────

class TestSLZeroBlock:
    def test_sl_missing_blocks(self):
        """Signal with stop_loss=0 must be blocked."""
        sig = _good_signal()
        sig["entry_signal"]["stop_loss"] = 0
        result = calculate_ml_capital_allocation(sig, account_state=_account())
        assert result["blocked"] is True
        assert "SL" in result["block_reason"] or "sl_pts" in result["block_reason"].lower()

    def test_sl_equals_entry_blocks(self):
        """Signal where SL == entry (risk_per_unit = 0) must be blocked."""
        sig = _good_signal(entry=24000, sl=24000)
        result = calculate_ml_capital_allocation(sig, account_state=_account())
        assert result["blocked"] is True

    def test_safe_wrapper_sl_zero_live_blocks(self):
        """safe_calculate_alloc: SL=0 in live mode → blocked (not fallback lots)."""
        sig = _good_signal()
        sig["entry_signal"]["stop_loss"] = 0
        result = safe_calculate_alloc(sig, account_state=_account(), paper_mode=False)
        assert result["blocked"] is True

    def test_safe_wrapper_sl_zero_paper_blocks(self):
        """safe_calculate_alloc: SL=0 in paper mode → still blocked (not a test-pass scenario)."""
        sig = _good_signal()
        sig["entry_signal"]["stop_loss"] = 0
        result = safe_calculate_alloc(sig, account_state=_account(), paper_mode=True)
        # SL=0 is a hard block even in paper mode — the exception fallback only applies
        # to an allocator *crash*, not to the allocator correctly returning blocked.
        assert result["blocked"] is True


# ── 3. risk_amount hard ceiling ──────────────────────────────────────────────

class TestRiskClamp:
    def test_risk_never_exceeds_500(self):
        """
        With a very tight SL (1 pt) and high capital, the allocator must still
        cap risk_amount at Rs500.
        """
        # 1pt SL, large capital → unclamped risk would be huge
        sig = _good_signal(entry=24000, sl=24000 - 1, score=15, win_prob=0.90)
        acc = _account(capital=500_000, available=500_000)
        result = calculate_ml_capital_allocation(sig, account_state=acc)
        if not result["blocked"]:
            assert result["risk_amount"] <= MAX_RISK_PER_TRADE, (
                f"risk_amount={result['risk_amount']} exceeds MAX_RISK_PER_TRADE={MAX_RISK_PER_TRADE}"
            )

    def test_safe_wrapper_re_enforces_risk_clamp(self, monkeypatch):
        """
        Even if calculate_ml_capital_allocation returns risk_amount > 500 due to
        a logic bug, safe_calculate_alloc must re-clamp it.
        """
        def _buggy(*args, **kwargs):
            return {
                "blocked"          : False,
                "block_reason"     : "",
                "allocation_pct"   : 30.0,
                "capital_to_use"   : 5000.0,
                "risk_amount"      : 9999.0,   # bug: too high
                "available_capital": 20000.0,
                "reason"           : "bug",
                "confidence"       : 0.7,
            }

        monkeypatch.setattr(
            "utils.ml_capital_allocator.calculate_ml_capital_allocation",
            _buggy,
        )

        result = safe_calculate_alloc(
            signal        = _good_signal(),
            account_state = _account(),
            paper_mode    = False,
        )

        assert not result["blocked"]
        assert result["risk_amount"] <= MAX_RISK_PER_TRADE, (
            f"safe_calculate_alloc did not re-clamp risk: {result['risk_amount']}"
        )

    def test_risk_clamp_with_multiple_lots(self):
        """
        A signal that would naturally request many lots should be clamped so
        actual_risk = lots * lot_size * sl_pts <= Rs500.

        We verify the ALLOCATOR side: risk_amount <= 500.
        The MAIN.PY side additionally enforces: lots * lot_size * sl_pts <= 500.
        """
        # 5pt SL on NIFTY (lot=65) → unclamped risk per lot = 5*65 = Rs325
        # High score, high capital → allocator would want 2+ lots
        sig = _good_signal(entry=24000, sl=24000 - 5, score=15, win_prob=0.90)
        acc = _account(capital=200_000, available=200_000)
        result = calculate_ml_capital_allocation(sig, account_state=acc)
        if not result["blocked"]:
            assert result["risk_amount"] <= MAX_RISK_PER_TRADE


# ── 4. allocation_pct hard ceiling ───────────────────────────────────────────

class TestAllocationClamp:
    def test_allocation_never_exceeds_50pct(self):
        """allocation_pct must never exceed MAX_CAPITAL_PCT (50%) regardless of score/modifiers."""
        sig = _good_signal(score=20, win_prob=0.99, r_hat=5.0)
        sig["option_volume"] = 1_000_000
        acc = _account(capital=20_000, available=20_000)
        result = calculate_ml_capital_allocation(sig, account_state=acc)
        if not result["blocked"]:
            assert result["allocation_pct"] <= MAX_CAPITAL_PCT * 100, (
                f"allocation_pct={result['allocation_pct']}% exceeds 50%"
            )

    def test_safe_wrapper_re_enforces_alloc_clamp(self, monkeypatch):
        """safe_calculate_alloc must re-clamp allocation_pct even if inner function is buggy."""
        def _buggy(*args, **kwargs):
            return {
                "blocked"          : False,
                "block_reason"     : "",
                "allocation_pct"   : 99.0,   # bug: above 50%
                "capital_to_use"   : 19800.0,
                "risk_amount"      : 100.0,
                "available_capital": 20000.0,
                "reason"           : "bug",
                "confidence"       : 0.9,
            }

        monkeypatch.setattr(
            "utils.ml_capital_allocator.calculate_ml_capital_allocation",
            _buggy,
        )

        result = safe_calculate_alloc(
            signal        = _good_signal(),
            account_state = _account(),
            paper_mode    = False,
        )

        assert not result["blocked"]
        assert result["allocation_pct"] <= MAX_CAPITAL_PCT * 100, (
            f"safe_calculate_alloc did not re-clamp allocation_pct: {result['allocation_pct']}"
        )

    def test_alloc_clamp_applies_to_all_bands(self):
        """Every band variant must respect the 50% ceiling."""
        for score in [8, 10, 13, 15, 20]:
            sig = _good_signal(score=score, win_prob=0.80)
            result = calculate_ml_capital_allocation(sig, account_state=_account())
            if not result["blocked"]:
                assert result["allocation_pct"] <= MAX_CAPITAL_PCT * 100, (
                    f"score={score}: allocation_pct={result['allocation_pct']}%"
                )


# ── 5. Margin feasibility gate ───────────────────────────────────────────────

class TestMarginFeasibility:
    def test_blocks_when_capital_below_min_margin(self):
        """If available_capital < min_margin_per_lot, the allocator must block."""
        sig = _good_signal()
        acc = _account(available=5_000)
        result = calculate_ml_capital_allocation(
            sig,
            account_state      = acc,
            min_margin_per_lot = 95_000,   # NIFTY futures margin
        )
        assert result["blocked"] is True
        assert "Insufficient capital" in result["block_reason"]

    def test_passes_when_capital_meets_margin(self):
        """If available_capital >= min_margin_per_lot, margin gate should not block."""
        sig = _good_signal()
        acc = _account(available=100_000)
        result = calculate_ml_capital_allocation(
            sig,
            account_state      = acc,
            min_margin_per_lot = 95_000,
        )
        # Should pass (entry_signal check may still block, but not margin)
        assert "Insufficient capital" not in result.get("block_reason", "")


# ── 6. Daily halt and emergency stop pass-through ────────────────────────────

class TestHardBlocks:
    def test_daily_halt_blocks(self):
        result = calculate_ml_capital_allocation(
            _good_signal(),
            account_state = _account(halted=True),
        )
        assert result["blocked"] is True
        assert "halt" in result["block_reason"].lower()

    def test_daily_loss_cap_blocks(self):
        result = calculate_ml_capital_allocation(
            _good_signal(),
            account_state = _account(daily_pnl=-1001),
        )
        assert result["blocked"] is True
        assert "Daily loss cap" in result["block_reason"]

    def test_ml_avoid_confidence_blocks(self):
        sig = _good_signal()
        sig["ml_prediction"]["confidence"] = "AVOID"
        result = calculate_ml_capital_allocation(sig, account_state=_account())
        assert result["blocked"] is True

    def test_low_win_prob_blocks(self):
        sig = _good_signal(win_prob=0.30)
        result = calculate_ml_capital_allocation(sig, account_state=_account())
        assert result["blocked"] is True


# ── 7. Valid signal passes and respects structure ────────────────────────────

class TestValidSignalPass:
    def test_good_signal_not_blocked(self):
        result = safe_calculate_alloc(
            signal        = _good_signal(),
            account_state = _account(),
            paper_mode    = False,
        )
        assert not result["blocked"]
        # risk_amount may be 0 when entry_px is the index level (24000) because
        # qty_estimate = int(capital / 24000) = 0. main.py defaults to 1 lot in
        # that case. capital_to_use must always be positive on a pass result.
        assert result["capital_to_use"] > 0
        assert result["confidence"] > 0

    def test_result_keys_always_present(self):
        result = safe_calculate_alloc(_good_signal(), account_state=_account())
        required = {
            "blocked", "block_reason", "allocation_pct",
            "capital_to_use", "risk_amount", "available_capital",
            "reason", "confidence",
        }
        assert required.issubset(set(result.keys())), (
            f"Missing keys: {required - set(result.keys())}"
        )

    def test_blocked_result_has_zero_risk(self):
        """A blocked result must never carry non-zero risk/capital."""
        sig = _good_signal()
        sig["ml_prediction"]["confidence"] = "AVOID"
        result = calculate_ml_capital_allocation(sig, account_state=_account())
        assert result["blocked"] is True
        assert result["risk_amount"] == 0
        assert result["capital_to_use"] == 0
