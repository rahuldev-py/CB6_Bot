# crypto_main.py
#
# Standalone launcher for the CB6 crypto trading engine (ETH/USDT, Binance Futures).
# Run independently from NSE bot:
#   Terminal 1: python main.py          (NSE — Indian market hours)
#   Terminal 2: python crypto_main.py   (Crypto — 24/5, Binance)
#
# Has NO dependency on Fyers, NSE schedulers, or Indian market infrastructure.

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

from utils.logger import logger


def main():
    # ── Safety gate: crypto is shelved until NSE WR ≥ 56% + GFT funded ──────────
    # To enable live crypto trading, BOTH conditions must hold:
    #   1. CRYPTO_PAPER=false  in .env
    #   2. LIVE_CRYPTO_CONFIRMATION=CONFIRMED  in .env
    # Without both, startup aborts to prevent accidental Binance futures trading.
    _paper = os.getenv('CRYPTO_PAPER', 'true').lower() == 'true'
    _confirmed = os.getenv('LIVE_CRYPTO_CONFIRMATION', '').strip().upper()
    if not _paper and _confirmed != 'CONFIRMED':
        raise RuntimeError(
            "STARTUP ABORT: CRYPTO_PAPER=false but LIVE_CRYPTO_CONFIRMATION != 'CONFIRMED'. "
            "Set LIVE_CRYPTO_CONFIRMATION=CONFIRMED in .env only after NSE WR ≥ 56% "
            "and GFT funded account is profitable."
        )
    if not _paper:
        logger.warning("LIVE CRYPTO MODE — BINANCE FUTURES ORDERS WILL BE PLACED")

    logger.info("=" * 50)
    logger.info("CB6 Crypto Engine Starting")
    logger.info("Market : ETH/USDT Binance Futures (24/5)")
    logger.info("Strategy: ICT Silver Bullet · 5m candles")
    logger.info(f"Mode   : {'Paper' if _paper else '🔴 LIVE — BINANCE FUTURES'}")
    logger.info("=" * 50)

    # Run crypto engine — listener + adapter wiring now handled inside crypto_worker.main()
    try:
        from crypto_engine.crypto_worker import main as _engine_main
        _engine_main()
    except KeyboardInterrupt:
        logger.info("Crypto engine stopped by user")
    except Exception as e:
        logger.error(f"Crypto engine fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
