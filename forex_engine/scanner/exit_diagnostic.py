# forex_engine/scanner/exit_diagnostic.py
#
# CB6 Quantum — Forex Exit Diagnostic
#
# Post-trade analysis tool derived from Dukascopy 2023-2026 backtest findings.
# Primary use: detect TIMEOUT failure mode and classify root cause per symbol.
#
# ── What the backtest actually showed ────────────────────────────────────────────
# XAUUSD: 1%  timeout, avg r at timeout = +0.46  → targets are well-calibrated
# XAGUSD: 94% timeout, avg r at timeout = -0.15  → price goes AGAINST the trade
# USOIL:  92% timeout, avg r at timeout = -0.11  → same structural failure
#
# IMPORTANT: For Silver/Oil the problem is NOT "targets too far".
# Price never approaches T1 at all (only 15-34% of timeouts are r>0).
# Root cause: ICT 3m Silver Bullet does not generate sustained momentum in these
# instruments within a session window. No target compression can fix this.
#
# Output written to: ml/backtest_results/forex/<symbol>_<date>_diagnostic.json
#
# Usage (standalone):
#   python -m forex_engine.scanner.exit_diagnostic --log ml/training_data/bt_forex_2023_2026.csv
#   python -m forex_engine.scanner.exit_diagnostic --log ml/training_data/bt_forex_2023_2026.csv --symbol XAUUSD
#
# Usage (programmatic):
#   from forex_engine.scanner.exit_diagnostic import run_exit_diagnostic
#   report = run_exit_diagnostic('ml/training_data/bt_forex_2023_2026.csv', symbol_filter='XAUUSD')

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / 'ml' / 'backtest_results' / 'forex'

# ── Column name aliases (backtest CSV uses these exact names) ───────────────────
# outcome : TIMEOUT | T1 | T2 | T3 | SL
# r       : actual R achieved at exit (proxy for MFE on TIMEOUT rows)
# hold_mins: minutes in trade
_OUTCOME_COL_CANDIDATES = ['outcome', 'exit_reason', 'ExitReason', 'exit_type']
_R_COL_CANDIDATES       = ['r', 'r_actual', 'rr_actual']
_HOLD_COL_CANDIDATES    = ['hold_mins', 'hold_minutes', 'hold_bars', 'duration_min']
_SYMBOL_COL_CANDIDATES  = ['symbol', 'Symbol']

# Strings that indicate a timeout exit regardless of column name
_TIMEOUT_STRINGS = ['timeout', 'TIMEOUT', 'EOS', 'eos', 'session_end', 'MAX_HOLD']


def _find_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


def run_exit_diagnostic(
    trade_log_path: str,
    symbol_filter: Optional[str] = None,
    save_report: bool = True,
) -> dict:
    """
    Analyse a forex backtest trade log CSV for TIMEOUT failure patterns.

    Parameters
    ----------
    trade_log_path : str
        Path to CSV file with columns: symbol, outcome, r, hold_mins.
        (Column names are auto-detected from known aliases.)
    symbol_filter : str, optional
        Restrict analysis to this symbol (e.g. 'XAUUSD').
    save_report : bool
        Write JSON report to ml/backtest_results/forex/.

    Returns
    -------
    dict with keys: symbol, total_trades, timeout_count, timeout_pct,
                    avg_r_on_timeout, pct_timeout_positive_r, action,
                    recommendation, be_trigger_r, compressed_t1_factor.
    """
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError("pandas required: pip install pandas")

    df = pd.read_csv(trade_log_path)

    # ── Column detection ────────────────────────────────────────────────────────
    sym_col     = _find_col(df, _SYMBOL_COL_CANDIDATES)
    outcome_col = _find_col(df, _OUTCOME_COL_CANDIDATES)
    r_col       = _find_col(df, _R_COL_CANDIDATES)
    hold_col    = _find_col(df, _HOLD_COL_CANDIDATES)

    if outcome_col is None:
        return {
            'action': 'MISSING_COLUMN',
            'reason': f'No outcome/exit_reason column found. Got: {list(df.columns)}',
        }

    # ── Symbol filter ───────────────────────────────────────────────────────────
    if symbol_filter and sym_col:
        df = df[df[sym_col].str.upper() == symbol_filter.upper()].copy()

    if df.empty:
        return {'action': 'NO_DATA', 'reason': f'No trades for {symbol_filter}'}

    symbol_label = symbol_filter or 'ALL'
    total        = len(df)

    # ── Timeout cohort ──────────────────────────────────────────────────────────
    # Match any of the known timeout string patterns
    timeout_mask = df[outcome_col].astype(str).str.upper().isin(
        [s.upper() for s in _TIMEOUT_STRINGS]
    )
    timeout_trades = df[timeout_mask]
    timeout_count  = len(timeout_trades)
    timeout_pct    = round(timeout_count / total * 100, 1) if total > 0 else 0.0

    # ── R distribution on timeout trades ───────────────────────────────────────
    # r > 0 = price moved favorably but timed out before target
    # r < 0 = price moved against trade direction entirely
    avg_r_on_timeout      = 0.0
    pct_timeout_positive  = 0.0
    avg_r_positive_timeout = 0.0
    avg_hold_mins         = 0.0

    if r_col and not timeout_trades.empty:
        avg_r_on_timeout     = round(float(timeout_trades[r_col].mean()), 3)
        pos_mask             = timeout_trades[r_col] > 0
        pos_count            = pos_mask.sum()
        pct_timeout_positive = round(pos_count / timeout_count * 100, 1)
        if pos_count > 0:
            avg_r_positive_timeout = round(float(timeout_trades.loc[pos_mask, r_col].mean()), 3)

    if hold_col and not timeout_trades.empty:
        avg_hold_mins = round(float(timeout_trades[hold_col].mean()), 1)

    # ── Win rate on non-timeout trades ─────────────────────────────────────────
    non_timeout = df[~timeout_mask]
    nt_wins     = non_timeout[non_timeout[outcome_col].astype(str).str.upper().isin(
        ['T1', 'T2', 'T3'])] if not non_timeout.empty else df.iloc[0:0]
    non_timeout_wr = round(len(nt_wins) / len(non_timeout) * 100, 1) if len(non_timeout) > 0 else 0.0

    # ── Decision logic ──────────────────────────────────────────────────────────
    #
    # STRUCTURAL FAILURE (disable symbol):
    #   Timeout >= 70% AND avg_r_on_timeout < 0
    #   → Price moves against the trade. No target adjustment can fix this.
    #   → ICT 3m Silver Bullet does not create momentum in this instrument.
    #
    # TARGET DISTANCE FAILURE (compress T1, add break-even):
    #   Timeout >= 40% AND avg_r_on_timeout >= 0.5
    #   → Price reaches ≥0.5R before reversing. Compressing T1 + BE at +0.5R captures it.
    #   → Typically: targets too wide for intraday expansion speed.
    #
    # MILD ISSUE (tighten T1 slightly):
    #   Timeout 20-40%
    #
    # HEALTHY:
    #   Timeout < 20%
    #
    action                = 'OK'
    recommendation        = ''
    be_trigger_r          = 0.0
    compressed_t1_factor  = 0.15   # default ATR factor

    if timeout_pct >= 70.0 and avg_r_on_timeout < 0.0:
        action = 'SKIP_SYMBOL'
        recommendation = (
            f"{symbol_label}: {timeout_pct:.0f}% timeout rate, avg r at timeout = "
            f"{avg_r_on_timeout:+.3f}R (price moves AGAINST the trade). "
            f"Only {pct_timeout_positive:.0f}% of timeouts ever turned positive. "
            f"This is a structural incompatibility — ICT 3m Silver Bullet does not "
            f"generate sustained momentum in this instrument. "
            f"No target compression can fix a negative MFE. Disable permanently."
        )

    elif timeout_pct >= 40.0 and avg_r_on_timeout >= 0.5:
        action        = 'ADD_BE_COMPRESS_T1'
        be_trigger_r  = round(avg_r_on_timeout * 0.70, 2)   # BE at 70% of avg excursion
        # Compress T1 to capture the excursion before reversal
        compressed_t1_factor = round(min(avg_r_positive_timeout * 0.80, 0.25), 3)
        recommendation = (
            f"{symbol_label}: {timeout_pct:.0f}% timeout rate, avg r at timeout = "
            f"+{avg_r_on_timeout:.3f}R. Price reaches target zone but reverses. "
            f"Add break-even at +{be_trigger_r}R. "
            f"Compress atr_t1_factor to {compressed_t1_factor} (from 0.15). "
            f"Expected improvement: capture ~{avg_r_positive_timeout*0.7:.2f}R per timeout trade."
        )

    elif timeout_pct >= 20.0:
        action = 'TIGHTEN_T1'
        recommendation = (
            f"{symbol_label}: {timeout_pct:.0f}% timeout rate. "
            f"Consider reducing atr_t1_factor from 0.15 → 0.12. "
            f"Avg r at timeout: {avg_r_on_timeout:+.3f}R. "
            f"Non-timeout win rate: {non_timeout_wr:.0f}%."
        )

    else:
        recommendation = (
            f"{symbol_label}: timeout rate {timeout_pct:.0f}% — target sizing is healthy. "
            f"Non-timeout win rate: {non_timeout_wr:.0f}%. No action needed."
        )

    report = {
        'symbol'                   : symbol_label,
        'generated_at'             : datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'trade_log'                : str(trade_log_path),
        'total_trades'             : total,
        'timeout_count'            : timeout_count,
        'timeout_pct'              : timeout_pct,
        'avg_r_on_timeout'         : avg_r_on_timeout,
        'pct_timeout_positive_r'   : pct_timeout_positive,
        'avg_r_positive_timeout'   : avg_r_positive_timeout,
        'avg_hold_mins'            : avg_hold_mins,
        'non_timeout_win_rate_pct' : non_timeout_wr,
        'action'                   : action,
        'recommendation'           : recommendation,
        'be_trigger_r'             : be_trigger_r,
        'compressed_t1_factor'     : compressed_t1_factor,
    }

    if save_report:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')
        sym_slug = (symbol_filter or 'all').lower()
        out_path = OUTPUT_DIR / f'{sym_slug}_{date_str}_diagnostic.json'
        with open(out_path, 'w') as fh:
            json.dump(report, fh, indent=2)
        print(f"[exit_diagnostic] Report saved → {out_path}")

    # ── Console output ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  CB6 Quantum — Exit Diagnostic: {symbol_label}")
    print(f"{'='*60}")
    print(f"  Total trades   : {total}")
    print(f"  Timeouts       : {timeout_count}  ({timeout_pct:.0f}%)")
    print(f"  Avg r@timeout  : {avg_r_on_timeout:+.3f}R  "
          f"(positive: {pct_timeout_positive:.0f}%, avg={avg_r_positive_timeout:+.3f}R)")
    print(f"  Avg hold (min) : {avg_hold_mins:.0f}")
    print(f"  Non-TO WR      : {non_timeout_wr:.0f}%")
    print(f"  Action         : {action}")
    print(f"  → {recommendation}")
    print(f"{'='*60}\n")

    return report


def run_all_symbols(trade_log_path: str) -> list:
    """Run diagnostic for each symbol found in the trade log."""
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError("pandas required: pip install pandas")

    df      = pd.read_csv(trade_log_path)
    sym_col = _find_col(df, _SYMBOL_COL_CANDIDATES)
    if sym_col is None:
        print("[exit_diagnostic] No symbol column found — running on full dataset")
        return [run_exit_diagnostic(trade_log_path, symbol_filter=None, save_report=True)]

    symbols = df[sym_col].str.upper().unique().tolist()
    reports = []
    for sym in sorted(symbols):
        r = run_exit_diagnostic(trade_log_path, symbol_filter=sym, save_report=True)
        reports.append(r)
    return reports


# ── CLI entry point ─────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        description='CB6 Quantum — Forex Exit Diagnostic (TIMEOUT failure analyser)'
    )
    parser.add_argument('--log', required=True,
                        help='Path to backtest trade log CSV (e.g. ml/training_data/bt_forex_2023_2026.csv)')
    parser.add_argument('--symbol', default=None,
                        help='Restrict to one symbol (e.g. XAUUSD). Default: all symbols.')
    parser.add_argument('--no-save', action='store_true',
                        help='Skip writing JSON report to disk')
    args = parser.parse_args()

    if args.symbol:
        run_exit_diagnostic(args.log, symbol_filter=args.symbol,
                            save_report=not args.no_save)
    else:
        run_all_symbols(args.log)


if __name__ == '__main__':
    _cli()
