import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import dotenv_values
from fyers_apiv3 import fyersModel
from scanner.index_futures import get_active_futures
from scanner.data_fetcher import get_historical_data, clear_cache
from scanner.silver_bullet import scan_silver_bullet
from trader.paper_trader import open_paper_trade
from utils.telegram_alerts import send_message

env = dotenv_values(os.path.join(os.path.dirname(__file__), '.env'))
t = env.get('ACCESS_TOKEN', '')
if ':' in t:
    t = t.split(':', 1)[1]
fyers = fyersModel.FyersModel(
    client_id=env.get('CLIENT_ID', ''), token=t,
    is_async=False, log_path=os.path.join(os.path.dirname(__file__), 'logs', '')
)

clear_cache()
futures = get_active_futures()
indexes = {
    futures['NIFTY']      : 'NIFTY',
    futures['BANKNIFTY']  : 'BANKNIFTY',
    futures['FINNIFTY']   : 'FINNIFTY',
    futures['MIDCPNIFTY'] : 'MIDCPNIFTY',
}

print('Scanning all 4 indexes for live setups...')
found_any = False

for sym, name in indexes.items():
    df = get_historical_data(fyers, sym, '3', days=3)
    if df is None or len(df) < 30:
        print(name + ': no data')
        continue

    ltp   = round(float(df['close'].iloc[-1]), 2)
    setup = scan_silver_bullet(df, sym, tf='3', fyers=fyers)

    if not setup:
        print(name + ': no setup | LTP=' + str(ltp))
        continue

    found_any  = True
    sig        = setup['entry_signal']
    direction  = setup['direction']
    mss_type   = setup.get('mss_type', 'BOS')
    score      = setup['confluence']
    ut         = setup.get('ut_bot', {})
    ut_aligned = ut.get('aligned', False)

    print(name + ' SETUP | ' + direction + ' | ' + mss_type +
          ' | Score ' + str(score) + '/20 | LTP ' + str(ltp) +
          ' | UT ' + ('aligned' if ut_aligned else 'counter'))
    print('  Entry ' + str(sig['entry']) +
          ' SL ' + str(sig['stop_loss']) +
          ' T1 ' + str(sig['target1']) +
          ' T2 ' + str(sig['target2']) +
          ' T3 ' + str(sig['target3']))

    setup['instrument_type'] = 'INDEX'

    option_info = setup.get('option_info')
    try:
        from trader.order_manager import place_silver_bullet_trade, place_futures_trade
        live = os.getenv('SCAN_NOW_LIVE', 'false').strip().lower() == 'true'
        mode_label = 'LIVE' if live else 'paper'
        # ── Futures trade (primary — direct index futures) ────────────────────
        place_futures_trade(fyers, setup, paper_mode=not live)
        print(f'  TRADE FIRED (futures {mode_label} trade)')
        # ── Options trade (secondary — if strike available) ───────────────────
        if option_info:
            opt_sym = option_info.get('symbol', '')
            opt_ltp = option_info.get('ltp', '')
            print('  Option : ' + opt_sym + ' LTP ' + str(opt_ltp))
            place_silver_bullet_trade(fyers, setup, option_info, paper_mode=not live)
            print(f'  TRADE FIRED (option {mode_label} trade)')
        else:
            print('  No option strike found — futures trade only')
    except Exception as oe:
        print('  Order manager error: ' + str(oe))

if not found_any:
    print('No setups on any index right now.')
