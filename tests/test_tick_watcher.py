# tests/test_tick_watcher.py — Verify tick→trigger logic.
# These tests are PURE — no I/O, no WebSocket. Just price math.
import unittest
import threading
from core.tick_watcher import (
    TickWatcher,
    TRIGGER_BUY_ENTRY, TRIGGER_SELL_ENTRY,
    TRIGGER_SL_LONG, TRIGGER_SL_SHORT,
    TRIGGER_TP_LONG, TRIGGER_TP_SHORT,
)


class TestTriggerLogic(unittest.TestCase):
    def setUp(self):
        self.fired = []
        self.cb = lambda p: self.fired.append(p)
        self.w = TickWatcher()

    def test_buy_entry_fires_on_break_above(self):
        self.w.watch('t1', 'NIFTY', TRIGGER_BUY_ENTRY, 24200, self.cb)
        self.assertEqual(self.w.on_tick('NIFTY', 24199), [])
        self.assertEqual(len(self.w.on_tick('NIFTY', 24201)), 1)
        self.assertEqual(self.fired[0]['kind'], TRIGGER_BUY_ENTRY)

    def test_sell_entry_fires_on_break_below(self):
        self.w.watch('t2', 'NIFTY', TRIGGER_SELL_ENTRY, 24000, self.cb)
        self.assertEqual(self.w.on_tick('NIFTY', 24050), [])
        self.assertEqual(len(self.w.on_tick('NIFTY', 23999)), 1)

    def test_long_sl_fires_when_ltp_drops(self):
        self.w.watch('sl1', 'X', TRIGGER_SL_LONG, 100, self.cb)
        self.w.on_tick('X', 105)
        self.w.on_tick('X', 99)
        self.assertEqual(len(self.fired), 1)

    def test_short_sl_fires_when_ltp_rises(self):
        self.w.watch('sl2', 'X', TRIGGER_SL_SHORT, 100, self.cb)
        self.w.on_tick('X', 95)
        self.w.on_tick('X', 101)
        self.assertEqual(len(self.fired), 1)

    def test_long_target_fires_when_ltp_rises(self):
        self.w.watch('tp1', 'X', TRIGGER_TP_LONG, 110, self.cb)
        self.w.on_tick('X', 105)
        self.w.on_tick('X', 110)
        self.assertEqual(len(self.fired), 1)

    def test_trigger_fires_only_once(self):
        self.w.watch('once', 'X', TRIGGER_BUY_ENTRY, 100, self.cb)
        self.w.on_tick('X', 105)
        self.w.on_tick('X', 110)
        self.w.on_tick('X', 120)
        self.assertEqual(len(self.fired), 1)

    def test_duplicate_id_rejected(self):
        ok1 = self.w.watch('dup', 'X', TRIGGER_BUY_ENTRY, 100, self.cb)
        ok2 = self.w.watch('dup', 'X', TRIGGER_BUY_ENTRY, 100, self.cb)
        self.assertTrue(ok1)
        self.assertFalse(ok2)

    def test_cancel_removes_trigger(self):
        self.w.watch('c1', 'X', TRIGGER_BUY_ENTRY, 100, self.cb)
        self.assertTrue(self.w.cancel('c1'))
        self.w.on_tick('X', 105)
        self.assertEqual(len(self.fired), 0)

    def test_cancel_symbol_removes_all(self):
        self.w.watch('a', 'X', TRIGGER_BUY_ENTRY, 100, self.cb)
        self.w.watch('b', 'X', TRIGGER_TP_LONG, 110, self.cb)
        self.w.watch('c', 'Y', TRIGGER_BUY_ENTRY, 50, self.cb)
        self.assertEqual(self.w.cancel_symbol('X'), 2)
        self.w.on_tick('X', 200)
        self.assertEqual(len(self.fired), 0)
        # Y still fires
        self.w.on_tick('Y', 51)
        self.assertEqual(len(self.fired), 1)

    def test_callback_payload_contains_meta(self):
        self.w.watch('m1', 'X', TRIGGER_BUY_ENTRY, 100, self.cb,
                     meta={'trade_id': 'PT123'})
        self.w.on_tick('X', 101)
        self.assertEqual(self.fired[0]['meta']['trade_id'], 'PT123')

    def test_multiple_triggers_same_tick(self):
        self.w.watch('t1', 'X', TRIGGER_BUY_ENTRY, 100, self.cb)
        self.w.watch('t2', 'X', TRIGGER_TP_LONG, 105, self.cb)
        self.w.on_tick('X', 110)
        self.assertEqual(len(self.fired), 2)

    def test_status_counts(self):
        self.w.watch('a', 'X', TRIGGER_BUY_ENTRY, 100, self.cb)
        self.w.watch('b', 'Y', TRIGGER_BUY_ENTRY, 200, self.cb)
        self.w.on_tick('X', 99)
        s = self.w.status()
        self.assertEqual(s['symbols_watched'], 2)
        self.assertEqual(s['active_watches'], 2)
        self.assertEqual(s['ticks_processed'], 1)
        self.assertEqual(s['triggers_fired'], 0)

    def test_callback_exception_doesnt_crash(self):
        bad_cb = lambda p: 1 / 0
        self.w.watch('bad', 'X', TRIGGER_BUY_ENTRY, 100, bad_cb)
        # Should not raise
        results = self.w.on_tick('X', 101)
        self.assertEqual(len(results), 1)
        self.assertIn('error', results[0])


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_writes(self):
        """Many threads registering watches simultaneously should all succeed."""
        w = TickWatcher()
        errors = []

        def worker(i):
            try:
                for j in range(50):
                    w.watch(f't{i}_{j}', f'SYM{i}', TRIGGER_BUY_ENTRY, 100,
                            lambda p: None)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(errors, [])
        s = w.status()
        self.assertEqual(s['active_watches'], 10 * 50)


if __name__ == '__main__':
    unittest.main()
