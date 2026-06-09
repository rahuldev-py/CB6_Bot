from dotenv import dotenv_values
import os
env = dotenv_values('.env')
for k, v in env.items():
    os.environ[k] = v

print('=== NSE SCANNER TEST ===')
try:
    from fyers_apiv3 import fyersModel
    from scanner.data_fetcher import get_historical_data
    from scanner.silver_bullet import detect_sb_mss
    client_id = env.get('CLIENT_ID', '')
    token_str = env.get('ACCESS_TOKEN', '')
    if ':' in token_str:
        token_str = token_str.split(':', 1)[1]
    fyers = fyersModel.FyersModel(client_id=client_id, token=token_str, is_async=False, log_path='logs/')
    df = get_historical_data(fyers, 'NSE:NIFTY50-INDEX', '5', days=3)
    if df is not None and not df.empty:
        print('Data fetch: OK | Rows:', len(df))
        mss = detect_sb_mss(df, lookback=40)
        print('MSS scan: OK |', mss['direction'] if mss else 'No setup')
    else:
        print('Data fetch: No data (market closed or token expired)')
except Exception as e:
    print('FAIL:', e)

# Crypto engine shelved — section removed

print()
print('=== FOREX SCANNER TEST ===')
try:
    from forex_engine.mt5.mt5_connector import MT5Connector
    from forex_engine.scanner.signal_scanner import scan_setup
    adapter = MT5Connector(paper=False)
    for sym in ['XAGUSD', 'USOIL']:
        df = adapter.get_klines(sym, '15m', 150)
        if df is not None:
            setup = scan_setup(df, sym)
            if setup:
                status = 'Setup: ' + setup['direction'] + ' score=' + str(setup['confluence'])
            else:
                status = 'No setup'
            print(sym + ': OK | ' + status)
        else:
            print(sym + ': No data')
    adapter.disconnect()
except Exception as e:
    print('FAIL:', e)
