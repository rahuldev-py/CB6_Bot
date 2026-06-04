"""
CB6 Quantum — OI Archive Runner
Fetches live NSE option chain snapshots and stores them in data/cb6_trades.db.

Run from project root:
  python archive_oi.py                    # snapshot all 4 indices now
  python archive_oi.py --symbol NIFTY     # one symbol only
  python archive_oi.py --catalog          # show what's stored
  python archive_oi.py --levels NIFTY     # show current max-OI support/resistance

Design: run AFTER market close (15:30+ IST) when the NSE bot has stopped.
TrueData WebSocket allows only one connection per account — running this
while main.py is active will fall through to Sensibull, which may return
empty data if Sensibull cache is stale. Best practice: run at 15:45 IST daily.
"""

import sys
import os
import argparse
from datetime import datetime, date, timedelta
from pathlib import Path
import pytz

sys.path.insert(0, str(Path(__file__).parent))

from utils.oi_archive import (
    save_oi_snapshot, save_option_chain,
    get_max_oi_strikes, get_pcr_history, oi_catalog,
)
from utils.logger import logger

IST = pytz.timezone("Asia/Kolkata")

# Symbols and their approximate lot step for ATM rounding
NSE_SYMBOLS = {
    "NIFTY":      50,
    "BANKNIFTY":  100,
    "FINNIFTY":   50,
    "MIDCPNIFTY": 25,
}

# Fyers → TrueData symbol map for spot price lookup
FYERS_SYMBOL_MAP = {
    "NIFTY":      "NSE:NIFTY50-INDEX",
    "BANKNIFTY":  "NSE:NIFTYBANK-INDEX",
    "FINNIFTY":   "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
}


# ---------------------------------------------------------------------------
# Expiry helpers
# ---------------------------------------------------------------------------

def _nearest_thursday(ref: date = None) -> date:
    """Nearest upcoming Thursday (NSE weekly expiry for NIFTY/BANKNIFTY)."""
    d = ref or date.today()
    days_ahead = (3 - d.weekday()) % 7   # Thursday = weekday 3
    if days_ahead == 0 and datetime.now(IST).hour >= 15:
        days_ahead = 7  # today's expiry has passed — use next week
    return d + timedelta(days=days_ahead)


def _nearest_tuesday(ref: date = None) -> date:
    """Nearest upcoming Tuesday (NSE weekly expiry for FINNIFTY)."""
    d = ref or date.today()
    days_ahead = (1 - d.weekday()) % 7
    if days_ahead == 0 and datetime.now(IST).hour >= 15:
        days_ahead = 7
    return d + timedelta(days=days_ahead)


def _get_expiry(symbol: str) -> date:
    if symbol == "FINNIFTY":
        return _nearest_tuesday()
    return _nearest_thursday()


# ---------------------------------------------------------------------------
# Spot price from archive (latest close)
# ---------------------------------------------------------------------------

def _get_spot(symbol: str) -> float:
    """Get most recent close price from the OHLCV archive."""
    try:
        from utils.ohlcv_archive import get_candles
        fyers_sym = FYERS_SYMBOL_MAP.get(symbol)
        if not fyers_sym:
            return 0.0
        df = get_candles("NSE", fyers_sym, "15m", limit=1)
        if df.empty:
            df = get_candles("NSE", fyers_sym, "1h", limit=1)
        if df.empty:
            return 0.0
        return float(df.iloc[-1]["close"])
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Main snapshot logic
# ---------------------------------------------------------------------------

def snapshot_symbol(symbol: str, strikes: int = 5) -> dict:
    """
    Fetch option chain for one symbol and store snapshot + per-strike data.
    Returns summary dict.
    """
    from nse_options.option_chain_fetcher import fetch_option_chain_context

    expiry   = _get_expiry(symbol)
    spot     = _get_spot(symbol)
    ts_ist   = datetime.now(IST).strftime("%Y-%m-%dT%H:%M:00")

    ctx = fetch_option_chain_context(
        symbol=symbol,
        expiry=expiry,
        strikes_around_atm=strikes,
        spot=spot,
    )

    if not ctx.get("data_available"):
        return {"symbol": symbol, "ok": False, "reason": "no data"}

    ok_snap   = save_oi_snapshot(symbol, ts_ist, expiry.isoformat(), ctx, spot)
    chain_n   = save_option_chain(symbol, ts_ist, expiry.isoformat(), ctx)

    from nse_options.option_pressure_engine import calculate_option_pressure
    pressure = calculate_option_pressure(ctx)

    return {
        "symbol":      symbol,
        "ok":          ok_snap,
        "ts":          ts_ist,
        "expiry":      expiry.isoformat(),
        "spot":        spot,
        "atm":         ctx.get("atm"),
        "pcr_oi":      pressure.get("pcr_oi"),
        "option_bias": pressure.get("option_bias"),
        "ce_oi":       pressure.get("ce_oi"),
        "pe_oi":       pressure.get("pe_oi"),
        "chain_rows":  chain_n,
        "source":      ctx.get("source"),
    }


def snapshot_all(symbols: list[str] = None, strikes: int = 5) -> list[dict]:
    """Snapshot all (or specified) symbols. Returns list of result dicts."""
    targets = symbols or list(NSE_SYMBOLS.keys())
    results = []
    for sym in targets:
        try:
            r = snapshot_symbol(sym, strikes)
            results.append(r)
        except Exception as e:
            results.append({"symbol": sym, "ok": False, "reason": str(e)})
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CB6 OI Archive")
    parser.add_argument("--symbol",  help="Single symbol to snapshot (e.g. NIFTY)")
    parser.add_argument("--strikes", type=int, default=5,
                        help="Strikes around ATM to fetch (default 5)")
    parser.add_argument("--catalog", action="store_true", help="Show OI catalog and exit")
    parser.add_argument("--levels",  help="Show max-OI support/resistance for a symbol")
    parser.add_argument("--pcr",     help="Show PCR history for a symbol")
    args = parser.parse_args()

    if args.catalog:
        rows = oi_catalog()
        if not rows:
            print("OI archive is empty — run without --catalog during market hours.")
            return
        print(f"\n{'Symbol':<15} {'Snapshots':>10}  {'Oldest':<22} {'Newest'}")
        print("-" * 65)
        for r in rows:
            print(f"{r['symbol']:<15} {r['snapshots']:>10}  {r['oldest']:<22} {r['newest']}")
        return

    if args.levels:
        r = get_max_oi_strikes(args.levels.upper())
        if not r:
            print(f"No OI data for {args.levels}")
            return
        print(f"\n=== Max OI Levels — {r['symbol']} (expiry {r['expiry']}) ===")
        print(f"  As of      : {r['as_of']}")
        print(f"  ATM        : {r['atm']}")
        print(f"  PCR OI     : {r['pcr_oi']}")
        print(f"  Bias       : {r['option_bias']}")
        print(f"  Resistance : {r['max_ce_strike']} (CE OI {r['max_ce_oi']:,.0f})" if r.get('max_ce_strike') else "  Resistance : N/A")
        print(f"  Support    : {r['max_pe_strike']} (PE OI {r['max_pe_oi']:,.0f})" if r.get('max_pe_strike') else "  Support    : N/A")
        return

    if args.pcr:
        df = get_pcr_history(args.pcr.upper(), limit=20)
        if df.empty:
            print(f"No PCR history for {args.pcr}")
            return
        print(f"\n{'Timestamp':<22} {'PCR OI':>8}  {'Bias':<10} {'ATM':>7}")
        print("-" * 52)
        for _, row in df.iterrows():
            print(f"{str(row['ts']):<22} {row['pcr_oi'] or 0:>8.3f}  {row['option_bias'] or 'N/A':<10} {int(row['atm_strike'] or 0):>7}")
        return

    # Default: snapshot
    targets = [args.symbol.upper()] if args.symbol else None
    print(f"CB6 OI Snapshot — {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    print()

    results = snapshot_all(targets, args.strikes)
    total_rows = 0

    for r in results:
        if r["ok"]:
            print(f"  {r['symbol']:<12} spot={r['spot']:.1f}  ATM={r['atm']}  "
                  f"PCR={r['pcr_oi']:.3f}  bias={r['option_bias']}  "
                  f"chain={r['chain_rows']} strikes  [{r['source']}]")
            total_rows += r.get("chain_rows", 0)
        else:
            print(f"  {r['symbol']:<12} SKIPPED — {r.get('reason', 'unknown')}")

    print()
    print(f"Done. {total_rows} chain rows stored.")
    print("Run with --catalog to inspect history, --levels NIFTY to see support/resistance.")


if __name__ == "__main__":
    main()
