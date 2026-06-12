import os
import json
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID    = os.getenv("CLIENT_ID") or ""
SECRET_KEY   = os.getenv("SECRET_KEY") or ""
REDIRECT_URI = os.getenv("REDIRECT_URI") or ""
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN") or ""

TRUEDATA_USER     = os.getenv("TRUEDATA_USER", "")
TRUEDATA_PASSWORD = os.getenv("TRUEDATA_PASSWORD", "")

# Optional NSE/IIFL API lane. Disabled unless explicitly selected by tooling.
IIFL_SOURCE = os.getenv("IIFL_SOURCE", "WebAPI")
IIFL_MARKETDATA_BASE_URL = os.getenv(
    "IIFL_MARKETDATA_BASE_URL",
    "https://ttblaze.iifl.com/apibinarymarketdata",
).rstrip("/")
IIFL_INTERACTIVE_BASE_URL = os.getenv(
    "IIFL_INTERACTIVE_BASE_URL",
    "https://ttblaze.iifl.com/interactive",
).rstrip("/")
IIFL_MARKETDATA_APP_KEY = os.getenv("IIFL_MARKETDATA_APP_KEY", "")
IIFL_MARKETDATA_SECRET_KEY = os.getenv("IIFL_MARKETDATA_SECRET_KEY", "")
IIFL_INTERACTIVE_APP_KEY = os.getenv("IIFL_INTERACTIVE_APP_KEY", "")
IIFL_INTERACTIVE_SECRET_KEY = os.getenv("IIFL_INTERACTIVE_SECRET_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

CAPITAL            = float(os.getenv("CAPITAL", 200000))     # Rs 2 Lakh default
RISK_PER_TRADE_PCT = 1    # Never above 1% — pro discipline

MAX_LOSS_PER_DAY   = 3    # halt after 3 losing trades (consecutive-loss guard)
MAX_TRADES_PER_DAY = 5    # max 5 trades per day
MAX_DAILY_LOSS_PCT = 3.03 # Hard halt if equity drops 3.03% (= Rs 1,000 on 33k capital)
MAX_DAILY_LOSS_ABS = float(os.getenv("MAX_DAILY_LOSS_ABS", 1000))  # Rs 1,000 hard stop
TIMEFRAMES         = ["60"]         # 60min only — 15min was too noisy. ICT works on 1H+ in Indian equity.
MARKET             = "NSE"

MIN_BUY_SCORE      = 12   # ML-validated: score≥12 → 72.3% WR, PF 8.57 vs PF 6.80 at ≥8
MIN_SELL_SCORE     = 12   # ML-validated: score≥12 + BEARISH → PF 10.40
MIN_RR_RATIO       = 3.0  # Minimum 1:3 RR — Bloomberg/ICT pros never take 1:1.5

AI_CHAT_MODEL      = os.getenv('AI_CHAT_MODEL', 'claude-haiku-4-5-20251001')

MARKET_OPEN     = "09:15"
MARKET_CLOSE    = "15:30"
SQUARE_OFF_TIME = "15:15"
NO_ENTRY_AFTER  = "15:00"
NO_ENTRY_BEFORE = "09:30"

# Execution safety layer feature flag:
# LEGACY (default) -> existing behavior
# SAFE_VALIDATION  -> validation + manual approval required
# HYBRID_TEST      -> validation + manual approval required (parallel test mode)
# SAFE_VALIDATION_REVALIDATE_AUTO -> ARMED one-cycle hold, auto revalidate, then auto execute
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "LEGACY").strip().upper()

# NSE instrument router — futures XOR options per signal (never both — double risk).
# False = options-only (current live mode, futures paused May 2026).
# Set NSE_FUTURES_ENABLED=true in .env to re-enable futures routing.
NSE_FUTURES_ENABLED = os.getenv("NSE_FUTURES_ENABLED", "false").strip().lower() == "true"

# ML Gate — NSE live trading
# When True: ML filters ICT setups before placing trades.
# AVOID confidence (win_prob ≤ 0.35) blocks the trade. Fails open if ML errors.
ML_GATE_NSE = os.getenv("ML_GATE_NSE", "true").strip().lower() == "true"

# Execution validation defaults (override via .env as needed)
MAX_ENTRY_DRIFT_PERCENT = float(os.getenv("MAX_ENTRY_DRIFT_PERCENT", 2.0))
MAX_ENTRY_DRIFT_POINTS = float(os.getenv("MAX_ENTRY_DRIFT_POINTS", 3.0))
EXECUTION_MIN_RR = float(os.getenv("EXECUTION_MIN_RR", 1.5))
EXECUTION_INVALIDATION_BUFFER_POINTS = float(os.getenv("EXECUTION_INVALIDATION_BUFFER_POINTS", 10.0))
EXECUTION_ALLOWED_SIGNAL_AGE_SECONDS = int(os.getenv("EXECUTION_ALLOWED_SIGNAL_AGE_SECONDS", 180))
EXECUTION_REVALIDATE_CYCLE_SECONDS = int(os.getenv("EXECUTION_REVALIDATE_CYCLE_SECONDS", 180))
EXECUTION_MAX_SPREAD_PCT = float(os.getenv("EXECUTION_MAX_SPREAD_PCT", 0.01))


def _parse_json_list(env_key: str, default: list):
    raw = os.getenv(env_key, "")
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else default
    except Exception:
        return default


# Forex execution safety layer feature flag (independent from Indian path)
# LEGACY                            -> pass-through (zero added latency)
# SAFE_VALIDATION_REVALIDATE_AUTO   -> ARMED wait cycle + auto revalidate + auto execute
FOREX_EXECUTION_MODE = os.getenv("FOREX_EXECUTION_MODE", "LEGACY").strip().upper()
FOREX_EXECUTION_REVALIDATE_CYCLE_SECONDS = int(
    os.getenv("FOREX_EXECUTION_REVALIDATE_CYCLE_SECONDS", 60)
)
FOREX_MAX_SPREAD_PCT = float(os.getenv("FOREX_MAX_SPREAD_PCT", 0.0005))
FOREX_DISABLED_SYMBOLS = _parse_json_list(
    "FOREX_DISABLED_SYMBOLS",
    [],   # Default empty — symbol blocking is owned by FTMO_DISABLED_SYMBOLS and account_router.blocked_symbols
)
FOREX_ALLOWED_UTC_WINDOWS = _parse_json_list(
    "FOREX_ALLOWED_UTC_WINDOWS",
    [["08:00", "11:00"], ["13:00", "16:30"]],
)
FOREX_ALLOWED_SIGNAL_AGE_SECONDS = int(os.getenv("FOREX_ALLOWED_SIGNAL_AGE_SECONDS", 600))
FOREX_MAX_ENTRY_DRIFT_PERCENT = float(os.getenv("FOREX_MAX_ENTRY_DRIFT_PERCENT", 2.0))
FOREX_MAX_ENTRY_DRIFT_POINTS = float(os.getenv("FOREX_MAX_ENTRY_DRIFT_POINTS", 0.5))
FOREX_EXECUTION_MIN_RR = float(os.getenv("FOREX_EXECUTION_MIN_RR", 1.5))
FOREX_EXECUTION_INVALIDATION_BUFFER_POINTS = float(
    os.getenv("FOREX_EXECUTION_INVALIDATION_BUFFER_POINTS", 0.5)
)

# Phase-1 institutional memory scaffolding flags (all OFF by default).
# These toggles are intentionally not wired into live execution paths yet.
CB6_MEMORY_V1_ENABLED = os.getenv("CB6_MEMORY_V1_ENABLED", "false").strip().lower() == "true"
CB6_REGIME_V1_ENABLED = os.getenv("CB6_REGIME_V1_ENABLED", "false").strip().lower() == "true"
CB6_SETUP_DNA_V1_ENABLED = os.getenv("CB6_SETUP_DNA_V1_ENABLED", "false").strip().lower() == "true"
CB6_REPLAY_V1_ENABLED = os.getenv("CB6_REPLAY_V1_ENABLED", "false").strip().lower() == "true"
CB6_GFT_CHALLENGE_MODE_ENABLED = os.getenv(
    "CB6_GFT_CHALLENGE_MODE_ENABLED", "false"
).strip().lower() == "true"
CB6_GFT_SHADOW_RECOMMENDATION_ENABLED = os.getenv(
    "CB6_GFT_SHADOW_RECOMMENDATION_ENABLED", "false"
).strip().lower() == "true"
CB6_GFT_SOFT_GATE_ENABLED = os.getenv(
    "CB6_GFT_SOFT_GATE_ENABLED", "false"
).strip().lower() == "true"
CB6_GFT_HARD_ENFORCEMENT_ENABLED = os.getenv(
    "CB6_GFT_HARD_ENFORCEMENT_ENABLED", "false"
).strip().lower() == "true"
CB6_ADAPTIVE_TRADE_GATE_ENABLED = os.getenv(
    "CB6_ADAPTIVE_TRADE_GATE_ENABLED", "false"
).strip().lower() == "true"
