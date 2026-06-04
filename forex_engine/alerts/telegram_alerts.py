# forex_engine/alerts/telegram_alerts.py
# Telegram alert formatters and sender for CB6 Quantum forex engine.

from datetime import datetime
from utils.logger import logger
from forex_engine.forex_instruments import INSTRUMENTS


def send_alert(msg: str):
    """Send a message via the communications forex bot."""
    try:
        from communications.forex_bot import send_alert as _send
        _send(msg)
    except Exception:
        logger.info(f"[FOREX TG] {msg[:120]}")


def format_entry_alert(
    setup: dict,
    lots: float,
    risk_usd: float,
    ticket: int = 0,
    platform: str = 'FTMO',
    paper: bool = False,
) -> str:
    sig   = setup['entry_signal']
    sym   = setup['symbol']
    label = INSTRUMENTS.get(sym, {}).get('label', sym)
    dlab  = 'LONG (BUY)' if setup['direction'] == 'BULLISH' else 'SHORT (SELL)'
    ut    = setup.get('ut_bot', {})

    from forex_engine.scanner.signal_scanner import current_session_label
    session = current_session_label()

    mode_line   = f'🔴 LIVE — {platform}' if not paper else f'Paper Trading ({platform})'
    ticket_line = f"MT5 Ticket : #{ticket}" if (not paper and ticket) else (
                  '⚠️ Paper only — no MT5 order' if not paper else '')

    liq_sweep       = setup.get('liq_sweep')
    sweep_confirmed = setup.get('sweep_confirmed', False)
    if sweep_confirmed and liq_sweep:
        s_type     = 'LOW swept ✅' if liq_sweep['sweep_type'] == 'LOW_SWEEP' else 'HIGH swept ✅'
        sweep_line = f"Liq Sweep  : {s_type} @ {liq_sweep['swept_level']}  ({liq_sweep['candles_ago']} candles ago)\n"
    elif liq_sweep:
        sweep_line = f"Liq Sweep  : {liq_sweep['sweep_type']} (opposite dir — caution)\n"
    else:
        sweep_line = "Liq Sweep  : None detected\n"

    ob      = setup.get('ob')
    ob_line = (f"Order Block: {ob['type']} {ob['ob_low']:.5f}–{ob['ob_high']:.5f} ✅\n"
               if ob else "Order Block: Not detected\n")

    sim_ratio = setup.get('sim_ratio', 0.0)
    boost     = setup.get('lot_boost', 1.0)
    if sim_ratio >= 0.55:
        sim_line = f"A+ Match   : {sim_ratio:.0%} ⭐ — lots boosted {boost}×\n"
    elif sim_ratio > 0:
        sim_line = f"A+ Match   : {sim_ratio:.0%} (threshold 55%)\n"
    else:
        sim_line = ""

    return (
        f"<b>CB6 QUANTUM — FOREX {label} [{setup['confluence']}/15]</b>\n\n"
        f"Direction  : {dlab}\n"
        f"Session    : {session}\n"
        f"Time       : {datetime.now().strftime('%H:%M:%S IST')}\n\n"
        f"<b>STRUCTURE</b>\n"
        f"{sweep_line}"
        f"DOL        : {sig['dol_level']}\n"
        f"MSS        : {sig['mss_level']} ({setup['mss_type']})\n"
        f"FVG Zone   : {sig['fvg_low']} – {sig['fvg_high']}\n"
        f"FVG Status : {'IN ZONE ✅' if setup.get('in_fvg') else 'APPROACHING'}\n"
        f"{ob_line}"
        f"UT Bot     : {ut.get('trend','?')} | {'✅' if ut.get('aligned') else '⚠️'}\n\n"
        f"<b>TRADE PLAN</b>\n"
        f"Entry      : {sig['entry']}\n"
        f"SL         : {sig['stop_loss']}\n"
        f"T1 (1/3)   : {sig['target1']}  (1:2R)\n"
        f"T2 (1/3)   : {sig['target2']}  (1:3R)\n"
        f"T3 (1/3)   : {sig['target3']}  (DOL)\n"
        f"RR         : 1:{sig['rr_ratio']}\n"
        f"Lots       : {lots}  |  Risk ${risk_usd}\n"
        f"{sim_line}\n"
        f"Mode       : {mode_line}\n"
        + (f"{ticket_line}\n" if ticket_line else "")
    )


def format_exit_alert(event: dict, platform: str = 'FTMO', daily_pnl: float = None) -> str:
    t     = event['trade']
    sym   = t.get('symbol', 'XAUUSD')
    label = INSTRUMENTS.get(sym, {}).get('label', sym)
    pnl   = event['pnl']
    sign  = '+' if pnl >= 0 else ''
    etype = event['type']
    dlab  = 'LONG' if t.get('direction') == 'BULLISH' else 'SHORT'

    result_map = {
        'SL'    : '🔴 STOP LOSS HIT',
        'T1_BE' : '🟡 T1 HIT — SL moved to breakeven (min-lot: full position runs to T2)',
        'T1'    : '🟡 T1 HIT — 1/3 booked, SL → breakeven',
        'T2'    : '🟢 T2 HIT — position closed, profit locked',
        'T3'    : '✅ T3 HIT — full target reached (DOL)',
        'TIME'  : '⏱️ TIME EXIT — 2hr max hold reached',
        'MAE'   : '⚠️ MAE EXIT — adverse excursion limit hit',
        'BE'    : '🟡 BREAKEVEN EXIT',
    }
    result = result_map.get(etype, f'{etype} HIT')

    daily_line = ''
    if daily_pnl is not None:
        daily_line = f"Daily PnL  : {'+' if daily_pnl >= 0 else ''}${daily_pnl:.2f} [{platform}]\n"

    return (
        f"<b>CB6 QUANTUM — FOREX {label} [{platform}]</b>\n"
        f"{result}\n\n"
        f"Direction  : {dlab}\n"
        f"Entry      : {t['entry_price']}\n"
        f"Exit       : {event['price']}\n"
        f"PnL        : {sign}${pnl:.2f}\n"
        + daily_line
        + f"Time       : {datetime.now().strftime('%H:%M:%S IST')}\n"
        f"Trade      : {t['id']}"
    )


def format_phase_alert(phase: str, capital: float, profit: float, platform: str = 'GFT-2STEP') -> str:
    if phase == 'phase_2':
        heading = '🎯 PHASE 1 COMPLETE — Advancing to Phase 2'
        detail  = f"Phase 2 target: $300 (6%) from current equity ${capital:.2f}"
    elif phase == 'funded':
        heading = '🏆 PHASE 2 COMPLETE — FUNDED ACCOUNT ACHIEVED!'
        detail  = f"Total profit: +${profit:.2f}. Awaiting funded account credentials."
    else:
        heading = f'📊 Phase update: {phase}'
        detail  = f"Profit: +${profit:.2f} | Capital: ${capital:.2f}"

    return (
        f"<b>CB6 QUANTUM [{platform}]</b>\n"
        f"{heading}\n\n"
        f"{detail}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S IST')}"
    )


def format_risk_alert(mode: str, reason: str, platform: str = 'FTMO') -> str:
    icon = {'paused': '🛑', 'reduced': '⚠️', 'warning': '🟡'}.get(mode, 'ℹ️')
    return (
        f"<b>CB6 QUANTUM [{platform}] RISK ALERT</b>\n"
        f"{icon} Mode: {mode.upper()}\n"
        f"Reason: {reason}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S IST')}"
    )


def format_daily_reset_alert(capital: float, snapshot: float, platform: str = 'FTMO') -> str:
    return (
        f"<b>CB6 QUANTUM [{platform}]</b>\n"
        f"📅 Daily Reset (5PM EST)\n"
        f"Snapshot equity: ${snapshot:.2f}\n"
        f"Current capital: ${capital:.2f}\n"
        f"Time: {datetime.now().strftime('%H:%M:%S IST')}"
    )
