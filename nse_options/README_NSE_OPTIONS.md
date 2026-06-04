# CB6 NSE Options Intelligence Layer

This package adds a disabled-by-default Sensibull-style analytics layer for NSE options.

It never executes trades. Fyers remains the primary broker/feed. Sensibull context is used only for ATM/chain/Greeks/pressure/expiry intelligence.

Enable it in `nse_options/option_config.json`:

```json
{
  "enabled": true,
  "use_for_trade_confirmation": true,
  "allow_strong_contradiction_block": false
}
```

Data flow:

`scan_silver_bullet` -> existing option selector -> optional Sensibull context -> small score delta/risk context -> journal/dashboard fields.

Failure mode:

If Sensibull is down, stale, slow, or schema changes, CB6 continues with neutral options context.
