# scripts/archive/alert_router.py
#
# ARCHIVED by Wave 4 refactor (2026-05-29).
# Original path: forex_engine/alerts/alert_router.py
# Reason: Zero imports anywhere in the codebase — dead code after Wave 3 FTMO/GFT split.
# Each bot (forex_bot.py, gft_bot.py) now dispatches alerts directly.
# Kept here for reference; safe to delete permanently if never needed.

# Routes alerts to Telegram + dashboard based on platform and event type.

from forex_engine.alerts import telegram_alerts as tg
from forex_engine.alerts import dashboard_alerts as dash


def on_entry(setup: dict, lots: float, risk_usd: float, platform: str,
             ticket: int = 0, paper: bool = False):
    msg = tg.format_entry_alert(setup, lots, risk_usd, ticket=ticket,
                                platform=platform, paper=paper)
    tg.send_alert(msg)
    dash.notify_entry(setup, lots, risk_usd, platform, ticket)


def on_exit(event: dict, platform: str, daily_pnl: float = None):
    msg = tg.format_exit_alert(event, platform=platform, daily_pnl=daily_pnl)
    tg.send_alert(msg)
    dash.notify_exit(event, platform)


def on_phase_advance(phase: str, capital: float, profit: float, platform: str):
    msg = tg.format_phase_alert(phase, capital, profit, platform=platform)
    tg.send_alert(msg)
    dash.notify_phase(phase, capital, profit, platform)


def on_risk_mode_change(mode: str, reason: str, platform: str):
    if mode in ('paused', 'reduced'):
        msg = tg.format_risk_alert(mode, reason, platform=platform)
        tg.send_alert(msg)
    dash.notify_risk_mode(mode, reason, platform)


def on_daily_reset(capital: float, snapshot: float, platform: str):
    msg = tg.format_daily_reset_alert(capital, snapshot, platform=platform)
    tg.send_alert(msg)


def on_blocked(reason: str, symbol: str, platform: str):
    dash.notify_blocked(reason, symbol, platform)


def on_kill_switch(activated: bool, platform: str):
    msg = f"CB6 QUANTUM [{platform}] KILL SWITCH {'ACTIVATED' if activated else 'DEACTIVATED'}"
    tg.send_alert(msg)
    dash.notify_kill_switch(activated, platform)
