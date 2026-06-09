"""
tests/test_trade_verifier.py
==============================
Trade verifier + daily report tests — added 2026-06-02.

Verifies:
  1. record_entry writes 25-field record to JSONL
  2. record_exit closes the record with all exit fields
  3. Fill drift check passes within threshold, fails beyond
  4. SL presence check
  5. TP presence check
  6. Quantity lot-alignment check
  7. Risk % check
  8. Lot size source flag
  9. PnL integrity: gross − brokerage = net
 10. Log trinity flags (journal / Excel / ML / Telegram)
 11. EOD open-trade flag
 12. Daily report renders with PASS / FAIL verdict
 13. Daily report shows correct trade count and PnL

Run:
    python -m pytest tests/test_trade_verifier.py -v
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_verifier(tmp_path, monkeypatch):
    """Each test gets a fresh TradeVerifier pointing at a temp audit dir."""
    import utils.trade_verifier as tv
    tv.TradeVerifier._instance = None
    monkeypatch.setattr(tv, "_AUDIT_DIR", str(tmp_path))
    # Pin CAPITAL to a large test default so risk% checks are .env-independent
    monkeypatch.setenv("CAPITAL", "200000")
    yield tv.get_verifier()
    tv.TradeVerifier._instance = None


def _make_trade(
    trade_id="T001",
    symbol="NSE:NIFTY26JUN24000CE",
    entry=130.0,
    sl=110.0,
    t1=150.0, t2=170.0, t3=190.0,
    qty=65,
):
    return {
        "id"             : trade_id,
        "symbol"         : symbol,
        "direction"      : "BULLISH",
        "entry_price"    : entry,
        "stop_loss"      : sl,
        "current_sl"     : sl,
        "target1"        : t1,
        "target2"        : t2,
        "target3"        : t3,
        "quantity"       : qty,
        "entry_time"     : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "journal_id"     : "JID001",
        "brokerage_paid" : 0,
        "realized_pnl"   : 0,
        "pnl"            : 0,
    }


def _make_setup(
    underlying="NSE:NIFTY26JUNFUT",
    direction="BULLISH",
    entry=130.0,
    sl=110.0,
    t1=150.0, t2=170.0, t3=190.0,
    lot_size=65,
    lot_size_source="master",
):
    return {
        "symbol"         : underlying,
        "underlying"     : underlying,
        "direction"      : direction,
        "mss_type"       : "CHOCH",
        "lot_size"       : lot_size,
        "_lot_size_source": lot_size_source,
        "in_fvg"         : True,
        "confluence"     : 13,
        "entry_signal"   : {
            "entry"    : entry,
            "stop_loss": sl,
            "target1"  : t1,
            "target2"  : t2,
            "target3"  : t3,
            "risk"     : abs(entry - sl),
            "rr_ratio" : 3.0,
        },
    }


# ============================================================================
# 1 & 2. record_entry / record_exit write correct JSONL
# ============================================================================

class TestRecordEntryExit:

    def test_record_entry_creates_jsonl(self, isolated_verifier, tmp_path):
        trade = _make_trade()
        setup = _make_setup()
        isolated_verifier.record_entry(trade, setup, lot_size_source="master")

        recs = isolated_verifier.get_today_records()
        assert len(recs) == 1
        rec = recs[0]
        assert rec["trade_id"]    == "T001"
        assert rec["status"]      == "OPEN"
        assert rec["signal_type"] == "CHOCH"
        assert rec["direction"]   == "BULLISH"
        assert rec["lot_size_source"] == "master"

    def test_record_exit_closes_record(self, isolated_verifier):
        trade = _make_trade()
        setup = _make_setup()
        isolated_verifier.record_entry(trade, setup)
        isolated_verifier.record_exit(
            trade_id="T001", exit_price=160.0, exit_reason="T2_HIT",
            gross_pnl=1950.0, brokerage=120.0, net_pnl=1830.0,
            telegram_sent=True, ml_updated=True, excel_written=True,
        )

        recs = isolated_verifier.get_today_records()
        assert len(recs) == 1
        rec = recs[0]
        assert rec["status"]       == "CLOSED"
        assert rec["exit_price"]   == 160.0
        assert rec["exit_reason"]  == "T2_HIT"
        assert rec["gross_pnl"]    == 1950.0
        assert rec["brokerage"]    == 120.0
        assert rec["net_pnl"]      == 1830.0
        assert rec["telegram_sent"] is True
        assert rec["ml_updated"]    is True
        assert rec["excel_written"] is True

    def test_all_25_fields_present(self, isolated_verifier):
        trade = _make_trade()
        setup = _make_setup()
        isolated_verifier.record_entry(trade, setup)
        isolated_verifier.record_exit(
            trade_id="T001", exit_price=155.0, exit_reason="SL_HIT",
            gross_pnl=-1300.0, brokerage=100.0, net_pnl=-1400.0,
        )
        rec = isolated_verifier.get_today_records()[0]

        required_fields = [
            "signal_ts", "data_provider", "futures_symbol", "futures_ltp",
            "signal_type", "direction", "option_symbol", "option_bid",
            "option_ask", "option_ltp", "spread_pct", "planned_entry",
            "order_price", "fill_price", "stop_loss", "target1", "target2",
            "target3", "quantity", "lot_size", "lot_size_source",
            "risk_amount_inr", "risk_pct_capital", "exit_price", "exit_reason",
            "gross_pnl", "brokerage", "net_pnl", "telegram_sent",
            "excel_written", "ml_updated",
        ]
        for f in required_fields:
            assert f in rec, f"Field {f!r} missing from audit record"


# ============================================================================
# 3. Fill drift check
# ============================================================================

class TestFillDriftCheck:

    def test_fill_within_threshold_no_flag(self, isolated_verifier):
        """Fill 1% above planned — within 2% threshold."""
        from utils.trade_verifier import VFlag
        trade = _make_trade(entry=100.0)
        setup = _make_setup(entry=100.0)
        isolated_verifier.record_entry(trade, setup)
        isolated_verifier.record_fill("T001", fill_price=101.0)  # +1%
        isolated_verifier.record_exit(
            "T001", 120.0, "T1", 1300.0, 100.0, 1200.0,
            telegram_sent=True, ml_updated=True,
        )
        rec = isolated_verifier.get_today_records()[0]
        assert VFlag.FILL_DRIFT_EXCEEDED not in (rec.get("verification_flags") or [])

    def test_fill_beyond_threshold_flags(self, isolated_verifier):
        """Fill 5% above planned — exceeds 2% threshold."""
        from utils.trade_verifier import VFlag
        trade = _make_trade(entry=100.0)
        setup = _make_setup(entry=100.0)
        isolated_verifier.record_entry(trade, setup)
        isolated_verifier.record_fill("T001", fill_price=105.0)  # +5%
        isolated_verifier.record_exit(
            "T001", 120.0, "T1", 1300.0, 100.0, 1200.0,
            telegram_sent=True, ml_updated=True,
        )
        rec = isolated_verifier.get_today_records()[0]
        flags = rec.get("verification_flags") or []
        assert any("FILL_DRIFT" in f for f in flags), f"Expected fill-drift flag, got: {flags}"


# ============================================================================
# 4. SL presence check
# ============================================================================

class TestSLCheck:

    def test_valid_sl_no_flag(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        trade = _make_trade(sl=110.0)
        setup = _make_setup(sl=110.0)
        isolated_verifier.record_entry(trade, setup)
        isolated_verifier.record_exit(
            "T001", 90.0, "SL_HIT", -1300.0, 100.0, -1400.0,
            telegram_sent=True, ml_updated=True,
        )
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.NO_SL not in flags

    def test_missing_sl_flags(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        trade = _make_trade(sl=0.0)
        setup = _make_setup(sl=0.0)
        isolated_verifier.record_entry(trade, setup)
        isolated_verifier.record_exit(
            "T001", 90.0, "SL_HIT", -1300.0, 100.0, -1400.0,
            telegram_sent=True, ml_updated=True,
        )
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.NO_SL in flags


# ============================================================================
# 5. TP presence check
# ============================================================================

class TestTPCheck:

    def test_with_targets_no_flag(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        isolated_verifier.record_entry(_make_trade(), _make_setup())
        isolated_verifier.record_exit("T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
                                      telegram_sent=True, ml_updated=True)
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.NO_TP not in flags

    def test_zero_targets_flags(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        trade = _make_trade(t1=0, t2=0, t3=0)
        setup = _make_setup(t1=0, t2=0, t3=0)
        isolated_verifier.record_entry(trade, setup)
        isolated_verifier.record_exit("T001", 90.0, "SL_HIT", -1300.0, 100.0, -1400.0,
                                      telegram_sent=True, ml_updated=True)
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.NO_TP in flags


# ============================================================================
# 6. Quantity lot-alignment check
# ============================================================================

class TestQtyLotAlignment:

    def test_aligned_qty_no_flag(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        trade = _make_trade(qty=65)
        setup = _make_setup(lot_size=65)
        isolated_verifier.record_entry(trade, setup)
        isolated_verifier.record_exit("T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
                                      telegram_sent=True, ml_updated=True)
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.QTY_NOT_LOT_ALIGNED not in flags

    def test_misaligned_qty_flags(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        trade = _make_trade(qty=70)   # 70 is not a multiple of 65
        setup = _make_setup(lot_size=65)
        isolated_verifier.record_entry(trade, setup)
        isolated_verifier.record_exit("T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
                                      telegram_sent=True, ml_updated=True)
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert any("QTY_NOT_LOT_ALIGNED" in f for f in flags), f"Expected lot flag, got: {flags}"


# ============================================================================
# 7. Risk % check
# ============================================================================

class TestRiskCheck:

    def test_within_risk_no_flag(self, isolated_verifier, monkeypatch):
        from utils.trade_verifier import VFlag
        monkeypatch.setenv("CAPITAL", "200000")
        trade = _make_trade(entry=130.0, sl=110.0, qty=65)  # risk = 20 × 65 = Rs 1300 = 0.65%
        isolated_verifier.record_entry(trade, _make_setup())
        isolated_verifier.record_exit("T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
                                      telegram_sent=True, ml_updated=True)
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.RISK_EXCEEDED not in flags

    def test_excess_risk_flags(self, isolated_verifier, monkeypatch):
        from utils.trade_verifier import VFlag
        monkeypatch.setenv("CAPITAL", "10000")  # tiny capital → risk % explodes
        trade = _make_trade(entry=130.0, sl=110.0, qty=65)  # Rs 1300 = 13% of Rs 10k
        isolated_verifier.record_entry(trade, _make_setup())
        isolated_verifier.record_exit("T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
                                      telegram_sent=True, ml_updated=True)
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.RISK_EXCEEDED in flags or any("RISK_EXCEEDED" in f for f in flags)


# ============================================================================
# 8. Lot size source flag
# ============================================================================

class TestLotSizeSourceFlag:

    def test_master_source_no_flag(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        isolated_verifier.record_entry(_make_trade(), _make_setup(), lot_size_source="master")
        isolated_verifier.record_exit("T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
                                      telegram_sent=True, ml_updated=True)
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.LOT_SIZE_FALLBACK not in flags

    def test_fallback_source_warns(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        isolated_verifier.record_entry(_make_trade(), _make_setup(), lot_size_source="fallback")
        isolated_verifier.record_exit("T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
                                      telegram_sent=True, ml_updated=True)
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.LOT_SIZE_FALLBACK in flags


# ============================================================================
# 9. PnL integrity
# ============================================================================

class TestPnLIntegrity:

    def test_consistent_pnl(self, isolated_verifier):
        """gross - brokerage == net → no discrepancy."""
        isolated_verifier.record_entry(_make_trade(), _make_setup())
        isolated_verifier.record_exit(
            "T001", 155.0, "T1",
            gross_pnl=1625.0, brokerage=125.0, net_pnl=1500.0,
            telegram_sent=True, ml_updated=True,
        )
        from scripts.nse_daily_report import DailyReport
        recs   = isolated_verifier.get_today_records()
        report = DailyReport(recs, date.today())
        ag     = report._aggregate()
        assert ag["pnl_discrepancies"] == [], \
            f"Unexpected PnL discrepancy: {ag['pnl_discrepancies']}"

    def test_inconsistent_pnl_flagged(self, isolated_verifier):
        """gross - brokerage ≠ net → discrepancy detected."""
        isolated_verifier.record_entry(_make_trade(), _make_setup())
        isolated_verifier.record_exit(
            "T001", 155.0, "T1",
            gross_pnl=1625.0, brokerage=125.0, net_pnl=1600.0,  # net off by Rs 100
            telegram_sent=True, ml_updated=True,
        )
        from scripts.nse_daily_report import DailyReport
        recs   = isolated_verifier.get_today_records()
        report = DailyReport(recs, date.today())
        ag     = report._aggregate()
        assert len(ag["pnl_discrepancies"]) == 1


# ============================================================================
# 10. Log trinity flags
# ============================================================================

class TestLogTrinityFlags:

    def test_all_flags_set_no_missing(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        isolated_verifier.record_entry(_make_trade(), _make_setup())
        isolated_verifier.record_exit(
            "T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
            telegram_sent=True, ml_updated=True, excel_written=True,
            journal_updated=True,
        )
        rec   = isolated_verifier.get_today_records()[0]
        flags = rec.get("verification_flags") or []
        for bad in [VFlag.TELEGRAM_MISSING, VFlag.ML_MISSING, VFlag.EXCEL_MISSING]:
            assert bad not in flags, f"Unexpected missing flag: {bad}"

    def test_missing_telegram_flagged(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        isolated_verifier.record_entry(_make_trade(), _make_setup())
        isolated_verifier.record_exit(
            "T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
            telegram_sent=False, ml_updated=True, excel_written=True,
        )
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.TELEGRAM_MISSING in flags

    def test_missing_ml_flagged(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        isolated_verifier.record_entry(_make_trade(), _make_setup())
        isolated_verifier.record_exit(
            "T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
            telegram_sent=True, ml_updated=False, excel_written=True,
        )
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.ML_MISSING in flags


# ============================================================================
# 11. EOD open-trade flag
# ============================================================================

class TestEODOpenFlag:

    def test_closed_trade_no_eod_flag(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        isolated_verifier.record_entry(_make_trade(), _make_setup())
        isolated_verifier.record_exit(
            "T001", 150.0, "T1", 1300.0, 100.0, 1200.0,
            telegram_sent=True, ml_updated=True,
        )
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.NOT_CLOSED_AT_EOD not in flags

    def test_open_trade_at_eod_flagged(self, isolated_verifier):
        from utils.trade_verifier import VFlag
        isolated_verifier.record_entry(_make_trade(), _make_setup())
        # Manually force verification without closing
        isolated_verifier._verify_one("T001")
        flags = isolated_verifier.get_today_records()[0].get("verification_flags") or []
        assert VFlag.NOT_CLOSED_AT_EOD in flags


# ============================================================================
# 12 & 13. Daily report renders correctly
# ============================================================================

class TestDailyReport:

    def _add_clean_trade(self, verifier, trade_id="T001"):
        trade = _make_trade(trade_id=trade_id)
        setup = _make_setup()
        verifier.record_entry(trade, setup, lot_size_source="master")
        verifier.record_exit(
            trade_id, 155.0, "T1",
            gross_pnl=1625.0, brokerage=125.0, net_pnl=1500.0,
            telegram_sent=True, ml_updated=True, excel_written=True,
        )

    def test_pass_verdict_on_clean_trades(self, isolated_verifier):
        from scripts.nse_daily_report import run_report
        self._add_clean_trade(isolated_verifier, "T001")
        self._add_clean_trade(isolated_verifier, "T002")
        text = run_report(report_date=date.today(), verbose=False)
        assert "PASS" in text

    def test_fail_verdict_on_missing_sl(self, isolated_verifier):
        from scripts.nse_daily_report import run_report
        trade = _make_trade(sl=0.0)
        setup = _make_setup(sl=0.0)
        isolated_verifier.record_entry(trade, setup, lot_size_source="master")
        isolated_verifier.record_exit(
            "T001", 90.0, "SL_HIT", -1300.0, 100.0, -1400.0,
            telegram_sent=True, ml_updated=True,
        )
        text = run_report(report_date=date.today())
        assert "FAIL" in text

    def test_report_shows_trade_count(self, isolated_verifier):
        from scripts.nse_daily_report import run_report
        self._add_clean_trade(isolated_verifier, "T001")
        self._add_clean_trade(isolated_verifier, "T002")
        self._add_clean_trade(isolated_verifier, "T003")
        text = run_report(report_date=date.today())
        assert "3" in text

    def test_no_trades_report(self, isolated_verifier):
        from scripts.nse_daily_report import run_report
        text = run_report(report_date=date.today())
        assert "No trades" in text

    def test_pnl_totals_in_report(self, isolated_verifier):
        from scripts.nse_daily_report import run_report
        self._add_clean_trade(isolated_verifier, "T001")
        text = run_report(report_date=date.today())
        # Net PnL Rs 1500 should appear somewhere in the report
        assert "1,500" in text or "1500" in text
