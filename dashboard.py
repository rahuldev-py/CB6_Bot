# dashboard.py — CB6 Bot | Quantum Dark Edition
# Bloomberg Terminal density × Glassmorphism aesthetic
# Cyan = Bullish/Win | Magenta = Bearish/Loss | Gold = Institutional Macros
import os, sys, json, csv, threading, time
from urllib.parse import urlparse, parse_qs
import pytz
from datetime import datetime, timedelta
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from utils.logger import logger
from settings import CAPITAL, MAX_DAILY_LOSS_PCT, MAX_TRADES_PER_DAY
from core.metrics import (
    calc_metrics as _core_calc_metrics,
    calc_drawdown_series as _core_calc_drawdown_series,
    calc_daily_pnl as _core_calc_daily_pnl,
    calc_symbol_breakdown as _core_calc_symbol_breakdown,
    r_histogram as _core_r_histogram,
)

STATE_FILE   = os.path.join(os.path.dirname(__file__), 'data', 'paper_state.json')
ARCHIVE_FILE = os.path.join(os.path.dirname(__file__), 'data', 'cb6_master_archive.csv')
LOG_DIR      = os.path.join(os.path.dirname(__file__), 'logs')

ARCHIVE_COLUMNS = [
    'archive_date','date','entry_time','exit_time','symbol','underlying',
    'direction','timeframe','setup_type','entry_price','exit_price','pnl',
    'status','result','quantity','risk','confluence','in_fvg','delta','theta',
    'iv','strike','expiry','fvg_low','fvg_high','displacement','mid_candle_body',
    'avg_candle_body','dol_level','mss_level','targets_hit','window','rr_ratio',
]


# ── data loaders ──────────────────────────────────────────────────────────────

def tail_log(n=80):
    fn = os.path.join(LOG_DIR, f"cb6_{datetime.now().strftime('%Y%m%d')}.log")
    if not os.path.exists(fn):
        return []
    try:
        with open(fn, 'rb') as f:
            f.seek(0, 2); block = min(f.tell(), 65536); f.seek(-block, 2)
            data = f.read().decode('utf-8', errors='ignore')
        lines = data.splitlines()[-n:]
    except Exception:
        return []
    out = []
    for line in lines:
        if not line.strip(): continue
        parts = line.split('|', 2)
        if len(parts) >= 3:
            ts, level, msg = parts[0].strip()[11:19], parts[1].strip(), parts[2].strip()
        else:
            ts, level, msg = '', 'INFO', line
        out.append({'time': ts, 'level': level, 'msg': msg})
    return out


def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {'capital': CAPITAL, 'available_capital': CAPITAL,
            'open_trades': [], 'closed_trades': [],
            'daily_losses': 0, 'daily_trades': 0,
            'total_pnl': 0, 'date': datetime.now().strftime('%Y-%m-%d')}


CRYPTO_STATE_FILE = os.path.join(os.path.dirname(__file__), 'data', 'crypto_paper_state.json')
CRYPTO_HB_FILE    = os.path.join(os.path.dirname(__file__), 'data', 'crypto_heartbeat.txt')


def _fetch_binance_live() -> dict:
    """
    Pull real-time data from Binance USDT-M Futures API.
    Each sub-call is independent — one failure never blocks the others.
    Returns dict with keys:
      positions       — list of open position dicts
      wallet_balance  — total settled USDT equity (crossWalletBalance)
      avail_balance   — USDT free for new margin (availableBalance)
      today_pnl       — realized PnL from income API since midnight UTC
      all_time_pnl    — realized PnL from income API (last 500 ETHUSDT entries)
      bnb_trades      — list of actual Binance fills for the trades table
      error           — first error string or None
    """
    import hmac, hashlib, urllib.parse, requests as _req, datetime as _dt

    # Try env first; fall back to reading .env file directly (dashboard may not
    # inherit the process environment that loaded dotenv)
    key    = os.getenv('BINANCE_API_KEY', '')
    secret = os.getenv('BINANCE_API_SECRET', '')
    if not key or not secret:
        try:
            from dotenv import dotenv_values as _dv
            _env = _dv(os.path.join(os.path.dirname(__file__), '.env'))
            key    = _env.get('BINANCE_API_KEY', '')
            secret = _env.get('BINANCE_API_SECRET', '')
        except Exception:
            pass
    result = {
        'positions': [], 'wallet_balance': None, 'avail_balance': None,
        'today_pnl': None, 'all_time_pnl': None, 'bnb_trades': [],
        'error': None, 'blocked_ip': None,
    }
    if not key or not secret:
        result['error'] = 'No API keys'
        return result

    def _sign(params: dict) -> str:
        qs = urllib.parse.urlencode(params)
        return hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()

    hdr = {'X-MBX-APIKEY': key}

    # Sync to Binance server time to avoid timestamp rejection (clock drift)
    _time_offset = 0
    try:
        _st = _req.get('https://fapi.binance.com/fapi/v1/time', timeout=4)
        if _st.ok:
            _time_offset = _st.json().get('serverTime', 0) - int(time.time() * 1000)
    except Exception:
        pass

    def _ts() -> int:
        return int(time.time() * 1000) + _time_offset

    # 1. Open positions
    try:
        p = {'timestamp': _ts()}
        p['signature'] = _sign(p)
        r = _req.get('https://fapi.binance.com/fapi/v2/positionRisk',
                     params=p, headers=hdr, timeout=6)
        for pos in (r.json() if r.ok else []):
            qty = float(pos.get('positionAmt', 0))
            if qty == 0:
                continue
            result['positions'].append({
                'symbol'  : pos['symbol'],
                'qty'     : abs(qty),
                'side'    : 'BULLISH' if qty > 0 else 'BEARISH',
                'entry'   : float(pos.get('entryPrice', 0)),
                'mark'    : float(pos.get('markPrice', 0)),
                'upnl'    : float(pos.get('unRealizedProfit', 0)),
                'leverage': pos.get('leverage', '20'),
            })
    except Exception as e:
        result['error'] = f'positions: {e}'

    # 2. Account balance
    try:
        b = {'timestamp': _ts()}
        b['signature'] = _sign(b)
        rb = _req.get('https://fapi.binance.com/fapi/v2/balance',
                      params=b, headers=hdr, timeout=6)
        if rb.ok:
            for asset in rb.json():
                if asset.get('asset') == 'USDT':
                    result['wallet_balance'] = round(
                        float(asset.get('crossWalletBalance',
                              asset.get('balance', 0))), 4)
                    result['avail_balance']  = round(
                        float(asset.get('availableBalance', 0)), 4)
                    break
        elif not result['error']:
            try:
                import re as _re
                _em = rb.json().get('msg', '')
                _ip = _re.search(r'request ip: ([\d.]+)', _em)
                if _ip:
                    result['blocked_ip'] = _ip.group(1)
                    result['error'] = f'IP not whitelisted: {_ip.group(1)}'
                else:
                    result['error'] = f'balance: {rb.status_code} {_em[:60]}'
            except Exception:
                result['error'] = f'balance: {rb.status_code}'
    except Exception as e:
        if not result['error']:
            result['error'] = f'balance: {e}'

    # 3. Today realized PnL (income API, midnight UTC → now)
    try:
        today_ms = int(_dt.datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        ip = {'symbol': 'ETHUSDT', 'incomeType': 'REALIZED_PNL',
              'startTime': today_ms, 'limit': 200,
              'timestamp': _ts()}
        ip['signature'] = _sign(ip)
        ri = _req.get('https://fapi.binance.com/fapi/v1/income',
                      params=ip, headers=hdr, timeout=8)
        if ri.ok:
            result['today_pnl'] = round(
                sum(float(e.get('income', 0)) for e in ri.json()), 4)
    except Exception as e:
        if not result['error']:
            result['error'] = f'today_pnl: {e}'

    # 4. All-time realized PnL (last 500 income entries)
    try:
        ap = {'symbol': 'ETHUSDT', 'incomeType': 'REALIZED_PNL',
              'limit': 500, 'timestamp': _ts()}
        ap['signature'] = _sign(ap)
        ra = _req.get('https://fapi.binance.com/fapi/v1/income',
                      params=ap, headers=hdr, timeout=10)
        if ra.ok:
            result['all_time_pnl'] = round(
                sum(float(e.get('income', 0)) for e in ra.json()), 4)
    except Exception as e:
        if not result['error']:
            result['error'] = f'all_pnl: {e}'

    # 5. Real trade fills from Binance (entry/exit/trailing detail)
    try:
        tp = {'symbol': 'ETHUSDT', 'limit': 100, 'timestamp': _ts()}
        tp['signature'] = _sign(tp)
        rt = _req.get('https://fapi.binance.com/fapi/v1/userTrades',
                      params=tp, headers=hdr, timeout=8)
        if rt.ok:
            trades = []
            for t in rt.json():
                trades.append({
                    'time'        : int(t.get('time', 0)),
                    'side'        : t.get('side', ''),
                    'price'       : round(float(t.get('price', 0)), 2),
                    'qty'         : float(t.get('qty', 0)),
                    'realizedPnl' : round(float(t.get('realizedPnl', 0)), 4),
                    'commission'  : round(float(t.get('commission', 0)), 4),
                    'orderId'     : t.get('orderId', ''),
                    'positionSide': t.get('positionSide', 'BOTH'),
                })
            # Newest first
            result['bnb_trades'] = sorted(trades, key=lambda x: -x['time'])
    except Exception as e:
        if not result['error']:
            result['error'] = f'trades: {e}'

    return result


def load_crypto_state() -> dict:
    try:
        if os.path.exists(CRYPTO_STATE_FILE):
            with open(CRYPTO_STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    _default_cap = float(os.getenv('CRYPTO_CAPITAL', '1000'))
    return {
        'starting_capital': _default_cap,
        'capital': _default_cap, 'available_capital': _default_cap,
        'open_trades': [], 'closed_trades': [],
        'daily_trades': 0, 'daily_pnl': 0.0, 'paused': False,
    }


def load_market_context():
    ctx = {'event': 'NORMAL', 'event_reason': 'No active event', 'is_geo': False,
           'fii_net': None, 'dii_net': None, 'fii_bias': 'N/A', 'fii_date': '',
           'aligned_total': 0, 'aligned_bull': 0, 'aligned_bear': 0, 'aligned_date': ''}
    try:
        wl_path = os.path.join(os.path.dirname(__file__), 'data', 'aligned_watchlist.json')
        if os.path.exists(wl_path):
            with open(wl_path) as f: wl = json.load(f)
            ctx['aligned_total'] = wl.get('aligned', 0)
            ctx['aligned_bull']  = len(wl.get('bullish', []))
            ctx['aligned_bear']  = len(wl.get('bearish', []))
            ctx['aligned_date']  = wl.get('date', '')[:10]
    except Exception: pass
    try:
        from data.news_calendar import get_news_status, is_geopolitical_event
        ns = get_news_status()
        if ns['is_news']:
            ctx['event'] = ns['event']; ctx['event_reason'] = ns['guidance'][:120]
        ctx['is_geo'] = is_geopolitical_event()
        if ctx['is_geo'] and ctx['event'] == 'NORMAL':
            ctx['event'] = 'GEOPOLITICAL'; ctx['event_reason'] = 'Crisis mode — A+ only'
    except Exception: pass
    try:
        from data.fii_dii import load_saved_fii_dii, _extract_fii_dii
        cached = load_saved_fii_dii()
        if cached:
            f, d, found = _extract_fii_dii(cached.get('data', cached))
            if found:
                ctx['fii_net'] = f; ctx['dii_net'] = d
                ctx['fii_date'] = cached.get('timestamp', '')[:10]
                ctx['fii_bias'] = 'BULLISH' if f > 500 else ('BEARISH' if f < -500 else 'NEUTRAL')
    except Exception: pass
    return ctx


def load_watchlist_data():
    try:
        wl_path = os.path.join(os.path.dirname(__file__), 'data', 'aligned_watchlist.json')
        if os.path.exists(wl_path):
            with open(wl_path) as f: return json.load(f)
    except Exception: pass
    return {'bullish': [], 'bearish': [], 'aligned': 0}


# ── metric wrappers ───────────────────────────────────────────────────────────

def calc_metrics(closed):       return _core_calc_metrics(closed, capital=CAPITAL)
def calc_symbol_breakdown(c):   return _core_calc_symbol_breakdown(c, top_n=5)
def calc_daily_pnl(c, days=30): return _core_calc_daily_pnl(c, days=days)
def calc_drawdown_series(c):    return _core_calc_drawdown_series(c)
def r_histogram(r):             return _core_r_histogram(r)


# ── archive ───────────────────────────────────────────────────────────────────

def archive_trades():
    state  = load_state()
    closed = state.get('closed_trades', [])
    if not closed:
        return {'status': 'nothing_to_archive', 'count': 0}
    exists = os.path.exists(ARCHIVE_FILE)
    try:
        with open(ARCHIVE_FILE, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=ARCHIVE_COLUMNS, extrasaction='ignore')
            if not exists: w.writeheader()
            for t in closed:
                # Use trade's exit date as archive date, not today's date
                arc_date = (t.get('exit_time') or t.get('entry_time') or datetime.now().strftime('%Y-%m-%d'))[:10]
                fvg = t.get('fvg', {}) or {}
                dol = t.get('dol', {}) or {}
                mss = t.get('mss', {}) or {}
                sig = t.get('entry_signal', {}) or {}
                w.writerow({
                    'archive_date'    : arc_date,
                    'date'            : t.get('entry_time', '')[:10],
                    'entry_time'      : t.get('entry_time', ''),
                    'exit_time'       : t.get('exit_time', ''),
                    'symbol'          : t.get('symbol','').replace('NSE:','').replace('-EQ',''),
                    'underlying'      : t.get('underlying', ''),
                    'direction'       : t.get('direction', ''),
                    'timeframe'       : t.get('timeframe', ''),
                    'setup_type'      : t.get('setup_type', 'SILVER_BULLET'),
                    'entry_price'     : t.get('entry_price', ''),
                    'exit_price'      : t.get('exit_price', ''),
                    'pnl'             : t.get('pnl', 0),
                    'status'          : t.get('status', ''),
                    'result'          : 'WIN' if t.get('pnl',0)>0 else ('LOSS' if t.get('pnl',0)<0 else 'BE'),
                    'quantity'        : t.get('quantity', ''),
                    'risk'            : t.get('risk', ''),
                    'confluence'      : t.get('confluence', ''),
                    'in_fvg'          : t.get('in_fvg', ''),
                    'delta'           : t.get('delta', ''),
                    'theta'           : t.get('theta', ''),
                    'iv'              : t.get('iv', ''),
                    'strike'          : t.get('strike', ''),
                    'expiry'          : t.get('expiry', ''),
                    'fvg_low'         : fvg.get('fvg_low', ''),
                    'fvg_high'        : fvg.get('fvg_high', ''),
                    'displacement'    : fvg.get('displacement', ''),
                    'mid_candle_body' : fvg.get('mid_candle_body', ''),
                    'avg_candle_body' : fvg.get('avg_candle_body', ''),
                    'dol_level'       : dol.get('level', sig.get('dol_level', '')),
                    'mss_level'       : mss.get('level', sig.get('mss_level', '')),
                    'targets_hit'     : ', '.join(t.get('targets_hit', [])),
                    'window'          : t.get('window', ''),
                    'rr_ratio'        : sig.get('rr_ratio', ''),
                })
        # Clear closed trades from paper state
        state['closed_trades'] = []
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)
        return {'status': 'archived', 'count': len(closed), 'path': ARCHIVE_FILE}
    except Exception as e:
        return {'status': f'error: {e}', 'count': 0}


# ── backtest data loaders + trigger ──────────────────────────────────────────

BT_SB_CSV  = os.path.join(os.path.dirname(__file__), 'data', 'backtest_silver_bullet_nifty.csv')

_bt_lock    = threading.Lock()
_bt_running = False
_bt_status  = 'idle'   # 'idle' | 'running' | 'done' | 'error: ...'

# Set by main.py after fyers auth so backtest reuses the live session
_fyers_ref  = None

def set_fyers_for_backtest(fyers):
    global _fyers_ref
    _fyers_ref = fyers


def _get_fyers_client():
    """Return live fyers client: prefer _fyers_ref, else build from .env token."""
    if _fyers_ref is not None:
        return _fyers_ref
    try:
        from fyers_apiv3 import fyersModel
        from dotenv import dotenv_values
        env = dotenv_values(os.path.join(os.path.dirname(__file__), '.env'))
        token = env.get('ACCESS_TOKEN', '').strip("'\"")
        client_id = env.get('CLIENT_ID', '').strip("'\"")
        if token and client_id:
            if ':' in token:
                token = token.split(':', 1)[1]
            return fyersModel.FyersModel(
                client_id=client_id,
                token=token,
                is_async=False,
                log_path=os.path.join(os.getcwd(), 'logs', ''),
            )
    except Exception:
        pass
    return None


def _read_bt_csv(path):
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                rows.append(r)
    except Exception:
        pass
    return rows


def _bt_stats(rows):
    if not rows:
        return {}
    total   = len(rows)
    wins    = sum(1 for r in rows if r.get('is_win', '').lower() in ('true', '1'))
    losses  = total - wins
    r_vals  = []
    for r in rows:
        try: r_vals.append(float(r.get('r_multiple', 0)))
        except: r_vals.append(0.0)
    avg_r   = round(sum(r_vals) / total, 2)
    total_r = round(sum(r_vals), 2)

    # max drawdown
    running = 0.0; peak = 0.0; max_dd = 0.0
    for v in r_vals:
        running += v
        if running > peak: peak = running
        dd = peak - running
        if dd > max_dd: max_dd = dd

    # monthly
    monthly = {}
    for r in rows:
        m = (r.get('date') or '')[:7]
        if not m: continue
        if m not in monthly: monthly[m] = {'w': 0, 'l': 0}
        if r.get('is_win', '').lower() in ('true', '1'):
            monthly[m]['w'] += 1
        else:
            monthly[m]['l'] += 1

    # hour
    hour_wl = {}
    for r in rows:
        try: h = int(r.get('hour', 0))
        except: continue
        if h not in hour_wl: hour_wl[h] = {'w': 0, 'l': 0}
        if r.get('is_win', '').lower() in ('true', '1'):
            hour_wl[h]['w'] += 1
        else:
            hour_wl[h]['l'] += 1

    return dict(
        total=total, wins=wins, losses=losses,
        win_rate=round(wins / total * 100, 1) if total else 0,
        avg_r=avg_r, total_r=total_r,
        max_dd=round(max_dd, 2),
        monthly=monthly, hour_wl=hour_wl,
    )


def load_backtest_data():
    sb_rows  = _read_bt_csv(BT_SB_CSV)
    global _bt_status
    last_run = ''
    if os.path.exists(BT_SB_CSV):
        try:
            ts = os.path.getmtime(BT_SB_CSV)
            last_run = datetime.fromtimestamp(ts).strftime('%d %b %Y %H:%M')
        except Exception: pass
    return {
        'sb' : {'stats': _bt_stats(sb_rows), 'rows': sb_rows},
        'last_run': last_run,
        'status'  : _bt_status,
    }


def trigger_backtest_bg():
    global _bt_running, _bt_status
    with _bt_lock:
        if _bt_running:
            return {'ok': False, 'message': 'Backtest already running — please wait.'}
        _bt_running = True
        _bt_status  = 'running'

    def _worker():
        global _bt_running, _bt_status
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.dirname(__file__))
            from backtest.nifty_strategy_backtest import (
                run_sb_backtest, _save_csv, _get_fyers,
            )
            # Prefer the live session already authenticated by main.py
            fyers = _fyers_ref if _fyers_ref is not None else _get_fyers()
            sb = run_sb_backtest(fyers, 'NSE:NIFTY50-INDEX', 180)
            _save_csv(sb, 'backtest_silver_bullet_nifty.csv')
            _bt_status = 'done'
            logger.info(f"Backtest complete — SB:{len(sb)} setups")
        except Exception as e:
            _bt_status = f'error: {e}'
            logger.error(f"Backtest runner error: {e}")
        finally:
            global _bt_running
            _bt_running = False

    threading.Thread(target=_worker, daemon=True).start()
    return {'ok': True, 'message': 'Backtest started — this takes 2-5 min. Refresh when done.'}


# ── HTML section builders ─────────────────────────────────────────────────────

def _delta_ring(delta):
    d = min(1.0, max(0.0, abs(float(delta)) if delta else 0))
    circ = 125.66
    filled = d * circ
    color = '#00d9ff' if d >= 0.55 else ('#f5c518' if d >= 0.40 else '#ff006e')
    return (
        f'<svg width="46" height="46" viewBox="0 0 46 46" style="display:inline-block;vertical-align:middle">'
        f'<circle cx="23" cy="23" r="20" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="3.5"/>'
        f'<circle cx="23" cy="23" r="20" fill="none" stroke="{color}" stroke-width="3.5"'
        f' stroke-dasharray="{filled:.1f} {circ:.1f}"'
        f' stroke-linecap="round" transform="rotate(-90 23 23)"/>'
        f'<text x="23" y="27" text-anchor="middle" fill="{color}" font-size="9"'
        f' font-family="Roboto Mono,monospace">Δ{d:.2f}</text>'
        f'</svg>'
    )


def _theta_cell(theta):
    th = float(theta) if theta else 0.0
    if th < -2.0:
        cls, tip = 'theta-critical', 'Critical burn'
    elif th < -0.5:
        cls, tip = 'theta-warn', 'Moderate decay'
    else:
        cls, tip = 'theta-ok', 'Theta OK'
    return f'<span class="theta-icon {cls}" title="{tip} {th:.2f}/day">⏳ {th:.2f}</span>'


def _build_ticker(closed, ctx, win_rate, total_pnl):
    items = []
    # Recent 5 trades
    for t in list(reversed(closed))[:5]:
        sym  = t['symbol'].replace('NSE:', '').replace('-EQ', '')
        pnl  = t['pnl']
        icon = '▲' if pnl > 0 else '▼'
        tag  = 'win' if pnl > 0 else 'loss'
        items.append(f'<span class="tick-item tick-{tag}">{icon} {sym} Rs {pnl:+,.0f}</span>')
    # Session stats
    items.append(f'<span class="tick-item tick-stat">📊 WR {win_rate}% | {len(closed)} trades</span>')
    items.append(f'<span class="tick-item tick-{"win" if total_pnl>=0 else "loss"}">💰 Net P&amp;L Rs {total_pnl:+,.0f}</span>')
    # FII
    if ctx['fii_net'] is not None:
        bias = ctx['fii_bias']
        fc   = 'win' if bias == 'BULLISH' else ('loss' if bias == 'BEARISH' else 'stat')
        items.append(f'<span class="tick-item tick-{fc}">🏦 FII Rs {ctx["fii_net"]:+,.0f}Cr — {bias}</span>')
    # Window status
    now = datetime.now()
    cur = now.hour * 60 + now.minute
    if 600 <= cur < 660:
        items.append('<span class="tick-item tick-macro">⚡ SILVER BULLET MORNING ACTIVE 10:00–11:00</span>')
    elif 810 <= cur < 870:
        items.append('<span class="tick-item tick-macro">⚡ SILVER BULLET AFTERNOON ACTIVE 13:30–14:30</span>')
    elif cur < 600:
        items.append('<span class="tick-item tick-stat">⏳ Waiting for SB Morning window 10:00 IST</span>')
    # Duplicate for seamless scroll
    items = items * 3
    return ''.join(items)


def _build_heatmap(closed):
    hour_stats = {h: {'w': 0, 'l': 0, 'pnl': 0} for h in range(9, 16)}
    for t in closed:
        try:
            raw = t.get('entry_time', '') or ''
            eh  = int(raw[11:13]) if len(raw) > 12 else int(raw[:2])
            if eh in hour_stats:
                if t['pnl'] > 0:   hour_stats[eh]['w'] += 1
                elif t['pnl'] < 0: hour_stats[eh]['l'] += 1
                hour_stats[eh]['pnl'] += t['pnl']
        except Exception: continue

    max_trades = max((hour_stats[h]['w'] + hour_stats[h]['l']) for h in range(9, 16)) or 1
    SB_HOURS   = {10, 13}
    cells = ''
    for h in range(9, 16):
        s   = hour_stats[h]
        tot = s['w'] + s['l']
        is_sb   = h in SB_HOURS
        sb_cls  = ' hm-sb' if is_sb else ''
        sb_badge = '<span class="hm-macro-badge">MACRO</span>' if is_sb else ''

        if tot == 0:
            style = 'background:rgba(14,21,32,0.6);border:1px solid rgba(255,255,255,0.05)'
            txt, sub = '—', 'no data'
        else:
            wr      = s['w'] / tot
            density = tot / max_trades
            alpha   = 0.12 + density * 0.45
            glow    = int(density * 18)
            if wr >= 0.5:
                rc, gc, bc = 0, 217, 255   # cyan
                gc_str     = '0,217,255'
            else:
                rc, gc, bc = 255, 0, 110   # magenta
                gc_str     = '255,0,110'
            style = (f'background:rgba({rc},{gc},{bc},{alpha:.2f});'
                     f'box-shadow:0 0 {glow}px rgba({gc_str},0.35);'
                     f'border:1px solid rgba({gc_str},0.25)')
            txt = f'{wr*100:.0f}%'
            sub = f'{tot}t · Rs {s["pnl"]:.0f}'

        cells += (
            f'<div class="hm-cell{sb_cls}" style="{style}">'
            f'{sb_badge}'
            f'<div class="hm-h">{h:02d}:00</div>'
            f'<div class="hm-wr">{txt}</div>'
            f'<div class="hm-sub">{sub}</div>'
            f'</div>'
        )
    return cells


def _build_dow_heatmap(closed):
    dow_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    dow_stats = {d: {'w': 0, 'l': 0, 'pnl': 0} for d in dow_names}
    for t in closed:
        try:
            raw = (t.get('entry_time') or '')[:10]
            if len(raw) == 10:
                d = datetime.strptime(raw, '%Y-%m-%d').weekday()
                if d < 5:
                    k = dow_names[d]
                    if t['pnl'] > 0:   dow_stats[k]['w'] += 1
                    elif t['pnl'] < 0: dow_stats[k]['l'] += 1
                    dow_stats[k]['pnl'] += t['pnl']
        except Exception: continue
    max_trades = max((dow_stats[d]['w'] + dow_stats[d]['l']) for d in dow_names) or 1
    cells = ''
    for d in dow_names:
        s = dow_stats[d]; tot = s['w'] + s['l']
        if tot == 0:
            style = 'background:rgba(14,21,32,0.6);border:1px solid rgba(255,255,255,0.05)'
            txt, sub = '—', 'no data'
        else:
            wr = s['w'] / tot; density = tot / max_trades
            alpha = 0.12 + density * 0.45; glow = int(density * 18)
            if wr >= 0.5: rc, gc, bc, gc_str = 0, 217, 255, '0,217,255'
            else:         rc, gc, bc, gc_str = 255, 0, 110, '255,0,110'
            style = (f'background:rgba({rc},{gc},{bc},{alpha:.2f});'
                     f'box-shadow:0 0 {glow}px rgba({gc_str},0.35);'
                     f'border:1px solid rgba({gc_str},0.25)')
            txt = f'{wr*100:.0f}%'; sub = f'{tot}t'
        cells += (f'<div class="hm-cell" style="{style}">'
                  f'<div class="hm-h">{d}</div>'
                  f'<div class="hm-wr">{txt}</div>'
                  f'<div class="hm-sub">{sub}</div></div>')
    return cells


def _build_open_rows(open_trades, ltp_map=None):
    if not open_trades:
        return '<tr><td colspan="13" class="no-data">No open positions</td></tr>'
    ltp_map = ltp_map or {}
    rows = ''
    for t in open_trades:
        sym    = t['symbol'].replace('NSE:', '').replace('-EQ', '').replace('-INDEX', '')
        entry  = t['entry_price']
        qty    = t['quantity']
        orig_q = t.get('original_quantity', qty)
        direction = t.get('direction', 'BUY')

        ltp = ltp_map.get(t['symbol'])
        if ltp and ltp > 0:
            sym_up    = t['symbol'].upper()
            is_option = sym_up.endswith('CE') or sym_up.endswith('PE')
            if is_option or direction in ('BUY', 'BULLISH'):
                pnl = (ltp - entry) * qty   # options always long premium
            else:
                pnl = (entry - ltp) * qty   # short futures/equity
            ltp_str = f'{ltp:,.2f}'
        else:
            pnl = t.get('pnl', 0)
            ltp_str = '—'

        pcol   = '#00d9ff' if pnl >= 0 else '#ff006e'
        ltp_col = '#00d9ff' if (ltp and ltp > entry) else ('#ff006e' if ltp else '#6b7280')
        tgt    = ', '.join(t.get('targets_hit', [])) or '—'
        delta  = t.get('delta', '')
        theta  = t.get('theta', '')
        ring   = _delta_ring(delta) if delta else '<span style="color:#4b6070;font-size:11px">no greek</span>'
        thetac = _theta_cell(theta) if theta else '<span style="color:#4b6070">—</span>'
        dir_cls = 'bull' if direction in ('BUY', 'BULLISH') else 'bear'
        rows += (
            f'<tr>'
            f'<td><span class="sym-tag">{sym}</span></td>'
            f'<td><span class="dir-badge {dir_cls}">{direction[:4]}</span></td>'
            f'<td>{t["timeframe"]}</td>'
            f'<td class="mono">{entry}</td>'
            f'<td class="mono red-val">{t["current_sl"]}</td>'
            f'<td class="mono">{t["target1"]}</td>'
            f'<td class="mono cyan-val">{t["target2"]}</td>'
            f'<td class="mono" style="color:{ltp_col};font-weight:600">{ltp_str}</td>'
            f'<td>{qty}/{orig_q}</td>'
            f'<td>{ring}</td>'
            f'<td>{thetac}</td>'
            f'<td style="color:{pcol};font-weight:600" class="mono">Rs {pnl:+,.0f}</td>'
            f'<td>{tgt}</td>'
            f'</tr>'
        )
    return rows


def _build_watchlist_html(wl_data):
    bulls = wl_data.get('bullish', [])[:10]
    bears = wl_data.get('bearish', [])[:5]
    html  = ''
    for sym in bulls:
        html += (f'<div class="wl-row bull">'
                 f'<span class="wl-sym">{sym}</span>'
                 f'<span class="wl-dir">▲ BULL</span>'
                 f'<span class="wl-metric">IV —</span>'
                 f'<span class="wl-metric">ATR —</span>'
                 f'</div>')
    for sym in bears:
        html += (f'<div class="wl-row bear">'
                 f'<span class="wl-sym">{sym}</span>'
                 f'<span class="wl-dir">▼ BEAR</span>'
                 f'<span class="wl-metric">IV —</span>'
                 f'<span class="wl-metric">ATR —</span>'
                 f'</div>')
    return html or '<div class="no-data" style="font-size:11px">Watchlist empty — run /watchlist update</div>'


def _build_liquidity_monitor(wl_data):
    bulls = wl_data.get('bullish', [])[:3]
    bears = wl_data.get('bearish', [])[:2]
    html  = ''
    for sym in bulls:
        html += (f'<div class="liq-row">'
                 f'<span class="liq-sym">{sym}</span>'
                 f'<span class="liq-zone pdh">PDH</span>'
                 f'<span class="liq-bar-wrap"><div class="liq-bar" style="width:72%"></div></span>'
                 f'<span class="liq-pct cyan-val">Near</span>'
                 f'</div>')
    for sym in bears:
        html += (f'<div class="liq-row">'
                 f'<span class="liq-sym">{sym}</span>'
                 f'<span class="liq-zone pdl">PDL</span>'
                 f'<span class="liq-bar-wrap"><div class="liq-bar red-bar" style="width:58%"></div></span>'
                 f'<span class="liq-pct red-val">Near</span>'
                 f'</div>')
    return html or '<div class="no-data" style="font-size:11px">No symbols — run morning scan</div>'


def _build_ai_insights(closed, metrics, ctx):
    insights = []
    n = len(closed)

    # Win rate assessment
    wr = metrics.get('win_rate', 0)
    if wr >= 56:
        insights.append(('cyan', '📈 Win rate', f'{wr}% — above the 56% institutional target. Edge is valid.'))
    elif wr >= 45:
        insights.append(('gold', '⚠ Win rate', f'{wr}% — borderline. Tighten score gate to 13+/20 (backtest sweet spot).'))
    elif n > 0:
        insights.append(('magenta', '📉 Win rate', f'{wr}% — below threshold. Avoid lower-confluence setups.'))

    # Theta burn check
    theta_burns = sum(1 for t in closed
                      if 'THETA' in (t.get('status') or '').upper())
    if theta_burns > 0:
        insights.append(('magenta', '⏳ Theta burn',
                          f'{theta_burns} trade(s) exited by 20-min FVG rule — '
                          f'entries may be too early in the window.'))

    # Displacement check
    disp_trades = [t for t in closed if (t.get('fvg') or {}).get('displacement')]
    if disp_trades:
        disp_wins = sum(1 for t in disp_trades if t['pnl'] > 0)
        disp_wr   = round(disp_wins / len(disp_trades) * 100, 1)
        insights.append(('cyan', '⚡ Displaced FVGs',
                          f'{len(disp_trades)} setups | WR {disp_wr}% — '
                          f'institutional displacement is working as expected.'))

    # Consecutive loss alert
    mcl = metrics.get('max_consec_l', 0)
    if mcl >= 3:
        insights.append(('magenta', '🔴 Streak alert',
                          f'{mcl} consecutive losses detected. Consider reducing lot size by 50%.'))

    # Best timeframe
    tf_stats = defaultdict(lambda: {'w': 0, 'n': 0})
    for t in closed:
        tf = t.get('timeframe', '?')
        tf_stats[tf]['n'] += 1
        if t['pnl'] > 0: tf_stats[tf]['w'] += 1
    if tf_stats:
        best_tf = max(tf_stats.items(), key=lambda x: x[1]['w']/x[1]['n'] if x[1]['n'] else 0)
        bwr = round(best_tf[1]['w'] / best_tf[1]['n'] * 100, 1) if best_tf[1]['n'] else 0
        insights.append(('gold', f'⏱ Best TF: {best_tf[0]}', f'{bwr}% WR on {best_tf[1]["n"]} trades'))

    # FII alignment
    if ctx.get('fii_bias') == 'BULLISH':
        insights.append(('cyan', '🏦 FII bias', 'Bullish — favour long CE entries. Sell-side setups need A+ confluence.'))
    elif ctx.get('fii_bias') == 'BEARISH':
        insights.append(('magenta', '🏦 FII bias', 'Bearish — favour PE entries. Sell-side has institutional backing today.'))

    if not insights:
        insights.append(('gold', '📊 No trades yet', 'Run a Silver Bullet scan at 10:00 or 13:30 IST to populate insights.'))

    html = ''
    for color, title, body in insights:
        html += (f'<div class="insight-card insight-{color}">'
                 f'<div class="insight-title">{title}</div>'
                 f'<div class="insight-body">{body}</div>'
                 f'</div>')
    return html


def _build_r_distributor(r_values):
    if not r_values: return '<div class="no-data">No R data yet</div>'
    buckets = defaultdict(int)
    for r in r_values:
        if r <= -3:   buckets['-3R+'] += 1
        elif r <= -2: buckets['-2R'] += 1
        elif r <= -1: buckets['-1R'] += 1
        elif r < 0:   buckets['-<1R'] += 1
        elif r < 1:   buckets['<1R'] += 1
        elif r < 2:   buckets['1R'] += 1
        elif r < 3:   buckets['2R'] += 1
        else:         buckets['3R+'] += 1
    order  = ['-3R+', '-2R', '-1R', '-<1R', '<1R', '1R', '2R', '3R+']
    colors = ['#ff006e','#ff006e','#ff006e','#ff4488','#00d9ff','#00d9ff','#00d9ff','#00d9ff']
    total  = max(len(r_values), 1)
    html   = '<div class="r-distributor">'
    for label, color in zip(order, colors):
        cnt = buckets.get(label, 0)
        pct = cnt / total * 100
        html += (f'<div class="r-bar-group">'
                 f'<div class="r-bar-label">{label}</div>'
                 f'<div class="r-bar-track">'
                 f'<div class="r-bar-fill" style="width:{pct:.1f}%;background:{color};'
                 f'box-shadow:0 0 8px {color}88"></div></div>'
                 f'<div class="r-bar-cnt">{cnt}</div>'
                 f'</div>')
    html += '</div>'
    return html


# ── API ───────────────────────────────────────────────────────────────────────

def api_snapshot():
    state   = load_state()
    ctx     = load_market_context()
    closed  = state['closed_trades']
    metrics = calc_metrics(closed)
    return {'state': state, 'context': ctx, 'metrics': metrics,
            'now': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}


# ── Trade Journal ─────────────────────────────────────────────────────────────

def _build_journal(closed):
    """Build daily trade journal grouped by date, newest first."""
    if not closed:
        return '<div class="no-data">No trades yet — journal will populate as bot trades.</div>'

    from collections import defaultdict as _dd
    from scanner.index_futures import INDEX_LOT_SIZES as _LOTS

    def _lot_size(sym):
        sym = (sym or '').upper().replace('NSE:', '')
        for idx, sz in _LOTS.items():
            if idx in sym:
                return sz
        return 1

    by_date = _dd(list)
    for t in closed:
        # EXPIRED_OVERNIGHT closes happen at 15:30 the next day — group by entry date
        if t.get('status') == 'EXPIRED_OVERNIGHT':
            d = (t.get('entry_time') or '')[:10]
        else:
            d = (t.get('exit_time') or t.get('entry_time') or '')[:10]
        if d:
            by_date[d].append(t)

    html = ''
    for date in sorted(by_date.keys(), reverse=True):
        trades  = by_date[date]
        d_wins  = sum(1 for t in trades if t.get('pnl', 0) > 0)
        d_loss  = sum(1 for t in trades if t.get('pnl', 0) < 0)
        d_gross = sum(t.get('pnl', 0) for t in trades)
        d_brok  = sum(t.get('brokerage_paid', 0) for t in trades)
        d_net   = d_gross    # brokerage already deducted in pnl
        net_col = '#00d9ff' if d_net >= 0 else '#ff006e'

        html += (
            f'<div class="jrn-day">'
            f'<div class="jrn-day-hdr" onclick="toggleJrnDay(this)">'
            f'<span class="jrn-date">{date}</span>'
            f'<span class="jrn-stat">{len(trades)} trades &nbsp;·&nbsp; {d_wins}W / {d_loss}L</span>'
            f'<span class="jrn-pnl" style="color:{net_col}">Rs {d_net:+,.0f}</span>'
            f'<span class="jrn-brok" style="color:#ff006e">brok Rs {d_brok:,.0f}</span>'
            f'<span class="jrn-arrow">▾</span>'
            f'</div>'
            f'<div class="jrn-body">'
            f'<table class="jrn-tbl"><thead><tr>'
            f'<th>Index</th><th>Dir</th><th>Lots</th><th>Qty</th>'
            f'<th>Entry</th><th>Exit</th><th>P&L</th><th>Brok.</th><th>Result</th><th>Time</th>'
            f'</tr></thead><tbody>'
        )
        for t in sorted(trades, key=lambda x: x.get('entry_time', '')):
            import re as _re
            sym    = _re.sub(r'\d{2}[A-Z]{3}', '', (t.get('symbol') or '').replace('NSE:', '').replace('FUT', '')).rstrip('0123456789')
            dirn   = t.get('direction', '—')
            qty    = t.get('quantity', t.get('original_quantity', 0))
            lot    = _lot_size(t.get('symbol', ''))
            lots   = int(qty) // lot if lot > 0 else qty
            entry  = t.get('entry_price', '—')
            exit_p = t.get('exit_price', '—')
            pnl    = t.get('pnl', 0)
            brok   = t.get('brokerage_paid', 0)
            pcol   = '#00d9ff' if pnl >= 0 else '#ff006e'
            result = 'WIN' if pnl > 0 else ('LOSS' if pnl < 0 else 'BE')
            rcls   = 'win-badge' if pnl > 0 else ('loss-badge' if pnl < 0 else 'be-badge')
            etime  = (t.get('entry_time') or '')[-8:-3]   # HH:MM
            dir_sym = '▲' if dirn in ('BUY', 'BULLISH') else '▼'
            dir_col = '#00d9ff' if dirn in ('BUY', 'BULLISH') else '#ff006e'
            html += (
                f'<tr>'
                f'<td class="mono" style="color:var(--text)">{sym}</td>'
                f'<td style="color:{dir_col};font-weight:600">{dir_sym} {dirn[:4]}</td>'
                f'<td class="mono" style="color:var(--gold)">{lots}L</td>'
                f'<td class="mono">{qty}</td>'
                f'<td class="mono">{entry}</td>'
                f'<td class="mono">{exit_p}</td>'
                f'<td class="mono" style="color:{pcol};font-weight:600">Rs {pnl:+,.0f}</td>'
                f'<td class="mono" style="color:#ff006e;font-size:11px">Rs {brok:.0f}</td>'
                f'<td><span class="{rcls}">{result}</span></td>'
                f'<td style="color:var(--muted);font-size:11px">{etime}</td>'
                f'</tr>'
            )
        html += '</tbody></table></div></div>'

    return html


# ── HTML ──────────────────────────────────────────────────────────────────────

def _load_archive_trades():
    """Load closed trades from master archive CSV for metric display."""
    rows = []
    try:
        if not os.path.exists(ARCHIVE_FILE):
            return rows
        with open(ARCHIVE_FILE, newline='') as f:
            for r in csv.DictReader(f):
                try:
                    rows.append({
                        'pnl'           : float(r.get('pnl', 0) or 0),
                        'brokerage_paid': float(r.get('brokerage_paid', 0) or 0),
                        'entry_price'   : float(r.get('entry_price', 0) or 0),
                        'exit_price'    : float(r.get('exit_price', 0) or 0),
                        'quantity'      : int(float(r.get('quantity', 1) or 1)),
                        'risk'          : float(r.get('risk', 1) or 1),
                        'symbol'        : r.get('symbol', ''),
                        'direction'     : r.get('direction', 'BUY'),
                        'timeframe'     : r.get('timeframe', ''),
                        'status'        : r.get('status', ''),
                        'entry_time'    : r.get('entry_time', ''),
                        'exit_time'     : r.get('exit_time', ''),
                        'targets_hit'   : [],
                        'rr_ratio'      : float(r.get('rr_ratio', 0) or 0),
                        'confluence'    : r.get('confluence', ''),
                        'id'            : r.get('id', ''),
                    })
                except Exception:
                    pass
    except Exception:
        pass
    return rows


def _build_crypto_tab() -> str:
    """Crypto engine is disabled — show placeholder."""
    return """
<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
  min-height:400px;gap:18px;padding:40px">
  <div style="font-size:48px;opacity:0.3">₿</div>
  <div style="font-size:18px;font-weight:700;color:var(--muted);letter-spacing:2px">
    CRYPTO ENGINE DISABLED
  </div>
  <div style="font-size:11px;color:var(--muted2);text-align:center;max-width:420px;line-height:1.8">
    The Binance crypto engine is currently disabled.<br>
    NSE and Forex engines are active.<br><br>
    <span style="color:var(--muted);font-family:var(--mono)">Re-enable via: orchestrator.py</span>
  </div>
  <div style="font-size:10px;color:var(--muted2);font-family:var(--mono);
    background:var(--panel);border:1px solid var(--border);border-radius:6px;
    padding:10px 20px;line-height:2">
    Active engines: NSE &nbsp;·&nbsp; FOREX
  </div>
</div>"""



def _compute_dashboard_data(state: dict, ctx: dict, closed: list) -> dict:
    """
    Extract all data computation from generate_dashboard().

    Returns a single dict with every derived value so generate_dashboard()
    is a pure orchestrator with no inline data computation. Reduces CC.
    """
    base    = state.get('capital', CAPITAL)
    opent   = state['open_trades']

    n        = len(closed)
    wins     = sum(1 for t in closed if t['pnl'] > 0)
    losses   = sum(1 for t in closed if t['pnl'] < 0)
    be       = sum(1 for t in closed if t['pnl'] == 0)
    _wr_denom = wins + losses
    win_rate  = round(wins / _wr_denom * 100, 1) if _wr_denom > 0 else 0
    cap_pct   = round(state.get('available_capital', base) / base * 100, 1) if base else 0
    metrics   = calc_metrics(closed)

    today_str       = datetime.now().strftime('%Y-%m-%d')
    daily_loss_used = abs(min(0, sum(
        t['pnl'] for t in closed
        if (t.get('exit_time') or '')[:10] == today_str and t['pnl'] < 0
    )))
    daily_win_today = sum(
        t['pnl'] for t in closed
        if (t.get('exit_time') or '')[:10] == today_str and t['pnl'] > 0
    )
    daily_net_today = round(daily_win_today - daily_loss_used, 2)
    today_trades    = sum(1 for t in closed if (t.get('exit_time') or '')[:10] == today_str)
    today_losses    = sum(1 for t in closed if (t.get('exit_time') or '')[:10] == today_str and t['pnl'] < 0)
    today_wins      = sum(1 for t in closed if (t.get('exit_time') or '')[:10] == today_str and t['pnl'] > 0)
    is_paused       = state.get('paused', False)
    open_exposure   = sum(t.get('position_value', 0) for t in opent)
    expo_pct        = round(open_exposure / base * 100, 1) if base else 0
    total_brokerage = round(sum(t.get('brokerage_paid', 0) for t in closed), 2)

    eq_labels = ['Start'] + [
        t['symbol'].replace('NSE:', '').replace('-EQ', '')[:6] for t in closed]
    eq_series, dd_series = calc_drawdown_series(closed)
    dpnl         = calc_daily_pnl(closed, days=30)
    daily_labels = [d[5:] for d, _ in dpnl]
    daily_values = [v for _, v in dpnl]
    r_labels, r_counts = _core_r_histogram(metrics['r_values'])

    return dict(
        base=base, opent=opent, n=n, wins=wins, losses=losses, be=be,
        win_rate=win_rate, cap_pct=cap_pct, metrics=metrics,
        today_str=today_str, daily_loss_used=daily_loss_used,
        daily_win_today=daily_win_today, daily_net_today=daily_net_today,
        today_trades=today_trades, today_losses=today_losses, today_wins=today_wins,
        is_paused=is_paused, open_exposure=open_exposure, expo_pct=expo_pct,
        total_brokerage=total_brokerage,
        eq_labels=eq_labels, eq_series=eq_series, dd_series=dd_series,
        daily_labels=daily_labels, daily_values=daily_values,
        r_labels=r_labels, r_counts=r_counts,
    )


# ── generate_dashboard() helpers (extracted to keep CC < 20) ─────────────────

def _fetch_live_pnl(opent: list) -> tuple:
    """Fetch live LTPs for open positions; compute open P&L. Returns (ltp_map, open_pnl)."""
    ltp_map = {}
    if opent:
        try:
            from scanner.live_price import get_live_prices
            import threading as _threading
            fc = _get_fyers_client()
            if fc is not None:
                syms   = [t['symbol'] for t in opent]
                result = {}
                def _fetch():
                    try: result.update(get_live_prices(fc, syms))
                    except Exception: pass
                th = _threading.Thread(target=_fetch, daemon=True)
                th.start(); th.join(timeout=3)
                ltp_map = result
        except Exception:
            pass
    open_pnl = 0.0
    for t in opent:
        ltp = ltp_map.get(t['symbol'])
        if ltp and ltp > 0:
            direction = t.get('direction', 'BUY')
            sym_up    = t['symbol'].upper()
            is_opt    = sym_up.endswith('CE') or sym_up.endswith('PE')
            long_side = is_opt or direction in ('BUY', 'BULLISH')
            open_pnl += ((ltp - t['entry_price']) if long_side else (t['entry_price'] - ltp)) * t['quantity']
        else:
            open_pnl += t.get('pnl', 0)
    return ltp_map, open_pnl


def _compute_pnl_summary(closed: list, open_pnl: float, base: float) -> dict:
    """Compute all-time and today P&L figures. Returns dict with pnl keys."""
    import pytz as _pytz
    ist        = _pytz.timezone('Asia/Kolkata')
    now_ist    = datetime.now(ist)
    today_date = now_ist.strftime('%Y-%m-%d')

    realized_all  = round(sum(t.get('pnl', 0) for t in closed), 2)
    total_pnl_all = round(realized_all + open_pnl, 2)
    pnl_all_pct   = round(total_pnl_all / base * 100, 2) if base else 0
    today_pnl     = round(sum(
        t.get('pnl', 0) for t in closed
        if (t.get('exit_time') or '')[:10] == today_date
    ) + open_pnl, 2)
    today_pnl_pct = round(today_pnl / base * 100, 2) if base else 0
    return {
        'total_pnl_all': total_pnl_all,
        'pnl_all_pct':   pnl_all_pct,
        'today_pnl':     today_pnl,
        'today_pnl_pct': today_pnl_pct,
    }


def _compute_display_colors(today_pnl: float, win_rate: float,
                             metrics: dict, ctx: dict,
                             daily_net_today: float, total_pnl_all: float) -> dict:
    """Map all scalar metrics to their CSS color tokens. Pure function, no I/O."""
    pf = metrics['profit_factor']
    return {
        'pnl_col':          '#00d9ff' if today_pnl >= 0 else '#ff006e',
        'wr_col':           '#00d9ff' if win_rate >= 50 else '#ff006e',
        'pf_col':           ('#00d9ff' if pf >= 1.5 else ('#f5c518' if pf >= 1 else '#ff006e')),
        'fii_col':          ('#00d9ff' if ctx['fii_bias'] == 'BULLISH'
                             else ('#ff006e' if ctx['fii_bias'] == 'BEARISH' else '#6b7280')),
        'fii_str':          (f"Rs {ctx['fii_net']:,.0f} Cr" if ctx['fii_net'] is not None else "N/A"),
        'dii_str':          (f"Rs {ctx['dii_net']:,.0f} Cr" if ctx['dii_net'] is not None else "N/A"),
        'total_pnl_all_col': '#00d9ff' if total_pnl_all >= 0 else '#ff006e',
        'avg_r_col':        'var(--cyan)' if metrics['avg_r'] >= 0 else 'var(--magenta)',
        'sharpe_col':       'var(--cyan)' if metrics['sharpe'] >= 1 else 'var(--gold)',
        'daily_net_col':    'var(--magenta)' if daily_net_today < 0 else 'var(--cyan)',
    }


def _sym_rows_html(rows: list) -> str:
    """Render symbol breakdown rows as HTML table cells."""
    out = ''
    for sym, s in rows:
        wr2 = round(s['w'] / s['n'] * 100, 1) if s['n'] else 0
        col = '#00d9ff' if s['pnl'] >= 0 else '#ff006e'
        out += (f'<tr><td class="mono" style="color:#e2e8f0">{sym}</td>'
                f'<td>{s["n"]}</td><td style="color:{col}">{wr2}%</td>'
                f'<td style="color:{col};font-weight:600" class="mono">Rs {s["pnl"]:,.0f}</td></tr>')
    return out or '<tr><td colspan="4" class="no-data">No data</td></tr>'


def _build_display_panels(opent: list, open_rows: str, is_paused: bool) -> dict:
    """Build HTML snippets for the paused-banner and open-positions panel."""
    paused_banner = (
        '<div style="background:#2a1a2a;border:1px solid var(--magenta);border-radius:6px;'
        'padding:6px 10px;font-size:10px;color:var(--magenta);margin-bottom:6px">'
        'SCANNING PAUSED — send /resume to re-enable</div>'
    ) if is_paused else ''
    open_positions_html = (
        '<div class="no-data">No open positions — watching for Silver Bullet setup</div>'
        if not opent else
        '<div style="overflow-x:auto"><table><thead><tr>'
        '<th>Symbol</th><th>Dir</th><th>TF</th><th>Entry</th><th>SL</th>'
        '<th>T1</th><th>T2</th><th>LTP</th><th>Qty</th><th>Delta Δ</th><th>Theta ⏳</th>'
        '<th>P&L</th><th>Targets</th></tr></thead><tbody>'
        + open_rows + '</tbody></table></div>'
    )
    return {'paused_banner': paused_banner, 'open_positions_html': open_positions_html}


def generate_dashboard():
    state   = load_state()
    ctx     = load_market_context()
    wl_data = load_watchlist_data()

    # Merge live closed trades with archive so metrics survive after /archive
    closed_live    = state['closed_trades']
    closed_archive = _load_archive_trades()
    live_ids       = {t.get('id', '') for t in closed_live}
    closed = closed_live + [t for t in closed_archive if t.get('id', '') not in live_ids]

    d = _compute_dashboard_data(state, ctx, closed)
    base            = d['base']
    opent           = d['opent']
    n               = d['n']
    wins            = d['wins']
    losses          = d['losses']
    be              = d['be']
    win_rate        = d['win_rate']
    cap_pct         = d['cap_pct']
    metrics         = d['metrics']
    today_str       = d['today_str']
    daily_loss_used = d['daily_loss_used']
    daily_net_today = d['daily_net_today']
    today_trades    = d['today_trades']
    today_losses    = d['today_losses']
    today_wins      = d['today_wins']
    is_paused       = d['is_paused']
    open_exposure   = d['open_exposure']
    expo_pct        = d['expo_pct']
    total_brokerage = d['total_brokerage']
    eq_labels       = d['eq_labels']
    eq_series       = d['eq_series']
    dd_series       = d['dd_series']
    daily_labels    = d['daily_labels']
    daily_values    = d['daily_values']
    r_labels        = d['r_labels']
    r_counts        = d['r_counts']

    _ltp_map, open_pnl = _fetch_live_pnl(opent)

    pnl = _compute_pnl_summary(closed, open_pnl, base)
    total_pnl_all = pnl['total_pnl_all']
    pnl_all_pct   = pnl['pnl_all_pct']
    today_pnl     = pnl['today_pnl']
    today_pnl_pct = pnl['today_pnl_pct']
    # backward-compat aliases used in the f-string template
    total_pnl = today_pnl
    pnl_pct   = today_pnl_pct

    colors = _compute_display_colors(today_pnl, win_rate, metrics, ctx, daily_net_today, total_pnl_all)
    pnl_col          = colors['pnl_col']
    wr_col           = colors['wr_col']
    pf_col           = colors['pf_col']
    fii_col          = colors['fii_col']
    fii_str          = colors['fii_str']
    dii_str          = colors['dii_str']
    total_pnl_all_col = colors['total_pnl_all_col']
    avg_r_col        = colors['avg_r_col']
    sharpe_col       = colors['sharpe_col']
    daily_net_col    = colors['daily_net_col']

    # Section HTML
    ticker_html   = _build_ticker(closed, ctx, win_rate, total_pnl_all)
    heatmap_cells = _build_heatmap(closed)
    dow_cells     = _build_dow_heatmap(closed)
    open_rows     = _build_open_rows(opent, _ltp_map)
    wl_html       = _build_watchlist_html(wl_data)
    liq_html      = _build_liquidity_monitor(wl_data)
    insights_html = _build_ai_insights(closed, metrics, ctx)
    r_dist_html   = _build_r_distributor(metrics['r_values'])
    journal_html  = _build_journal(closed)

    # Symbol breakdown
    best, worst = calc_symbol_breakdown(closed)

    # JSON for JS
    eq_labels_js   = json.dumps(eq_labels)
    eq_values_js   = json.dumps(eq_series)
    dd_values_js   = json.dumps(dd_series)
    daily_labs_js  = json.dumps(daily_labels)
    daily_vals_js  = json.dumps(daily_values)
    r_labels_js    = json.dumps(r_labels)
    r_counts_js    = json.dumps(r_counts)

    now_str         = datetime.now().strftime('%d %b %Y  %H:%M:%S')
    crypto_tab_html = _build_crypto_tab()

    panels = _build_display_panels(opent, open_rows, is_paused)
    paused_banner       = panels['paused_banner']
    open_positions_html = panels['open_positions_html']

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CB6 Quantum | ICT Silver Bullet</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Roboto+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {{
  --bg:      #070b12;
  --glass:   rgba(12,18,28,0.88);
  --glass2:  rgba(14,22,36,0.92);
  --border:  rgba(0,217,255,0.07);
  --border2: rgba(255,255,255,0.05);
  --cyan:    #00d9ff;
  --magenta: #ff006e;
  --gold:    #f5c518;
  --text:    #d1d9e6;
  --muted:   #4b6070;
  --muted2:  #2a3547;
  --font:    'Inter', 'Segoe UI', system-ui, sans-serif;
  --mono:    'Roboto Mono', 'Consolas', monospace;
}}
*{{ margin:0; padding:0; box-sizing:border-box }}
html {{ scroll-behavior:smooth }}
body {{
  font-family: var(--font);
  background: var(--bg);
  background-image:
    radial-gradient(ellipse 80% 40% at 50% -10%, rgba(0,217,255,0.04) 0%, transparent 70%),
    radial-gradient(ellipse 60% 30% at 80% 110%, rgba(255,0,110,0.03) 0%, transparent 70%);
  color: var(--text);
  min-height: 100vh;
  font-size: 13px;
  line-height: 1.5;
}}

/* ── ticker ───────────────────────── */
.ticker-strip {{
  display: flex;
  align-items: center;
  background: rgba(0,217,255,0.04);
  border-bottom: 1px solid rgba(0,217,255,0.1);
  height: 32px;
  overflow: hidden;
  position: sticky;
  top: 0;
  z-index: 200;
  backdrop-filter: blur(12px);
}}
.ticker-label {{
  flex-shrink: 0;
  padding: 0 14px;
  font-size: 9px;
  font-family: var(--mono);
  color: var(--cyan);
  letter-spacing: 1.5px;
  font-weight: 500;
  border-right: 1px solid rgba(0,217,255,0.15);
  height: 100%;
  display: flex;
  align-items: center;
}}
.ticker-track {{
  flex: 1;
  overflow: hidden;
  position: relative;
}}
.ticker-roll {{
  display: flex;
  gap: 0;
  animation: ticker-scroll 40s linear infinite;
  white-space: nowrap;
  width: max-content;
}}
.ticker-roll:hover {{ animation-play-state: paused }}
@keyframes ticker-scroll {{
  0%   {{ transform: translateX(0) }}
  100% {{ transform: translateX(-33.33%) }}
}}
.tick-item {{
  padding: 0 28px;
  font-size: 11px;
  font-family: var(--mono);
  border-right: 1px solid rgba(255,255,255,0.05);
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  height: 32px;
}}
.tick-win    {{ color: var(--cyan) }}
.tick-loss   {{ color: var(--magenta) }}
.tick-stat   {{ color: #6b8090 }}
.tick-macro  {{ color: var(--gold); font-weight: 600 }}

/* ── topbar ───────────────────────── */
.topbar {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: var(--glass);
  backdrop-filter: blur(16px);
  border-bottom: 1px solid var(--border);
}}
.topbar-brand {{ display: flex; align-items: center; gap: 12px }}
.topbar-logo {{
  font-size: 18px;
  font-weight: 700;
  font-family: var(--mono);
  color: var(--cyan);
  text-shadow: 0 0 20px rgba(0,217,255,0.5);
  letter-spacing: -0.5px;
}}
.topbar-sub {{ font-size: 10px; color: var(--muted); letter-spacing: 0.5px }}
.topbar-right {{ display: flex; align-items: center; gap: 12px }}
.bt-topbar-btn {{
  padding: 5px 14px; font-size: 11px; font-weight: 700;
  font-family: var(--mono); letter-spacing: 0.8px;
  background: rgba(0,217,255,0.10); color: var(--cyan);
  border: 1px solid rgba(0,217,255,0.35); border-radius: 6px;
  cursor: pointer; transition: all 0.18s;
}}
.bt-topbar-btn:hover {{
  background: rgba(0,217,255,0.20);
  box-shadow: 0 0 14px rgba(0,217,255,0.30);
}}
.badge-paper {{
  padding: 3px 10px;
  background: rgba(0,217,255,0.1);
  border: 1px solid rgba(0,217,255,0.25);
  border-radius: 20px;
  font-size: 10px;
  color: var(--cyan);
  font-family: var(--mono);
  letter-spacing: 0.5px;
}}
.live-dot {{
  display: inline-block; width: 6px; height: 6px;
  border-radius: 50%; background: var(--cyan);
  box-shadow: 0 0 8px var(--cyan);
  animation: pulse 1.6s ease-in-out infinite;
}}
@keyframes pulse {{
  0%,100% {{ opacity:1; transform:scale(1) }}
  50%      {{ opacity:0.3; transform:scale(0.7) }}
}}
.time-tag {{ font-size: 11px; color: var(--muted); font-family: var(--mono) }}

/* ── session bar ──────────────────── */
.session-container {{
  padding: 8px 16px 10px;
  background: rgba(7,11,18,0.9);
  border-bottom: 1px solid var(--border2);
}}
.session-title {{
  font-size: 9px; color: var(--muted); letter-spacing: 1.5px;
  margin-bottom: 6px; text-transform: uppercase;
}}
.session-bar-outer {{
  position: relative; height: 22px;
  background: rgba(255,255,255,0.03);
  border-radius: 4px; overflow: visible;
  border: 1px solid var(--border2);
}}
.session-zone {{
  position: absolute; top: 0; bottom: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 8px; font-family: var(--mono); letter-spacing: 0.8px;
  border-radius: 3px; overflow: hidden; white-space: nowrap;
}}
.sz-judas  {{ background: rgba(245,197,24,0.12); color: var(--gold);   left:0%;    width:12% }}
.sz-sb1    {{ background: rgba(245,197,24,0.18); color: var(--gold);   left:12%;   width:16% }}
.sz-mid    {{ background: rgba(255,255,255,0.02); color: var(--muted); left:28%;   width:40% }}
.sz-sb2    {{ background: rgba(245,197,24,0.18); color: var(--gold);   left:68%;   width:16% }}
.sz-eod    {{ background: rgba(255,0,110,0.08);  color: var(--magenta); left:84%; width:16% }}
.session-needle {{
  position: absolute; top: -3px; bottom: -3px; width: 2px;
  background: var(--cyan);
  box-shadow: 0 0 8px var(--cyan);
  border-radius: 2px;
  transition: left 60s linear;
  z-index: 10;
}}
.session-labels {{
  display: flex; justify-content: space-between;
  margin-top: 3px; font-size: 9px; color: var(--muted);
  font-family: var(--mono);
}}

/* ── layout ───────────────────────── */
.main-grid {{
  display: grid;
  grid-template-columns: 270px 1fr 252px;
  gap: 12px;
  padding: 12px 14px;
  align-items: start;
}}
@media(max-width:1200px) {{
  .main-grid {{ grid-template-columns: 220px 1fr; }}
  .sidebar-right {{ display: none }}
}}
@media(max-width:800px) {{
  .main-grid {{ grid-template-columns: 1fr; }}
  .sidebar-left {{ display: none }}
}}

/* ── sidebar ──────────────────────── */
.sidebar-left, .sidebar-right {{
  display: flex; flex-direction: column; gap: 10px;
  position: sticky; top: 8px;
  max-height: calc(100vh - 100px);
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--muted2) transparent;
}}

/* ── panels ───────────────────────── */
.panel {{
  background: var(--glass2);
  backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
}}
.panel-title {{
  font-size: 9px; font-weight: 600; letter-spacing: 1.5px;
  text-transform: uppercase; color: var(--muted);
  margin-bottom: 10px; padding-bottom: 7px;
  border-bottom: 1px solid var(--border2);
  display: flex; align-items: center; gap: 6px;
}}
.pt-dot {{ width: 5px; height: 5px; border-radius: 50% }}
.pt-cyan {{ background: var(--cyan); box-shadow: 0 0 6px var(--cyan) }}
.pt-gold {{ background: var(--gold); box-shadow: 0 0 6px var(--gold) }}
.pt-mag  {{ background: var(--magenta); box-shadow: 0 0 6px var(--magenta) }}

/* ── macro windows ────────────────── */
.macro-window {{
  border-radius: 8px; padding: 10px 12px;
  border: 1px solid rgba(245,197,24,0.2);
  background: rgba(245,197,24,0.04);
  transition: all 0.4s ease;
  margin-bottom: 6px;
}}
.macro-window.active {{
  border-color: rgba(245,197,24,0.6);
  background: rgba(245,197,24,0.1);
  box-shadow: 0 0 24px rgba(245,197,24,0.25), inset 0 0 16px rgba(245,197,24,0.05);
  animation: macro-glow 2.5s ease-in-out infinite;
}}
@keyframes macro-glow {{
  0%,100% {{ box-shadow: 0 0 24px rgba(245,197,24,0.25), inset 0 0 16px rgba(245,197,24,0.05) }}
  50%     {{ box-shadow: 0 0 40px rgba(245,197,24,0.45), inset 0 0 24px rgba(245,197,24,0.1) }}
}}
.mw-name {{ font-size: 11px; font-weight: 600; color: var(--gold) }}
.mw-time {{ font-size: 9px; color: var(--muted); font-family: var(--mono); margin-top: 2px }}
.mw-status {{ font-size: 9px; margin-top: 4px }}
.mw-active {{ color: var(--gold) }}
.mw-inactive {{ color: var(--muted) }}

/* ── watchlist ────────────────────── */
.wl-row {{
  display: flex; align-items: center; gap: 6px;
  padding: 5px 0; border-bottom: 1px solid var(--border2);
  font-size: 11px;
}}
.wl-sym {{ flex: 1; font-family: var(--mono); color: var(--text); font-weight: 500 }}
.wl-dir {{ font-size: 9px; font-family: var(--mono); font-weight: 600; width: 50px; text-align: right }}
.bull .wl-dir {{ color: var(--cyan) }}
.bear .wl-dir {{ color: var(--magenta) }}
.wl-metric {{ font-size: 9px; color: var(--muted); font-family: var(--mono); width: 36px; text-align: right }}

/* ── liquidity monitor ────────────── */
.liq-row {{ padding: 6px 0; border-bottom: 1px solid var(--border2) }}
.liq-sym {{ font-family: var(--mono); font-size: 11px; font-weight: 500; display: block }}
.liq-zone {{
  display: inline-block; font-size: 8px; font-family: var(--mono);
  padding: 1px 5px; border-radius: 3px; letter-spacing: 0.5px; margin: 3px 0;
}}
.pdh {{ background: rgba(0,217,255,0.12); color: var(--cyan) }}
.pdl {{ background: rgba(255,0,110,0.12); color: var(--magenta) }}
.liq-bar-wrap {{
  height: 4px; background: rgba(255,255,255,0.05);
  border-radius: 2px; overflow: hidden; display: block; margin: 4px 0;
}}
.liq-bar {{
  height: 100%; border-radius: 2px;
  background: var(--cyan); box-shadow: 0 0 6px var(--cyan);
}}
.liq-bar.red-bar {{ background: var(--magenta); box-shadow: 0 0 6px var(--magenta) }}
.liq-pct {{ font-size: 10px; font-family: var(--mono) }}

/* ── hero cards ───────────────────── */
.cards-row {{
  display: grid; grid-template-columns: repeat(5,1fr); gap: 10px;
  margin-bottom: 10px;
}}
@media(max-width:1000px) {{ .cards-row {{ grid-template-columns: repeat(3,1fr) }} }}
.card {{
  background: var(--glass2);
  border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 12px;
  text-align: center; position: relative; overflow: hidden;
  transition: transform 0.2s, border-color 0.2s;
}}
.card:hover {{ transform: translateY(-1px); border-color: rgba(0,217,255,0.2) }}
.card::before {{
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(0,217,255,0.3), transparent);
}}
.card-label {{ font-size: 9px; color: var(--muted); letter-spacing: 1.2px; text-transform: uppercase }}
.card-value {{ font-size: 22px; font-weight: 700; font-family: var(--mono); margin: 6px 0 4px }}
.card-sub {{ font-size: 10px; color: var(--muted); font-family: var(--mono) }}

/* ── charts ───────────────────────── */
.chart-wrap {{ position: relative; height: 180px; margin-top: 4px }}
.chart-wrap-sm {{ position: relative; height: 140px; margin-top: 4px }}

/* ── heatmap ──────────────────────── */
.heatmap-grid {{
  display: grid; grid-template-columns: repeat(7,1fr); gap: 6px;
}}
.heatmap-dow {{ display: grid; grid-template-columns: repeat(5,1fr); gap: 6px }}
.hm-cell {{
  padding: 10px 5px; border-radius: 7px;
  text-align: center; position: relative;
  transition: transform 0.2s;
  cursor: default;
}}
.hm-cell:hover {{ transform: scale(1.06) }}
.hm-cell.hm-sb {{ border-width: 1px; border-style: solid }}
.hm-macro-badge {{
  position: absolute; top: -5px; left: 50%; transform: translateX(-50%);
  font-size: 7px; font-family: var(--mono);
  background: var(--gold); color: #000;
  padding: 1px 4px; border-radius: 3px; font-weight: 700; letter-spacing: 0.5px;
}}
.hm-h   {{ font-size: 9px; color: rgba(255,255,255,0.5); font-family: var(--mono) }}
.hm-wr  {{ font-size: 18px; font-weight: 700; color: #fff; margin: 4px 0 }}
.hm-sub {{ font-size: 8px; color: rgba(255,255,255,0.5) }}

/* ── r-distributor ────────────────── */
.r-distributor {{ display: flex; flex-direction: column; gap: 5px }}
.r-bar-group {{ display: flex; align-items: center; gap: 8px }}
.r-bar-label {{ width: 32px; font-size: 10px; font-family: var(--mono); color: var(--muted); text-align: right; flex-shrink:0 }}
.r-bar-track {{ flex: 1; height: 10px; background: rgba(255,255,255,0.04); border-radius: 5px; overflow: hidden }}
.r-bar-fill  {{ height: 100%; border-radius: 5px; transition: width 0.8s cubic-bezier(.22,.61,.36,1) }}
.r-bar-cnt   {{ width: 20px; font-size: 10px; font-family: var(--mono); color: var(--muted); text-align: left; flex-shrink:0 }}

/* ── tables ───────────────────────── */
table {{ width: 100%; border-collapse: collapse; font-size: 11.5px }}
th {{
  background: rgba(0,0,0,0.3); color: var(--muted);
  padding: 7px 8px; text-align: center; font-weight: 500;
  font-size: 9px; letter-spacing: 0.8px; text-transform: uppercase;
  border-bottom: 1px solid var(--border2);
  cursor: pointer; user-select: none; white-space: nowrap;
}}
th:hover {{ color: var(--cyan) }}
th.sort-asc::after  {{ content: ' ▲'; color: var(--cyan) }}
th.sort-desc::after {{ content: ' ▼'; color: var(--cyan) }}
td {{
  padding: 8px 8px; text-align: center;
  border-bottom: 1px solid rgba(255,255,255,0.03);
  vertical-align: middle;
}}

/* ── tags and badges ──────────────── */
.sym-tag {{
  font-family: var(--mono); font-weight: 500;
  background: rgba(255,255,255,0.06);
  padding: 2px 7px; border-radius: 4px; font-size: 11px;
}}
.dir-badge {{
  font-size: 9px; font-family: var(--mono); font-weight: 600;
  padding: 2px 7px; border-radius: 3px; letter-spacing: 0.5px;
}}
.dir-badge.bull {{ background: rgba(0,217,255,0.12); color: var(--cyan) }}
.dir-badge.bear {{ background: rgba(255,0,110,0.12); color: var(--magenta) }}
.result-be {{
  background: rgba(107,112,128,0.15); color: var(--muted);
  padding: 2px 8px; border-radius: 3px; font-size: 9px;
  font-family: var(--mono); font-weight: 600;
}}

/* ── greek widgets ────────────────── */
.theta-icon {{ font-family: var(--mono); font-size: 11px }}
.theta-ok       {{ color: var(--cyan) }}
.theta-warn     {{ color: var(--gold) }}
.theta-critical {{
  color: var(--magenta);
  animation: theta-pulse 1.2s ease-in-out infinite;
}}
@keyframes theta-pulse {{
  0%,100% {{ opacity:1 }}
  50%      {{ opacity:0.4 }}
}}

.btn-sm {{
  padding: 4px 12px; border-radius: 5px; font-size: 11px; cursor: pointer;
  border: 1px solid var(--border2); background: rgba(0,0,0,0.3);
  color: var(--text); font-family: var(--font); transition: all 0.15s;
}}
.btn-sm:hover {{ border-color: rgba(0,217,255,0.3); color: var(--cyan) }}

/* ── risk bars ────────────────────── */
.risk-bar-wrap {{
  height: 5px; background: rgba(255,255,255,0.05);
  border-radius: 3px; overflow: hidden; margin-top: 5px;
}}
.risk-bar {{ height: 100%; border-radius: 3px; transition: width 0.5s }}

/* ── metric grid ──────────────────── */
.metric-grid {{
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
}}
.metric-item {{
  background: rgba(0,0,0,0.25); border: 1px solid var(--border2);
  border-radius: 7px; padding: 9px 10px;
}}
.mi-label {{ font-size: 8px; color: var(--muted); letter-spacing: 1px; text-transform: uppercase }}
.mi-value {{ font-size: 16px; font-weight: 700; font-family: var(--mono); margin: 4px 0 2px }}
.mi-sub   {{ font-size: 9px; color: var(--muted); font-family: var(--mono) }}

/* ── insights ─────────────────────── */
.insight-card {{
  border-radius: 8px; padding: 10px 12px; margin-bottom: 8px;
  border: 1px solid transparent;
}}
.insight-cyan    {{ background: rgba(0,217,255,0.05); border-color: rgba(0,217,255,0.15) }}
.insight-magenta {{ background: rgba(255,0,110,0.05); border-color: rgba(255,0,110,0.15) }}
.insight-gold    {{ background: rgba(245,197,24,0.05); border-color: rgba(245,197,24,0.15) }}
.insight-title {{ font-size: 11px; font-weight: 600; margin-bottom: 4px }}
.insight-cyan .insight-title    {{ color: var(--cyan) }}
.insight-magenta .insight-title {{ color: var(--magenta) }}
.insight-gold .insight-title    {{ color: var(--gold) }}
.insight-body {{ font-size: 11px; color: var(--muted); line-height: 1.5 }}

/* ── panic button ─────────────────── */
.panic-btn {{
  width: 100%; padding: 14px; font-size: 13px; font-weight: 700;
  letter-spacing: 1px; font-family: var(--mono);
  background: rgba(255,0,110,0.08);
  border: 1px solid rgba(255,0,110,0.4);
  color: var(--magenta); border-radius: 8px; cursor: pointer;
  transition: all 0.2s;
  text-transform: uppercase;
}}
.panic-btn:hover {{
  background: rgba(255,0,110,0.18);
  box-shadow: 0 0 30px rgba(255,0,110,0.35);
}}

/* ── archive button ───────────────── */
.archive-btn {{
  width: 100%; padding: 10px; font-size: 11px; font-weight: 600;
  font-family: var(--mono); letter-spacing: 0.5px;
  background: rgba(245,197,24,0.06);
  border: 1px solid rgba(245,197,24,0.3);
  color: var(--gold); border-radius: 7px; cursor: pointer;
  transition: all 0.2s;
}}
.archive-btn:hover {{
  background: rgba(245,197,24,0.12);
  box-shadow: 0 0 16px rgba(245,197,24,0.25);
}}

/* ── backtest button ──────────────── */
.bt-btn {{
  width: 100%; padding: 10px; font-size: 11px; font-weight: 600;
  font-family: var(--mono); letter-spacing: 0.5px;
  background: rgba(0,217,255,0.06); color: var(--cyan);
  border: 1px solid rgba(0,217,255,0.2); border-radius: 7px;
  cursor: pointer; transition: all 0.2s;
}}
.bt-btn:hover {{
  background: rgba(0,217,255,0.13);
  box-shadow: 0 0 18px rgba(0,217,255,0.28);
}}

/* ── backtest modal overlay ──────── */
.bt-overlay {{
  display: none; position: fixed; inset: 0; z-index: 500;
  background: rgba(4,7,12,0.88); backdrop-filter: blur(8px);
  align-items: flex-start; justify-content: center;
  padding: 24px 12px; overflow-y: auto;
}}
.bt-overlay.open {{ display: flex; }}
.bt-modal {{
  width: 100%; max-width: 980px;
  background: var(--glass2); border: 1px solid var(--border);
  border-radius: 14px; padding: 24px 28px;
  box-shadow: 0 0 60px rgba(0,217,255,0.08);
  position: relative;
}}
.bt-close {{
  position: absolute; top: 14px; right: 18px;
  background: none; border: none; color: var(--magenta);
  font-size: 20px; cursor: pointer; line-height: 1;
}}
.bt-title {{
  font-size: 13px; font-weight: 700; letter-spacing: 1px;
  color: var(--cyan); font-family: var(--mono); margin-bottom: 18px;
  display: flex; align-items: center; gap: 10px;
}}
.bt-tabs {{
  display: flex; gap: 6px; margin-bottom: 18px;
}}
.bt-tab {{
  padding: 5px 16px; border-radius: 5px; font-size: 11px;
  font-family: var(--mono); font-weight: 600; cursor: pointer;
  background: rgba(0,0,0,0.4); color: var(--muted);
  border: 1px solid var(--border2); transition: all 0.15s;
}}
.bt-tab.active {{
  background: rgba(0,217,255,0.12); color: var(--cyan);
  border-color: rgba(0,217,255,0.3);
}}
.bt-stat-grid {{
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px;
  margin-bottom: 20px;
}}
.bt-stat {{
  background: rgba(0,0,0,0.35); border: 1px solid var(--border2);
  border-radius: 8px; padding: 12px; text-align: center;
}}
.bt-stat-val {{
  font-size: 22px; font-weight: 700; font-family: var(--mono);
  line-height: 1;
}}
.bt-stat-lbl {{
  font-size: 9px; color: var(--muted); letter-spacing: 0.7px;
  text-transform: uppercase; margin-top: 4px;
}}
.bt-month-table {{
  width: 100%; border-collapse: collapse; font-size: 11px;
  font-family: var(--mono); margin-bottom: 18px;
}}
.bt-month-table th {{
  text-align: left; padding: 5px 8px; color: var(--muted);
  font-size: 9px; letter-spacing: 0.6px; border-bottom: 1px solid var(--border2);
}}
.bt-month-table td {{
  padding: 5px 8px; border-bottom: 1px solid rgba(255,255,255,0.03);
}}
.bt-trade-wrap {{
  max-height: 280px; overflow-y: auto;
  scrollbar-width: thin; scrollbar-color: var(--muted2) transparent;
}}
.bt-trade-table {{
  width: 100%; border-collapse: collapse; font-size: 10px;
  font-family: var(--mono);
}}
.bt-trade-table th {{
  position: sticky; top: 0; background: var(--glass2);
  text-align: left; padding: 5px 7px; color: var(--muted);
  font-size: 9px; letter-spacing: 0.5px; border-bottom: 1px solid var(--border2);
}}
.bt-trade-table td {{ padding: 4px 7px; border-bottom: 1px solid rgba(255,255,255,0.025); }}
.bt-run-btn {{
  padding: 9px 20px; font-size: 11px; font-weight: 600; cursor: pointer;
  font-family: var(--mono); letter-spacing: 0.5px;
  background: rgba(0,217,255,0.08); color: var(--cyan);
  border: 1px solid rgba(0,217,255,0.25); border-radius: 7px; transition: all 0.2s;
}}
.bt-run-btn:hover {{ background: rgba(0,217,255,0.16); }}
.bt-run-btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
.bt-status {{ font-size: 10px; color: var(--muted); font-family: var(--mono); margin-left: 10px; }}
.bt-nodata {{ color: var(--muted); font-size: 11px; text-align: center; padding: 30px 0; }}
.bt-capital-row {{
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  background: rgba(245,197,24,0.06); border: 1px solid rgba(245,197,24,0.18);
  border-radius: 8px; padding: 8px 14px; margin-bottom: 16px;
}}
.bt-cap-lbl {{ font-size: 10px; color: var(--gold); font-family: var(--mono); letter-spacing: 0.5px; }}
.bt-cap-prefix {{ font-size: 12px; color: var(--gold); font-family: var(--mono); font-weight: 700; }}
.bt-cap-input {{
  background: rgba(0,0,0,0.4); border: 1px solid rgba(245,197,24,0.25);
  color: var(--gold); font-family: var(--mono); font-size: 12px; font-weight: 600;
  padding: 3px 8px; border-radius: 5px; width: 100px; outline: none;
}}
.bt-cap-input:focus {{ border-color: rgba(245,197,24,0.5); }}
.bt-pnl-grid {{
  display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin-bottom: 18px;
}}
.bt-pnl-card {{
  background: rgba(245,197,24,0.06); border: 1px solid rgba(245,197,24,0.15);
  border-radius: 8px; padding: 12px; text-align: center;
}}
.bt-pnl-val {{
  font-size: 20px; font-weight: 700; font-family: var(--mono); line-height: 1;
}}
.bt-pnl-lbl {{
  font-size: 9px; color: var(--gold); letter-spacing: 0.7px;
  text-transform: uppercase; margin-top: 4px;
}}
.bt-bar-win {{ display:inline-block; height:8px; background:#00d9ff; border-radius:2px; vertical-align:middle }}
.bt-bar-loss {{ display:inline-block; height:8px; background:#ff006e; border-radius:2px; vertical-align:middle; margin-left:2px }}

/* ── log panel ────────────────────── */
.log-panel {{
  position: fixed; left: 0; top: 0; bottom: 0; width: 340px;
  background: rgba(4,7,12,0.97); backdrop-filter: blur(20px);
  border-right: 1px solid var(--border);
  z-index: 300; display: flex; flex-direction: column;
  transform: translateX(-100%); transition: transform 0.25s ease;
  font-family: var(--mono);
}}
.log-panel.open {{ transform: translateX(0) }}
.log-head {{
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 12px; background: rgba(0,0,0,0.4);
  border-bottom: 1px solid var(--border);
  color: var(--cyan); font-size: 11px; font-weight: 600; letter-spacing: 0.8px;
}}
.log-body {{
  flex: 1; overflow-y: auto; padding: 4px 8px; font-size: 10px; line-height: 1.45;
  scrollbar-width: thin; scrollbar-color: var(--muted2) transparent;
}}
.log-line {{ padding: 2px 0; display: flex; gap: 5px; border-bottom: 1px solid rgba(255,255,255,0.02) }}
.log-line .lt {{ color: #3a4a58; flex-shrink: 0 }}
.log-line .ll {{ font-weight: 600; flex-shrink: 0; width: 38px }}
.log-line .lm {{ color: #8a9ab0; word-break: break-word }}
.lvl-INFO {{ color: var(--cyan) }}
.lvl-WARNING, .lvl-WARN {{ color: var(--gold) }}
.lvl-ERROR {{ color: var(--magenta) }}
.lvl-DEBUG {{ color: #3a4a58 }}
.log-filters {{
  display: flex; gap: 4px; padding: 5px 10px;
  background: rgba(0,0,0,0.3); border-bottom: 1px solid var(--border2);
}}
.log-filt-btn {{
  background: rgba(0,0,0,0.3); color: var(--muted);
  border: 1px solid var(--border2); padding: 2px 8px;
  border-radius: 4px; font-size: 9px; cursor: pointer; font-family: var(--mono);
}}
.log-filt-btn.active {{ background: rgba(0,217,255,0.15); color: var(--cyan); border-color: rgba(0,217,255,0.3) }}
.log-close {{ background: none; border: none; color: var(--magenta); cursor: pointer; font-size: 16px; font-weight: bold }}
.log-toggle {{
  position: fixed; left: 0; top: 50%; transform: translateY(-50%);
  background: var(--glass2); color: var(--cyan);
  border: 1px solid var(--border); border-left: none;
  padding: 16px 5px; border-radius: 0 7px 7px 0;
  cursor: pointer; font-size: 10px; writing-mode: vertical-rl;
  letter-spacing: 1.5px; z-index: 299; font-family: var(--mono); font-weight: 600;
  transition: background 0.15s;
}}
.log-toggle:hover {{ background: rgba(0,217,255,0.1) }}
.log-toggle.open, .log-panel.open + .log-toggle {{ left: 340px }}

/* ── toast ────────────────────────── */
.toast {{
  position: fixed; bottom: 24px; right: 24px;
  padding: 10px 18px; border-radius: 8px;
  font-size: 12px; font-family: var(--mono);
  opacity: 0; transform: translateY(10px);
  transition: all 0.3s; z-index: 500;
  backdrop-filter: blur(12px);
}}
.toast.show {{ opacity: 1; transform: translateY(0) }}
.toast-success {{ background: rgba(0,217,255,0.15); border: 1px solid rgba(0,217,255,0.4); color: var(--cyan) }}
.toast-error   {{ background: rgba(255,0,110,0.15); border: 1px solid rgba(255,0,110,0.4); color: var(--magenta) }}
.toast-info    {{ background: rgba(245,197,24,0.1);  border: 1px solid rgba(245,197,24,0.3); color: var(--gold) }}

/* ── utils ────────────────────────── */
.no-data {{ text-align: center; color: var(--muted); padding: 16px; font-size: 11px }}
.mono {{ font-family: var(--mono) }}
.cyan-val  {{ color: var(--cyan) }}
.red-val   {{ color: var(--magenta) }}
.gold-val  {{ color: var(--gold) }}
.two-col   {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px }}
.three-col {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px }}

/* ── Trade Journal ───────────────── */
.jrn-day {{ border: 1px solid var(--border2); border-radius: 6px; margin-bottom: 8px; overflow: hidden }}
.jrn-day-hdr {{
  display: flex; align-items: center; gap: 12px; padding: 10px 14px;
  background: rgba(0,217,255,0.04); cursor: pointer;
  border-bottom: 1px solid var(--border2);
  user-select: none;
}}
.jrn-day-hdr:hover {{ background: rgba(0,217,255,0.09) }}
.jrn-date  {{ font-family: var(--mono); font-size: 12px; color: var(--gold); font-weight: 600; min-width: 90px }}
.jrn-stat  {{ font-size: 11px; color: var(--muted) }}
.jrn-pnl   {{ font-family: var(--mono); font-size: 13px; font-weight: 700; margin-left: auto }}
.jrn-brok  {{ font-family: var(--mono); font-size: 10px }}
.jrn-arrow {{ font-size: 12px; color: var(--muted) }}
.jrn-body  {{ padding: 0 }}
.jrn-tbl   {{ width: 100%; border-collapse: collapse; font-size: 11px }}
.jrn-tbl th {{ background: rgba(0,0,0,0.3); color: var(--muted); padding: 6px 10px;
               text-align: left; font-size: 9px; letter-spacing: 0.8px; text-transform: uppercase;
               border-bottom: 1px solid var(--border2) }}
.jrn-tbl td {{ padding: 7px 10px; border-bottom: 1px solid rgba(255,255,255,0.04); color: var(--text) }}
.jrn-tbl tr:last-child td {{ border-bottom: none }}
.jrn-tbl tr:hover td {{ background: rgba(255,255,255,0.03) }}

/* ── main tab navigation ──────────── */
.main-tab-nav {{
  display: flex; gap: 4px; padding: 8px 16px 0;
  background: rgba(7,11,18,0.95);
  border-bottom: 1px solid var(--border2);
}}
.main-tab-btn {{
  padding: 7px 22px; border-radius: 7px 7px 0 0;
  font-size: 11px; font-weight: 700; letter-spacing: 1px;
  font-family: var(--mono); cursor: pointer;
  border: 1px solid var(--border2); border-bottom: none;
  background: rgba(255,255,255,0.02); color: var(--muted);
  transition: all 0.18s; position: relative; bottom: -1px;
}}
.main-tab-btn:hover {{ color: var(--text); background: rgba(255,255,255,0.06) }}
.main-tab-btn.active-nse {{
  background: rgba(0,217,255,0.10); color: var(--cyan);
  border-color: rgba(0,217,255,0.35);
  box-shadow: 0 0 16px rgba(0,217,255,0.15);
}}
.main-tab-btn.active-btc {{
  background: rgba(245,197,24,0.10); color: var(--gold);
  border-color: rgba(245,197,24,0.35);
  box-shadow: 0 0 16px rgba(245,197,24,0.15);
}}
.tab-section {{ display: none }}
.tab-section.tab-active {{ display: block }}

/* ── crypto tab layout ────────────── */
.crypto-grid {{
  display: grid;
  grid-template-columns: 260px 1fr 252px;
  gap: 12px; padding: 12px 14px; align-items: start;
}}
@media(max-width:1200px) {{ .crypto-grid {{ grid-template-columns: 220px 1fr; }} }}
@media(max-width:800px)  {{ .crypto-grid {{ grid-template-columns: 1fr; }} }}

/* BTC session windows */
.btc-window {{
  border-radius: 8px; padding: 10px 12px;
  border: 1px solid rgba(245,197,24,0.2);
  background: rgba(245,197,24,0.04);
  margin-bottom: 6px; transition: all 0.4s;
}}
.btc-window.active {{
  border-color: rgba(245,197,24,0.6);
  background: rgba(245,197,24,0.1);
  box-shadow: 0 0 24px rgba(245,197,24,0.25);
  animation: macro-glow 2.5s ease-in-out infinite;
}}
.btc-stat-row {{
  display: flex; justify-content: space-between;
  align-items: center; padding: 5px 0;
  border-bottom: 1px solid var(--border2); font-size: 11px;
}}
.btc-stat-row:last-child {{ border-bottom: none }}
.btc-stat-label {{ color: var(--muted); font-size: 10px }}
.btc-stat-val   {{ font-family: var(--mono); font-weight: 600 }}
</style>
</head>
<body>

<!-- LOG PANEL -->
<div class="log-panel" id="logPanel">
  <div class="log-head">
    <span><span class="live-dot"></span>&nbsp;BOT LIVE FEED</span>
    <span style="font-size:9px;color:var(--muted)" id="logStats">0 lines</span>
    <button class="log-close" onclick="toggleLog()">×</button>
  </div>
  <div class="log-filters">
    <button class="log-filt-btn active" data-lvl="ALL" onclick="filterLog(this)">ALL</button>
    <button class="log-filt-btn" data-lvl="INFO" onclick="filterLog(this)">INFO</button>
    <button class="log-filt-btn" data-lvl="WARNING" onclick="filterLog(this)">WARN</button>
    <button class="log-filt-btn" data-lvl="ERROR" onclick="filterLog(this)">ERR</button>
    <input id="logSearch" type="text" placeholder="filter…"
      style="background:rgba(0,0,0,0.4);border:1px solid var(--border2);color:var(--text);padding:2px 7px;border-radius:4px;font-size:10px;flex:1;min-width:60px;font-family:var(--mono)"
      oninput="renderLog()">
  </div>
  <div class="log-body" id="logBody"></div>
</div>
<button class="log-toggle" id="logToggle" onclick="toggleLog()">LIVE FEED ►</button>

<!-- STRATEGY PULSE TICKER -->
<div class="ticker-strip">
  <div class="ticker-label">STRATEGY PULSE</div>
  <div class="ticker-track">
    <div class="ticker-roll" id="tickerRoll">{ticker_html}</div>
  </div>
</div>

<!-- TOPBAR -->
<header class="topbar">
  <div class="topbar-brand">
    <div>
      <div class="topbar-logo">CB6 QUANTUM</div>
      <div class="topbar-sub">ICT SILVER BULLET · NSE INDIA · PAPER MODE</div>
    </div>
  </div>
  <div class="topbar-right">
    <button class="bt-topbar-btn" onclick="openBacktest()" title="View 180-day strategy backtest results">
      📊 BACKTEST
    </button>
    <span class="badge-paper">PAPER TRADING</span>
    <span class="live-dot"></span>
    <span class="time-tag" id="last-upd">{now_str} IST</span>
  </div>
</header>

<!-- SESSION PROGRESS BAR -->
<div class="session-container">
  <div class="session-title">NSE SESSION PROGRESS &nbsp;·&nbsp; 9:15 → 15:30 IST</div>
  <div class="session-bar-outer">
    <div class="session-zone sz-judas">JUDAS SWING</div>
    <div class="session-zone sz-sb1">⚡ SB WINDOW 1</div>
    <div class="session-zone sz-mid">NORMAL HOURS</div>
    <div class="session-zone sz-sb2">⚡ SB WINDOW 2</div>
    <div class="session-zone sz-eod">SQUARE-OFF</div>
    <div class="session-needle" id="sessionNeedle" style="left:0%"></div>
  </div>
  <div class="session-labels">
    <span>9:15</span><span>10:00</span><span>11:00</span>
    <span style="margin-left:20%">13:30</span><span>14:30</span><span>15:30</span>
  </div>
</div>

<!-- TAB NAV -->
<div class="main-tab-nav">
  <button class="main-tab-btn active-nse" id="tabBtnNse" onclick="switchTab('nse')">
    ◈ NSE INDIA
  </button>
  <button class="main-tab-btn" id="tabBtnBtc" onclick="switchTab('btc')"
    style="opacity:0.45;cursor:default" title="Crypto engine disabled">
    ₿ CRYPTO (disabled)
  </button>
</div>

<!-- NSE TAB -->
<div class="tab-section tab-active" id="tab-nse">
<!-- MAIN GRID -->
<div class="main-grid">

<!-- ══ LEFT SIDEBAR ══════════════════════════════════════════════════ -->
<aside class="sidebar-left">

  <!-- Macro Windows -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-gold"></span>MACRO WINDOWS</div>
    <div class="macro-window" id="mw1">
      <div class="mw-name">⚡ SB MORNING</div>
      <div class="mw-time">10:00 – 11:00 IST</div>
      <div class="mw-status mw-inactive" id="mw1-status">Waiting…</div>
    </div>
    <div class="macro-window" id="mw2">
      <div class="mw-name">⚡ SB AFTERNOON</div>
      <div class="mw-time">13:30 – 14:30 IST</div>
      <div class="mw-status mw-inactive" id="mw2-status">Waiting…</div>
    </div>
    <div style="font-size:10px;color:var(--muted);margin-top:6px;padding-top:6px;border-top:1px solid var(--border2)">
      Chain: Gap Bias → HOD/LOD → MSS → FVG → Entry<br>
      PM: Reverse from AM extreme set before 13:00<br>
      Judas Swing: 9:15-10:00 · Avoid entry · Score max 20
    </div>
  </div>

  <!-- Watchlist -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-cyan"></span>WATCHLIST
      <span style="margin-left:auto;font-size:9px;color:var(--muted)">{ctx['aligned_total']} aligned</span>
    </div>
    {wl_html}
    <div style="font-size:9px;color:var(--muted);margin-top:6px">
      IV &amp; ATR populate when Fyers is live
    </div>
  </div>

  <!-- Liquidity Sweep Monitor -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-mag"></span>LIQUIDITY SWEEP MONITOR</div>
    {liq_html}
    <div style="font-size:9px;color:var(--muted);margin-top:6px">
      Stocks approaching PDH/PDL — potential sweep targets
    </div>
  </div>

  <!-- FII/DII -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-gold"></span>FII / DII FLOW</div>
    <div style="display:flex;flex-direction:column;gap:8px">
      <div>
        <div style="font-size:9px;color:var(--muted)">FII NET</div>
        <div style="font-size:16px;font-weight:700;font-family:var(--mono);color:{fii_col}">{fii_str}</div>
        <div style="font-size:9px;color:var(--muted)">Bias: <span style="color:{fii_col};font-weight:600">{ctx['fii_bias']}</span></div>
      </div>
      <div>
        <div style="font-size:9px;color:var(--muted)">DII NET</div>
        <div style="font-size:14px;font-weight:600;font-family:var(--mono);color:var(--text)">{dii_str}</div>
      </div>
      <div style="font-size:9px;color:var(--muted)">{ctx['fii_date'] or 'No date'}</div>
    </div>
  </div>

</aside>

<!-- ══ CENTER HUB ═════════════════════════════════════════════════════ -->
<main class="center-hub" style="display:flex;flex-direction:column;gap:10px;min-width:0">

  <!-- HERO CARDS -->
  <div class="cards-row">
    <div class="card">
      <div class="card-label">Capital</div>
      <div class="card-value" style="color:var(--cyan)">Rs {base:,.0f}</div>
      <div class="card-sub">Avail {cap_pct}%</div>
    </div>
    <div class="card">
      <div class="card-label">Today P&amp;L</div>
      <div class="card-value" style="color:{pnl_col}">Rs {today_pnl:+,.0f}</div>
      <div class="card-sub" style="color:{pnl_col}">{today_pnl_pct:+.2f}% · resets 8AM</div>
    </div>
    <div class="card">
      <div class="card-label">Total Profit (All-Time)</div>
      <div class="card-value" style="color:{total_pnl_all_col}">Rs {total_pnl_all:+,.0f}</div>
      <div class="card-sub" style="color:{total_pnl_all_col}">{pnl_all_pct:+.2f}% on Rs {base:,.0f}</div>
    </div>
    <div class="card">
      <div class="card-label">Win Rate</div>
      <div class="card-value" style="color:{wr_col}">{win_rate}%</div>
      <div class="card-sub">{wins}W / {losses}L / {be}BE · {n} trades</div>
    </div>
    <div class="card">
      <div class="card-label">Profit Factor</div>
      <div class="card-value" style="color:{pf_col}">{metrics['profit_factor']}</div>
      <div class="card-sub">Need ≥ 1.5</div>
    </div>
    <div class="card">
      <div class="card-label">Avg R-Multiple</div>
      <div class="card-value" style="color:{avg_r_col}">{metrics['avg_r']}R</div>
      <div class="card-sub">Expectancy Rs {metrics['expectancy']:,.0f}</div>
    </div>
    <div class="card">
      <div class="card-label">Total Brokerage</div>
      <div class="card-value" style="color:#ff006e">Rs {total_brokerage:,.0f}</div>
      <div class="card-sub">STT · Exch · SEBI · GST · Stamp</div>
    </div>
  </div>

  <!-- EQUITY CURVE + DAILY PNL -->
  <div class="two-col">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot pt-cyan"></span>EQUITY CURVE
        <span style="margin-left:auto;font-size:9px;color:var(--muted)">neon trail · drawdown zone</span>
      </div>
      <div class="chart-wrap"><canvas id="eqChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title"><span class="pt-dot pt-gold"></span>DAILY P&amp;L (30D)</div>
      <div class="chart-wrap"><canvas id="dpChart"></canvas></div>
    </div>
  </div>

  <!-- R-DISTRIBUTOR -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-mag"></span>R-MULTIPLE DISTRIBUTOR
      <span style="margin-left:auto;font-size:9px;color:var(--magenta)">Magenta = loss · Cyan = profit</span>
    </div>
    {r_dist_html}
  </div>

  <!-- HEATMAPS -->
  <div class="two-col">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot pt-gold"></span>HOUR-OF-DAY HEATMAP
        <span style="margin-left:auto;font-size:9px;color:var(--gold)">luminosity = density</span>
      </div>
      <div class="heatmap-grid">{heatmap_cells}</div>
    </div>
    <div class="panel">
      <div class="panel-title"><span class="pt-dot pt-cyan"></span>DAY-OF-WEEK HEATMAP</div>
      <div class="heatmap-dow">{dow_cells}</div>
    </div>
  </div>

  <!-- OPEN TRADES -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-cyan"></span>OPEN POSITIONS ({len(opent)})</div>
    {open_positions_html}
    <button class="panic-btn" style="margin-top:12px" onclick="confirmPanic()">
      ⛔ PANIC CLOSE ALL POSITIONS
    </button>
  </div>

  <!-- TRADE JOURNAL -->
  <div class="panel" id="journalPanel">
    <div class="panel-title">
      <span class="pt-dot pt-gold"></span>TRADE JOURNAL
      <span style="font-size:10px;color:var(--gold);margin-left:6px">({n} trades)</span>
      <span style="margin-left:auto;font-size:9px;color:var(--muted)">click day to expand / collapse</span>
    </div>
    <div id="journalContent" style="max-height:600px;overflow-y:auto;padding:8px">{journal_html}</div>
  </div>

  <!-- SYMBOL BREAKDOWN -->
  <div class="two-col">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot pt-cyan"></span>BEST 5 SYMBOLS</div>
      <table><thead><tr><th>Symbol</th><th>Trades</th><th>WR%</th><th>P&L</th></tr></thead>
      <tbody>{_sym_rows_html(best)}</tbody></table>
    </div>
    <div class="panel">
      <div class="panel-title"><span class="pt-dot pt-mag"></span>WORST 5 SYMBOLS</div>
      <table><thead><tr><th>Symbol</th><th>Trades</th><th>WR%</th><th>P&L</th></tr></thead>
      <tbody>{_sym_rows_html(worst)}</tbody></table>
    </div>
  </div>

</main>

<!-- ══ RIGHT PANEL ════════════════════════════════════════════════════ -->
<aside class="sidebar-right">

  <!-- AI Trade Insights -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-gold"></span>AI TRADE INSIGHTS</div>
    {insights_html}
  </div>

  <!-- Advanced Metrics -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-cyan"></span>ADVANCED METRICS</div>
    <div class="metric-grid">
      <div class="metric-item">
        <div class="mi-label">Max Drawdown</div>
        <div class="mi-value" style="color:var(--magenta)">Rs {metrics['max_dd_rs']:,.0f}</div>
        <div class="mi-sub">{metrics['max_dd_pct']}% capital</div>
      </div>
      <div class="metric-item">
        <div class="mi-label">Largest Win</div>
        <div class="mi-value" style="color:var(--cyan)">Rs {metrics['largest_win']:,.0f}</div>
        <div class="mi-sub">Best trade</div>
      </div>
      <div class="metric-item">
        <div class="mi-label">Largest Loss</div>
        <div class="mi-value" style="color:var(--magenta)">Rs {metrics['largest_loss']:,.0f}</div>
        <div class="mi-sub">Worst trade</div>
      </div>
      <div class="metric-item">
        <div class="mi-label">Sharpe</div>
        <div class="mi-value" style="color:{sharpe_col}">{metrics['sharpe']}</div>
        <div class="mi-sub">Per-trade</div>
      </div>
      <div class="metric-item">
        <div class="mi-label">Consec Wins</div>
        <div class="mi-value" style="color:var(--cyan)">{metrics['max_consec_w']}</div>
        <div class="mi-sub">Best streak</div>
      </div>
      <div class="metric-item">
        <div class="mi-label">Consec Loss</div>
        <div class="mi-value" style="color:var(--magenta)">{metrics['max_consec_l']}</div>
        <div class="mi-sub">Worst streak</div>
      </div>
      <div class="metric-item">
        <div class="mi-label">Avg Win</div>
        <div class="mi-value" style="color:var(--cyan);font-size:13px">Rs {metrics['avg_win']:,.0f}</div>
        <div class="mi-sub">Per winner</div>
      </div>
      <div class="metric-item">
        <div class="mi-label">Avg Loss</div>
        <div class="mi-value" style="color:var(--magenta);font-size:13px">Rs {metrics['avg_loss']:,.0f}</div>
        <div class="mi-sub">Per loser</div>
      </div>
    </div>
  </div>

  <!-- Risk Monitor -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-mag"></span>RISK MONITOR</div>
    {paused_banner}
    <div style="display:flex;flex-direction:column;gap:10px">
      <div>
        <div style="display:flex;justify-content:space-between;font-size:10px;margin-bottom:4px">
          <span style="color:var(--muted)">Today P&amp;L</span>
          <span style="color:{daily_net_col}">Rs {daily_net_today:+,.0f}</span>
        </div>
        <div style="font-size:9px;color:var(--muted);margin-top:3px">
          {today_trades} trades — {today_wins}W / {today_losses}L | Loss Rs {daily_loss_used:,.0f}
        </div>
      </div>
      <div>
        <div style="display:flex;justify-content:space-between;font-size:10px;margin-bottom:4px">
          <span style="color:var(--muted)">Open Exposure</span>
          <span>Rs {open_exposure:,.0f} ({expo_pct}%)</span>
        </div>
        <div class="risk-bar-wrap">
          <div class="risk-bar" style="width:{min(100,expo_pct)}%;background:var(--gold)"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- Drawdown Chart -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-mag"></span>DRAWDOWN CURVE</div>
    <div class="chart-wrap-sm"><canvas id="ddChart"></canvas></div>
  </div>

  <!-- Archive -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot pt-gold"></span>DATA ARCHIVE</div>
    <div style="font-size:10px;color:var(--muted);margin-bottom:10px;line-height:1.6">
      Export all closed trades to <span style="color:var(--gold);font-family:var(--mono)">cb6_master_archive.csv</span>
      with full Greeks &amp; FVG metrics. Clears dashboard for next session.
      Auto-runs at <strong style="color:var(--gold)">15:45 IST</strong>.
    </div>
    <button class="archive-btn" id="archiveBtn" onclick="triggerArchive()">
      📦 EXPORT TO ARCHIVE ({n} trades)
    </button>
    <div style="font-size:9px;color:var(--muted);margin-top:7px;text-align:center">
      Feeds long-term AI training data
    </div>
  </div>

  <!-- Backtest -->
  <div class="panel">
    <div class="panel-title"><span class="pt-dot" style="background:var(--cyan)"></span>STRATEGY BACKTEST</div>
    <div style="font-size:10px;color:var(--muted);margin-bottom:10px;line-height:1.6">
      180-day walk-forward backtest on <span style="color:var(--cyan);font-family:var(--mono)">NSE:NIFTY50-INDEX</span>.<br>
      Runs <strong style="color:var(--cyan)">Silver Bullet</strong> strategy. Shows WR, R-multiple, monthly breakdown.
    </div>
    <button class="bt-btn" onclick="openBacktest()">
      📊 VIEW BACKTEST RESULTS
    </button>
    <div style="font-size:9px;color:var(--muted);margin-top:7px;text-align:center">
      Click inside modal to run a fresh 180-day scan
    </div>
  </div>

</aside>
</div><!-- end main-grid -->
</div><!-- end tab-nse -->

<!-- BTC CRYPTO TAB -->
<div class="tab-section" id="tab-btc">
{crypto_tab_html}
</div><!-- end tab-btc -->

<div style="text-align:center;padding:16px;font-size:10px;color:var(--muted2);font-family:var(--mono);border-top:1px solid var(--border2);margin-top:8px">
  CB6 QUANTUM · Local · Live refresh 15s · ICT Silver Bullet NSE India
</div>

<!-- BACKTEST MODAL -->
<div class="bt-overlay" id="btOverlay" onclick="closeBtOutside(event)">
  <div class="bt-modal" id="btModal">
    <button class="bt-close" onclick="closeBacktest()">×</button>
    <div class="bt-title">
      <span class="pt-dot" style="background:var(--cyan);width:8px;height:8px;border-radius:50%;display:inline-block"></span>
      STRATEGY BACKTEST — NSE:NIFTY50-INDEX — 180 DAYS
      <span id="btLastRun" class="bt-status"></span>
    </div>

    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap">
      <button class="bt-run-btn" id="btRunBtn" onclick="runBacktest()">▶ RUN FRESH BACKTEST</button>
      <span id="btRunStatus" class="bt-status"></span>
    </div>

    <!-- Capital simulator row -->
    <div class="bt-capital-row">
      <span class="bt-cap-lbl">Capital</span>
      <span class="bt-cap-prefix">₹</span>
      <input id="btCapital" type="number" class="bt-cap-input" value="200000" min="10000" step="10000">
      <span class="bt-cap-lbl" style="margin-left:12px">Risk/trade</span>
      <input id="btRiskPct" type="number" class="bt-cap-input" style="width:52px" value="1" min="0.1" max="5" step="0.1">
      <span class="bt-cap-lbl">%</span>
      <button class="bt-run-btn" style="padding:5px 14px;font-size:10px;margin-left:8px" onclick="renderBtContent(_btData)">↻ Apply</button>
      <span id="btCapInfo" style="font-size:10px;color:var(--gold);font-family:var(--mono);margin-left:10px"></span>
    </div>

    <div id="btContent">
      <div class="bt-nodata">Loading backtest data…</div>
    </div>
  </div>
</div>

<script>
// ── tab switching ──────────────────────────────────────────
function switchTab(tab) {{
  ['nse','btc'].forEach(t => {{
    document.getElementById('tab-'+t).classList.toggle('tab-active', t === tab);
    const btn = document.getElementById('tabBtn'+t.charAt(0).toUpperCase()+t.slice(1));
    if (btn) {{
      btn.className = 'main-tab-btn' + (t === tab ? ' active-'+(t==='btc'?'btc':'nse') : '');
    }}
  }});
  localStorage.setItem('cb6ActiveTab', tab);
}}
// Restore last active tab on load
(function() {{
  const saved = localStorage.getItem('cb6ActiveTab');
  if (saved && saved !== 'nse') switchTab(saved);
}})();

// ── chart data ────────────────────────────────────────────
const eqLabels  = {eq_labels_js};
const eqValues  = {eq_values_js};
const ddValues  = {dd_values_js};
const dpLabels  = {daily_labs_js};
const dpValues  = {daily_vals_js};
const rLabels   = {r_labels_js};
const rCounts   = {r_counts_js};

// ── Chart.js glow plugin ──────────────────────────────────
const glowPlugin = {{
  id: 'glow',
  beforeDatasetsDraw(chart) {{
    const ds = chart.data.datasets[0];
    const last = ds.data[ds.data.length - 1];
    chart.ctx.shadowColor = (last >= 0) ? '#00d9ff' : '#ff006e';
    chart.ctx.shadowBlur  = 18;
  }},
  afterDatasetsDraw(chart) {{ chart.ctx.shadowBlur = 0; }}
}};

const GRID = 'rgba(255,255,255,0.04)';
const TICK = {{ color:'#4b6070', font:{{ size:9, family:"'Roboto Mono',monospace" }} }};

// ── equity curve ──────────────────────────────────────────
(function() {{
  const isPos = eqValues[eqValues.length-1] >= 0;
  const lineCol = isPos ? '#00d9ff' : '#ff006e';
  const fillGrad = (ctx) => {{
    const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, ctx.chart.height);
    g.addColorStop(0, isPos ? 'rgba(0,217,255,0.18)' : 'rgba(255,0,110,0.18)');
    g.addColorStop(1, 'rgba(0,0,0,0)');
    return g;
  }};
  // Drawdown danger zone dataset (filled below zero as red)
  const ddZone = eqValues.map(() => 0);

  new Chart(document.getElementById('eqChart'), {{
    plugins: [glowPlugin],
    type: 'line',
    data: {{
      labels: eqLabels,
      datasets: [
        {{
          data: eqValues, borderColor: lineCol,
          backgroundColor: fillGrad,
          borderWidth: 2, pointRadius: 0, fill: true, tension: 0.3,
          order: 1
        }},
        {{
          data: eqValues.map(v => v < 0 ? v : null),
          borderColor: 'transparent',
          backgroundColor: 'rgba(255,0,110,0.12)',
          borderWidth: 0, pointRadius: 0, fill: {{ target: 'origin', above: 'transparent', below: 'rgba(255,0,110,0.15)' }},
          tension: 0.3, order: 2
        }}
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ display: false }},
        y: {{ ticks: TICK, grid: {{ color: GRID }} }}
      }}
    }}
  }});
}})();

// ── drawdown curve ────────────────────────────────────────
new Chart(document.getElementById('ddChart'), {{
  type: 'line',
  data: {{
    labels: eqLabels,
    datasets: [{{
      data: ddValues.map(v => -v),
      borderColor: '#ff006e',
      backgroundColor: 'rgba(255,0,110,0.1)',
      borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.3
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ display: false }},
      y: {{ ticks: TICK, grid: {{ color: GRID }} }}
    }}
  }}
}});

// ── daily PnL bars ────────────────────────────────────────
new Chart(document.getElementById('dpChart'), {{
  type: 'bar',
  data: {{
    labels: dpLabels,
    datasets: [{{
      data: dpValues,
      backgroundColor: dpValues.map(v => v >= 0 ? 'rgba(0,217,255,0.6)' : 'rgba(255,0,110,0.6)'),
      borderRadius: 3
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ ...TICK, maxRotation: 0 }}, grid: {{ display: false }} }},
      y: {{ ticks: TICK, grid: {{ color: GRID }} }}
    }}
  }}
}});

// ── session needle ────────────────────────────────────────
function updateSession() {{
  const now = new Date();
  const cur = now.getHours() * 60 + now.getMinutes();
  const open = 9*60+15, close = 15*60+30;
  const pct = cur < open ? 0 : cur > close ? 100 : ((cur-open)/(close-open)*100);
  document.getElementById('sessionNeedle').style.left = pct.toFixed(1) + '%';
}}
updateSession();
setInterval(updateSession, 30000);

// ── macro window glow ─────────────────────────────────────
function updateMacroWindows() {{
  const cur = new Date().getHours()*60 + new Date().getMinutes();
  const mw1 = document.getElementById('mw1');
  const mw2 = document.getElementById('mw2');
  const s1  = document.getElementById('mw1-status');
  const s2  = document.getElementById('mw2-status');

  if (cur >= 600 && cur < 660) {{
    mw1.classList.add('active');
    s1.textContent = '⚡ ACTIVE NOW'; s1.className = 'mw-status mw-active';
  }} else {{
    mw1.classList.remove('active');
    s1.textContent = cur < 600 ? 'Opens at 10:00' : 'Closed'; s1.className = 'mw-status mw-inactive';
  }}
  if (cur >= 810 && cur < 870) {{
    mw2.classList.add('active');
    s2.textContent = '⚡ ACTIVE NOW'; s2.className = 'mw-status mw-active';
  }} else {{
    mw2.classList.remove('active');
    s2.textContent = cur < 810 ? 'Opens at 13:30' : 'Closed'; s2.className = 'mw-status mw-inactive';
  }}
}}
updateMacroWindows();
setInterval(updateMacroWindows, 10000);

// ── panic close ───────────────────────────────────────────
function confirmPanic() {{
  if (!confirm('PANIC CLOSE ALL\\n\\nThis will square off every open position immediately.\\n\\nContinue?')) return;
  fetch('/api/panic_close', {{method:'POST'}})
    .then(r => r.json())
    .then(d => {{ showToast(d.message || 'All positions closed', d.ok ? 'success' : 'error'); setTimeout(()=>location.reload(),1500); }})
    .catch(e => showToast('Panic close error: ' + e, 'error'));
}}

// ── archive ───────────────────────────────────────────────
async function triggerArchive() {{
  if (!confirm('Archive all {n} closed trades to cb6_master_archive.csv?\\nThis will clear the trade history from this dashboard.')) return;
  const btn = document.getElementById('archiveBtn');
  btn.textContent = 'Archiving…'; btn.disabled = true;
  try {{
    const r = await fetch('/api/archive', {{method:'POST'}});
    const d = await r.json();
    if (d.status === 'archived') {{
      showToast('Archived ' + d.count + ' trades ✓', 'success');
      setTimeout(() => location.reload(), 1800);
    }} else {{
      showToast('Archive: ' + d.status, 'info');
    }}
  }} catch(e) {{ showToast('Archive failed: '+e, 'error'); }}
  btn.textContent = 'EXPORT TO ARCHIVE ({n} trades)'; btn.disabled = false;
}}

// ── toast ─────────────────────────────────────────────────
function showToast(msg, type) {{
  const t = document.createElement('div');
  t.className = 'toast toast-'+(type||'info'); t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.classList.add('show'), 10);
  setTimeout(() => {{ t.classList.remove('show'); setTimeout(()=>t.remove(),300); }}, 3500);
}}

// ── live refresh ──────────────────────────────────────────
async function liveRefresh() {{
  try {{
    const r = await fetch('/api/snapshot');
    const d = await r.json();
    const el = document.getElementById('last-upd');
    if (el) el.textContent = d.now + ' IST';
  }} catch(e) {{}}
}}
setInterval(liveRefresh, 15000);
setTimeout(() => location.reload(), 120000);

/* ── Journal toggle ── */
function toggleJrnDay(hdr) {{
  const body = hdr.nextElementSibling;
  const arrow = hdr.querySelector('.jrn-arrow');
  if (body.style.display === 'none' || body.style.display === '') {{
    body.style.display = 'block';
    if (arrow) arrow.textContent = '▴';
  }} else {{
    body.style.display = 'none';
    if (arrow) arrow.textContent = '▾';
  }}
}}
// expand first day, collapse rest
const jrnBodies = document.querySelectorAll('.jrn-body');
jrnBodies.forEach((b, i) => {{
  if (i === 0) {{
    b.style.display = 'block';
    const arrow = b.previousElementSibling && b.previousElementSibling.querySelector('.jrn-arrow');
    if (arrow) arrow.textContent = '▴';
  }} else {{
    b.style.display = 'none';
  }}
}});

// ── log feed ──────────────────────────────────────────────
let _logBuf = []; let _logFilt = 'ALL';
function toggleLog() {{
  const p = document.getElementById('logPanel');
  const t = document.getElementById('logToggle');
  const open = p.classList.toggle('open');
  t.classList.toggle('open', open);
  t.textContent = open ? '◄ HIDE' : 'LIVE FEED ►';
  if (open) {{ localStorage.setItem('cb6-log','1'); fetchLog(); }}
  else localStorage.removeItem('cb6-log');
}}
function filterLog(btn) {{
  document.querySelectorAll('.log-filt-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active'); _logFilt = btn.dataset.lvl; renderLog();
}}
function renderLog() {{
  const body = document.getElementById('logBody'); if (!body) return;
  const q    = (document.getElementById('logSearch').value||'').toLowerCase();
  const list = _logBuf.filter(l => {{
    if (_logFilt !== 'ALL' && l.level !== _logFilt) return false;
    return !q || l.msg.toLowerCase().includes(q);
  }});
  body.innerHTML = list.map(l =>
    `<div class="log-line"><span class="lt">${{l.time}}</span>`+
    `<span class="ll lvl-${{l.level}}">${{l.level.slice(0,4)}}</span>`+
    `<span class="lm">${{l.msg.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}}</span></div>`
  ).join('');
  body.scrollTop = body.scrollHeight;
  const st = document.getElementById('logStats');
  if (st) st.textContent = list.length+'/'+_logBuf.length+' lines';
}}
async function fetchLog() {{
  try {{
    const r = await fetch('/api/logs?n=120'); _logBuf = await r.json(); renderLog();
  }} catch(e) {{}}
}}
if (localStorage.getItem('cb6-log')) {{
  document.getElementById('logPanel').classList.add('open');
  document.getElementById('logToggle').classList.add('open');
  document.getElementById('logToggle').textContent = '◄ HIDE';
  fetchLog();
}}
setInterval(() => {{ if (document.getElementById('logPanel').classList.contains('open')) fetchLog(); }}, 3000);

// ── backtest modal ────────────────────────────────────────
let _btData = null;
let _btPollInterval = null;

function openBacktest() {{
  document.getElementById('btOverlay').classList.add('open');
  document.body.style.overflow = 'hidden';
  fetchBtData();
}}
function closeBacktest() {{
  document.getElementById('btOverlay').classList.remove('open');
  document.body.style.overflow = '';
  if (_btPollInterval) {{ clearInterval(_btPollInterval); _btPollInterval = null; }}
}}
function closeBtOutside(e) {{
  if (e.target === document.getElementById('btOverlay')) closeBacktest();
}}
async function fetchBtData() {{
  try {{
    const r = await fetch('/api/backtest');
    _btData = await r.json();
    renderBtContent(_btData);
  }} catch(e) {{
    document.getElementById('btContent').innerHTML = '<div class="bt-nodata">Failed to load data.</div>';
  }}
}}

function _fmtRs(v) {{
  const abs = Math.abs(Math.round(v));
  const s   = abs >= 100000 ? (abs/100000).toFixed(2)+'L'
             : abs >= 1000  ? (abs/1000).toFixed(1)+'K'
             : abs.toString();
  return (v < 0 ? '-₹' : '₹') + s;
}}

function renderBtContent(data) {{
  if (!data) return;
  const lastRun = document.getElementById('btLastRun');
  lastRun.textContent = data.last_run ? 'Last run: ' + data.last_run : 'No data — click Run to generate';

  // Read capital inputs
  const capital    = parseFloat(document.getElementById('btCapital').value) || 200000;
  const riskPct    = parseFloat(document.getElementById('btRiskPct').value) || 1.0;
  const riskAmount = capital * riskPct / 100;
  document.getElementById('btCapInfo').textContent =
    'Risk/trade: ₹' + Math.round(riskAmount).toLocaleString('en-IN');

  const d     = data.sb;
  const s     = d.stats;
  const rows  = d.rows || [];
  const label = 'Silver Bullet (5-min SB windows)';

  if (!s || !s.total) {{
    document.getElementById('btContent').innerHTML =
      '<div class="bt-nodata">No backtest data for ' + label +
      '.<br>Click <strong>RUN FRESH BACKTEST</strong> to generate results.</div>';
    return;
  }}

  // ── Compute Rs P&L per trade ─────────────────────────────────────────────
  const rVals   = rows.map(r => parseFloat(r.r_multiple || 0));
  const pnlList = rVals.map(r => r * riskAmount);
  const totalPnlRs  = pnlList.reduce((a, b) => a + b, 0);
  const returnPct   = (totalPnlRs / capital * 100).toFixed(1);
  const avgMonthRs  = totalPnlRs / Math.max(Object.keys(s.monthly || {{}}).length, 1);

  // max drawdown in Rs
  let running = 0, peak = 0, maxDdRs = 0;
  pnlList.forEach(p => {{
    running += p;
    if (running > peak) peak = running;
    const dd = peak - running;
    if (dd > maxDdRs) maxDdRs = dd;
  }});

  const wrCol   = s.win_rate >= 50 ? '#00d9ff' : '#ff006e';
  const rCol    = s.total_r  >= 0  ? '#00d9ff' : '#ff006e';
  const pnlCol  = totalPnlRs >= 0  ? '#00d9ff' : '#ff006e';
  const retCol  = parseFloat(returnPct) >= 0 ? '#00d9ff' : '#ff006e';

  // ── R-metric stat cards ──────────────────────────────────────────────────
  const statCards = `
    <div class="bt-stat-grid" style="margin-bottom:10px">
      <div class="bt-stat">
        <div class="bt-stat-val" style="color:${{wrCol}}">${{s.win_rate}}%</div>
        <div class="bt-stat-lbl">Win Rate</div>
      </div>
      <div class="bt-stat">
        <div class="bt-stat-val" style="color:var(--text)">${{s.total}}</div>
        <div class="bt-stat-lbl">Total Setups</div>
      </div>
      <div class="bt-stat">
        <div class="bt-stat-val" style="color:${{rCol}}">${{s.total_r}}R</div>
        <div class="bt-stat-lbl">Total R</div>
      </div>
      <div class="bt-stat">
        <div class="bt-stat-val" style="color:${{rCol}}">${{s.avg_r}}R</div>
        <div class="bt-stat-lbl">Avg R / Trade</div>
      </div>
      <div class="bt-stat">
        <div class="bt-stat-val" style="color:var(--cyan)">${{s.wins}}</div>
        <div class="bt-stat-lbl">Wins</div>
      </div>
      <div class="bt-stat">
        <div class="bt-stat-val" style="color:var(--magenta)">${{s.losses}}</div>
        <div class="bt-stat-lbl">Losses</div>
      </div>
      <div class="bt-stat">
        <div class="bt-stat-val" style="color:#f5c518">${{s.max_dd}}R</div>
        <div class="bt-stat-lbl">Max DD (R)</div>
      </div>
      <div class="bt-stat">
        <div class="bt-stat-val" style="color:var(--muted);font-size:13px">${{label.split('(')[0].trim()}}</div>
        <div class="bt-stat-lbl">Strategy</div>
      </div>
    </div>`;

  // ── Capital P&L cards ────────────────────────────────────────────────────
  const pnlCards = `
    <div class="bt-pnl-grid" style="margin-bottom:18px">
      <div class="bt-pnl-card">
        <div class="bt-pnl-val" style="color:${{pnlCol}}">${{_fmtRs(totalPnlRs)}}</div>
        <div class="bt-pnl-lbl">Net Profit (180d)</div>
      </div>
      <div class="bt-pnl-card">
        <div class="bt-pnl-val" style="color:${{retCol}}">${{returnPct}}%</div>
        <div class="bt-pnl-lbl">Return on ₹${{(capital/100000).toFixed(1)}}L</div>
      </div>
      <div class="bt-pnl-card">
        <div class="bt-pnl-val" style="color:var(--gold)">${{_fmtRs(avgMonthRs)}}</div>
        <div class="bt-pnl-lbl">Avg / Month</div>
      </div>
      <div class="bt-pnl-card">
        <div class="bt-pnl-val" style="color:#ff006e">${{_fmtRs(-maxDdRs)}}</div>
        <div class="bt-pnl-lbl">Max Drawdown ₹</div>
      </div>
    </div>`;

  // ── Monthly breakdown with Rs P&L ────────────────────────────────────────
  const monthly   = s.monthly || {{}};
  const monthlyRs = {{}};
  rows.forEach((r, i) => {{
    const m = (r.date || '').slice(0, 7);
    if (m) monthlyRs[m] = (monthlyRs[m] || 0) + pnlList[i];
  }});

  let monthRows = '';
  Object.keys(monthly).sort().forEach(m => {{
    const ms   = monthly[m];
    const tot  = ms.w + ms.l;
    const wr   = tot ? Math.round(ms.w / tot * 100) : 0;
    const wrC  = wr >= 50 ? '#00d9ff' : '#ff006e';
    const bw   = Math.round(ms.w / Math.max(tot, 1) * 60);
    const mRs  = monthlyRs[m] || 0;
    const mCol = mRs >= 0 ? '#00d9ff' : '#ff006e';
    monthRows += `<tr>
      <td>${{m}}</td>
      <td style="color:var(--cyan)">${{ms.w}}</td>
      <td style="color:var(--magenta)">${{ms.l}}</td>
      <td style="color:${{wrC}};font-weight:600">${{wr}}%</td>
      <td>
        <span class="bt-bar-win" style="width:${{bw}}px"></span>
        <span class="bt-bar-loss" style="width:${{60-bw}}px"></span>
      </td>
      <td style="color:var(--muted)">${{tot}}</td>
      <td style="color:${{mCol}};font-family:var(--mono);font-weight:600">${{_fmtRs(mRs)}}</td>
    </tr>`;
  }});

  const monthTable = `
    <div style="margin-bottom:18px">
      <div style="font-size:10px;color:var(--muted);font-family:var(--mono);letter-spacing:0.6px;margin-bottom:6px">MONTHLY BREAKDOWN</div>
      <table class="bt-month-table">
        <thead><tr>
          <th>Month</th><th>Wins</th><th>Losses</th><th>WR%</th><th>Bar</th><th>Total</th><th>P&L (₹)</th>
        </tr></thead>
        <tbody>${{monthRows || '<tr><td colspan="7" style="color:var(--muted);padding:8px">No monthly data</td></tr>'}}</tbody>
      </table>
    </div>`;

  // ── Trade log with Rs P&L ─────────────────────────────────────────────────
  const tradeList = rows.slice(-200).reverse();
  const pnlRev    = pnlList.slice(-200).reverse();
  const tradeRows = tradeList.map((r, i) => {{
    const isWin  = (r.is_win || '').toLowerCase() === 'true';
    const wc     = isWin ? '#00d9ff' : '#ff006e';
    const rm     = parseFloat(r.r_multiple || 0).toFixed(2);
    const tradePnl = pnlRev[i] || 0;
    return `<tr>
      <td style="color:var(--muted2)">${{r.date || ''}}</td>
      <td style="color:var(--muted2)">${{(r.time || '').slice(0,5)}}</td>
      <td style="color:${{r.direction==='BUY'?'#00d9ff':'#ff006e'}}">${{r.direction || ''}}</td>
      <td style="color:var(--text);font-family:var(--mono)">${{parseFloat(r.entry||0).toFixed(1)}}</td>
      <td style="color:var(--magenta);font-family:var(--mono)">${{parseFloat(r.stop_loss||0).toFixed(1)}}</td>
      <td style="color:var(--muted)">${{parseFloat(r.risk_pts||0).toFixed(0)}}</td>
      <td style="color:${{wc}};font-weight:600">${{isWin?'▲ WIN':'▼ LOSS'}}</td>
      <td style="color:${{wc}};font-family:var(--mono);font-weight:600">${{rm}}R</td>
      <td style="color:${{wc}};font-family:var(--mono);font-weight:700">${{_fmtRs(tradePnl)}}</td>
      <td style="color:var(--muted2)">${{(r.targets_hit||'').replace(/[\\[\\]']/g,'')}}</td>
    </tr>`;
  }}).join('');

  const tradeTable = `
    <div>
      <div style="font-size:10px;color:var(--muted);font-family:var(--mono);letter-spacing:0.6px;margin-bottom:6px">
        TRADE LOG — ${{rows.length}} setups · Risk/trade ₹${{Math.round(riskAmount).toLocaleString('en-IN')}} · (showing latest 200)
      </div>
      <div class="bt-trade-wrap">
        <table class="bt-trade-table">
          <thead><tr>
            <th>Date</th><th>Time</th><th>Dir</th><th>Entry</th>
            <th>SL</th><th>Risk pts</th><th>Result</th><th>R-mult</th><th>P&L (₹)</th><th>Targets</th>
          </tr></thead>
          <tbody>${{tradeRows || '<tr><td colspan="10" style="color:var(--muted);padding:10px">No trades</td></tr>'}}</tbody>
        </table>
      </div>
    </div>`;

  document.getElementById('btContent').innerHTML = statCards + pnlCards + monthTable + tradeTable;
}}

async function runBacktest() {{
  const btn = document.getElementById('btRunBtn');
  const st  = document.getElementById('btRunStatus');
  if (!confirm('Run fresh 180-day backtest on NIFTY?\\n\\nThis fetches live data from Fyers and takes 2-5 minutes.\\nResults will auto-refresh when complete.')) return;
  btn.disabled = true; btn.textContent = '⏳ Running…';
  st.textContent = 'Fetching data + scanning…';
  try {{
    const r = await fetch('/api/run_backtest', {{method:'POST'}});
    const d = await r.json();
    st.textContent = d.message || '';
    if (d.ok) {{
      // Poll every 8s until done
      _btPollInterval = setInterval(async () => {{
        try {{
          const sr = await fetch('/api/backtest_status');
          const sd = await sr.json();
          st.textContent = sd.status;
          if (sd.status === 'done' || sd.status.startsWith('error')) {{
            clearInterval(_btPollInterval); _btPollInterval = null;
            btn.disabled = false; btn.textContent = '▶ RUN FRESH BACKTEST';
            if (sd.status === 'done') {{ fetchBtData(); }}
          }}
        }} catch(e) {{}}
      }}, 8000);
    }} else {{
      btn.disabled = false; btn.textContent = '▶ RUN FRESH BACKTEST';
    }}
  }} catch(e) {{
    st.textContent = 'Error: ' + e;
    btn.disabled = false; btn.textContent = '▶ RUN FRESH BACKTEST';
  }}
}}
</script>
</body>
</html>"""


# ── HTTP handler ──────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/api/snapshot':
            self._json(api_snapshot())
        elif path.startswith('/api/logs'):
            n = 80
            try:
                n = int(parse_qs(parsed.query).get('n', ['80'])[0])
            except Exception: pass
            self._json(tail_log(n))
        elif path == '/api/backtest':
            self._json(load_backtest_data())
        elif path == '/api/backtest_status':
            self._json({'status': _bt_status, 'running': _bt_running})
        elif path == '/api/ml_chat':
            try:
                from ml_engine.chat.dashboard_chat import ask_ml
                q = parse_qs(parsed.query).get('q', [''])[0]
                self._json(ask_ml(q))
            except Exception as e:
                self._json({
                    'ok': False,
                    'error': str(e),
                    'shadow_only': True,
                    'execution_unchanged': True,
                })
        elif path == '/ml-chat':
            self._html(_ml_chat_page())
        else:
            try:
                html = generate_dashboard().encode('utf-8')
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            except (ConnectionAbortedError, BrokenPipeError):
                pass  # browser closed the tab / navigated away mid-response
            except Exception as _e:
                import traceback
                logger.error(f"Dashboard render error: {_e}\n{traceback.format_exc()}")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/api/archive':
            self._json(archive_trades())
        elif path == '/api/run_backtest':
            self._json(trigger_backtest_bg())
        elif path == '/api/panic_close':
            try:
                from trader.paper_trader import square_off_all_trades
                square_off_all_trades()
                self._json({'ok': True, 'message': 'All positions squared off'})
            except Exception as e:
                self._json({'ok': False, 'message': str(e)})
        elif path == '/api/ml_chat':
            try:
                from ml_engine.chat.dashboard_chat import ask_ml
                length = int(self.headers.get('Content-Length', '0') or 0)
                body = self.rfile.read(length).decode('utf-8', errors='ignore') if length > 0 else '{}'
                payload = json.loads(body or '{}')
                q = str(payload.get('question', ''))
                self._json(ask_ml(q))
            except Exception as e:
                self._json({
                    'ok': False,
                    'error': str(e),
                    'shadow_only': True,
                    'execution_unchanged': True,
                })
        else:
            self.send_response(404); self.end_headers()

    def _json(self, data):
        try:
            body = json.dumps(data, default=str).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError):
            pass

    def _html(self, html_text: str):
        try:
            body = html_text.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError):
            pass

    def log_message(self, *args): pass


def _ml_chat_page() -> str:
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>CB6 ML Chat</title>
  <style>
    body { background:#0b0f14; color:#e6edf3; font-family:Inter,Arial,sans-serif; margin:0; }
    .wrap { max-width:980px; margin:18px auto; padding:0 14px; }
    .top { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
    .pill { font-size:12px; color:#89ddff; border:1px solid #1e4158; padding:6px 10px; border-radius:8px; }
    .chat { border:1px solid #1a2230; border-radius:10px; background:#0f1520; min-height:420px; padding:12px; overflow:auto; }
    .msg { margin:10px 0; padding:10px 12px; border-radius:8px; white-space:pre-wrap; line-height:1.35; }
    .u { background:#122235; border:1px solid #234; }
    .a { background:#101b14; border:1px solid #1f3b29; }
    .bar { display:flex; gap:8px; margin-top:10px; }
    input { flex:1; background:#0a0f18; color:#fff; border:1px solid #2a3345; border-radius:8px; padding:10px; }
    button { background:#00d9ff; color:#00131a; border:none; border-radius:8px; padding:10px 14px; font-weight:600; cursor:pointer; }
    .hint { color:#95a2b3; font-size:12px; margin-top:8px; }
    a { color:#89ddff; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <h2 style="margin:0">CB6 ML Chat (Shadow Research)</h2>
      <div class="pill">Read-only · No execution changes</div>
    </div>
    <div style="margin-bottom:8px"><a href="/">← Back to Dashboard</a></div>
    <div id="chat" class="chat"></div>
    <div class="bar">
      <input id="q" placeholder="Ask: best setup for NSE 13:30? what should we skip?" />
      <button onclick="sendQ()">Ask ML</button>
    </div>
    <div class="hint">Safety lock: advisory only. No SL/TP/lot/risk/trade control.</div>
  </div>
  <script>
    const chat = document.getElementById('chat');
    const qEl = document.getElementById('q');
    function addMsg(cls, txt) {
      const d = document.createElement('div');
      d.className = 'msg ' + cls;
      d.textContent = txt;
      chat.appendChild(d);
      chat.scrollTop = chat.scrollHeight;
    }
    async function sendQ() {
      const q = (qEl.value || '').trim();
      if (!q) return;
      addMsg('u', 'You: ' + q);
      qEl.value = '';
      try {
        const r = await fetch('/api/ml_chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question: q})
        });
        const d = await r.json();
        addMsg('a', 'CB6 ML: ' + (d.answer || d.error || 'No response'));
      } catch (e) {
        addMsg('a', 'CB6 ML: Chat request failed: ' + e);
      }
    }
    qEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendQ(); });
    addMsg('a', 'CB6 ML is ready. Ask any research question.\\n\\nThis is shadow-only advisory mode.');
  </script>
</body>
</html>"""


def start_dashboard(port=8080):
    try:
        server = HTTPServer(('localhost', port), DashboardHandler)
        logger.info(f"CB6 Quantum Dashboard → http://localhost:{port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
