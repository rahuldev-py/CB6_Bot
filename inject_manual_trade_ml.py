"""
Inject today's manually traded NIFTY LONG as a complete ML record.
Run once to seed the NSE ML system with this validated trade.
"""
import json, os
from datetime import datetime, timezone

ROOT   = os.path.dirname(os.path.abspath(__file__))
JSONL  = os.path.join(ROOT, 'data', 'ml', 'nse', 'trades.jsonl')
NOW_ISO = datetime.now(timezone.utc).isoformat()

ENTRY = {
    "_type"             : "ENTRY",
    "_schema_version"   : 2,
    "_written_at"       : "2026-06-05T08:48:00+00:00",  # 14:18 IST = 08:48 UTC
    "market"            : "NSE",
    "mode"              : "live",
    "trade_id"          : "manual_20260605_1418",
    "symbol"            : "NSE:NIFTY2650623350CE",
    "underlying"        : "NSE:NIFTY50-INDEX",
    "instrument_type"   : "OPTION",
    "direction"         : "BULLISH",
    "timeframe"         : "3min",
    "entry_price"       : 155.20,
    "stop_loss"         : 23267.0,
    "target_1"          : 23375.0,
    "target_2"          : 23445.0,
    "target_3"          : 23513.0,
    "sl_distance"       : 54.0,
    "rr_t1"             : 1.0,
    "rr_t2"             : 2.28,
    "rr_t3"             : 3.56,
    "in_ote"            : False,
    "in_fvg"            : True,
    "mss_type"          : "BOS",
    "sweep_type"        : "SELL_SIDE",
    "sweep_candles_ago" : 25,
    "sweep_confirmed"   : True,
    "fvg_low"           : 23321.0,
    "fvg_high"          : 23326.0,
    "fvg_size"          : 5.0,
    "fvg_equilibrium"   : 23323.5,
    "fvg_in_discount"   : True,
    "dol_direction"     : "SELL_SIDE",
    "dol_price"         : 23282.0,
    "dol_mss_match"     : True,
    "ut_bot_trend"      : "BULLISH",
    "ut_bot_aligned"    : False,
    "score"             : 14,
    "score_flags"       : ["sweep", "bos", "fvg", "ob", "displacement"],
    "h1_bias"           : "BEARISH",
    "h4_bias"           : "BEARISH",
    "fii_dii_sentiment" : "UNKNOWN",
    "brain_direction"   : "BEARISH",
    "brain_score"       : -2,
    "brain_confidence"  : 5,
    "brain_gate"        : 12,
    "brain_mode"        : "SELECTIVE",
    "ist_hour"          : 14,
    "ist_minute"        : 18,
    "session"           : "afternoon_silver_bullet",
    "utc_hour"          : 8,
    "utc_minute"        : 48,
    "day_of_week"       : 4,
    "day_name"          : "Friday",
    "week_of_month"     : 1,
    "timestamp_utc"     : "2026-06-05T08:48:00+00:00",
    "outcome"           : None,
    "strike"            : 23350,
    "option_type"       : "CE",
    "expiry_days_remaining" : 1,
    "underlying_at_entry"   : 23321.0,
    "ob_duration_mins"      : 75,
    "counter_trend"         : True,
    "size_rule"             : "HALF_SIZE_COUNTER_TREND",
}

OUTCOME = {
    "_type"     : "OUTCOME",
    "_written_at": NOW_ISO,
    "trade_id"  : "manual_20260605_1418",
    "outcome"   : {
        "exit_reason"        : "MANUAL_EARLY",
        "exit_price"         : 165.80,
        "underlying_at_exit" : 23390.0,
        "pnl_inr"            : 689.00,
        "r_multiple"         : 1.27,
        "targets_hit"        : ["T1"],
        "hold_time_minutes"  : 26,
        "result"             : "WIN",
        "timestamp_exit_ist" : "2026-06-05 14:44:00",
        "notes"              : "Exited early before T2/T3. Correct setup — H4 counter-trend, 50% size applied. BOS+FVG+OB sweep confirmed.",
    }
}

with open(JSONL, 'a', encoding='utf-8') as f:
    f.write(json.dumps(ENTRY, default=str) + '\n')
    f.write(json.dumps(OUTCOME, default=str) + '\n')

print(f"Injected ENTRY + OUTCOME for manual_20260605_1418")
print(f"NSE JSONL now has an outcome record for ML training.")
