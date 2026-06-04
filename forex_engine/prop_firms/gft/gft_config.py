# forex_engine/prop_firms/gft/gft_config.py
# GFT $5,000 2-Step GOAT account configuration.
# Completely isolated from FTMO config and Instant Pro config.

# ── GFT $5K 2-Step GOAT — Account Spec ────────────────────────────────────────
GFT_2STEP_PROFILE = {
    'name'             : 'GFT_5K_2STEP',
    'account_size'     : 5000.0,
    'leverage'         : 100,

    # ── Evaluation phases ──────────────────────────────────────────────────────
    'phase_1': {
        'target_pct'   : 8.0,        # 8% = $400
        'target_usd'   : 400.0,
        'label'        : 'Phase 1 — $400 target',
    },
    'phase_2': {
        'target_pct'   : 6.0,        # 6% = $300
        'target_usd'   : 300.0,
        'label'        : 'Phase 2 — $300 target',
    },

    # ── Official GFT limits (hard rules — breach = account blown) ──────────────
    'official_daily_loss_pct'  : 4.0,     # $200/day
    'official_daily_loss_usd'  : 200.0,
    'official_max_loss_pct'    : 10.0,    # $500 total
    'official_max_loss_usd'    : 500.0,

    # ── Internal guards (more conservative — fire BEFORE official limits) ───────
    'internal_daily_warning'   : 100.0,   # -$100 → warn (Telegram alert)
    'internal_daily_risk_cut'  : 140.0,   # -$140 → reduce lots 50%
    'internal_daily_hard_stop' : 170.0,   # -$170 → stop trading today  (official limit $200)

    'internal_total_warning'   : 250.0,   # -$250 → warn
    'internal_total_risk_cut'  : 350.0,   # -$350 → reduce lots 50%
    'internal_total_hard_stop' : 430.0,   # -$430 → halt all entries  (official limit $500)

    # ── Risk per trade ─────────────────────────────────────────────────────────
    'risk_normal_pct'   : 0.50,    # 0.50% = $24.75/trade — Phase 1 growth mode
    'risk_reduced_pct'  : 0.25,    # 0.25% = $12.37/trade (after daily risk cut)
    'risk_max_pct'      : 0.75,    # 0.75% = $37.13 (A+ setups — makes boost meaningful)

    # ── Position limits ────────────────────────────────────────────────────────
    'max_open_positions'        : 2,
    'max_trades_per_day'        : 6,
    'max_trades_per_hour'       : 2,
    'min_seconds_between_trades': 300,   # 5 min minimum between entries
    'minimum_hold_time_seconds' : 120,   # 2 min min hold (GFT flags HFT)

    # ── GFT Evaluation Rules — from dashboard ──────────────────────────────────
    # Source: Trading Objectives panel on goatfundedtrader.com (May 2026 rules)
    'min_trading_days'          : 3,     # Must trade on at least 3 separate days per phase

    # ── Convenience aliases (used by Telegram bot + display code) ─────────────
    # Mirror the official limits so callers don't need to know the internal key name.
    'daily_loss_limit' : 200.0,   # == official_daily_loss_usd
    'total_loss_limit' : 500.0,   # == official_max_loss_usd

    # ── Symbols ────────────────────────────────────────────────────────────────
    'enabled_symbols'  : ['XAGUSD', 'USOIL'],
    'disabled_symbols' : ['XAUUSD'],
    'allowed_note'     : 'XAUUSD permanently disabled on GFT',

    # ── GFT kill zone windows (UTC) ────────────────────────────────────────────
    # Aligned to MT5 FTMO 15m backtest windows (2yr validated: XAGUSD 53% WR, USOIL 69% WR)
    # London: 07-12 UTC | NY: 16-20 UTC  — same as forex_worker.py KILL_ZONE_WINDOWS
    'kill_zone_windows_utc': [(7, 12), (16, 20)],

    # ── State file (isolated per-account directory — Phase 4) ─────────────────
    # Legacy: data/gft_2step_state.json  (migrated on first run)
    'state_file': 'data/gft_5k/state.json',
    'legacy_state_file': 'data/gft_2step_state.json',

    # ── Telegram prefix ────────────────────────────────────────────────────────
    'alert_prefix': '[GFT-2STEP]',
}

# ── Helper accessors ───────────────────────────────────────────────────────────

def get_profile() -> dict:
    return GFT_2STEP_PROFILE


def get_phase_target(phase: str) -> float:
    return GFT_2STEP_PROFILE[phase]['target_usd']


def get_risk_pct(risk_mode: str = 'normal') -> float:
    if risk_mode == 'reduced':
        return GFT_2STEP_PROFILE['risk_reduced_pct']
    return GFT_2STEP_PROFILE['risk_normal_pct']


def is_symbol_allowed(symbol: str) -> bool:
    return symbol in GFT_2STEP_PROFILE['enabled_symbols']


def is_kz_active(utc_hour: int) -> bool:
    return any(s <= utc_hour < e
               for s, e in GFT_2STEP_PROFILE['kill_zone_windows_utc'])
