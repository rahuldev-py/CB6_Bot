# backtest/champion_challenger.py — A/B testing for strategy variants
# Run two variants on the same historical data, compare WR + expectancy.
# Promote the winner monthly.
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger


def run_champion_vs_challenger(fyers, symbol, timeframe='15', days=90, challenger_overrides=None):
    """
    Run two strategy variants on the same data:
      - CHAMPION: current production parameters (settings.py defaults)
      - CHALLENGER: same scanner with `challenger_overrides` applied

    `challenger_overrides` is a dict that monkey-patches settings, e.g.:
      {'MIN_RR_RATIO': 4.0, 'MIN_BUY_SCORE': 9}

    Returns comparison dict with both result sets.
    """
    try:
        from backtest.backtester import run_backtest
        import settings

        # Run champion (current settings)
        logger.info("Running CHAMPION (current settings)...")
        champion = run_backtest(fyers, symbol, timeframe, days)

        if not challenger_overrides:
            return {'champion': champion, 'challenger': None}

        # Snapshot original settings
        snapshot = {k: getattr(settings, k, None) for k in challenger_overrides}
        # Apply overrides
        for k, v in challenger_overrides.items():
            setattr(settings, k, v)

        try:
            logger.info(f"Running CHALLENGER with {challenger_overrides}...")
            challenger = run_backtest(fyers, symbol, timeframe, days)
        finally:
            # Restore
            for k, v in snapshot.items():
                setattr(settings, k, v)

        return {
            'champion'     : champion,
            'challenger'   : challenger,
            'overrides'    : challenger_overrides,
            'winner'       : _pick_winner(champion, challenger),
        }
    except Exception as e:
        logger.error(f"Champion/challenger error: {e}")
        return None


def _pick_winner(c, ch):
    """Pick the variant with higher total R, requiring at least 60% trade overlap."""
    if not c or not ch:
        return 'CHAMPION'
    if c.get('total', 0) == 0 and ch.get('total', 0) == 0:
        return 'TIE_NO_TRADES'
    if c.get('total_r', 0) > ch.get('total_r', 0):
        return 'CHAMPION'
    return 'CHALLENGER'


def format_comparison(result):
    """Telegram-friendly side-by-side comparison."""
    if not result:
        return "Champion/challenger run failed."
    c  = result.get('champion', {})
    ch = result.get('challenger', {})
    if not ch:
        return f"Champion only — {c.get('total', 0)} setups, WR {c.get('win_rate', 0)}%"

    return (
        "CB6 - CHAMPION vs CHALLENGER\n\n"
        f"Symbol     : {c.get('symbol', '?')}\n"
        f"TF         : {c.get('timeframe', '?')}\n"
        f"Period     : {c.get('days', '?')} days\n"
        f"Overrides  : {result.get('overrides', {})}\n\n"
        "             CHAMPION  CHALLENGER\n"
        f"Trades    : {c.get('total', 0):>7}   {ch.get('total', 0):>7}\n"
        f"Win Rate  : {c.get('win_rate', 0):>6}%   {ch.get('win_rate', 0):>6}%\n"
        f"Avg R     : {c.get('avg_r', 0):>7}   {ch.get('avg_r', 0):>7}\n"
        f"Total R   : {c.get('total_r', 0):>7}   {ch.get('total_r', 0):>7}\n\n"
        f"WINNER    : {result.get('winner', '?')}"
    )
