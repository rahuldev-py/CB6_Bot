import unittest
from unittest.mock import patch

from trader import paper_trader


class TestPaperTradeGates(unittest.TestCase):
    @patch("trader.paper_trader.load_state")
    @patch("trader.paper_trader.save_state")
    @patch("trader.paper_trader.send_message")
    def test_timeframe_gate_blocks_non_60min(self, _send_message, _save_state, _load_state):
        _load_state.return_value = {
            "capital": 200000.0,
            "available_capital": 200000.0,
            "open_trades": [],
            "closed_trades": [],
            "daily_losses": 0,
            "daily_trades": 0,
            "total_pnl": 0.0,
            "date": "2026-05-06",
        }

        setup = {
            "symbol": "NSE:RELIANCE-EQ",
            "direction": "BUY",
            "instrument_type": "EQUITY",
            "timeframe": "15min",  # should be blocked
            "confluence": 9.0,
            "entry_signal": {
                "entry": 100.0,
                "stop_loss": 98.0,
                "target1": 106.0,
                "target2": 109.0,
                "target3": 112.0,
                "risk": 2.0,
                "rr_ratio": 3.0,
            },
        }

        trade = paper_trader.open_paper_trade(setup)
        self.assertIsNone(trade)

    @patch("trader.paper_trader.load_state")
    @patch("trader.paper_trader.save_state")
    @patch("trader.paper_trader.send_message")
    def test_rr_gate_blocks_rr_below_min(self, _send_message, _save_state, _load_state):
        _load_state.return_value = {
            "capital": 200000.0,
            "available_capital": 200000.0,
            "open_trades": [],
            "closed_trades": [],
            "daily_losses": 0,
            "daily_trades": 0,
            "total_pnl": 0.0,
            "date": "2026-05-06",
        }

        setup = {
            "symbol": "NSE:RELIANCE-EQ",
            "direction": "BUY",
            "instrument_type": "EQUITY",
            "timeframe": "60min",  # allowed
            "confluence": 9.0,
            "entry_signal": {
                "entry": 100.0,
                "stop_loss": 98.0,
                "target1": 104.0,  # with risk=2, rr=2 (below 3)
                "target2": 107.0,
                "target3": 110.0,
                "risk": 2.0,
                "rr_ratio": 2.0,
            },
        }

        trade = paper_trader.open_paper_trade(setup)
        self.assertIsNone(trade)


if __name__ == "__main__":
    unittest.main()
