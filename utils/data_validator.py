"""
Data Validator — CB6 Quantum Phase 3.5
Checks the OHLCV archive for quality issues:
  - Duplicate timestamps
  - Bad OHLC values (high < low, zero/negative prices)
  - Timezone drift (all archive ts should be UTC)
  - Gaps during expected market hours
  - Candle count sanity vs expected bars per period

Returns a structured ValidationReport for use by health_check.py.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, time as dtime
from typing import Optional
import pandas as pd

from utils.ohlcv_archive import get_candles, catalog
from utils.trade_db import _connect, init_db

# ---------------------------------------------------------------------------
# Expected bar counts per timeframe per trading day
# NSE: ~6.25 hours, 375 minutes | Forex: 24h weekdays
# ---------------------------------------------------------------------------
NSE_MARKET_OPEN_IST  = dtime(9, 15)
NSE_MARKET_CLOSE_IST = dtime(15, 30)
NSE_MINUTES_PER_DAY  = 375   # 9:15 to 15:30

EXPECTED_BARS_PER_DAY = {
    # NSE
    ("NSE", "15m"): 25,    # 375 / 15
    ("NSE", "1h"):   7,    # 6.25 rounded up
    ("NSE", "D"):    1,
    # Forex (London + NY sessions, ~16h of active trading)
    ("FOREX", "15m"): 64,
    ("FOREX", "1h"):  16,
    ("FOREX", "4h"):   4,
    ("FOREX", "1d"):   1,
}


@dataclass
class SymbolValidation:
    market:       str
    symbol:       str
    timeframe:    str
    total_bars:   int = 0
    duplicates:   int = 0
    bad_ohlc:     int = 0
    tz_issues:    int = 0
    gaps:         list = field(default_factory=list)   # list of gap descriptions
    coverage_days: int = 0
    expected_bars: int = 0
    coverage_pct:  float = 0.0
    issues:        list = field(default_factory=list)  # human-readable issue list
    ok:            bool = True


@dataclass
class ValidationReport:
    generated_at:  str
    symbols:       list[SymbolValidation] = field(default_factory=list)
    total_issues:  int = 0
    warnings:      list[str] = field(default_factory=list)

    def has_issues(self) -> bool:
        return self.total_issues > 0

    def summary(self) -> str:
        lines = [f"Data Validation Report — {self.generated_at}",
                 f"Symbols checked: {len(self.symbols)} | Issues: {self.total_issues}"]
        for sv in self.symbols:
            status = "OK" if sv.ok else "WARN"
            lines.append(
                f"  [{status}] {sv.market:6} {sv.symbol.replace('NSE:',''):<28} "
                f"{sv.timeframe:<5} {sv.total_bars:>6} bars  "
                f"cov={sv.coverage_pct:.0f}%  "
                f"dup={sv.duplicates}  bad={sv.bad_ohlc}  gaps={len(sv.gaps)}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-symbol validation
# ---------------------------------------------------------------------------

def validate_symbol(market: str, symbol: str, timeframe: str,
                    limit: int = 2000) -> SymbolValidation:
    sv = SymbolValidation(market=market, symbol=symbol, timeframe=timeframe)

    df = get_candles(market, symbol, timeframe, limit=limit)
    if df.empty:
        sv.issues.append("no data in archive")
        sv.ok = False
        return sv

    sv.total_bars = len(df)

    # ── 1. Duplicate timestamps ──────────────────────────────────────────────
    dup_count = int(df["timestamp"].duplicated().sum())
    sv.duplicates = dup_count
    if dup_count:
        sv.issues.append(f"{dup_count} duplicate timestamps")
        sv.ok = False

    # ── 2. Bad OHLC values ───────────────────────────────────────────────────
    bad = df[
        (df["high"] < df["low"]) |
        (df["open"] <= 0) |
        (df["close"] <= 0) |
        (df["high"] <= 0) |
        (df["low"] <= 0)
    ]
    sv.bad_ohlc = len(bad)
    if sv.bad_ohlc:
        sv.issues.append(f"{sv.bad_ohlc} bars with bad OHLC (high<low or zero prices)")
        sv.ok = False

    # ── 3. Timezone check ────────────────────────────────────────────────────
    # All timestamps in archive should be UTC (have +00:00 or be tz-aware UTC)
    # NSE stored as IST strings (e.g. "2026-05-19T09:15:00") — flag if offset present
    sample = str(df["timestamp"].iloc[0]) if not df.empty else ""
    if "+05:30" in sample or "Asia" in sample:
        sv.tz_issues = sv.total_bars
        sv.issues.append("timestamps appear to be IST — expected UTC")
        sv.ok = False
    elif "+08" in sample or "+09" in sample:
        sv.tz_issues = sv.total_bars
        sv.issues.append("suspicious timezone offset in timestamps")
        sv.ok = False

    # ── 4. Coverage vs expected ──────────────────────────────────────────────
    df_sorted = df.sort_values("timestamp").reset_index(drop=True)
    oldest = df_sorted["timestamp"].iloc[0]
    newest = df_sorted["timestamp"].iloc[-1]

    if pd.notna(oldest) and pd.notna(newest):
        span_days = max((newest - oldest).days, 1)
        sv.coverage_days = span_days

        # Estimate expected bars: trading days in span × bars/day
        trading_days = int(span_days * 5 / 7)   # rough weekday estimate
        bars_per_day = EXPECTED_BARS_PER_DAY.get((market, timeframe), 0)
        if bars_per_day:
            sv.expected_bars = trading_days * bars_per_day
            sv.coverage_pct  = min(sv.total_bars / sv.expected_bars * 100, 100.0)
            if sv.coverage_pct < 70:
                sv.issues.append(
                    f"low coverage {sv.coverage_pct:.0f}% "
                    f"({sv.total_bars}/{sv.expected_bars} expected bars)"
                )
                sv.ok = False
        else:
            sv.coverage_pct = 100.0   # unknown TF — skip coverage check

    # ── 5. Gap detection ─────────────────────────────────────────────────────
    sv.gaps = _detect_gaps(df_sorted, market, timeframe)
    if len(sv.gaps) > 5:
        sv.issues.append(f"{len(sv.gaps)} candle gaps detected")
        sv.ok = False
    elif sv.gaps:
        sv.issues.append(f"{len(sv.gaps)} minor gaps")

    return sv


def _detect_gaps(df: pd.DataFrame, market: str, timeframe: str) -> list[str]:
    """Find large unexpected gaps between consecutive candles."""
    if len(df) < 2:
        return []

    tf_minutes = {
        "1m": 1, "3m": 3, "5m": 5, "15m": 15,
        "30m": 30, "1h": 60, "4h": 240, "D": 1440,
    }
    expected_gap = tf_minutes.get(timeframe, 60)
    # NSE overnight gap: 15:30→9:15 next day = ~17.75h. Use 90× for 15m (90×15=1350min > 1065min ok).
    # Forex weekend gap: 6× expected_gap for daily, 96× for hourly (4 days × 24h).
    if market == "NSE":
        max_ok_gap = expected_gap * 90   # covers overnight; flags multi-day holiday gaps
    else:
        max_ok_gap = expected_gap * 96   # covers Forex weekend

    gaps = []
    ts = df["timestamp"]
    for i in range(1, min(len(ts), 500)):   # check first 500 gaps
        try:
            diff = (ts.iloc[i] - ts.iloc[i-1]).total_seconds() / 60
        except Exception:
            continue
        if diff > max_ok_gap:
            gaps.append(
                f"{str(ts.iloc[i-1])[:16]} → {str(ts.iloc[i])[:16]} "
                f"({diff/60:.1f}h gap)"
            )
    return gaps[:20]   # cap at 20 reported gaps


# ---------------------------------------------------------------------------
# Full archive scan
# ---------------------------------------------------------------------------

def validate_all() -> ValidationReport:
    """Run validation across all symbols in the archive."""
    report = ValidationReport(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    entries = catalog()   # list of {market, symbol, timeframe, bars, oldest, newest}

    for entry in entries:
        sv = validate_symbol(entry["market"], entry["symbol"], entry["timeframe"])
        report.symbols.append(sv)
        report.total_issues += len(sv.issues)

    return report


# ---------------------------------------------------------------------------
# Trade DB checks
# ---------------------------------------------------------------------------

def validate_trade_db() -> dict:
    """Quick sanity checks on the trades table."""
    init_db()
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        no_result = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE result IS NULL AND exit_time IS NOT NULL"
        ).fetchone()[0]
        no_exit = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE exit_time IS NULL AND result IS NOT NULL"
        ).fetchone()[0]
        future_entries = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE entry_time > datetime('now','+1 day')"
        ).fetchone()[0]
        dup_ids = conn.execute(
            "SELECT COUNT(*) FROM (SELECT trade_id, COUNT(*) c FROM trades GROUP BY trade_id HAVING c > 1)"
        ).fetchone()[0]

    issues = []
    if no_result:  issues.append(f"{no_result} closed trades missing result field")
    if no_exit:    issues.append(f"{no_exit} non-open trades missing exit_time")
    if future_entries: issues.append(f"{future_entries} trades with future entry_time")
    if dup_ids:    issues.append(f"{dup_ids} duplicate trade IDs")

    return {
        "total_trades": total,
        "issues": issues,
        "ok": len(issues) == 0,
    }
