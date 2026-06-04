# config/strategy.py — Single source of truth for all tunable strategy parameters.
# Anything that affects WHEN we trade, HOW MUCH we risk, or WHAT we filter — lives here.
# Hot-reload: edit + restart bot. No code changes elsewhere needed.
from dataclasses import dataclass, field
from datetime import time
from typing import List, Tuple


@dataclass(frozen=True)
class StrategyConfig:
    # ── CAPITAL & RISK ─────────────────────────────────────────────────
    risk_per_trade_pct  : float = 1.0    # 1% of capital per trade (Rs 330 on 33k)
    max_loss_per_day    : int   = 3      # halt after 3 losing trades
    max_trades_per_day  : int   = 5      # max 5 trades per day
    max_open_trades     : int   = 1      # only 1 simultaneous position
    max_consecutive_losses: int = 3      # halt + alert after 3 consecutive losses
    max_daily_loss_pct  : float = 3.03   # equity-based halt (= Rs 1,000 on 33k)
    min_rr_ratio        : float = 3.0    # 1:3 minimum

    # ── SIGNAL QUALITY ─────────────────────────────────────────────────
    # Score gate lives in settings.py (MIN_BUY_SCORE=11) and is dynamically
    # adjusted per-setup by data/pattern_library.py (SCORE_GATE_HIGH).
    # Do not add a static gate here — it would conflict and be ignored.

    # ── TIMEFRAMES ─────────────────────────────────────────────────────
    # 60min only — 15min too noisy in Indian equity (proven by 18% WR data).
    # Macro bias requires W1 + D1 + H4 alignment before any 60min setup fires.
    timeframes          : Tuple[str, ...] = ('60',)

    # ── KILL ZONES (IST) ───────────────────────────────────────────────
    # Morning starts at 10:15 — skip the Judas swing window (9:45-10:15)
    morning_kz_start    : time = time(10, 15)
    morning_kz_end      : time = time(11, 30)
    afternoon_kz_start  : time = time(13, 30)
    afternoon_kz_end    : time = time(15, 0)

    # ── HARD MARKET WINDOWS ────────────────────────────────────────────
    market_open         : time = time(9, 15)
    market_close        : time = time(15, 30)
    square_off_time     : time = time(15, 15)
    no_entry_after      : time = time(15, 0)
    no_entry_before     : time = time(9, 30)

    # ── EVENT MODE THRESHOLDS ──────────────────────────────────────────
    vix_auto_on         : float = 25.0
    vix_auto_off        : float = 18.0
    nifty_abnormal_pct  : float = 2.5

    # ── FII/DII BIAS THRESHOLDS (Cr) ───────────────────────────────────
    fii_strong_buy_cr   : float = 500.0
    fii_strong_sell_cr  : float = -500.0
    dii_strong_buy_cr   : float = 500.0

    # ── REAL-TIME EXECUTION (WebSocket) ────────────────────────────────
    # Phase 1: scaffold complete, defaults OFF. Toggle via /ws on at runtime.
    # When ON: subscribes to ticks for aligned watchlist; tick triggers fire
    # SL/TP on open paper trades and pre-empt entry alerts.
    enable_websocket    : bool  = False
    ws_max_subscriptions: int   = 50    # Fyers tier dependent — 50 is safe


STRATEGY = StrategyConfig()


def reload() -> StrategyConfig:
    """Re-instantiate from current dataclass defaults. Called on /reload."""
    global STRATEGY
    STRATEGY = StrategyConfig()
    return STRATEGY
