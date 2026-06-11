#!/usr/bin/env python3
"""
backtest/no_h4_backtest_june8_11.py
CB6 Quantum — Focused backtest June 8-11 2026 with H4 completely disabled.
Runs ICT Silver Bullet scanner on NSE (NIFTY/BNIFTY/FINNIFTY/MIDCP) + Forex (XAUUSD/XAGUSD/USOIL).
H4 bias is forced to RANGING for every symbol — no direction gate, no score penalty.
Output: console table + reports/no_h4_backtest_june8_11.json
"""

from __future__ import annotations
import os, sys, json, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault('CB6_MEMORY_V1_ENABLED',    'false')
os.environ.setdefault('CB6_REGIME_V1_ENABLED',    'false')
os.environ.setdefault('CB6_SETUP_DNA_V1_ENABLED', 'false')

from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / '.env').items():
    if k not in os.environ:
        os.environ[k] = v

logging.basicConfig(level=logging.WARNING)   # silence scanner debug spam
logger = logging.getLogger('no_h4_bt')
logger.setLevel(logging.INFO)

import pandas as pd

# ── Date window ────────────────────────────────────────────────────────────────
START_DATE = datetime(2026, 6,  8, 0,  0, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 6, 11, 23, 59, tzinfo=timezone.utc)
DAYS_BACK  = 7   # extra history for scanner context (DOL/MSS need look-back)

# ── Scanner imports ─────────────────────────────────────────────────────────
from scanner.silver_bullet import scan_silver_bullet
from forex_engine.scanner.signal_scanner import scan_setup

# ─────────────────────────────────────────────────────────────────────────────
#   FOREX: MT5 data fetch
# ─────────────────────────────────────────────────────────────────────────────
FOREX_SYMBOLS = ['XAUUSD', 'XAGUSD', 'USOIL']
FOREX_TF      = '15m'   # 15-min bars

def _get_mt5_df(symbol: str, tf_str: str = '15m', bars: int = 2000) -> Optional[pd.DataFrame]:
    """
    Fetch `bars` most-recent MT5 bars (copy_rates_from_pos pos=0 = newest bar).
    Returns (df_with_utc_index, actual_symbol) or (None, symbol).
    """
    try:
        import MetaTrader5 as mt5
        TF_MAP = {'1m': mt5.TIMEFRAME_M1, '3m': mt5.TIMEFRAME_M3, '5m': mt5.TIMEFRAME_M5,
                  '15m': mt5.TIMEFRAME_M15, '30m': mt5.TIMEFRAME_M30,
                  '1h': mt5.TIMEFRAME_H1, '4h': mt5.TIMEFRAME_H4, '1d': mt5.TIMEFRAME_D1}
        tf = TF_MAP.get(tf_str, mt5.TIMEFRAME_M15)

        for s in [symbol, symbol + '.x', symbol.replace('USOIL', 'WTI') + '.x']:
            mt5.symbol_select(s, True)
            # pos=0 means start from current bar going backward; returns bars in ASC order
            rates = mt5.copy_rates_from_pos(s, tf, 0, bars)
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df['timestamp'] = pd.to_datetime(df['time'], unit='s', utc=True)
                df = df.set_index('timestamp').sort_index()
                logger.info(f"MT5 {s}: {len(df)} bars fetched  [{df.index.min()} → {df.index.max()}]")
                return df, s
        return None, symbol
    except Exception as e:
        logger.warning(f"MT5 fetch error {symbol}: {e}")
        return None, symbol


def _init_mt5(account: str = '5k') -> bool:
    try:
        import MetaTrader5 as mt5
        configs = {
            '5k': (os.getenv('MT5_TERMINAL_GFT', r'C:\CB6_MT5\MT5_GFT_5K\terminal64.exe'),
                   int(os.getenv('GFT_2STEP_LOGIN', '0')),
                   os.getenv('GFT_2STEP_PASSWORD', ''),
                   os.getenv('GFT_2STEP_SERVER', 'GoatFunded-Server3')),
            '1k': (os.getenv('GFT_1K_MT5_TERMINAL_PATH', r'C:\CB6_MT5\MT5_GFT_1K\terminal64.exe'),
                   int(os.getenv('GFT_1K_MT5_LOGIN', '0')),
                   os.getenv('GFT_1K_MT5_PASSWORD', ''),
                   os.getenv('GFT_1K_MT5_SERVER', 'GoatFunded-Server')),
        }
        path, login, pwd, server = configs.get(account, configs['5k'])
        ok = mt5.initialize(path=path, login=login, password=pwd, server=server, timeout=15000)
        if not ok:
            logger.warning(f"MT5 init failed: {mt5.last_error()}")
        return ok
    except Exception as e:
        logger.warning(f"MT5 init error: {e}")
        return False


def simulate_trade(df: pd.DataFrame, setup: dict, entry_idx: int) -> dict:
    """Walk-forward simulation. Partial exit 1/3 at T1, 1/3 at T2, 1/3 at T3."""
    sig       = setup['entry_signal']
    direction = setup['direction']
    entry     = sig['entry']
    sl        = sig['stop_loss']
    t1, t2, t3 = sig['target1'], sig['target2'], sig['target3']
    risk_pts  = abs(entry - sl)

    current_sl  = sl
    targets_hit = []
    partial_r   = 0.0
    result      = 'TIMEOUT'
    exit_price  = float(df['close'].iloc[-1])

    for i in range(entry_idx + 1, min(entry_idx + 200, len(df))):
        h = float(df['high'].iloc[i])
        lo = float(df['low'].iloc[i])

        if direction == 'BULLISH':
            if lo <= current_sl:
                result = 'SL_HIT'
                exit_price = current_sl
                break
            if 'T1' not in targets_hit and h >= t1:
                targets_hit.append('T1')
                partial_r += (t1 - entry) / risk_pts / 3
                current_sl = entry
            if 'T2' not in targets_hit and h >= t2:
                targets_hit.append('T2')
                partial_r += (t2 - entry) / risk_pts / 3
            if 'T3' not in targets_hit and h >= t3:
                targets_hit.append('T3')
                partial_r += (t3 - entry) / risk_pts / 3
                result = 'T3_HIT'
                exit_price = t3
                break
        else:
            if h >= current_sl:
                result = 'SL_HIT'
                exit_price = current_sl
                break
            if 'T1' not in targets_hit and lo <= t1:
                targets_hit.append('T1')
                partial_r += (entry - t1) / risk_pts / 3
                current_sl = entry
            if 'T2' not in targets_hit and lo <= t2:
                targets_hit.append('T2')
                partial_r += (entry - t2) / risk_pts / 3
            if 'T3' not in targets_hit and lo <= t3:
                targets_hit.append('T3')
                partial_r += (entry - t3) / risk_pts / 3
                result = 'T3_HIT'
                exit_price = t3
                break

    if result == 'TIMEOUT' and targets_hit:
        result = f"PARTIAL({','.join(targets_hit)})"

    remaining = 1.0 - len(targets_hit) / 3
    if result == 'SL_HIT':
        total_r = partial_r - remaining   # remaining portion stopped out at -1R
    elif 'T3' in result:
        total_r = partial_r
    else:
        final_dist = (exit_price - entry) if direction == 'BULLISH' else (entry - exit_price)
        total_r = partial_r + remaining * final_dist / risk_pts if risk_pts > 0 else partial_r

    return {
        'result'      : result,
        'targets_hit' : targets_hit,
        'total_r'     : round(total_r, 2),
        'exit_price'  : round(exit_price, 5),
        'risk_pts'    : round(risk_pts, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
#   FOREX backtest
# ─────────────────────────────────────────────────────────────────────────────
def run_forex_backtest(df_full: pd.DataFrame, symbol: str) -> list[dict]:
    trades = []
    # Slice to only the June 8-11 window for signal detection
    df_window = df_full[
        (df_full.index >= START_DATE) & (df_full.index <= END_DATE)
    ]
    if len(df_window) < 5:
        logger.info(f"FOREX {symbol}: no bars in June 8-11 window")
        return trades

    seen_entries: set = set()

    for pos in range(40, len(df_window)):
        # Context: up to 80 bars before signal candle (from full df for structure)
        signal_ts  = df_window.index[pos]
        utc_hour   = signal_ts.hour

        # Kill zone only
        in_kz = any(s <= utc_hour < e for s, e in [(7, 12), (16, 20)])
        if not in_kz:
            continue

        # Build context window from FULL df for structure detection
        full_idx = df_full.index.get_loc(signal_ts)
        ctx_start = max(0, full_idx - 80)
        df_ctx = df_full.iloc[ctx_start : full_idx + 1].copy()

        if len(df_ctx) < 40:
            continue

        # ── SCAN with H4 = RANGING (disabled) ──
        setup = scan_setup(df_ctx, symbol, min_rr=2.0, h4_bias='RANGING')
        if not setup:
            continue

        direction = setup['direction']
        entry     = setup['entry_signal']['entry']
        score     = setup['confluence']

        # Dedup: same direction + entry zone
        dedup = (signal_ts.date(), symbol, direction, round(entry, 2))
        if dedup in seen_entries:
            continue
        seen_entries.add(dedup)

        # Simulate on bars AFTER signal
        sim_df = df_full.iloc[full_idx:]
        outcome = simulate_trade(sim_df, setup, 0)

        trades.append({
            'market'    : 'FOREX',
            'symbol'    : symbol,
            'ts'        : signal_ts.strftime('%Y-%m-%d %H:%M UTC'),
            'direction' : direction,
            'score'     : score,
            'mss_type'  : setup.get('mss_type', '?'),
            'entry'     : round(entry, 5),
            'sl'        : round(setup['entry_signal']['stop_loss'], 5),
            'result'    : outcome['result'],
            'targets'   : ','.join(outcome['targets_hit']) or '-',
            'total_r'   : outcome['total_r'],
        })
        logger.info(
            f"  FOREX {symbol} {signal_ts.strftime('%m-%d %H:%M')} "
            f"{direction} score={score} → {outcome['result']} {outcome['total_r']}R"
        )

    return trades


# ─────────────────────────────────────────────────────────────────────────────
#   NSE backtest
# ─────────────────────────────────────────────────────────────────────────────
NSE_SYMBOLS = {
    'NSE:NIFTY26JUNFUT'    : 'NIFTY',
    'NSE:BANKNIFTY26JUNFUT': 'BANKNIFTY',
    'NSE:FINNIFTY26JUNFUT' : 'FINNIFTY',
    'NSE:MIDCPNIFTY26JUNFUT': 'MIDCPNIFTY',
}
import pytz
IST = pytz.timezone('Asia/Kolkata')

SB_WINDOWS_IST = [(10, 0, 11, 0), (13, 0, 14, 0), (15, 0, 15, 30)]  # h,m,h,m

def _in_sb_window(ts) -> bool:
    try:
        if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
            t = ts.astimezone(IST)
        else:
            t = IST.localize(ts)
        mins = t.hour * 60 + t.minute
        return any(sh * 60 + sm <= mins < eh * 60 + em
                   for sh, sm, eh, em in SB_WINDOWS_IST)
    except Exception:
        return False


def _get_nse_df(fyers, symbol: str, tf: str = '3', days: int = DAYS_BACK + 4) -> Optional[pd.DataFrame]:
    try:
        from scanner.data_fetcher import get_historical_data
        return get_historical_data(fyers, symbol, tf, days=days)
    except Exception as e:
        logger.warning(f"NSE fetch error {symbol}: {e}")
        return None


def run_nse_backtest(df_full: pd.DataFrame, symbol: str, label: str) -> list[dict]:
    trades = []

    # Fyers returns a 'timestamp' column with RangeIndex — promote it to index
    if 'timestamp' in df_full.columns and not isinstance(df_full.index, pd.DatetimeIndex):
        df_full = df_full.copy()
        df_full['timestamp'] = pd.to_datetime(df_full['timestamp'])
        df_full = df_full.set_index('timestamp').sort_index()

    # Ensure IST timezone on index
    try:
        if df_full.index.tz is None:
            df_full.index = df_full.index.tz_localize('Asia/Kolkata')
        else:
            df_full.index = df_full.index.tz_convert('Asia/Kolkata')
    except Exception:
        pass

    start_ist = datetime(2026, 6, 8,  9, 15, tzinfo=IST)
    end_ist   = datetime(2026, 6, 11, 15, 30, tzinfo=IST)

    df_window = df_full[(df_full.index >= start_ist) & (df_full.index <= end_ist)]
    if len(df_window) < 5:
        logger.info(f"NSE {label}: no bars in June 8-11 window")
        return trades

    seen_entries: set = set()

    for pos in range(30, len(df_window)):
        signal_ts = df_window.index[pos]

        if not _in_sb_window(signal_ts):
            continue

        full_idx = df_full.index.get_loc(signal_ts)
        ctx_start = max(0, full_idx - 80)
        df_ctx = df_full.iloc[ctx_start : full_idx + 1].copy()
        if len(df_ctx) < 30:
            continue

        # ── SCAN with fyers=None → H4 defaults to RANGING (disabled) ──
        setup = scan_silver_bullet(df_ctx, symbol, tf='3', fyers=None)
        if not setup:
            continue

        direction = setup['direction']
        entry     = setup['entry_signal']['entry']
        score     = setup['confluence']

        dedup = (signal_ts.date(), symbol, direction, round(entry))
        if dedup in seen_entries:
            continue
        seen_entries.add(dedup)

        # Simulate outcome on bars after signal
        sim_df = df_full.iloc[full_idx:]
        outcome = simulate_trade(sim_df, setup, 0)

        trades.append({
            'market'    : 'NSE',
            'symbol'    : label,
            'ts'        : signal_ts.strftime('%Y-%m-%d %H:%M IST'),
            'direction' : direction,
            'score'     : score,
            'mss_type'  : setup.get('mss_type', '?'),
            'entry'     : round(entry, 1),
            'sl'        : round(setup['entry_signal']['stop_loss'], 1),
            'result'    : outcome['result'],
            'targets'   : ','.join(outcome['targets_hit']) or '-',
            'total_r'   : outcome['total_r'],
        })
        logger.info(
            f"  NSE {label} {signal_ts.strftime('%m-%d %H:%M')} "
            f"{direction} score={score} → {outcome['result']} {outcome['total_r']}R"
        )

    return trades


# ─────────────────────────────────────────────────────────────────────────────
#   MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    all_trades = []
    mt5_ok     = False
    fyers_inst = None

    # ── Init MT5 ──────────────────────────────────────────────────────────────
    print("\n═══════════════════════════════════════════════════")
    print("  CB6 Quantum — No-H4 Backtest  |  June 8-11 2026")
    print("═══════════════════════════════════════════════════")
    print("\n[1/2] Connecting to MT5 for Forex data...")

    try:
        import MetaTrader5 as _mt5_mod
        mt5_ok = _init_mt5('5k')
        if mt5_ok:
            info = _mt5_mod.account_info()
            print(f"      MT5 connected — {info.company if info else 'OK'}")
        else:
            print("      MT5 not available — Forex backtest skipped")
    except Exception as e:
        print(f"      MT5 error: {e} — Forex backtest skipped")

    # ── Init Fyers ────────────────────────────────────────────────────────────
    print("\n[2/2] Connecting to Fyers for NSE data...")
    try:
        from fyers_apiv3 import fyersModel
        from dotenv import dotenv_values as _dv2
        _env2      = _dv2(ROOT / '.env')
        _client_id = _env2.get('CLIENT_ID', '')
        _token_str = _env2.get('ACCESS_TOKEN', '')
        if ':' in _token_str:
            _token_str = _token_str.split(':', 1)[1]
        if _token_str and _client_id:
            (ROOT / 'logs').mkdir(exist_ok=True)
            fyers_inst = fyersModel.FyersModel(
                client_id=_client_id, token=_token_str,
                is_async=False, log_path=str(ROOT / 'logs' / '')
            )
            test = fyers_inst.get_profile()
            if test and test.get('s') == 'ok':
                print(f"      Fyers connected — {test.get('data', {}).get('name', 'OK')}")
            else:
                print(f"      Fyers token may be stale (code={test.get('code')}), continuing anyway")
        else:
            print("      Fyers CLIENT_ID or ACCESS_TOKEN missing in .env — NSE skipped")
            fyers_inst = None
    except Exception as e:
        print(f"      Fyers not available: {e} — NSE backtest skipped")
        fyers_inst = None

    # ── Forex ─────────────────────────────────────────────────────────────────
    if mt5_ok:
        print(f"\n{'─'*52}")
        print("  FOREX  |  XAUUSD / XAGUSD / USOIL  |  H4 = RANGING")
        print(f"{'─'*52}")
        for sym in FOREX_SYMBOLS:
            print(f"\n  Scanning {sym}...")
            df, actual_sym = _get_mt5_df(sym, FOREX_TF)
            if df is None:
                print(f"    No data for {sym}")
                continue
            trades = run_forex_backtest(df, actual_sym)
            all_trades.extend(trades)
            _print_symbol_summary(actual_sym, trades)

    # ── NSE ───────────────────────────────────────────────────────────────────
    if fyers_inst is not None:
        print(f"\n{'─'*52}")
        print("  NSE  |  NIFTY / BNIFTY / FINNIFTY / MIDCP  |  H4 = RANGING (fyers=None)")
        print(f"{'─'*52}")
        for sym, label in NSE_SYMBOLS.items():
            print(f"\n  Scanning {label}...")
            df = _get_nse_df(fyers_inst, sym)
            if df is None:
                print(f"    No data for {label}")
                continue
            trades = run_nse_backtest(df, sym, label)
            all_trades.extend(trades)
            _print_symbol_summary(label, trades)

    # ── Summary ───────────────────────────────────────────────────────────────
    _print_final_summary(all_trades)

    # ── Save ──────────────────────────────────────────────────────────────────
    out_path = ROOT / 'reports' / 'no_h4_backtest_june8_11.json'
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'window'      : 'June 8-11 2026',
            'h4_setting'  : 'DISABLED (forced RANGING)',
            'total_trades': len(all_trades),
            'trades'      : all_trades,
        }, f, indent=2)
    print(f"\n  Saved → {out_path}")


def _print_symbol_summary(symbol: str, trades: list[dict]):
    if not trades:
        print(f"    {symbol}: 0 setups found in window")
        return
    wins   = sum(1 for t in trades if t['total_r'] > 0)
    losses = sum(1 for t in trades if t['total_r'] <= 0)
    total_r = sum(t['total_r'] for t in trades)
    wr = wins / len(trades) * 100 if trades else 0
    print(f"    {symbol}: {len(trades)} setups  |  WR {wr:.0f}%  |  Total {total_r:+.1f}R")
    for t in trades:
        icon = '✅' if t['total_r'] > 0 else '❌' if t['total_r'] < 0 else '➖'
        print(f"      {icon} {t['ts']}  {t['direction'][:4]}  "
              f"score={t['score']}  {t['mss_type']}  "
              f"{t['result']}  {t['total_r']:+.2f}R")


def _print_final_summary(trades: list[dict]):
    if not trades:
        print("\n  No trades found in either market.")
        return
    wins    = sum(1 for t in trades if t['total_r'] > 0)
    losses  = sum(1 for t in trades if t['total_r'] <= 0)
    total_r = sum(t['total_r'] for t in trades)
    wr      = wins / len(trades) * 100 if trades else 0

    nse_t   = [t for t in trades if t['market'] == 'NSE']
    forex_t = [t for t in trades if t['market'] == 'FOREX']

    print(f"\n{'═'*52}")
    print("  FINAL SUMMARY — H4 DISABLED (June 8-11 2026)")
    print(f"{'═'*52}")
    print(f"  Total setups : {len(trades)}")
    print(f"  Win Rate     : {wr:.1f}%  ({wins}W / {losses}L)")
    print(f"  Total R      : {total_r:+.2f}R")
    if nse_t:
        nr = sum(t['total_r'] for t in nse_t)
        nw = sum(1 for t in nse_t if t['total_r'] > 0)
        print(f"\n  NSE   : {len(nse_t)} trades  |  WR {nw/len(nse_t)*100:.0f}%  |  {nr:+.1f}R")
    if forex_t:
        fr = sum(t['total_r'] for t in forex_t)
        fw = sum(1 for t in forex_t if t['total_r'] > 0)
        print(f"  FOREX : {len(forex_t)} trades  |  WR {fw/len(forex_t)*100:.0f}%  |  {fr:+.1f}R")
    print(f"{'═'*52}")


if __name__ == '__main__':
    main()
