# scanner/nifty50.py — Nifty 50 + Nifty 200 Stock Universe for CB6 Bot

# ── NIFTY 50 ──────────────────────────────────────────────────────────────────
NIFTY50_SYMBOLS = [
    "NSE:RELIANCE-EQ",   "NSE:TCS-EQ",         "NSE:HDFCBANK-EQ",
    "NSE:INFY-EQ",       "NSE:ICICIBANK-EQ",   "NSE:HINDUNILVR-EQ",
    "NSE:ITC-EQ",        "NSE:SBIN-EQ",        "NSE:BHARTIARTL-EQ",
    "NSE:KOTAKBANK-EQ",  "NSE:LT-EQ",          "NSE:HCLTECH-EQ",
    "NSE:AXISBANK-EQ",   "NSE:ASIANPAINT-EQ",  "NSE:MARUTI-EQ",
    "NSE:SUNPHARMA-EQ",  "NSE:TITAN-EQ",       "NSE:ULTRACEMCO-EQ",
    "NSE:BAJFINANCE-EQ", "NSE:WIPRO-EQ",       "NSE:ONGC-EQ",
    "NSE:NTPC-EQ",       "NSE:POWERGRID-EQ",   "NSE:TECHM-EQ",
    "NSE:NESTLEIND-EQ",  "NSE:TVSMOTOR-EQ",    "NSE:ADANIENT-EQ",
    "NSE:JSWSTEEL-EQ",   "NSE:TATASTEEL-EQ",   "NSE:HINDALCO-EQ",
    "NSE:COALINDIA-EQ",  "NSE:BAJAJFINSV-EQ",  "NSE:DRREDDY-EQ",
    "NSE:CIPLA-EQ",      "NSE:APOLLOHOSP-EQ",  "NSE:BRITANNIA-EQ",
    "NSE:EICHERMOT-EQ",  "NSE:HEROMOTOCO-EQ",  "NSE:BPCL-EQ",
    "NSE:TATACONSUM-EQ", "NSE:GRASIM-EQ",      "NSE:DIVISLAB-EQ",
    "NSE:SBILIFE-EQ",    "NSE:HDFCLIFE-EQ",    "NSE:BAJAJ-AUTO-EQ",
    "NSE:UPL-EQ",        "NSE:INDUSINDBK-EQ",  "NSE:M&M-EQ",
    "NSE:ADANIPORTS-EQ", "NSE:SHRIRAMFIN-EQ",
]

# ── NIFTY NEXT 50 ─────────────────────────────────────────────────────────────
NIFTY_NEXT50_SYMBOLS = [
    # Banking
    "NSE:BANKBARODA-EQ", "NSE:PNB-EQ",         "NSE:CANBK-EQ",
    "NSE:UNIONBANK-EQ",  "NSE:IDFCFIRSTB-EQ",  "NSE:BANDHANBNK-EQ",
    "NSE:FEDERALBNK-EQ", "NSE:AUBANK-EQ",
    # NBFC & Capital Markets
    "NSE:CHOLAFIN-EQ",   "NSE:MUTHOOTFIN-EQ",  "NSE:SBICARD-EQ",
    "NSE:CDSL-EQ",       "NSE:BSE-EQ",         "NSE:MCX-EQ",
    "NSE:ANGELONE-EQ",   "NSE:360ONE-EQ",
    # Insurance & AMC
    "NSE:ICICIPRULI-EQ", "NSE:ICICIGI-EQ",     "NSE:HDFCAMC-EQ",
    "NSE:NAM-INDIA-EQ",  "NSE:CAMS-EQ",
    # IT Midcap
    "NSE:MPHASIS-EQ",    "NSE:COFORGE-EQ",     "NSE:LTTS-EQ",
    "NSE:PERSISTENT-EQ", "NSE:TATAELXSI-EQ",
    "NSE:OFSS-EQ",       "NSE:KPITTECH-EQ",
    # Pharma
    "NSE:LUPIN-EQ",      "NSE:TORNTPHARM-EQ",  "NSE:BIOCON-EQ",
    # FMCG / Consumer
    "NSE:DABUR-EQ",      "NSE:MARICO-EQ",      "NSE:GODREJCP-EQ",
    "NSE:COLPAL-EQ",     "NSE:BERGEPAINT-EQ",  "NSE:PIDILITIND-EQ",
    "NSE:TRENT-EQ",      "NSE:VBL-EQ",         "NSE:PAGEIND-EQ",
    # Industrials
    "NSE:SIEMENS-EQ",    "NSE:HAVELLS-EQ",     "NSE:ABB-EQ",
    "NSE:VOLTAS-EQ",     "NSE:POLYCAB-EQ",     "NSE:BOSCHLTD-EQ",
    # Power & Infra
    "NSE:TATAPOWER-EQ",  "NSE:IRCTC-EQ",       "NSE:RECLTD-EQ",
    "NSE:PFC-EQ",
]

# ── NIFTY MIDCAP PICKS ────────────────────────────────────────────────────────
NIFTY_MIDCAP_SYMBOLS = [
    # Defence & PSU
    "NSE:HAL-EQ",        "NSE:BEL-EQ",         "NSE:BHEL-EQ",
    "NSE:GRSE-EQ",       "NSE:COCHINSHIP-EQ",  "NSE:RVNL-EQ",
    "NSE:IRFC-EQ",       "NSE:CONCOR-EQ",      "NSE:NHPC-EQ",
    "NSE:SJVN-EQ",       "NSE:HUDCO-EQ",       "NSE:GMRAIRPORT-EQ",
    # Power & Renewables
    "NSE:ADANIGREEN-EQ", "NSE:TORNTPOWER-EQ",  "NSE:JSWENERGY-EQ",
    "NSE:SUZLON-EQ",     "NSE:CESC-EQ",
    # Pharma / Healthcare
    "NSE:ALKEM-EQ",      "NSE:ABBOTINDIA-EQ",  "NSE:AUROPHARMA-EQ",
    "NSE:GLENMARK-EQ",   "NSE:IPCALAB-EQ",     "NSE:ZYDUSLIFE-EQ",
    "NSE:MANKIND-EQ",    "NSE:LAURUSLABS-EQ",  "NSE:NATCOPHARM-EQ",
    "NSE:LALPATHLAB-EQ", "NSE:MAXHEALTH-EQ",   "NSE:FORTIS-EQ",
    "NSE:METROPOLIS-EQ",
    # IT / Tech
    "NSE:HAPPSTMNDS-EQ", "NSE:ZENSARTECH-EQ",  "NSE:TANLA-EQ",
    "NSE:NAUKRI-EQ",
    # Metals & Mining
    "NSE:SAIL-EQ",       "NSE:NMDC-EQ",        "NSE:JINDALSTEL-EQ",
    "NSE:RATNAMANI-EQ",  "NSE:APLAPOLLO-EQ",
    # Chemicals
    "NSE:DEEPAKNTR-EQ",  "NSE:PIIND-EQ",       "NSE:TATACHEM-EQ",
    "NSE:COROMANDEL-EQ", "NSE:GNFC-EQ",        "NSE:AARTIIND-EQ",
    # FMCG / Retail
    "NSE:UNITDSPR-EQ",   "NSE:RADICO-EQ",
    "NSE:KALYANKJIL-EQ", "NSE:DMART-EQ",       "NSE:JUBLFOOD-EQ",
    "NSE:DEVYANI-EQ",    "NSE:WESTLIFE-EQ",
    # Real Estate
    "NSE:OBEROIRLTY-EQ", "NSE:PRESTIGE-EQ",    "NSE:GODREJPROP-EQ",
    "NSE:BRIGADE-EQ",    "NSE:PHOENIXLTD-EQ",
    # Auto Ancillary
    "NSE:APOLLOTYRE-EQ", "NSE:BALKRISIND-EQ",  "NSE:MOTHERSON-EQ",
    "NSE:CEATLTD-EQ",    "NSE:EXIDEIND-EQ",
    # Aviation & Logistics
    "NSE:INDIGO-EQ",     "NSE:DELHIVERY-EQ",
    # Banking extras
    "NSE:RBLBANK-EQ",    "NSE:KARURVYSYA-EQ",  "NSE:DCBBANK-EQ",
    "NSE:KTKBANK-EQ",
    # NBFC extras
    "NSE:MFSL-EQ",       "NSE:BAJAJHLDNG-EQ",  "NSE:MOTILALOFS-EQ",
    # New Age Tech
    "NSE:ETERNAL-EQ",    "NSE:NYKAA-EQ",       "NSE:POLICYBZR-EQ",
    # Industrials
    "NSE:THERMAX-EQ",    "NSE:CUMMINSIND-EQ",  "NSE:CROMPTON-EQ",
    "NSE:BLUESTARCO-EQ",
    # Cement
    "NSE:AMBUJACEM-EQ",  "NSE:ACC-EQ",         "NSE:JKCEMENT-EQ",
    "NSE:RAMCOCEM-EQ",
    # Telecom
    "NSE:TATACOMM-EQ",   "NSE:HFCL-EQ",
    # Misc
    "NSE:LICI-EQ",       "NSE:RAYMOND-EQ",
]

# ── FULL NIFTY 200 UNIVERSE (deduplicated) ────────────────────────────────────
NIFTY200_SYMBOLS = list(dict.fromkeys(
    NIFTY50_SYMBOLS + NIFTY_NEXT50_SYMBOLS + NIFTY_MIDCAP_SYMBOLS
))

# ── INDEX ETFs (track NIFTY/BANKNIFTY, tradeable like equity) ─────────────────
# NIFTYBEES ≈ NIFTY50 / 100  |  BANKBEES ≈ BANKNIFTY / 100
INDEX_ETF_SYMBOLS = [
    "NSE:NIFTYBEES-EQ",   # Nippon India ETF Nifty BeES
    "NSE:BANKBEES-EQ",    # Nippon India ETF Bank BeES
    "NSE:JUNIORBEES-EQ",  # Mirae Asset NYSE FANG+ ETF (Nifty Next 50)
]

# ── NIFTY / BANKNIFTY FUTURES (auto current-month, rolls over near expiry) ────
try:
    from scanner.index_futures import get_nifty_symbols
    FUTURES_SYMBOLS = get_nifty_symbols()
except Exception:
    FUTURES_SYMBOLS = []

# ── INDEX REFERENCE SYMBOLS (data only, not tradeable) ────────────────────────
INDEX_SYMBOLS = [
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTYBANK-INDEX",
]

# ── FULL SCAN UNIVERSE (equity + index ETFs + futures) ────────────────────────
ALL_SYMBOLS = NIFTY200_SYMBOLS + INDEX_ETF_SYMBOLS + FUTURES_SYMBOLS
