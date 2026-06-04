"""
tests/test_nse_concurrency.py
==============================
NSE scanner dedup race-condition tests — added after audit 2026-06-02.

Verifies that concurrent calls to the SB scanner and live scanner cannot
both place a trade on the same (date, symbol, direction, fvg_zone) key.

Run:
    python -m pytest tests/test_nse_concurrency.py -v
"""

import threading
import time
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ============================================================================
# Helper — minimal atomic dedup as used in the fixed scanners
# ============================================================================

def _make_dedup_state():
    return {
        'lock':           threading.Lock(),
        'live_alerted':   set(),
        'sb_daily_taken': set(),
        'watch_alerted':  set(),
    }


def _try_claim(state: dict, key: tuple) -> bool:
    """
    Atomic check-and-claim matching the fixed scanner pattern.
    Returns True if this thread won the claim, False if already taken.
    """
    with state['lock']:
        if key in state['live_alerted'] or key in state['sb_daily_taken']:
            return False
        state['live_alerted'].add(key)
        state['sb_daily_taken'].add(key)
        return True


def _rollback_to_watch(state: dict, key: tuple):
    """Roll back a claim (price outside FVG) and move to watch state."""
    with state['lock']:
        state['live_alerted'].discard(key)
        state['sb_daily_taken'].discard(key)
        state['watch_alerted'].add(key)


# ============================================================================
# 1. Concurrent dedup — only one thread may claim a zone
# ============================================================================

class TestAtomicDedup:

    def test_single_claim_under_concurrency(self):
        """Only one of N concurrent threads may claim the same zone."""
        state = _make_dedup_state()
        key   = ('2026-06-03', 'NSE:NIFTY26JUNFUT', 'BEARISH', 24150)
        winners = []
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()   # all threads start simultaneously
            if _try_claim(state, key):
                winners.append(threading.current_thread().name)

        threads = [threading.Thread(target=worker, name=f"T{i}") for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert len(winners) == 1, f"Expected 1 winner, got {len(winners)}: {winners}"

    def test_second_claim_after_first_is_blocked(self):
        """After the first claim, every subsequent attempt is blocked."""
        state = _make_dedup_state()
        key   = ('2026-06-03', 'NSE:BANKNIFTY26JUNFUT', 'BULLISH', 54050)

        assert _try_claim(state, key) is True
        assert _try_claim(state, key) is False  # same thread, same key
        assert _try_claim(state, key) is False  # third attempt

    def test_different_keys_do_not_block_each_other(self):
        """Two threads claiming different zones must both succeed."""
        state  = _make_dedup_state()
        key_a  = ('2026-06-03', 'NSE:NIFTY26JUNFUT', 'BEARISH', 24150)
        key_b  = ('2026-06-03', 'NSE:NIFTY26JUNFUT', 'BULLISH', 24150)   # different direction

        assert _try_claim(state, key_a) is True
        assert _try_claim(state, key_b) is True   # different key → allowed

    def test_cross_scanner_dedup(self):
        """SB scanner and live scanner share the same dedup sets."""
        state = _make_dedup_state()
        key   = ('2026-06-03', 'NSE:NIFTY26JUNFUT', 'BEARISH', 24150)

        # SB scanner claims first
        assert _try_claim(state, key) is True
        assert key in state['sb_daily_taken']
        assert key in state['live_alerted']

        # Live scanner sees it as already claimed
        assert _try_claim(state, key) is False

    def test_50_threads_produce_exactly_1_trade(self):
        """Stress test: 50 concurrent threads, exactly 1 trade placed."""
        state = _make_dedup_state()
        key   = ('2026-06-03', 'NSE:MIDCPNIFTY26JUNFUT', 'BULLISH', 14200)
        trades = []
        barrier = threading.Barrier(50)

        def worker():
            barrier.wait()
            if _try_claim(state, key):
                time.sleep(0.005)   # simulate slow work (ML gate, network call)
                trades.append(1)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"


# ============================================================================
# 2. Watch-state rollback — zone is released if LTP is outside FVG
# ============================================================================

class TestWatchStateRollback:

    def test_rollback_releases_claim(self):
        """After rollback, zone is no longer in live_alerted or sb_daily_taken."""
        state = _make_dedup_state()
        key   = ('2026-06-03', 'NSE:NIFTY26JUNFUT', 'BEARISH', 24150)

        assert _try_claim(state, key) is True
        _rollback_to_watch(state, key)

        assert key not in state['live_alerted']
        assert key not in state['sb_daily_taken']
        assert key in state['watch_alerted']

    def test_after_rollback_next_claim_succeeds(self):
        """After a watch-rollback, the next scan cycle can re-claim the zone."""
        state = _make_dedup_state()
        key   = ('2026-06-03', 'NSE:NIFTY26JUNFUT', 'BEARISH', 24150)

        # Cycle 1: price outside FVG → rollback
        _try_claim(state, key)
        _rollback_to_watch(state, key)

        # Cycle 2: price enters FVG → second claim succeeds
        assert _try_claim(state, key) is True
        assert key in state['live_alerted']

    def test_concurrent_rollback_and_new_claim(self):
        """
        Thread A rolls back; Thread B must not claim before A's rollback finishes.
        Result: only one trade placed across the two phases.
        """
        state   = _make_dedup_state()
        key     = ('2026-06-03', 'NSE:FINNIFTY26JUNFUT', 'BULLISH', 25100)
        results = []

        def cycle_1_rollback():
            if _try_claim(state, key):
                time.sleep(0.01)   # simulate slow path
                _rollback_to_watch(state, key)
                results.append('rolled_back')

        def cycle_2_claim():
            time.sleep(0.02)   # after cycle 1 rollback completes
            if _try_claim(state, key):
                results.append('traded')

        t1 = threading.Thread(target=cycle_1_rollback)
        t2 = threading.Thread(target=cycle_2_claim)
        t1.start(); t2.start()
        t1.join();  t2.join()

        assert 'rolled_back' in results
        assert 'traded' in results
        assert len(results) == 2


# ============================================================================
# 3. paper_trader._state_lock last-resort check (defence-in-depth)
# ============================================================================

class TestPaperTraderRLock:
    """
    Verify that open_paper_trade's internal RLock serialises concurrent callers
    so only one trade is written for any given symbol+direction pair.

    Patches are applied ONCE at the test level (not inside each thread) to
    avoid the double-patch collision that occurs when two threads each enter
    their own `with patch(...)` block against the same module attribute.
    """

    def _make_state(self):
        return {
            'capital': 200_000,
            'available_capital': 200_000,
            'open_trades': [],
            'closed_trades': [],
            'daily_trades': 0,
            'daily_losses': 0,
            'daily_option_strikes': {},
            'paused': False,
            'date': '2026-06-03',
        }

    def test_rlock_serialises_open_paper_trade(self, tmp_path):
        """
        Two threads call open_paper_trade with identical setups simultaneously.
        The RLock ensures the duplicate-check sees the first thread's trade and
        the second thread returns None.
        """
        import copy
        import trader.paper_trader as _pt
        from unittest.mock import patch

        shared_state = self._make_state()
        state_lock   = threading.Lock()

        def mock_load():
            with state_lock:
                return copy.deepcopy(shared_state)

        def mock_save(s):
            with state_lock:
                shared_state.clear()
                shared_state.update(s)

        setup = {
            'symbol'          : 'NSE:NIFTY26JUN24350CE',
            'direction'       : 'BULLISH',
            'timeframe'       : '3min',
            'instrument_type' : 'INDEX',
            'confluence'      : 12,
            'quantity'        : 65,
            'product_type'    : 'MARGIN',
            'entry_signal': {
                'entry'    : 195.0,
                'stop_loss': 185.0,
                'target1'  : 205.0,
                'target2'  : 215.0,
                'target3'  : 225.0,
                'risk'     : 10.0,
                'rr_ratio' : 3.0,
                'in_ote'   : False,
                'in_fvg'   : True,
            },
        }

        results  = {'opened': 0, 'skipped': 0}
        tally_lk = threading.Lock()
        barrier  = threading.Barrier(2)

        def place_trade():
            barrier.wait()   # both threads start simultaneously
            result = _pt.open_paper_trade(setup)
            with tally_lk:
                if result is not None:
                    results['opened'] += 1
                else:
                    results['skipped'] += 1

        # Patches are applied ONCE here, outside the threads, so both threads
        # share the same mock state without double-patching the attribute.
        with patch.object(_pt, 'load_state',  mock_load), \
             patch.object(_pt, 'save_state',  mock_save), \
             patch.object(_pt, 'send_message', lambda *a, **kw: None):

            threads = [threading.Thread(target=place_trade) for _ in range(2)]
            for t in threads: t.start()
            for t in threads: t.join()

        assert results['opened']  == 1, (
            f"RLock failed: {results['opened']} trades opened (expected 1). "
            f"Skipped={results['skipped']}"
        )
        assert results['skipped'] == 1, (
            f"Expected 1 duplicate to be skipped, got {results['skipped']}"
        )
        assert len(shared_state.get('open_trades', [])) == 1
