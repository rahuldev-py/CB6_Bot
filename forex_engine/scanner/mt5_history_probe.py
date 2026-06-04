# forex_engine/scanner/mt5_history_probe.py
#
# CB6 Quantum — MT5 History Depth Probe
# Checks how much historical data GFT/FTMO has for each forex symbol.
# Run this once MT5 terminal is open and connected.
#
# Usage:
#   python -m forex_engine.scanner.mt5_history_probe

from __future__ import annotations
import os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
_env = dotenv_values(ROOT / '.env')
for k, v in _env.items():
    if k not in os.environ:
        os.environ[k] = v

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed — pip install MetaTrader5")
    sys.exit(1)

import pandas as pd
from datetime import datetime, timezone

# ── Symbol candidates (tries each until one works) ─────────────────────────────
SYMBOL_CANDIDATES = {
    'XAUUSD': ['XAUUSD.x', 'XAUUSD', 'GOLD.x', 'GOLD', 'XAUUSDm'],
    'XAGUSD': ['XAGUSD.x', 'XAGUSD', 'SILVER.x', 'XAGUSDm'],
    'USOIL' : ['WTI.x', 'USOIL.x', 'USOIL.cash', 'USOIL', 'OIL.x', 'WTI', 'BRENT.x'],
}

TF_MAP = {
    '3m' : mt5.TIMEFRAME_M3,
    '5m' : mt5.TIMEFRAME_M5,
    '15m': mt5.TIMEFRAME_M15,
    '1h' : mt5.TIMEFRAME_H1,
    '1d' : mt5.TIMEFRAME_D1,
}

# Max bars to request — FTMO terminal reports maxbars=100,000
# Exceeding this silently returns empty; keep at 99,000 to stay under limit
MAX_BARS = 99_000


def _init_mt5() -> bool:
    path = os.environ.get('MT5_FTMO_PATH') or os.environ.get('MT5_PATH') or ''
    if path:
        ok = mt5.initialize(path=path)
    else:
        ok = mt5.initialize()
    if not ok:
        err = mt5.last_error()
        print(f"MT5 init failed: {err}")
        if err[0] == -10005:
            print("  → IPC timeout: MT5 terminal may not be open, or a previous")
            print("    connection wasn't fully closed. Restart MT5 and try again.")
        return False
    return True


def _find_symbol(label: str) -> tuple[str | None, object | None]:
    """Try symbol name candidates until one that has live data is found."""
    for sym in SYMBOL_CANDIDATES.get(label, []):
        info = mt5.symbol_info(sym)
        if info is None:
            continue
        mt5.symbol_select(sym, True)
        time.sleep(0.4)
        # Quick live-data check
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 5)
        if rates is not None and len(rates) > 0:
            return sym, info
    return None, None


def _probe_symbol(sym: str) -> dict:
    """
    Return bar counts and date ranges for each timeframe.

    MT5 only downloads history when a chart is open OR when a small seed
    request triggers a server sync.  Strategy:
      1. Seed with 10 recent bars  → wakes MT5 broker sync
      2. Wait 2s for download
      3. Request full history via date range (triggers broker download)
      4. Wait 3s more
      5. Final pull via from_pos
    """
    result = {}
    since = datetime(2015, 1, 1, tzinfo=timezone.utc)

    for tf_name, tf_const in TF_MAP.items():
        # Step 1 — seed with 10 recent bars (wakes terminal sync)
        mt5.copy_rates_from_pos(sym, tf_const, 0, 10)
        time.sleep(0.5)

        # Step 2 — date-range pull from far back (this is the reliable method)
        # copy_rates_from returns up to MAX_BARS starting from `since`
        rates = mt5.copy_rates_from(sym, tf_const, since, MAX_BARS)
        if rates is None or len(rates) == 0:
            time.sleep(1.0)
            rates = mt5.copy_rates_from(sym, tf_const, since, MAX_BARS)

        if rates is None or len(rates) == 0:
            result[tf_name] = None
            continue

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        bars  = len(df)
        first = df['time'].iloc[0]
        last  = df['time'].iloc[-1]
        days  = (last - first).days
        result[tf_name] = {
            'bars' : bars,
            'from' : first.strftime('%Y-%m-%d'),
            'to'   : last.strftime('%Y-%m-%d'),
            'days' : days,
            'years': round(days / 365, 1),
        }
    return result


def main():
    print("\n" + "=" * 62)
    print("  CB6 Quantum — MT5 History Depth Probe")
    print("=" * 62)

    if not _init_mt5():
        sys.exit(1)

    acc  = mt5.account_info()
    term = mt5.terminal_info()
    print(f"  Broker   : {acc.company}")
    print(f"  Account  : {acc.login}")
    print(f"  Terminal : {term.name}  connected={term.connected}")
    print("=" * 62 + "\n")

    all_data   = {}
    found_syms = {}

    # ── Discover symbols ────────────────────────────────────────────────────────
    for label in SYMBOL_CANDIDATES:
        sym, info = _find_symbol(label)
        if sym is None:
            print(f"  {label:<8} ✗ NOT FOUND  (tried: {SYMBOL_CANDIDATES[label]})")
            continue

        print(f"  {label:<8} ✓ {sym:<16}  "
              f"bid={info.bid:.3f}  spread={info.ask - info.bid:.4f}  "
              f"contract={info.trade_contract_size}  digits={info.digits}")
        found_syms[label] = sym

    if not found_syms:
        print("\nNo symbols found. Is the MT5 terminal open and logged in?")
        mt5.shutdown()
        sys.exit(1)

    # ── Probe each found symbol ─────────────────────────────────────────────────
    print()
    print(f"  {'Symbol':<8} {'TF':<5}  {'Bars':>8}  {'From':<12}  {'To':<12}  {'Days':>5}  Period")
    print("  " + "-" * 60)

    for label, sym in found_syms.items():
        print(f"\n  [{label} — {sym}]")
        data = _probe_symbol(sym)
        all_data[label] = {'mt5_sym': sym, 'tfs': data}

        for tf_name, d in data.items():
            if d is None:
                print(f"  {'':<8} {tf_name:<5}  {'NO DATA':>8}")
            else:
                yr = d['days'] // 365
                mo = (d['days'] % 365) // 30
                period = f"{yr}yr {mo}mo" if yr else f"{mo}mo {d['days']%30}d"
                print(f"  {'':<8} {tf_name:<5}  {d['bars']:>8,}  "
                      f"{d['from']:<12}  {d['to']:<12}  {d['days']:>5}  {period}")

    mt5.shutdown()

    # ── Recommendation ──────────────────────────────────────────────────────────
    print()
    print("=" * 62)
    print("  BACKTEST RECOMMENDATION")
    print("=" * 62)

    for label, info in all_data.items():
        d3  = info['tfs'].get('3m')
        d15 = info['tfs'].get('15m')
        best_tf, best = ('3m', d3) if d3 else ('15m', d15) if d15 else (None, None)

        if best is None:
            print(f"  {label}: ✗ No usable data")
            continue

        days = best['days']
        if days >= 365:
            rec = f"✅ Run full backtest on {best_tf}  ({best['years']}yr)"
        elif days >= 180:
            rec = f"⚠️  Run {days}d backtest on {best_tf}  (limited but useful)"
        elif days >= 60:
            rec = f"⚠️  Only {days}d on {best_tf}  — results indicative only"
        else:
            rec = f"✗  Only {days}d — too short for reliable backtest"

        print(f"  {label:<8} {rec}  |  {best['bars']:,} bars  "
              f"{best['from']} → {best['to']}")

    print()
    print("  Run backtest with:")
    print("    python _bt_forex_mt5.py")
    print("=" * 62 + "\n")


if __name__ == '__main__':
    main()
