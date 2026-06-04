# Secret Rotation Checklist — CB6 Quantum
**Date:** 2026-05-30 | **Classification:** Internal — Do not share

---

## .gitignore Status

| File | In .gitignore? | Risk |
|------|---------------|------|
| `.env` | ✅ YES — first line | Protected from git commits |
| `data/` | ✅ YES | State files not tracked |
| `venv/` | ✅ YES | Dependencies not tracked |
| `logs/` | ✅ YES | Log files not tracked |
| `*.pyc` | ✅ YES | Bytecode not tracked |
| `*.xlsx` | ✅ YES | Excel exports not tracked |

**Note:** This project is NOT currently a git repository (`git init` not run).
If you ever run `git init`, the `.gitignore` file will take effect immediately.
Run `git init` + `git add .gitignore` FIRST before any other `git add`.

---

## Credentials Inventory

### Active Live Credentials in `.env`

| Credential | Service | Rotation Priority | How to Rotate |
|-----------|---------|-------------------|---------------|
| `MT5_PASSWORD` | MT5 FTMO account | 🔴 HIGH — live trading | MT5 account settings → change password |
| `GFT_2STEP_PASSWORD` | MT5 GFT account | 🔴 HIGH — live trading | GFT client portal → account settings |
| `BINANCE_API_KEY` + `BINANCE_API_SECRET` | Binance Futures | 🔴 HIGH — could place live orders | Binance → API Management → delete + recreate |
| `FYERS_APP_SECRET` / `SECRET_KEY` | Fyers broker | 🟡 MEDIUM — token generation | Fyers API dashboard → regenerate app secret |
| `ACCESS_TOKEN` | Fyers live API JWT | 🟡 MEDIUM — expires daily anyway | Auto-rotated by `auto_token.py` |
| `TELEGRAM_BOT_TOKEN` | NSE Telegram bot | 🟡 MEDIUM — command injection risk | BotFather → /revoke |
| `FOREX_TELEGRAM_TOKEN` | FTMO Telegram bot | 🟡 MEDIUM | BotFather → /revoke |
| `TELEGRAM_BOT_TOKEN_GFT` | GFT Telegram bot | 🟡 MEDIUM | BotFather → /revoke |
| `CRYPTO_TELEGRAM_TOKEN` | Crypto bot (shelved) | 🟢 LOW — bot shelved | BotFather → /revoke when ready |
| `KITE_API_SECRET` | Zerodha Kite API | 🟢 LOW — currently unused | Kite Connect developer console |

---

## Rotation Actions

### IMMEDIATE (do within 24 hours)

- [ ] **Binance API key** — This key can place perpetual futures orders. Rotate immediately:
  1. Log into Binance → Profile → API Management
  2. Delete the current key pair
  3. Create a new key pair with IP whitelist set to your VPS IP only
  4. Update `.env`: `BINANCE_API_KEY=<new>` and `BINANCE_API_SECRET=<new>`

### BEFORE NEXT LIVE SESSION

- [ ] **MT5 FTMO password** — Change via MT5 account portal before next live trading day
- [ ] **GFT password** — Change via GFT client portal before next live trading day
- [ ] **Fyers SECRET_KEY** — Rotate via Fyers developer dashboard

### BEST PRACTICE (within 7 days)

- [ ] Set IP whitelist on Binance API key (your VPS IP only, not 0.0.0.0/0)
- [ ] Review Telegram bot permissions — bots should only respond to `CB6_ADMIN_USER_ID`
- [ ] Enable 2FA on all broker accounts if not already done
- [ ] Enable 2FA on Binance account

---

## Windows Credential Manager (Recommended Upgrade)

For the most sensitive credentials (MT5 passwords, Binance secrets), consider migrating
from `.env` to Windows Credential Manager:

```python
import subprocess
# Store: cmdkey /add:CB6_MT5_FTMO /user:login /pass:password
# Read:  winreg or keyring library
import keyring
password = keyring.get_password("CB6_MT5_FTMO", "login")
```

This keeps credentials out of any text file on disk entirely.

---

## Crypto Safety Lock

`CRYPTO_PAPER=false` has been changed to `CRYPTO_PAPER=true` in `.env`.

To re-enable live crypto (only after NSE WR ≥ 56% + GFT funded):
1. Set `CRYPTO_PAPER=false` in `.env`
2. Set `LIVE_CRYPTO_CONFIRMATION=CONFIRMED` in `.env`
3. Both must be present — `crypto_main.py` will abort if either is missing

---

## Status: ⚠️ ACTION REQUIRED — rotate Binance API key within 24 hours
