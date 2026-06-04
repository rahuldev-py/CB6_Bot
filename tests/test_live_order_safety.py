import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from trader.order_manager import MAX_LIVE_RISK_INR, _cap_lots


def test_cap_lots_blocks_when_one_lot_exceeds_live_risk_cap():
    lots, reason = _cap_lots(lots=1, lot_size=65, risk_per_unit=10.0)

    assert lots == 0
    assert "exceeds" in reason


def test_cap_lots_reduces_multi_lot_order_to_risk_cap():
    lots, reason = _cap_lots(lots=5, lot_size=65, risk_per_unit=2.0)

    assert lots == int(MAX_LIVE_RISK_INR / (65 * 2.0))
    assert lots < 5
    assert "clamped" in reason


def test_cap_lots_blocks_when_ml_capital_cannot_buy_one_option_lot():
    lots, reason = _cap_lots(
        lots=1,
        lot_size=65,
        risk_per_unit=2.0,
        capital_per_lot=2_000.0,
        capital_budget=1_500.0,
    )

    assert lots == 0
    assert "capital budget" in reason


def test_cap_lots_allows_valid_single_lot():
    lots, reason = _cap_lots(lots=1, lot_size=65, risk_per_unit=2.0)

    assert lots == 1
    assert reason == "OK"
