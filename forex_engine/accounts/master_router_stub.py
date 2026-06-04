# forex_engine/accounts/master_router_stub.py
#
# CB6 Quantum — Phase 5: Master Trade Router STUB
#
# ARCHITECTURE PLACEHOLDER — NOT IMPLEMENTED YET.
#
# Future state (Phase 5):
#
#   CB6 MASTER
#       ↓
#   MasterRouter.broadcast(signal)
#       ↓           ↓
#   FTMO engine   GFT engine   (Paper engine)
#       ↓           ↓
#   MT5_FTMO    MT5_GFT_5K
#
# When to implement:
#   - Both FTMO + GFT running live and profitable simultaneously
#   - Want to add a 3rd account (Instant Pro, FTMO Challenge, etc.)
#   - Want paper shadow copy of every live signal
#
# Do NOT implement until:
#   - FTMO free trial funded
#   - GFT 2-step Phase 1 passed
#   - Win rate >= 56% validated over >= 30 trades
#
# See project_cb6_commercial_roadmap.md for timeline.

class MasterRouterStub:
    """
    Stub for future multi-account signal broadcasting.

    When implemented, this will:
      - Accept a single trade signal from the scanner
      - Fan out to all registered account engines simultaneously
      - Apply account-specific lot sizing and risk rules per engine
      - Provide a unified journal view across all accounts

    Currently a no-op placeholder.
    """

    def __init__(self):
        raise NotImplementedError(
            "MasterRouter is Phase 5 — not yet implemented. "
            "Run FTMO and GFT as separate processes via forex_main.py --profile ALL"
        )

    def broadcast(self, signal: dict) -> None:
        raise NotImplementedError

    def add_account(self, account_id: str, engine) -> None:
        raise NotImplementedError
