# FTMO Symbol Routing Audit
**Date:** 2026-05-30 | **Auditor:** CB6 Quantum Hardening — Phase 1 Task 1

---

## Findings Before Fix

| Source | XAUUSD Present? | Authority? |
|--------|-----------------|------------|
| `forex_worker.py:ACTIVE_SYMBOLS` | ✅ YES (was `['XAUUSD','XAGUSD','USOIL']`) | Controls actual polling + scan |
| `forex_worker.py:SYMBOL_MIN_SCORE` | ✅ YES | Minimum score gate |
| `ftmo_config.py:FTMO_ACTIVE_SYMBOLS` | ❌ NO (was `['XAGUSD','USOIL','EURUSD']`) | Not used at runtime |
| `ftmo_config.py:FTMO_DISABLED_SYMBOLS` | XAUUSD listed | **Never imported in forex_worker.py** |

**Root cause:** `FTMO_DISABLED_SYMBOLS` existed in `ftmo_config.py` but was never imported or checked
in `forex_worker.py`. The operational `ACTIVE_SYMBOLS` list was the sole gating mechanism,
and it still had XAUUSD in it. The engine **would have traded XAUUSD** on any valid ICT setup.

---

## Fixes Applied

### Fix 1 — Remove XAUUSD from `ACTIVE_SYMBOLS`
**File:** `forex_engine/forex_worker.py:111`

```python
# BEFORE
ACTIVE_SYMBOLS = ['XAUUSD', 'XAGUSD', 'USOIL']

# AFTER
ACTIVE_SYMBOLS = ['XAGUSD', 'USOIL']
```

### Fix 2 — Update `SYMBOL_MIN_SCORE` comment
**File:** `forex_engine/forex_worker.py:104`

XAUUSD entry now clearly marked as `# DISABLED` with reason.

### Fix 3 — Startup RuntimeError guard in `main()`
**File:** `forex_engine/forex_worker.py` — added at top of `main()`:

```python
from forex_engine.prop_firms.ftmo.ftmo_config import FTMO_DISABLED_SYMBOLS
for _sym in ACTIVE_SYMBOLS:
    if _sym in FTMO_DISABLED_SYMBOLS:
        raise RuntimeError(
            f"STARTUP ABORT: {_sym} is in ACTIVE_SYMBOLS but listed in "
            f"FTMO_DISABLED_SYMBOLS {FTMO_DISABLED_SYMBOLS}. "
            f"Remove it from ACTIVE_SYMBOLS before running."
        )
```

This guard fires **before** the news monitor starts, before MT5 connects,
and before any trade path is reachable.

---

## Post-Fix Symbol State

| Symbol | ACTIVE_SYMBOLS | SYMBOL_MIN_SCORE | FTMO_DISABLED_SYMBOLS | Will Trade? |
|--------|----------------|------------------|-----------------------|-------------|
| XAUUSD | ❌ REMOVED | Annotated DISABLED | ✅ Listed | **NO** |
| XAGUSD | ✅ | 11 | — | YES |
| USOIL  | ✅ | 11 | — | YES |
| EURUSD | ❌ Not in list | 11 (standby) | — | NO |

---

## Re-enabling XAUUSD (when appropriate)

To re-enable XAUUSD on FTMO:
1. Verify H4 trend is aligned (no counter-trend trades — the May 22 lesson)
2. Remove `'XAUUSD'` from `FTMO_DISABLED_SYMBOLS` in `ftmo_config.py`
3. Add `'XAUUSD'` back to `ACTIVE_SYMBOLS` in `forex_worker.py`
4. Startup guard will now pass silently

Do NOT re-enable on GFT — XAUUSD is permanently disabled there.

---

## Status: ✅ RESOLVED
