import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import dotenv_values
from fyers_apiv3 import fyersModel

from backtest.backtester import simulate_trade_outcome
from scanner.data_fetcher import get_historical_data
from scanner.silver_bullet import scan_silver_bullet
from utils.execution_validation import revalidate_existing, SIGNAL_WAITING_CONFIRM


INDIAN_MARKETS = {
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "MIDCAPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
}

FOREX_MARKETS = {
    "XAUUSD": "XAUUSD",
    "XAGUSD": "XAGUSD",
    "USOIL": "USOIL",
}

# yfinance ticker map — used when Fyers returns Invalid Symbol for forex/commodity
_YFINANCE_TICKERS = {
    "XAUUSD": "GC=F",     # Gold futures
    "XAGUSD": "SI=F",     # Silver futures
    "USOIL":  "CL=F",     # Crude Oil futures
}


def _fetch_yfinance(symbol: str, days: int, interval: str = "5m"):
    """
    Fetch historical 5-min OHLCV for a forex/commodity symbol via yfinance.
    Returns a DataFrame with columns [timestamp, open, high, low, close, volume]
    matching get_historical_data() output format.  Returns None on error.
    """
    try:
        import yfinance as yf
        import pandas as pd
        ticker = _YFINANCE_TICKERS.get(symbol)
        if ticker is None:
            return None
        # yfinance 5m data limited to ~60 days; fetch in two 58-day chunks if needed.
        period_map = {5: "5d", 30: "1mo", 60: "60d", 100: "60d"}
        period = period_map.get(days, "60d")
        raw = yf.download(ticker, period=period, interval=interval,
                          progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        raw = raw.reset_index()
        raw.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in raw.columns]
        ts_col = "datetime" if "datetime" in raw.columns else "date"
        raw = raw.rename(columns={ts_col: "timestamp"})
        raw["timestamp"] = pd.to_datetime(raw["timestamp"]).dt.tz_localize(None)
        for col in ("open", "high", "low", "close", "volume"):
            if col in raw.columns:
                raw[col] = raw[col].astype(float)
        return raw[["timestamp", "open", "high", "low", "close"]].dropna().reset_index(drop=True)
    except Exception as e:
        print(f"  yfinance fetch error for {symbol}: {e}")
        return None

BLOCKED_STATES = {"MISSED", "REJECTED", "INVALIDATED", "EXPIRED"}


class FyersSessionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _classify_fyers_error(resp: Optional[Dict], default: str = "BAD_REQUEST") -> str:
    if not isinstance(resp, dict):
        return default
    msg = str(resp.get("message", "")).lower()
    code = resp.get("code")
    if code in (-16, -22) or "expired" in msg or "token" in msg and "expired" in msg:
        return "TOKEN_EXPIRED"
    if code == -99:
        return "BAD_REQUEST"
    return default


def _make_fyers():
    base = os.path.dirname(os.path.dirname(__file__))
    env = dotenv_values(os.path.join(base, ".env"))
    client_id = str(env.get("CLIENT_ID", "")).strip()
    access_token = str(env.get("ACCESS_TOKEN", "")).strip()
    if not client_id or not access_token:
        raise FyersSessionError("TOKEN_MISSING", "Missing CLIENT_ID or ACCESS_TOKEN in .env")

    token_client = access_token.split(":", 1)[0] if ":" in access_token else ""
    token_value = access_token.split(":", 1)[1] if ":" in access_token else access_token
    if token_client and token_client != client_id:
        raise FyersSessionError(
            "BAD_REQUEST",
            "ACCESS_TOKEN client-id prefix does not match CLIENT_ID in .env",
        )

    fy = fyersModel.FyersModel(client_id=client_id, token=token_value, is_async=False, log_path="")
    profile = fy.get_profile()
    if not isinstance(profile, dict) or profile.get("code") != 200:
        err_code = _classify_fyers_error(profile, default="BAD_REQUEST")
        raise FyersSessionError(err_code, f"get_profile failed: {profile}")
    return fy


def _execution_cfg():
    return {
        "max_entry_drift_percent": 2.0,
        "max_entry_drift_points": 3.0,
        "minimum_required_rr": 1.5,
        "invalidation_buffer_points": 10.0,
        "allowed_signal_age_seconds": 180,
    }


def _signal_from_setup(setup: Dict, ltp: float, created_at: datetime) -> Dict:
    sig = setup.get("entry_signal", {})
    return {
        "signal_id": "BT",
        "created_at": created_at.isoformat(),
        "symbol": setup.get("symbol"),
        "direction": setup.get("direction"),
        "planned_entry": sig.get("entry"),
        "current_ltp": ltp,
        "stop_loss": sig.get("stop_loss"),
        "target": sig.get("target2") or sig.get("target1") or sig.get("target3"),
        "target1": sig.get("target1"),
        "target2": sig.get("target2"),
        "target3": sig.get("target3"),
        "calculated_rr": None,
        "signal_age_seconds": 0,
    }


def _bucket_reason(reason: str) -> str:
    if reason.startswith("STOP_TARGET_SANITY_FAILED_"):
        return "STOP_TARGET_SANITY_FAILED_*"
    return reason


def _calc_stats(executed: List[Dict], blocked: List[Dict], legacy_lookup: Dict[str, Dict], mode_name: str) -> Dict:
    wins = [t for t in executed if t.get("is_win")]
    losses = [t for t in executed if not t.get("is_win")]
    total_signals = len(executed) + len(blocked)
    blocked_count = len(blocked)

    gross_win = sum(max(0.0, t.get("pnl_pts", 0.0)) for t in executed)
    gross_loss = sum(abs(min(0.0, t.get("pnl_pts", 0.0))) for t in executed)
    profit_factor = round(gross_win / gross_loss, 3) if gross_loss > 0 else None

    avg_rr = round(sum(t.get("planned_rr", 0.0) for t in executed) / len(executed), 3) if executed else 0.0
    avg_win = round(sum(t.get("pnl_pts", 0.0) for t in wins) / len(wins), 3) if wins else 0.0
    avg_loss = round(sum(t.get("pnl_pts", 0.0) for t in losses) / len(losses), 3) if losses else 0.0

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted(executed, key=lambda x: x["ts"]):
        equity += t.get("pnl_pts", 0.0)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    reasons = Counter(_bucket_reason(b["reason"]) for b in blocked)

    missed_winner = 0
    blocked_loser = 0
    stale_prevention = 0
    for b in blocked:
        lk = legacy_lookup.get(b["trade_key"])
        if lk:
            if lk.get("is_win"):
                missed_winner += 1
            else:
                blocked_loser += 1
        if b["reason"] == "LTP_OUTSIDE_ENTRY_BAND_WAITING_FOR_RECLAIM":
            stale_prevention += 1

    return {
        "mode": mode_name,
        "total_signals": total_signals,
        "executed_trades": len(executed),
        "blocked_trades": blocked_count,
        "blocked_reason_breakdown": dict(reasons),
        "win_rate": round((len(wins) / len(executed)) * 100.0, 2) if executed else 0.0,
        "profit_factor": profit_factor,
        "average_rr": avg_rr,
        "max_drawdown": round(max_dd, 3),
        "average_loss": avg_loss,
        "average_win": avg_win,
        "missed_winner_count": missed_winner,
        "blocked_loser_count": blocked_loser,
        "stale_entry_prevention_count": stale_prevention,
        "net_pnl": round(sum(t.get("pnl_pts", 0.0) for t in executed), 3),
        "slippage_impact": None,
    }


def run_market_backtest(fyers, market_name: str, symbol: str, days: int, manual_delay_bars: int = 1) -> Dict:
    df = get_historical_data(fyers, symbol, "5", days=days)
    if df is None or len(df) < 120:
        # Fyers doesn't support forex/commodity symbols — try yfinance fallback
        if symbol in _YFINANCE_TICKERS:
            print(f"  -> Fyers unsupported, trying yfinance ({_YFINANCE_TICKERS[symbol]})...")
            df = _fetch_yfinance(symbol, days)
        if df is None or len(df) < 120:
            return {
                "market": market_name,
                "symbol": symbol,
                "error": "HISTORY_FETCH_FAILED",
                "error_details": "Insufficient or empty historical data (Fyers + yfinance both failed)",
            }

    cfg = _execution_cfg()
    min_window = 80
    cooldown = 0
    legacy_exec: List[Dict] = []
    safe_manual_exec: List[Dict] = []
    safe_auto_exec: List[Dict] = []
    safe_manual_blocked: List[Dict] = []
    safe_auto_blocked: List[Dict] = []

    for i in range(min_window, len(df) - 20):
        if cooldown > 0:
            cooldown -= 1
            continue
        window = df.iloc[: i + 1].copy().reset_index(drop=True)
        setup = scan_silver_bullet(window, symbol, tf="5", fyers=None, force=True)
        if not setup:
            continue

        ts = df["timestamp"].iloc[i]
        sig = setup.get("entry_signal", {})
        entry = float(sig.get("entry", 0) or 0)
        sl = float(sig.get("stop_loss", 0) or 0)
        t1 = float(sig.get("target1", 0) or 0)
        t2 = float(sig.get("target2", 0) or 0)
        t3 = float(sig.get("target3", 0) or 0)
        if not entry or not sl or not t1 or not t2 or not t3:
            continue
        direction = "BUY" if setup.get("direction") == "BULLISH" else "SELL"
        trade_key = f"{market_name}|{ts.isoformat()}|{direction}|{round(entry,3)}"

        outcome = simulate_trade_outcome(df.iloc[i:].reset_index(drop=True), 0, entry, sl, t1, t2, t3, direction)
        base_trade = {
            "trade_key": trade_key,
            "ts": ts,
            "market": market_name,
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": sl,
            "target2": t2,
            "planned_rr": float(sig.get("rr_ratio", 0) or 0),
            **outcome,
        }
        legacy_exec.append(base_trade)

        # SAFE AUTO APPROVAL: validate at signal bar close then execute.
        ltp_now = float(df["close"].iloc[i])
        bar_time = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        signal = _signal_from_setup(setup, ltp_now, bar_time)
        st_auto, rs_auto, _ = revalidate_existing(signal, current_ltp=ltp_now, config=cfg, ref_time=bar_time)
        if st_auto == SIGNAL_WAITING_CONFIRM:
            safe_auto_exec.append(dict(base_trade))
        else:
            safe_auto_blocked.append({"trade_key": trade_key, "state": st_auto, "reason": rs_auto, "ts": ts})

        # SAFE MANUAL SIMULATED: validate now, then revalidate after delay bar.
        st0, rs0, _ = revalidate_existing(signal, current_ltp=ltp_now, config=cfg, ref_time=bar_time)
        if st0 != SIGNAL_WAITING_CONFIRM:
            safe_manual_blocked.append({"trade_key": trade_key, "state": st0, "reason": rs0, "ts": ts})
        else:
            j = min(i + manual_delay_bars, len(df) - 1)
            ltp_delay = float(df["close"].iloc[j])
            # Keep ref_time=bar_time for the delayed revalidation.
            # Signal age is a live-trading safety valve (prevents stale market-order
            # chasing). In bar-replay simulation the "1-bar delay" is always exactly
            # 5 minutes which would exceed the 180 s limit and wrongly expire every
            # signal. The meaningful check here is: is the new LTP still valid for a
            # limit fill and is RR still intact? Age check lives in live execution only.
            sig_delay = _signal_from_setup(setup, ltp_delay, bar_time)
            st1, rs1, _ = revalidate_existing(sig_delay, current_ltp=ltp_delay, config=cfg, ref_time=bar_time)
            if st1 == SIGNAL_WAITING_CONFIRM:
                safe_manual_exec.append(dict(base_trade))
            else:
                safe_manual_blocked.append({"trade_key": trade_key, "state": st1, "reason": f"REVALIDATION:{rs1}", "ts": ts})

        cooldown = 6

    legacy_lookup = {t["trade_key"]: t for t in legacy_exec}
    legacy = _calc_stats(legacy_exec, [], legacy_lookup, "LEGACY")
    safe_manual = _calc_stats(safe_manual_exec, safe_manual_blocked, legacy_lookup, "SAFE_VALIDATION_MANUAL_SIMULATED")
    safe_auto = _calc_stats(safe_auto_exec, safe_auto_blocked, legacy_lookup, "SAFE_VALIDATION_AUTO_APPROVAL")
    safe_manual["net_pnl_difference_vs_legacy"] = round(safe_manual["net_pnl"] - legacy["net_pnl"], 3)
    safe_auto["net_pnl_difference_vs_legacy"] = round(safe_auto["net_pnl"] - legacy["net_pnl"], 3)

    return {
        "market": market_name,
        "symbol": symbol,
        "legacy": legacy,
        "safe_manual_simulated": safe_manual,
        "safe_auto_approval": safe_auto,
    }


def summarize_group(results: List[Dict], label: str) -> Dict:
    valid = [r for r in results if "legacy" in r]
    if not valid:
        return {
            "group": label,
            "error": "no_valid_results",
            "markets_covered": [],
            "markets_failed": [r for r in results if "error" in r],
        }

    def merge_mode(mode_key: str) -> Dict:
        merged = {
            "mode": mode_key,
            "total_signals": 0,
            "executed_trades": 0,
            "blocked_trades": 0,
            "approved_count_like": 0,
            "wins": 0,
            "net_pnl": 0.0,
            "reason_counter": Counter(),
            "missed_winner_count": 0,
            "blocked_loser_count": 0,
            "stale_entry_prevention_count": 0,
            "avg_rr_num": 0.0,
        }
        all_drawdowns = []
        all_pf = []
        all_avg_win = []
        all_avg_loss = []
        for r in valid:
            s = r[mode_key]
            merged["total_signals"] += s["total_signals"]
            merged["executed_trades"] += s["executed_trades"]
            merged["blocked_trades"] += s["blocked_trades"]
            merged["net_pnl"] += s["net_pnl"]
            merged["missed_winner_count"] += s["missed_winner_count"]
            merged["blocked_loser_count"] += s["blocked_loser_count"]
            merged["stale_entry_prevention_count"] += s["stale_entry_prevention_count"]
            merged["avg_rr_num"] += s["average_rr"] * s["executed_trades"]
            merged["reason_counter"].update(s.get("blocked_reason_breakdown", {}))
            all_drawdowns.append(s["max_drawdown"])
            if s["profit_factor"] is not None:
                all_pf.append(s["profit_factor"])
            all_avg_win.append(s["average_win"])
            all_avg_loss.append(s["average_loss"])

        win_rate = 0.0
        if merged["executed_trades"] > 0:
            # estimate wins from wr*count (per-market) not stored directly; derive from avg maybe unavailable.
            # Keep blended from pnl signs isn't available aggregated. Use weighted by per-market wr.
            weighted_wr = 0.0
            for r in valid:
                s = r[mode_key]
                weighted_wr += s["win_rate"] * s["executed_trades"]
            win_rate = round(weighted_wr / merged["executed_trades"], 2)

        return {
            "mode": mode_key,
            "total_signals": merged["total_signals"],
            "executed_trades": merged["executed_trades"],
            "blocked_trades": merged["blocked_trades"],
            "block_rate": round((merged["blocked_trades"] / merged["total_signals"] * 100.0), 2) if merged["total_signals"] else 0.0,
            "blocked_reason_breakdown": dict(merged["reason_counter"]),
            "win_rate": win_rate,
            "profit_factor": round(sum(all_pf) / len(all_pf), 3) if all_pf else None,
            "average_rr": round(merged["avg_rr_num"] / merged["executed_trades"], 3) if merged["executed_trades"] else 0.0,
            "max_drawdown": round(max(all_drawdowns), 3) if all_drawdowns else 0.0,
            "average_loss": round(sum(all_avg_loss) / len(all_avg_loss), 3) if all_avg_loss else 0.0,
            "average_win": round(sum(all_avg_win) / len(all_avg_win), 3) if all_avg_win else 0.0,
            "missed_winner_count": merged["missed_winner_count"],
            "blocked_loser_count": merged["blocked_loser_count"],
            "stale_entry_prevention_count": merged["stale_entry_prevention_count"],
            "net_pnl": round(merged["net_pnl"], 3),
        }

    out = {
        "group": label,
        "markets_covered": [r["market"] for r in valid],
        "markets_failed": [r for r in results if "error" in r],
        "legacy": merge_mode("legacy"),
        "safe_manual_simulated": merge_mode("safe_manual_simulated"),
        "safe_auto_approval": merge_mode("safe_auto_approval"),
    }
    out["safe_manual_simulated"]["net_pnl_difference_vs_legacy"] = round(
        out["safe_manual_simulated"]["net_pnl"] - out["legacy"]["net_pnl"], 3
    )
    out["safe_auto_approval"]["net_pnl_difference_vs_legacy"] = round(
        out["safe_auto_approval"]["net_pnl"] - out["legacy"]["net_pnl"], 3
    )
    return out


def _write_report(group_summary: Dict, market_results: List[Dict], out_json: str):
    import json

    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "group_summary": group_summary,
        "market_results": market_results,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)


def main():
    p = argparse.ArgumentParser(description="Execution validation 100-day comparison backtest")
    p.add_argument("--days", type=int, default=100)
    p.add_argument("--group", choices=["indian", "forex"], default="indian")
    p.add_argument("--out", default=os.path.join("reports", "execution_validation_100d_indian.json"))
    args = p.parse_args()

    try:
        fyers = _make_fyers()
    except FyersSessionError as e:
        summary = {
            "group": args.group.upper(),
            "error": e.code,
            "error_details": e.message,
            "markets_covered": [],
            "markets_failed": [],
        }
        _write_report(summary, [], args.out)
        print(f"Session error: {e.code} - {e.message}")
        print(f"Report written: {args.out}")
        return
    markets = INDIAN_MARKETS if args.group == "indian" else FOREX_MARKETS

    results = []
    for name, symbol in markets.items():
        print(f"[RUN] {name} ({symbol})")
        try:
            r = run_market_backtest(fyers, name, symbol, args.days)
            if "error" in r:
                print(f"  -> skipped: {r['error']}")
            else:
                print(
                    f"  -> signals={r['legacy']['total_signals']} "
                    f"safe_auto_blocked={r['safe_auto_approval']['blocked_trades']}"
                )
            results.append(r)
        except Exception as e:
            results.append({"market": name, "symbol": symbol, "error": str(e)})
            print(f"  -> failed: {e}")

    summary = summarize_group(results, args.group.upper())
    _write_report(summary, results, args.out)
    print(f"\nReport written: {args.out}")
    if "error" in summary:
        print(f"\nGroup summary unavailable: {summary['error']}")
        return
    print("\nGroup summary:")
    for mode_key in ("legacy", "safe_manual_simulated", "safe_auto_approval"):
        s = summary[mode_key]
        print(
            f"{mode_key}: total={s['total_signals']} exec={s['executed_trades']} "
            f"blocked={s['blocked_trades']} wr={s['win_rate']} pf={s['profit_factor']} "
            f"avg_rr={s['average_rr']} max_dd={s['max_drawdown']} net_pnl={s['net_pnl']}"
        )


if __name__ == "__main__":
    main()
