"""
utils/trade_verifier.py — NSE Signal-to-Fill Audit Trail

Records every NSE trade as a structured 25-field SignalFillRecord from
the moment a signal fires through to the final exit + log confirmation.

The verifier is the single source of truth for post-session audits:
  - Fill slippage vs planned entry
  - SL/TP integrity
  - Lot-size source (master vs fallback)
  - Risk compliance
  - Data-provider freshness at signal time
  - Log trinity: state + journal + Excel written

Records are stored as JSONL at data/audit/trade_audit_YYYYMMDD.jsonl.
Each record is updated in-place (load → merge → write-back) so partial
records are always recoverable.

Usage (from paper_trader.py, order_manager.py — all wrapped in try/except):

    from utils.trade_verifier import get_verifier
    get_verifier().record_entry(trade, setup)
    get_verifier().record_fill(trade_id, fill_price, order_price, bid, ask)
    get_verifier().record_exit(trade_id, exit_price, reason, gross, brok, net, tg, ml, xl)

Design rules:
  - NEVER raise. Every method is fully exception-safe.
  - NEVER block. All file I/O is minimal.
  - NEVER change strategy or risk logic.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, date
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AUDIT_DIR = os.path.join(_ROOT, "data", "audit")

# ── Verification thresholds ────────────────────────────────────────────────────
MAX_FILL_DRIFT_PCT  : float = 2.0    # fill must be ≤ 2% from planned entry
MAX_SPREAD_PCT      : float = 1.0    # option spread ≤ 1% of bid
MAX_RISK_PCT        : float = 1.5    # risk per trade ≤ 1.5% of capital
BAR_MAX_AGE_MINS    : int   = 15     # data freshness for signal-time bars


# ── Flags ──────────────────────────────────────────────────────────────────────

class VFlag:
    OK                       = "OK"
    FILL_DRIFT_EXCEEDED      = "FILL_DRIFT_EXCEEDED"
    NO_SL                    = "NO_SL"
    NO_TP                    = "NO_TP"
    QTY_NOT_LOT_ALIGNED      = "QTY_NOT_LOT_ALIGNED"
    RISK_EXCEEDED            = "RISK_EXCEEDED"
    LOT_SIZE_FALLBACK        = "LOT_SIZE_FALLBACK"
    STALE_DATA               = "STALE_DATA"
    NOT_CLOSED_AT_EOD        = "NOT_CLOSED_AT_EOD"
    JOURNAL_MISSING          = "JOURNAL_MISSING"
    EXCEL_MISSING            = "EXCEL_MISSING"
    ML_MISSING               = "ML_MISSING"
    TELEGRAM_MISSING         = "TELEGRAM_MISSING"
    ML_ALLOC_FAIL_CLOSED     = "ML_ALLOC_FAIL_CLOSED"    # allocator threw, live mode blocked
    ML_ALLOC_BLOCKED         = "ML_ALLOC_BLOCKED"         # allocator returned blocked=True
    ML_ALLOC_RISK_CLAMPED    = "ML_ALLOC_RISK_CLAMPED"    # risk_amount was hard-clamped to ₹500


# ── Record ──────────────────────────────────────────────────────────────────────

def _blank_record(trade_id: str) -> dict:
    return {
        # identity
        "trade_id"          : trade_id,
        "paper_state_id"    : "",
        "journal_id"        : "",
        "status"            : "OPEN",
        "created_at"        : datetime.now().isoformat(),
        "updated_at"        : datetime.now().isoformat(),

        # 1  signal timestamp
        "signal_ts"         : "",
        # 2  data provider
        "data_provider"     : "",
        # 3  futures symbol
        "futures_symbol"    : "",
        # 4  futures LTP at signal time
        "futures_ltp"       : None,
        # 5  signal type
        "signal_type"       : "",
        # 6  direction
        "direction"         : "",
        # 7  option symbol
        "option_symbol"     : "",
        # 8  option bid / ask / ltp at entry
        "option_bid"        : None,
        "option_ask"        : None,
        "option_ltp"        : None,
        # 9  spread %
        "spread_pct"        : None,
        # 10 planned entry (from signal)
        "planned_entry"     : None,
        # 11 actual order price submitted
        "order_price"       : None,
        # 12 fill price (paper = same as entry; live = broker fill)
        "fill_price"        : None,
        # 13 SL
        "stop_loss"         : None,
        # 14 TP
        "target1"           : None,
        "target2"           : None,
        "target3"           : None,
        # 15 quantity
        "quantity"          : None,
        # 16 lot size
        "lot_size"          : None,
        "lot_size_source"   : "",    # "master" | "fallback"
        # 17 risk
        "risk_amount_inr"   : None,
        "risk_pct_capital"  : None,
        # 18 exit price
        "exit_price"        : None,
        # 19 exit reason
        "exit_reason"       : "",
        # 20 gross PnL (before brokerage)
        "gross_pnl"         : None,
        # 21 brokerage
        "brokerage"         : None,
        # 22 net PnL
        "net_pnl"           : None,
        # 23 Telegram alert sent
        "telegram_sent"     : False,
        # 24 Excel log written
        "excel_written"     : False,
        # 25 ML/memory update written
        "ml_updated"        : False,

        # verification results (populated by verify())
        "verification_flags": [],
        "verified"          : False,
    }


# ── File helpers ───────────────────────────────────────────────────────────────

def _audit_path(day: Optional[date] = None) -> str:
    day = day or date.today()
    os.makedirs(_AUDIT_DIR, exist_ok=True)
    return os.path.join(_AUDIT_DIR, f"trade_audit_{day.isoformat()}.jsonl")


def _load_today_records(path: str) -> List[dict]:
    records = []
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _save_records(path: str, records: List[dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ── TradeVerifier ──────────────────────────────────────────────────────────────

class TradeVerifier:
    """Thread-safe singleton for recording and verifying NSE trade records."""

    _instance: "TradeVerifier | None" = None
    _cls_lock = threading.Lock()

    def __new__(cls) -> "TradeVerifier":
        with cls._cls_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._ready = False
                cls._instance = inst
        return cls._instance

    def __init__(self) -> None:
        if self._ready:
            return
        self._ready = True
        self._lock  = threading.Lock()

    # ── internal helpers ────────────────────────────────────────────────────────

    def _upsert(self, trade_id: str, updates: dict) -> None:
        """Load today's JSONL, update the record for trade_id, write back."""
        path = _audit_path()
        with self._lock:
            records = _load_today_records(path)
            found = False
            for rec in records:
                if rec.get("trade_id") == trade_id:
                    rec.update(updates)
                    rec["updated_at"] = datetime.now().isoformat()
                    found = True
                    break
            if not found:
                rec = _blank_record(trade_id)
                rec.update(updates)
                records.append(rec)
            _save_records(path, records)

    def _get(self, trade_id: str) -> Optional[dict]:
        path = _audit_path()
        with self._lock:
            for rec in _load_today_records(path):
                if rec.get("trade_id") == trade_id:
                    return rec
        return None

    # ── Public recording API ────────────────────────────────────────────────────

    def record_entry(
        self,
        trade: dict,
        setup: dict,
        lot_size_source: str = "fallback",
    ) -> None:
        """
        Call immediately after open_paper_trade writes the trade to state.
        Captures all entry-side fields from the trade and setup dicts.
        """
        try:
            sig = setup.get("entry_signal", {})
            fvg = setup.get("fvg", {})

            # Data provider — read from health monitor
            provider = "unknown"
            try:
                from data.data_health import get_monitor
                provider = "Fyers" if get_monitor().is_fyers_active() else "TrueData"
            except Exception:
                pass

            # Signal type: CHoCH, BOS, or derive from scanner
            sig_type = (
                setup.get("mss_type")
                or setup.get("signal_type")
                or setup.get("pattern")
                or ("FVG" if setup.get("in_fvg") else "Silver Bullet")
            )

            planned = sig.get("entry") or trade.get("entry_price")
            sl      = sig.get("stop_loss") or trade.get("stop_loss") or trade.get("current_sl")

            # Risk
            risk_pts    = abs(float(planned or 0) - float(sl or 0))
            qty         = trade.get("quantity", 0)
            risk_inr    = round(risk_pts * qty, 2)
            capital     = float(os.getenv("CAPITAL", 200_000))
            risk_pct    = round(risk_inr / capital * 100, 3) if capital else 0

            self._upsert(trade.get("id", "?"), {
                "paper_state_id" : trade.get("id", ""),
                "journal_id"     : trade.get("journal_id", ""),
                "signal_ts"      : trade.get("entry_time", datetime.now().isoformat()),
                "data_provider"  : provider,
                "futures_symbol" : setup.get("underlying") or setup.get("symbol", ""),
                "futures_ltp"    : setup.get("futures_ltp") or setup.get("underlying_ltp"),
                "signal_type"    : str(sig_type),
                "direction"      : setup.get("direction", trade.get("direction", "")),
                "option_symbol"  : trade.get("symbol", ""),
                "option_ltp"     : setup.get("option_ltp") or sig.get("entry"),
                "option_bid"     : setup.get("option_bid"),
                "option_ask"     : setup.get("option_ask"),
                "spread_pct"     : setup.get("spread_pct"),
                "planned_entry"  : planned,
                "order_price"    : setup.get("order_price") or planned,
                "fill_price"     : trade.get("entry_price"),   # paper = planned; live overwritten by record_fill
                "stop_loss"      : sl,
                "target1"        : sig.get("target1") or trade.get("target1"),
                "target2"        : sig.get("target2") or trade.get("target2"),
                "target3"        : sig.get("target3") or trade.get("target3"),
                "quantity"       : qty,
                "lot_size"       : setup.get("lot_size") or trade.get("lot_size"),
                "lot_size_source": lot_size_source,
                "risk_amount_inr": risk_inr,
                "risk_pct_capital": risk_pct,
                "status"         : "OPEN",
            })
        except Exception as exc:
            try:
                from utils.logger import logger
                logger.debug(f"TradeVerifier.record_entry error: {exc}")
            except Exception:
                pass

    def record_fill(
        self,
        trade_id: str,
        fill_price: float,
        order_price: Optional[float] = None,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
    ) -> None:
        """
        Call after a live broker fill is confirmed.
        Updates fill_price (which differs from planned_entry in live mode).
        """
        try:
            updates: dict = {"fill_price": fill_price}
            if order_price is not None:
                updates["order_price"] = order_price
            if bid is not None:
                updates["option_bid"] = bid
            if ask is not None:
                updates["option_ask"] = ask
                if bid and bid > 0:
                    updates["spread_pct"] = round(abs(ask - bid) / bid * 100, 3)
            self._upsert(trade_id, updates)
        except Exception as exc:
            try:
                from utils.logger import logger
                logger.debug(f"TradeVerifier.record_fill error: {exc}")
            except Exception:
                pass

    def record_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        gross_pnl: float,
        brokerage: float,
        net_pnl: float,
        telegram_sent: bool   = False,
        ml_updated: bool      = False,
        excel_written: bool   = False,
        journal_updated: bool = False,
    ) -> None:
        """
        Call after close_paper_trade or close_paper_trade_by_id completes.
        Marks the record CLOSED and sets all 'did it happen' flags.
        Runs verification checks automatically after recording the exit.
        """
        try:
            self._upsert(trade_id, {
                "exit_price"    : exit_price,
                "exit_reason"   : exit_reason,
                "gross_pnl"     : round(gross_pnl, 2),
                "brokerage"     : round(brokerage, 2),
                "net_pnl"       : round(net_pnl, 2),
                "telegram_sent" : telegram_sent,
                "ml_updated"    : ml_updated,
                "excel_written" : excel_written,
                "journal_id"    : journal_updated,
                "status"        : "CLOSED",
            })
            # Run verification immediately on close
            self._verify_one(trade_id)
        except Exception as exc:
            try:
                from utils.logger import logger
                logger.debug(f"TradeVerifier.record_exit error: {exc}")
            except Exception:
                pass

    def update_flags(
        self,
        trade_id: str,
        telegram_sent: Optional[bool]   = None,
        excel_written: Optional[bool]   = None,
        ml_updated: Optional[bool]      = None,
        journal_id: Optional[str]       = None,
    ) -> None:
        """Update individual 'did it happen' flags after the fact."""
        try:
            updates = {}
            if telegram_sent is not None:
                updates["telegram_sent"] = telegram_sent
            if excel_written is not None:
                updates["excel_written"] = excel_written
            if ml_updated is not None:
                updates["ml_updated"] = ml_updated
            if journal_id is not None:
                updates["journal_id"] = journal_id
            if updates:
                self._upsert(trade_id, updates)
        except Exception:
            pass

    # ── Verification ───────────────────────────────────────────────────────────

    def _verify_one(self, trade_id: str) -> List[str]:
        """Run all verification checks on one record. Returns list of VFlags."""
        flags: List[str] = []
        try:
            rec = self._get(trade_id)
            if rec is None:
                return [VFlag.JOURNAL_MISSING]

            planned    = rec.get("planned_entry")
            fill       = rec.get("fill_price")
            sl         = rec.get("stop_loss")
            t1         = rec.get("target1")
            t2         = rec.get("target2")
            t3         = rec.get("target3")
            qty        = rec.get("quantity")
            lot_size   = rec.get("lot_size")
            risk_pct   = rec.get("risk_pct_capital")
            lot_src    = rec.get("lot_size_source", "fallback")
            exit_price = rec.get("exit_price")
            status     = rec.get("status", "OPEN")

            # 1. Fill drift
            if planned and fill and planned > 0:
                drift_pct = abs(fill - planned) / planned * 100
                if drift_pct > MAX_FILL_DRIFT_PCT:
                    flags.append(f"{VFlag.FILL_DRIFT_EXCEEDED}({drift_pct:.1f}%)")

            # 2. SL integrity
            if not sl or float(sl or 0) <= 0:
                flags.append(VFlag.NO_SL)

            # 3. TP integrity
            tp_present = any(float(t or 0) > 0 for t in [t1, t2, t3])
            if not tp_present:
                flags.append(VFlag.NO_TP)

            # 4. Quantity lot-aligned
            if qty and lot_size and int(lot_size) > 1:
                if int(qty) % int(lot_size) != 0:
                    flags.append(f"{VFlag.QTY_NOT_LOT_ALIGNED}(qty={qty},lot={lot_size})")

            # 5. Risk check
            if risk_pct and float(risk_pct) > MAX_RISK_PCT:
                flags.append(f"{VFlag.RISK_EXCEEDED}({risk_pct:.2f}%)")

            # 6. Lot size source
            if lot_src == "fallback":
                flags.append(VFlag.LOT_SIZE_FALLBACK)

            # 7. EOD closure check (only applies if status still OPEN at report time)
            if status == "OPEN" and not exit_price:
                flags.append(VFlag.NOT_CLOSED_AT_EOD)

            # 8. Log trinity
            if not rec.get("journal_id"):
                flags.append(VFlag.JOURNAL_MISSING)
            if not rec.get("excel_written"):
                flags.append(VFlag.EXCEL_MISSING)
            if not rec.get("ml_updated"):
                flags.append(VFlag.ML_MISSING)
            if not rec.get("telegram_sent"):
                flags.append(VFlag.TELEGRAM_MISSING)

            # Write flags back
            all_ok = not flags
            self._upsert(trade_id, {
                "verification_flags": flags,
                "verified"          : all_ok,
            })
        except Exception as exc:
            try:
                from utils.logger import logger
                logger.debug(f"TradeVerifier._verify_one error: {exc}")
            except Exception:
                pass
        return flags

    def verify_all_today(self) -> List[dict]:
        """
        Run verification on every record in today's audit file.
        Returns list of records with their verification_flags populated.
        """
        results = []
        try:
            path = _audit_path()
            records = _load_today_records(path)
            for rec in records:
                tid = rec.get("trade_id", "?")
                flags = self._verify_one(tid)
                rec["verification_flags"] = flags
                results.append(rec)
        except Exception:
            pass
        return results

    # ── Report helpers ─────────────────────────────────────────────────────────

    def get_today_records(self) -> List[dict]:
        """Return all audit records for today."""
        try:
            return _load_today_records(_audit_path())
        except Exception:
            return []

    def get_records_for_date(self, day: date) -> List[dict]:
        """Return audit records for a specific date."""
        try:
            return _load_today_records(_audit_path(day))
        except Exception:
            return []

    def record_alloc_decision(
        self,
        alloc_result: dict,
        symbol:       str   = "",
        direction:    str   = "",
        mode:         str   = "",
        signal_score: int   = 0,
        sl_pts:       float = 0.0,
        lots_decided: int   = 0,
        paper_mode:   bool  = False,
    ) -> None:
        """
        Append one allocation decision to data/audit/alloc_decisions_YYYYMMDD.jsonl.

        Called for EVERY allocator outcome — passes and blocks alike — so the
        audit trail captures why every signal was sized or rejected.
        No trade_id required; records are keyed by timestamp.
        """
        try:
            os.makedirs(_AUDIT_DIR, exist_ok=True)
            today_str = date.today().isoformat()
            path = os.path.join(_AUDIT_DIR, f"alloc_decisions_{today_str}.jsonl")
            record = {
                "ts"            : datetime.now().isoformat(),
                "symbol"        : symbol,
                "direction"     : direction,
                "mode"          : mode,
                "paper_mode"    : paper_mode,
                "signal_score"  : signal_score,
                "sl_pts"        : round(sl_pts, 2),
                "lots_decided"  : lots_decided,
                "blocked"       : alloc_result.get("blocked", True),
                "block_reason"  : alloc_result.get("block_reason", ""),
                "allocation_pct": alloc_result.get("allocation_pct", 0),
                "capital_to_use": alloc_result.get("capital_to_use", 0),
                "risk_amount"   : alloc_result.get("risk_amount", 0),
                "confidence"    : alloc_result.get("confidence", 0),
                "reason"        : alloc_result.get("reason", ""),
            }
            with self._lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            try:
                from utils.logger import logger
                logger.debug(f"TradeVerifier.record_alloc_decision error: {exc}")
            except Exception:
                pass


# ── Module-level singleton ──────────────────────────────────────────────────────

_verifier = TradeVerifier()


def get_verifier() -> TradeVerifier:
    return _verifier
