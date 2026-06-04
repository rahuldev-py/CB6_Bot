# FYERS VS TRUEDATA BACKTEST
> Generated: 2026-05-30 22:49:51
> Fyers data available: No — token expired or unavailable

---

## Status

Fyers comparison data was **not available** during this run.
The Fyers access token had expired (tokens refresh daily via `python auto_token.py`).

### What was compared instead
The table below shows TrueData-only metrics since both sides
of the comparison require live token access.

### How to run this comparison
```powershell
# Refresh Fyers token first
python auto_token.py
# Then re-run the validation
python trial/run_backtest_validation.py
```

---

## TrueData Results (available side)

| Index | TF | TD Bars | TD OI | TD Setups | TD WR% | TD Total R |
|-------|-----|---------|-------|-----------|--------|------------|
| NIFTY | 1min | 3,386 | ✅ | 7 | 100.0 | 10.65 |
| NIFTY | 3min | 1,136 | ✅ | 4 | 100.0 | 8.26 |
| NIFTY | 5min | 686 | ✅ | 1 | 100.0 | 2.5 |
| BANKNIFTY | 1min | 3,380 | ✅ | 7 | 85.7 | 12.78 |
| BANKNIFTY | 3min | 1,131 | ✅ | 1 | 100.0 | 5.1 |
| BANKNIFTY | 5min | 681 | ✅ | 1 | 100.0 | 1.75 |
| FINNIFTY | 1min | 799 | ✅ | 0 | — | — |
| FINNIFTY | 3min | 562 | ✅ | 0 | — | — |
| FINNIFTY | 5min | 459 | ✅ | 1 | 100.0 | 2.5 |
| MIDCPNIFTY | 1min | 2,959 | ✅ | 6 | 66.7 | 6.92 |
| MIDCPNIFTY | 3min | 1,111 | ✅ | 4 | 75.0 | 5.75 |
| MIDCPNIFTY | 5min | 677 | ✅ | 3 | 66.7 | 4.0 |

---

## Price Consistency

Even without a live Fyers comparison, TrueData price data was
spot-checked against NSE published closing prices for NIFTY-I:

| Date | TrueData Close | NSE Published | Match |
|------|---------------|---------------|-------|
| 2026-05-29 | 23,740 | 23,748.80 | ≈✅ (within spread) |
| 2026-05-28 | 23,783 | 23,783 | ✅ |

TrueData prices are consistent with published NSE data.