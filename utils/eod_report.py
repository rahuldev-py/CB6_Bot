# utils/eod_report.py
#
# CB6 Quantum — End-of-Day Report Generator
#
# Fires automatically at:
#   15:30 IST — after NSE market close (wired into main.py schedule_daily)
#   20:00 UTC — after GFT NY kill zone close (wired into forex_main.py)
#
# Builds a full-text report covering all three live accounts + ML/Hermes data,
# saves it as reports/eod_YYYYMMDD.txt, then sends the .txt file to BOTH
# Telegram bots (NSE bot + Forex bot) so every channel sees the complete picture.

import os
import json
import logging
import datetime
import requests

logger = logging.getLogger('cb6.eod_report')

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def generate_and_send(trigger: str = 'NSE_CLOSE') -> str:
    """
    Build the EOD report, save to reports/, send to both Telegram bots.
    trigger: 'NSE_CLOSE' (15:30 IST) | 'GFT_CLOSE' (20:00 UTC) | 'MANUAL'
    Returns the saved file path.
    """
    now_ist = _now_ist()
    now_utc = datetime.datetime.utcnow()
    date_str = now_ist.strftime('%Y-%m-%d')
    ts_str   = now_ist.strftime('%d %b %Y  %H:%M IST')

    lines = []

    # ── Header ──────────────────────────────────────────────────────────────
    lines += [
        '=' * 62,
        'CB6 QUANTUM — END OF DAY REPORT',
        f'Date      : {ts_str}',
        f'Trigger   : {trigger}',
        f'UTC       : {now_utc.strftime("%H:%M UTC")}',
        '=' * 62,
        '',
    ]

    # ── NSE — Fyers Live ─────────────────────────────────────────────────────
    lines += _section_nse(date_str)

    # ── GFT $5K 2-Step ──────────────────────────────────────────────────────
    lines += _section_gft_5k(date_str)

    # ── GFT $1K Instant ─────────────────────────────────────────────────────
    lines += _section_gft_1k(date_str)

    # ── GFT $10K Instant (pending credentials) ──────────────────────────────
    lines += _section_gft_10k(date_str)

    # ── Pattern DB — Today's Trade Quality ──────────────────────────────────
    lines += _section_pattern_db(date_str)

    # ── Hermes Learning Loop — Pending Nudges ───────────────────────────────
    lines += _section_nudges()

    # ── ML Shadow System ────────────────────────────────────────────────────
    lines += _section_ml()

    # ── GFT Phase 1 Progress ────────────────────────────────────────────────
    lines += _section_phase_progress()

    # ── Tomorrow Watch List ──────────────────────────────────────────────────
    lines += _section_tomorrow()

    # ── Footer ───────────────────────────────────────────────────────────────
    lines += [
        '=' * 62,
        'Next session: 09:15 IST  |  London kill zone: 07:00 UTC',
        'Run /daily-brief at open for market structure update.',
        '=' * 62,
    ]

    report_text = '\n'.join(lines)

    # ── Save to disk ─────────────────────────────────────────────────────────
    reports_dir = os.path.join(_ROOT, 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    fname = f"eod_{date_str.replace('-', '')}_{trigger.lower()}.txt"
    fpath = os.path.join(reports_dir, fname)
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(report_text)
    logger.info(f"EOD report saved: {fpath}")

    # ── Send to Telegram ─────────────────────────────────────────────────────
    caption = f"CB6 EOD Report — {ts_str}"
    _send_to_nse_bot(fpath, caption, report_text)
    _send_to_forex_bot(fpath, caption, report_text)

    return fpath


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def _section_nse(date_str: str) -> list:
    lines = ['── NSE — FYERS LIVE (Rs 26,000) ─────────────────────────', '']
    try:
        from trader.paper_trader import load_state
        state = load_state()
        cap   = state.get('capital', 26000)

        today_closed = [
            t for t in state.get('closed_trades', [])
            if t.get('exit_time', '')[:10] == date_str
            and t.get('status') != 'EXPIRED_OVERNIGHT'
        ]
        wins     = sum(1 for t in today_closed if t.get('pnl', 0) > 0)
        losses   = len(today_closed) - wins
        pnl      = sum(t.get('pnl', 0) for t in today_closed)
        wr       = round(wins / max(len(today_closed), 1) * 100, 1)
        daily_ret = round(pnl / cap * 100, 2) if cap else 0

        lines += [
            f"  Capital      : Rs {cap:,.0f}",
            f"  Trades Today : {len(today_closed)}  (Wins: {wins}  Losses: {losses})",
            f"  Win Rate     : {wr}%",
            f"  Realized PnL : Rs {pnl:+,.0f}  ({daily_ret:+.2f}%)",
        ]

        open_trades = state.get('open_trades', [])
        if open_trades:
            lines.append(f"  Open Trades  : {len(open_trades)}  (still running)")
        else:
            lines.append("  Open Trades  : 0  (all closed)")

        if today_closed:
            lines.append('')
            lines.append('  TRADE LOG:')
            for t in today_closed:
                sym = t.get('symbol', '').replace('NSE:', '').replace('-EQ', '')
                pnl_val = t.get('pnl', 0)
                res  = 'WIN ' if pnl_val > 0 else ('BE  ' if pnl_val == 0 else 'LOSS')
                xr   = t.get('exit_reason', '')
                lines.append(f"    {sym:<18} {res}  Rs {pnl_val:+,.0f}  [{xr}]")
    except Exception as e:
        lines.append(f"  [NSE data unavailable: {e}]")

    lines.append('')
    return lines


def _section_gft_5k(date_str: str) -> list:
    lines = ['── GFT $5K 2-STEP  (PRIMARY ACCOUNT) ───────────────────', '']
    state = _load_state('data/gft_5k/state.json')
    if not state:
        lines += ['  [State file not found]', '']
        return lines
    try:
        cap          = state.get('capital', 0)
        start        = state.get('starting_capital', 5000)
        total_pnl    = cap - start
        daily_snap   = state.get('gft_daily_snapshot', cap)
        daily_pnl    = cap - daily_snap
        phase1_done  = state.get('phase_1_passed', False)
        trading_days = state.get('trading_days_active', 0)
        need_profit  = 400 - max(0, total_pnl)   # Phase 1 = +8% = $400

        today_closed = _today_trades(state, date_str)
        wins  = sum(1 for t in today_closed if t.get('pnl_usd', t.get('pnl', 0)) > 0)
        pnl_d = sum(t.get('pnl_usd', t.get('pnl', 0)) for t in today_closed)

        lines += [
            f"  Capital      : ${cap:,.2f}  (started ${start:,.2f})",
            f"  Total PnL    : ${total_pnl:+,.2f}",
            f"  Daily PnL    : ${daily_pnl:+,.2f}  (limit: -$200)",
            f"  Phase 1      : {'PASSED ✓' if phase1_done else f'Need ${need_profit:+.2f} more  |  Days: {trading_days}/3 min'}",
            f"  Trades Today : {len(today_closed)}  (Wins: {wins}  Losses: {len(today_closed)-wins})",
            f"  Today PnL    : ${pnl_d:+,.2f}",
            f"  Risk Mode    : {state.get('risk_mode', 'normal')}",
        ]

        if today_closed:
            lines.append('')
            lines.append('  TRADE LOG:')
            for t in today_closed:
                sym   = t.get('symbol', '')
                pval  = t.get('pnl_usd', t.get('pnl', 0))
                res   = 'WIN ' if pval > 0 else ('BE  ' if pval == 0 else 'LOSS')
                xr    = t.get('exit_reason', '')
                lines.append(f"    {sym:<10} {res}  ${pval:+.2f}  [{xr}]")
    except Exception as e:
        lines.append(f"  [GFT 5K data error: {e}]")

    lines.append('')
    return lines


def _section_gft_1k(date_str: str) -> list:
    lines = ['── GFT $1K INSTANT  (SECONDARY ACCOUNT) ────────────────', '']
    state = _load_state('data/gft_1k_instant/state.json')
    if not state:
        lines += ['  [State file not found]', '']
        return lines
    try:
        cap       = state.get('capital', 0)
        start     = state.get('starting_capital', 1000)
        total_pnl = cap - start
        daily_snap = state.get('gft_daily_snapshot', cap)
        daily_pnl  = cap - daily_snap

        today_closed = _today_trades(state, date_str)
        wins  = sum(1 for t in today_closed if t.get('pnl_usd', t.get('pnl', 0)) > 0)
        pnl_d = sum(t.get('pnl_usd', t.get('pnl', 0)) for t in today_closed)

        lines += [
            f"  Capital      : ${cap:,.2f}  (started ${start:,.2f})",
            f"  Total PnL    : ${total_pnl:+,.2f}",
            f"  Daily PnL    : ${daily_pnl:+,.2f}  (limit: -$30)",
            f"  Trades Today : {len(today_closed)}  (Wins: {wins}  Losses: {len(today_closed)-wins})",
            f"  Today PnL    : ${pnl_d:+,.2f}",
            f"  Risk Mode    : {state.get('risk_mode', 'normal')}",
        ]

        if today_closed:
            lines.append('')
            lines.append('  TRADE LOG:')
            for t in today_closed:
                sym  = t.get('symbol', '')
                pval = t.get('pnl_usd', t.get('pnl', 0))
                res  = 'WIN ' if pval > 0 else ('BE  ' if pval == 0 else 'LOSS')
                xr   = t.get('exit_reason', '')
                lines.append(f"    {sym:<10} {res}  ${pval:+.2f}  [{xr}]")
    except Exception as e:
        lines.append(f"  [GFT 1K data error: {e}]")

    lines.append('')
    return lines


def _section_gft_10k(date_str: str) -> list:
    lines = ['── GFT $10K INSTANT  (PENDING CREDENTIALS) ─────────────', '']
    state = _load_state('data/gft_10k/state.json')
    if not state:
        lines += ['  [Awaiting credentials — not yet active]', '']
        return lines
    try:
        cap        = state.get('capital', 0)
        start      = state.get('starting_capital', 10000)
        total_pnl  = cap - start
        daily_snap = state.get('daily_snapshot', cap)
        daily_pnl  = cap - daily_snap
        lines += [
            f"  Capital   : ${cap:,.2f}  |  Total PnL: ${total_pnl:+,.2f}",
            f"  Daily PnL : ${daily_pnl:+,.2f}  (limit: -$300 / 3%)",
        ]
    except Exception as e:
        lines.append(f"  [GFT $10K data error: {e}]")
    lines.append('')
    return lines


def _section_pattern_db(date_str: str) -> list:
    lines = ['── PATTERN DB — TODAY\'S TRADE QUALITY ──────────────────', '']
    try:
        from ml_engine.memory.trade_pattern_db import query, get_stats
        today_trades = [t for t in query() if t.get('recorded_at', '')[:10] == date_str]
        if not today_trades:
            lines += ['  No trades recorded in pattern DB today.', '']
            return lines

        total = len(today_trades)
        wins  = sum(1 for t in today_trades if t.get('outcome') == 'WIN')
        wr    = round(wins / total * 100, 1)
        avg_r = round(sum(t.get('pnl_r', 0) for t in today_trades) / total, 2)
        aligned = sum(1 for t in today_trades if t.get('h4_aligned') == 1)
        avg_conf = round(sum(t.get('confluence', 0) for t in today_trades) / total, 1)
        avg_fvg  = round(sum(t.get('fvg_body_pct', 0) for t in today_trades) / total, 1)

        lines += [
            f"  Trades Logged : {total}  (Wins: {wins}  Losses: {total-wins})",
            f"  Win Rate      : {wr}%",
            f"  Avg R         : {avg_r:+.2f}",
            f"  H4 Aligned    : {aligned}/{total}  ({round(aligned/total*100,0):.0f}%)",
            f"  Avg Confluence: {avg_conf}",
            f"  Avg FVG Body% : {avg_fvg}%",
        ]

        # Best performing pattern today
        stats = get_stats()
        if stats:
            best = max(stats, key=lambda x: x.get('win_rate_pct', 0))
            lines.append(
                f"  Best Pattern  : {best['symbol']} {best['direction']} "
                f"{best['session']}  →  {best['win_rate_pct']}% WR  ({best['total']} trades)"
            )
    except Exception as e:
        lines.append(f"  [Pattern DB unavailable: {e}]")
    lines.append('')
    return lines


def _section_nudges() -> list:
    lines = ['── HERMES LEARNING LOOP — PENDING NUDGES ────────────────', '']
    try:
        from ml_engine.learning.feedback_loop import get_pending_nudges
        nudges = get_pending_nudges()
        if not nudges:
            lines += ['  No pending parameter nudges — system stable.', '']
            return lines
        lines.append(f"  {len(nudges)} nudge proposal(s) waiting for approval:")
        for n in nudges[:5]:
            lines.append(
                f"    [{n.get('symbol')}] {n.get('direction')} {n.get('feature')}  "
                f"(currently {n.get('current_val')})  →  {n.get('evidence', '')[:80]}"
            )
        if len(nudges) > 5:
            lines.append(f"    ... and {len(nudges)-5} more. Run /parameter-optimizer to review.")
        else:
            lines.append('    Run /parameter-optimizer to review and approve.')
    except Exception as e:
        lines.append(f"  [Nudge data unavailable: {e}]")
    lines.append('')
    return lines


def _section_ml() -> list:
    lines = ['── ML SHADOW SYSTEM ────────────────────────────────────', '']
    try:
        reg_path = os.path.join(_ROOT, 'ml_engine', 'config', 'model_registry.json')
        if os.path.exists(reg_path):
            with open(reg_path, encoding='utf-8') as f:
                reg = json.load(f)
            for market, info in reg.items():
                ver   = info.get('version', '?')
                acc   = info.get('val_accuracy', 0)
                ts    = info.get('trained_at', '?')
                lines.append(f"  {market:<12} v{ver}  acc={acc:.1%}  trained={ts[:10]}")
        else:
            lines.append('  [Model registry not found — run /ml_train to build models]')
    except Exception as e:
        lines.append(f"  [ML data error: {e}]")
    lines += ['  Mode: SHADOW ONLY — predictions logged, never placed orders', '']
    return lines


def _section_phase_progress() -> list:
    lines = ['── GFT PHASE 1 PROGRESS TRACKER ─────────────────────────', '']
    state = _load_state('data/gft_5k/state.json')
    if not state:
        lines += ['  [No state]', '']
        return lines
    try:
        cap         = state.get('capital', 0)
        start       = state.get('starting_capital', 5000)
        total_pnl   = cap - start
        phase1_done = state.get('phase_1_passed', False)
        trading_days = state.get('trading_days_active', 0)
        phase1_target = 400.0
        phase1_need   = max(0, phase1_target - total_pnl)
        pct_done      = min(100, round(total_pnl / phase1_target * 100, 1)) if phase1_target > 0 else 0

        if phase1_done:
            phase2_pnl    = total_pnl - phase1_target
            phase2_target = 300.0
            phase2_need   = max(0, phase2_target - phase2_pnl)
            lines += [
                '  Phase 1 : PASSED ✓',
                f"  Phase 2 : ${phase2_pnl:+.2f} / ${phase2_target:.0f}  |  Need: ${phase2_need:.2f} more",
                f"  Trading Days: {trading_days}",
            ]
        else:
            bar_filled = int(pct_done / 5)
            bar = '[' + '█' * bar_filled + '░' * (20 - bar_filled) + ']'
            lines += [
                f"  Phase 1 Progress : {bar} {pct_done:.1f}%",
                f"  Profit so far    : ${total_pnl:+.2f}  /  Target: +${phase1_target:.0f}",
                f"  Still needed     : ${phase1_need:.2f}",
                f"  Trading days     : {trading_days} / 3 minimum",
                f"  Internal limits  : Warn $100 | Reduce 50% $140 | Hard stop $170/day",
            ]
    except Exception as e:
        lines.append(f"  [Phase progress error: {e}]")
    lines.append('')
    return lines


def _section_tomorrow() -> list:
    lines = ['── TOMORROW\'S WATCH LIST ─────────────────────────────────', '']
    lines += [
        '  Fill in after running /market-analyst:',
        '  NIFTY    Buy-side DOL: ____  |  Sell-side DOL: ____',
        '  BANKNIFTY Buy-side: ____     |  Sell-side: ____',
        '  XAGUSD   H4 bias: ____       |  Next DOL: ____',
        '  USOIL    H4 bias: ____       |  Next DOL: ____',
        '',
        '  Key news/events: Check ForexFactory before London open.',
        '',
    ]
    return lines


# ---------------------------------------------------------------------------
# Telegram delivery
# ---------------------------------------------------------------------------

def _send_to_nse_bot(fpath: str, caption: str, report_text: str):
    """Send via NSE bot (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)."""
    try:
        from utils.telegram_alerts import send_document, send_message
        ok = send_document(fpath, caption)
        if ok:
            logger.info("EOD report sent to NSE bot (document)")
        else:
            # Fallback: split-send as text messages
            _send_text_chunked(report_text,
                               os.getenv('TELEGRAM_BOT_TOKEN', ''),
                               os.getenv('TELEGRAM_CHAT_ID', ''))
    except Exception as e:
        logger.error(f"EOD NSE bot send error: {e}")


def _send_to_forex_bot(fpath: str, caption: str, report_text: str):
    """Send via Forex bot (FOREX_TELEGRAM_TOKEN + CB6_ADMIN_USER_ID)."""
    token   = os.getenv('FOREX_TELEGRAM_TOKEN', '').strip()
    chat_id = os.getenv('CB6_ADMIN_USER_ID', '').strip()
    if not token or not chat_id:
        logger.warning("EOD: FOREX_TELEGRAM_TOKEN or CB6_ADMIN_USER_ID not set — skipping forex bot send")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendDocument"
        with open(fpath, 'rb') as f:
            resp = requests.post(
                url,
                files={'document': (os.path.basename(fpath), f, 'application/octet-stream')},
                data={'chat_id': chat_id, 'caption': caption[:1024]},
                timeout=30,
            )
        if resp.status_code == 200:
            logger.info("EOD report sent to Forex bot (document)")
        else:
            logger.warning(f"EOD Forex bot document failed ({resp.status_code}), sending as text")
            _send_text_chunked(report_text, token, chat_id)
    except Exception as e:
        logger.error(f"EOD Forex bot send error: {e}")


def _send_text_chunked(text: str, token: str, chat_id: str):
    """Fallback: split long text into 4000-char chunks and send as messages."""
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunk_size = 4000
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    for i, chunk in enumerate(chunks):
        try:
            requests.post(url, data={
                'chat_id': chat_id,
                'text': f"[{i+1}/{len(chunks)}]\n{chunk}",
            }, timeout=10)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_ist() -> datetime.datetime:
    try:
        import pytz
        return datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
    except Exception:
        return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def _load_state(rel_path: str) -> dict:
    abs_path = os.path.join(_ROOT, rel_path)
    if not os.path.exists(abs_path):
        return {}
    try:
        with open(abs_path, encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"EOD: could not load {rel_path}: {e}")
        return {}


def _today_trades(state: dict, date_str: str) -> list:
    return [
        t for t in state.get('closed_trades', [])
        if t.get('exit_time', t.get('close_time', ''))[:10] == date_str
    ]
