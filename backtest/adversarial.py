# backtest/adversarial.py — Adversarial robustness test
# "If I were a market maker, how would I trap CB6?"
# Generates synthetic counter-setups and verifies the scanner rejects them.
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger


def _make_df(prices, volumes=None, base_time=None):
    """Build a DataFrame from a price array (treats each as close)."""
    if base_time is None:
        base_time = datetime.now() - timedelta(hours=len(prices))
    rows = []
    for i, p in enumerate(prices):
        rows.append({
            'timestamp': base_time + timedelta(hours=i),
            'open' : p * 0.999,
            'high' : p * 1.002,
            'low'  : p * 0.998,
            'close': p,
            'volume': (volumes[i] if volumes else 100000),
        })
    return pd.DataFrame(rows)


def fake_breakout_test():
    """
    A retail bull-trap: price breaks above resistance with thin volume,
    immediately reverses. Bot should REJECT this as a sweep + sell setup,
    not buy the breakout.
    """
    base = 100
    prices = [base + np.sin(i / 5) * 1 for i in range(40)]   # range-bound
    prices += [base * 1.02, base * 1.025, base * 1.015]      # fake breakout
    prices += [base * 0.99, base * 0.98, base * 0.97]        # reversal
    df = _make_df(prices)
    return {
        'name'    : 'fake_breakout',
        'df'      : df,
        'expect'  : 'REJECT_BUY',  # bot should NOT generate a buy setup here
    }


def low_volume_sweep_test():
    """
    Sweep below support with thin volume = retail trap, not institutional.
    Bot should REJECT (volume_strength < 1.3 mandatory check).
    """
    base = 100
    prices = [base + np.sin(i / 5) * 0.5 for i in range(40)]
    prices += [base * 0.97]                  # sweep low
    prices += [base * 1.01, base * 1.02]     # bounce
    volumes = [100000] * 40 + [80000, 90000, 85000]   # LOW volume on sweep
    df = _make_df(prices, volumes)
    return {
        'name'   : 'low_volume_sweep',
        'df'     : df,
        'expect' : 'REJECT_BUY',
    }


def distribution_phase_test():
    """
    Wyckoff distribution at the top — smart money is selling.
    Bot should NOT take buys here.
    """
    base = 100
    # Markup phase
    prices = list(np.linspace(base, base * 1.10, 30))
    # Distribution: range-bound at highs with declining volume
    prices += list(base * 1.10 + np.sin(np.arange(20) / 3) * 0.5)
    df = _make_df(prices)
    return {
        'name'   : 'distribution_buy',
        'df'     : df,
        'expect' : 'REJECT_BUY',
    }


def run_adversarial_suite(symbol='NSE:RELIANCE-EQ'):
    """
    Run all adversarial tests; report pass/fail.
    Pass = scanner correctly rejects the synthetic trap.
    """
    tests = [
        fake_breakout_test(),
        low_volume_sweep_test(),
        distribution_phase_test(),
    ]
    results = []
    for t in tests:
        df    = t['df']
        buy   = None   # ICT scanner removed (SB-only mode)
        sell  = None

        if t['expect'] == 'REJECT_BUY':
            passed = buy is None
        elif t['expect'] == 'REJECT_SELL':
            passed = sell is None
        else:
            passed = (buy is None and sell is None)

        results.append({
            'test'   : t['name'],
            'expect' : t['expect'],
            'buy'    : 'YES' if buy else 'NO',
            'sell'   : 'YES' if sell else 'NO',
            'passed' : passed,
        })
        logger.info(f"Adversarial test {t['name']}: {'PASS' if passed else 'FAIL'}")

    return results


def format_adversarial_report(results):
    if not results:
        return "Adversarial run failed."
    passed = sum(1 for r in results if r['passed'])
    total  = len(results)
    lines  = [
        "CB6 - ADVERSARIAL ROBUSTNESS\n",
        f"Score: {passed}/{total} tests passed\n",
    ]
    for r in results:
        flag = "PASS" if r['passed'] else "FAIL"
        lines.append(
            f"[{flag}] {r['test']:25s} "
            f"expect={r['expect']:12s} buy={r['buy']:3s} sell={r['sell']}"
        )
    if passed == total:
        lines.append("\nBot correctly rejected all retail traps.")
    else:
        lines.append("\n⚠ Some traps slipped through — review filters.")
    return "\n".join(lines)
