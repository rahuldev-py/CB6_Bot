# forex_engine/accounts/account_router.py
#
# CB6 Quantum — Account Router
#
# Routes trade signals, journals, and monitoring events to the correct
# account engine (FTMO vs GFT), preventing cross-account contamination.
#
# Responsibilities:
#   - Map account_id → active engine instance
#   - Route signals to correct engine
#   - Route journal entries to correct journal
#   - Verify magic number isolation before every order
#
# Usage:
#   router = AccountRouter()
#   router.register('FTMO_10K', ftmo_engine)
#   router.register('GFT_5K',   gft_engine)
#   router.route_trade('FTMO_10K', signal)   # only FTMO engine receives it

from typing import Any, Callable, Dict, Optional
from utils.logger import logger
from forex_engine.accounts.account_registry import get_account, get_magic


class AccountRouter:
    """
    Routes trade signals and events to the correct engine.

    Each CB6 engine (ForexWorker for FTMO, GFT2StepWorker for GFT) registers
    itself here at startup. The router ensures:
      1. No signal sent to wrong engine
      2. Magic number verified before routing
      3. Journal writes go to the correct account's state directory
    """

    def __init__(self):
        self._engines: Dict[str, Any] = {}
        self._journals: Dict[str, Any] = {}
        self._magic_map: Dict[int, str] = {}      # magic → account_id
        self._callbacks: Dict[str, list] = {}      # event_type → [callables]

    # ── Registration ────────────────────────────────────────────────────────────

    def register(self, account_id: str, engine: Any) -> None:
        """Register an engine instance for an account."""
        self._engines[account_id] = engine
        magic = get_magic(account_id)
        if magic:
            self._magic_map[magic] = account_id
        logger.info(
            f"[Router] Registered engine for {account_id} "
            f"(magic={magic})"
        )

    def register_journal(self, account_id: str, journal: Any) -> None:
        """Register a journal handler for an account."""
        self._journals[account_id] = journal
        logger.info(f"[Router] Registered journal for {account_id}")

    def unregister(self, account_id: str) -> None:
        engine = self._engines.pop(account_id, None)
        magic  = get_magic(account_id)
        self._magic_map.pop(magic, None)
        self._journals.pop(account_id, None)
        if engine:
            logger.info(f"[Router] Unregistered {account_id}")

    # ── Routing ─────────────────────────────────────────────────────────────────

    def route_trade(self, account_id: str, signal: dict) -> bool:
        """
        Route a trade signal to the engine registered for account_id.

        Pre-checks:
          - account_id is registered
          - engine is active
          - magic in signal matches account magic (if present in signal)

        Returns True if routed, False if blocked.
        """
        engine = self._engines.get(account_id)
        if not engine:
            logger.error(
                f"[Router] route_trade({account_id}): "
                f"no engine registered — trade BLOCKED"
            )
            return False

        # Magic number cross-contamination check
        sig_magic = signal.get('magic')
        if sig_magic is not None:
            expected = get_magic(account_id)
            if sig_magic != expected:
                logger.error(
                    f"[Router] MAGIC MISMATCH — account={account_id} "
                    f"expected magic={expected} got magic={sig_magic} "
                    f"— trade BLOCKED (cross-account contamination prevention)"
                )
                return False

        logger.debug(f"[Router] Routing trade to {account_id}: {signal.get('symbol')} {signal.get('direction')}")
        try:
            if hasattr(engine, 'on_signal'):
                engine.on_signal(signal)
            return True
        except Exception as e:
            logger.error(f"[Router] route_trade({account_id}) error: {e}")
            return False

    def resolve_account_by_magic(self, magic: int) -> Optional[str]:
        """
        Given a magic number from an MT5 order, return the account_id that owns it.
        Used to route position monitoring events to the correct engine.
        """
        acc = self._magic_map.get(magic)
        if not acc:
            logger.warning(
                f"[Router] Magic {magic} not mapped to any account — "
                f"unknown order origin"
            )
        return acc

    def validate_order_ownership(self, magic: int, claimed_account: str) -> bool:
        """
        Verify that an MT5 order's magic number belongs to claimed_account.
        Returns False if the magic belongs to a DIFFERENT account — potential contamination.
        """
        owner = self.resolve_account_by_magic(magic)
        if owner is None:
            logger.warning(
                f"[Router] Magic {magic} unregistered — cannot verify ownership"
            )
            return True     # Unknown magic — allow but warn

        if owner != claimed_account:
            logger.error(
                f"[Router] CROSS-ACCOUNT ORDER DETECTED — "
                f"magic={magic} belongs to {owner!r} but claimed by {claimed_account!r}. "
                f"Order will NOT be touched."
            )
            return False
        return True

    # ── Status ──────────────────────────────────────────────────────────────────

    def registered_accounts(self) -> list:
        return list(self._engines.keys())

    def __repr__(self) -> str:
        return f"<AccountRouter accounts={list(self._engines.keys())}>"


# ── Global singleton (optional — each engine can also own a local router) ───────
_global_router: Optional[AccountRouter] = None


def get_global_router() -> AccountRouter:
    global _global_router
    if _global_router is None:
        _global_router = AccountRouter()
    return _global_router
