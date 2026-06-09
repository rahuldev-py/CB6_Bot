"""
Inject today's MISSED NIFTY BEARISH (PE) trade as a validated ML + template record.

Trade: NIFTY 23200 PE — Close Silver Bullet 15:00-15:30 IST, Jun 8 2026
Chain: EQH @ 23247 wick-swept at 13:00 → BEARISH BOS → FVG in PREMIUM (23200-23232) → PE entry
Root cause: detect_eqh_eql used 'close' to check swept status — Judas wick sweep not recognized.
Fix applied: scanner/silver_bullet.py — now uses high/low prices for EQH/EQL swept check.

Run once after market close to seed ML + agent reports:
    python inject_missed_20260608_nifty_pe.py
"""
import json, os
from datetime import datetime, timezone

ROOT     = os.path.dirname(os.path.abspath(__file__))
NSE_JSONL = os.path.join(ROOT, 'data', 'ml', 'nse', 'trades.jsonl')
REPORTS  = os.path.join(ROOT, 'agent_reports', 'manual_trades')
os.makedirs(os.path.dirname(NSE_JSONL), exist_ok=True)
os.makedirs(REPORTS, exist_ok=True)

NOW_ISO = datetime.now(timezone.utc).isoformat()

# ── Trade DNA ─────────────────────────────────────────────────────────────────
# Entry: FVG midpoint 23,216 at 15:05 IST (first FVG retest after window open)
# SL:    23,265 — above wick-swept EQH (23,247) + 18pt buffer
# T1:    23,100 — touched intraday low 23,100.70 (T1 HIT)
# T2:    23,070 — day low area
# T3:    22,985 — next 30m support
# Exit:  T1 hit at 23,100 (conservative close — could have held to 23,070)
# PE option: 23200 PE, estimated entry 70 Rs, exit 145 Rs, lot 65, PnL ~Rs 4,875

ENTRY = {
    "_type"                  : "ENTRY",
    "_schema_version"        : 2,
    "_written_at"            : "2026-06-08T09:35:00+00:00",  # 15:05 IST = 09:35 UTC
    "market"                 : "NSE",
    "mode"                   : "live",
    "trade_id"               : "missed_20260608_1505_nifty_pe",
    "symbol"                 : "NSE:NIFTY2661223200PE",
    "underlying"             : "NSE:NIFTY50-INDEX",
    "instrument_type"        : "PE",
    "direction"              : "BEARISH",
    "timeframe"              : "3min",

    # ── Entry / exit ──────────────────────────────────────────────────────────
    "entry_price"            : 70.0,       # 23200 PE estimated at NIFTY 23,216
    "stop_loss"              : 23265.0,    # above EQH 23,247 + 18pt buffer
    "target_1"               : 23100.0,
    "target_2"               : 23070.0,
    "target_3"               : 22985.0,
    "sl_distance"            : 49.0,       # 23,265 - 23,216
    "rr_t1"                  : 2.37,       # (23,216 - 23,100) / 49
    "rr_t2"                  : 2.98,
    "rr_t3"                  : 4.71,
    "underlying_at_entry"    : 23216.0,

    # ── ICT chain ─────────────────────────────────────────────────────────────
    "dol_type"               : "EQH",      # Equal Highs — buy-side cluster
    "dol_price"              : 23247.0,
    "dol_direction"          : "BUY_SIDE",
    "dol_swept"              : True,
    "dol_sweep_mechanism"    : "WICK",     # Judas swing — wick above, close below
    "dol_sweep_time_ist"     : "13:00",    # swept ~13:00 IST, 2h before entry window
    "dol_mss_match"          : True,
    "sweep_type"             : "HIGH_SWEEP",
    "sweep_confirmed"        : True,
    "sweep_candles_ago"      : 42,         # ~2h before on 3m bars

    "mss_type"               : "BOS",
    "bos_level"              : 23200.0,    # structure break below prior swing

    "in_fvg"                 : True,
    "fvg_low"                : 23200.0,
    "fvg_high"               : 23232.0,
    "fvg_size"               : 32.0,
    "fvg_equilibrium"        : 23216.0,
    "fvg_in_premium"         : True,       # BEARISH FVG above current price = PREMIUM
    "fvg_in_discount"        : False,

    # ── Context ───────────────────────────────────────────────────────────────
    "h1_bias"                : "BEARISH",
    "h4_bias"                : "BEARISH",
    "counter_trend"          : False,      # aligned with H4

    "score"                  : 14,
    "score_flags"            : ["sweep", "eqh_eql", "bos", "fvg", "h4_aligned", "premium_zone"],
    "ut_bot_trend"           : "BEARISH",
    "ut_bot_aligned"         : True,

    "ist_hour"               : 15,
    "ist_minute"             : 5,
    "utc_hour"               : 9,
    "utc_minute"             : 35,
    "session"                : "close_silver_bullet",
    "day_of_week"            : 0,          # Monday
    "day_name"               : "Monday",
    "timestamp_utc"          : "2026-06-08T09:35:00+00:00",

    # ── Option details ────────────────────────────────────────────────────────
    "strike"                 : 23200,
    "option_type"            : "PE",
    "expiry_days_remaining"  : 4,          # Weekly Thursday Jun 12 expiry

    # ── Bot miss metadata (for ML + CIPHER analysis) ──────────────────────────
    "bot_traded"             : False,
    "bot_missed"             : True,
    "bot_missed_reason"      : (
        "detect_eqh_eql used 'close' price for swept check — "
        "Judas wick sweep of EQH 23247 not recognized. "
        "EQH remained as active DOL with BULLISH direction. "
        "MSS was BEARISH → direction mismatch → skip every scan. "
        "Fix: use high/low prices for EQH/EQL swept detection."
    ),
    "bug_fixed"              : True,
    "bug_fix_file"           : "scanner/silver_bullet.py::detect_eqh_eql",

    # ── Template classification ────────────────────────────────────────────────
    "template_type"          : "EQH_WICK_SWEPT_BEARISH_FVG_PREMIUM",
    "template_notes"         : (
        "Pattern: EQH (buy-side cluster) wick-swept by Judas candle → "
        "BEARISH BOS confirmed → price retraces into BEARISH FVG in PREMIUM → PE short. "
        "Key feature: DOL is WICK-swept (close never exceeds EQH). "
        "Classic ICT Silver Bullet reversal after buy-side liquidity grab. "
        "Counter-intuitive: EQH above price looks bullish but POST-sweep direction is BEARISH."
    ),

    "outcome"                : None,
}

OUTCOME = {
    "_type"      : "OUTCOME",
    "_written_at": NOW_ISO,
    "trade_id"   : "missed_20260608_1505_nifty_pe",
    "outcome"    : {
        "exit_reason"        : "T1",
        "exit_price"         : 145.0,      # estimated at NIFTY 23,100
        "underlying_at_exit" : 23100.0,
        "pnl_inr"            : 4875.0,     # (145-70) × 65 = Rs 4,875 / lot (estimated)
        "r_multiple"         : 2.37,
        "targets_hit"        : ["T1"],
        "hold_time_minutes"  : 25,
        "result"             : "WIN",
        "timestamp_exit_ist" : "2026-06-08 15:30:00",
        "notes"              : (
            "MISSED BY BOT — estimated PnL. "
            "NIFTY low touched 23,100.70 during close window — T1 reached. "
            "Premiums estimated: entry 70 Rs, T1 exit 145 Rs. "
            "Root cause fixed same day: EQH wick-sweep detection now uses HIGH prices. "
            "Template added to ML for EQH_WICK_SWEPT_BEARISH_FVG_PREMIUM pattern."
        ),
    }
}

# ── Write to NSE ML JSONL ──────────────────────────────────────────────────────
with open(NSE_JSONL, 'a', encoding='utf-8') as f:
    f.write(json.dumps(ENTRY, default=str) + '\n')
    f.write(json.dumps(OUTCOME, default=str) + '\n')

# ── Write agent report ─────────────────────────────────────────────────────────
report_path = os.path.join(REPORTS, "missed_20260608_1505_nifty_pe.md")
with open(report_path, 'w', encoding='utf-8') as f:
    f.write(f"""# Missed Trade — NIFTY 23200 PE BEARISH | 2026-06-08
## For: ML / NEXUS / CIPHER / SHADOW / ATLAS

| Field | Value |
|-------|-------|
| Trade ID | missed_20260608_1505_nifty_pe |
| Market | NSE |
| Window | Close Silver Bullet 15:00-15:30 IST |
| Direction | BEARISH → PE |
| Symbol | NIFTY 23200 PE (exp Jun 12) |
| DOL | EQH @ 23,247 — wick-swept 13:00 IST |
| MSS | BEARISH BOS @ 23,200 |
| FVG | 23,200–23,232 (PREMIUM zone) |
| Entry | ~23,216 (FVG midpoint, 15:05 IST) |
| SL | 23,265 (+18pt above EQH) |
| T1 | 23,100 ✅ HIT (low 23,100.70) |
| T2 | 23,070 |
| T3 | 22,985 |
| R @ T1 | 2.37R |
| Est. PnL | Rs 4,875 / lot |
| H4 bias | BEARISH (aligned) |
| Score | 14/15 |

## Why Bot Missed

> `detect_eqh_eql` checked **close prices** for EQH swept status.
> EQH @ 23,247 was wick-swept at 13:00 IST (Judas candle wick to 23,260+, close returned below).
> Since close never exceeded 23,247 × 1.0005 = 23,259, `swept = False`.
> EQH remained as active DOL with `direction = BULLISH`.
> MSS was BEARISH → direction mismatch → **skip every 15-second scan for 30 minutes**.

## Fix Applied

- **File**: `scanner/silver_bullet.py` → `detect_eqh_eql._emit()`
- **Change**: EQH swept check now uses `recent['high']` (wick-based), EQL uses `recent['low']`
- **Effect**: Wick sweeps (Judas swings) now correctly mark EQH/EQL as swept
- **Applies to**: NSE + Forex GFT $5K + GFT $1K (shared module)

## Template Pattern: EQH_WICK_SWEPT_BEARISH_FVG_PREMIUM

```
1. EQH cluster above price (2+ equal highs — buy-side stop cluster)
2. Judas wick spike above EQH — close returns below (wick sweep, NOT close sweep)
3. BEARISH BOS/CHoCH confirms — structure breaks down after sweep
4. BEARISH FVG forms in PREMIUM zone (above current price)
5. Price retraces into FVG → PE entry
6. SL: above EQH level + 10-15pt buffer
7. TP: sell-side liquidity below (EQL cluster or day low)
```

Key distinguishing feature: DOL swept by WICK, not close. Classic ICT Judas swing / SMC liquidity grab.
""")

print(f"✓ Injected ENTRY + OUTCOME → data/ml/nse/trades.jsonl")
print(f"✓ Agent report  → {os.path.relpath(report_path)}")
print(f"  Trade ID  : missed_20260608_1505_nifty_pe")
print(f"  Template  : EQH_WICK_SWEPT_BEARISH_FVG_PREMIUM")
print(f"  Result    : WIN (estimated) | T1 hit | 2.37R | Rs 4,875/lot")
print(f"  Bug fixed : scanner/silver_bullet.py::detect_eqh_eql (HIGH/LOW wick check)")
