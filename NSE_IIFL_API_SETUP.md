# NSE IIFL API Setup

This adds IIFL as an extra NSE API lane for verification. It does not switch live NSE execution away from Fyers.

## IIFL Portal Form

For the app screen shown in IIFL Capital Markets APIs:

- App Name: `CB6_NSE_IIFL_MARKETDATA` for data, and `CB6_NSE_IIFL_INTERACTIVE` for orders if the portal requires separate apps.
- Redirect URL: use a local callback such as `http://127.0.0.1:8091/iifl/callback` unless IIFL support gives a specific production URL.
- IP Type: `IPv4` if your static IP is IPv4.
- Primary Static IP: enter the fixed public IP from the machine/VPS/proxy that will send broker API requests.
- Secondary Static IP: optional backup static IP only if you have one.
- Algo Registration Type: use `Non-Registered` for a normal retail algo under the broker/exchange low-order-rate category; use registered only if your algo is exchange registered.

Do not use a changing home/public IP for order APIs. Static IP whitelisting is now part of Indian broker API compliance and order requests can be rejected when they originate from an unregistered IP.

## Environment Keys

After IIFL approves the app and shows the keys, add these to `.env`:

```env
IIFL_SOURCE=WebAPI
IIFL_MARKETDATA_BASE_URL=https://ttblaze.iifl.com/apibinarymarketdata
IIFL_INTERACTIVE_BASE_URL=https://ttblaze.iifl.com/interactive
IIFL_MARKETDATA_APP_KEY=
IIFL_MARKETDATA_SECRET_KEY=
IIFL_INTERACTIVE_APP_KEY=
IIFL_INTERACTIVE_SECRET_KEY=
```

Run:

```powershell
python scripts/check_iifl_session.py
```

Use `--marketdata-only` or `--interactive-only` when only one app has been approved.

## Readiness Gate

Treat IIFL as not ready until `scripts/check_iifl_session.py` returns `OK` for the approved app. Live NSE execution still uses Fyers-shaped order code until a dedicated IIFL order adapter is implemented and paper-tested.
