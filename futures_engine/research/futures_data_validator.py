"""
CB6 Futures Core — 1m CSV Data Quality Validator
Validates imported CSV files before they are used in backtests.
Checks: timestamps, duplicates, gaps, timezone, OHLC sanity, bar count.

Usage:
    python -m futures_engine.research.futures_data_validator --symbol MES --timeframe 1m
    python -m futures_engine.research.futures_data_validator --file data/futures/historical/MES_1m.csv
    python -m futures_engine.research.futures_data_validator --all

Produces: reports/futures/data_quality_report.md
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

logger = logging.getLogger("cb6.futures.research.validator")

DATA_DIR   = "data/futures/historical"
REPORT_DIR = "reports/futures"

# Expected bar intervals per timeframe (in minutes)
EXPECTED_INTERVAL: dict[str, int] = {
    "1m":  1,
    "5m":  5,
    "15m": 15,
    "30m": 30,
    "1h":  60,
    "4h":  240,
    "1d":  1440,
}

# Gap thresholds are timeframe-specific (in absolute minutes, not multipliers).
# CME futures have a daily 60-min maintenance break (16:00-17:00 CT = 21:00-22:00 UTC).
# For 1m data this produces a 60-minute gap every trading day — that's EXPECTED.
# Weekend gaps (~49 hours) are also expected. These should not fire as errors.
GAP_THRESHOLDS: dict[str, tuple[int, int]] = {
    # timeframe: (warn_minutes, error_minutes)
    "1m":  (90,   1500),   # warn >90 min, error >25 h (weekend ~49h still warns not errors)
    "5m":  (120,  1500),
    "15m": (240,  1500),
    "30m": (480,  1500),
    "1h":  (300,  1500),
    "4h":  (600,  7200),
    "1d":  (4320, 21600),  # 3 days warn, 15 days error
}
# Fallback multipliers for unknown timeframes
GAP_WARN_MULT  = 90
GAP_ERROR_MULT = 1500


@dataclass
class BarRow:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    line_num: int


@dataclass
class ValidationIssue:
    severity: str        # "ERROR" | "WARNING" | "INFO"
    category: str        # "TIMESTAMP" | "DUPLICATE" | "GAP" | "OHLC" | "TIMEZONE" | "COUNT"
    description: str
    line_num: int = 0
    value: str = ""


@dataclass
class ValidationResult:
    symbol: str
    timeframe: str
    filepath: str
    total_bars: int
    date_range_start: Optional[str]
    date_range_end: Optional[str]
    duplicate_count: int
    gap_count_warn: int
    gap_count_error: int
    ohlc_violation_count: int
    timezone_issues: int
    issues: List[ValidationIssue] = field(default_factory=list)
    passed: bool = True

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "WARNING")


class FuturesDataValidator:

    def __init__(self, data_dir: str = DATA_DIR):
        self._dir = data_dir

    def validate_file(
        self,
        filepath: str,
        symbol: str,
        timeframe: str,
    ) -> ValidationResult:
        issues: List[ValidationIssue] = []
        bars: List[BarRow] = []

        if not os.path.exists(filepath):
            return ValidationResult(
                symbol=symbol, timeframe=timeframe, filepath=filepath,
                total_bars=0, date_range_start=None, date_range_end=None,
                duplicate_count=0, gap_count_warn=0, gap_count_error=0,
                ohlc_violation_count=0, timezone_issues=0,
                issues=[ValidationIssue("ERROR", "COUNT", f"File not found: {filepath}")],
                passed=False,
            )

        # ── Detect delimiter (comma vs semicolon) ──────────────────────────
        with open(filepath, encoding="utf-8") as f:
            sample = f.read(4096)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","

        # Record the column names found in the header for the audit
        header_columns: list = []

        # ── Parse rows ─────────────────────────────────────────────────────
        parse_errors = 0
        with open(filepath, encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            if reader.fieldnames:
                header_columns = [k.lower().strip().lstrip("﻿") for k in reader.fieldnames if k]
            for line_num, row in enumerate(reader, start=2):
                try:
                    # Normalize all keys to lowercase, strip BOM
                    r = {k.lower().strip().lstrip("﻿"): v.strip() for k, v in row.items() if k}

                    # Timestamp — accepts: timestamp, time (TradingView), date (NinjaTrader)
                    ts_raw = (r.get("timestamp") or r.get("time") or r.get("date") or "")

                    # NinjaTrader: separate Date (YYYYMMDD) + Time (HHMMSS) columns
                    if not ts_raw and "date" in r:
                        ts_raw = r["date"]
                        if "time" in r and r["time"]:
                            ts_raw = ts_raw + " " + r["time"]

                    ts_raw = ts_raw.strip()
                    if not ts_raw:
                        issues.append(ValidationIssue("ERROR", "TIMESTAMP",
                            "Empty timestamp", line_num))
                        parse_errors += 1
                        continue

                    ts = self._parse_ts(ts_raw)

                    o = float(r.get("open", 0))
                    h = float(r.get("high", 0))
                    l = float(r.get("low",  0))
                    c = float(r.get("close", 0))
                    v = int(float(r.get("volume", r.get("totalvolume", 0))))

                    bars.append(BarRow(ts, o, h, l, c, v, line_num))
                except (ValueError, TypeError) as e:
                    issues.append(ValidationIssue("ERROR", "TIMESTAMP",
                        f"Parse error: {e}", line_num, str(dict(list(row.items())[:4]))))
                    parse_errors += 1

        # Report detected column format
        if header_columns:
            ts_col = next((c for c in header_columns if c in ("timestamp","time","date")), None)
            if ts_col:
                issues.append(ValidationIssue("INFO", "TIMESTAMP",
                    f"Detected timestamp column: '{ts_col}' | delimiter: '{delimiter}' | "
                    f"all columns: {header_columns[:8]}"))
            else:
                issues.append(ValidationIssue("WARNING", "TIMESTAMP",
                    f"Could not identify timestamp column in: {header_columns[:8]}. "
                    "Expected: 'timestamp', 'time', or 'date'"))

        if parse_errors > 10:
            issues.insert(0, ValidationIssue("ERROR", "COUNT",
                f"{parse_errors} rows failed to parse — check CSV format"))

        if not bars:
            return ValidationResult(
                symbol=symbol, timeframe=timeframe, filepath=filepath,
                total_bars=0, date_range_start=None, date_range_end=None,
                duplicate_count=0, gap_count_warn=0, gap_count_error=0,
                ohlc_violation_count=0, timezone_issues=0, issues=issues, passed=False,
            )

        # ── Sort by timestamp ───────────────────────────────────────────────
        bars.sort(key=lambda b: b.timestamp)

        # ── Timezone consistency ────────────────────────────────────────────
        tz_issues = 0
        for bar in bars[:50]:  # sample first 50
            if bar.timestamp.tzinfo is None:
                tz_issues += 1
        if tz_issues > 0:
            issues.append(ValidationIssue("WARNING", "TIMEZONE",
                f"{tz_issues}/50 sampled bars have no timezone info (assuming UTC)"))

        # ── Duplicate timestamps ────────────────────────────────────────────
        seen_ts: set = set()
        dup_count = 0
        for bar in bars:
            key = bar.timestamp.isoformat()
            if key in seen_ts:
                dup_count += 1
                if dup_count <= 5:
                    issues.append(ValidationIssue("ERROR", "DUPLICATE",
                        f"Duplicate timestamp: {key}", bar.line_num))
            seen_ts.add(key)

        if dup_count > 5:
            issues.append(ValidationIssue("ERROR", "DUPLICATE",
                f"Total duplicates: {dup_count} (showing first 5)"))

        # ── Gap detection ───────────────────────────────────────────────────
        tf_thresholds = GAP_THRESHOLDS.get(timeframe)
        warn_min  = tf_thresholds[0] if tf_thresholds else EXPECTED_INTERVAL.get(timeframe, 60) * GAP_WARN_MULT
        error_min = tf_thresholds[1] if tf_thresholds else EXPECTED_INTERVAL.get(timeframe, 60) * GAP_ERROR_MULT

        gap_warn  = 0
        gap_error = 0
        weekend_gaps = 0

        for i in range(1, len(bars)):
            prev_ts = bars[i-1].timestamp
            cur_ts  = bars[i].timestamp
            delta = cur_ts - prev_ts
            delta_min = delta.total_seconds() / 60

            if delta_min < 0:
                issues.append(ValidationIssue("ERROR", "TIMESTAMP",
                    f"Bars out of order: {prev_ts} → {cur_ts}",
                    bars[i].line_num))
                continue

            # Classify gap
            is_weekend     = self._is_weekend_gap(prev_ts, cur_ts)
            is_daily_break = self._is_daily_maintenance(prev_ts, cur_ts, timeframe)
            is_holiday     = self._is_holiday_gap(prev_ts, cur_ts)

            if is_weekend or is_daily_break or is_holiday:
                weekend_gaps += 1
                continue   # expected CME market closure — not an error or warning

            if delta_min > error_min:
                gap_error += 1
                if gap_error <= 10:
                    issues.append(ValidationIssue("ERROR", "GAP",
                        f"Unexpected large gap: {prev_ts.date()} {prev_ts.strftime('%H:%M')} → "
                        f"{cur_ts.date()} {cur_ts.strftime('%H:%M')} "
                        f"({delta_min:.0f} min — possible missing data)",
                        bars[i].line_num))
            elif delta_min > warn_min:
                gap_warn += 1
                if gap_warn <= 10:
                    issues.append(ValidationIssue("WARNING", "GAP",
                        f"Intra-session gap: {prev_ts} → {cur_ts} "
                        f"({delta_min:.0f} min — possible missing bars)",
                        bars[i].line_num))

        if gap_error > 10:
            issues.append(ValidationIssue("ERROR", "GAP",
                f"Total unexpected large gaps: {gap_error} (showing first 10)"))
        if gap_warn > 10:
            issues.append(ValidationIssue("WARNING", "GAP",
                f"Total intra-session gaps: {gap_warn} (showing first 10)"))
        if weekend_gaps > 0:
            issues.append(ValidationIssue("INFO", "GAP",
                f"Expected market closures (weekends/maintenance): {weekend_gaps} — normal"))

        # ── OHLC sanity ─────────────────────────────────────────────────────
        ohlc_violations = 0
        for bar in bars:
            if bar.high < bar.low:
                ohlc_violations += 1
                if ohlc_violations <= 5:
                    issues.append(ValidationIssue("ERROR", "OHLC",
                        f"High < Low at {bar.timestamp}: H={bar.high} L={bar.low}",
                        bar.line_num))
            if bar.open > bar.high or bar.open < bar.low:
                ohlc_violations += 1
                if ohlc_violations <= 5:
                    issues.append(ValidationIssue("ERROR", "OHLC",
                        f"Open outside High/Low at {bar.timestamp}: O={bar.open} H={bar.high} L={bar.low}",
                        bar.line_num))
            if bar.close > bar.high or bar.close < bar.low:
                ohlc_violations += 1
                if ohlc_violations <= 5:
                    issues.append(ValidationIssue("ERROR", "OHLC",
                        f"Close outside High/Low at {bar.timestamp}: C={bar.close} H={bar.high} L={bar.low}",
                        bar.line_num))
            if bar.open <= 0 or bar.high <= 0:
                ohlc_violations += 1

        if ohlc_violations > 5:
            issues.append(ValidationIssue("ERROR", "OHLC",
                f"Total OHLC violations: {ohlc_violations} (showing first 5)"))

        # ── Bar count sanity ────────────────────────────────────────────────
        total_bars = len(bars)
        min_required = {"1m": 5000, "5m": 1000, "1h": 200, "4h": 50, "1d": 50}
        min_req = min_required.get(timeframe, 100)
        if total_bars < min_req:
            issues.append(ValidationIssue("WARNING", "COUNT",
                f"Only {total_bars} bars — minimum recommended is {min_req} for {timeframe} backtest"))

        # ── Compile result ──────────────────────────────────────────────────
        passed = not any(i.severity == "ERROR" for i in issues)
        return ValidationResult(
            symbol=symbol,
            timeframe=timeframe,
            filepath=filepath,
            total_bars=total_bars,
            date_range_start=bars[0].timestamp.isoformat() if bars else None,
            date_range_end=bars[-1].timestamp.isoformat() if bars else None,
            duplicate_count=dup_count,
            gap_count_warn=gap_warn,
            gap_count_error=gap_error,
            ohlc_violation_count=ohlc_violations,
            timezone_issues=tz_issues,
            issues=issues,
            passed=passed,
        )

    def validate_symbol(self, symbol: str, timeframe: str = "1m") -> ValidationResult:
        filepath = os.path.join(self._dir, f"{symbol.upper()}_{timeframe}.csv")
        return self.validate_file(filepath, symbol.upper(), timeframe)

    def validate_all_phase1(self, timeframes: Optional[list] = None) -> dict[str, ValidationResult]:
        from futures_engine.core.futures_symbol_registry import PHASE1_SYMBOLS
        tfs = timeframes or ["1m", "1h", "4h"]
        results = {}
        for sym in PHASE1_SYMBOLS:
            for tf in tfs:
                key = f"{sym}_{tf}"
                results[key] = self.validate_symbol(sym, tf)
        return results

    @staticmethod
    def _parse_ts(raw: str) -> datetime:
        raw = raw.strip()
        if raw.isdigit():
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        for fmt in [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%Y%m%d %H%M%S",   # NinjaTrader YYYYMMDD HHMMSS
            "%Y%m%d %H%M",
            "%Y%m%d",
        ]:
            try:
                dt = datetime.strptime(raw.replace("Z", "+00:00"), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        raise ValueError(f"Cannot parse timestamp: {raw!r}")

    @staticmethod
    def _is_weekend_gap(ts_before: datetime, ts_after: datetime) -> bool:
        """True if the gap spans a Saturday or Sunday CME closure."""
        # UTC weekday: 0=Mon, 4=Fri, 5=Sat, 6=Sun
        wday_before = ts_before.astimezone(timezone.utc).weekday()
        wday_after  = ts_after.astimezone(timezone.utc).weekday()
        gap_min = (ts_after - ts_before).total_seconds() / 60
        # Any gap that includes Saturday (>= Friday afternoon) and crosses to Sunday/Monday
        if wday_before == 4 and gap_min >= 60:  # Friday gap ≥ 1 hour
            return True
        if wday_before == 5:  # Starts on Saturday — always closed
            return True
        if wday_before == 6 and wday_after == 6 and gap_min < 180:  # Sunday pre-open
            return True
        # Gap includes a Saturday in the middle
        ts_mid = ts_before + (ts_after - ts_before) / 2
        if ts_mid.astimezone(timezone.utc).weekday() == 5:
            return True
        return False

    @staticmethod
    def _is_holiday_gap(ts_before: datetime, ts_after: datetime) -> bool:
        """True if the gap is explained by a CME market holiday."""
        try:
            from futures_engine.core.futures_session_manager import CME_HOLIDAYS, CME_HALF_DAYS
        except ImportError:
            return False
        date_before = ts_before.astimezone(timezone.utc).date()
        date_after  = ts_after.astimezone(timezone.utc).date()
        gap_days = (date_after - date_before).days
        if gap_days <= 0 or gap_days > 10:
            return False
        # Walk every date spanning the gap (inclusive of both endpoints).
        # A bar timestamped 2024-11-28 04:00 UTC is actually 2024-11-27 22:00 CT
        # (the evening before Thanksgiving). The gap spans the holiday, so we
        # start the walk from date_before itself, not date_before+1.
        from datetime import date as _date
        cur = date_before
        while cur <= date_after:
            if cur in CME_HOLIDAYS:
                return True
            cur += timedelta(days=1)
        return False

    @staticmethod
    def _is_daily_maintenance(ts_before: datetime, ts_after: datetime, timeframe: str) -> bool:
        """True if the gap matches CME's daily 60-min maintenance window (UTC 21:00-22:00)."""
        if timeframe not in ("1m", "5m", "15m"):
            return False
        gap_min = (ts_after - ts_before).total_seconds() / 60
        if gap_min > 90:  # longer than maintenance break
            return False
        # Maintenance ends around UTC 22:00 (17:00 CT winter / 18:00 CT summer)
        before_hour = ts_before.astimezone(timezone.utc).hour
        after_hour  = ts_after.astimezone(timezone.utc).hour
        # Window: before in 20–21 UTC, after in 21–23 UTC
        if before_hour in (20, 21) and after_hour in (21, 22, 23):
            return True
        return False


def generate_data_quality_report(
    validator: FuturesDataValidator,
    symbols: Optional[list] = None,
    timeframes: Optional[list] = None,
    output_path: str = "reports/futures/data_quality_report.md",
) -> str:
    from futures_engine.core.futures_symbol_registry import PHASE1_SYMBOLS
    syms = symbols or (PHASE1_SYMBOLS + ["MGC"])
    tfs  = timeframes or ["1m", "1h", "4h"]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    lines = [
        "# CB6 Futures Core — Data Quality Report",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Data directory:** `{validator._dir}`",
        "",
        "---",
        "",
        "## Summary Table",
        "",
        f"| {'Symbol':<6} | {'TF':<4} | {'Bars':>7} | {'Date Range':<35} | {'Dups':>5} | {'Gaps':>5} | {'OHLC':>5} | Status |",
        f"|{'-'*7}|{'-'*6}|{'-'*8}|{'-'*36}|{'-'*6}|{'-'*6}|{'-'*6}|{'-'*8}|",
    ]

    all_results = {}
    for sym in syms:
        for tf in tfs:
            key = f"{sym}_{tf}"
            result = validator.validate_symbol(sym, tf)
            all_results[key] = result
            dr = ""
            if result.date_range_start:
                s = result.date_range_start[:10]
                e = result.date_range_end[:10] if result.date_range_end else "?"
                dr = f"{s} → {e}"
            status = "✓ PASS" if result.passed else "✗ FAIL"
            if result.total_bars == 0:
                status = "— MISSING"
            lines.append(
                f"| {sym:<6} | {tf:<4} | {result.total_bars:>7,} | {dr:<35} | "
                f"{result.duplicate_count:>5} | {result.gap_count_error:>5} | "
                f"{result.ohlc_violation_count:>5} | {status} |"
            )

    lines += ["", "---", "", "## Per-Symbol Detail", ""]

    for key, result in all_results.items():
        lines.append(f"### {result.symbol} [{result.timeframe}]")
        lines.append("")
        if result.total_bars == 0:
            lines.append("**File not found.** No data available for this symbol/timeframe combination.")
            lines.append("")
            if result.timeframe == "1m":
                lines.append(
                    "> **Action required:** Export 1m data from TradingView, NinjaTrader Kinetick, "
                    "or Rithmic and import using:"
                )
                lines.append(f"> ```")
                lines.append(f"> python -m futures_engine.research.futures_data_downloader \\")
                lines.append(f">   --symbol {result.symbol} --source csv \\")
                lines.append(f">   --file {result.symbol}_1m.csv --timeframe 1m")
                lines.append(f"> ```")
            lines.append("")
            continue

        lines.append(f"- **Bars:** {result.total_bars:,}")
        lines.append(f"- **Range:** {result.date_range_start} → {result.date_range_end}")
        lines.append(f"- **Duplicates:** {result.duplicate_count}")
        lines.append(f"- **Large gaps (errors):** {result.gap_count_error}")
        lines.append(f"- **Session gaps (warnings):** {result.gap_count_warn}")
        lines.append(f"- **OHLC violations:** {result.ohlc_violation_count}")
        lines.append(f"- **Status:** {'✓ PASS' if result.passed else '✗ FAIL'}")
        lines.append("")

        errors   = [i for i in result.issues if i.severity == "ERROR"]
        warnings = [i for i in result.issues if i.severity == "WARNING"][:5]

        if errors:
            lines.append("**Errors:**")
            for e in errors[:10]:
                lines.append(f"- `{e.category}` L{e.line_num}: {e.description}")
            if len(errors) > 10:
                lines.append(f"- … and {len(errors)-10} more errors")
            lines.append("")

        if warnings:
            lines.append("**Warnings (first 5):**")
            for w in warnings:
                lines.append(f"- `{w.category}` L{w.line_num}: {w.description}")
            lines.append("")

    # 1m readiness
    lines += ["---", "", "## 1m Data Readiness for Corrected Backtests", ""]
    for sym in ["MES", "MGC", "MNQ"]:
        r = all_results.get(f"{sym}_1m")
        if r and r.total_bars > 5000 and r.passed:
            lines.append(f"- **{sym} 1m:** READY ({r.total_bars:,} bars, {r.date_range_start[:10]} → {r.date_range_end[:10]})")
        elif r and r.total_bars > 0:
            lines.append(f"- **{sym} 1m:** PARTIAL — {r.total_bars:,} bars (need ≥ 5,000), issues: {r.error_count} errors")
        else:
            lines.append(f"- **{sym} 1m:** **MISSING** — must be imported before production backtesting")

    lines += [
        "",
        "---",
        "",
        "## Import Instructions",
        "",
        "### Option 1 — NinjaTrader + Kinetick (Recommended, Free)",
        "```",
        "1. Download NinjaTrader 8 (free): ninjatrader.com",
        "2. Register for Kinetick data feed (free with NinjaTrader account)",
        "3. Connect Kinetick → Tools → Historical Data → Request Data",
        "4. Symbol: MES 09-25 (continuous back-adjust: @MES#)",
        "5. Export to CSV: right-click → Export → CSV",
        "6. Import:",
        "   python -m futures_engine.research.futures_data_downloader \\",
        "     --symbol MES --source csv --file MES_1m.csv --timeframe 1m",
        "```",
        "",
        "### Option 2 — TradingView Pro+ Export",
        "```",
        "1. Open MES1! chart on TradingView, 1 minute timeframe",
        "2. Click Export → Download → CSV",
        "3. Limited to 20,000 bars (~2 weeks) — insufficient for full backtest",
        "4. Useful for spot-checking signal quality only",
        "```",
        "",
        "After import, validate with:",
        "```",
        "python -m futures_engine.research.futures_data_validator --symbol MES --timeframe 1m",
        "```",
    ]

    content = "\n".join(lines) + "\n"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="CB6 Futures Data Quality Validator")
    p.add_argument("--symbol", default=None)
    p.add_argument("--timeframe", default="1m")
    p.add_argument("--file", default=None, help="Direct path to CSV file")
    p.add_argument("--all", action="store_true", help="Validate all Phase 1 symbols")
    p.add_argument("--report", action="store_true", help="Generate markdown report")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    v = FuturesDataValidator()

    if args.report or args.all:
        path = generate_data_quality_report(v)
        print(f"Report: {path}")
        return

    if args.file:
        sym = args.symbol or os.path.basename(args.file).split("_")[0]
        result = v.validate_file(args.file, sym, args.timeframe)
    elif args.symbol:
        result = v.validate_symbol(args.symbol, args.timeframe)
    else:
        path = generate_data_quality_report(v)
        print(f"Report: {path}")
        return

    status = "PASS" if result.passed else "FAIL"
    print(f"\n{result.symbol} {result.timeframe}: {status}")
    print(f"  Bars: {result.total_bars:,}  |  Dups: {result.duplicate_count}  |  "
          f"Gaps: {result.gap_count_error}  |  OHLC: {result.ohlc_violation_count}")
    if result.date_range_start:
        print(f"  Range: {result.date_range_start[:10]} → {result.date_range_end[:10]}")
    for issue in result.issues:
        icon = "✗" if issue.severity == "ERROR" else "!"
        print(f"  {icon} [{issue.category}] {issue.description}")


if __name__ == "__main__":
    main()
