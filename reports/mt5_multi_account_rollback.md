# CB6 Quantum — MT5 Multi-Account Infrastructure: Rollback Plan

**Date applied:** 2026-05-24  
**Tests:** 24/24 passing (tests/test_mt5_multi_account.py)

---

## What Was Changed

| File | Type | Change |
|---|---|---|
| `C:\CB6_MT5\` | NEW DIR | 3 portable terminal folders created |
| `C:\CB6_MT5\README_SETUP.md` | NEW | Step-by-step terminal setup guide |
| `config/mt5_accounts.json` | NEW | Account registry (FTMO_10K, GFT_5K, PAPER) |
| `forex_engine/accounts/__init__.py` | NEW | Package init |
| `forex_engine/accounts/account_registry.py` | NEW | Loads registry, resolves credentials + paths |
| `forex_engine/accounts/account_router.py` | NEW | Magic-number-safe trade routing |
| `forex_engine/accounts/mt5_session_manager.py` | NEW | Terminal lifecycle + heartbeat |
| `forex_engine/accounts/ftmo_adapter.py` | NEW | FTMO connector factory |
| `forex_engine/accounts/gft_adapter.py` | NEW | GFT connector factory |
| `forex_engine/accounts/pre_entry_validator.py` | NEW | 10-check pre-entry gate |
| `forex_engine/accounts/master_router_stub.py` | NEW | Phase 5 stub (not yet active) |
| `forex_engine/mt5/mt5_connector.py` | MODIFIED | Added `terminal_path` param + account login guard |
| `forex_engine/forex_worker.py` | MODIFIED | Uses `build_ftmo_connector()` instead of bare `MT5Connector()` |
| `forex_engine/prop_firms/gft/gft_5k_2step.py` | MODIFIED | Uses `build_gft_connector()` instead of bare `MT5Connector()` |
| `forex_engine/prop_firms/ftmo/ftmo_state.py` | MODIFIED | STATE_FILE → `data/ftmo_10k/state.json` + migration |
| `forex_engine/prop_firms/gft/gft_config.py` | MODIFIED | `state_file` → `data/gft_5k/state.json` |
| `forex_engine/prop_firms/gft/gft_phase_tracker.py` | MODIFIED | Migration from legacy path |
| `communications/forex_bot.py` | MODIFIED | Added `_cmd_terminals()` + `/fx_terminals` command |
| `data/ftmo_10k/` | NEW DIR | Isolated FTMO state directory |
| `data/gft_5k/` | NEW DIR | Isolated GFT state directory |
| `tests/test_mt5_multi_account.py` | NEW | 24-test verification suite |

---

## Hard Rollback (revert entire change set)

```powershell
# 1. Stop engines
# Send /fx_stop via Telegram, then kill processes:
Get-Process python | Stop-Process -Force

# 2. Restore modified files from pre-session backup
Copy-Item "C:\cb6_bot\CB6_PRE_LIVE_RESUME_20260523\*" "C:\cb6_bot\" -Recurse -Force

# 3. Verify syntax of restored files
python -m py_compile forex_engine/mt5/mt5_connector.py
python -m py_compile forex_engine/forex_worker.py
python -m py_compile forex_engine/prop_firms/gft/gft_5k_2step.py
python -m py_compile forex_engine/prop_firms/ftmo/ftmo_state.py
python -m py_compile communications/forex_bot.py
```

---

## Surgical Rollbacks

### MT5Connector — Remove terminal_path param

**File:** `forex_engine/mt5/mt5_connector.py`  
**Symptom:** Terminal connection failures in live mode  
**Rollback:**
1. Remove `terminal_path: Optional[str] = None` from `__init__` signature
2. Remove `self._terminal_path = terminal_path`
3. Replace the new `_connect()` body with original: `mt5.initialize(login=login, password=password, server=server)`
4. Remove the account mismatch guard block

### forex_worker.py — Revert to bare MT5Connector

**File:** `forex_engine/forex_worker.py`  
**Rollback:** Replace:
```python
from forex_engine.accounts.ftmo_adapter import build_ftmo_connector
self._adapter = build_ftmo_connector(paper=self._paper)
```
With:
```python
self._adapter = MT5Connector(paper=self._paper)
```

### gft_5k_2step.py — Revert to bare MT5Connector

**File:** `forex_engine/prop_firms/gft/gft_5k_2step.py`  
**Rollback:** Replace:
```python
from forex_engine.accounts.gft_adapter import build_gft_connector
self._connector = build_gft_connector(paper=paper)
```
With:
```python
from forex_engine.mt5.mt5_connector import MT5Connector
login    = os.getenv('GFT_2STEP_LOGIN')
password = os.getenv('GFT_2STEP_PASSWORD')
server   = os.getenv('GFT_2STEP_SERVER')
creds = ({'login': login, 'password': password, 'server': server}
         if login and password and server else None)
self._connector = MT5Connector(paper=paper, credentials=creds)
```

### State file path rollback — FTMO

**File:** `forex_engine/prop_firms/ftmo/ftmo_state.py`  
**Rollback:** Change:
```python
STATE_FILE = os.path.join(_ROOT, 'data', 'ftmo_10k', 'state.json')
```
Back to:
```python
STATE_FILE = os.path.join(os.path.dirname(...), 'data', 'forex_paper_state.json')
```
Then remove `_ROOT`, `_LEGACY_STATE_FILE`, `_migrate_once()`.

### State file path rollback — GFT

**File:** `forex_engine/prop_firms/gft/gft_config.py`  
**Rollback:** Change `'state_file': 'data/gft_5k/state.json'` back to `'data/gft_2step_state.json'`

---

## Final Verification Checklist

Run AFTER completing portable terminal setup:

```powershell
# 1. All tests pass
python -m pytest tests/test_mt5_multi_account.py -v
# Expected: 24 passed

# 2. Both terminal files exist
Test-Path "C:\CB6_MT5\MT5_FTMO_10K\terminal64.exe"   # → True
Test-Path "C:\CB6_MT5\MT5_GFT_5K\terminal64.exe"     # → True

# 3. Both terminals log in and show Algo Trading = ON simultaneously
# (visual check — open both terminals, confirm green Algo button)

# 4. Env vars for terminal paths set (add to .env):
# MT5_TERMINAL_FTMO=C:/CB6_MT5/MT5_FTMO_10K/terminal64.exe
# MT5_TERMINAL_GFT=C:/CB6_MT5/MT5_GFT_5K/terminal64.exe

# 5. State migration ran correctly
Test-Path "C:\cb6_bot\data\ftmo_10k\state.json"   # → True (migrated from forex_paper_state.json)
Test-Path "C:\cb6_bot\data\gft_5k\state.json"     # → True (migrated from gft_2step_state.json)

# 6. Start both engines — confirm separate processes
python -m forex_engine.forex_main --profile ALL
# Log lines to look for:
#   [FTMO_10K] Building LIVE connector — terminal=C:\CB6_MT5\MT5_FTMO_10K\...
#   [GFT_5K]   Building LIVE connector — terminal=C:\CB6_MT5\MT5_GFT_5K\...
#   MT5 connected — login=<FTMO_login>  balance=$...
#   MT5 connected — login=<GFT_login>   balance=$...

# 7. Telegram: send /fx_terminals
# Expected response shows:
#   FTMO: ONLINE ✅  terminal: ✅ found
#   GFT:  ONLINE ✅  terminal: ✅ found
#   Isolation: ✅ FTMO + GFT run as separate processes
#              ✅ Each process connects to its own terminal

# 8. Confirm NO "Algo Trading OFF" events appear in either terminal
#    after running for 5 minutes (each terminal's Algo button stays green)

# 9. Confirm magic number isolation — send /fx_positions
#    FTMO positions should show magic=20260517 only
#    GFT positions should show magic=62001 only

# 10. Kill one terminal manually — confirm only that engine disconnects
#    (the other engine continues trading unaffected)
```

---

## Anomaly Detection After Deploy

```powershell
# Monitor for account mismatch warnings (means wrong terminal path)
Select-String -Path "logs\cb6_$(Get-Date -f yyyyMMdd).log" -Pattern "ACCOUNT MISMATCH|WRONG TERMINAL" | Select-Object -Last 10

# Monitor for cross-account contamination
Select-String -Path "logs\cb6_$(Get-Date -f yyyyMMdd).log" -Pattern "MAGIC MISMATCH|contamination" | Select-Object -Last 10

# Confirm both engines heartbeat successfully (should see every ~60s)
Select-String -Path "logs\cb6_$(Get-Date -f yyyyMMdd).log" -Pattern "HB OK" | Select-Object -Last 20

# Confirm state migration ran
Select-String -Path "logs\cb6_$(Get-Date -f yyyyMMdd).log" -Pattern "state migrated" | Select-Object -Last 5
```
