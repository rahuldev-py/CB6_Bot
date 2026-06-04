# forex_engine/forex_worker.py
#
# CB6 Quantum Forex Engine
#
# Strategy : ICT Silver Bullet — same CHoCH + BOS + FVG chain as NSE + Crypto
# Instruments: XAUUSD | XAGUSD | USOIL
# Timeframe : 15-min candles
# Risk      : 0.5% of account per trade (FTMO-safe: daily limit 3%)
# Leverage  : 1:100 (FTMO standard)
# Sessions  : London (07:00-16:00 UTC) + NY (13:00-21:00 UTC)
#
# Run: python -m forex_engine.forex_worker

import os
import sys
import time
import threading
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import dotenv_values
_env = dotenv_values(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
for k, v in _env.items():
    if k not in os.environ:
        os.environ[k] = v

from utils.logger import logger
from settings import (
    FOREX_EXECUTION_MODE,
    FOREX_EXECUTION_REVALIDATE_CYCLE_SECONDS,
    FOREX_MAX_SPREAD_PCT,
    FOREX_DISABLED_SYMBOLS,
    FOREX_ALLOWED_UTC_WINDOWS,
    FOREX_ALLOWED_SIGNAL_AGE_SECONDS,
    FOREX_MAX_ENTRY_DRIFT_PERCENT,
    FOREX_MAX_ENTRY_DRIFT_POINTS,
    FOREX_EXECUTION_MIN_RR,
    FOREX_EXECUTION_INVALIDATION_BUFFER_POINTS,
)
from forex_engine.forex_instruments import (
    INSTRUMENTS, FTMO_RULES, calc_lot_size, dollar_risk,
)
from forex_engine.mt5.mt5_connector import MT5Connector
from forex_engine.scanner.signal_scanner import scan_setup as _scan_setup
from forex_engine.scanner.setup_scorer import (
    score_aplus_similarity,
    lot_boost_factor as _lot_boost_factor,
)
from forex_engine.scanner.liquidity_sweep import sweep_confirmed as _sweep_ok
from forex_engine.trade.duplicate_guard import DuplicateGuard as _DuplicateGuard
from forex_engine.prop_firms.ftmo.ftmo_state import (
    load_state               as ftmo_load_state,
    open_trade               as ftmo_open_trade,
    update_trades            as ftmo_update_trades,
    get_summary              as ftmo_get_summary,
    update_trade_ticket      as ftmo_update_ticket,
    update_trade_fill_price  as ftmo_update_fill,
    rollback_trade           as ftmo_rollback,
    get_risk_mode            as ftmo_get_risk_mode,
    # REQ-4: deterministic daily reset + public save
    reset_daily_if_needed    as ftmo_reset_daily,
    save_state               as ftmo_save_state,
)
from utils.emergency_stop import is_emergency_stop_active  # REQ-3
try:
    from utils.market_intelligence import MarketIntelligence as _MI
    _mi = _MI()
except Exception:
    _mi = None
from forex_engine.forex_instruments import FTMO_RISK_GUARD, SYMBOL_MAX_SLIPPAGE
from utils.execution_validation import (
    SIGNAL_ARMED,
    SIGNAL_EXECUTED,
    create_forex_signal,
    get_forex_signal,
    revalidate_forex_signal,
    update_forex_signal,
    FOREX_CME_PROXY_MAP,
)
from ml_engine.memory.gft_shadow_recommendation import recommend_shadow_for_candidate
from ml_engine.memory.gft_soft_gate import evaluate_soft_gate_and_log

import random
import pandas as pd

# Aliases kept so rest of file can still call load_state / open_trade / update_trades
load_state    = ftmo_load_state
open_trade    = ftmo_open_trade
update_trades = ftmo_update_trades
get_summary   = ftmo_get_summary

# ── Config ─────────────────────────────────────────────────────────────────────
# MT5 FTMO 15m backtest 2024-2026 (2yr real broker data):
# XAUUSD 46% WR PF3.63 | XAGUSD 53% WR PF5.34 | USOIL 69% WR PF8.43
# 120 × 15m = 30 hours of candle history — enough for DOL/MSS/FVG lookback
INTERVAL      = '15m'
CANDLE_LIMIT  = 120
MONITOR_SECS  = 15
RISK_PCT      = FTMO_RULES['risk_per_trade_pct']
MIN_SCORE     = 11
MIN_RR        = 3.0

BLOCK_NEWS_TRADING = os.getenv('BLOCK_NEWS', 'false').lower() == 'true'
_FTMO_MAGIC        = int(os.getenv('FTMO_MAGIC', 62002))
_FOREX_EXEC_MODE   = str(FOREX_EXECUTION_MODE or 'LEGACY').strip().upper()

# Per-symbol minimum score — Silver Bullet 15m sessions (London 07-12 UTC, NY 16-20 UTC)
# Sweep + inFVG are hard requirements — these gates are for remaining confluence.
# MT5 FTMO 15m backtest 2024-05-27 to 2026-05-27 (2yr real broker data):
#   XAUUSD: 46.0% WR  +94.61R  PF 3.63  113 trades ✅
#   XAGUSD: 53.4% WR +154.87R  PF 5.34  133 trades ✅  ← BEST total R
#   USOIL : 69.0% WR  +46.05R  PF 8.43   42 trades ✅  ← BEST win rate + PF
# Note: Dukascopy 3m results that showed XAGUSD/USOIL as losers were CORRUPTED data.
SYMBOL_MIN_SCORE = {
    'XAUUSD': 11,   # active — 46.0% WR, PF 3.63, +94.61R (re-enabled Jun 2026)
    'XAGUSD': 11,   # active — 53.4% WR, PF 5.34, +154.87R (re-enabled 2026-05-27)
    'USOIL' : 11,   # active — 69.0% WR, PF 8.43,  +46.05R (re-enabled 2026-05-27)
    'EURUSD': 11,   # standby
}

ACTIVE_SYMBOLS = ['XAUUSD', 'XAGUSD', 'USOIL', 'EURUSD']

# ── Session windows (UTC) — matched to MT5 15m backtest ────────────────────────
# London : 07:00–12:00 UTC  (= 02:00–07:00 AM EST / 12:30–17:30 IST)
# NY     : 16:00–20:00 UTC  (= 11:00–15:00 AM EST / 21:30–01:30 IST+1)
# Backtest WR on these exact windows: XAUUSD 46% | XAGUSD 53% | USOIL 69%
KILL_ZONE_WINDOWS = [
    (7,  12),   # London session: 07-12 UTC
    (16, 20),   # NY session: 16-20 UTC
]

# Prime hours (first hour of each session — highest momentum)
PRIME_KZ_HOURS = {7, 8, 16, 17}

PAPER = os.getenv('FOREX_PAPER', 'true').lower() == 'true'

# ── Rollover window (UTC) ───────────────────────────────────────────────────────
# 5PM EST = 22:00 UTC. Liquidity vanishes; spreads explode (Gold +50 pips, Oil +30c).
# Block new entries 22:00-23:00 UTC. Pre-rollover guard fires at 21:55 UTC.
ROLLOVER_BLOCK_START = 22    # UTC hour — no new entries from here
ROLLOVER_BLOCK_END   = 23    # UTC hour — market resumes after this
ROLLOVER_WARN_MINUTE = 55    # if it's :55 in the block-start hour, pre-warn fires


def _in_rollover_window(utc_hour: int) -> bool:
    """True during the 22:00-23:00 UTC broker rollover — no new entries."""
    return ROLLOVER_BLOCK_START <= utc_hour < ROLLOVER_BLOCK_END


def _approaching_rollover() -> bool:
    """True at 21:55-21:59 UTC — 5 min before rollover. Used to close tight-SL trades."""
    now = datetime.now(timezone.utc)
    return now.hour == (ROLLOVER_BLOCK_START - 1) and now.minute >= ROLLOVER_WARN_MINUTE


def _in_kill_zone(utc_hour: int) -> bool:
    """True only if we're inside an active kill zone window."""
    return any(start <= utc_hour < end for start, end in KILL_ZONE_WINDOWS)


def _is_prime_kz(utc_hour: int) -> bool:
    """True during the highest-volume portion of a kill zone (standard score gate applies)."""
    return utc_hour in PRIME_KZ_HOURS


# ── Session label (display only) ───────────────────────────────────────────────

def _current_session_label() -> str:
    h = datetime.now(timezone.utc).hour
    if h == 8:
        return 'London Silver Bullet 08-09 UTC ✅'
    elif h == 15:
        return 'NY AM Silver Bullet 15-16 UTC ✅'
    elif 7 <= h < 12:
        return 'London session (outside SB window — no entries)'
    elif 12 <= h < 15:
        return 'London/NY Overlap (outside SB window — no entries)'
    elif 16 <= h < 21:
        return 'NY session (outside SB window — no entries)'
    else:
        return 'Asia / After-Hours (no entries)'


# ── News filter ─────────────────────────────────────────────────────────────────

# Manually maintained high-impact news windows (UTC).
# Format: (YYYY-MM-DD, HH_start, HH_end) — add before each event, remove after.
# When BLOCK_NEWS_TRADING=true, no entry is allowed inside any window.
NEWS_WINDOWS: list = [
    # Example: ('2026-05-21', 14, 15),  # USD CPI release
]

def _in_news_window() -> bool:
    """
    True if a high-impact news block is active.
    Checks two sources in order:
      1. Manual NEWS_WINDOWS list (legacy — hardcoded dates for known events)
      2. Yahoo Finance news monitor (automatic — detects CPI/NFP/FOMC headlines)
    Either source can trigger a block independently.
    """
    now  = datetime.now(timezone.utc)
    date = now.strftime('%Y-%m-%d')
    h    = now.hour

    # Manual list (BLOCK_NEWS env var not required for manual entries)
    if NEWS_WINDOWS and any(d == date and s <= h < e for d, s, e in NEWS_WINDOWS):
        return True

    # Yahoo news monitor (automatic detection)
    try:
        from data.forex_news_monitor import is_news_blackout
        if is_news_blackout():
            return True
    except Exception:
        pass

    return False


# ── GFT lot modifier (anti-pattern fingerprint) ─────────────────────────────────

def _gft_lot_modifier(lots: float) -> float:
    """Add ±0.01-0.02 fractional noise to avoid round-lot pattern detection."""
    offset = round(random.choice([-0.02, -0.01, 0.01, 0.02]), 2)
    return max(0.01, round(lots + offset, 2))


# ── Telegram ────────────────────────────────────────────────────────────────────

def _send(msg: str):
    try:
        from communications.forex_bot import send_alert
        send_alert(msg)
    except Exception:
        logger.info(f"[FOREX TG] {msg[:120]}")


def _compute_spread_pct(spread_value: Optional[float], price_value: Optional[float]) -> Optional[float]:
    try:
        spread = float(spread_value)
        price = float(price_value)
        if price <= 0:
            return None
        return abs(spread / price)
    except Exception:
        return None


def _get_proxy_snapshot(symbol: str) -> dict:
    """
    Structural proxy snapshot for validation only.
    Never used for order routing.
    """
    proxy_symbol = FOREX_CME_PROXY_MAP.get(str(symbol or '').upper())
    if not proxy_symbol:
        return {'available': False, 'reason': 'PROXY_DATA_UNAVAILABLE'}
    try:
        import yfinance as yf
        hist = yf.Ticker(proxy_symbol).history(period='1d', interval='5m')
        if hist is None or len(hist) < 2:
            return {'available': False, 'reason': 'PROXY_DATA_UNAVAILABLE', 'proxy_symbol': proxy_symbol}
        last = float(hist['Close'].iloc[-1])
        prev = float(hist['Close'].iloc[-2])
        if last > prev:
            trend = 'BULLISH'
        elif last < prev:
            trend = 'BEARISH'
        else:
            trend = 'RANGING'
        return {
            'available': True,
            'proxy_symbol': proxy_symbol,
            'last': last,
            'prev': prev,
            'trend': trend,
        }
    except Exception:
        return {'available': False, 'reason': 'PROXY_DATA_UNAVAILABLE', 'proxy_symbol': proxy_symbol}


# ── H1 Higher-Timeframe Bias ───────────────────────────────────────────────────

def _get_h1_bias(adapter, symbol: str) -> Optional[str]:
    """
    H1 trend bias via EMA(3) vs EMA(8).

    Returns:
      'BULLISH'  — H1 trending up   → only take BULLISH 15m setups
      'BEARISH'  — H1 trending down → only take BEARISH 15m setups
      'RANGING'  — EMAs within 0.02% → trade allowed, but score gate raised by +1

    ICT rationale:
      A 15m CHoCH against the H1 trend is a Judas swing (trap). Blocking it is
      correct. A 15m CHoCH WITH the H1 trend is a pullback entry — take it.
      When H1 is ranging, the 15m setup is neither confirmed nor contradicted;
      we allow it only if the ICT structure score is high enough.
    """
    try:
        df = adapter.get_klines(symbol, '1h', 20)
        if df is None or len(df) < 10:
            return 'RANGING'   # no H1 data — treat as ranging, allow with stricter gate
        c    = df['close']
        fast = c.ewm(span=3, adjust=False).mean().iloc[-1]
        slow = c.ewm(span=8, adjust=False).mean().iloc[-1]
        if fast > slow * 1.0002:
            return 'BULLISH'
        if fast < slow * 0.9998:
            return 'BEARISH'
        return 'RANGING'
    except Exception:
        return 'RANGING'


def _get_h4_bias(adapter, symbol: str) -> Optional[str]:
    """
    H4 multi-day trend bias via EMA(3) vs EMA(8) on H4 candles.

    Used as a hard directional gate — the live data shows:
    - Every BULLISH trade on XAUUSD/XAGUSD won when H4 was bullish
    - Every BEARISH XAUUSD trade lost because H4 was in an uptrend
    - Every XAGUSD SELL in Asia (H4 ranging) lost

    Returns 'BULLISH', 'BEARISH', or 'RANGING'.
    """
    try:
        df = adapter.get_klines(symbol, '4h', 20)
        if df is None or len(df) < 8:
            return 'RANGING'
        c    = df['close']
        fast = c.ewm(span=3, adjust=False).mean().iloc[-1]
        slow = c.ewm(span=8, adjust=False).mean().iloc[-1]
        if fast > slow * 1.0003:   # slightly wider band for H4 (less noise)
            return 'BULLISH'
        if fast < slow * 0.9997:
            return 'BEARISH'
        return 'RANGING'
    except Exception:
        return 'RANGING'


# ── A+ Setup Similarity Scorer ────────────────────────────────────────────────
# ── ICT Scanner (same logic as crypto — market structure is universal) ──────────

def scan_forex_setup(df: pd.DataFrame, symbol: str) -> Optional[dict]:
    """
    Run ICT Silver Bullet chain on 15-min forex DataFrame.
    Reuses the exact same scanner as crypto/NSE engines.
    """
    try:
        from scanner.silver_bullet import (
            find_draw_on_liquidity,
            detect_sb_mss,
            detect_sb_fvg,
            detect_order_block,
            premium_discount_context,
            premium_discount_aligned,
        )
        from forex_engine.scanner.liquidity_sweep import (
            analyze_liquidity_state,
            detect_sweep,
        )
        from scanner.ut_bot import get_ut_signal

        if df is None or len(df) < 40:
            return None

        cfg = INSTRUMENTS.get(symbol, {})

        # 0. Liquidity Sweep — did smart money grab stops at a swing level?
        #    Sweep of highs → SHORT (distributed at top)
        #    Sweep of lows  → LONG  (accumulated at bottom)
        #    15m bars: sweep_window=20 = last 5 hours
        liquidity_state = analyze_liquidity_state(
            df,
            symbol=symbol,
            timeframe=INTERVAL,
            lookback=80,
            sweep_window=20,
        )
        liq_sweep = detect_sweep(
            df,
            lookback=80,
            sweep_window=20,
            symbol=symbol,
            timeframe=INTERVAL,
        )
        if liq_sweep:
            logger.info(
                f"FOREX {symbol}: Liq sweep {liq_sweep['sweep_type']} "
                f"@ {liq_sweep['swept_level']:.5f}  "
                f"wick={liq_sweep['wick_extreme']:.5f}  "
                f"{liq_sweep['candles_ago']} candles ago "
                f"conf={liq_sweep.get('confidence', 0)}/100"
            )

        # 1. Draw on Liquidity
        dol = find_draw_on_liquidity(df, lookback=80, wick_sweep=True)
        if dol is None:
            return None
        logger.info(f"FOREX {symbol}: DOL {dol['direction']} @ {dol['level']:.5f}")

        # 2. Market Structure Shift — close-based: candle must close beyond swing
        mss = detect_sb_mss(df, lookback=40)
        if mss is None:
            return None
        direction = mss['direction']
        logger.info(f"FOREX {symbol}: MSS {direction} {mss.get('type')} @ {mss['level']:.5f}")

        if dol['direction'] != direction:
            logger.info(f"FOREX {symbol}: DOL {dol['direction']} vs MSS {direction} — mismatch, no score bonus, proceeding")

        # 3. Fair Value Gap
        fvg = detect_sb_fvg(df, direction, lookback=25, displacement_mult=1.0, use_range=True)
        if fvg is None:
            logger.info(f"FOREX {symbol}: no {direction} FVG found after MSS — skip")
            return None

        # FVG displacement — tiered body ratio (data-driven from 199 Forex journal trades):
        #   body < 45% (WEAK)    → skip (wick-dominated candle, noise)
        #   body 45-65% (PARTIAL)→ allow, score -1 applied below in scoring
        #   body >= 65% (STRONG) → standard, no penalty
        _forex_body_tier = fvg.get('body_tier', 'WEAK')
        if _forex_body_tier == 'WEAK':
            logger.info(
                'FOREX %s: FVG body %.0f%% < 45%% - wick noise, no displacement',
                symbol, fvg.get('body_ratio', 0) * 100
            )
            return None

        min_sl      = cfg.get('min_sl_dist', 0.0010)
        min_fvg     = cfg.get('min_fvg_size', min_sl * 0.5)
        fvg_actual_size = fvg.get('size', 0)
        if fvg_actual_size < min_fvg:
            logger.info(f"FOREX {symbol}: FVG too small ({fvg_actual_size:.5f} < {min_fvg:.5f}) — skip")
            return None

        # 4. Spread filter — skip during news spikes / wide spread (live MT5 data only)
        # MT5 candle 'spread' column is in raw points; convert to price before comparing.
        if 'spread' in df.columns:
            raw_spread   = float(df['spread'].iloc[-1])
            point_size   = cfg.get('point_size', 0.00001)
            spread_price = raw_spread * point_size
            max_spread   = cfg.get('max_spread', 999)
            if spread_price > max_spread:
                logger.info(
                    f"FOREX {symbol}: spread {raw_spread:.0f} pts ({spread_price:.5f}) > max {max_spread} — skip (news/spike)"
                )
                return None

        # 5. Price gate  (was 4)
        last_low   = float(df['low'].iloc[-1])
        last_high  = float(df['high'].iloc[-1])
        last_close = float(df['close'].iloc[-1])
        fvg_low    = fvg['fvg_low']
        fvg_high   = fvg['fvg_high']
        fvg_mid    = fvg['mid']

        pd_context = premium_discount_context(df, fvg_mid, lookback=40)
        pd_context['aligned'] = premium_discount_aligned(direction, pd_context)
        if not pd_context['aligned']:
            logger.info(
                f"FOREX {symbol}: {direction} FVG in {pd_context.get('zone')} "
                f"(eq={pd_context.get('equilibrium', 0):.5f}) â€” skip"
            )
            return None

        in_fvg    = last_low <= fvg_high and last_high >= fvg_low
        fvg_size_pts = fvg_high - fvg_low

        # Approach entry: price heading toward FVG, within 1×FVG-size of edge.
        # Data (199 Forex journal): not-in-FVG WR=79.3% vs in-FVG WR=65.9%
        # Same finding as NSE — pre-touch entries outperform.
        if direction == 'BULLISH':
            _forex_approaching = (last_close > fvg_high) and (last_close <= fvg_high + fvg_size_pts)
        else:
            _forex_approaching = (last_close < fvg_low) and (last_close >= fvg_low - fvg_size_pts)

        _forex_approach_entry = False
        if not in_fvg:
            if _forex_approaching:
                _forex_approach_entry = True
                logger.info(
                    f"FOREX {symbol}: APPROACH — price {last_close:.5f} heading toward "
                    f"{direction} FVG {fvg_low:.5f}–{fvg_high:.5f} (pre-touch, gate=12)"
                )
            else:
                return None

        # FVG fill depth at current price (0%=touched edge, 100%=fully filled)
        if direction == 'BULLISH':
            _forex_fill_pct = ((fvg_high - last_close) / fvg_size_pts * 100) if fvg_size_pts > 0 else 50
        else:
            _forex_fill_pct = ((last_close - fvg_low) / fvg_size_pts * 100) if fvg_size_pts > 0 else 50

        # 6. Order Block detection (LuxAlgo-style, same as NSE scanner)
        #    Last opposing candle before the displacement that caused the MSS.
        #    OB zone = institutional supply (BEAR_OB) or demand (BULL_OB).
        #    Used as scoring factor (+1) and for entry alert enrichment.
        ob = detect_order_block(df, direction, lookback=40)
        if ob:
            logger.info(
                f"FOREX {symbol}: OB {ob['type']} zone "
                f"{ob['ob_low']:.5f}–{ob['ob_high']:.5f} mid={ob['ob_mid']:.5f}"
            )
        ob_present = ob is not None

        # 7. Build trade plan
        fvg_buf  = cfg.get('fvg_buf', 0.0003)
        fvg_size = max(fvg.get('size', min_sl), min_sl)

        if direction == 'BULLISH':
            if _forex_approach_entry:
                # Limit order at FVG top edge (pre-touch = better price)
                entry = round(fvg_high - fvg_buf, 5)
                sl    = round(fvg_low  - fvg_size, 5)    # standard SL below gap
            else:
                # Deep-fill entry (fvg_low) — Forex data shows 75%+ fill WR=77% (good)
                entry = round(fvg_low + fvg_buf, 5)
                sl    = round(fvg_low - fvg_size, 5)
            risk  = round(entry - sl, 5)
            if risk <= 0:
                return None
            t1    = round(entry + risk * 2.0, 5)
            t2    = round(entry + risk * 3.0, 5)
            dol_l = dol['level']
            t3    = round(max(dol_l if dol_l > t2 else entry + risk * 4.0, t2), 5)
            rr    = round((t2 - entry) / risk, 1)
        else:
            if _forex_approach_entry:
                entry = round(fvg_low  + fvg_buf, 5)
                sl    = round(fvg_high + fvg_size, 5)
            else:
                entry = round(fvg_high - fvg_buf, 5)
                sl    = round(fvg_high + fvg_size, 5)
            risk  = round(sl - entry, 5)
            if risk <= 0:
                return None
            t1    = round(entry - risk * 2.0, 5)
            t2    = round(entry - risk * 3.0, 5)
            dol_l = dol['level']
            t3    = round(min(dol_l if dol_l < t2 else entry - risk * 4.0, t2), 5)
            rr    = round((entry - t2) / risk, 1)

        if rr < MIN_RR:
            return None

        # 8. UT Bot trend filter
        try:
            ut = get_ut_signal(df)
            ut['aligned'] = (ut.get('trend') == direction)
        except Exception:
            ut = {'trend': None, 'stop': None, 'signal': None,
                  'bars_in_trend': 0, 'aligned': None}

        # 9. Confluence score (max 15: added OB +1 vs 14 baseline)
        #    DOL(5) + CHoCH(2)/BOS(1) + inFVG(1) + disp(1) + RR(1) + UT(2) + sweep(2) + OB(1) = 15
        mss_type       = mss.get('type', 'BOS')
        dol_agrees     = (dol['direction'] == direction)
        sweep_confirmed = (
            liq_sweep is not None and
            liq_sweep['direction'] == direction and
            liq_sweep['candles_ago'] <= 15 and
            liq_sweep.get('level_state') == 'SWEPT'
        )
        sweep_confidence = int((liq_sweep or {}).get('confidence', 0))
        score      = 5 if dol_agrees else 4
        score     += 2 if mss_type == 'CHOCH' else 1   # CHoCH already +1 over BOS (data: CHoCH WR 75% vs BOS 62.6%)
        score     += 1 if in_fvg else 0
        score     += 1 if fvg.get('displacement') else 0
        if _forex_body_tier == 'PARTIAL':
            score -= 1   # body 45-65%: weaker displacement, same as NSE
        score     += 1 if rr >= 3.0 else 0
        score     += 2 if ut.get('aligned') else 0
        score     += 2 if sweep_confirmed else 0
        score     += 1 if sweep_confidence >= 70 else 0
        score     += 1 if ob_present else 0

        # ── Data-driven Forex adjustments (199 journal + 288 MT5 trades) ──────
        # 1. CHoCH + London session: WR 80.0% — best combo in Forex data
        _sess_label = setup.get('session', '') if 'setup' in dir() else ''
        if mss_type == 'CHOCH' and 'london' in str(utc_hour).lower() or (7 <= utc_hour <= 11):
            if mss_type == 'CHOCH':
                score += 1
                logger.debug(f"FOREX {symbol}: CHoCH+London bonus +1 (data WR 80%)")

        # 2. Early London penalty: UTC 07-09 WR=38-43% — structurally weak
        if 7 <= utc_hour <= 9:
            score -= 1
            logger.debug(f"FOREX {symbol}: early London hour {utc_hour} UTC — score-1 (data WR 38-43%)")

        # 3. MSS gap — per-symbol optimal max gap (from journal win p75)
        _mss_gap = fvg_size_pts  # proxy: FVG size ≈ MSS-to-entry distance
        _sym_max_gap = {'XAUUSD': 14.6, 'XAGUSD': 0.505, 'USOIL': 1.090, 'EURUSD': 0.001}
        for _sg_sym, _max_g in _sym_max_gap.items():
            if _sg_sym in symbol.upper() and _mss_gap > _max_g * 2:
                # >2× optimal gap: price has moved too far from structure
                score -= 1
                logger.debug(f"FOREX {symbol}: gap {_mss_gap:.4f} > 2×{_max_g} optimal — score-1")
                break

        # 4. Approach entry gate: score >= 12 required (same as NSE)
        if _forex_approach_entry and score < 12:
            logger.info(
                f"FOREX {symbol}: APPROACH entry rejected — score {score} < 12 "
                f"(pre-touch needs high confidence)"
            )
            return None

        logger.info(
            f"FOREX {symbol}: score {score}/16 "
            f"(CHoCH={mss_type=='CHOCH'} inFVG={in_fvg} approach={_forex_approach_entry} "
            f"fill={_forex_fill_pct:.0f}% body={_forex_body_tier} "
            f"displ={fvg.get('displacement')} UT={ut.get('aligned')} "
            f"sweep={sweep_confirmed} sweepConf={sweep_confidence} "
            f"OB={ob_present})"
        )

        return {
            'symbol'          : symbol,
            'direction'       : direction,
            'confluence'      : score,
            'in_fvg'          : in_fvg,
            'approach_entry'  : _forex_approach_entry,
            'fvg_fill_pct'    : round(_forex_fill_pct, 1),
            'fvg_body_tier'   : _forex_body_tier,
            'entry_type'      : 'APPROACH' if _forex_approach_entry else f'IN_FVG_{_forex_fill_pct:.0f}pct',
            'mss_type'        : mss_type,
            'dol'             : dol,
            'mss'             : mss,
            'fvg'             : fvg,
            'premium_discount': pd_context,
            'ob'              : ob,           # None or {ob_high, ob_low, ob_mid, type}
            'ob_present'      : ob_present,
            'ut_bot'          : ut,
            'liq_sweep'       : liq_sweep,
            'liquidity_state' : liquidity_state,
            'sweep_confidence': sweep_confidence,
            'sweep_quality'   : (liq_sweep or {}).get('quality', {}),
            'sweep_confirmed' : sweep_confirmed,
            'entry_signal': {
                'entry'    : entry,
                'stop_loss': sl,
                'target1'  : t1,
                'target2'  : t2,
                'target3'  : t3,
                'risk'     : risk,
                'rr_ratio' : rr,
                'fvg_low'  : round(fvg_low, 5),
                'fvg_high' : round(fvg_high, 5),
                'dol_level': round(dol['level'], 5),
                'mss_level': round(mss['level'], 5),
            },
        }

    except Exception as e:
        import traceback
        logger.error(f"scan_forex_setup({symbol}) error: {e}\n{traceback.format_exc()}")
        return None


# ── Alert formatters ────────────────────────────────────────────────────────────

def _format_entry_alert(setup: dict, lots: float, risk_usd: float, ticket: int = 0, platform: str = 'FTMO') -> str:
    sig   = setup['entry_signal']
    sym   = setup['symbol']
    label = INSTRUMENTS.get(sym, {}).get('label', sym)
    dlab  = 'LONG (BUY)' if setup['direction'] == 'BULLISH' else 'SHORT (SELL)'
    ut    = setup.get('ut_bot', {})

    session = _current_session_label()

    mode_line = f'🔴 LIVE — {platform}' if not PAPER else f'Paper Trading ({platform})'
    ticket_line = f"MT5 Ticket : #{ticket}" if (not PAPER and ticket) else ('⚠️ Paper only — no MT5 order' if not PAPER else '')

    liq_sweep       = setup.get('liq_sweep')
    sweep_confirmed = setup.get('sweep_confirmed', False)
    if sweep_confirmed and liq_sweep:
        s_type      = 'LOW swept ✅' if liq_sweep['sweep_type'] == 'LOW_SWEEP' else 'HIGH swept ✅'
        sweep_line  = f"Liq Sweep  : {s_type} @ {liq_sweep['swept_level']}  ({liq_sweep['candles_ago']} candles ago)\n"
    elif liq_sweep:
        sweep_line  = f"Liq Sweep  : {liq_sweep['sweep_type']} (opposite dir — caution)\n"
    else:
        sweep_line  = "Liq Sweep  : None detected\n"

    ob      = setup.get('ob')
    ob_line = (f"Order Block: {ob['type']} {ob['ob_low']:.5f}–{ob['ob_high']:.5f} ✅\n"
               if ob else "Order Block: Not detected\n")

    # A+ similarity line — only shown if similarity data is present
    sim_ratio = setup.get('sim_ratio', 0.0)
    boost     = setup.get('lot_boost', 1.0)
    if sim_ratio >= 0.55:
        sim_line = f"A+ Match   : {sim_ratio:.0%} ⭐ — lots boosted {boost}×\n"
    elif sim_ratio > 0:
        sim_line = f"A+ Match   : {sim_ratio:.0%} (threshold 55%)\n"
    else:
        sim_line = ""

    conv_score = setup.get('conviction_score')
    conv_grade = setup.get('conviction_grade')
    if conv_score is not None:
        conv_emoji = "🟢" if conv_grade in ("A+", "A") else ("🟡" if conv_grade == "B" else "🔴")
        conv_line  = f"Conviction : {conv_emoji} {conv_grade} ({conv_score:.0f}/100)\n"
    else:
        conv_line  = ""

    return (
        f"<b>CB6 QUANTUM — FOREX {label} [{setup['confluence']}/15]</b>\n\n"
        f"Direction  : {dlab}\n"
        f"Session    : {session}\n"
        f"Time       : {datetime.now().strftime('%H:%M:%S IST')}\n\n"
        f"<b>STRUCTURE</b>\n"
        f"{sweep_line}"
        f"DOL        : {sig['dol_level']}\n"
        f"MSS        : {sig['mss_level']} ({setup['mss_type']})\n"
        f"FVG Zone   : {sig['fvg_low']} – {sig['fvg_high']}\n"
        f"FVG Status : {'IN ZONE ✅' if setup.get('in_fvg') else 'APPROACHING'}\n"
        f"{ob_line}"
        f"UT Bot     : {ut.get('trend','?')} | {'✅' if ut.get('aligned') else '⚠️'}\n\n"
        f"<b>TRADE PLAN</b>\n"
        f"Entry      : {sig['entry']}\n"
        f"SL         : {sig['stop_loss']}\n"
        f"T1 (1/3)   : {sig['target1']}  (1:2R)\n"
        f"T2 (1/3)   : {sig['target2']}  (1:3R)\n"
        f"T3 (1/3)   : {sig['target3']}  (DOL)\n"
        f"RR         : 1:{sig['rr_ratio']}\n"
        f"Lots       : {lots}  |  Risk ${risk_usd}\n"
        f"{sim_line}"
        f"{conv_line}\n"
        f"Mode       : {mode_line}\n"
        + (f"{ticket_line}\n" if ticket_line else "")
    )


def _format_exit_alert(event: dict, platform: str = 'FTMO') -> str:
    t     = event['trade']
    sym   = t.get('symbol', 'XAUUSD')
    label = INSTRUMENTS.get(sym, {}).get('label', sym)
    pnl   = event['pnl']
    sign  = '+' if pnl >= 0 else ''
    etype = event['type']
    dlab  = 'LONG' if t.get('direction') == 'BULLISH' else 'SHORT'

    if etype == 'SL':
        result = '🔴 STOP LOSS HIT'
    elif etype == 'T1_BE':
        result = '🟡 T1 HIT — SL moved to breakeven (min-lot: full position runs to T2)'
    elif etype == 'T1':
        result = '🟡 T1 HIT — 1/3 booked, SL → breakeven'
    elif etype == 'T2':
        result = '🟢 T2 HIT — position closed, profit locked'
    elif etype == 'T3':
        result = '✅ T3 HIT — full target reached (DOL)'
    else:
        result = f'{etype} HIT'

    try:
        daily_pnl  = ftmo_get_summary().get('daily_pnl', 0)
        daily_line = f"Daily PnL  : {'+' if daily_pnl >= 0 else ''}${daily_pnl:.2f} [FTMO]"
    except Exception:
        daily_line = ''

    return (
        f"<b>CB6 QUANTUM — FOREX {label} [{platform}]</b>\n"
        f"{result}\n\n"
        f"Direction  : {dlab}\n"
        f"Entry      : {t['entry_price']}\n"
        f"Exit       : {event['price']}\n"
        f"PnL        : {sign}${pnl:.2f}\n"
        + (f"{daily_line}\n" if daily_line else "")
        + f"Time       : {datetime.now().strftime('%H:%M:%S IST')}\n"
        f"Trade      : {t['id']}"
    )


# ── Main engine ─────────────────────────────────────────────────────────────────

class ForexWorker:
    def __init__(self):
        self._paper   = PAPER
        # ── Multi-account terminal isolation ────────────────────────────────────
        # Build connector via FTMO adapter — passes the FTMO terminal path to
        # mt5.initialize() so this process connects to MT5_FTMO_10K exclusively.
        # This subprocess never shares an MT5 session with the GFT subprocess.
        from forex_engine.accounts.ftmo_adapter import build_ftmo_connector
        self._adapter = build_ftmo_connector(paper=self._paper)

        self._candles       : dict = {}
        self._locks         : dict = {sym: threading.Lock() for sym in ACTIVE_SYMBOLS}
        self._entry_lock    = threading.Lock()
        self._dedup = _DuplicateGuard(
            persist_path=os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                'data', 'ftmo_10k', 'dedup.json'
            )
        )
        self._ema_alerted   : dict = {sym: set() for sym in ACTIVE_SYMBOLS}
        self._bd_cap_alerted: str  = ''
        self._risk_alerted  : dict = {}
        self._armed_signals : set = set()
        self._forex_exec_cfg = {
            'disabled_symbols': [str(s).upper() for s in (FOREX_DISABLED_SYMBOLS or [])],
            'allowed_utc_windows': FOREX_ALLOWED_UTC_WINDOWS or [["08:00", "11:00"], ["13:00", "16:30"]],
            'max_spread_pct': FOREX_MAX_SPREAD_PCT,
            'max_entry_drift_percent': FOREX_MAX_ENTRY_DRIFT_PERCENT,
            'max_entry_drift_points': FOREX_MAX_ENTRY_DRIFT_POINTS,
            'minimum_required_rr': FOREX_EXECUTION_MIN_RR,
            'invalidation_buffer_points': FOREX_EXECUTION_INVALIDATION_BUFFER_POINTS,
            'allowed_signal_age_seconds': FOREX_ALLOWED_SIGNAL_AGE_SECONDS,
        }
        self._revalidate_cycle_secs = max(1, int(FOREX_EXECUTION_REVALIDATE_CYCLE_SECONDS or 60))
        self._running       = False

    # ── Candle callback ────────────────────────────────────────────────────────

    def _on_closed_candle(self, symbol: str, df: pd.DataFrame):
        if symbol not in ACTIVE_SYMBOLS:
            return
        self._candles[symbol] = df
        t_str = df.index[-1].strftime('%H:%M') if hasattr(df.index[-1], 'strftime') else str(df.index[-1])
        logger.info(f"FOREX {symbol} candle closed {t_str} | C={df['close'].iloc[-1]:.5f}")
        threading.Thread(target=self._run_scan, args=(symbol,),
                         daemon=True, name=f"ForexScan_{symbol}").start()

    # ── Scanner ────────────────────────────────────────────────────────────────

    def _run_scan(self, symbol: str):
        if not self._locks[symbol].acquire(blocking=False):
            return
        forex_signal_id = None
        safe_mode_active = (_FOREX_EXEC_MODE == 'SAFE_VALIDATION_REVALIDATE_AUTO')
        try:
            # REQ-3: emergency stop — abort scan if file-based flag is active
            if is_emergency_stop_active():
                logger.warning(
                    f"EMERGENCY_STOP.flag active — FTMO scan skipped ({symbol})"
                )
                return

            state = load_state()
            if state.get('paused'):
                return

            # Rollover block — no new entries during 22:00-23:00 UTC
            utc_now  = datetime.now(timezone.utc)
            utc_hour = utc_now.hour
            if _in_rollover_window(utc_hour):
                logger.debug(f"FOREX {symbol}: ROLLOVER BLOCK (22:00-23:00 UTC) — skip")
                return

            # News filter — block entries near red-folder events
            if _in_news_window():
                logger.info(f"FOREX {symbol}: NEWS BLOCK — high-impact event window active")
                return

            # Determine session tier — affects score gate, never hard-blocks
            _in_kz   = _in_kill_zone(utc_hour)
            kz_label = _current_session_label()
            logger.debug(
                f"FOREX {symbol}: {utc_hour}:xx UTC — {kz_label} "
                f"[{'prime KZ' if _is_prime_kz(utc_hour) else 'off-peak KZ' if _in_kz else 'out-of-KZ'}]"
            )

            df = self._candles.get(symbol)
            if df is None or len(df) < 40:
                return

            # ── Daily ATR — for ATR-fractional target sizing ──────────────────
            # Fetch 14 daily bars from MT5/yfinance to compute true daily ATR.
            # This prevents EOS-timeout failures by anchoring T1 to actual
            # daily price expansion rather than fixed risk multiples.
            # (Prompt req: T1 = Entry ± 0.15 × ATR_daily; skip if T2 > 0.5 × ATR_daily)
            _daily_atr = None
            try:
                _df_d = self._adapter.get_klines(symbol, '1d', 14)
                if _df_d is not None and len(_df_d) >= 2:
                    _hi = _df_d['high']
                    _lo = _df_d['low']
                    _cl = _df_d['close'].shift(1)
                    _tr = pd.concat([
                        _hi - _lo,
                        (_hi - _cl).abs(),
                        (_lo - _cl).abs(),
                    ], axis=1).max(axis=1)
                    _daily_atr = round(float(_tr.tail(14).mean()), 5)
                    logger.info(
                        f"FOREX {symbol}: daily ATR(14) = {_daily_atr:.4f} "
                        f"(T1≈±{_daily_atr*0.15:.4f}, T2max≈{_daily_atr*0.50:.4f})"
                    )
            except Exception as _atr_e:
                logger.debug(f"FOREX {symbol}: daily ATR fetch skipped: {_atr_e}")

            setup = _scan_setup(df, symbol, min_rr=MIN_RR, daily_atr=_daily_atr)
            if not setup:
                return
            try:
                _shadow_setup = dict(setup)
                _shadow_setup.setdefault('session', _current_session_label())
                _shadow_setup.setdefault('window', _current_session_label())
                _shadow_state = ftmo_load_state()
                recommend_shadow_for_candidate(
                    setup=_shadow_setup,
                    state=_shadow_state if isinstance(_shadow_state, dict) else {},
                    engine='forex_worker',
                    market='forex',
                    daily_loss_limit_abs=300.0,    # FTMO: $300/day limit
                    max_drawdown_abs=1000.0,       # FTMO: $1,000 EOD trailing DD
                    profit_target_abs=500.0,        # FTMO free trial: $500 target
                    max_trades_per_day=4,           # FTMO: 4 trades/day max
                )
                _unused_soft_gate_decision = evaluate_soft_gate_and_log(
                    setup=_shadow_setup,
                    state=_shadow_state if isinstance(_shadow_state, dict) else {},
                    engine='forex_worker',
                    market='forex',
                    daily_loss_limit_abs=300.0,    # FTMO: $300/day limit
                    max_drawdown_abs=1000.0,       # FTMO: $1,000 EOD trailing DD
                    max_trades_per_day=4,           # FTMO: 4 trades/day max
                )
                _ = _unused_soft_gate_decision
            except Exception:
                pass

            today = datetime.now().strftime('%Y-%m-%d')

            # H1 HTF bias — block counter-trend setups (Judas swing protection)
            # Exception: CHoCH score ≥ 11 overrides H1 EMA — CHoCH means structure
            # has already shifted, EMA is a lagging indicator that hasn't caught up.
            # RANGING means no clear H1 trend — allow trade but raise score gate by +1
            h1_bias    = _get_h1_bias(self._adapter, symbol)
            h1_ranging = (h1_bias == 'RANGING')
            mss_type_h1 = setup.get('mss_type', 'BOS')
            score_h1    = setup['confluence']
            choch_override = (mss_type_h1 == 'CHOCH' and score_h1 >= 11)

            if not h1_ranging and h1_bias != setup['direction'] and not choch_override:
                sig     = setup['entry_signal']
                fvg_key = round(sig['fvg_low'] * 1000) / 1000
                ema_key = (today, setup['direction'], fvg_key)
                if ema_key not in self._ema_alerted[symbol]:
                    self._ema_alerted[symbol].add(ema_key)
                    self._ema_alerted[symbol] = {k for k in self._ema_alerted[symbol] if k[0] == today}
                    msg = (
                        f"⚠️ <b>CB6 QUANTUM — EMA BLOCK</b>\n\n"
                        f"Symbol    : {symbol}\n"
                        f"15m Setup : {setup['direction']} {mss_type_h1} "
                        f"score={score_h1}/14\n"
                        f"H1 Bias   : {h1_bias} (EMA disagrees)\n"
                        f"Entry was : {sig['entry']} | SL {sig['stop_loss']}\n"
                        f"Reason    : Counter-trend — Judas swing risk\n"
                        f"Time      : {datetime.now().strftime('%H:%M:%S IST')}"
                    )
                    _send(msg)
                logger.info(
                    f"FOREX {symbol}: H1 {h1_bias} ≠ 15m {setup['direction']} "
                    f"— counter-trend, Judas swing risk — blocked"
                )
                return

            if choch_override and h1_bias != setup['direction']:
                logger.info(f"FOREX {symbol}: CHoCH {score_h1}/14 overrides H1 {h1_bias} EMA — structure shifted")
            else:
                logger.info(f"FOREX {symbol}: H1 bias {h1_bias} ✅")

            # ── H4 multi-day trend gate ────────────────────────────────────────
            # Live data shows: EVERY trade that fought the H4 trend lost.
            # Gold: all 4 SELL losses happened while H4 was BULLISH (uptrend).
            # Silver: only SELL loss was in Asia when H4 was ranging.
            # Rule: trade must align with H4 OR H4 must be RANGING.
            # Exception: CHoCH score ≥ 13 (very strong reversal = possible H4 flip)
            h4_bias = _get_h4_bias(self._adapter, symbol)
            if (h4_bias != 'RANGING'
                    and h4_bias != setup['direction']
                    and not (setup.get('mss_type') == 'CHOCH' and setup['confluence'] >= 13)):
                logger.info(
                    f"FOREX {symbol}: H4 BIAS BLOCK — H4 is {h4_bias}, "
                    f"setup is {setup['direction']} (score {setup['confluence']}/14) — skip"
                )
                _send(
                    f"<b>H4 TREND BLOCK — {symbol}</b>\n\n"
                    f"H4 bias  : {h4_bias}\n"
                    f"Setup    : {setup['direction']} {setup.get('mss_type','BOS')} "
                    f"score={setup['confluence']}/14\n"
                    f"Reason   : Counter multi-day trend — pattern shows 100% loss rate\n"
                    f"Need     : H4 aligned OR CHoCH score ≥13"
                )
                return

            # ── Regime context (Phase 3 intelligence — enriches ML records) ─────
            # Non-blocking: if archive has no data, falls back to UNKNOWN gracefully.
            _regime_ctx = {"market_regime": "UNKNOWN", "volatility_regime": "UNKNOWN",
                           "regime_adx": 0.0, "regime_strength": "NONE"}
            if _mi is not None:
                try:
                    _r4h = _mi.get_regime("FOREX", symbol, "4h")
                    _r1h = _mi.get_regime("FOREX", symbol, "1h")
                    _regime_ctx = {
                        "market_regime":    _r4h.regime,
                        "volatility_regime": _r4h.volatility,
                        "regime_adx":       round(_r4h.adx, 2),
                        "regime_strength":  _r4h.trend_strength,
                        "regime_1h":        _r1h.regime,
                    }
                    logger.info(
                        f"FOREX {symbol}: regime 4H={_r4h.regime}/{_r4h.trend_strength} "
                        f"1H={_r1h.regime} vol={_r4h.volatility} ADX={_r4h.adx:.1f}"
                    )
                except Exception:
                    pass
            setup.update(_regime_ctx)

            # ── Regime gate ───────────────────────────────────────────────────
            try:
                from utils.regime_gate import evaluate
                _rg = evaluate(
                    regime=_regime_ctx.get("market_regime", "UNKNOWN"),
                    volatility=_regime_ctx.get("volatility_regime", "UNKNOWN"),
                    direction=setup.get("direction", ""),
                    h4_trend=h4_bias,
                )
                if not _rg.allowed:
                    logger.info(f"FOREX {symbol}: REGIME BLOCK — {_rg.block_reason}")
                    _send(
                        f"<b>REGIME BLOCK — {symbol}</b>\n\n"
                        f"Regime   : {_regime_ctx.get('market_regime')} "
                        f"ADX={_regime_ctx.get('regime_adx')}\n"
                        f"Reason   : {_rg.block_reason}"
                    )
                    return
                if _rg.note and "no adj" not in _rg.note and "skipped" not in _rg.note:
                    logger.info(f"FOREX {symbol}: regime adjust — {_rg.note}")
                # Apply lot multiplier and risk mode override
                setup["regime_lot_mult"] = _rg.lot_multiplier
                setup["regime_score_boost"] = _rg.score_boost
                if _rg.risk_mode:
                    setup["risk_mode"] = _rg.risk_mode
            except Exception:
                setup["regime_lot_mult"]    = 1.0
                setup["regime_score_boost"] = 0

            # ── Silver Asia SELL block ────────────────────────────────────────
            # Silver trends bullish in Asian hours — SELL setups are usually Judas swings.
            # Exception: A+ score (≥13 incl CHoCH bonus) overrides — structure has genuinely shifted.
            _asia_aplus = (setup['confluence'] + (1 if setup.get('mss_type') == 'CHOCH' else 0)) >= 13
            if symbol == 'XAGUSD' and utc_hour < 7 and setup['direction'] == 'BEARISH' and not _asia_aplus:
                logger.info(
                    f"XAGUSD: Asia SELL block (hour={utc_hour} UTC) "
                    f"— silver bullish bias in 00-07 UTC. Override requires score ≥13."
                )
                return
            if symbol == 'XAGUSD' and utc_hour < 7 and setup['direction'] == 'BEARISH' and _asia_aplus:
                logger.info(
                    f"XAGUSD: Asia SELL block OVERRIDDEN — A+ score "
                    f"{setup['confluence']}/15 at hour={utc_hour} UTC"
                )

            # ── HARD BLOCK: Kill zone only ───────────────────────────────────
            # Outside London (07-12 UTC) and NY (16-20 UTC) = no trade.
            # Asia/overnight sessions have low volume, choppy price action.
            # All winning trades were in KZ. Losing outside KZ is guaranteed long-run.
            if not _in_kz:
                logger.info(
                    f"FOREX {symbol}: OUT-OF-KZ BLOCK ({utc_hour}:xx UTC) — "
                    f"no trades outside London/NY kill zones"
                )
                return

            # ── Sweep quality assessment (soft filter — mirrors NSE scoring) ────
            # ICT: sweep is a definitive prerequisite, but 15m micro-wicks can
            # evade algorithmic detection. Rather than a hard execution kill,
            # an absent/unconfirmed sweep raises the score gate by +2, forcing
            # the setup to prove itself through stronger CHoCH + FVG confluence.
            # Bug fixes applied here:
            #   Bug 1: replaced hard `return` with score-gate adjustment.
            #   Bug 2: replaced inline level_state == 'SWEPT' with the lenient
            #          sweep_confirmed() helper (allows level_state None or SWEPT).
            #   Bug 3: removed the arbitrary confidence < 45 hard block —
            #          a clean wick close-back-inside IS the sweep per ICT;
            #          ATR/volume filters on top kill valid quiet-KZ entries.
            liq_sweep_data = setup.get('liq_sweep')
            sweep_same_dir = _sweep_ok(
                liq_sweep_data,
                setup['direction'],
                max_candles_ago=15,
                min_confidence=0,
            )
            if not sweep_same_dir:
                logger.info(
                    f"FOREX {symbol}: NO CONFIRMED SWEEP — score gate raised +2. "
                    f"Setup: {setup['direction']} score={setup['confluence']}/18 "
                    f"(strong CHoCH+FVG required to compensate)"
                )

            # ── FVG proximity gate ────────────────────────────────────────────
            # Accept price IN the FVG (overlapping wick) OR within 0.5% of FVG mid
            # (near_fvg). Strict in_fvg-only blocked limit-order setups where price
            # is approaching but hasn't yet touched the zone — common Silver Bullet
            # entry mode (place limit at FVG edge, fill on touch).
            if not (setup.get('in_fvg') or setup.get('near_fvg', False)):
                logger.info(
                    f"FOREX {symbol}: NOT IN/NEAR FVG — price too far from imbalance zone. "
                    f"Setup: {setup['direction']} score={setup['confluence']}/18"
                )
                return

            # Score gate: tiered by session + H1 bias
            # Tier 1 — prime KZ     : sym_min        (07-10 UTC, 16-18 UTC)
            # Tier 2 — off-peak KZ  : sym_min + 1    (10-12 UTC, 18-20 UTC)
            # H1 ranging adds +1 on top (no clear HTF bias = need cleaner structure)
            sym_min       = SYMBOL_MIN_SCORE.get(symbol, MIN_SCORE)
            mss_type      = setup.get('mss_type', 'BOS')
            eff_score     = setup['confluence'] + (1 if mss_type == 'CHOCH' else 0)
            min_score_now = sym_min
            if not _is_prime_kz(utc_hour):
                min_score_now += 1   # off-peak KZ: stronger confirmation needed
            if h1_ranging:
                min_score_now += 1   # no clear H1 bias — require cleaner structure
            # NOTE: no gate penalty for absent sweep — the scoring engine already
            # penalises it naturally: sweep_confirmed=False ⟹ confluence −2 pts.
            # Adding another +2 to the gate creates a −4 effective gap that no
            # realistic setup can bridge, causing a permanent silent freeze.
            tier_label = 'prime-KZ' if _is_prime_kz(utc_hour) else 'off-peak-KZ'
            if eff_score < min_score_now:
                logger.info(
                    f"FOREX {symbol}: score {setup['confluence']} ({mss_type}) "
                    f"eff={eff_score} < {min_score_now} "
                    f"[{tier_label} H1={h1_bias}] — skip"
                )
                return

            # Dedup — one trade per FVG zone per day (persisted across restarts)
            sig     = setup['entry_signal']
            fvg_key = round(sig['fvg_low'] * 1000) / 1000
            dedup_k = (today, setup['direction'], fvg_key)
            if self._dedup.is_duplicate(symbol, setup['direction'], fvg_key):
                logger.info(f"FOREX {symbol}: dedup — already traded this zone today")
                return

            # A+ setup threshold — overrides soft blocks below
            # Score 13+ (including CHoCH +1 bonus) = all confluence aligned,
            # sweep confirmed, UT aligned, displacement — never skip these.
            _aplus = (setup['confluence'] + (1 if setup.get('mss_type') == 'CHOCH' else 0)) >= 13

            # ── IMPROVEMENT 2: Per-symbol session trade limit (max 1) ─────────
            # After 1 closed trade on a symbol in this session, stop — UNLESS
            # the new setup is A+ (score ≥13). A+ setups fire regardless.
            # Prevents whipsawing in choppy markets while keeping elite setups.
            _sess_start_h = 7 if utc_hour < 13 else 13
            _sess_start   = datetime.now(timezone.utc).replace(
                hour=_sess_start_h, minute=0, second=0, microsecond=0
            )
            _sess_start_s = _sess_start.astimezone().strftime('%Y-%m-%d %H:%M:%S')
            _sym_sess_trades = [
                t for t in state.get('closed_trades', [])
                if t.get('symbol') == symbol
                and (t.get('entry_time', '') or '') >= _sess_start_s
            ]
            if len(_sym_sess_trades) >= 1 and not _aplus:
                logger.info(
                    f"FOREX {symbol}: SESSION LIMIT — {len(_sym_sess_trades)} trade(s) this session, "
                    f"score {setup['confluence']}/14 not A+ — skip"
                )
                return
            if len(_sym_sess_trades) >= 1 and _aplus:
                logger.info(
                    f"FOREX {symbol}: SESSION LIMIT overridden — A+ setup "
                    f"(score {setup['confluence']}/14 + CHoCH) fires regardless"
                )

            # ── IMPROVEMENT 3: 90-min cooldown after any loss ─────────────────
            # After a loss on this symbol, wait 90 min — UNLESS the new setup
            # is A+. Prevents revenge entries in choppy/ranging markets.
            _last_loss = None
            for _t in reversed(state.get('closed_trades', [])):
                if _t.get('symbol') == symbol and _t.get('pnl_usd', 0) < 0:
                    _last_loss = _t
                    break
            if _last_loss:
                _loss_exit = _last_loss.get('exit_time', '')
                if _loss_exit:
                    try:
                        _loss_dt = datetime.strptime(_loss_exit, '%Y-%m-%d %H:%M:%S')
                        _elapsed = (datetime.now() - _loss_dt).total_seconds() / 60
                        if _elapsed < 90 and not _aplus:
                            logger.info(
                                f"FOREX {symbol}: COOLDOWN — last loss {_elapsed:.0f}min ago, "
                                f"score {setup['confluence']}/14 not A+ — "
                                f"{90-_elapsed:.0f}min remaining"
                            )
                            return
                        if _elapsed < 90 and _aplus:
                            logger.info(
                                f"FOREX {symbol}: COOLDOWN overridden — A+ setup "
                                f"(score {setup['confluence']}/14 + CHoCH) fires regardless"
                            )
                    except Exception:
                        pass

            # ── Risk mode gate ────────────────────────────────────────────────
            # Check internal prop-risk guards BEFORE opening any position.
            # 'paused'     → can_open_trade will block; log and return early.
            # 'aplus_only' → block non-A+ setups (save API calls to MT5).
            # 'reduced'    → proceed but halve lot size below.
            # 'normal'     → proceed unchanged.
            _risk_mode, _risk_reason = ftmo_get_risk_mode(state)
            if _risk_mode == 'paused':
                logger.info(f"FOREX {symbol}: RISK GUARD PAUSED — {_risk_reason}")
                if self._risk_alerted.get(symbol) != today:
                    self._risk_alerted[symbol] = today
                    _send(
                        f"🛑 <b>RISK GUARD — {symbol} PAUSED</b>\n\n"
                        f"{_risk_reason}\n"
                        f"No new entries until tomorrow or guard clears.\n"
                        f"Time: {datetime.now().strftime('%H:%M IST')}"
                    )
                return
            if _risk_mode == 'aplus_only' and not _aplus:
                logger.info(
                    f"FOREX {symbol}: RISK GUARD A+ ONLY — "
                    f"score {setup['confluence']}/14 not A+ — {_risk_reason}"
                )
                return
            if _risk_mode in ('aplus_only', 'reduced'):
                logger.info(f"FOREX {symbol}: Risk mode = {_risk_mode} — {_risk_reason}")

            cfg        = INSTRUMENTS.get(symbol, {})
            min_lot    = cfg.get('min_lot', 0.01)
            max_spread = cfg.get('max_spread')

            # ── USOIL volatility & gap filter ─────────────────────────────────
            # Oil gaps at session open and spikes on inventory data — extra guards.
            if symbol == 'USOIL':
                df_oil = self._candles.get(symbol)
                if df_oil is not None and len(df_oil) >= 3:
                    # Gap protection: block if current open gaps > $0.50 vs prev close
                    gap_thresh = cfg.get('gap_threshold', 0.50)
                    prev_close = float(df_oil['close'].iloc[-2])
                    curr_open  = float(df_oil['open'].iloc[-1])
                    gap        = abs(curr_open - prev_close)
                    if gap > gap_thresh:
                        logger.info(
                            f"USOIL: GAP BLOCK — gap ${gap:.2f} > threshold ${gap_thresh:.2f} "
                            f"(prev close {prev_close:.2f} → open {curr_open:.2f})"
                        )
                        return
                    # Volatility filter: block if ATR(5) > 3× min_sl_dist
                    try:
                        atr5    = float((df_oil['high'] - df_oil['low']).tail(5).mean())
                        atr_max = cfg.get('min_sl_dist', 0.50) * cfg.get('volatility_atr_max', 3.0)
                        if atr5 > atr_max:
                            logger.info(
                                f"USOIL: VOLATILITY BLOCK — ATR5 ${atr5:.2f} > max ${atr_max:.2f}"
                            )
                            return
                    except Exception:
                        pass

            # ── Expected RRR gate ─────────────────────────────────────────────
            # T2 distance / SL distance must meet minimum threshold.
            # At MIN_RR=3.0 this is always 3.0+, but log it for every trade.
            sl_dist_chk = abs(sig['entry'] - sig['stop_loss'])
            t2_dist_chk = abs(sig['target2'] - sig['entry'])
            entry_rrr   = round(t2_dist_chk / sl_dist_chk, 2) if sl_dist_chk > 0 else 0.0
            min_rrr     = FTMO_RISK_GUARD.get('min_entry_rrr', 2.0)
            if entry_rrr < min_rrr:
                logger.info(
                    f"FOREX {symbol}: RRR BLOCK — T2 RRR {entry_rrr:.2f} < min {min_rrr:.2f}"
                )
                return
            logger.info(f"FOREX {symbol}: expected RRR = {entry_rrr:.2f} (T2/{sl_dist_chk:.4f} SL) ✅")

            # ── A+ Similarity Score — lot boost if setup matches template ─────
            # Compare against the two A+ reference setups (May 21, 2026):
            # XAGUSD BULL 16:30 +$144, USOIL BEAR 17:30 +$107 — both 3T hit.
            # Boost is only applied in 'normal' risk mode — guards override boost.
            _df15_now = self._candles.get(symbol)
            _sim_ratio, _sim_bd = score_aplus_similarity(
                setup, _df15_now, h4_bias, h1_bias, utc_hour
            )
            _boost = _lot_boost_factor(_sim_ratio)
            _sim_hits = [k for k, v in _sim_bd.items() if v >= 1.0]
            _sim_partial = [k for k, v in _sim_bd.items() if 0 < v < 1.0]
            logger.info(
                f"FOREX {symbol}: A+ similarity {_sim_ratio:.0%} "
                f"hits=[{','.join(_sim_hits)}] partial=[{','.join(_sim_partial)}] "
                f"boost={_boost}×"
            )

            # ── Conviction evaluation (Phase 7) ──────────────────────────────
            # Must happen BEFORE lot sizing so grade affects position size.
            # Grade D → skip.  Grade C/B → lot reduction applied after sizing.
            # Hard block (CHOPPY) → skip.  A/A+ → no reduction (boost already applied).
            _session_label = (
                "london"    if 7  <= utc_hour < 12 else
                "new_york"  if 16 <= utc_hour < 20 else
                "off_session"
            )
            # Temporarily stamp sim_ratio so conviction technical scorer reads it
            setup['sim_ratio'] = _sim_ratio
            _conviction = None
            try:
                from utils.conviction_engine import evaluate_conviction
                _conviction = evaluate_conviction(
                    market    = 'FOREX',
                    symbol    = symbol,
                    direction = setup.get('direction', ''),
                    setup     = setup,
                    session   = _session_label,
                    regime_4h = _regime_ctx.get('market_regime'),
                )
                logger.info(
                    f"FOREX {symbol}: conviction={_conviction.conviction_score:.0f} "
                    f"grade={_conviction.conviction_grade} "
                    f"mult={_conviction.recommended_risk_multiplier}× "
                    f"block={_conviction.hard_block}"
                )
                if not _conviction.should_trade():
                    logger.info(
                        f"FOREX {symbol}: CONVICTION BLOCK — "
                        f"grade={_conviction.conviction_grade} "
                        f"score={_conviction.conviction_score:.0f} "
                        f"({_conviction.hard_block_reason or 'grade D — no edge'})"
                    )
                    _send(
                        f"⛔ <b>CONVICTION BLOCK — {symbol}</b>\n\n"
                        f"Grade  : {_conviction.conviction_grade} "
                        f"({_conviction.conviction_score:.0f}/100)\n"
                        f"Reason : {_conviction.hard_block_reason or 'Grade D — below minimum edge threshold'}\n"
                        f"Setup  : {setup.get('direction')} score={setup.get('confluence')}/15 "
                        f"sim={_sim_ratio:.0%}\n"
                        f"Time   : {datetime.now().strftime('%H:%M:%S IST')}"
                    )
                    return
            except Exception as _conv_e:
                logger.debug(f"FOREX {symbol}: conviction eval skipped: {_conv_e}")
                _conviction = None

            # Spread check — live mode only (paper has no real spread)
            if max_spread is not None:
                live_spread = self._adapter.get_spread(symbol)
                if live_spread is not None and live_spread > max_spread:
                    msg = (
                        f"⚠️ <b>SPREAD BLOCK — {symbol}</b>\n"
                        f"Live spread : {live_spread:.5f}\n"
                        f"Max allowed : {max_spread:.5f}\n"
                        f"Session     : {kz_label}\n"
                        f"Reason      : Spread too wide — news/low liquidity\n"
                        f"Time        : {datetime.now().strftime('%H:%M:%S IST')}"
                    )
                    logger.info(
                        f"FOREX {symbol}: SPREAD BLOCK "
                        f"({live_spread:.5f} > max {max_spread:.5f}) — skip"
                    )
                    _send(msg)
                    return
            # SAFE_VALIDATION_REVALIDATE_AUTO (Forex-only): ARMED -> wait cycle -> revalidate.
            if safe_mode_active:
                live_spread = self._adapter.get_spread(symbol)
                live_ltp = self._adapter.get_price(symbol)
                if live_ltp is None:
                    live_ltp = sig.get('entry')
                spread_pct = _compute_spread_pct(live_spread, live_ltp)
                proxy_snapshot = _get_proxy_snapshot(symbol)
                created = create_forex_signal(
                    setup=setup,
                    current_ltp=float(live_ltp),
                    config=self._forex_exec_cfg,
                    spread_pct=spread_pct,
                    proxy_snapshot=proxy_snapshot,
                )
                forex_signal_id = created.get('signal_id')
                if created.get('state') != SIGNAL_ARMED:
                    logger.info(
                        f"FOREX {symbol}: SAFE gate blocked {forex_signal_id} "
                        f"state={created.get('state')} reason={created.get('status_reason')}"
                    )
                    return
                self._armed_signals.add(forex_signal_id)
                logger.info(
                    f"FOREX {symbol}: signal {forex_signal_id} ARMED "
                    f"for {self._revalidate_cycle_secs}s"
                )
                time.sleep(self._revalidate_cycle_secs)

                armed = get_forex_signal(forex_signal_id)
                if not armed:
                    logger.warning(f"FOREX {symbol}: missing armed signal {forex_signal_id}")
                    self._armed_signals.discard(forex_signal_id)
                    return
                if armed.get('state') != SIGNAL_ARMED:
                    logger.info(
                        f"FOREX {symbol}: signal {forex_signal_id} state changed to "
                        f"{armed.get('state')} before revalidation"
                    )
                    self._armed_signals.discard(forex_signal_id)
                    return

                re_ltp = self._adapter.get_price(symbol)
                if re_ltp is None:
                    re_ltp = armed.get('planned_entry')
                re_spread = self._adapter.get_spread(symbol)
                re_spread_pct = _compute_spread_pct(re_spread, re_ltp)
                re_proxy = _get_proxy_snapshot(symbol)
                st2, rs2, sig2 = revalidate_forex_signal(
                    armed,
                    current_ltp=float(re_ltp),
                    config=self._forex_exec_cfg,
                    spread_pct=re_spread_pct,
                    proxy_snapshot=re_proxy,
                )
                if st2 != 'WAITING_FOR_MANUAL_CONFIRMATION':
                    update_forex_signal(
                        forex_signal_id,
                        st2,
                        rs2,
                        fields={
                            'current_ltp': re_ltp,
                            'spread_pct': re_spread_pct,
                            'proxy_note': sig2.get('proxy_note', ''),
                            'proxy_symbol': sig2.get('proxy_symbol'),
                            'entry_band_low': sig2.get('entry_band_low'),
                            'entry_band_high': sig2.get('entry_band_high'),
                            'signal_age_seconds': sig2.get('signal_age_seconds'),
                            'calculated_rr': sig2.get('calculated_rr'),
                        },
                    )
                    self._armed_signals.discard(forex_signal_id)
                    logger.info(
                        f"FOREX {symbol}: revalidation blocked {forex_signal_id} "
                        f"state={st2} reason={rs2}"
                    )
                    return
                update_forex_signal(
                    forex_signal_id,
                    SIGNAL_ARMED,
                    'REVALIDATION_PASS_AUTO_EXECUTE',
                    fields={
                        'current_ltp': re_ltp,
                        'spread_pct': re_spread_pct,
                        'proxy_note': sig2.get('proxy_note', ''),
                        'proxy_symbol': sig2.get('proxy_symbol'),
                        'entry_band_low': sig2.get('entry_band_low'),
                        'entry_band_high': sig2.get('entry_band_high'),
                        'signal_age_seconds': sig2.get('signal_age_seconds'),
                        'calculated_rr': sig2.get('calculated_rr'),
                    },
                )

            # ── FTMO Execution ─────────────────────────────────────────────────
            ftmo_trade = None
            # FTMO execution — runs unconditionally on each signal
            # Apply risk reduction factor when mode = 'reduced' or 'aplus_only'
            _risk_pct = RISK_PCT
            if _risk_mode in ('reduced', 'aplus_only'):
                _risk_pct = round(RISK_PCT * FTMO_RISK_GUARD.get('risk_reduction_factor', 0.5), 4)
                logger.info(f"FOREX {symbol}: REDUCED RISK — {_risk_pct:.3f}% (mode={_risk_mode})")
            elif _boost > 1.0:
                # A+ template match — boost risk proportionally (normal mode only)
                _risk_pct = round(RISK_PCT * _boost, 4)
                logger.info(
                    f"FOREX {symbol}: A+ BOOST {_boost}× "
                    f"({_sim_ratio:.0%} match) → risk={_risk_pct:.3f}% "
                    f"(base {RISK_PCT}% × {_boost})"
                )
            # REQ-6: Pull live MT5 equity for accurate lot sizing.
            # Falls back to state-file capital only if MT5 is unreachable.
            _live_capital = state.get('capital', 10000.0)
            if not self._paper:
                try:
                    _mt5_eq = self._adapter.get_equity()
                    if _mt5_eq and _mt5_eq > 0:
                        _live_capital = _mt5_eq
                        logger.debug(
                            f"FOREX {symbol}: lot sizing on live MT5 "
                            f"equity ${_mt5_eq:.2f}"
                        )
                except Exception:
                    logger.warning(
                        f"FOREX {symbol}: MT5 equity fetch failed — "
                        f"using state capital ${_live_capital:.2f}"
                    )
            ftmo_lots = calc_lot_size(symbol, _live_capital,
                                      sig['entry'], sig['stop_loss'], _risk_pct)
            # Apply regime lot multiplier (0.5 for RANGING+HIGH_VOL, 1.0 otherwise)
            _regime_mult = setup.get("regime_lot_mult", 1.0)
            if _regime_mult != 1.0:
                ftmo_lots = round(ftmo_lots * _regime_mult, 2)
                logger.info(f"FOREX {symbol}: regime lot adj ×{_regime_mult} → {ftmo_lots} lots")

            # ── Conviction lot adjustment (Phase 7) ──────────────────────────
            # Only reduces — never adds on top of existing A+ boost.
            # A+ boost already applied via _risk_pct above; conviction guards the downside.
            if _conviction is not None and _conviction.recommended_risk_multiplier < 1.0:
                _conv_mult = _conviction.recommended_risk_multiplier
                ftmo_lots  = max(min_lot, round(ftmo_lots * _conv_mult, 2))
                logger.info(
                    f"FOREX {symbol}: conviction grade={_conviction.conviction_grade} "
                    f"({_conviction.conviction_score:.0f}) → lots ×{_conv_mult} = {ftmo_lots}"
                )
            # Store conviction metadata for Telegram alert and DB capture
            setup['conviction_score'] = round(_conviction.conviction_score, 0) if _conviction else None
            setup['conviction_grade'] = _conviction.conviction_grade if _conviction else None
            setup['conviction_mult']  = _conviction.recommended_risk_multiplier if _conviction else 1.0

            ftmo_risk = dollar_risk(symbol, ftmo_lots, sig['entry'], sig['stop_loss'])
            sig['risk_usd'] = ftmo_risk
            # Attach metadata for trade record logging
            live_spread_at_entry = self._adapter.get_spread(symbol) if not self._paper else 0.0
            _regime_note = setup.get("regime_lot_mult", 1.0)
            setup['entry_reason']    = (
                f"{setup.get('mss_type','BOS')} score={setup['confluence']}/15 "
                f"H4={h4_bias} sim={_sim_ratio:.0%} boost={_boost}× mode={_risk_mode}"
                + (f" regime={_regime_ctx.get('market_regime','?')}" if _regime_ctx.get('market_regime') != 'UNKNOWN' else "")
            )
            setup['spread_at_entry'] = live_spread_at_entry or 0.0
            setup['risk_mode']       = _risk_mode
            setup['sim_ratio']       = _sim_ratio
            setup['lot_boost']       = _boost
            if ftmo_lots >= min_lot:
                with self._entry_lock:
                    fresh = ftmo_load_state()
                    # REQ-4: Deterministic daily reset — guarantees daily_pnl is
                    # always TODAY's figure, never stale from yesterday.
                    _prev_reset = fresh.get('last_reset_date', '')
                    fresh = ftmo_reset_daily(fresh)
                    if fresh.get('last_reset_date', '') != _prev_reset:
                        ftmo_save_state(fresh)
                        logger.info(
                            f"FTMO daily counters reset — period: "
                            f"{fresh.get('last_reset_date', '')}"
                        )
                    fresh_daily = fresh.get('daily_pnl', 0)  # guaranteed TODAY
                    if len(fresh.get('open_trades', [])) > 0:
                        logger.info(f"FTMO {symbol}: position already open")
                    elif fresh_daily >= (state.get('starting_capital', 10000.0)
                                         * FTMO_RISK_GUARD.get('daily_profit_stop_pct', 1.2) / 100):
                        # Profit protection cap — configurable via FTMO_RISK_GUARD
                        _pp_cap = round(state.get('starting_capital', 10000.0)
                                       * FTMO_RISK_GUARD.get('daily_profit_stop_pct', 1.2) / 100, 2)
                        logger.info(f"FTMO {symbol}: daily profit cap hit (${fresh_daily:.2f} ≥ ${_pp_cap:.2f})")
                        if self._bd_cap_alerted != today:
                            self._bd_cap_alerted = today
                            _send(
                                f"🛑 <b>CB6 QUANTUM — PROFIT CAP REACHED</b>\n\n"
                                f"Daily PnL : ${fresh_daily:.2f}\n"
                                f"Bot cap   : ${_pp_cap:.2f} (profit protection)\n"
                                f"No more entries today — locking in gains.\n"
                                f"Time      : {datetime.now().strftime('%H:%M:%S IST')}"
                            )
                    else:
                        ftmo_trade = ftmo_open_trade(setup, ftmo_lots)

            if ftmo_trade:
                self._dedup.mark_seen(symbol, setup['direction'], fvg_key)
                ticket = 0   # initialise here so it's always bound before line 1183
                if not self._paper:
                    ftmo_result = self._adapter.place_market_order(
                        symbol    = symbol,
                        direction = 'BUY' if setup['direction'] == 'BULLISH' else 'SELL',
                        lots      = ftmo_lots,
                        sl        = sig['stop_loss'],
                        tp        = sig['target2'],
                        magic     = _FTMO_MAGIC,
                    )
                    if not ftmo_result:
                        logger.error(f"FTMO {symbol}: MT5 order failed — rolling back")
                        ftmo_rollback(ftmo_trade['id'], ftmo_risk)
                        _send(f"⚠️ FTMO MT5 order FAILED for {symbol}. Check AutoTrading.")
                        ftmo_trade = None
                    else:
                        ticket = ftmo_result.get('ticket', 0)
                        if ticket:
                            ftmo_update_ticket(ftmo_trade['id'], ticket)
                            fill = ftmo_result.get('price')
                            if fill:
                                is_long  = setup['direction'] == 'BULLISH'
                                # Use ORIGINAL entry→SL distance so R:R stays consistent
                                # regardless of slippage between alert price and fill
                                sl_dist  = abs(sig['entry'] - sig['stop_loss'])
                                new_sl   = round(fill - sl_dist if is_long else fill + sl_dist, 5)
                                new_t1   = round(fill + sl_dist * 2 if is_long else fill - sl_dist * 2, 5)
                                new_t2   = round(fill + sl_dist * 3 if is_long else fill - sl_dist * 3, 5)
                                new_t3   = round(fill + sl_dist * 4 if is_long else fill - sl_dist * 4, 5)
                                new_risk = round(dollar_risk(symbol, ftmo_lots, fill, new_sl), 2)
                                ftmo_update_fill(ftmo_trade['id'], fill, new_sl, new_t1, new_t2, new_t3, new_risk)
                                # Slippage logging and high-slippage symbol tracking
                                slippage = abs(fill - sig['entry'])
                                max_slip = SYMBOL_MAX_SLIPPAGE.get(symbol, 999)
                                slip_flag = '⚠️ HIGH SLIPPAGE' if slippage > max_slip else 'OK'
                                logger.info(
                                    f"FTMO {symbol}: fill={fill:.5f} alert={sig['entry']:.5f} "
                                    f"slippage={slippage:.5f} [{slip_flag}] "
                                    f"spread_at_entry={live_spread_at_entry}"
                                )
                                if slippage > max_slip:
                                    _send(
                                        f"⚠️ <b>HIGH SLIPPAGE — {symbol}</b>\n\n"
                                        f"Alert entry : {sig['entry']:.5f}\n"
                                        f"MT5 fill    : {fill:.5f}\n"
                                        f"Slippage    : {slippage:.5f} (max {max_slip:.5f})\n"
                                        f"Spread      : {live_spread_at_entry}\n"
                                        f"Risk reduced next trade if this persists."
                                    )
                                # Modify the live MT5 order SL/TP to match fill-adjusted levels
                                if ticket and new_sl != sig['stop_loss']:
                                    try:
                                        self._adapter.modify_sl(symbol, ticket, new_sl)
                                        logger.info(
                                            f"FTMO {symbol}: fill {fill:.2f} vs alert {sig['entry']:.2f} "
                                            f"| SL adjusted {sig['stop_loss']:.2f}→{new_sl:.2f}"
                                        )
                                    except Exception as _me:
                                        logger.warning(f"FTMO {symbol}: SL modify failed: {_me}")
                                # Update sig so the Telegram alert shows actual fill levels
                                sig['entry']    = fill
                                sig['stop_loss']= new_sl
                                sig['target1']  = new_t1
                                sig['target2']  = new_t2
                                sig['target3']  = new_t3
                                sig['risk_usd'] = new_risk
                                ftmo_risk       = new_risk
                ftmo_ticket = ticket if ticket else (ftmo_trade.get('ticket', 0) if ftmo_trade else 0)
                _send(_format_entry_alert(setup, ftmo_lots, ftmo_risk, ticket=ftmo_ticket, platform='FTMO'))
                logger.info(f"FTMO {symbol}: trade opened {setup['direction']} {ftmo_lots}L @ {sig['entry']:.5f}")

                # ── ML price series (CNN/RNN) ──────────────────────────────
                try:
                    _df15 = self._candles.get(symbol)
                    if _df15 is not None and len(_df15) >= 5:
                        from ml.data_pipeline import save_price_series
                        _candle_list = [
                            {'open': float(r['open']), 'high': float(r['high']),
                             'low': float(r['low']),   'close': float(r['close']),
                             'volume': float(r.get('volume', r.get('tick_volume', 0)))}
                            for _, r in _df15.iterrows()
                        ]
                        save_price_series(
                            ftmo_trade.get('id', ''), 'forex', 'ftmo',
                            _candle_list, n_before=50
                        )
                        logger.debug(f"ML FTMO price series saved: {len(_candle_list)} candles")
                except Exception as _ml_e:
                    logger.debug(f"ML FTMO price series save skipped: {_ml_e}")

                # ── ML data capture ───────────────────────────────────────
                try:
                    from ml.forex_collector import record_entry as _ml_fx_entry
                    _ml_fx_entry(
                        trade     = ftmo_trade,
                        setup     = setup,
                        account   = 'ftmo',
                        lots      = ftmo_lots,
                        risk_usd  = ftmo_risk,
                        h1_bias   = setup.get('h1_bias', 'UNKNOWN'),
                        h4_bias   = setup.get('h4_bias', 'UNKNOWN'),
                        sim_ratio = setup.get('sim_ratio', 0.0),
                        lot_boost = setup.get('lot_boost', 1.0),
                        risk_mode = setup.get('risk_mode', 'normal'),
                    )
                except Exception as _ml_e:
                    logger.debug(f"ML FTMO entry capture skipped: {_ml_e}")

                # ── Trade replay + conviction context capture (Phase 3.5/7) ──
                try:
                    from utils.trade_replay import capture_entry_context
                    capture_entry_context(
                        trade_id  = ftmo_trade['id'],
                        market    = 'FOREX',
                        symbol    = symbol,
                        direction = setup.get('direction', ''),
                        setup     = setup,
                        session   = _session_label,
                    )
                except Exception as _rep_e:
                    logger.debug(f"Trade replay capture skipped: {_rep_e}")

                # ── ML shadow prediction ───────────────────────────────────
                try:
                    from ml.predictor import predict_forex
                    import numpy as np
                    _df15 = self._candles.get(symbol)
                    _candles_arr = None
                    if _df15 is not None and len(_df15) >= 5:
                        _candles_arr = _df15[['open','high','low','close','volume']].values.astype(float) \
                            if 'volume' in _df15.columns else \
                            _df15[['open','high','low','close']].assign(volume=0).values.astype(float)
                    predict_forex(
                        ftmo_trade.get('id', ''),
                        {**setup,
                         'h1_bias': setup.get('h1_bias','UNKNOWN'),
                         'h4_bias': setup.get('h4_bias','UNKNOWN'),
                         'aplus_sim_ratio': setup.get('sim_ratio', 0.0),
                         'aplus_lot_boost': setup.get('lot_boost', 1.0),
                         'is_aplus': 1 if setup.get('sim_ratio', 0) >= 0.55 else 0},
                        'ftmo',
                        candles=_candles_arr,
                    )
                except Exception as _ml_e:
                    logger.debug(f"ML FTMO shadow predict skipped: {_ml_e}")
                if safe_mode_active and forex_signal_id:
                    update_forex_signal(
                        forex_signal_id,
                        SIGNAL_EXECUTED,
                        'AUTO_REVALIDATION_EXECUTED',
                        fields={
                            'current_ltp': sig.get('entry'),
                            'spread_pct': _compute_spread_pct(live_spread_at_entry, sig.get('entry')),
                        },
                    )
                    self._armed_signals.discard(forex_signal_id)


            if not ftmo_trade:
                if safe_mode_active and forex_signal_id:
                    update_forex_signal(
                        forex_signal_id,
                        'BLOCKED',
                        'REVALIDATION_PASSED_BUT_EXECUTION_SKIPPED',
                    )
                    self._armed_signals.discard(forex_signal_id)
                return

        except Exception as e:
            logger.error(f"ForexWorker._run_scan({symbol}) error: {e}")
            if safe_mode_active and forex_signal_id:
                update_forex_signal(
                    forex_signal_id,
                    'BLOCKED',
                    f"EXECUTION_EXCEPTION:{e}",
                )
                self._armed_signals.discard(forex_signal_id)
        finally:
            self._locks[symbol].release()

    # ── Pre-rollover trade guard ────────────────────────────────────────────────

    def _pre_rollover_guard(self):
        """
        Fires at 21:55 UTC (5 min before rollover).
        Closes any open trade whose SL is within 3× current spread of price —
        i.e., SL so tight the spread explosion at 22:00 UTC would trigger it.
        Sends Telegram warning regardless.
        """
        state = ftmo_load_state()
        open_trades = state.get('open_trades', [])
        if not open_trades:
            return

        for trade in open_trades:
            sym    = trade['symbol']
            price  = self._adapter.get_price(sym)
            spread = self._adapter.get_spread(sym)
            sl     = trade.get('current_sl', trade.get('stop_loss', 0))
            if price is None:
                continue

            sl_distance = abs(price - sl)
            cfg         = INSTRUMENTS.get(sym, {})
            max_spread  = cfg.get('max_spread', 999)

            # If SL distance < 3× max spread → rollover will almost certainly stop us out
            danger = spread is not None and sl_distance < (max_spread * 3)
            msg = (
            f"⚠️ <b>ROLLOVER WARNING — {sym}</b>\n"
            f"Rollover in 5 min (22:00 UTC / 5PM EST)\n"
            f"Open trade  : {trade['direction']} @ {trade['entry_price']}\n"
            f"Current SL  : {sl}\n"
            f"SL distance : {sl_distance:.4f}  |  Max spread : {max_spread:.4f}\n"
            + ("🔴 <b>DANGER — SL too tight, closing now to protect DD floor</b>"
               if danger else
               "✅ SL distance safe — monitoring through rollover")
            )
            _send(msg)
            logger.info(f"FOREX {sym}: pre-rollover check — SL dist {sl_distance:.4f} danger={danger}")

            if danger and not self._paper:
                ticket = trade.get('ticket', 0)
                if ticket:
                    self._adapter.close_position(sym, ticket, trade['lots'], trade['direction'])
                    logger.info(f"FOREX {sym}: closed #{ticket} before rollover (tight SL)")

    # ── Position monitor ────────────────────────────────────────────────────────

    def _monitor_loop(self):
        _rollover_guard_fired = False   # fire once per rollover window
        while self._running:
            try:
                # REQ-3: Emergency stop — skip entire monitor cycle if flag is set
                if is_emergency_stop_active():
                    logger.warning(
                        "EMERGENCY_STOP.flag active — forex _monitor_loop cycle skipped"
                    )
                    time.sleep(MONITOR_SECS)
                    continue

                # Pre-rollover guard — fires once at 21:55 UTC
                if _approaching_rollover():
                    if not _rollover_guard_fired:
                        self._pre_rollover_guard()
                        _rollover_guard_fired = True
                else:
                    _rollover_guard_fired = False   # reset after window passes

                for sym in ACTIVE_SYMBOLS:
                    price = self._adapter.get_price(sym)
                    if price is None:
                        continue

                    # ── FTMO monitor ───────────────────────────────────────────
                    # REQ-2: Serialize with _run_scan entry path via _entry_lock.
                    # Prevents monitor and scan from interleaving on the same state.
                    with self._entry_lock:
                        events = ftmo_update_trades(price, symbol=sym)
                        for ev in events:
                            t          = ev['trade']
                            ticket     = t.get('ticket', 0)
                            close_lots = ev.get('close_lots', 0.0)
                            ev_type    = ev['type']
                            if ev_type == 'T1_BE':
                                # SL → BE only. No partial close sent to MT5.
                                if not self._paper and ticket:
                                    self._adapter.modify_sl(sym, ticket, t['entry_price'])
                            elif ev_type == 'BE_TRIGGER':
                                # Early break-even trigger (40% to T1). Move SL to entry.
                                if not self._paper and ticket:
                                    self._adapter.modify_sl(sym, ticket, t['entry_price'])
                                logger.info(
                                    f"FTMO {sym}: BE_TRIGGER — SL moved to entry "
                                    f"{t['entry_price']:.5f} @ price {ev['price']:.5f}"
                                )
                                _send(
                                    f"🛡️ <b>BREAK-EVEN TRIGGERED — {sym}</b>\n\n"
                                    f"Trade  : {t['direction']} @ {t['entry_price']:.5f}\n"
                                    f"Current: {ev['price']:.5f} (40% toward T1)\n"
                                    f"SL moved to entry — risk eliminated."
                                )
                            elif ev_type in ('MAE_EXIT', 'TIME_EXIT') and close_lots > 0:
                                # Emergency exits — close full position immediately
                                if not self._paper and ticket:
                                    self._adapter.close_position(sym, ticket, close_lots, t['direction'])
                                reason = ('Max Adverse Excursion — price reached 85% of SL'
                                          if ev_type == 'MAE_EXIT'
                                          else 'Time Exit — no progress in 2 hours')
                                logger.info(
                                    f"FTMO {sym}: {ev_type} exit @ {ev['price']:.5f} "
                                    f"pnl=${ev['pnl']:.2f} | {reason}"
                                )
                                _send(
                                    f"⏱️ <b>{ev_type} — {sym}</b>\n\n"
                                    f"Reason : {reason}\n"
                                    f"Entry  : {t['entry_price']:.5f}\n"
                                    f"Exit   : {ev['price']:.5f}\n"
                                    f"PnL    : ${ev['pnl']:.2f}\n"
                                    f"Actual RRR: {t.get('actual_rrr', 0.0):.2f}\n"
                                    f"Time   : {datetime.now().strftime('%H:%M IST')}"
                                )
                            elif ev_type in ('SL', 'T3', 'T2') and close_lots > 0:
                                if not self._paper and ticket:
                                    self._adapter.close_position(sym, ticket, close_lots, t['direction'])
                            elif ev_type == 'T1' and close_lots > 0:
                                if not self._paper and ticket:
                                    self._adapter.close_position(sym, ticket, close_lots, t['direction'])
                                    self._adapter.modify_sl(sym, ticket, t['entry_price'])
                            if ev_type not in ('BE_TRIGGER', 'MAE_EXIT', 'TIME_EXIT'):
                                _send(_format_exit_alert(ev, platform='FTMO'))

                            # ── ML outcome capture (all terminal exits) ───────
                            if ev_type in ('SL', 'T1', 'T2', 'T3', 'MAE_EXIT', 'TIME_EXIT'):
                                try:
                                    from ml.forex_collector import record_outcome as _ml_fx_out
                                    _ml_fx_out(
                                        trade       = t,
                                        account     = 'ftmo',
                                        exit_reason = ev_type,
                                        exit_price  = ev.get('price', t.get('exit_price', 0)),
                                        pnl_usd     = ev.get('pnl', t.get('pnl_usd', 0)),
                                    )
                                except Exception as _ml_e:
                                    logger.debug(f"ML FTMO outcome capture skipped: {_ml_e}")

                                # ── ML shadow monitor + auto-trainer ──────────
                                try:
                                    _pnl = ev.get('pnl', t.get('pnl_usd', 0)) or 0
                                    _res = 'WIN' if _pnl >= 0 else 'LOSS'
                                    _r   = t.get('r_multiple', 0) or 0
                                    from ml.shadow_monitor import on_trade_closed as _ml_otc
                                    _ml_otc(t.get('id',''), 'forex', 'ftmo', _res, float(_r))
                                    from ml.auto_trainer import check_and_train as _ml_ct
                                    _ml_ct('forex', 'ftmo')
                                except Exception as _ml_e:
                                    logger.debug(f"ML FTMO monitor/train skipped: {_ml_e}")

            except Exception as e:
                logger.error(f"Forex monitor loop error: {e}")
            time.sleep(MONITOR_SECS)

    # ── Heartbeat ───────────────────────────────────────────────────────────────

    def _heartbeat_loop(self):
        hb = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                      'data', 'forex_heartbeat.txt')
        while self._running:
            try:
                with open(hb, 'w') as f:
                    f.write(datetime.now().isoformat())
            except Exception as e:
                logger.warning(f"Forex heartbeat write failed: {e}")
            time.sleep(60)

    # ── Run ─────────────────────────────────────────────────────────────────────

    def run(self):
        self._running = True
        # C6 fix: reset daily counters at startup so caps always evaluate today's data,
        # not stale figures from a previous session (only resets if date has rolled over).
        _st = ftmo_load_state()
        _st = ftmo_reset_daily(_st)
        ftmo_save_state(_st)
        summary  = get_summary()
        starting = summary['starting']

        # Live mode: pull real MT5 balance as ground truth
        if not self._paper:
            try:
                mt5_bal = self._adapter.get_balance()
                mt5_eq  = self._adapter.get_equity()
                if mt5_bal and mt5_bal > 0:
                    display_bal    = round(mt5_bal, 2)
                    display_growth = round((mt5_bal - starting) / starting * 100, 2)
                    open_pnl       = round((mt5_eq or mt5_bal) - mt5_bal, 2)
                    src_label      = 'MT5'
                else:
                    raise ValueError("MT5 balance zero")
            except Exception:
                display_bal    = summary['capital']
                display_growth = summary['growth_pct']
                open_pnl       = 0.0
                src_label      = 'Paper (MT5 unavailable)'
        else:
            display_bal    = summary['capital']
            display_growth = summary['growth_pct']
            open_pnl       = 0.0
            src_label      = 'Paper'

        growth_s   = f"+{display_growth}%" if display_growth >= 0 else f"{display_growth}%"
        total_pnl  = round(display_bal - starting, 2)
        open_line  = f"  (open P&L ${open_pnl:+.2f})" if open_pnl != 0 else ""

        logger.info("=" * 55)
        logger.info("CB6 Quantum Forex Engine")
        logger.info(f"Mode      : {'Paper' if self._paper else 'LIVE — FTMO'}")
        logger.info(f"Balance   : ${display_bal} [{src_label}] (started ${starting}  {growth_s})")
        logger.info(f"Risk/trade: {RISK_PCT}% | Leverage: 1:{FTMO_RULES['leverage']}")
        logger.info(f"Symbols   : {', '.join(ACTIVE_SYMBOLS)}")
        logger.info(f"Timeframe : {INTERVAL}  (Silver Bullet — MT5 FTMO 15m validated)")
        logger.info(f"Sessions  : London 07-12 UTC | NY 16-20 UTC")
        logger.info(f"Exec Mode : {_FOREX_EXEC_MODE}")
        logger.info("=" * 55)

        _send(
            "<b>CB6 QUANTUM FOREX ENGINE STARTED</b>\n\n"
            f"Symbols  : {', '.join(ACTIVE_SYMBOLS)}\n"
            f"Mode     : {'Paper Trading' if self._paper else '🔴 LIVE — FTMO'}\n"
            f"Balance  : ${display_bal:,.2f}{open_line}  [{src_label}]\n"
            f"Total PnL: ${total_pnl:+.2f}  ({growth_s})\n"
            f"Risk     : {RISK_PCT}% per trade | 1:100 leverage\n"
            f"Strategy : ICT Silver Bullet · 15m · CHoCH/BOS → FVG → ATR targets\n"
            f"Sessions : London 07-12 UTC | NY 16-20 UTC\n"
            f"HTF Filter: H4+H1 EMA bias | Sweep+inFVG hard req.\n"
            f"Exec Mode: {_FOREX_EXEC_MODE}\n"
            f"Edge     : XAUUSD 46% WR PF3.63 | XAGUSD 53% WR PF5.34 | USOIL 69% WR PF8.43  (MT5 FTMO 2yr)"
        )

        threading.Thread(target=self._monitor_loop, daemon=True,
                     name="ForexMonitor").start()
        threading.Thread(target=self._heartbeat_loop, daemon=True,
                     name="ForexHeartbeat").start()

        # Poll for new candles — 3m candle duration requires frequent polling
        # Paper: 30s (yfinance delays; 5m fallback used — candle fires ~every 5m)
        # Live MT5: 15s (MT5 is real-time; 3m candle needs sub-candle poll)
        poll_secs = 30 if self._paper else 15
        self._adapter.start_polling(
            symbols          = ACTIVE_SYMBOLS,
            interval         = INTERVAL,
            on_closed_candle = self._on_closed_candle,
            poll_secs        = poll_secs,
        )

        logger.info("Forex engine running.")
        while self._running:
            time.sleep(10)

    def stop(self):
        self._running = False
        self._adapter.stop_polling()
        self._adapter.disconnect()
        logger.info("Forex engine stopped")
        _send("CB6 QUANTUM FOREX ENGINE STOPPED")


# ── Entry point ─────────────────────────────────────────────────────────────────

def main():
    # ── Startup guard 1: catch FTMO-specific disabled symbols ───────────────────
    from forex_engine.prop_firms.ftmo.ftmo_config import FTMO_DISABLED_SYMBOLS
    for _sym in ACTIVE_SYMBOLS:
        if _sym in FTMO_DISABLED_SYMBOLS:
            raise RuntimeError(
                f"STARTUP ABORT: {_sym} is in ACTIVE_SYMBOLS but listed in "
                f"FTMO_DISABLED_SYMBOLS {FTMO_DISABLED_SYMBOLS}. "
                f"Remove it from ACTIVE_SYMBOLS before running."
            )

    # ── Startup guard 2: FOREX_DISABLED_SYMBOLS (settings.py / .env override) ──
    # This setting is loaded from .env as FOREX_DISABLED_SYMBOLS (JSON list).
    # If the list is non-empty AND a symbol in ACTIVE_SYMBOLS appears in it,
    # refuse startup so the conflict is visible rather than silently ignored.
    _forex_disabled = [str(s).upper() for s in (FOREX_DISABLED_SYMBOLS or [])]
    for _sym in ACTIVE_SYMBOLS:
        if _sym.upper() in _forex_disabled:
            raise RuntimeError(
                f"STARTUP ABORT: {_sym} is in ACTIVE_SYMBOLS but also in "
                f"FOREX_DISABLED_SYMBOLS {_forex_disabled} (set via .env or settings.py default). "
                f"Either remove {_sym} from ACTIVE_SYMBOLS or clear FOREX_DISABLED_SYMBOLS in .env."
            )

    # Start Yahoo news monitor in background before anything else
    try:
        from data.forex_news_monitor import start_forex_news_monitor
        start_forex_news_monitor()
        logger.info("Forex news monitor started (CPI/NFP/FOMC detection active)")
    except Exception as _e:
        logger.warning(f"Forex news monitor failed to start: {_e}")

    worker = ForexWorker()

    # Wire adapters into Telegram bot for live price lookups + manual exit
    try:
        from communications.forex_bot import set_adapter as _set_adapter
        _set_adapter(worker._adapter)
    except Exception as _e:
        logger.warning(f"Could not set forex bot adapter: {_e}")

    # Start Telegram command listener
    try:
        from communications.forex_bot import start_listening as _fx_listen
        threading.Thread(target=_fx_listen, daemon=True,
                     name="ForexTGBot").start()
    except Exception as _e:
        logger.error(f"Forex bot listener failed: {_e}")

    try:
        worker.run()
    except KeyboardInterrupt:
        worker.stop()


if __name__ == '__main__':
    main()
