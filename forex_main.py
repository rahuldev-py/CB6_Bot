# forex_main.py — CB6 Quantum Forex Engine launcher (root entry point)
#
# Usage:
#   python forex_main.py                        # defaults to FTMO profile
#   python forex_main.py --profile FTMO
#   python forex_main.py --profile GFT_5K_2STEP
#   python forex_main.py --profile PAPER_FOREX
#
# Delegates entirely to forex_engine/forex_main.py (the modular engine launcher).

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import dotenv_values
_env = dotenv_values(os.path.join(os.path.dirname(__file__), '.env'))
for _k, _v in _env.items():
    if _k not in os.environ:
        os.environ[_k] = _v

from forex_engine.forex_main import main

if __name__ == '__main__':
    main()
