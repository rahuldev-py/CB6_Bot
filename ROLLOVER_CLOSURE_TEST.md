# Rollover Position Closure — Bug Report & Fix
**Date:** 2026-05-30 | **File:** `forex_engine/forex_worker.py`

---

## The Bug

**Location:** `forex_engine/forex_worker.py` — `_pre_rollover_guard()` method

```python
# BEFORE (BROKEN — indentation error)
if danger and not self._paper:
    ticket = trade.get('ticket', 0)
if ticket:                          # ← outside the 'if danger' block!
    self._adapter.close_position(sym, ticket, trade['lots'], trade['direction'])
    logger.info(f"FOREX {sym}: closed #{ticket} before rollover (tight SL)")
```

### What this caused

The `if ticket:` block executes **unconditionally** across loop iterations because:

1. `ticket` is a local variable that persists across iterations of the `for trade in open_trades` loop
2. If iteration N has `danger=True`, `ticket` gets assigned `trade.get('ticket', 0)`
3. In iteration N+1 with `danger=False`, `ticket` still holds the previous value
4. The `if ticket:` block fires for iteration N+1, closing a trade that does **not** have a tight SL

**Blast radius:** Wrong position closed at rollover, OR a dangerous position NOT closed
because ticket was `0` from a previous iteration.

---

## The Fix

```python
# AFTER (CORRECT — if ticket: is inside the danger block)
if danger and not self._paper:
    ticket = trade.get('ticket', 0)
    if ticket:
        self._adapter.close_position(sym, ticket, trade['lots'], trade['direction'])
        logger.info(f"FOREX {sym}: closed #{ticket} before rollover (tight SL)")
```

The `if ticket:` is now indented one level deeper — it is only reachable when `danger=True AND paper=False`.

---

## Logic Verification

| Condition | Before Fix | After Fix |
|-----------|-----------|-----------|
| `danger=True, paper=False, ticket≠0` | Close fires ✅ | Close fires ✅ |
| `danger=True, paper=False, ticket=0` | Close does not fire (ticket=0) ✅ | Close does not fire ✅ |
| `danger=False` | **Close may fire with stale ticket** ❌ | Close never fires ✅ |
| `paper=True` | **Close may fire with stale ticket** ❌ | Close never fires ✅ |
| Multiple open trades, first has tight SL | **Both trades may be closed** ❌ | Only tight-SL trade closed ✅ |

---

## Unit Test Specification

```python
# tests/test_rollover_guard.py

def test_rollover_only_closes_dangerous_trade():
    """Stale ticket from a safe trade must NOT trigger close of next trade."""
    worker = MockForexWorker(paper=False)
    
    # Trade 1: safe SL distance (no danger)
    safe_trade = {'ticket': 11111, 'direction': 'BULLISH', 'lots': 0.1,
                  'entry_price': 30.00, 'current_sl': 29.80, 'symbol': 'XAGUSD'}
    
    # Trade 2: tight SL distance (danger)
    danger_trade = {'ticket': 22222, 'direction': 'BULLISH', 'lots': 0.1,
                    'entry_price': 30.00, 'current_sl': 29.999, 'symbol': 'XAGUSD'}
    
    closed_tickets = []
    worker._adapter.close_position = lambda sym, tkt, lots, dir: closed_tickets.append(tkt)
    
    # Only the danger trade has tight SL
    worker._run_rollover_guard(trades=[safe_trade, danger_trade])
    
    assert closed_tickets == [22222], f"Expected only danger ticket 22222, got {closed_tickets}"


def test_rollover_skips_close_in_paper_mode():
    """Paper mode must never send MT5 close commands."""
    worker = MockForexWorker(paper=True)
    danger_trade = {'ticket': 33333, 'direction': 'BULLISH', 'lots': 0.1,
                    'entry_price': 30.00, 'current_sl': 29.999, 'symbol': 'XAGUSD'}
    
    closed_tickets = []
    worker._adapter.close_position = lambda sym, tkt, lots, dir: closed_tickets.append(tkt)
    
    worker._run_rollover_guard(trades=[danger_trade])
    
    assert closed_tickets == [], "Paper mode must not close any positions"


def test_rollover_skips_trade_with_no_ticket():
    """Trade with ticket=0 (MT5 order not yet confirmed) must not be closed."""
    worker = MockForexWorker(paper=False)
    no_ticket_trade = {'ticket': 0, 'direction': 'BULLISH', 'lots': 0.1,
                       'entry_price': 30.00, 'current_sl': 29.999, 'symbol': 'XAGUSD'}
    
    closed_tickets = []
    worker._adapter.close_position = lambda sym, tkt, lots, dir: closed_tickets.append(tkt)
    
    worker._run_rollover_guard(trades=[no_ticket_trade])
    
    assert closed_tickets == [], "Trade with ticket=0 must not attempt a close"
```

---

## Status: ✅ FIXED
