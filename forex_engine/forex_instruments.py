# forex_engine/forex_instruments.py
#
# Instrument specs for CB6 Quantum Forex Engine.
# All values calibrated for FTMO MT5 accounts with 1:100 leverage.
#
# Lot size formula (universal):
#   lots = risk_usd / (contract_size * sl_distance_in_price)
#
# Where sl_distance_in_price = abs(entry - stop_loss) in raw price units.
# Works for forex (0.0050 = 50 pips) and commodities ($5 move on gold).

INSTRUMENTS = {
    'XAUUSD': {
        'label'        : 'Gold / USD',
        'contract_size': 100,
        'pip_size'     : 0.01,
        'point_size'   : 0.01,     # MT5 candle spread column is in points; 1 pt = $0.01
        'min_lot'      : 0.01,
        'max_lot'      : 50.0,
        'lot_step'     : 0.01,
        'min_sl_dist'  : 3.00,
        'min_fvg_size' : 2.00,     # $2 minimum FVG — filters weak Gold displacement
        'fvg_buf'      : 0.50,
        # FTMO: avg spread 40-55 pts = $0.40-0.55. Max $1.50 blocks only news spikes (NFP/FOMC).
        'max_spread'   : 1.50,
        'sessions'     : ['asia', 'london', 'ny'],
        'yf_ticker'    : 'GC=F',
        'mt5_symbol'   : 'XAUUSD',
        # ── Silver Bullet session windows (UTC) ──────────────────────────────
        # Validated in Dukascopy 2023-2026 backtest: 58.7% WR, +1805R, PF 2.93
        # London SB: 08-09 UTC (03:00-04:00 AM EST)
        # NY AM SB : 15-16 UTC (10:00-11:00 AM EST)
        'silver_bullet_windows_utc': [(8, 9), (15, 16)],
        'timeframe'    : '3m',     # 3-minute candles — backtest-validated TF
        # ── ATR-fractional target sizing ────────────────────────────────────
        # Prevents EOS-timeout failure: T1 anchored to 15% of daily ATR range
        # T2 runway check: skip if T2 > 50% of daily ATR (price can't get there)
        'atr_t1_factor'    : 0.15,   # T1 = Entry ± 0.15 × ATR_daily
        'atr_t2_max_factor': 0.50,   # compress/skip if T2 > 0.50 × ATR_daily
    },
    'XAGUSD': {
        'label'        : 'Silver / USD',
        'contract_size': 5000,
        'pip_size'     : 0.001,
        'point_size'   : 0.001,    # 1 pt = $0.001
        'min_lot'      : 0.01,
        'max_lot'      : 20.0,
        'lot_step'     : 0.01,
        'min_sl_dist'  : 0.05,
        'min_fvg_size' : 0.025,    # $0.025 minimum FVG — 50% of min SL
        'fvg_buf'      : 0.02,
        # FTMO: avg spread 60-80 pts = $0.06-0.08. Max $0.15 blocks news spikes.
        'max_spread'   : 0.15,
        'sessions'     : ['asia', 'london', 'ny'],
        'yf_ticker'    : 'SI=F',
        'mt5_symbol'   : 'XAGUSD',
        # MT5 FTMO 15m backtest 2024-2026: 53.4% WR, +154.87R, PF 5.34 — active
        'atr_t1_factor'    : 0.12,   # tighter than Gold — Silver moves slower intraday
        'atr_t2_max_factor': 0.40,
        'silver_bullet_windows_utc': [(15, 16)],  # NY AM only when re-enabled
    },
    'USOIL': {
        'label'        : 'WTI Crude Oil',
        'contract_size': 100,
        'pip_size'     : 0.01,
        'point_size'   : 0.001,    # 1 pt = $0.001
        'min_lot'      : 0.01,
        'max_lot'      : 0.50,     # capped lower — oil volatility risk
        'lot_step'     : 0.01,
        'min_sl_dist'  : 0.50,
        'min_fvg_size' : 0.25,     # $0.25 minimum FVG — 50% of min SL
        'fvg_buf'      : 0.05,
        # FTMO: avg spread 80-100 pts = $0.08-0.10. Max $0.20 blocks news spikes.
        'max_spread'   : 0.20,
        'sessions'     : ['london', 'ny'],  # Asia removed — oil choppy overnight
        'yf_ticker'    : 'CL=F',
        'mt5_symbol'   : 'USOIL.cash',
        # Volatility filter: skip entry if ATR(5) > this multiple of min_sl_dist
        'volatility_atr_max': 3.0,
        # Gap protection: block if open gaps more than this vs prev close
        'gap_threshold': 0.50,
        # Session open filter: skip first N candles after London/NY open (gap risk)
        'session_open_skip_candles': 2,
        # MT5 FTMO 15m backtest 2024-2026: 69.0% WR, +46.05R, PF 8.43 — active
        'silver_bullet_windows_utc': [(13, 17)],  # NY pit session when re-enabled
        'atr_t1_factor'    : 0.20,   # Oil moves more — 20% of daily ATR for T1
        'atr_t2_max_factor': 0.45,
    },
    'EURUSD': {
        'label'        : 'EUR / USD',
        'contract_size': 100000,
        'pip_size'     : 0.0001,
        'point_size'   : 0.00001,  # 5-decimal pricing; 1 pt = 0.1 pip = 0.00001
        'min_lot'      : 0.01,
        'max_lot'      : 100.0,
        'lot_step'     : 0.01,
        'min_sl_dist'  : 0.0010,
        'min_fvg_size' : 0.00030,  # 3 pips — allows valid setups in any session
        'fvg_buf'      : 0.0003,
        # Standard forex: max 2 pips = 0.0002
        'max_spread'   : 0.0002,
        'sessions'     : ['london', 'ny'],
        'yf_ticker'    : 'EURUSD=X',
        'mt5_symbol'   : 'EURUSD',
    },
    'AUDUSD': {
        'label'        : 'AUD / USD',
        'contract_size': 100000,
        'pip_size'     : 0.0001,
        'point_size'   : 0.00001,
        'min_lot'      : 0.01,
        'max_lot'      : 100.0,
        'lot_step'     : 0.01,
        'min_sl_dist'  : 0.0008,     # 8 pips
        'fvg_buf'      : 0.0002,
        'sessions'     : ['london', 'ny', 'asia'],
        'yf_ticker'    : 'AUDUSD=X',
        'mt5_symbol'   : 'AUDUSD',
    },
}

# ── FTMO Prop-Risk Guard Config ───────────────────────────────────────────────
# Risk: 0.7% per trade ($70/trade). Deadline: ~Jun 6, 2026. Target: +$500 (5%).
# FTMO hard limits: $300/day loss, $1,000 total DD, $250 best-day profit.
FTMO_RISK_GUARD = {
    # Daily loss tiers — at $70/trade: 2 losses=$140, 3=$210, 4=$280 (near $300 limit)
    # Guards kick in at $150 (2.1 losses), go A+-only at $200, stop at $250 (50 under limit)
    'daily_loss_reduce_pct'    : 1.5,   # $150 loss → 50% lots  (was 1.0/$100)
    'daily_loss_aplus_pct'     : 2.0,   # $200 loss → A+ only   (was 1.3/$130)
    'daily_loss_stop_pct'      : 2.5,   # $250 loss → stop today (was 1.5/$150 — FTMO limit $300)

    # Total drawdown tiers — scaled up for 0.7% risk
    'total_dd_reduce_pct'      : 5.0,   # $500 DD → 50% lots    (was 4.0/$400)
    'total_dd_aplus_pct'       : 7.0,   # $700 DD → A+ only     (was 6.0/$600)
    'total_dd_stop_pct'        : 8.5,   # $850 DD → halt         (was 7.5/$750 — FTMO limit $1,000)

    # Profit protection — let good days RUN. Stop at $240 (FTMO best-day cap is $250)
    'daily_profit_reduce_pct'  : 1.5,   # $150 profit → reduce lots (was 0.8/$80)
    'daily_profit_stop_pct'    : 2.4,   # $240 profit → stop today  (was 1.2/$120)

    # Best Day consistency — today ≤ 45% of all positive days combined
    'best_day_max_pct'         : 45.0,

    # Entry quality gate — unchanged (T2/SL must be ≥ 2R)
    'min_entry_rrr'            : 2.0,

    # Risk reduction multiplier when mode = 'reduced'
    'risk_reduction_factor'    : 0.50,

    # Max Adverse Excursion — exit at 85% of SL distance (cuts $70 loss to ~$60)
    'mae_exit_pct'             : 0.85,

    # Time-based exit — 2 hours max per trade
    'max_candles_no_progress'  : 8,     # 8 × 15m = 2 hours

    # Early break-even — move SL to entry when 40% of way to T1
    'be_trigger_pct'           : 0.40,
}

# Per-symbol max slippage thresholds — log warning if exceeded, flag for lot reduction
SYMBOL_MAX_SLIPPAGE = {
    'XAGUSD': 0.05,   # $0.05 — tight instrument, any more = bad fill
    'USOIL' : 0.20,   # $0.20 — wider at session opens
    'EURUSD': 0.0003, # 3 pips
    'XAUUSD': 0.80,   # $0.80 — FTMO avg spread 40-55pts, max blocks NFP/FOMC spikes
}

# ── FTMO Account Risk Rules ────────────────────────────────────────────────────
FTMO_RULES = {
    # Free Trial ($10K 1-Step)
    'free_trial': {
        'profit_target_pct' : 5.0,    # $500 target
        'max_total_dd_pct'  : 10.0,   # $1,000 EOD trailing
        'max_daily_loss_pct': 3.0,    # $300/day hard stop
        'best_day_rule_pct' : 50.0,   # best day profit <= 50% of target = $250
        'trading_days'      : 14,
    },
    # Challenge ($10K 1-Step) — €79
    'challenge': {
        'profit_target_pct' : 10.0,   # $1,000 target
        'max_total_dd_pct'  : 10.0,   # $1,000 EOD trailing
        'max_daily_loss_pct': 3.0,    # $300/day hard stop
        'best_day_rule_pct' : 50.0,   # best day profit <= 50% of target = $500
        'trading_days'      : None,   # unlimited
    },
    # Shared
    'leverage'           : 100,       # 1:100
    'risk_per_trade_pct' : 0.7,       # 0.7% per trade = $70/trade — sprint mode May 25-29
    'max_trades_per_day' : 4,         # at $70/trade: 4 losses = $280, guards stop at $250 anyway
}

# ── GFT (Goat Funded Trader) Rules ────────────────────────────────────────────
GFT_RULES = {
    # Instant Pro — user's actual accounts ($5,000 each, instantly funded, no evaluation)
    'instant_pro': {
        'profit_target_pct'  : None,   # no profit target — already funded
        'max_total_dd_pct'   : 6.0,    # static floor = 94% of starting ($4,700)
        'max_daily_loss_pct' : 4.0,    # $200/day — relative to 5PM EST equity snapshot
        'daily_profit_cap'   : 3000.0, # stop trading once $3,000 closed PnL reached today
        'trading_days'       : None,
    },
    # 1-Step model ($10K) — kept for reference
    '1_step': {
        'profit_target_pct'  : 8.0,
        'max_total_dd_pct'   : 6.0,
        'max_daily_loss_pct' : 4.0,
        'daily_profit_cap'   : 3000.0,
        'trading_days'       : None,
    },
    # Shared GFT params
    'leverage'            : 100,
    'risk_per_trade_pct'  : 1.0,       # 1% per trade — under GFT gambling filter
    'max_trades_per_day'  : 6,
    'min_trade_duration_s': 120,       # GFT flags trades closed in < 2 minutes
    'news_block_minutes'  : 5,         # block 5 min before/after red-folder news
    # GFT full kill zone windows (London + NY) matching gft_config.py
    # London: 07:00-12:00 UTC | NY: 16:00-20:00 UTC
    'kill_zone_windows_utc': [(7, 12), (16, 20)],
}

# Active GFT accounts — start with 1, add 2nd/3rd after $200+ first week
GFT_ACCOUNT_COUNT = 1
GFT_ACCOUNT_SIZE  = 5000.0

# ── Session windows (UTC hours) ────────────────────────────────────────────────
SESSIONS = {
    'asia'  : (0,  8),    # 00:00-08:00 UTC
    'london': (7,  16),   # 07:00-16:00 UTC
    'ny'    : (13, 21),   # 13:00-21:00 UTC
}


def calc_lot_size(instrument: str, account_balance: float,
                  entry: float, sl: float,
                  risk_pct: float = None) -> float:
    """
    Risk-based lot size calculator.
    Returns lots rounded to lot_step, clamped to [min_lot, max_lot].
    """
    cfg = INSTRUMENTS.get(instrument)
    if not cfg:
        return 0.0

    if risk_pct is None:
        risk_pct = FTMO_RULES['risk_per_trade_pct']

    risk_usd    = account_balance * risk_pct / 100
    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        return 0.0

    raw_lots = risk_usd / (cfg['contract_size'] * sl_distance)

    # Round to lot_step
    step = cfg['lot_step']
    lots = round(int(raw_lots / step) * step, 4)
    if lots < cfg['min_lot']:
        return 0.0          # risk-based size too small → caller skips trade
    lots = min(lots, cfg['max_lot'])
    return lots


def dollar_risk(instrument: str, lots: float, entry: float, sl: float) -> float:
    """Actual dollar risk for a given lot size and SL distance."""
    cfg = INSTRUMENTS.get(instrument)
    if not cfg:
        return 0.0
    return round(lots * cfg['contract_size'] * abs(entry - sl), 2)


def margin_required(instrument: str, lots: float, price: float,
                    leverage: int = 100) -> float:
    """Margin needed to hold this position at given leverage."""
    cfg = INSTRUMENTS.get(instrument)
    if not cfg:
        return 0.0
    return round(lots * cfg['contract_size'] * price / leverage, 2)


def is_in_session(instrument: str, utc_hour: int) -> bool:
    """True if current UTC hour falls inside any of the instrument's active sessions."""
    cfg = INSTRUMENTS.get(instrument, {})
    for sess_name in cfg.get('sessions', []):
        start, end = SESSIONS.get(sess_name, (0, 24))
        if start <= utc_hour < end:
            return True
    return False
