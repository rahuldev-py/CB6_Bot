# forex_engine/risk/position_reconciler.py
#
# CB6 Quantum — MT5 Position Reconciler
#
# Compares MT5 broker positions (via MT5Connector) against CB6 internal state.
# Detects mismatches and writes alerts to audit_log and Telegram.
#
# Design rules:
#   - NEVER auto-close positions on mismatch (operator must decide).
#   - NEVER block trade entries.
#   - Alert only — append to audit_log and optionally send Telegram message.
#   - Each check is isolated per account_id / magic_number.
#   - Emergency auto-close ONLY when a position is open with no matching internal
#     state AND the account daily DD has already exceeded the hard stop (safety net).
#
# Usage:
#   from forex_engine.risk.position_reconciler import reconcile_account
#   report = reconcile_account('GFT_5K', state, connector, magic=62001)

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from utils.logger import logger


# ─── Public API ────────────────────────────────────────────────────────────────

def reconcile_account(
    account_id: str,
    internal_state: dict,
    connector,                  # MT5Connector instance (duck-typed)
    magic: int = 0,
    telegram_fn=None,           # optional callable(message_str) for alerts
) -> Dict:
    """
    Reconcile MT5 broker positions against internal state for one account.

    Args:
        account_id     : human label e.g. 'GFT_5K'
        internal_state : account state dict with 'open_trades' list
        connector      : MT5Connector with .get_open_positions(magic=magic) method
        magic          : MT5 magic number for this account
        telegram_fn    : optional callable that sends a Telegram alert string

    Returns dict:
        ok                — bool, True if no discrepancies
        phantom_in_mt5    — positions open in MT5 but absent from internal state
        ghost_in_state    — trades in internal state but absent from MT5
        matched           — correctly matched positions
        mismatch_details  — list of human-readable strings describing each issue
        checked_at        — unix timestamp
    """
    result = _empty_report(account_id)

    # ── 1. Fetch MT5 positions ──────────────────────────────────────────────────
    try:
        mt5_positions: List[dict] = connector.get_open_positions(magic=magic) or []
    except Exception as e:
        msg = f"[RECONCILE:{account_id}] MT5 position fetch failed: {e}"
        logger.warning(msg)
        _audit_failure(account_id, str(e))
        result['error'] = str(e)
        return result

    internal_trades: List[dict] = internal_state.get('open_trades', [])

    # ── 2. Build lookup tables ──────────────────────────────────────────────────
    # MT5: keyed by ticket (int)
    mt5_by_ticket: Dict[int, dict] = {
        int(p.get('ticket', 0)): p for p in mt5_positions if p.get('ticket')
    }
    # Internal: keyed by mt5_ticket (int) — prefer ticket field, fall back to id
    internal_by_ticket: Dict[int, dict] = {}
    for t in internal_trades:
        ticket = t.get('mt5_ticket') or t.get('ticket') or t.get('id')
        try:
            ticket = int(ticket)
        except (TypeError, ValueError):
            ticket = 0
        if ticket:
            internal_by_ticket[ticket] = t

    # ── 3. Find phantom (MT5 open, internal missing) ───────────────────────────
    phantoms = []
    for ticket, pos in mt5_by_ticket.items():
        if ticket not in internal_by_ticket:
            sym = pos.get('symbol', '?')
            vol = pos.get('volume', '?')
            phantoms.append({
                'ticket': ticket,
                'symbol': sym,
                'volume': vol,
                'source': 'MT5_ONLY',
            })
            result['mismatch_details'].append(
                f"PHANTOM #{ticket} {sym} vol={vol} — open in MT5, missing from internal state"
            )

    # ── 4. Find ghosts (internal open, MT5 missing) ───────────────────────────
    ghosts = []
    for ticket, trade in internal_by_ticket.items():
        if ticket not in mt5_by_ticket:
            sym = trade.get('symbol', '?')
            dir_ = trade.get('direction', trade.get('side', '?'))
            ghosts.append({
                'ticket': ticket,
                'symbol': sym,
                'direction': dir_,
                'source': 'STATE_ONLY',
            })
            result['mismatch_details'].append(
                f"GHOST #{ticket} {sym} {dir_} — in internal state, absent from MT5"
            )

    # ── 5. Matched positions ───────────────────────────────────────────────────
    matched = []
    for ticket in set(mt5_by_ticket.keys()) & set(internal_by_ticket.keys()):
        matched.append(ticket)

    # ── 6. Populate result ─────────────────────────────────────────────────────
    result['phantom_in_mt5']  = phantoms
    result['ghost_in_state']  = ghosts
    result['matched']         = matched
    result['ok']              = not phantoms and not ghosts
    result['checked_at']      = int(time.time())

    # ── 7. Write to audit log + Telegram if mismatches ─────────────────────────
    if not result['ok']:
        _handle_mismatches(account_id, result, telegram_fn)

    return result


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _handle_mismatches(account_id: str, report: Dict, telegram_fn=None) -> None:
    details = '\n'.join(report['mismatch_details'])
    logger.warning(
        f"[RECONCILE:{account_id}] MISMATCH DETECTED — "
        f"{len(report['phantom_in_mt5'])} phantom(s), "
        f"{len(report['ghost_in_state'])} ghost(s)\n{details}"
    )

    try:
        from utils.audit_log import append as _audit
        _audit(
            'POSITION_RECONCILE_MISMATCH',
            account_id,
            'forex',
            phantom_count=len(report['phantom_in_mt5']),
            ghost_count=len(report['ghost_in_state']),
            details=details,
        )
    except Exception as e:
        logger.error(f"[RECONCILE:{account_id}] audit log write failed: {e}")

    if telegram_fn:
        try:
            n_phantom = len(report['phantom_in_mt5'])
            n_ghost   = len(report['ghost_in_state'])
            msg = (
                f"<b>RECONCILE ALERT — {account_id}</b>\n"
                f"Phantoms (MT5 only): {n_phantom}\n"
                f"Ghosts (state only): {n_ghost}\n\n"
                f"<pre>{details[:800]}</pre>\n\n"
                f"Manual review required. No auto-close performed."
            )
            telegram_fn(msg)
        except Exception as e:
            logger.error(f"[RECONCILE:{account_id}] Telegram alert failed: {e}")


def _audit_failure(account_id: str, error: str) -> None:
    try:
        from utils.audit_log import append as _audit
        _audit(
            'POSITION_RECONCILE_FETCH_FAILED',
            account_id,
            'forex',
            error=error,
        )
    except Exception:
        pass


def _empty_report(account_id: str) -> Dict:
    return {
        'account_id'     : account_id,
        'ok'             : True,
        'phantom_in_mt5' : [],
        'ghost_in_state' : [],
        'matched'        : [],
        'mismatch_details': [],
        'checked_at'     : int(time.time()),
        'error'          : None,
    }
