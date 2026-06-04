"""
CB6 Quantum — Weekly Research Report
Auto-generates a summary of the past 7 days across:
  - Best/worst regime, index, session, OI behaviour
  - Correlation changes
  - Trade performance by condition

Run from project root:
  python weekly_research_report.py
  python weekly_research_report.py --days 14    # 2-week window
  python weekly_research_report.py --save       # save to reports/
"""

import sys
import os
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import pytz

sys.path.insert(0, str(Path(__file__).parent))

IST = pytz.timezone("Asia/Kolkata")
NOW = datetime.now(IST)
SEP  = "─" * 72
SEP2 = "═" * 72


def _pct(wins, total) -> str:
    if not total: return "N/A"
    return f"{wins/total*100:.0f}%"


# ---------------------------------------------------------------------------
# Section 1: Trade Performance by Condition
# ---------------------------------------------------------------------------

def _section_trade_performance(days: int) -> list[str]:
    lines = []
    lines.append(f"\n{'TRADE PERFORMANCE — LAST %d DAYS' % days:^72}")
    lines.append(SEP)
    try:
        from utils.trade_replay import winning_conditions
        from utils.trade_db import query

        since = (NOW - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = query(f"""
            SELECT account, symbol, direction, result,
                   pnl_usd, r_multiple, session, mss_type,
                   entry_time
            FROM trades
            WHERE result IS NOT NULL AND entry_time >= '{since}'
            ORDER BY entry_time DESC
        """)

        if not rows:
            lines.append("  No closed trades in the period.")
            return lines

        total = len(rows)
        wins  = sum(1 for r in rows if r["result"] == "WIN")
        total_pnl = sum((r["pnl_usd"] or 0) for r in rows)

        lines.append(f"  Period   : {since} → {NOW.strftime('%Y-%m-%d')}")
        lines.append(f"  Trades   : {total}  |  WR: {_pct(wins, total)}  |  PnL: ${total_pnl:+.2f}")

        # By account
        accs = {}
        for r in rows:
            a = r["account"]
            accs.setdefault(a, {"total": 0, "wins": 0, "pnl": 0.0})
            accs[a]["total"] += 1
            accs[a]["wins"]  += 1 if r["result"] == "WIN" else 0
            accs[a]["pnl"]   += r["pnl_usd"] or 0

        lines.append(f"\n  {'Account':<15} {'Trades':>7} {'WR':>6} {'PnL':>10}")
        lines.append(f"  {'-'*14} {'-'*7} {'-'*6} {'-'*10}")
        for acc, d in sorted(accs.items()):
            lines.append(f"  {acc:<15} {d['total']:>7} {_pct(d['wins'],d['total']):>6} ${d['pnl']:>+9.2f}")

        # By session
        lines.append(f"\n  {'Session':<15} {'Trades':>7} {'WR':>6} {'PnL':>10}")
        lines.append(f"  {'-'*14} {'-'*7} {'-'*6} {'-'*10}")
        sess = {}
        for r in rows:
            s = r["session"] or "unknown"
            sess.setdefault(s, {"total": 0, "wins": 0, "pnl": 0.0})
            sess[s]["total"] += 1
            sess[s]["wins"]  += 1 if r["result"] == "WIN" else 0
            sess[s]["pnl"]   += r["pnl_usd"] or 0
        for s, d in sorted(sess.items(), key=lambda x: -x[1]["total"]):
            lines.append(f"  {s:<15} {d['total']:>7} {_pct(d['wins'],d['total']):>6} ${d['pnl']:>+9.2f}")

        # Winning conditions from replay DB
        wc = winning_conditions()
        if wc.get("by_regime_4h"):
            lines.append(f"\n  {'Regime (4H)':<15} {'Trades':>7} {'WR':>6} {'AvgPnL':>10} {'AvgR':>7}")
            lines.append(f"  {'-'*14} {'-'*7} {'-'*6} {'-'*10} {'-'*7}")
            for r in wc["by_regime_4h"]:
                if r["regime_4h"] and r["total"] > 0:
                    lines.append(
                        f"  {(r['regime_4h'] or 'N/A'):<15} {r['total']:>7} "
                        f"{_pct(r['wins'],r['total']):>6} "
                        f"${r['avg_pnl'] or 0:>+9.2f} {r['avg_r'] or 0:>7.3f}R"
                    )

    except Exception as e:
        lines.append(f"  Error: {e}")
    return lines


# ---------------------------------------------------------------------------
# Section 2: Regime Analysis
# ---------------------------------------------------------------------------

def _section_regime_analysis() -> list[str]:
    lines = []
    lines.append(f"\n{'CURRENT MARKET REGIME':^72}")
    lines.append(SEP)
    try:
        from utils.market_intelligence import MarketIntelligence, SNAPSHOT_SYMBOLS
        mi = MarketIntelligence()

        best_trend   = []
        choppy_list  = []
        high_vol     = []

        lines.append(f"  {'Symbol':<28} {'TF':<5} {'Regime':<15} {'Strength':<10} {'Vol':<8} {'ADX':>5}")
        lines.append(f"  {'-'*27} {'-'*4} {'-'*14} {'-'*9} {'-'*7} {'-'*5}")

        for market, symbol, tf in SNAPSHOT_SYMBOLS:
            r = mi.get_regime(market, symbol, tf)
            sym = symbol.replace("NSE:", "")
            if r.regime in ("TRENDING_UP", "TRENDING_DOWN") and r.trend_strength in ("STRONG", "MODERATE"):
                best_trend.append(f"{sym} {tf}")
            if r.regime == "CHOPPY":
                choppy_list.append(f"{sym} {tf}")
            if r.volatility == "HIGH":
                high_vol.append(f"{sym} {tf}")
            lines.append(
                f"  {sym:<28} {tf:<5} {r.regime:<15} "
                f"{r.trend_strength:<10} {r.volatility:<8} {r.adx:>5.1f}"
            )

        if best_trend:
            lines.append(f"\n  Best trending : {', '.join(best_trend[:3])}")
        if choppy_list:
            lines.append(f"  Choppy (avoid): {', '.join(choppy_list)}")
        if high_vol:
            lines.append(f"  High vol (reduce risk): {', '.join(high_vol)}")

    except Exception as e:
        lines.append(f"  Error: {e}")
    return lines


# ---------------------------------------------------------------------------
# Section 3: Correlation Analysis
# ---------------------------------------------------------------------------

def _section_correlations() -> list[str]:
    lines = []
    lines.append(f"\n{'CROSS-MARKET CORRELATIONS':^72}")
    lines.append(SEP)
    try:
        from utils.market_intelligence import MarketIntelligence
        mi = MarketIntelligence()
        corrs = mi.get_correlations(timeframe="1h", window=50)

        lines.append(f"  {'Pair':<42} {'Corr':>7}  {'Strength':<10} {'Note'}")
        lines.append(f"  {'-'*41} {'-'*7}  {'-'*9} {'-'*20}")

        for c in corrs:
            if c["bars_used"] == 0:
                note = "no data"
            elif abs(c["correlation"]) >= 0.7:
                note = "trade with confirmation"
            elif c["direction"] == "NEGATIVE" and abs(c["correlation"]) >= 0.4:
                note = "divergence watch"
            else:
                note = ""
            a = c["symbol_a"].replace("NSE:", "")
            b = c["symbol_b"].replace("NSE:", "")
            pair = f"{a} ↔ {b}"
            corr_s = f"{c['correlation']:>7.3f}" if c["bars_used"] else "    N/A"
            lines.append(f"  {pair:<42} {corr_s}  {c['strength']:<10} {note}")

    except Exception as e:
        lines.append(f"  Error: {e}")
    return lines


# ---------------------------------------------------------------------------
# Section 4: OI Analysis
# ---------------------------------------------------------------------------

def _section_oi_analysis(days: int) -> list[str]:
    lines = []
    lines.append(f"\n{'OI / PCR ANALYSIS':^72}")
    lines.append(SEP)
    try:
        from utils.oi_archive import get_pcr_history, get_max_oi_strikes, oi_catalog

        cat = oi_catalog()
        if not cat:
            lines.append("  No OI snapshots yet. Run archive_oi.py after market close.")
            return lines

        for sym_row in cat:
            sym = sym_row["symbol"]
            df = get_pcr_history(sym, limit=days * 2)  # ~2 snapshots/day
            if df.empty:
                continue
            avg_pcr = df["pcr_oi"].mean()
            latest_pcr = df.iloc[-1]["pcr_oi"]
            latest_bias = df.iloc[-1]["option_bias"]
            lines.append(f"\n  {sym}")
            lines.append(f"    Latest PCR  : {latest_pcr:.3f}  Bias: {latest_bias}")
            lines.append(f"    Avg PCR ({days}d) : {avg_pcr:.3f}")
            pcr_trend = "RISING (bullish)" if latest_pcr > avg_pcr * 1.05 else \
                        "FALLING (bearish)" if latest_pcr < avg_pcr * 0.95 else "STABLE"
            lines.append(f"    PCR Trend   : {pcr_trend}")

            lvl = get_max_oi_strikes(sym)
            if lvl:
                lines.append(f"    Resistance  : {lvl.get('max_ce_strike')} "
                              f"(CE OI {(lvl.get('max_ce_oi') or 0):,.0f})")
                lines.append(f"    Support     : {lvl.get('max_pe_strike')} "
                              f"(PE OI {(lvl.get('max_pe_oi') or 0):,.0f})")

    except Exception as e:
        lines.append(f"  Error: {e}")
    return lines


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Section 5: Macro + Knowledge Graph
# ---------------------------------------------------------------------------

def _section_macro_intelligence() -> list[str]:
    lines = []
    lines.append(f"\n{'MACRO INTELLIGENCE (Phase 5 Knowledge Graph)':^72}")
    lines.append(SEP)
    try:
        from utils.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()

        # Key scenarios relevant to CB6 instruments
        scenarios = [
            ("FED_RATE",       "Fed rate rising"),
            ("OIL_PRICE",      "Oil price rising"),
            ("GOLD",           "Gold rising (risk-off signal)"),
            ("USD_INDEX",      "USD Index rising"),
        ]

        for node, label in scenarios:
            impacts = kg.impact_of(node, depth=1, min_strength=0.5)
            key_impacts = impacts[:4]
            if not key_impacts:
                continue
            parts = []
            for imp in key_impacts:
                arrow = "↑" if imp.direction == "POSITIVE" else "↓"
                parts.append(f"{imp.node} {arrow}")
            lines.append(f"  {label:<30} → {', '.join(parts)}")

        # Trade context for CB6 active symbols
        lines.append(f"\n  SYMBOL MACRO CONTEXT:")
        for sym, node in [("XAUUSD", "XAUUSD"), ("USOIL", "USOIL"),
                           ("NIFTYBANK", "NSE:NIFTYBANK-INDEX")]:
            ctx = kg.trade_context(node)
            if ctx.get("drivers"):
                top = ctx["drivers"][0]
                arrow = "↑" if top["direction"] == "POSITIVE" else "↓"
                lines.append(f"  {sym:<12} top driver: {top['driver']} {arrow}  "
                              f"(str={top['strength']:.2f})")

    except Exception as e:
        lines.append(f"  Error: {e}")
    return lines


# ---------------------------------------------------------------------------
# Section 6: Sector Intelligence
# ---------------------------------------------------------------------------

def _section_sector_intelligence() -> list[str]:
    lines = []
    lines.append(f"\n{'SECTOR INTELLIGENCE (Phase 6)':^72}")
    lines.append(SEP)
    try:
        from utils.sector_intelligence import SectorIntelligence
        si = SectorIntelligence()

        # Active regime detection
        active_regimes = si.detect_active_regimes()
        lines.append(f"  Active macro regimes: {', '.join(active_regimes)}")

        if active_regimes and active_regimes != ["UNKNOWN"]:
            pred = si.multi_regime_prediction(active_regimes)
            if pred["winners"]:
                lines.append(f"  Expected winners: {', '.join(w['label'] for w in pred['winners'][:3])}")
            if pred["losers"]:
                lines.append(f"  Expected losers:  {', '.join(l['label'] for l in pred['losers'][:3])}")

        # Sector snapshot (data-available sectors only)
        snap = si.sector_snapshot()
        available = [s for s in snap if s.data_available]
        if available:
            lines.append(f"\n  {'Sector':<16} {'Regime':<15} {'7d%':>7}  {'vs Nifty':>9}")
            lines.append(f"  {'─'*16} {'─'*14} {'─'*7}  {'─'*9}")
            for s in available[:6]:
                m_arrow = "+" if s.momentum_7d >= 0 else ""
                r_arrow = "+" if s.vs_nifty_7d >= 0 else ""
                lines.append(
                    f"  {s.sector:<16} {s.regime:<15} "
                    f"{m_arrow}{s.momentum_7d:>5.1f}%  {r_arrow}{s.vs_nifty_7d:>7.1f}%"
                )
        else:
            lines.append("  (Archive sector data not yet available — run archive_ohlcv with sector symbols)")

        # Rotation matrix summary for context
        lines.append(f"\n  ROTATION PLAYBOOK (static knowledge):")
        for regime in ["RATE_CUT_CYCLE", "HIGH_OIL", "USD_STRONG"]:
            pred_r = si.rotation_prediction(regime)
            w = [p["label"] for p in pred_r["winners"][:2]]
            l = [p["label"] for p in pred_r["losers"][:2]]
            lines.append(f"  {regime:<22}  Win: {', '.join(w):<28}  Lose: {', '.join(l)}")

    except Exception as e:
        lines.append(f"  Error: {e}")
    return lines


# ---------------------------------------------------------------------------
# Section 7: Data Health Summary
# ---------------------------------------------------------------------------

def _section_data_health() -> list[str]:
    lines = []
    lines.append(f"\n{'DATA HEALTH SUMMARY':^72}")
    lines.append(SEP)
    try:
        from utils.ohlcv_archive import catalog as ohlcv_catalog
        from utils.oi_archive import oi_catalog
        from utils.trade_db import query

        ohlcv_rows = ohlcv_catalog()
        oi_rows    = oi_catalog()
        trades     = query("SELECT COUNT(*) AS n FROM trades")[0]["n"]
        total_bars = sum(r["bars"] for r in ohlcv_rows)
        oi_snaps   = sum(r["snapshots"] for r in oi_rows)

        lines.append(f"  OHLCV archive : {total_bars:,} bars ({len(ohlcv_rows)} symbol/TF pairs)")
        lines.append(f"  OI snapshots  : {oi_snaps:,}")
        lines.append(f"  Trades in DB  : {trades:,}")

        # Quick validation
        from utils.data_validator import validate_all
        report = validate_all()
        if report.total_issues:
            lines.append(f"\n  ⚠  {report.total_issues} data quality issues found:")
            for sv in report.symbols:
                for issue in sv.issues:
                    sym = sv.symbol.replace("NSE:", "")
                    lines.append(f"     {sv.market} {sym} {sv.timeframe}: {issue}")
        else:
            lines.append(f"\n  Data quality  : OK — no issues detected")

    except Exception as e:
        lines.append(f"  Error: {e}")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CB6 Weekly Research Report")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    parser.add_argument("--save", action="store_true",
                        help="Save report to reports/weekly_YYYYMMDD.txt")
    args = parser.parse_args()

    all_lines = []
    all_lines.append("")
    all_lines.append(SEP2)
    all_lines.append(f"{'CB6 QUANTUM — WEEKLY RESEARCH REPORT':^72}")
    all_lines.append(f"{'Generated: ' + NOW.strftime('%Y-%m-%d %H:%M IST'):^72}")
    all_lines.append(f"{'Window: Last %d days' % args.days:^72}")
    all_lines.append(SEP2)

    all_lines += _section_trade_performance(args.days)
    all_lines += _section_regime_analysis()
    all_lines += _section_correlations()
    all_lines += _section_oi_analysis(args.days)
    all_lines += _section_macro_intelligence()
    all_lines += _section_sector_intelligence()
    all_lines += _section_data_health()

    all_lines.append("")
    all_lines.append(SEP)
    all_lines.append("  End of report. Run `python health_check.py` for live data status.")
    all_lines.append("")

    report_text = "\n".join(all_lines)
    print(report_text)

    if args.save:
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        fname = reports_dir / f"weekly_{NOW.strftime('%Y%m%d')}.txt"
        fname.write_text(report_text, encoding="utf-8")
        print(f"\nReport saved to {fname}")


if __name__ == "__main__":
    main()
