# scanner/index_futures.py — Auto-generates active NIFTY/BANKNIFTY futures
# symbols and provides comprehensive F&O lot sizes for indices + stocks.
# Source: Dhan NSE F&O lot size table (May 2026 contracts).
import re
import calendar
from datetime import datetime, timedelta

# ─── INDEX LOT SIZES ─────────────────────────────────────────────────────────
INDEX_LOT_SIZES = {
    'NIFTY'     : 65,
    'BANKNIFTY' : 30,
    'FINNIFTY'  : 60,
    'MIDCPNIFTY': 120,
    'NIFTYNXT50': 25,
}

# ─── STOCK F&O LOT SIZES (May 2026 contracts) ────────────────────────────────
STOCK_LOT_SIZES = {
    '360ONE': 500, 'ABB': 125, 'ABCAPITAL': 3100, 'ADANIENSOL': 675,
    'ADANIENT': 309, 'ADANIGREEN': 600, 'ADANIPORTS': 475, 'ADANIPOWER': 3550,
    'ALKEM': 125, 'AMBER': 100, 'AMBUJACEM': 1050, 'ANGELONE': 2500,
    'APLAPOLLO': 350, 'APOLLOHOSP': 125, 'ASHOKLEY': 5000, 'ASIANPAINT': 250,
    'ASTRAL': 425, 'AUBANK': 1000, 'AUROPHARMA': 550, 'AXISBANK': 625,
    'BAJAJ-AUTO': 75, 'BAJAJFINSV': 250, 'BAJAJHLDNG': 50, 'BAJFINANCE': 750,
    'BANDHANBNK': 3600, 'BANKBARODA': 2925, 'BANKINDIA': 5200, 'BDL': 350,
    'BEL': 1425, 'BHARATFORG': 500, 'BHARTIARTL': 475, 'BHEL': 2625,
    'BIOCON': 2500, 'BLUESTARCO': 325, 'BOSCHLTD': 25, 'BPCL': 1975,
    'BRITANNIA': 125, 'BSE': 375, 'CAMS': 750, 'CANBK': 6750, 'CDSL': 475,
    'CGPOWER': 850, 'CHOLAFIN': 625, 'CIPLA': 375, 'COALINDIA': 1350,
    'COCHINSHIP': 400, 'COFORGE': 375, 'COLPAL': 225, 'CONCOR': 1250,
    'CROMPTON': 1800, 'CUMMINSIND': 200, 'DABUR': 1250, 'DALBHARAT': 325,
    'DELHIVERY': 2075, 'DIVISLAB': 100, 'DIXON': 50, 'DLF': 825, 'DMART': 150,
    'DRREDDY': 625, 'EICHERMOT': 100, 'ETERNAL': 2425, 'EXIDEIND': 1800,
    'FEDERALBNK': 2500, 'FORCEMOT': 25, 'FORTIS': 775, 'GAIL': 3150,
    'GLENMARK': 375, 'GMRAIRPORT': 6975, 'GODFRYPHLP': 275, 'GODREJCP': 500,
    'GODREJPROP': 275, 'GRASIM': 250, 'HAL': 150, 'HAVELLS': 500,
    'HCLTECH': 350, 'HDFCAMC': 300, 'HDFCBANK': 550, 'HDFCLIFE': 1100,
    'HEROMOTOCO': 150, 'HINDALCO': 700, 'HINDPETRO': 2025, 'HINDUNILVR': 300,
    'HINDZINC': 1225, 'HYUNDAI': 275, 'ICICIBANK': 700, 'ICICIGI': 325,
    'ICICIPRULI': 925, 'IDEA': 71475, 'IDFCFIRSTB': 9275, 'IEX': 3750,
    'INDHOTEL': 1000, 'INDIANB': 1000, 'INDIGO': 150, 'INDUSINDBK': 700,
    'INDUSTOWER': 1700, 'INFY': 400, 'INOXWIND': 3575, 'IOC': 4875,
    'IREDA': 3450, 'IRFC': 4250, 'ITC': 1600, 'JINDALSTEL': 625,
    'JIOFIN': 2350, 'JSWENERGY': 1000, 'JSWSTEEL': 675, 'JUBLFOOD': 1250,
    'KALYANKJIL': 1175, 'KAYNES': 100, 'KEI': 175, 'KFINTECH': 500,
    'KOTAKBANK': 2000, 'KPITTECH': 425, 'LAURUSLABS': 850, 'LICHSGFIN': 1000,
    'LICI': 700, 'LODHA': 450, 'LT': 175, 'LTF': 2250, 'LTM': 150,
    'LUPIN': 425, 'M&M': 200, 'MANAPPURAM': 3000, 'MANKIND': 225,
    'MARICO': 1200, 'MARUTI': 50, 'MAXHEALTH': 525, 'MAZDOCK': 200,
    'MCX': 625, 'MFSL': 400, 'MOTHERSON': 6150, 'MOTILALOFS': 775,
    'MPHASIS': 275, 'MUTHOOTFIN': 275, 'NAM-INDIA': 625, 'NATIONALUM': 1875,
    'NAUKRI': 375, 'NBCC': 6500, 'NESTLEIND': 500, 'NHPC': 6400, 'NMDC': 6750,
    'NTPC': 1500, 'NUVAMA': 500, 'NYKAA': 3125, 'OBEROIRLTY': 350, 'OFSS': 75,
    'OIL': 1400, 'ONGC': 2250, 'PAGEIND': 15, 'PATANJALI': 900, 'PAYTM': 725,
    'PERSISTENT': 100, 'PETRONET': 1900, 'PFC': 1300, 'PGEL': 950,
    'PHOENIXLTD': 350, 'PIDILITIND': 500, 'PIIND': 175, 'PNB': 8000,
    'PNBHOUSING': 650, 'POLICYBZR': 350, 'POLYCAB': 125, 'POWERGRID': 1900,
    'POWERINDIA': 25, 'PREMIERENE': 575, 'PRESTIGE': 450, 'RBLBANK': 3175,
    'RECLTD': 1400, 'RELIANCE': 500, 'RVNL': 1525, 'SAIL': 4700,
    'SAMMAANCAP': 4300, 'SBICARD': 800, 'SBILIFE': 375, 'SBIN': 750,
    'SHREECEM': 25, 'SHRIRAMFIN': 825, 'SIEMENS': 175, 'SOLARINDS': 50,
    'SONACOMS': 1225, 'SRF': 200, 'SUNPHARMA': 350, 'SUPREMEIND': 175,
    'SUZLON': 9025, 'SWIGGY': 1300, 'TATACONSUM': 550, 'TATAELXSI': 100,
    'TATAPOWER': 1450, 'TATASTEEL': 2750, 'TCS': 175, 'TECHM': 600,
    'TIINDIA': 200, 'TITAN': 175, 'TMPV': 800, 'TORNTPHARM': 125,
    'TRENT': 100, 'TVSMOTOR': 175, 'ULTRACEMCO': 50, 'UNIONBANK': 4425,
    'UNITDSPR': 400, 'UNOMINDA': 550, 'UPL': 1355, 'VBL': 1125, 'VEDL': 1150,
    'VMM': 4850, 'VOLTAS': 375, 'WAAREEENER': 175, 'WIPRO': 3000,
    'YESBANK': 31100, 'ZYDUSLIFE': 900,
}

# Backward-compat alias used by paper_trader._round_to_lot
LOT_SIZES = INDEX_LOT_SIZES


# ─── FUTURES SYMBOL HELPERS ──────────────────────────────────────────────────

def _last_thursday(year, month):
    """Return the last Thursday of the given month as a datetime."""
    last_day  = calendar.monthrange(year, month)[1]
    last_date = datetime(year, month, last_day)
    offset    = (last_date.weekday() - 3) % 7   # Thu = weekday 3
    return last_date - timedelta(days=offset)


def _fut_symbol(index, year, month):
    """Build Fyers futures symbol. e.g. 'NIFTY', 2026, 5 → 'NSE:NIFTY26MAYFUT'"""
    mon = datetime(year, month, 1).strftime('%b').upper()
    yy  = str(year)[-2:]
    return f"NSE:{index}{yy}{mon}FUT"


def get_active_futures(rollover_days=3):
    """Return {index: symbol} dict for the active contract month."""
    now    = datetime.now()
    y, m   = now.year, now.month
    expiry = _last_thursday(y, m)

    if (expiry.date() - now.date()).days < rollover_days:
        m += 1
        if m > 12:
            m, y = 1, y + 1

    return {idx: _fut_symbol(idx, y, m) for idx in INDEX_LOT_SIZES}


def get_nifty_symbols():
    """Return [NIFTY_FUT, BANKNIFTY_FUT] for the active month."""
    f = get_active_futures()
    return [f['NIFTY'], f['BANKNIFTY']]


# ─── SYMBOL → BASE TICKER ────────────────────────────────────────────────────

# Match e.g. NSE:RELIANCE26MAYFUT → RELIANCE, NSE:NIFTY26MAYFUT → NIFTY
_FUT_PATTERN = re.compile(r'^(?:NSE:)?(.+?)\d{2}[A-Z]{3}FUT$', re.IGNORECASE)


def _base_ticker(symbol):
    """Strip NSE: prefix, futures month/year, -EQ suffix → bare ticker."""
    s = symbol.upper().strip()
    m = _FUT_PATTERN.match(s)
    if m:
        return m.group(1)
    return s.replace('NSE:', '').replace('-EQ', '').replace('-INDEX', '')


def get_lot_size(symbol):
    """
    Lot size for any F&O symbol — index futures, stock futures, or equity.
    Returns 1 for cash equity / unmapped tickers.
    """
    sym = symbol.upper()
    if 'FUT' not in sym:
        return 1
    base = _base_ticker(symbol)
    if base in INDEX_LOT_SIZES:
        return INDEX_LOT_SIZES[base]
    if base in STOCK_LOT_SIZES:
        return STOCK_LOT_SIZES[base]
    return 1


def is_futures(symbol):
    return 'FUT' in symbol.upper()


def is_index_future(symbol):
    base = _base_ticker(symbol)
    return base in INDEX_LOT_SIZES and 'FUT' in symbol.upper()
