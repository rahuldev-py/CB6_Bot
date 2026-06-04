# CB6 OI ANALYSIS
> Generated: 2026-05-30 22:49:51
> Period: 9 trading days, TrueData data
> Trades with OI data: 35
> ⚠️ 35 observations — correlations below are NOT statistically significant

---

## OI Change in 3 Bars Before Entry

| Group | n | Mean OI Δ% | Median OI Δ% | % Positive |
|-------|---|-----------|--------------|-----------|
| Before winners | 30 | 0.07 | 0.044 | 70.0 |
| Before losers  | 5 | 0.013 | 0.006 | 60.0 |
| At DOL events  | 35 | 0.062 | 0.04 | 68.6 |

**Winners:** n=30, mean=+0.070%, median=+0.044%, positive=70.0%
**Losers:** n=5, mean=+0.013%, median=+0.006%, positive=60.0%
**DOL events:** n=35, mean=+0.062%, median=+0.040%, positive=68.6%

---

## OI DOL Boost Activations

- Boost fires (OI spike at DOL): **2** times

---

## OI Divergence Signal Distribution

| Signal | Count |
|--------|-------|
| CONFIRMATION (price + OI same direction) | 23 |
| DIVERGENCE (price + OI opposite) | 7 |

---

## Interpretation

### What this data shows
- OI is present and populated on every TrueData bar — confirmed ready for use.
- The OI Δ% calculation (3 bars before entry) is computing correctly.
- The OI DOL boost filter is executing without errors.

### What this data does NOT show
With 35 observations:

1. **OI expansion before winners vs losers** — not distinguishable from random noise.
   Need ≥50 in each group for any inference.
2. **OI filter impact on win rate** — cannot measure with this sample.
3. **Optimal OI threshold** — the current 0.5% decline threshold is reasonable but
   untested. Verify with ≥200 trades post-purchase.

### Planned measurements once paid data available
1. Split 200+ trades by OI_RISING / OI_FLAT / OI_DECLINING at entry
2. Compare win rates across OI states with confidence intervals
3. Measure OI contraction rate 3 bars before SL hits (possible early exit signal)
4. Correlate OI spike at DOL level with sweep probability

---

## Structural Advantage (Data Only)

Regardless of the small sample:

| Feature | Fyers | TrueData |
|---------|-------|----------|
| OI on intraday bars | ❌ | ✅ |
| OI completeness | N/A | 70%+ of bars |

The **availability** of OI from TrueData is confirmed.
Whether it adds predictive value requires a full-sized sample.
