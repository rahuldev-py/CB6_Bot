# backtest/forex_revalidate_backtest.py
#
# SAFE_VALIDATION_REVALIDATE_AUTO vs LEGACY — Forex 60-day comparison
#
# Mode logic:
#   LEGACY                         — every scanner signal executes unconditionally.
#   SAFE_VALIDATION_REVALIDATE_AUTO:
#       1. Instrument blacklist  — XAGUSD always blocked (WR 27%, PF 0.72 historically).
#       2. Validate at bar close — entry band + structure + RR at planned_entry + SL/TP sanity.
#       3. Revalidate 1 bar later — same checks at next bar's LTP (catches stale/chased entries).
#       4. Auto-execute if both passes — no manual confirmation required.
#
# Data: yfinance CME proxy  (GC=F  SI=F  CL=F) — graceful skip if unavailable.
# Does NOT place real orders.

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest.backtester import simulate_trade_outcome
from scanner.silver_bullet import scan_silver_bullet
from utils.execution_validation import revalidate_existing, SIGNAL_WAITING_CONFIRM

# ── Markets + CME proxy tickers ───────────────────────────────────────────────
FOREX_MARKETS: Dict[str, str] = {
    "XAUUSD": "GC=F",   # CME Gold futures
    "XAGUSD": "SI=F",   # CME Silver futures
    "USOIL":  "CL=F",   # CME Crude Oil futures (WTI)
}

# Instruments blocked in SAFE mode regardless of signal quality.
# XAGUSD: 100-day WR 27.2%, PF 0.724 — losing instrument, no edge confirmed.
SAFE_BLACKLIST: frozenset[str] = frozenset({"XAGUSD"})

# Reason string written to audit for blacklisted instruments
_BLACKLIST_REASON = "INSTRUMENT_BLACKLISTED"


# ── Data fetch — yfinance with MultiIndex flatten ─────────────────────────────
def _fetch_cme_proxy(market: str, ticker: str, days: int, interval: str = "5m"):
    """
    Download OHLCV via yfinance CME proxy.
    Handles yfinance ≥1.0 MultiIndex columns ((Price, Ticker) tuples).
    Returns DataFrame[timestamp, open, high, low, close] or None on any failure.
    """
    try:
        import yfinance as yf
        import pandas as pd

        period = "60d" if days >= 60 else "30d"
        raw = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
        if raw is None or raw.empty:
            print(f"  CME proxy: no data returned for {ticker}")
            return None

        raw = raw.reset_index()

        # Flatten MultiIndex columns: ('Close', 'GC=F') → 'close'
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [
                col[0].lower() if isinstance(col, tuple) else str(col).lower()
                for col in raw.columns
            ]
        else:
            raw.columns = [str(c).lower() for c in raw.columns]

        # Normalise timestamp column name
        ts_col = next(
            (c for c in raw.columns if c in ("datetime", "date", "timestamp")), None
        )
        if ts_col is None:
            print(f"  CME proxy: no timestamp column found for {ticker}")
            return None
        raw = raw.rename(columns={ts_col: "timestamp"})

        # Strip timezone so downstream code stays tz-naive
        raw["timestamp"] = pd.to_datetime(raw["timestamp"]).dt.tz_localize(None)

        for col in ("open", "high", "low", "close"):
            if col in raw.columns:
                raw[col] = pd.to_numeric(raw[col], errors="coerce")

        df = (
            raw[["timestamp", "open", "high", "low", "close"]]
            .dropna()
            .reset_index(drop=True)
        )
        print(f"  CME proxy OK: {len(df)} candles for {market} ({ticker})")
        return df if len(df) >= 120 else None

    except Exception as exc:
        print(f"  CME proxy error for {ticker}: {exc}")
        return None


# ── Execution config ──────────────────────────────────────────────────────────
_EXEC_CFG = {
    "max_entry_drift_percent":    2.0,
    "max_entry_drift_points":     3.0,
    "minimum_required_rr":        1.5,
    "invalidation_buffer_points": 10.0,
    "allowed_signal_age_seconds": 180,   # live-only; bar-replay uses ref_time=bar_time
}


def _make_signal(setup: Dict, ltp: float, bar_time: datetime) -> Dict:
    """Build a minimal signal dict compatible with revalidate_existing()."""
    sig = setup.get("entry_signal") or {}
    return {
        "signal_id":        "BT",
        "created_at":       bar_time.isoformat(),
        "symbol":           setup.get("symbol", ""),
        "direction":        setup.get("direction", ""),
        "planned_entry":    sig.get("entry"),
        "current_ltp":      ltp,
        "stop_loss":        sig.get("stop_loss"),
        "target":           sig.get("target2") or sig.get("target1") or sig.get("target3"),
        "target1":          sig.get("target1"),
        "target2":          sig.get("target2"),
        "target3":          sig.get("target3"),
        "calculated_rr":    None,
        "signal_age_seconds": 0,
    }


def _bucket(reason: str) -> str:
    return "STOP_TARGET_SANITY_FAILED_*" if reason.startswith("STOP_TARGET_SANITY_FAILED_") else reason


# ── Stats builder ─────────────────────────────────────────────────────────────
def _build_stats(
    executed: List[Dict],
    blocked: List[Dict],
    legacy_lookup: Dict[str, Dict],
    mode: str,
    market: str,
) -> Dict:
    wins   = [t for t in executed if t.get("is_win")]
    losses = [t for t in executed if not t.get("is_win")]
    total  = len(executed) + len(blocked)

    gross_win  = sum(max(0.0, t.get("pnl_pts", 0.0)) for t in executed)
    gross_loss = sum(abs(min(0.0, t.get("pnl_pts", 0.0))) for t in executed)
    pf = round(gross_win / gross_loss, 3) if gross_loss > 0 else None

    avg_rr   = round(sum(t.get("planned_rr", 0.0) for t in executed) / len(executed), 3) if executed else 0.0
    avg_win  = round(sum(t.get("pnl_pts", 0.0) for t in wins)   / len(wins),   3) if wins   else 0.0
    avg_loss = round(sum(t.get("pnl_pts", 0.0) for t in losses) / len(losses), 3) if losses else 0.0

    # Max drawdown (equity curve)
    equity = 0.0; peak = 0.0; max_dd = 0.0
    for t in sorted(executed, key=lambda x: x["ts"]):
        equity += t.get("pnl_pts", 0.0)
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    reasons: Counter = Counter(_bucket(b["reason"]) for b in blocked)

    missed_winner = 0
    blocked_loser = 0
    xagusd_blocked = 0
    for b in blocked:
        if b["reason"] == _BLACKLIST_REASON:
            xagusd_blocked += 1        # count every blacklisted trade here; symbol check below
        lk = legacy_lookup.get(b["trade_key"])
        if lk:
            if lk.get("is_win"):
                missed_winner += 1
            else:
                blocked_loser += 1

    return {
        "mode":                    mode,
        "market":                  market,
        "total_signals":           total,
        "executed_trades":         len(executed),
        "blocked_trades":          len(blocked),
        "block_rate_pct":          round(len(blocked) / total * 100, 2) if total else 0.0,
        "blocked_reason_breakdown": dict(reasons),
        "win_rate":                round(len(wins) / len(executed) * 100, 2) if executed else 0.0,
        "profit_factor":           pf,
        "average_rr":              avg_rr,
        "max_drawdown":            round(max_dd, 3),
        "average_win":             avg_win,
        "average_loss":            avg_loss,
        "missed_winner_count":     missed_winner,
        "blocked_loser_count":     blocked_loser,
        "xagusd_blocked_count":    xagusd_blocked if market == "XAGUSD" else 0,
        "net_pnl":                 round(sum(t.get("pnl_pts", 0.0) for t in executed), 3),
    }


# ── Per-symbol backtest ───────────────────────────────────────────────────────
def run_symbol_backtest(
    market: str,
    ticker: str,
    df,
    revalidate_bars: int = 1,
) -> Dict:
    """
    Walk-forward bar replay.  Returns {market, ticker, legacy:{...}, safe:{...}}.
    """
    min_window = 80
    cooldown   = 0
    is_blacklisted = market in SAFE_BLACKLIST

    legacy_exec: List[Dict] = []
    safe_exec:   List[Dict] = []
    safe_blocked: List[Dict] = []

    for i in range(min_window, len(df) - 20):
        if cooldown > 0:
            cooldown -= 1
            continue

        window = df.iloc[: i + 1].copy().reset_index(drop=True)
        setup  = scan_silver_bullet(window, market, tf="5", fyers=None, force=True)
        if not setup:
            continue

        ts  = df["timestamp"].iloc[i]
        sig = setup.get("entry_signal") or {}

        entry = float(sig.get("entry",     0) or 0)
        sl    = float(sig.get("stop_loss", 0) or 0)
        t1    = float(sig.get("target1",   0) or 0)
        t2    = float(sig.get("target2",   0) or 0)
        t3    = float(sig.get("target3",   0) or 0)
        if not (entry and sl and t1 and t2 and t3):
            continue

        direction = "BUY" if setup.get("direction") == "BULLISH" else "SELL"
        trade_key = f"{market}|{ts.isoformat()}|{direction}|{round(entry, 5)}"

        outcome = simulate_trade_outcome(
            df.iloc[i:].reset_index(drop=True), 0, entry, sl, t1, t2, t3, direction
        )
        base = {
            "trade_key":  trade_key,
            "ts":         ts,
            "market":     market,
            "direction":  direction,
            "entry":      entry,
            "stop_loss":  sl,
            "target2":    t2,
            "planned_rr": float(sig.get("rr_ratio", 0) or 0),
            **outcome,
        }
        legacy_exec.append(base)

        # ── SAFE_VALIDATION_REVALIDATE_AUTO ──────────────────────────────────

        # Step 0 — instrument blacklist
        if is_blacklisted:
            safe_blocked.append({
                "trade_key": trade_key,
                "state":     "BLACKLISTED",
                "reason":    _BLACKLIST_REASON,
                "ts":        ts,
            })
            cooldown = 6
            continue

        bar_time = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        ltp_now  = float(df["close"].iloc[i])

        # Step 1 — validate at signal bar close (ref_time = bar_time → age = 0)
        sig0 = _make_signal(setup, ltp_now, bar_time)
        st0, rs0, _ = revalidate_existing(sig0, current_ltp=ltp_now, config=_EXEC_CFG, ref_time=bar_time)
        if st0 != SIGNAL_WAITING_CONFIRM:
            safe_blocked.append({"trade_key": trade_key, "state": st0, "reason": rs0, "ts": ts})
            cooldown = 6
            continue

        # Step 2 — revalidate N bars later (ref_time = bar_time keeps age = 0 in bar replay)
        j         = min(i + revalidate_bars, len(df) - 1)
        ltp_delay = float(df["close"].iloc[j])
        sig1      = _make_signal(setup, ltp_delay, bar_time)
        st1, rs1, _ = revalidate_existing(sig1, current_ltp=ltp_delay, config=_EXEC_CFG, ref_time=bar_time)

        if st1 == SIGNAL_WAITING_CONFIRM:
            safe_exec.append(dict(base))           # auto-execute — no manual gate
        else:
            safe_blocked.append({
                "trade_key": trade_key,
                "state":     st1,
                "reason":    f"REVALIDATION:{rs1}",
                "ts":        ts,
            })
        cooldown = 6

    legacy_lookup = {t["trade_key"]: t for t in legacy_exec}
    legacy = _build_stats(legacy_exec, [],           legacy_lookup, "LEGACY",                        market)
    safe   = _build_stats(safe_exec,   safe_blocked, legacy_lookup, "SAFE_VALIDATION_REVALIDATE_AUTO", market)
    safe["net_pnl_difference_vs_legacy"] = round(safe["net_pnl"] - legacy["net_pnl"], 3)

    return {"market": market, "ticker": ticker, "legacy": legacy, "safe": safe}


# ── Group aggregation ─────────────────────────────────────────────────────────
def _agg_mode(valid_results: List[Dict], mode_key: str) -> Dict:
    totals = dict(
        total_signals=0, executed_trades=0, blocked_trades=0,
        xagusd_blocked_count=0, missed_winner_count=0, blocked_loser_count=0,
        net_pnl=0.0, avg_rr_weighted=0.0,
    )
    all_dd: List[float] = []
    all_pf: List[float] = []
    all_wr_w: List[float] = []
    all_win: List[float] = []
    all_loss: List[float] = []
    reasons: Counter = Counter()

    for r in valid_results:
        s = r[mode_key]
        totals["total_signals"]          += s["total_signals"]
        totals["executed_trades"]        += s["executed_trades"]
        totals["blocked_trades"]         += s["blocked_trades"]
        totals["xagusd_blocked_count"]   += s.get("xagusd_blocked_count", 0)
        totals["missed_winner_count"]    += s["missed_winner_count"]
        totals["blocked_loser_count"]    += s["blocked_loser_count"]
        totals["net_pnl"]                += s["net_pnl"]
        totals["avg_rr_weighted"]        += s["average_rr"] * s["executed_trades"]
        reasons.update(s.get("blocked_reason_breakdown", {}))
        all_dd.append(s["max_drawdown"])
        if s["profit_factor"] is not None:
            all_pf.append(s["profit_factor"])
        if s["executed_trades"] > 0:
            all_wr_w.append(s["win_rate"] * s["executed_trades"])
        all_win.append(s["average_win"])
        all_loss.append(s["average_loss"])

    exec_t = totals["executed_trades"]
    return {
        "mode":                     mode_key,
        "total_signals":            totals["total_signals"],
        "executed_trades":          exec_t,
        "blocked_trades":           totals["blocked_trades"],
        "block_rate_pct":           round(totals["blocked_trades"] / totals["total_signals"] * 100, 2)
                                    if totals["total_signals"] else 0.0,
        "blocked_reason_breakdown": dict(reasons),
        "win_rate":                 round(sum(all_wr_w) / exec_t, 2) if exec_t and all_wr_w else 0.0,
        "profit_factor":            round(sum(all_pf) / len(all_pf), 3) if all_pf else None,
        "average_rr":               round(totals["avg_rr_weighted"] / exec_t, 3) if exec_t else 0.0,
        "max_drawdown":             round(max(all_dd), 3) if all_dd else 0.0,
        "average_win":              round(sum(all_win) / len(all_win), 3) if all_win else 0.0,
        "average_loss":             round(sum(all_loss) / len(all_loss), 3) if all_loss else 0.0,
        "missed_winner_count":      totals["missed_winner_count"],
        "blocked_loser_count":      totals["blocked_loser_count"],
        "xagusd_blocked_count":     totals["xagusd_blocked_count"],
        "net_pnl":                  round(totals["net_pnl"], 3),
    }


def summarize_group(results: List[Dict]) -> Dict:
    valid = [r for r in results if "legacy" in r]
    failed = [r for r in results if "error" in r and "legacy" not in r]

    if not valid:
        return {
            "group": "FOREX", "verdict": "NO_DATA",
            "markets_covered": [], "markets_failed": failed,
        }

    lg  = _agg_mode(valid, "legacy")
    saf = _agg_mode(valid, "safe")
    saf["net_pnl_difference_vs_legacy"] = round(saf["net_pnl"] - lg["net_pnl"], 3)

    # ── Pass/fail assessment ──────────────────────────────────────────────────
    xagusd_result = next((r for r in valid if r["market"] == "XAGUSD"), None)
    xagusd_exec   = xagusd_result["safe"]["executed_trades"] if xagusd_result else 0

    criteria = {
        "safe_pf_gt_legacy":               (saf["profit_factor"] or 0) > (lg["profit_factor"] or 0),
        "max_dd_lte_legacy":               saf["max_drawdown"] <= lg["max_drawdown"],
        "blocked_losers_gt_missed_winners": saf["blocked_loser_count"] > saf["missed_winner_count"],
        "xagusd_not_executed":             xagusd_exec == 0,
        "no_crash_cme_proxy_unavailable":  True,   # script reached this point → no crash
    }
    verdict = "PASS" if all(criteria.values()) else "FAIL"

    return {
        "group":              "FOREX",
        "generated_at":       datetime.now().isoformat(),
        "days":               60,
        "markets_covered":    [r["market"] for r in valid],
        "markets_failed":     failed,
        "legacy":             lg,
        "safe_validation_revalidate_auto": saf,
        "pass_criteria":      criteria,
        "verdict":            verdict,
    }


# ── Report writer ─────────────────────────────────────────────────────────────
def _write_report(summary: Dict, market_results: List[Dict], out_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"group_summary": summary, "market_results": market_results},
            fh, indent=2, default=str,
        )


# ── CLI entry point ───────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Forex SAFE_VALIDATION_REVALIDATE_AUTO vs LEGACY backtest"
    )
    ap.add_argument("--days",  type=int, default=60,
                    help="Lookback days (yfinance 5m limited to 60d)")
    ap.add_argument("--delay", type=int, default=1,
                    help="Bars between initial signal and revalidation (default 1 = 5 min)")
    ap.add_argument("--out",   default=os.path.join("reports", "forex_revalidate_60d.json"))
    args = ap.parse_args()

    print(f"Forex SAFE_VALIDATION_REVALIDATE_AUTO backtest — {args.days}d, delay={args.delay} bar(s)")
    print(f"Blacklisted: {sorted(SAFE_BLACKLIST)}")
    print()

    results: List[Dict] = []
    for market, ticker in FOREX_MARKETS.items():
        print(f"[RUN] {market} ({ticker})")
        df = None
        try:
            df = _fetch_cme_proxy(market, ticker, args.days)
        except Exception as exc:
            print(f"  CME proxy unavailable: {exc}")

        if df is None or len(df) < 120:
            results.append({
                "market": market, "ticker": ticker,
                "error": "HISTORY_FETCH_FAILED",
                "error_details": "CME proxy (yfinance) returned insufficient data — skipped",
            })
            print(f"  -> skipped")
            continue

        try:
            r   = run_symbol_backtest(market, ticker, df, revalidate_bars=args.delay)
            leg = r["legacy"]
            saf = r["safe"]
            print(
                f"  LEGACY    : sig={leg['total_signals']} exec={leg['executed_trades']} "
                f"wr={leg['win_rate']}% pf={leg['profit_factor']} rr={leg['average_rr']} "
                f"dd={leg['max_drawdown']} pnl={leg['net_pnl']}"
            )
            print(
                f"  SAFE_AUTO : sig={saf['total_signals']} exec={saf['executed_trades']} "
                f"blocked={saf['blocked_trades']} wr={saf['win_rate']}% pf={saf['profit_factor']} "
                f"rr={saf['average_rr']} dd={saf['max_drawdown']} pnl={saf['net_pnl']}"
            )
            if saf["blocked_reason_breakdown"]:
                for reason, count in sorted(saf["blocked_reason_breakdown"].items(), key=lambda x: -x[1]):
                    print(f"    block: {reason}: {count}")
            results.append(r)
        except Exception as exc:
            results.append({"market": market, "ticker": ticker, "error": str(exc)})
            print(f"  -> failed: {exc}")

    summary = summarize_group(results)
    _write_report(summary, results, args.out)

    print(f"\nReport: {args.out}")
    print(f"Verdict: {summary.get('verdict', 'N/A')}")
    print()

    print("Pass criteria:")
    for k, v in summary.get("pass_criteria", {}).items():
        mark = "PASS" if v else "FAIL"
        print(f"  [{mark}] {k}")

    print()
    print("Group summary:")
    for mode_key in ("legacy", "safe_validation_revalidate_auto"):
        s = summary.get(mode_key, {})
        if not s:
            continue
        print(
            f"  {mode_key}:\n"
            f"    signals={s.get('total_signals')} exec={s.get('executed_trades')} "
            f"blocked={s.get('blocked_trades')} block_rate={s.get('block_rate_pct')}%\n"
            f"    wr={s.get('win_rate')}% pf={s.get('profit_factor')} "
            f"avg_rr={s.get('average_rr')} max_dd={s.get('max_drawdown')} pnl={s.get('net_pnl')}\n"
            f"    missed_winners={s.get('missed_winner_count')} "
            f"blocked_losers={s.get('blocked_loser_count')} "
            f"xagusd_blocked={s.get('xagusd_blocked_count')}"
        )
    print()
    print("NOTE: Live mode NOT enabled. Report only.")


if __name__ == "__main__":
    main()
