# core/risk.py — Pure risk calculations and trade-gating logic.
# Zero I/O. All decisions are pure functions of inputs.
from datetime import datetime
from typing import Dict, List, Tuple
from config.strategy import STRATEGY


def position_size(capital: float, entry_price: float, stop_loss: float,
                  risk_pct: float = None) -> int:
    """
    Returns integer share count for a trade where loss = capital × risk_pct.
    Returns 0 if SL is at/above entry (invalid setup).
    """
    if risk_pct is None:
        risk_pct = STRATEGY.risk_per_trade_pct
    if entry_price <= 0 or stop_loss >= entry_price:
        return 0
    risk_amount = capital * (risk_pct / 100)
    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share <= 0:
        return 0
    return max(0, int(risk_amount / risk_per_share))


def daily_loss_used(closed_trades: List[Dict], today: str = None) -> float:
    """Sum of negative P&L from trades exited today. Returns absolute value."""
    if today is None:
        today = datetime.now().strftime('%Y-%m-%d')
    losses = sum(
        t['pnl'] for t in closed_trades
        if t.get('exit_time', '')[:10] == today and t.get('pnl', 0) < 0
    )
    return abs(losses)


def consecutive_losses(closed_trades: List[Dict]) -> int:
    """Count current streak of consecutive losing trades (most recent first)."""
    count = 0
    for t in reversed(closed_trades):
        if t.get('pnl', 0) < 0:
            count += 1
        else:
            break
    return count


def open_trade_count(open_trades: List[Dict]) -> int:
    return len(open_trades)


def can_enter(state: Dict, capital: float) -> Tuple[bool, str]:
    """
    Pure trade-gating decision. Inputs: paper/live state dict, base capital.
    Returns (allowed, reason).

    Checks (in order):
      1. Daily trade limit
      2. Daily loss count
      3. Consecutive loss streak
      4. Max open positions
      5. Absolute daily loss cap (Rs 1,000 hard stop)
      6. Percentage-based daily loss cap
    """
    today = datetime.now().strftime('%Y-%m-%d')
    daily_trades = state.get('daily_trades', 0)
    daily_losses = state.get('daily_losses', 0)
    open_trades  = state.get('open_trades', [])
    closed       = state.get('closed_trades', [])

    if daily_trades >= STRATEGY.max_trades_per_day:
        return False, f"Daily trade limit hit ({STRATEGY.max_trades_per_day})"

    if daily_losses >= STRATEGY.max_loss_per_day:
        return False, f"Daily loss limit hit ({STRATEGY.max_loss_per_day} losses)"

    streak = consecutive_losses(closed)
    if streak >= STRATEGY.max_consecutive_losses:
        return False, f"Consecutive loss streak ({streak}) — halt until next session"

    if open_trade_count(open_trades) >= STRATEGY.max_open_trades:
        return False, f"Max open positions ({STRATEGY.max_open_trades}) reached"

    loss_used = daily_loss_used(closed, today)

    # Absolute hard cap — Rs 1,000 regardless of capital level
    from settings import MAX_DAILY_LOSS_ABS
    if loss_used >= MAX_DAILY_LOSS_ABS:
        return False, f"Daily loss hard cap hit (Rs {loss_used:.0f} >= Rs {MAX_DAILY_LOSS_ABS:.0f})"

    # Percentage-based cap (secondary)
    loss_limit_pct = capital * STRATEGY.max_daily_loss_pct / 100
    if loss_used >= loss_limit_pct:
        return False, f"Daily DD limit hit (Rs {loss_used:.0f} of Rs {loss_limit_pct:.0f})"

    return True, "OK"
