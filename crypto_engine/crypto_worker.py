# crypto_engine/crypto_worker.py
#
# CB6 Crypto Engine — BTC/USDT + ETH/USDT Perpetual Futures on Binance
#
# Strategy : ICT Silver Bullet adapted for 24/7 crypto markets
# Schedule : Mon–Fri all day & night | Rest Sat–Sun (low volume / bug fixing)
# Symbols  : BTCUSDT + ETHUSDT (combined WebSocket stream — single connection)
# Timeframe: 5-min candles
# Risk     : 5% of available capital per trade
#
# Run standalone : python -m crypto_engine.crypto_worker
# Run via orch   : launched as subprocess by orchestrator.py

import os
import sys
import time
import socket
import threading
from collections import deque
from datetime import datetime
from typing import Optional

# ── Single-instance lock ───────────────────────────────────────────────────────
# Binds to a local port so a second process fails fast instead of double-trading.
_INSTANCE_PORT = 47823
_instance_sock: socket.socket = None

def _acquire_instance_lock() -> bool:
    global _instance_sock
    try:
        _instance_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _instance_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        _instance_sock.bind(('127.0.0.1', _INSTANCE_PORT))
        return True
    except OSError:
        return False

import pytz
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from utils.logger import logger
from crypto_engine.binance_adapter import BinanceAdapter
from crypto_engine.crypto_paper_trader import (
    open_crypto_trade, update_crypto_trades,
    get_crypto_summary, load_state, save_state,
    rollback_open_trade, update_trade_sl_order, reconcile_pnl,
)
from crypto_engine.trade_memory import (
    record_trade_open, get_memory_score_boost,
)
from ml_engine.memory.shadow_logger import log_scanner_outcome

IST = pytz.timezone('Asia/Kolkata')

# ── Config ─────────────────────────────────────────────────────────────────────
INTERVAL      = '5m'
CANDLE_BUFFER = 200        # candles per symbol
MONITOR_SECS  = 30         # position monitor interval
RISK_PCT      = 5.0        # % of available capital at risk per trade
MIN_SCORE     = 7          # min score: CHoCH(7) or BOS+in_fvg(7) or BOS+displacement(7)
MIN_RR        = 2.5        # minimum RR ratio

# Per-symbol parameters fetched from Binance at startup, with safe defaults
# BTC disabled — needs >$10 margin per trade, capital is $8.4
# Re-enable BTCUSDT once capital grows above $50
SYMBOLS = {
    'ETHUSDT': {
        'lot_step': 0.001,
        'min_qty' : 0.001,
        'min_fvg' : 8.0,   # min SL dist $8.5 → margin ~$4.5 at 20x (fits $8.43 capital)
        'fvg_buf' : 1.5,
        'label'   : 'ETH/USDT',
    },
}

# 24/5 gate — Mon(0)–Fri(4) trade, Sat(5)–Sun(6) rest
def _is_trading_day() -> bool:
    return datetime.now(IST).weekday() < 5


# ── Telegram ───────────────────────────────────────────────────────────────────
CRYPTO_TG_TOKEN   = os.getenv('CRYPTO_TELEGRAM_TOKEN', '')
CRYPTO_TG_CHAT_ID = os.getenv('CRYPTO_TELEGRAM_CHAT_ID', '')

def _send(msg: str):
    if not CRYPTO_TG_TOKEN or not CRYPTO_TG_CHAT_ID:
        logger.info(f"[CRYPTO TG] {msg[:120]}")
        return
    try:
        import requests as _req
        _req.post(
            f"https://api.telegram.org/bot{CRYPTO_TG_TOKEN}/sendMessage",
            json={'chat_id': CRYPTO_TG_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=10
        )
    except Exception as e:
        logger.debug(f"Crypto TG send error: {e}")


# ── ICT scanner ────────────────────────────────────────────────────────────────

def _detect_order_block(df: pd.DataFrame, direction: str,
                         lookback: int = 40) -> Optional[dict]:
    """
    LuxAlgo-style Order Block using candle wicks (high/low), not body.
    Bull OB: last bearish candle (close < open) before the displacement that
             caused the most recent bullish FVG / structure break.
    Bear OB: last bullish candle (close > open) before the displacement.
    Returns zone dict or None.
    """
    try:
        recent = df.tail(lookback).reset_index(drop=True)
        n = len(recent)
        if n < 5:
            return None

        # Average range for displacement threshold
        avg_range = float((recent['high'] - recent['low']).mean())

        if direction == 'BULLISH':
            for i in range(n - 1, 2, -1):
                c = recent.iloc[i]
                # Displacement candle: bullish body + above-average range
                if (float(c['close']) > float(c['open']) and
                        (float(c['high']) - float(c['low'])) >= avg_range * 1.2):
                    # Walk back to find last bearish candle (OB uses wicks)
                    for j in range(i - 1, 0, -1):
                        prev = recent.iloc[j]
                        if float(prev['close']) < float(prev['open']):
                            return {
                                'ob_high': float(prev['high']),   # wick
                                'ob_low' : float(prev['low']),    # wick
                                'ob_mid' : (float(prev['high']) + float(prev['low'])) / 2,
                                'type'   : 'BULL_OB',
                            }
                    break
        else:
            for i in range(n - 1, 2, -1):
                c = recent.iloc[i]
                # Displacement candle: bearish body + above-average range
                if (float(c['close']) < float(c['open']) and
                        (float(c['high']) - float(c['low'])) >= avg_range * 1.2):
                    for j in range(i - 1, 0, -1):
                        prev = recent.iloc[j]
                        if float(prev['close']) > float(prev['open']):
                            return {
                                'ob_high': float(prev['high']),   # wick
                                'ob_low' : float(prev['low']),    # wick
                                'ob_mid' : (float(prev['high']) + float(prev['low'])) / 2,
                                'type'   : 'BEAR_OB',
                            }
                    break
        return None
    except Exception as e:
        logger.debug(f"OB detect error: {e}")
        return None


def _detect_three_bar_reversal(df: pd.DataFrame, direction: str) -> bool:
    """
    Three Bar Reversal pattern — uses candle wicks (high/low) for level checks.
    Bull: bar[-3] bearish → bar[-2] lower-low + lower-high bearish → bar[-1] bullish,
          breaks above bar[-3] HIGH (wick).
    Bear: bar[-3] bullish → bar[-2] higher-high + higher-low bullish → bar[-1] bearish,
          breaks below bar[-3] LOW (wick).
    """
    try:
        if len(df) < 3:
            return False
        b0 = df.iloc[-1]   # most recent
        b1 = df.iloc[-2]
        b2 = df.iloc[-3]

        if direction == 'BULLISH':
            return (
                float(b2['close']) < float(b2['open']) and        # b2 bearish body
                float(b1['low'])   < float(b2['low'])   and        # b1 lower wick low
                float(b1['high'])  < float(b2['high'])  and        # b1 lower wick high
                float(b1['close']) < float(b1['open'])  and        # b1 bearish body
                float(b0['close']) > float(b0['open'])  and        # b0 bullish body
                float(b0['high'])  > float(b2['high'])             # b0 wick breaks b2 high
            )
        else:
            return (
                float(b2['close']) > float(b2['open']) and        # b2 bullish body
                float(b1['high'])  > float(b2['high'])  and        # b1 higher wick high
                float(b1['low'])   > float(b2['low'])   and        # b1 higher wick low
                float(b1['close']) > float(b1['open'])  and        # b1 bullish body
                float(b0['close']) < float(b0['open'])  and        # b0 bearish body
                float(b0['low'])   < float(b2['low'])              # b0 wick breaks b2 low
            )
    except Exception as e:
        logger.debug(f"3BR detect error: {e}")
        return False


def scan_crypto_setup(df: pd.DataFrame, symbol: str) -> Optional[dict]:
    """
    Run ICT Silver Bullet chain on a 5-min crypto DataFrame.
    Works for any symbol — parameters tuned per symbol via SYMBOLS dict.

    Chain: DOL → MSS → FVG (wicks) → Price gate (wicks) →
           UT Bot trend filter → Order Block confluence → Three Bar Reversal
    """
    try:
        from scanner.silver_bullet import (
            find_draw_on_liquidity,
            detect_sb_mss,
            detect_sb_fvg,
            market_regime,
        )
        from scanner.ut_bot import get_ut_signal

        if df is None or len(df) < 40:
            return None

        # Skip choppy markets — backtest: CHOPPY (ADX<18) only 60% WR on 5-min
        regime = market_regime(df)
        if regime == 'CHOPPY':
            logger.info(f"{symbol}: SCAN — CHOPPY regime (ADX<18) — skip")
            return None

        cfg = SYMBOLS.get(symbol, {'min_fvg': 5.0, 'fvg_buf': 1.5})

        # 1. Draw on Liquidity — wick_sweep=True: wick touching level counts as swept
        dol = find_draw_on_liquidity(df, lookback=80, wick_sweep=True)
        if dol is None:
            logger.info(f"{symbol}: SCAN — no DOL found")
            return None
        logger.info(f"{symbol}: SCAN — DOL {dol['direction']} @ {dol['level']:.2f}")

        # 2. Market Structure Shift — MSS sets trade direction; DOL level used for T3 target only.
        #    DOL and MSS can disagree (e.g. sell-side swept then structure continues bearish).
        mss = detect_sb_mss(df, lookback=40)
        if mss is None:
            logger.info(f"{symbol}: SCAN — no MSS found")
            return None
        direction = mss['direction']   # MSS is the primary direction signal
        logger.info(f"{symbol}: SCAN — MSS {direction} {mss.get('type')} @ {mss['level']:.2f}"
                    f" (DOL={dol['direction']}{'✓' if dol['direction']==direction else '≠ override OK'})")

        # 3. Fair Value Gap — FVG uses candle wicks (high/low), not body
        #    use_range=True: displacement measured by full wick range
        fvg = detect_sb_fvg(df, direction, lookback=25, displacement_mult=1.0, use_range=True)
        if fvg is None:
            logger.info(f"{symbol}: SCAN — no FVG found after MSS")
            return None
        fvg_actual_size = fvg.get('size', 0)
        if fvg_actual_size < 1.0:
            logger.info(f"{symbol}: SCAN — FVG too small ({fvg_actual_size:.2f}) — skip")
            return None
        logger.info(f"{symbol}: SCAN — FVG {fvg['fvg_low']:.2f}–{fvg['fvg_high']:.2f} "
                    f"size=${fvg_actual_size:.2f} displaced={fvg.get('displacement')}")

        # 4. Price gate — use candle wicks (high/low), not body (close)
        last_low   = float(df['low'].iloc[-1])    # wick low
        last_high  = float(df['high'].iloc[-1])   # wick high
        last_close = float(df['close'].iloc[-1])
        fvg_low    = fvg['fvg_low']
        fvg_high   = fvg['fvg_high']
        fvg_mid    = fvg['mid']

        # Wick overlap with FVG zone OR price within 2% of midpoint
        in_fvg   = last_low <= fvg_high and last_high >= fvg_low
        near_fvg = abs(last_close - fvg_mid) / fvg_mid <= 0.02
        pct_away = round(abs(last_close - fvg_mid) / fvg_mid * 100, 2)
        if not (in_fvg or near_fvg):
            logger.info(f"{symbol}: SCAN — price {last_close:.2f} not at FVG "
                        f"{fvg_low:.2f}–{fvg_high:.2f} ({pct_away}% from mid)")
            return None
        logger.info(f"{symbol}: SCAN — price {'IN ZONE' if in_fvg else 'NEAR'} FVG "
                    f"close={last_close:.2f} wick={last_low:.2f}–{last_high:.2f} "
                    f"FVG={fvg_low:.2f}–{fvg_high:.2f}")

        # 5. Build trade plan
        min_fvg  = cfg['min_fvg']
        fvg_buf  = cfg['fvg_buf']
        fvg_size = max(fvg.get('size', min_fvg), min_fvg)

        if direction == 'BULLISH':
            entry = round(fvg_low + fvg_buf, 2)
            sl    = round(fvg_low - fvg_size, 2)
            risk  = round(entry - sl, 2)
            if risk <= 0:
                return None
            t1    = round(entry + risk * 2.0, 2)
            t2    = round(entry + risk * 3.0, 2)
            dol_l = dol['level']
            t3    = round(max(dol_l if dol_l > t2 else entry + risk * 4.0, t2), 2)
            rr    = round((t2 - entry) / risk, 1)
        else:
            entry = round(fvg_high - fvg_buf, 2)
            sl    = round(fvg_high + fvg_size, 2)
            risk  = round(sl - entry, 2)
            if risk <= 0:
                return None
            t1    = round(entry - risk * 2.0, 2)
            t2    = round(entry - risk * 3.0, 2)
            dol_l = dol['level']
            t3    = round(min(dol_l if dol_l < t2 else entry - risk * 4.0, t2), 2)
            rr    = round((entry - t2) / risk, 1)

        if rr < MIN_RR:
            return None

        # 6. UT Bot ATR trend filter — trade must align with ATR trailing stop direction
        try:
            ut = get_ut_signal(df)
            ut['aligned'] = (ut.get('trend') == direction)
        except Exception as ue:
            logger.debug(f"UT Bot error: {ue}")
            ut = {'trend': None, 'stop': None, 'signal': None,
                  'bars_in_trend': 0, 'aligned': None}

        # 7. Order Block — institutional zone using candle wicks (high/low)
        ob = _detect_order_block(df, direction, lookback=40)
        ob_confluence = False
        if ob:
            # OB overlaps with FVG zone → institutional confluence
            ob_confluence = (ob['ob_low'] <= fvg_high and ob['ob_high'] >= fvg_low)
            logger.info(f"{symbol}: SCAN — OB {ob['type']} "
                        f"{ob['ob_low']:.2f}–{ob['ob_high']:.2f} "
                        f"({'overlaps FVG ✓' if ob_confluence else 'no FVG overlap'})")

        # 8. Three Bar Reversal pattern — uses wicks for level comparisons
        three_bar = _detect_three_bar_reversal(df, direction)
        if three_bar:
            logger.info(f"{symbol}: SCAN — Three Bar Reversal confirmed ✓")

        # 9. Confluence score (max 14)
        mss_type   = mss.get('type', 'BOS')
        dol_agrees = (dol['direction'] == direction)
        score      = 5 if dol_agrees else 4                    # -1 when DOL/MSS disagree
        score   += 2 if mss_type == 'CHOCH' else 1            # CHoCH > BOS
        score   += 1 if in_fvg else 0                         # wick IN zone (not just near)
        score   += 1 if fvg.get('displacement') else 0        # displacement candle
        score   += 1 if rr >= 3.0 else 0                      # good RR
        score   += 2 if ut.get('aligned') else 0              # UT Bot agrees
        score   += 1 if ob_confluence else 0                  # OB overlaps FVG
        score   += 1 if three_bar else 0                      # 3-bar reversal pattern
        logger.info(f"{symbol}: SCAN — score {score}/14 "
                    f"(CHoCH={mss_type=='CHOCH'} inFVG={in_fvg} "
                    f"displ={fvg.get('displacement')} UT={ut.get('aligned')} "
                    f"OB={ob_confluence} 3BR={three_bar})")

        # Memory boost: ±1 based on historical win rate for this pattern
        setup_probe = {
            'mss_type'     : mss_type,
            'ob'           : ob,
            'ob_confluence': ob_confluence,
            'three_bar'    : three_bar,
            'ut_bot'       : ut,
        }
        mem_boost = get_memory_score_boost(setup_probe)
        if mem_boost != 0:
            score = max(0, min(14, score + mem_boost))
            logger.info(f"{symbol}: memory boost {mem_boost:+d} → score {score}/14")

        return {
            'symbol'      : symbol,
            'direction'   : direction,
            'window'      : '24/5',
            'confluence'  : score,
            'in_fvg'      : in_fvg,
            'mss_type'    : mss_type,
            'dol'         : dol,
            'mss'         : mss,
            'fvg'         : fvg,
            'ob'          : ob,
            'ob_confluence': ob_confluence,
            'three_bar'   : three_bar,
            'ut_bot'      : ut,
            'entry_signal': {
                'entry'    : entry,
                'stop_loss': sl,
                'target1'  : t1,
                'target2'  : t2,
                'target3'  : t3,
                'risk'     : risk,
                'rr_ratio' : rr,
                'fvg_low'  : round(fvg_low, 2),
                'fvg_high' : round(fvg_high, 2),
                'dol_level': round(dol['level'], 2),
                'mss_level': round(mss['level'], 2),
            },
        }

    except Exception as e:
        import traceback
        logger.error(f"scan_crypto_setup({symbol}) error: {e}\n{traceback.format_exc()}")
        return None


# ── Live entry gate ────────────────────────────────────────────────────────────

def _apply_live_entry(setup: dict, adapter: BinanceAdapter) -> Optional[dict]:
    """Replace FVG-boundary entry with live Binance mark price."""
    fvg      = setup.get('fvg', {})
    fvg_low  = fvg.get('fvg_low')
    fvg_high = fvg.get('fvg_high')
    symbol   = setup['symbol']
    if not fvg_low or not fvg_high:
        return setup

    mark = adapter.get_mark_price(symbol)
    if mark is None:
        logger.warning(f"{symbol}: mark price unavailable — skipping trade (live gate required)")
        return None

    mark = round(mark, 2)
    # Allow mark up to fvg_buf beyond the FVG edge to absorb REST poll timing slippage
    # (wick may have touched FVG on candle close but mark is fetched 0.5-1s later)
    buf = SYMBOLS.get(symbol, {}).get('fvg_buf', 1.5)
    if not (fvg_low - buf <= mark <= fvg_high + buf):
        logger.info(f"{symbol}: stale — mark {mark} outside FVG {fvg_low}–{fvg_high} (±{buf} buf)")
        return None

    direction = setup['direction']
    sig       = setup['entry_signal']
    sig['entry'] = mark
    risk = round(abs(mark - sig['stop_loss']), 2)
    if risk <= 0:
        return None
    sig['risk'] = risk

    dol = sig.get('dol_level')
    if direction == 'BULLISH':
        t1 = round(mark + risk * 2.0, 2)
        t2 = round(mark + risk * 3.0, 2)
        t3 = round(max(dol if (dol and dol > t2) else mark + risk * 4.0, t2), 2)
    else:
        t1 = round(mark - risk * 2.0, 2)
        t2 = round(mark - risk * 3.0, 2)
        t3 = round(min(dol if (dol and dol < t2) else mark - risk * 4.0, t2), 2)

    sig['target1']  = t1
    sig['target2']  = t2
    sig['target3']  = t3
    sig['rr_ratio'] = 3.0
    return setup


# ── Position sizing ────────────────────────────────────────────────────────────

def _calc_qty(available_usdt: float, entry: float, sl: float,
              lot_step: float, min_qty: float) -> float:
    """Risk-based sizing: RISK_PCT% of available capital."""
    risk_usdt = available_usdt * RISK_PCT / 100
    sl_dist   = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0
    qty   = risk_usdt / sl_dist
    steps = int(qty / lot_step)
    qty   = round(steps * lot_step, 3)
    return max(qty, min_qty)


# ── Alert formatters ───────────────────────────────────────────────────────────

def _format_entry_alert(setup: dict, qty: float) -> str:
    sig      = setup['entry_signal']
    sym      = setup['symbol']
    label    = SYMBOLS.get(sym, {}).get('label', sym)
    dlab     = 'LONG (BUY)' if setup['direction'] == 'BULLISH' else 'SHORT (SELL)'
    risk_usd = round(sig['risk'] * qty, 2)
    ut       = setup.get('ut_bot', {})
    ut_line  = f"{ut.get('trend','?')} | Stop: {ut.get('stop','?')} | {'✅' if ut.get('aligned') else '⚠️'}"
    ob       = setup.get('ob')
    ob_line  = f"{ob['ob_low']:.2f}–{ob['ob_high']:.2f} {'✅' if setup.get('ob_confluence') else ''}" if ob else "None"
    return (
        f"<b>CB6 CRYPTO — {label} [{setup['confluence']}/14]</b>\n\n"
        f"Direction  : {dlab}\n\n"
        f"<b>STRUCTURE</b>\n"
        f"DOL        : {sig['dol_level']}\n"
        f"MSS        : {sig['mss_level']} ({setup['mss_type']})\n"
        f"FVG Zone   : {sig['fvg_low']} – {sig['fvg_high']}\n"
        f"FVG Status : {'IN ZONE ✅' if setup.get('in_fvg') else 'APPROACHING'}\n"
        f"Order Block: {ob_line}\n"
        f"UT Bot     : {ut_line}\n"
        f"3-Bar Rev  : {'✅' if setup.get('three_bar') else '—'}\n\n"
        f"<b>TRADE PLAN</b>\n"
        f"Entry      : {sig['entry']}\n"
        f"SL         : {sig['stop_loss']}\n"
        f"T1 (1/3)   : {sig['target1']}  (1:2)\n"
        f"T2 (1/3)   : {sig['target2']}  (1:3)\n"
        f"T3 (1/3)   : {sig['target3']}  (DOL)\n"
        f"Risk/pt    : ${sig['risk']}  |  RR 1:{sig['rr_ratio']}\n"
        f"Qty        : {qty} {sym[:3]}  |  Risk ${risk_usd} USDT\n\n"
        f"Mode       : {'Paper' if os.getenv('CRYPTO_PAPER','true').lower()=='true' else '🔴 LIVE'} · 24/5"
    )


def _format_exit_alert(event: dict) -> str:
    t    = event['trade']
    typ  = event['type']
    pnl  = event['pnl']
    sign = '+' if pnl >= 0 else ''
    sym  = t.get('symbol', 'BTCUSDT')
    label = SYMBOLS.get(sym, {}).get('label', sym)
    return (
        f"<b>CB6 CRYPTO — {typ} HIT</b>\n\n"
        f"Symbol : {label}  {t['direction']}\n"
        f"Entry  : {t['entry_price']}\n"
        f"Hit @  : {event['price']}\n"
        f"PnL    : {sign}${pnl:.2f} USDT\n"
        f"Trade  : {t['id']}"
    )


# ── Main engine ────────────────────────────────────────────────────────────────

class CryptoWorker:
    def __init__(self):
        self._api_key    = os.getenv('BINANCE_API_KEY', '')
        self._api_secret = os.getenv('BINANCE_API_SECRET', '')
        self._paper      = os.getenv('CRYPTO_PAPER', 'true').lower() == 'true'

        self._adapter = BinanceAdapter(
            api_key    = self._api_key,
            api_secret = self._api_secret,
            paper      = self._paper,
        )

        # Per-symbol candle buffers and state
        self._candles     = {sym: deque(maxlen=CANDLE_BUFFER) for sym in SYMBOLS}
        self._buf_locks   = {sym: threading.Lock()            for sym in SYMBOLS}
        self._scan_locks  = {sym: threading.Lock()            for sym in SYMBOLS}
        self._last_ts     = {sym: 0                           for sym in SYMBOLS}
        self._dedup       = {sym: set()                       for sym in SYMBOLS}
        self._running     = False

        # Fetch live symbol info (lot step, min qty) from Binance at startup
        for sym in SYMBOLS:
            try:
                info = self._adapter.get_symbol_info(sym)
                SYMBOLS[sym]['lot_step'] = info['lot_step']
                SYMBOLS[sym]['min_qty']  = info['min_qty']
                logger.info(f"{sym}: lot_step={info['lot_step']} "
                            f"min_qty={info['min_qty']} "
                            f"price_precision={info['price_precision']}")
            except Exception as e:
                logger.warning(f"{sym}: symbol info fetch failed ({e}) — using defaults")

    # ── Buffer management ──────────────────────────────────────────────────────

    def _init_buffers(self):
        """Prime candle buffers with historical data before WS starts."""
        for sym in SYMBOLS:
            logger.info(f"Priming buffer: {sym} ({CANDLE_BUFFER} candles)")
            rows = self._adapter.get_klines(sym, INTERVAL, CANDLE_BUFFER)
            if rows:
                with self._buf_locks[sym]:
                    self._candles[sym].extend(rows)
                logger.info(f"{sym}: {len(self._candles[sym])} candles ready")
            else:
                logger.error(f"{sym}: historical data fetch failed — cold start")

    def _to_df(self, symbol: str) -> Optional[pd.DataFrame]:
        with self._buf_locks[symbol]:
            if len(self._candles[symbol]) < 40:
                return None
            rows = list(self._candles[symbol])
        df = pd.DataFrame(rows)
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df = df.set_index('datetime')
        return df[['open', 'high', 'low', 'close', 'volume']].astype(float)

    # ── WebSocket callback ─────────────────────────────────────────────────────

    def _on_closed_candle(self, symbol: str, candle: dict):
        """Called by multi-stream WS for each closed 5-min candle."""
        if symbol not in SYMBOLS:
            return
        ts = candle['timestamp']
        if ts == self._last_ts[symbol]:
            return   # duplicate close event

        self._last_ts[symbol] = ts
        with self._buf_locks[symbol]:
            self._candles[symbol].append(candle)

        t_str = datetime.fromtimestamp(ts / 1000, tz=IST).strftime('%H:%M')
        logger.info(f"{symbol} candle closed {t_str} | C={candle['close']}")
        # Run scan in a separate thread — never block the WS receive loop
        threading.Thread(target=self._run_scan, args=(symbol,),
                         daemon=True, name=f"Scan_{symbol}").start()

    # ── Scanner ────────────────────────────────────────────────────────────────

    def _run_scan(self, symbol: str):
        # Non-blocking lock: skip if a scan is already running for this symbol
        if not self._scan_locks[symbol].acquire(blocking=False):
            return
        try:
            state = load_state()
            if state.get('paused'):
                return

            # Weekend gate
            if not _is_trading_day():
                logger.info(f"{symbol}: weekend — skipping scan")
                return

            df = self._to_df(symbol)
            if df is None:
                return

            setup = scan_crypto_setup(df, symbol)
            if not setup:
                log_scanner_outcome('crypto', 'crypto_worker_scan', symbol, None, outcome='SCANNER_FAIL', reason='no_setup')
                return

            if setup['confluence'] < MIN_SCORE:
                logger.info(f"{symbol}: score {setup['confluence']} < {MIN_SCORE} — skip")
                log_scanner_outcome('crypto', 'crypto_worker_scan', symbol, setup, outcome='SCANNER_FAIL', reason='score_below_threshold')
                return
            log_scanner_outcome('crypto', 'crypto_worker_scan', symbol, setup, outcome='SCANNER_PASS')

            cfg = SYMBOLS[symbol]

            # Live price gate FIRST — only dedup after price is confirmed in FVG
            # Bug: deduping before gate means a stale setup blocks re-entry when price
            # returns to the FVG on a later candle
            setup = _apply_live_entry(setup, self._adapter)
            if setup is None:
                return

            # Dedup check — only READS here, key is added only after trade is confirmed open
            # (prevents a gate-blocked scan from poisoning the dedup for the rest of the day)
            fvg_key = round(setup['fvg']['fvg_low'] / 50) * 50
            today   = datetime.now().strftime('%Y-%m-%d')
            dedup_k = (today, setup['direction'], fvg_key)
            if dedup_k in self._dedup[symbol]:
                logger.info(f"{symbol}: dedup — already traded this FVG zone today")
                return

            sig = setup['entry_signal']
            available = load_state().get('available_capital', 1000)
            qty = _calc_qty(available, sig['entry'], sig['stop_loss'],
                            cfg['lot_step'], cfg['min_qty'])
            if qty < cfg['min_qty']:
                logger.info(f"{symbol}: qty {qty} below min — skip")
                return

            # Record trade in paper state first
            trade = open_crypto_trade(setup, qty)
            if not trade:
                logger.info(f"{symbol}: trade blocked by gate")
                return

            # Trade confirmed open — now lock the dedup so this FVG zone isn't re-entered today
            self._dedup[symbol].add(dedup_k)
            self._dedup[symbol] = {k for k in self._dedup[symbol] if k[0] == today}

            # Live mode: place real Binance orders
            if not self._paper:
                direction = setup['direction']
                entry_side = 'BUY' if direction == 'BULLISH' else 'SELL'
                sl_side    = 'SELL' if direction == 'BULLISH' else 'BUY'

                # 1. Market entry order
                entry_order = self._adapter.place_order(symbol, entry_side, qty,
                                                        lot_step=cfg['lot_step'])
                if not entry_order:
                    logger.error(f"{symbol}: entry order FAILED — rolling back paper trade")
                    rollback_open_trade(trade['id'])
                    return
                logger.info(f"{symbol}: entry order filled "
                            f"orderId={entry_order.get('orderId')} "
                            f"side={entry_side} qty={qty}")

                # 2. STOP_MARKET SL order (quantity + reduceOnly, not closePosition)
                sl_order = self._adapter.place_stop_market(symbol, sl_side, sig['stop_loss'], qty=qty)
                if sl_order:
                    update_trade_sl_order(trade['id'], sl_order.get('orderId'))
                    logger.info(f"{symbol}: SL order placed @ {sig['stop_loss']} "
                                f"orderId={sl_order.get('orderId')}")
                else:
                    logger.error(f"{symbol}: SL order failed — trade is unprotected!")

            # ── Trade memory snapshot ───────────────────────────────────────────
            # Volume ratio: displacement candle vol vs 20-bar average
            _vol_ratio  = None
            _sweep_type = 'WICK'
            try:
                if df is not None and len(df) >= 20:
                    avg_vol    = float(df['volume'].iloc[-20:].mean())
                    disp_vol   = float(df['volume'].iloc[-4])   # c1 displacement candle (approx)
                    if avg_vol > 0:
                        _vol_ratio = round(disp_vol / avg_vol, 2)
                # Sweep type: was DOL level breached by close or only by wick?
                dol_info = setup.get('dol', {})
                dol_lvl  = dol_info.get('level')
                dol_type = dol_info.get('type')
                if dol_lvl and dol_type and df is not None:
                    recent = df.tail(20)
                    if dol_type == 'HIGH':
                        _sweep_type = 'CLOSE' if any(recent['close'] > dol_lvl) else 'WICK'
                    else:
                        _sweep_type = 'CLOSE' if any(recent['close'] < dol_lvl) else 'WICK'
            except Exception:
                pass
            record_trade_open(trade['id'], setup, vol_ratio=_vol_ratio, sweep_type=_sweep_type)

            _send(_format_entry_alert(setup, qty))
            logger.info(f"{symbol} trade opened: {setup['direction']} "
                        f"{qty} {symbol[:3]} @ {sig['entry']}")

        except Exception as e:
            logger.error(f"_run_scan({symbol}) error: {e}")
        finally:
            self._scan_locks[symbol].release()

    # ── Real exit execution ────────────────────────────────────────────────────

    def _fetch_binance_pnl(self, trade: dict) -> None:
        """
        Query Binance for the actual realized PnL AND exit fill price after a close.
        Reconciles both pnl_usdt and exit_price in state so the dashboard shows
        real Binance data, not the software estimate.
        2s delay gives Binance time to settle income and fill records.
        """
        try:
            time.sleep(2)
            entry_ms = int(
                datetime.strptime(trade['entry_time'], '%Y-%m-%d %H:%M:%S').timestamp() * 1000
            )
            symbol    = trade['symbol']
            is_long   = trade['direction'] == 'BULLISH'
            close_side = 'SELL' if is_long else 'BUY'

            # 1. Realized PnL from income API
            entries = self._adapter.get_realized_pnl(symbol, since_ms=entry_ms, limit=20)
            total_pnl = None
            if entries is not None:
                total_pnl = round(sum(e['income'] for e in entries), 4)
                logger.info(
                    f"Binance realized PnL for {trade['id']}: ${total_pnl} "
                    f"({len(entries)} entr{'y' if len(entries) == 1 else 'ies'})"
                )

            # 2. Actual exit fill price from userTrades
            exit_price = None
            exit_time  = None
            fills = self._adapter.get_user_trades(symbol, since_ms=entry_ms, limit=20)
            if fills:
                # Closing fills = opposite side from entry, with non-zero realizedPnl
                closing = [f for f in fills
                           if f['side'] == close_side and f['realizedPnl'] != 0]
                if not closing:
                    # Fallback: any opposite-side fill (handles breakeven exits)
                    closing = [f for f in fills if f['side'] == close_side]
                if closing:
                    last = max(closing, key=lambda x: x['time'])
                    exit_price = last['price']
                    exit_time  = datetime.fromtimestamp(
                        last['time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                    logger.info(
                        f"Binance exit fill for {trade['id']}: "
                        f"price={exit_price} time={exit_time}"
                    )

            if total_pnl is not None or exit_price is not None:
                reconcile_pnl(
                    trade['id'],
                    actual_pnl = total_pnl if total_pnl is not None else trade.get('pnl_usdt', 0),
                    exit_price = exit_price,
                    exit_time  = exit_time,
                )
        except Exception as e:
            logger.error(f"_fetch_binance_pnl error: {e}")

    def _execute_real_exit(self, ev: dict):
        """
        Place real Binance MARKET close order for a SL/TP event.
        Also manages the Binance STOP_MARKET SL order:
          - T1: cancel old SL, place new SL at breakeven for 2/3 remaining qty
          - T2: no SL change (breakeven SL still covers remaining 1/3)
          - T3 / SL: cancel remaining SL order (position fully closed)
        After every full close (SL or T3), queries Binance income API for
        actual realized PnL and reconciles state if needed.
        """
        try:
            t        = ev['trade']
            symbol   = t['symbol']
            is_long  = t['direction'] == 'BULLISH'
            close_side = 'SELL' if is_long else 'BUY'
            ev_type  = ev['type']
            qty_full = t['qty_btc']
            partial  = round(qty_full / 3, 3)

            cfg      = SYMBOLS.get(symbol, {'lot_step': 0.001, 'min_qty': 0.001})
            lot_step = cfg['lot_step']
            min_qty  = cfg['min_qty']

            if ev_type in ('T1', 'T2', 'T3'):
                # Partial close — 1/3 of original qty at each target
                close_qty = max(round(int(partial / lot_step) * lot_step, 3), min_qty)
            else:
                # SL — close remaining qty (full minus already-booked partials)
                booked    = len(t.get('targets_hit', []))
                remaining = round(qty_full - booked * partial, 3)
                close_qty = max(round(int(remaining / lot_step) * lot_step, 3), min_qty)

            # Manage Binance SL order first (before placing market close)
            sl_order_id = t.get('sl_order_id')
            if ev_type == 'SL' and sl_order_id:
                # For SL events: try to cancel the Binance STOP_MARKET order.
                # If cancel fails, Binance already triggered it and the position is
                # already closed — skip the redundant market close to avoid a 400.
                sl_cancel_ok = self._adapter.cancel_order(symbol, sl_order_id)
                if not sl_cancel_ok:
                    logger.info(f"{symbol}: SL order {sl_order_id} already triggered by exchange "
                                f"— skipping redundant market close")
                    # Position already closed by Binance — fetch actual PnL in background
                    threading.Thread(target=self._fetch_binance_pnl, args=(t,),
                                     daemon=True, name="PnLFetch").start()
                    return
            elif sl_order_id:
                if ev_type == 'T1':
                    # Trail SL to breakeven — cancel old, place new (2/3 qty remaining after T1)
                    self._adapter.cancel_order(symbol, sl_order_id)
                    new_sl_side = 'SELL' if is_long else 'BUY'
                    be_qty = max(round(int((qty_full - partial) / lot_step) * lot_step, 3), min_qty)
                    be_order = self._adapter.place_stop_market(
                        symbol, new_sl_side, t['entry_price'], qty=be_qty)
                    if be_order:
                        update_trade_sl_order(t['id'], be_order.get('orderId'))
                        logger.info(f"{symbol}: SL trailed to breakeven "
                                    f"@ {t['entry_price']} orderId={be_order.get('orderId')}")
                elif ev_type == 'T3':
                    # Position fully closed — cancel any remaining SL order
                    self._adapter.cancel_order(symbol, sl_order_id)

            # Place market close
            order = self._adapter.place_order(symbol, close_side, close_qty,
                                              reduce_only=True, lot_step=lot_step)
            if order:
                logger.info(f"{symbol}: real exit {ev_type} {close_side} "
                            f"{close_qty} orderId={order.get('orderId')}")
                # For full closes, fetch actual Binance PnL and reconcile in background
                if ev_type in ('SL', 'T3'):
                    threading.Thread(target=self._fetch_binance_pnl, args=(t,),
                                     daemon=True, name="PnLFetch").start()
            else:
                logger.error(f"{symbol}: real exit {ev_type} FAILED")

        except Exception as e:
            logger.error(f"_execute_real_exit({ev.get('type')}) error: {e}")

    # ── Position monitor ───────────────────────────────────────────────────────

    def _monitor_loop(self):
        """Every 30s: check all open positions for SL/TP hits, per symbol."""
        while self._running:
            try:
                state = load_state()
                syms_needed = {t['symbol'] for t in state.get('open_trades', [])}

                # ── Bug fix: detect positions Binance already closed ──────────
                # If a Binance SL order fires (or position is manually closed)
                # before the monitor polls, state still shows the trade as OPEN.
                # The next mark-price check would then wrongly log a software SL
                # at the stale mark price. Check Binance reality first.
                if not self._paper and syms_needed:
                    bnb_positions = self._adapter.get_open_positions(list(syms_needed))
                    if bnb_positions is not None:
                        for trade in list(state.get('open_trades', [])):
                            sym = trade['symbol']
                            bnb_pos = bnb_positions.get(sym, {})
                            if bnb_pos.get('qty', 0) == 0:
                                # Binance has no open position — closed externally
                                logger.info(
                                    f"{sym}: position is FLAT on Binance but OPEN in state "
                                    f"— fetching actual PnL and closing"
                                )
                                # Close in state using SL level as best-guess exit price;
                                # _fetch_binance_pnl will correct with actual fill price
                                from crypto_engine.crypto_paper_trader import update_crypto_trades as _uct
                                # Use SL as exit price approximation (better than mark)
                                _uct(trade['current_sl'], symbol=sym)
                                threading.Thread(
                                    target=self._fetch_binance_pnl, args=(trade,),
                                    daemon=True, name="PnLFetch_ExternalClose"
                                ).start()
                                _send(
                                    f"<b>CB6 CRYPTO — External Close Detected</b>\n"
                                    f"Symbol: {sym}\n"
                                    f"Binance position = FLAT (SL or manual close)\n"
                                    f"Fetching actual PnL from Binance..."
                                )

                # Fetch and cache mark prices in state so dashboard can show live PnL
                mark_prices = {}
                for sym in syms_needed:
                    mark = self._adapter.get_mark_price(sym)
                    if mark:
                        mark_prices[sym] = mark
                if mark_prices:
                    state = load_state()
                    state['mark_prices'] = mark_prices
                    save_state(state)

                had_closes = False
                for sym, mark in mark_prices.items():
                    events = update_crypto_trades(mark, symbol=sym)
                    for ev in events:
                        if not self._paper:
                            self._execute_real_exit(ev)
                        _send(_format_exit_alert(ev))
                        if ev['type'] in ('SL', 'T3'):
                            had_closes = True

                # After any full close, re-sync equity from Binance if no positions remain
                if had_closes and not self._paper:
                    remaining = load_state().get('open_trades', [])
                    if not remaining:
                        self._sync_balance_from_binance()
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
            time.sleep(MONITOR_SECS)

    # ── Heartbeat ──────────────────────────────────────────────────────────────

    def _heartbeat_loop(self):
        hb = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                          'data', 'crypto_heartbeat.txt')
        while self._running:
            try:
                with open(hb, 'w') as f:
                    f.write(datetime.now().isoformat())
            except Exception:
                pass
            time.sleep(60)

    # ── Run ────────────────────────────────────────────────────────────────────

    def _sync_balance_from_binance(self):
        """
        Pull real USDT balance from Binance and write it to state.
        Called at startup and after every full close when no positions remain.
        This is the single source of truth — overrides any accumulated rounding error.
        """
        if self._paper:
            return
        try:
            real_balance = self._adapter.get_usdt_balance()
            if not real_balance:
                return
            state = load_state()
            open_count = len(state.get('open_trades', []))
            old_avail  = state.get('available_capital', 0)
            old_equity = state.get('capital', 0)

            # Always update available_capital to Binance reality
            state['available_capital'] = round(real_balance, 4)

            # When no positions open, equity = available balance
            if open_count == 0:
                state['capital'] = round(real_balance, 4)

            # Seed starting_capital only on very first sync (never overwrite)
            if 'starting_capital' not in state:
                state['starting_capital'] = round(real_balance, 4)

            from crypto_engine.crypto_paper_trader import _sync_equity
            _sync_equity(state)
            save_state(state)
            logger.info(
                f"Balance synced from Binance: ${real_balance:.4f} USDT "
                f"(was avail=${old_avail:.4f} equity=${old_equity:.4f})"
            )
        except Exception as e:
            logger.error(f"Balance sync error: {e}")

    def run(self):
        if not _acquire_instance_lock():
            logger.error("Crypto engine already running (port 47823 busy) — exiting duplicate")
            return

        self._running = True

        # Sync state from real Binance balance before doing anything else
        self._sync_balance_from_binance()

        _st = load_state()
        _equity   = _st.get('capital', 1000)
        _starting = _st.get('starting_capital', _equity)
        _growth   = round((_equity - _starting) / _starting * 100, 1) if _starting else 0.0
        _growth_s = f"+{_growth}%" if _growth >= 0 else f"{_growth}%"
        logger.info("=" * 55)
        logger.info("CB6 Crypto Engine — ETH/USDT Perpetual (LIVE)")
        logger.info(f"Mode      : {'Paper' if self._paper else 'LIVE'}")
        logger.info(f"Equity    : ${_equity} USDT  (started ${_starting}  {_growth_s})")
        logger.info(f"Risk/trade: {RISK_PCT}% of equity per trade")
        logger.info(f"Schedule  : 24/5 — Mon–Fri all day, rest Sat–Sun")
        logger.info(f"Symbols   : {', '.join(SYMBOLS.keys())}")
        logger.info("=" * 55)

        _send(
            "<b>CB6 CRYPTO ENGINE STARTED</b>\n\n"
            "Symbol   : ETH/USDT Perpetual\n"
            f"Mode     : {'Paper Trading' if self._paper else '🔴 LIVE TRADING'}\n"
            f"Equity   : ${_equity} USDT (started ${_starting}  {_growth_s})\n"
            f"Risk     : {RISK_PCT}% of equity per trade\n"
            "Schedule : Mon–Fri 24hrs · Rest Sat–Sun\n"
            "Strategy : ICT Silver Bullet · 5-min"
        )

        # 1. Prime buffers
        self._init_buffers()

        # 2. Position monitor
        threading.Thread(target=self._monitor_loop, daemon=True,
                         name="CryptoMonitor").start()

        # 3. Heartbeat
        threading.Thread(target=self._heartbeat_loop, daemon=True,
                         name="CryptoHeartbeat").start()

        # 4. Combined WebSocket for all symbols
        self._adapter.start_multi_stream(
            symbols          = list(SYMBOLS.keys()),
            interval         = INTERVAL,
            on_closed_candle = self._on_closed_candle,
        )

        # 6. Fallback REST poll — scans if WS silent > 90s
        # Runs every 30s so entries are within 30s of candle close if WS is down.
        logger.info("Crypto engine running. REST fallback poll every 30s.")
        _last_polled = {sym: 0 for sym in SYMBOLS}
        while self._running:
            time.sleep(30)
            try:
                now_ms = time.time() * 1000
                for sym in SYMBOLS:
                    last_ws = self._last_ts[sym]
                    ws_silent = (now_ms - last_ws) > 90_000   # 90s — WS should fire within 5min
                    if ws_silent:
                        # Fetch latest candles via REST
                        rows = self._adapter.get_klines(sym, INTERVAL, 50)
                        if not rows:
                            continue
                        latest_ts = rows[-1]['timestamp']
                        if latest_ts == _last_polled[sym]:
                            continue   # no new closed candle since last poll
                        with self._buf_locks[sym]:
                            buf_latest = self._candles[sym][-1]['timestamp'] if self._candles[sym] else 0
                            new_rows = [r for r in rows if r['timestamp'] > buf_latest]
                            for r in new_rows:
                                self._candles[sym].append(r)
                        _last_polled[sym] = latest_ts
                        if not new_rows:
                            continue
                        logger.info(f"{sym}: REST poll — new candle ts={latest_ts}")
                        threading.Thread(target=self._run_scan, args=(sym,),
                                         daemon=True, name=f"FallbackScan_{sym}").start()
            except Exception as e:
                logger.error(f"Fallback poll error: {e}")

    def stop(self):
        self._running = False
        self._adapter.stop_kline_stream()
        # Release the instance-lock port so a new process can start immediately
        global _instance_sock
        try:
            if _instance_sock:
                _instance_sock.close()
                _instance_sock = None
        except Exception:
            pass
        logger.info("Crypto engine stopped")
        _send("CB6 CRYPTO ENGINE STOPPED")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    worker = CryptoWorker()

    # Wire adapter into the Telegram bot so /btc_status etc. can fetch live prices
    try:
        from communications.bot_crypto import set_adapter as _set_adapter
        _set_adapter(worker._adapter)
    except Exception as _e:
        logger.warning(f"Could not set Telegram bot adapter: {_e}")

    # Start Telegram command listener (works whether launched via orchestrator or directly)
    try:
        from communications.bot_crypto import start_listening as _crypto_listen
        threading.Thread(target=_crypto_listen, daemon=True,
                         name="CryptoTGBot").start()
        logger.info("Crypto Telegram bot listener started")
    except Exception as _e:
        logger.error(f"Crypto bot listener failed to start: {_e}")

    try:
        worker.run()
    except KeyboardInterrupt:
        worker.stop()


if __name__ == '__main__':
    main()
