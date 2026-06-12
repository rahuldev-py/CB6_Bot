import MetaTrader5 as mt5

terminals = [
    ('MT5_GFT_5K',  r'C:\CB6_MT5\MT5_GFT_5K\terminal64.exe'),
    ('MT5_GFT_1K',  r'C:\CB6_MT5\MT5_GFT_1K\terminal64.exe'),
    ('MT5_GFT_10K', r'C:\CB6_MT5\MT5_GFT_10K\terminal64.exe'),
]

for name, path in terminals:
    ok = mt5.initialize(path=path)
    if ok:
        info = mt5.account_info()
        if info:
            print(f'{name}: login={info.login} balance=${info.balance:.2f} server={info.server} name={info.name}')
        else:
            print(f'{name}: connected but no account info — {mt5.last_error()}')
    else:
        print(f'{name}: INIT FAILED — {mt5.last_error()}')
    mt5.shutdown()
