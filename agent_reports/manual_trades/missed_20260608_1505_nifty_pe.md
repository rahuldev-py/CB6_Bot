# Missed Trade — NIFTY 23200 PE BEARISH | 2026-06-08
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
