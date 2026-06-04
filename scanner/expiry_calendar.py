# scanner/expiry_calendar.py â€” Indian F&O expiry tracker
#
# Primary source: Fyers public instruments master CSV (no auth required).
#   NSE: https://public.fyers.in/sym_details/NSE_FO.csv
#   BSE: https://public.fyers.in/sym_details/BSE_FO.csv
# Column layout (no header row):
#   [0] fytoken  [1] description  [8] expiry epoch  [9] Fyers symbol  [13] underlying
#
# Classification rule: the last expiry date within a calendar month
# is the MONTHLY contract; all earlier dates in that month are WEEKLIES.
#
# Fallback: pure date-math when the CSV download fails.
import calendar
import urllib.request
import io
import csv
import threading
from datetime import datetime, date, timedelta

# â”€â”€â”€ FALLBACK CALCULATION CONFIG â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

MON, TUE, WED, THU, FRI = 0, 1, 2, 3, 4

# Keep for backwards compatibility with any callers that import these directly
WEEKLY_EXPIRY_DAY = {
    'NIFTY'      : TUE,   # MOVED to Tuesday (confirmed from live data)
    'BANKNIFTY'  : WED,
    'FINNIFTY'   : TUE,
    'MIDCPNIFTY' : MON,
    'SENSEX'     : THU,
    'BANKEX'     : MON,
}

HAS_WEEKLY_OPTIONS   = {'NIFTY', 'SENSEX'}
MONTHLY_ONLY_INDICES = {'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'BANKEX'}
STOCK_FNO_EXPIRY_DAY = THU

# Public CSV URLs â€” no authentication required
_NSE_FO_CSV = 'https://public.fyers.in/sym_details/NSE_FO.csv'
_BSE_FO_CSV = 'https://public.fyers.in/sym_details/BSE_FO.csv'

# NSE underlyings covered by NSE_FO.csv
_NSE_INDICES = {'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTYNXT50'}
# BSE underlyings covered by BSE_FO.csv
_BSE_INDICES = {'SENSEX', 'BANKEX'}

# Cache: {underlying: [date, ...]}  refreshed once per calendar day
_cache: dict = {}
_cache_date: date | None = None
_cache_lock = threading.Lock()


# â”€â”€â”€ FALLBACK DATE MATH â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _last_weekday_of_month(year, month, weekday):
    last_day  = calendar.monthrange(year, month)[1]
    last_date = datetime(year, month, last_day)
    offset    = (last_date.weekday() - weekday) % 7
    return last_date - timedelta(days=offset)


def _next_or_today_weekday(from_date, weekday):
    days_ahead = (weekday - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead)


# â”€â”€â”€ INSTRUMENTS CSV LOADER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _download_expiries(url: str) -> dict:
    """
    Download a Fyers instruments CSV and return {underlying: sorted [date]}.
    Col [8] = expiry epoch, col [13] = underlying name.
    """
    result: dict = {}
    try:
        req     = urllib.request.urlopen(url, timeout=10)
        content = req.read().decode('utf-8', errors='replace')
        today   = date.today()
        for row in csv.reader(io.StringIO(content)):
            if len(row) < 14:
                continue
            underlying = row[13].strip().upper()
            if not underlying:
                continue
            try:
                epoch = int(row[8])
                if epoch <= 0:
                    continue
                dt = datetime.fromtimestamp(epoch).date()
                if dt < today:
                    continue
                result.setdefault(underlying, []).append(dt)
            except (ValueError, OSError):
                continue
    except Exception:
        pass
    for key in result:
        result[key] = sorted(set(result[key]))
    return result


def _refresh_cache():
    """Download both CSVs and populate _cache. Call once per day."""
    global _cache, _cache_date
    nse = _download_expiries(_NSE_FO_CSV)
    bse = _download_expiries(_BSE_FO_CSV)
    merged = {}
    merged.update(nse)
    merged.update(bse)
    with _cache_lock:
        _cache      = merged
        _cache_date = date.today()


def _get_cache() -> dict:
    """Return cache, refreshing if stale (new calendar day or empty)."""
    with _cache_lock:
        today = date.today()
        if _cache_date == today and _cache:
            return _cache
    _refresh_cache()
    with _cache_lock:
        return _cache


# â”€â”€â”€ EXPIRY CLASSIFICATION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _classify(dates: list) -> dict:
    """
    Given sorted future expiry dates for one underlying, return:
      weekly_next  â€” first upcoming weekly (None if none)
      monthly_next â€” first upcoming monthly
    Rule: last expiry within a calendar month = MONTHLY; earlier = WEEKLY.
    """
    by_month: dict = {}
    for d in dates:
        key = (d.year, d.month)
        by_month.setdefault(key, []).append(d)

    weeklies, monthlies = [], []
    for month_dates in sorted(by_month.values()):
        mx = max(month_dates)
        monthlies.append(mx)
        weeklies.extend(d for d in month_dates if d != mx)

    today = date.today()
    future_w = [d for d in weeklies  if d >= today]
    future_m = [d for d in monthlies if d >= today]
    return {
        'weekly_next' : future_w[0] if future_w else None,
        'monthly_next': future_m[0] if future_m else None,
    }


# â”€â”€â”€ PUBLIC API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _to_dt(d: date | None) -> datetime | None:
    if d is None:
        return None
    return datetime(d.year, d.month, d.day, 15, 30, 0)


def _get_index_expiries(index: str) -> dict:
    """
    Returns {'weekly': datetime|None, 'monthly': datetime|None, 'live': bool}.
    Uses live CSV; falls back to calculation.
    """
    idx   = index.upper()
    cache = _get_cache()
    dates = cache.get(idx)

    if dates:
        cl = _classify(dates)
        idx_has_weekly = idx in HAS_WEEKLY_OPTIONS
        return {
            'weekly' : _to_dt(cl['weekly_next']) if idx_has_weekly else None,
            'monthly': _to_dt(cl['monthly_next']),
            'live'   : True,
        }

    # Fallback to calculation
    weekday = WEEKLY_EXPIRY_DAY.get(idx, THU)
    now     = datetime.now()
    y, m    = now.year, now.month
    monthly_dt = _last_weekday_of_month(y, m, weekday)
    if monthly_dt.date() < now.date():
        m += 1
        if m > 12: m, y = 1, y + 1
        monthly_dt = _last_weekday_of_month(y, m, weekday)
    monthly_dt = monthly_dt.replace(hour=15, minute=30, second=0, microsecond=0)

    weekly_dt = None
    if idx in HAS_WEEKLY_OPTIONS:
        weekly_dt = _next_or_today_weekday(now, weekday).replace(
            hour=15, minute=30, second=0, microsecond=0
        )

    return {'weekly': weekly_dt, 'monthly': monthly_dt, 'live': False}


# Legacy public names kept for callers that import them directly

def next_weekly_expiry(index):
    return _get_index_expiries(index)['weekly']


def next_monthly_expiry(index, year=None, month=None):
    if year is None and month is None:
        return _get_index_expiries(index)['monthly']
    idx     = index.upper()
    weekday = WEEKLY_EXPIRY_DAY.get(idx, THU)
    now     = datetime.now()
    y = year or now.year
    m = month or now.month
    expiry = _last_weekday_of_month(y, m, weekday)
    if expiry.date() < now.date():
        m += 1
        if m > 12: m, y = 1, y + 1
        expiry = _last_weekday_of_month(y, m, weekday)
    return expiry.replace(hour=15, minute=30, second=0, microsecond=0)


def next_index_future_expiry(index='NIFTY', year=None, month=None):
    return next_monthly_expiry(index, year, month)


def get_active_index_option_expiry(index, prefer='AUTO'):
    info = _get_index_expiries(index)
    if prefer == 'WEEKLY':
        return info['weekly']
    if prefer == 'MONTHLY':
        return info['monthly']
    return info['weekly'] or info['monthly']


def next_stock_fno_expiry():
    now = datetime.now()
    y, m = now.year, now.month
    expiry = _last_weekday_of_month(y, m, STOCK_FNO_EXPIRY_DAY)
    if expiry.date() < now.date():
        m += 1
        if m > 12: m, y = 1, y + 1
        expiry = _last_weekday_of_month(y, m, STOCK_FNO_EXPIRY_DAY)
    return expiry.replace(hour=15, minute=30, second=0, microsecond=0)


# â”€â”€â”€ COUNTDOWN / EXPIRY-DAY DETECTION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def days_to_expiry(expiry_date) -> int | None:
    if expiry_date is None:
        return None
    d = expiry_date.date() if isinstance(expiry_date, datetime) else expiry_date
    return (d - date.today()).days


def is_expiry_today(expiry_date) -> bool:
    if expiry_date is None:
        return False
    d = expiry_date.date() if isinstance(expiry_date, datetime) else expiry_date
    return d == date.today()


def is_any_expiry_today():
    today   = date.today()
    matches = []
    for idx in list(HAS_WEEKLY_OPTIONS) + list(MONTHLY_ONLY_INDICES):
        info = _get_index_expiries(idx)
        if info['weekly']  and info['weekly'].date()  == today: matches.append(f"{idx}_WEEKLY")
        if info['monthly'] and info['monthly'].date() == today: matches.append(f"{idx}_MONTHLY")
    stock = next_stock_fno_expiry()
    if stock.date() == today:
        matches.append("STOCK_FNO_MONTHLY")
    return (len(matches) > 0, matches)


# â”€â”€â”€ SUMMARY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_DISPLAY_INDICES = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'BANKEX']


def format_expiry_summary() -> str:
    """Telegram-friendly F&O expiry calendar using live Fyers instruments data."""
    today_str = datetime.now().strftime('%Y-%m-%d (%a)')
    indices   = {idx: _get_index_expiries(idx) for idx in _DISPLAY_INDICES}
    any_live  = any(v['live'] for v in indices.values())
    src_tag   = "live" if any_live else "estimated"

    is_exp, exp_list = is_any_expiry_today()
    fno = next_stock_fno_expiry()

    lines = [
        f"CB6 QUANTUM - F&O EXPIRY CALENDAR",
        f"Today: {today_str}  [{src_tag}]",
    ]

    if is_exp:
        lines.append(f"\nEXPIRY TODAY: {', '.join(exp_list)}")
        lines.append("Expect elevated volatility + theta burn.")

    lines.append(
        f"\nSTOCK F&O (monthly): {fno.strftime('%a %d %b')} ({days_to_expiry(fno)}d)"
    )

    lines.append("\nINDEX OPTIONS / FUTURES:")
    for idx in _DISPLAY_INDICES:
        info    = indices[idx]
        weekly  = info['weekly']
        monthly = info['monthly']

        if idx in HAS_WEEKLY_OPTIONS and weekly:
            w_str = f"W: {weekly.strftime('%a %d %b')} ({days_to_expiry(weekly)}d)"
        else:
            w_str = "W: monthly only"

        if monthly:
            m_str = f"M: {monthly.strftime('%a %d %b')} ({days_to_expiry(monthly)}d)"
        else:
            m_str = "M: n/a"

        lines.append(f"  {idx:11s}  {w_str}  |  {m_str}")

    if not any_live:
        lines.append("\n(Fyers CSV unavailable - dates estimated)")

    return "\n".join(lines)


# â”€â”€â”€ LEGACY get_expiry_summary kept for any callers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def get_expiry_summary():
    now = datetime.now()
    return {
        'today'            : now.strftime('%Y-%m-%d (%a)'),
        'stock_fno_monthly': next_stock_fno_expiry(),
        'indices'          : {idx: _get_index_expiries(idx) for idx in _DISPLAY_INDICES},
        'expiry_today'     : is_any_expiry_today()[1],
    }


# â”€â”€â”€ BACKWARD COMPAT: set_fyers_client is a no-op now â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def set_fyers_client(fyers):
    pass  # No longer needed â€” CSV requires no auth


if __name__ == '__main__':
    print(format_expiry_summary())

