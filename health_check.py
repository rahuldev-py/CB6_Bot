"""
CB6 Quantum — Data Health Dashboard
Run from project root: python health_check.py

Shows:
  - OHLCV archive: freshness, bar counts, validation issues
  - OI snapshot: last snapshot time, coverage
  - Trade DB: record counts, completeness
  - Regime status: current classification per symbol
  - Stale data warnings
"""

import sys
import os
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import pytz

sys.path.insert(0, str(Path(__file__).parent))

IST = pytz.timezone("Asia/Kolkata")
NOW_IST = datetime.now(IST)
NOW_UTC = datetime.utcnow()

# Staleness thresholds — adjusted for market hours context
# NSE: during market hours (9:15-15:30 IST) stale after 2h, outside after 20h
# Forex: 24/5, stale after 6h; weekends are expected to have no new data
_MARKET_OPEN_IST  = 9 * 60 + 15    # 09:15 in minutes
_MARKET_CLOSE_IST = 15 * 60 + 30   # 15:30 in minutes
_NOW_MINS_IST     = NOW_IST.hour * 60 + NOW_IST.minute
_IN_MARKET_HOURS  = _MARKET_OPEN_IST <= _NOW_MINS_IST <= _MARKET_CLOSE_IST
_IS_WEEKDAY       = NOW_IST.weekday() < 5

STALE_CANDLE_HOURS  = 2 if (_IN_MARKET_HOURS and _IS_WEEKDAY) else 20
STALE_OI_HOURS      = 26
STALE_TRADE_DAYS    = 3

SEP  = "─" * 72
SEP2 = "═" * 72


def _age_label(ts_str: str) -> str:
    """Human-readable age of a timestamp string."""
    if not ts_str:
        return "never"
    try:
        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=pytz.UTC)
        delta = datetime.now(pytz.UTC) - ts
        h = delta.total_seconds() / 3600
        if h < 1:   return f"{int(delta.total_seconds()/60)}m ago"
        if h < 24:  return f"{h:.1f}h ago"
        return f"{delta.days}d ago"
    except Exception:
        return str(ts_str)[:16]


def _stale_flag(ts_str: str, warn_hours: float, timeframe: str = "") -> str:
    if not ts_str:
        return " [NO DATA]"
    try:
        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=pytz.UTC)
        age_h = (datetime.now(pytz.UTC) - ts).total_seconds() / 3600
        # Daily candles only update post-market — allow 28h before flagging
        effective_threshold = 28 if timeframe == "D" else warn_hours
        if age_h > effective_threshold:
            return f" [STALE — {age_h:.0f}h old]"
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Section 1: OHLCV Archive
# ---------------------------------------------------------------------------

def _section_ohlcv(validate: bool = False):
    print(f"\n{'OHLCV ARCHIVE':^72}")
    print(SEP)

    try:
        from utils.ohlcv_archive import catalog
        rows = catalog()
    except Exception as e:
        print(f"  ERROR loading archive catalog: {e}")
        return

    if not rows:
        print("  Archive is empty. Run: python archive_ohlcv.py --days 365")
        return

    print(f"  {'Market':<8} {'Symbol':<28} {'TF':<5} {'Bars':>7}  {'Oldest':<12}  {'Newest':<16}  Age")
    print(f"  {'-'*7} {'-'*27} {'-'*4} {'-'*7}  {'-'*11}  {'-'*15}  {'-'*12}")

    warnings = []
    for r in rows:
        age_label = _age_label(r["newest"])
        stale = _stale_flag(r["newest"], STALE_CANDLE_HOURS, r["timeframe"])
        sym = r["symbol"].replace("NSE:", "")
        oldest_s = str(r["oldest"])[:10] if r["oldest"] else "?"
        newest_s = str(r["newest"])[:16] if r["newest"] else "?"
        flag = " ⚠" if stale else ""
        print(f"  {r['market']:<8} {sym:<28} {r['timeframe']:<5} {r['bars']:>7}  "
              f"{oldest_s:<12}  {newest_s:<16}  {age_label}{stale}{flag}")
        if stale:
            warnings.append(f"{r['market']} {sym} {r['timeframe']} is stale")

    if warnings:
        print()
        for w in warnings:
            print(f"  ⚠  {w}")

    # Optional: full validation
    if validate:
        print()
        print(f"  {'VALIDATION':^68}")
        print(f"  {'-'*68}")
        try:
            from utils.data_validator import validate_all
            report = validate_all()
            for sv in report.symbols:
                status = "OK  " if sv.ok else "WARN"
                sym = sv.symbol.replace("NSE:", "")
                cov = f"{sv.coverage_pct:.0f}%" if sv.coverage_pct else "N/A"
                print(f"  [{status}] {sv.market:<6} {sym:<26} {sv.timeframe:<5} "
                      f"cov={cov:<6} dup={sv.duplicates} bad={sv.bad_ohlc} "
                      f"gaps={len(sv.gaps)}")
                for issue in sv.issues:
                    print(f"         → {issue}")
            print(f"\n  Total issues: {report.total_issues}")
        except Exception as e:
            print(f"  Validation error: {e}")


# ---------------------------------------------------------------------------
# Section 2: OI Archive
# ---------------------------------------------------------------------------

def _section_oi():
    print(f"\n{'OI / OPTIONS CHAIN ARCHIVE':^72}")
    print(SEP)

    try:
        from utils.oi_archive import oi_catalog
        rows = oi_catalog()
    except Exception as e:
        print(f"  ERROR: {e}")
        return

    if not rows:
        print("  No OI snapshots yet. Run: python archive_oi.py  (after market close)")
        return

    print(f"  {'Symbol':<15} {'Snapshots':>10}  {'Oldest':<22}  {'Newest':<22}  Age")
    print(f"  {'-'*14} {'-'*10}  {'-'*21}  {'-'*21}  {'-'*12}")
    for r in rows:
        age_label = _age_label(r["newest"])
        stale = _stale_flag(r["newest"], STALE_OI_HOURS)
        flag = " ⚠" if stale else ""
        print(f"  {r['symbol']:<15} {r['snapshots']:>10}  "
              f"{str(r['oldest']):<22}  {str(r['newest']):<22}  {age_label}{stale}{flag}")


# ---------------------------------------------------------------------------
# Section 3: Trade DB
# ---------------------------------------------------------------------------

def _section_trades():
    print(f"\n{'TRADE DATABASE':^72}")
    print(SEP)

    try:
        from utils.trade_db import query
        from utils.data_validator import validate_trade_db

        # Per-account summary
        rows = query("""
            SELECT account, mode,
                   COUNT(*) AS total,
                   SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END) AS wins,
                   ROUND(SUM(pnl_usd),2) AS pnl,
                   MAX(entry_time) AS last_trade
            FROM trades
            GROUP BY account, mode
            ORDER BY account
        """)

        if not rows:
            print("  Trade DB empty. Run: python sync_trade_db.py")
            return

        print(f"  {'Account':<15} {'Mode':<12} {'Trades':>7} {'Wins':>6} {'PnL':>10}  {'Last Trade':<20}  Age")
        print(f"  {'-'*14} {'-'*11} {'-'*7} {'-'*6} {'-'*10}  {'-'*19}  {'-'*12}")
        for r in rows:
            total = r["total"] or 0
            wins  = r["wins"]  or 0
            wr    = f"{wins/total*100:.0f}%" if total else "N/A"
            age   = _age_label(r["last_trade"])
            pnl_s = f"${r['pnl'] or 0:+.2f}"
            print(f"  {r['account']:<15} {r['mode']:<12} {total:>7} "
                  f"{wr:>6} {pnl_s:>10}  {str(r['last_trade'] or ''):<20}  {age}")

        # Validation
        v = validate_trade_db()
        if v["issues"]:
            print()
            for issue in v["issues"]:
                print(f"  ⚠  {issue}")
        else:
            print(f"\n  Trade DB integrity: OK ({v['total_trades']} total records)")

    except Exception as e:
        print(f"  ERROR: {e}")


# ---------------------------------------------------------------------------
# Section 4: Regime Status
# ---------------------------------------------------------------------------

def _section_regime():
    print(f"\n{'MARKET REGIME (from archive)':^72}")
    print(SEP)

    try:
        from utils.market_intelligence import MarketIntelligence, SNAPSHOT_SYMBOLS
        mi = MarketIntelligence()

        print(f"  {'Symbol':<28} {'TF':<5} {'Regime':<15} {'Strength':<10} {'Vol':<8} {'ADX':>5}")
        print(f"  {'-'*27} {'-'*4} {'-'*14} {'-'*9} {'-'*7} {'-'*5}")

        for market, symbol, tf in SNAPSHOT_SYMBOLS:
            r = mi.get_regime(market, symbol, tf)
            sym = symbol.replace("NSE:", "")
            regime_flag = ""
            if r.regime == "CHOPPY":   regime_flag = " ⚠"
            if r.regime == "UNKNOWN":  regime_flag = " ?"
            print(f"  {sym:<28} {tf:<5} {r.regime:<15}{regime_flag} "
                  f"{r.trend_strength:<10} {r.volatility:<8} {r.adx:>5.1f}")

    except Exception as e:
        print(f"  ERROR: {e}")


# ---------------------------------------------------------------------------
# Section 5: System Summary
# ---------------------------------------------------------------------------

def _section_summary():
    print(f"\n{'SYSTEM SUMMARY':^72}")
    print(SEP)
    print(f"  Generated : {NOW_IST.strftime('%Y-%m-%d %H:%M IST')}")

    # Quick counts
    try:
        from utils.ohlcv_archive import catalog as ohlcv_catalog
        from utils.oi_archive import oi_catalog
        from utils.trade_db import query

        ohlcv_rows  = ohlcv_catalog()
        oi_rows     = oi_catalog()
        trade_count = query("SELECT COUNT(*) AS n FROM trades")[0]["n"]
        total_bars  = sum(r["bars"] for r in ohlcv_rows)
        oi_snaps    = sum(r["snapshots"] for r in oi_rows)

        print(f"  OHLCV bars : {total_bars:,} ({len(ohlcv_rows)} symbol/TF pairs)")
        print(f"  OI snaps   : {oi_snaps:,} ({len(oi_rows)} symbols)")
        print(f"  Trades     : {trade_count:,}")
    except Exception as e:
        print(f"  Summary error: {e}")

    print()
    print("  Quick commands:")
    print("    python health_check.py --validate        full data quality check")
    print("    python archive_ohlcv.py                  refresh OHLCV archive")
    print("    python archive_oi.py                     snapshot OI (after market)")
    print("    python sync_trade_db.py --stats          trade performance")
    print("    python -m utils.market_intelligence      live regime snapshot")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CB6 Data Health Dashboard")
    parser.add_argument("--validate", action="store_true",
                        help="Run full data quality validation (slower)")
    parser.add_argument("--section", choices=["ohlcv", "oi", "trades", "regime", "all"],
                        default="all", help="Which section to show")
    args = parser.parse_args()

    print()
    print(SEP2)
    print(f"{'CB6 QUANTUM — DATA HEALTH DASHBOARD':^72}")
    print(SEP2)

    sec = args.section
    if sec in ("all", "ohlcv"):   _section_ohlcv(validate=args.validate)
    if sec in ("all", "oi"):      _section_oi()
    if sec in ("all", "trades"):  _section_trades()
    if sec in ("all", "regime"):  _section_regime()
    if sec == "all":              _section_summary()
    print()


if __name__ == "__main__":
    main()
