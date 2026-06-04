#!/usr/bin/env python3
"""
scripts/nse_daily_report.py — NSE Signal-to-Fill Daily Verification Report

Reads data/audit/trade_audit_YYYYMMDD.jsonl, runs all verification checks,
and prints (and optionally Telegrams) a structured PASS/FAIL summary.

Usage:
    python scripts/nse_daily_report.py               # today
    python scripts/nse_daily_report.py 2026-06-02    # specific date
    python scripts/nse_daily_report.py --telegram    # also send to Telegram

Output sections:
  1. Executive verdict (PASS / PASS_WITH_WARNINGS / FAIL)
  2. Trade count + open/closed breakdown
  3. Per-check summary table
  4. Per-trade detail (only on FAIL or --verbose)
  5. PnL integrity (gross − brokerage = net for each trade)
  6. Fill slippage summary
  7. Missing logs
"""

from __future__ import annotations

import os
import sys
from datetime import date
from typing import List

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from utils.trade_verifier import (
    TradeVerifier, VFlag, get_verifier,
    MAX_FILL_DRIFT_PCT, MAX_RISK_PCT,
)


# ── Formatting helpers ─────────────────────────────────────────────────────────

_SEP = "─" * 65


def _pct(n, total):
    return f"{n}/{total} ({n*100//max(total,1)}%)"


def _rs(v):
    try:
        return f"Rs {float(v):+,.0f}"
    except Exception:
        return str(v)


# ── Report builder ─────────────────────────────────────────────────────────────

class DailyReport:

    CRITICAL_FLAGS = {
        VFlag.NO_SL,
        VFlag.RISK_EXCEEDED,
        VFlag.NOT_CLOSED_AT_EOD,
        VFlag.STALE_DATA,
        VFlag.ML_ALLOC_FAIL_CLOSED,
    }

    WARNING_FLAGS = {
        VFlag.LOT_SIZE_FALLBACK,
        VFlag.ML_MISSING,
        VFlag.EXCEL_MISSING,
        VFlag.TELEGRAM_MISSING,
        VFlag.JOURNAL_MISSING,
        VFlag.ML_ALLOC_BLOCKED,
        VFlag.ML_ALLOC_RISK_CLAMPED,
    }

    def __init__(self, records: List[dict], report_date: date):
        self.records     = records
        self.report_date = report_date

    # ── Checks ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _has_critical(flags: List[str]) -> bool:
        return any(
            any(f.startswith(cf) for cf in DailyReport.CRITICAL_FLAGS)
            for f in flags
        )

    @staticmethod
    def _has_warning(flags: List[str]) -> bool:
        return any(
            any(f.startswith(wf) for wf in DailyReport.WARNING_FLAGS)
            for f in flags
        )

    # ── Aggregate stats ────────────────────────────────────────────────────────

    def _aggregate(self):
        total     = len(self.records)
        closed    = [r for r in self.records if r.get("status") == "CLOSED"]
        open_recs = [r for r in self.records if r.get("status") != "CLOSED"]

        wins   = [r for r in closed if (r.get("net_pnl") or 0) > 0]
        losses = [r for r in closed if (r.get("net_pnl") or 0) < 0]

        total_gross = sum(r.get("gross_pnl") or 0 for r in closed)
        total_brok  = sum(r.get("brokerage") or 0 for r in closed)
        total_net   = sum(r.get("net_pnl") or 0 for r in closed)

        # PnL integrity: gross - brokerage should equal net
        pnl_discrepancies = []
        for r in closed:
            g = r.get("gross_pnl")
            b = r.get("brokerage")
            n = r.get("net_pnl")
            if g is not None and b is not None and n is not None:
                expected = round(g - b, 2)
                actual   = round(n, 2)
                if abs(expected - actual) > 1.0:   # Rs 1 tolerance for rounding
                    pnl_discrepancies.append({
                        "id"      : r.get("trade_id", "?"),
                        "symbol"  : r.get("option_symbol", "?"),
                        "expected": expected,
                        "actual"  : actual,
                        "diff"    : round(actual - expected, 2),
                    })

        # Fill slippage
        slippage = []
        for r in self.records:
            p = r.get("planned_entry")
            f = r.get("fill_price")
            if p and f and float(p) > 0:
                drift = round((float(f) - float(p)) / float(p) * 100, 3)
                slippage.append({
                    "id"      : r.get("trade_id", "?"),
                    "symbol"  : r.get("option_symbol", "?"),
                    "planned" : p,
                    "fill"    : f,
                    "drift"   : drift,
                    "exceeded": abs(drift) > MAX_FILL_DRIFT_PCT,
                })

        # Per-flag counts
        flag_counts: dict = {}
        for r in self.records:
            for fl in (r.get("verification_flags") or []):
                key = fl.split("(")[0]
                flag_counts[key] = flag_counts.get(key, 0) + 1

        # Verdict
        has_critical = any(self._has_critical(r.get("verification_flags") or []) for r in self.records)
        has_warning  = any(self._has_warning(r.get("verification_flags") or []) for r in self.records)
        if has_critical:
            verdict = "❌ FAIL"
        elif has_warning or pnl_discrepancies:
            verdict = "⚠️  PASS_WITH_WARNINGS"
        else:
            verdict = "✅ PASS"

        return dict(
            total=total, closed=len(closed), open=len(open_recs),
            wins=len(wins), losses=len(losses),
            total_gross=total_gross, total_brok=total_brok, total_net=total_net,
            pnl_discrepancies=pnl_discrepancies,
            slippage=slippage,
            flag_counts=flag_counts,
            verdict=verdict,
        )

    # ── Render ─────────────────────────────────────────────────────────────────

    def render(self, verbose: bool = False) -> str:
        ag  = self._aggregate()
        ver = ag["verdict"]
        lines = [
            "",
            _SEP,
            f"  CB6 QUANTUM — NSE DAILY VERIFICATION REPORT",
            f"  Date  : {self.report_date}",
            f"  Trades: {ag['total']} total  ({ag['closed']} closed, {ag['open']} open)",
            _SEP,
            "",
            f"  VERDICT: {ver}",
            "",
        ]

        # ── 1. Trade count ──────────────────────────────────────────────────
        if ag["closed"] > 0:
            wr = ag["wins"] * 100 // max(ag["wins"] + ag["losses"], 1)
            lines += [
                "  TRADE SUMMARY",
                f"    Wins        : {ag['wins']}",
                f"    Losses      : {ag['losses']}",
                f"    Win rate    : {wr}%",
                f"    Gross PnL   : {_rs(ag['total_gross'])}",
                f"    Brokerage   : Rs {ag['total_brok']:,.0f}",
                f"    Net PnL     : {_rs(ag['total_net'])}",
                "",
            ]

        # ── 2. Per-check summary ────────────────────────────────────────────
        lines.append("  VERIFICATION CHECKS")
        check_rows = [
            ("Fill drift ≤ 2%",    VFlag.FILL_DRIFT_EXCEEDED),
            ("SL present",         VFlag.NO_SL),
            ("TP present",         VFlag.NO_TP),
            ("Qty lot-aligned",    VFlag.QTY_NOT_LOT_ALIGNED),
            ("Risk ≤ 1.5%",        VFlag.RISK_EXCEEDED),
            ("Lot from master",    VFlag.LOT_SIZE_FALLBACK),
            ("Data fresh",         VFlag.STALE_DATA),
            ("Closed at EOD",      VFlag.NOT_CLOSED_AT_EOD),
            ("Journal written",    VFlag.JOURNAL_MISSING),
            ("Excel written",      VFlag.EXCEL_MISSING),
            ("ML updated",         VFlag.ML_MISSING),
            ("Telegram sent",      VFlag.TELEGRAM_MISSING),
        ]
        total = max(ag["total"], 1)
        for label, flag in check_rows:
            fail_count = ag["flag_counts"].get(flag, 0)
            pass_count = total - fail_count
            status = "✅" if fail_count == 0 else ("❌" if flag in self.CRITICAL_FLAGS else "⚠️ ")
            lines.append(f"    {status} {label:<24} {_pct(pass_count, total)} pass")

        lines.append("")

        # ── 3. Fill slippage ────────────────────────────────────────────────
        if ag["slippage"]:
            lines.append("  FILL SLIPPAGE")
            exceeded = [s for s in ag["slippage"] if s["exceeded"]]
            ok       = [s for s in ag["slippage"] if not s["exceeded"]]
            lines.append(f"    Within threshold : {len(ok)}/{len(ag['slippage'])}")
            if exceeded:
                lines.append(f"    EXCEEDED (>{MAX_FILL_DRIFT_PCT}%):")
                for s in exceeded:
                    lines.append(
                        f"      {s['symbol'][:25]:<26} planned={s['planned']} "
                        f"fill={s['fill']} drift={s['drift']:+.2f}%"
                    )
            lines.append("")

        # ── 4. PnL integrity ────────────────────────────────────────────────
        if ag["pnl_discrepancies"]:
            lines.append("  PNL INTEGRITY DISCREPANCIES (gross − brok ≠ net)")
            for d in ag["pnl_discrepancies"]:
                lines.append(
                    f"    {d['symbol'][:25]:<26} expected={_rs(d['expected'])} "
                    f"actual={_rs(d['actual'])} diff={_rs(d['diff'])}"
                )
            lines.append("")
        else:
            lines.append("  PNL INTEGRITY: ✅ gross − brok = net for all trades")
            lines.append("")

        # ── 5. Per-trade detail (on FAIL or verbose) ────────────────────────
        if verbose or "FAIL" in ver:
            lines.append("  PER-TRADE DETAIL")
            for r in self.records:
                flags = r.get("verification_flags") or []
                symbol = r.get("option_symbol") or r.get("futures_symbol") or "?"
                status = r.get("status", "?")
                net    = r.get("net_pnl")
                net_s  = f"{_rs(net)}" if net is not None else "open"
                flag_s = ", ".join(flags) if flags else "OK"
                crit   = "❌" if self._has_critical(flags) else ("⚠️ " if flags else "✅")
                lines.append(
                    f"    {crit} {symbol[:30]:<31} {status:<6} net={net_s:<12} flags={flag_s}"
                )
            lines.append("")

        lines.append(_SEP)
        return "\n".join(lines)

    def render_telegram(self) -> str:
        """Condensed HTML-formatted version for Telegram."""
        ag  = self._aggregate()
        ver = ag["verdict"]
        has_crit = any(
            self._has_critical(r.get("verification_flags") or [])
            for r in self.records
        )
        flag_summary = "  ".join(
            f"{k}: {v}" for k, v in sorted(ag["flag_counts"].items())[:6]
        )
        net = _rs(ag["total_net"])
        wr  = (ag["wins"] * 100 // max(ag["wins"] + ag["losses"], 1)) if ag["closed"] else 0
        return (
            f"<b>CB6 NSE Daily Report — {self.report_date}</b>\n\n"
            f"Verdict: <b>{ver}</b>\n"
            f"Trades : {ag['total']} ({ag['wins']}W/{ag['losses']}L, WR {wr}%)\n"
            f"Net PnL: <b>{net}</b>\n"
            + (f"Flags  : {flag_summary}\n" if flag_summary else "Flags  : None\n")
            + ("\n<b>⚠️  Review required — see full report</b>" if has_crit else "")
        )


# ── Entry point ────────────────────────────────────────────────────────────────

def run_report(
    report_date: date = None,
    verbose: bool     = False,
    send_telegram: bool = False,
) -> str:
    report_date = report_date or date.today()

    verifier = get_verifier()

    # Run verification on all records for the day
    records = verifier.get_records_for_date(report_date)
    if not records:
        msg = (
            f"\n{_SEP}\n"
            f"  CB6 NSE Daily Report — {report_date}\n"
            f"  No trades recorded for this date.\n"
            f"{_SEP}\n"
        )
        print(msg)
        return msg

    # Re-run verification on every record to ensure flags are fresh
    for rec in records:
        tid = rec.get("trade_id", "")
        if tid:
            verifier._verify_one(tid)

    # Reload after verification writes flags back
    records = verifier.get_records_for_date(report_date)

    report = DailyReport(records, report_date)
    text   = report.render(verbose=verbose)
    print(text)

    if send_telegram:
        try:
            from utils.telegram_alerts import send_message
            send_message(report.render_telegram())
        except Exception as e:
            print(f"Telegram send failed: {e}")

    return text


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CB6 NSE Daily Trade Report")
    parser.add_argument("date", nargs="?", help="YYYY-MM-DD (default: today)")
    parser.add_argument("--telegram", action="store_true", help="Send to Telegram")
    parser.add_argument("--verbose",  action="store_true", help="Show per-trade detail")
    args = parser.parse_args()

    report_date = date.today()
    if args.date:
        try:
            report_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"Invalid date: {args.date!r}  (use YYYY-MM-DD)")
            sys.exit(1)

    run_report(report_date=report_date, verbose=args.verbose, send_telegram=args.telegram)
