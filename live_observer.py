"""
CB6 Quantum — Live Market Observer
===================================
Mirrors the production 3-minute scanner exactly — same data source (TrueData),
same scanner (scan_silver_bullet), same logic — but never places a trade.

Purpose: observe how the scanner behaves on live Monday data.

Run at 09:10 IST:
    python live_observer.py

Optional: pass Fyers token for H1/H4 bias (recommended):
    python live_observer.py --with-fyers

Output:
    - Terminal: colour-coded candle summary + scanner decision chain
    - logs/live_observer_YYYYMMDD.log: full machine-readable session log
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.WARNING)
logging.getLogger("truedata_ws").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

_IST = ZoneInfo("Asia/Kolkata")

# ── terminal colours ──────────────────────────────────────────────────────────
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"

def _g(s):  return f"{_GREEN}{s}{_RESET}"
def _y(s):  return f"{_YELLOW}{s}{_RESET}"
def _r(s):  return f"{_RED}{s}{_RESET}"
def _c(s):  return f"{_CYAN}{s}{_RESET}"
def _b(s):  return f"{_BOLD}{s}{_RESET}"
def _d(s):  return f"{_DIM}{s}{_RESET}"

# ── market schedule ───────────────────────────────────────────────────────────
_MARKET_OPEN  = (9, 15)
_MARKET_CLOSE = (15, 30)
_SB_WINDOWS   = [((10, 0), (11, 0)), ((13, 30), (14, 30))]

INDICES = [
    ("NSE:NIFTY50-FUT",    "NIFTY-I",      "NIFTY   "),
    ("NSE:NIFTYBANK-FUT",  "BANKNIFTY-I",  "BNKFTY  "),
    ("NSE:FINNIFTY-FUT",   "FINNIFTY-I",   "FINNFTY "),
    ("NSE:MIDCPNIFTY-FUT", "MIDCPNIFTY-I", "MIDCAP  "),
]

SCAN_INTERVAL_S = 180   # 3 minutes


def _ist_now() -> datetime:
    return datetime.now(_IST)


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


def _in_market() -> bool:
    now = _ist_now()
    t = (now.hour, now.minute)
    return _MARKET_OPEN <= t < _MARKET_CLOSE


def _in_sb_window() -> tuple[bool, str]:
    now = _ist_now()
    t   = (now.hour, now.minute)
    for (sh, sm), (eh, em) in _SB_WINDOWS:
        if (sh, sm) <= t < (eh, em):
            return True, f"{sh:02d}:{sm:02d}–{eh:02d}:{em:02d}"
    return False, ""


def _seconds_to_market() -> float:
    now = _ist_now()
    open_dt = now.replace(hour=_MARKET_OPEN[0], minute=_MARKET_OPEN[1],
                           second=0, microsecond=0)
    if now >= open_dt:
        return 0.0
    return (open_dt - now).total_seconds()


def _next_sb_banner() -> str:
    now = _ist_now()
    t   = (now.hour, now.minute)
    for (sh, sm), _ in _SB_WINDOWS:
        if t < (sh, sm):
            return f"{sh:02d}:{sm:02d} IST"
    return "none today"


# ── logging ───────────────────────────────────────────────────────────────────
_log_path: Path | None = None
_log_file = None


def _setup_log() -> None:
    global _log_path, _log_file
    log_dir = _ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    fname = f"live_observer_{_ist_now().strftime('%Y%m%d')}.log"
    _log_path = log_dir / fname
    _log_file = open(_log_path, "a", encoding="utf-8")
    _log("SESSION_START", {"time": _ist_now().isoformat()})
    print(f"{_d('Log:')} {_log_path}")


def _log(event: str, data: dict) -> None:
    if _log_file:
        line = json.dumps({"ts": _ist_now().isoformat(), "event": event, **data})
        _log_file.write(line + "\n")
        _log_file.flush()


# ── TrueData connection ───────────────────────────────────────────────────────
_td_manager = None


def _connect_truedata() -> bool:
    global _td_manager
    print(f"\n{_b('[CONNECT]')} TrueData historical + live feed...")
    try:
        from data.truedata_feed import get_manager
        td = get_manager()
        ok_hist = td.connect_hist()
        print(f"  Historical: {'✅' if ok_hist else '❌'}")

        td_syms = [td_sym for _, td_sym, _ in INDICES]
        ok_live = td.connect_live(td_syms)
        print(f"  Live WS:    {'✅' if ok_live else '❌ (ticks unavailable; historical still works)'}")

        _td_manager = td
        return ok_hist
    except Exception as e:
        print(f"  {_r(f'TrueData connect error: {e}')}")
        return False


def _get_ltp(td_sym: str) -> float | None:
    if _td_manager is None:
        return None
    return _td_manager.get_ltp(td_sym)


# ── Fyers (optional — for H1/H4 bias) ────────────────────────────────────────
_fyers = None


def _connect_fyers() -> bool:
    global _fyers
    try:
        from dotenv import dotenv_values
        env = dotenv_values(_ROOT / ".env")
        token = env.get("ACCESS_TOKEN", "")
        if not token or ":" not in token:
            return False
        from fyers_apiv3 import fyersModel
        cid = token.split(":")[0]
        _fyers = fyersModel.FyersModel(
            client_id=cid, token=token, is_async=False, log_path=""
        )
        # Quick test
        r = _fyers.get_profile()
        if r.get("s") == "ok":
            print(f"  Fyers:      ✅ ({r.get('data', {}).get('name', 'connected')})")
            return True
        return False
    except Exception as e:
        print(f"  Fyers:      {_y(f'unavailable ({e})')}")
        return False


# ── single-index scan cycle ───────────────────────────────────────────────────
def _scan_one(fyers_sym: str, td_sym: str, label: str) -> dict:
    """
    Fetch 3-min data via TrueData, run scanner, return observation dict.
    Never places a trade.
    """
    result = {
        "symbol":    fyers_sym,
        "label":     label.strip(),
        "bars":      0,
        "last_close": None,
        "last_oi":   None,
        "ltp":       None,
        "setup":     False,
        "direction": None,
        "score":     None,
        "skip_reason": None,
        "oi_entry":  None,
        "oi_dol":    None,
        "mss_type":  None,
        "dol_type":  None,
        "entry":     None,
        "sl":        None,
        "t3":        None,
        "rr":        None,
        "regime":    None,
        "error":     None,
    }

    try:
        from scanner.data_fetcher import get_historical_data
        df = get_historical_data(_fyers, fyers_sym, "3", days=4)
        if df is None or len(df) < 30:
            result["skip_reason"] = f"insufficient data ({len(df) if df is not None else 0} bars)"
            return result

        result["bars"]       = len(df)
        result["last_close"] = float(df["close"].iloc[-1])
        result["last_oi"]    = float(df["oi"].iloc[-1]) if "oi" in df.columns else None

        ltp = _get_ltp(td_sym)
        result["ltp"] = ltp

        from scanner.silver_bullet import scan_silver_bullet
        setup = scan_silver_bullet(df, fyers_sym, tf="3",
                                    fyers=_fyers, force=True)

        if setup is None:
            result["skip_reason"] = "no setup (chain incomplete)"
            return result

        sig = setup.get("entry_signal", {})
        result.update({
            "setup":     True,
            "direction": setup.get("direction"),
            "score":     setup.get("confluence"),
            "mss_type":  setup.get("mss_type"),
            "dol_type":  setup.get("dol", {}).get("type"),
            "oi_entry":  setup.get("oi_entry_reason"),
            "oi_dol":    setup.get("oi_dol_boost", 0),
            "regime":    setup.get("regime"),
            "entry":     sig.get("entry"),
            "sl":        sig.get("stop_loss"),
            "t3":        sig.get("target3"),
            "rr":        sig.get("rr_ratio"),
        })

    except Exception as e:
        result["error"] = str(e)

    return result


# ── display helpers ───────────────────────────────────────────────────────────
_SEPARATOR = "─" * 72


def _print_candle_header(now: datetime, in_sb: bool, sb_name: str) -> None:
    sb_tag = _g(f"  ◀ SB WINDOW {sb_name}") if in_sb else _d(f"  (next SB: {_next_sb_banner()})")
    print(f"\n{_SEPARATOR}")
    print(f"{_b(_hhmm(now))} IST{sb_tag}")
    print(_SEPARATOR)


def _print_result(r: dict, verbose: bool) -> None:
    label = r["label"]
    close = r["last_close"]
    ltp   = r["ltp"]
    oi    = r["last_oi"]

    # Price line
    close_str = f"{close:,.1f}" if close else "—"
    ltp_str   = f"  LTP={ltp:,.1f}" if ltp else ""
    oi_str    = f"  OI={int(oi/1000):,}K" if oi else ""
    bars_str  = _d(f"  [{r['bars']}bars]")
    print(f"  {_b(label)}  {_c(close_str)}{ltp_str}{_d(oi_str)}{bars_str}")

    if r["error"]:
        print(f"    {_r('ERROR:')} {r['error']}")
        return

    if r["setup"]:
        direction = r["direction"] or "?"
        score     = r["score"] or 0
        mss       = r.get("mss_type", "?")
        dol       = r.get("dol_type", "?")
        oi_tag    = f"  OI-DOL+{r['oi_dol']:.0f}" if r.get("oi_dol", 0) > 0 else ""
        regime    = r.get("regime", "?")

        dir_colour = _g if direction == "BULLISH" else _r
        print(f"    {dir_colour('▶ SETUP')}  {dir_colour(direction)}  score={_b(str(score))}  "
              f"MSS={mss}  DOL={dol}  regime={regime}{oi_tag}")

        if r["entry"] and r["sl"] and r["t3"]:
            risk = abs(r["entry"] - r["sl"])
            print(f"    entry={r['entry']:,.1f}  sl={r['sl']:,.1f}  "
                  f"t3={r['t3']:,.1f}  rr={r['rr']}R  risk={risk:.1f}pts")

        oi_entry = r.get("oi_entry") or ""
        if oi_entry and oi_entry not in ("OI_SKIP", "NO_OI_PASS_THROUGH"):
            print(f"    OI-entry: {_y(oi_entry)}")

    elif verbose:
        reason = r["skip_reason"] or "filtered"
        print(f"    {_d(f'skip: {reason}')}")


def _print_summary_row(results: list[dict]) -> None:
    setups = [r for r in results if r["setup"]]
    n = len(setups)
    if n == 0:
        print(f"\n  {_d('No setups this cycle.')}")
    else:
        labels = ", ".join(r["label"] for r in setups)
        print(f"\n  {_g(f'{n} setup(s) this cycle:')} {labels}")


# ── main scan loop ────────────────────────────────────────────────────────────
def _run_scan_cycle(verbose: bool) -> list[dict]:
    now     = _ist_now()
    in_sb, sb_name = _in_sb_window()

    _print_candle_header(now, in_sb, sb_name)

    results = []
    for fyers_sym, td_sym, label in INDICES:
        r = _scan_one(fyers_sym, td_sym, label)
        results.append(r)
        # Always print in SB window; outside window only if verbose or setup
        show = in_sb or verbose or r["setup"] or r["error"]
        if show:
            _print_result(r, verbose=verbose or in_sb)

    _print_summary_row(results)

    # Log cycle
    _log("SCAN_CYCLE", {
        "in_sb": in_sb,
        "results": [
            {k: v for k, v in r.items() if k not in ("setup",)}
            for r in results
        ]
    })
    return results


# ── session summary ───────────────────────────────────────────────────────────
def _print_session_summary(all_setups: list[dict]) -> None:
    print(f"\n{'═' * 72}")
    print(_b("SESSION SUMMARY — Live Observer"))
    print(f"{'═' * 72}")
    if not all_setups:
        print("  No setups fired today.")
        return
    from collections import Counter
    by_sym   = Counter(r["label"].strip() for r in all_setups)
    by_dir   = Counter(r["direction"] for r in all_setups)
    scores   = [r["score"] for r in all_setups if r["score"]]
    print(f"  Total setups  : {len(all_setups)}")
    print(f"  By index      : {dict(by_sym)}")
    print(f"  By direction  : {dict(by_dir)}")
    if scores:
        print(f"  Score range   : {min(scores)}–{max(scores)}  avg={sum(scores)/len(scores):.1f}")
    print(f"\n  Log saved to  : {_log_path}")
    print(f"{'═' * 72}")


# ── entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="CB6 Quantum live market observer (3-minute, observation only)"
    )
    parser.add_argument("--with-fyers", action="store_true",
                        help="Load Fyers token for H1/H4 bias (run auto_token.py first)")
    parser.add_argument("--verbose",    action="store_true",
                        help="Show skip reasons outside Silver Bullet windows")
    parser.add_argument("--no-wait",    action="store_true",
                        help="Start immediately (skip market-open wait)")
    parser.add_argument("--until",      default="15:35",
                        help="Stop at HH:MM IST (default 15:35)")
    args = parser.parse_args()

    _setup_log()

    print(f"\n{'═' * 72}")
    print(_b("  CB6 Quantum — Live Market Observer"))
    print(f"  3-min candles  |  observation only  |  no trades fired")
    print(f"{'═' * 72}")
    print(f"  Date  : {_ist_now().strftime('%A %Y-%m-%d')}")
    print(f"  Engine: NSE Silver Bullet (TrueData primary)")
    print(f"  Scan  : every {SCAN_INTERVAL_S}s during market hours")
    print(f"  Until : {args.until} IST\n")

    # Parse stop time
    stop_h, stop_m = map(int, args.until.split(":"))

    # Connect data feeds
    td_ok = _connect_truedata()
    if not td_ok:
        print(_r("  TrueData connection failed — cannot proceed"))
        sys.exit(1)

    if args.with_fyers:
        print(f"\n{_b('[CONNECT]')} Fyers (H1/H4 bias)...")
        _connect_fyers()
    else:
        print(f"  {_d('Fyers: skipped (H1/H4 bias will show RANGING — run with --with-fyers to enable)')}")

    # Wait for market open
    if not args.no_wait:
        secs = _seconds_to_market()
        if secs > 0:
            open_at = _ist_now().replace(
                hour=_MARKET_OPEN[0], minute=_MARKET_OPEN[1], second=0
            )
            print(f"\n  Market opens at {_b(open_at.strftime('%H:%M IST'))}  "
                  f"({int(secs / 60)}m {int(secs % 60)}s from now)")
            print(f"  {_d('Waiting...')}  (Ctrl+C to abort)\n")
            try:
                while _seconds_to_market() > 5:
                    remaining = _seconds_to_market()
                    mins = int(remaining // 60)
                    secs_r = int(remaining % 60)
                    print(f"\r  {_c(f'{mins:02d}m {secs_r:02d}s')} until market open", end="", flush=True)
                    time.sleep(5)
                print("\r" + " " * 40 + "\r", end="")
            except KeyboardInterrupt:
                print("\n  Aborted.")
                return

    # Opening context
    now = _ist_now()
    print(f"\n  {_g('Market open')} — scanning every 3 minutes")
    print(f"  Silver Bullet windows: 10:00–11:00  and  13:30–14:30 IST")
    print(f"  OI filters: ACTIVE  |  Bid/Ask filter: ACTIVE (during SB windows)")
    print()

    all_setups: list[dict] = []
    _last_scan  = datetime.min.replace(tzinfo=_IST)

    try:
        while True:
            now = _ist_now()

            # Stop condition
            if (now.hour, now.minute) >= (stop_h, stop_m):
                print(f"\n  {_b(_hhmm(now))} — session end ({args.until} IST reached)")
                break

            if not _in_market():
                time.sleep(10)
                continue

            # Scan every SCAN_INTERVAL_S
            elapsed = (now - _last_scan).total_seconds()
            if elapsed >= SCAN_INTERVAL_S:
                results = _run_scan_cycle(verbose=args.verbose)
                all_setups.extend(r for r in results if r["setup"])
                _last_scan = now

            time.sleep(2)

    except KeyboardInterrupt:
        print(f"\n\n  {_b('Observer stopped')} (Ctrl+C)")

    _print_session_summary(all_setups)
    _log("SESSION_END", {
        "total_setups": len(all_setups),
        "setup_symbols": [r["label"].strip() for r in all_setups],
    })
    if _log_file:
        _log_file.close()


if __name__ == "__main__":
    main()
