# ml/scanner.py
#
# CB6 Quantum — ML Multi-Timeframe Scanner
# Triggered by /ml_scan command from Telegram.
#
# Flow:
#   4H → HTF trend bias
#   1H → intermediate bias + confirmation
#   15m → primary entry timeframe (ICT chain)
#   5m  → precision entry / FVG refinement
#
# Returns actionable levels: entry, SL, T1/T2/T3, ML win_prob, R_hat
#
# SHADOW MODE — never places orders, reads data only.

from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from utils.logger import logger

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Index name → Fyers futures symbol resolver ─────────────────────────────────

_INDEX_ALIASES = {
    'NIFTY'      : 'NIFTY',
    'NF'         : 'NIFTY',
    'BANKNIFTY'  : 'BANKNIFTY',
    'BNF'        : 'BANKNIFTY',
    'FINNIFTY'   : 'FINNIFTY',
    'FINIFTY'    : 'FINNIFTY',
    'FN'         : 'FINNIFTY',
    'MIDCPNIFTY' : 'MIDCPNIFTY',
    'MIDCP'      : 'MIDCPNIFTY',
    'MCN'        : 'MIDCPNIFTY',
}


def _resolve_index(name: str) -> Optional[str]:
    """Resolve user input (NIFTY / BNF / etc.) → canonical index name."""
    return _INDEX_ALIASES.get(name.upper().strip())


# ── HTF bias via EMA crossover (same logic as forex engine) ────────────────────

def _ema_bias(df: pd.DataFrame, fast: int = 8, slow: int = 21) -> str:
    """
    EMA(fast) vs EMA(slow) on close prices.
    Returns 'BULLISH', 'BEARISH', or 'RANGING'.
    """
    if df is None or len(df) < slow + 5:
        return 'RANGING'
    c = df['close']
    ema_f = c.ewm(span=fast, adjust=False).mean().iloc[-1]
    ema_s = c.ewm(span=slow, adjust=False).mean().iloc[-1]
    band  = 0.0003   # 0.03% — inside = ranging
    ratio = (ema_f - ema_s) / ema_s if ema_s != 0 else 0
    if   ratio >  band: return 'BULLISH'
    elif ratio < -band: return 'BEARISH'
    return 'RANGING'


def _swing_levels(df: pd.DataFrame, lookback: int = 50) -> dict:
    """Find recent swing high/low for S/R context."""
    if df is None or len(df) < 10:
        return {}
    window = df.tail(lookback)
    return {
        'swing_high': round(float(window['high'].max()), 2),
        'swing_low' : round(float(window['low'].min()), 2),
        'last_close': round(float(window['close'].iloc[-1]), 2),
    }


def _premium_discount(swing_high: float, swing_low: float, current: float) -> str:
    """ICT premium/discount: above 50% = premium, below = discount."""
    if swing_high <= swing_low:
        return 'EQUILIBRIUM'
    eq = (swing_high + swing_low) / 2
    pct = (current - swing_low) / (swing_high - swing_low)
    if   pct > 0.55: return 'PREMIUM'
    elif pct < 0.45: return 'DISCOUNT'
    return 'EQUILIBRIUM'


# ── Main multi-TF scan ─────────────────────────────────────────────────────────

def scan_index(index_name: str, fyers) -> Optional[dict]:
    """
    Full multi-TF ICT + ML scan for one NSE index.

    Args:
        index_name : 'NIFTY' | 'BANKNIFTY' | 'FINNIFTY' | 'MIDCPNIFTY'
        fyers      : authenticated Fyers instance

    Returns setup dict with levels + ML scores, or None if no setup.
    SHADOW MODE — never touches orders.
    """
    from scanner.index_futures import get_active_futures
    from scanner.data_fetcher  import get_historical_data
    from scanner.silver_bullet import scan_silver_bullet

    futures = get_active_futures()
    symbol  = futures.get(index_name)
    if not symbol:
        logger.warning(f"ML Scanner: no active futures symbol for {index_name}")
        return None

    # ── Fetch all timeframes ───────────────────────────────────────────────────
    logger.info(f"ML Scanner: fetching multi-TF data for {index_name} ({symbol})")

    df_4h  = get_historical_data(fyers, symbol, '240', days=60)   # 4H bias
    df_1h  = get_historical_data(fyers, symbol, '60',  days=30)   # 1H bias
    df_15m = get_historical_data(fyers, symbol, '15',  days=10)   # entry TF
    df_5m  = get_historical_data(fyers, symbol, '5',   days=5)    # precision
    df_3m  = get_historical_data(fyers, symbol, '3',   days=3)    # 3m precision

    if df_5m is None or len(df_5m) < 30:
        logger.warning(f"ML Scanner {index_name}: insufficient 5m data")
        return None

    # ── Live LTP from Fyers quotes API ────────────────────────────────────────
    from scanner.live_price import get_live_price
    ltp = get_live_price(fyers, symbol)
    if ltp is None:
        # fallback: last candle close from 5m data
        ltp = float(df_5m['close'].iloc[-1]) if df_5m is not None and len(df_5m) > 0 else 0
        logger.warning(f"ML Scanner {index_name}: live price unavailable, using last 5m close {ltp}")
    else:
        logger.info(f"ML Scanner {index_name}: live LTP = {ltp:,.2f}")

    # ── HTF bias ───────────────────────────────────────────────────────────────
    bias_4h = _ema_bias(df_4h, fast=8, slow=21)
    bias_1h = _ema_bias(df_1h, fast=8, slow=21)

    # ── Swing levels (from 1H for context) ────────────────────────────────────
    swings_1h = _swing_levels(df_1h, lookback=50)
    swings_4h = _swing_levels(df_4h, lookback=30)

    # PD zone calculated against live LTP, not stale candle close
    pd_zone_1h = _premium_discount(
        swings_1h.get('swing_high', ltp),
        swings_1h.get('swing_low',  ltp),
        ltp
    )

    # ── ICT scan: 15m primary, 5m secondary, 3m precision ────────────────────
    setup_15m = None
    setup_5m  = None
    setup_3m  = None

    if df_15m is not None and len(df_15m) >= 30:
        setup_15m = scan_silver_bullet(df_15m, symbol, tf='15', fyers=fyers, force=True)

    if df_5m is not None and len(df_5m) >= 30:
        setup_5m  = scan_silver_bullet(df_5m,  symbol, tf='5',  fyers=fyers, force=True)

    if df_3m is not None and len(df_3m) >= 30:
        setup_3m  = scan_silver_bullet(df_3m,  symbol, tf='3',  fyers=fyers, force=True)

    # Priority: 15m (highest quality) → 5m → 3m (most granular / earliest signal)
    primary_setup = setup_15m or setup_5m or setup_3m
    primary_tf    = ('15m' if setup_15m else
                     '5m'  if setup_5m  else
                     '3m'  if setup_3m  else None)

    # ── ML DNN scoring — bt_trainer (trained on 768 real trades) ────────────────
    ml_result = None
    if primary_setup:
        try:
            from ml.bt_trainer import predict_from_setup
            ml_result = predict_from_setup(primary_setup, index_name=index_name)
            if ml_result:
                ml_result['model'] = '+'.join(ml_result.get('models_used', ['DNN']))
        except Exception as e:
            logger.debug(f"ML Scanner bt_trainer skip: {e}")

        # CNN/RNN will be added when live trades accumulate price series data

    # ── Build result dict ──────────────────────────────────────────────────────
    return {
        'index'        : index_name,
        'symbol'       : symbol,
        'scanned_at'   : datetime.now().strftime('%H:%M IST'),
        'bias_4h'      : bias_4h,
        'bias_1h'      : bias_1h,
        'pd_zone'      : pd_zone_1h,
        'swing_high_1h': swings_1h.get('swing_high'),
        'swing_low_1h' : swings_1h.get('swing_low'),
        'swing_high_4h': swings_4h.get('swing_high'),
        'swing_low_4h' : swings_4h.get('swing_low'),
        'ltp'          : ltp,          # live price from Fyers quotes API
        'setup'        : primary_setup,
        'setup_tf'     : primary_tf,
        'has_setup'    : primary_setup is not None,
        'setup_15m'    : setup_15m,
        'setup_5m'     : setup_5m,
        'setup_3m'     : setup_3m,
        'ml'           : ml_result,
    }


# ── Format for Telegram ────────────────────────────────────────────────────────

def format_scan_message(result: dict) -> str:
    """Format scan result as clean Telegram message."""
    index  = result['index']
    close  = result['ltp']          # live LTP from Fyers quotes
    b4h    = result['bias_4h']
    b1h    = result['bias_1h']
    pd_z   = result['pd_zone']
    sh1h   = result['swing_high_1h']
    sl1h   = result['swing_low_1h']
    sh4h   = result['swing_high_4h']
    sl4h   = result['swing_low_4h']
    t      = result['scanned_at']
    setup  = result['setup']
    tf     = result['setup_tf'] or '—'
    ml     = result['ml']

    # Bias icons
    def bias_icon(b):
        return '🟢' if b == 'BULLISH' else ('🔴' if b == 'BEARISH' else '🟡')

    def pd_icon(p):
        return '⬆️ PREMIUM' if p == 'PREMIUM' else ('⬇️ DISCOUNT' if p == 'DISCOUNT' else '⚖️ EQUILIBRIUM')

    lines = [
        f"🔍 <b>ML SCAN — {index}</b>   {t}",
        f"LTP: <b>{close:,.0f}</b>",
        "",
        "📊 <b>MULTI-TF BIAS</b>",
        f"  4H : {bias_icon(b4h)} {b4h}   (H: {sh4h:,.0f}  L: {sl4h:,.0f})" if sh4h else f"  4H : {bias_icon(b4h)} {b4h}",
        f"  1H : {bias_icon(b1h)} {b1h}   (H: {sh1h:,.0f}  L: {sl1h:,.0f})" if sh1h else f"  1H : {bias_icon(b1h)} {b1h}",
        f"  PD : {pd_icon(pd_z)}",
        "",
    ]

    # All timeframe setups
    s15 = result.get('setup_15m')
    s5  = result.get('setup_5m')
    s3  = result.get('setup_3m')

    if not s15 and not s5 and not s3:
        lines += [
            "❌ <b>NO SETUP</b>",
            "",
            "ICT chain incomplete on 3m / 5m / 15m.",
            "DOL → MSS → FVG chain not formed yet.",
            "Continue monitoring.",
        ]
    else:
        # Show primary setup in detail (15m → 5m → 3m)
        for (s, stf) in [(s15, '15m'), (s5, '5m'), (s3, '3m')]:
            if not s:
                continue
            sig = s['entry_signal']
            direction  = s.get('direction', '')
            mss_type   = s.get('mss_type', 'BOS')
            score      = s.get('confluence', 0)
            sweep_type = s.get('sweep_type', '')
            fvg        = s.get('fvg', {})
            fvg_low    = fvg.get('fvg_low', 0)
            fvg_high   = fvg.get('fvg_high', 0)
            dol        = s.get('dol', {})
            dol_level  = dol.get('level', sig.get('target3', 0)) if isinstance(dol, dict) else 0
            in_fvg     = s.get('in_fvg', False)
            in_ote     = s.get('in_ote', False)

            dir_icon = '🟢 LONG' if direction in ('BULLISH', 'BUY') else '🔴 SHORT'
            htf_ok = (
                (direction in ('BULLISH', 'BUY')  and b1h == 'BULLISH') or
                (direction in ('BEARISH', 'SELL') and b1h == 'BEARISH') or
                b1h == 'RANGING'
            )
            htf_icon = '✅' if htf_ok else '⚠️'

            lines += [
                f"🎯 <b>{stf} SETUP — {dir_icon}</b>  {htf_icon} HTF {'aligned' if htf_ok else 'COUNTER-TREND'}",
                f"  {mss_type} | Score: {score}/15 | Sweep: {sweep_type}",
                "",
                f"  Entry  : <b>{sig['entry']:,.1f}</b>{'  ◀ IN FVG ✅' if in_fvg else ''}",
                f"  SL     : {sig['stop_loss']:,.1f}",
                f"  T1     : {sig['target1']:,.1f}  (1:{sig.get('rr_ratio', 0):.1f}R partial)",
                f"  T2     : {sig['target2']:,.1f}",
                f"  T3     : {sig['target3']:,.1f}  ← DOL target",
                f"  Risk   : {sig.get('risk', 0):,.0f} pts",
                "",
                f"  FVG zone : {fvg_low:,.1f} – {fvg_high:,.1f}",
            ]
            if dol_level:
                lines.append(f"  DOL      : {dol_level:,.1f}")
            if in_ote:
                lines.append("  OTE      : ✅ price in optimal trade entry")
            lines.append("")

    # ML section
    if ml:
        conf_icon = {'HIGH': '🔥', 'MEDIUM': '🟡', 'LOW': '🟠', 'AVOID': '🛑'}.get(ml['confidence'], '❓')
        lines += [
            "🧠 <b>ML ASSESSMENT</b>",
            f"  Win prob   : <b>{ml['win_prob']:.1%}</b>  {conf_icon} {ml['confidence']}",
            f"  Expected R : {ml['r_hat']:+.2f}R",
            f"  Models     : {ml['model']}",
            "",
        ]
    else:
        lines += [
            "🧠 <b>ML</b> : No model yet (need 30+ closed trades to train)",
            "",
        ]

    # HTF alignment warning
    if setup:
        direction = setup.get('direction', '')
        if ((direction in ('BULLISH', 'BUY')  and b4h == 'BEARISH') or
            (direction in ('BEARISH', 'SELL') and b4h == 'BULLISH')):
            lines += [
                "⚠️ <b>WARNING</b>: Setup is COUNTER to 4H trend.",
                "Trade with reduced size or skip — Judas swing risk.",
                "",
            ]
        elif ((direction in ('BULLISH', 'BUY')  and b4h == 'BULLISH') or
              (direction in ('BEARISH', 'SELL') and b4h == 'BEARISH')):
            lines.append("✅ 4H trend aligned — full size setup")
            lines.append("")

    lines.append(f"<i>Shadow scan — advisory only. Bot places trades independently.</i>")
    return '\n'.join(lines)


# ── Scan all 4 indices at once ─────────────────────────────────────────────────

def scan_all_indices(fyers) -> list[dict]:
    """Scan all 4 NSE indices. Returns list of results."""
    results = []
    for idx in ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']:
        try:
            r = scan_index(idx, fyers)
            if r:
                results.append(r)
        except Exception as e:
            logger.error(f"ML Scanner [{idx}] error: {e}")
    return results


def format_summary_message(results: list[dict]) -> str:
    """One-line summary of all 4 indices for quick overview."""
    if not results:
        return "ML Scanner: no data available."
    lines = ["🔍 <b>ML SCAN — ALL INDICES</b>", ""]
    for r in results:
        idx   = r['index']
        close = r['ltp']            # live LTP from Fyers quotes
        b4h   = r['bias_4h']
        b1h   = r['bias_1h']
        setup = r['setup']
        ml    = r['ml']
        tf    = r['setup_tf'] or '—'

        b_icon = {'BULLISH': '🟢', 'BEARISH': '🔴', 'RANGING': '🟡'}
        has_s  = '🎯' if setup else '—'
        dir_s  = ''
        if setup:
            d = setup.get('direction', '')
            dir_s = ' LONG' if d in ('BULLISH','BUY') else ' SHORT'

        ml_s = f"  ML:{ml['win_prob']:.0%}/{ml['confidence'][0]}" if ml else ''
        lines.append(
            f"{has_s} <b>{idx}</b> {close:,.0f}  "
            f"4H:{b_icon.get(b4h,'?')} 1H:{b_icon.get(b1h,'?')}"
            f"{('  '+tf+dir_s) if setup else '  no setup'}{ml_s}"
        )
    lines += ["", "<i>Use /ml_scan NIFTY for full detail</i>"]
    return '\n'.join(lines)
