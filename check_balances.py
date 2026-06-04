import os, sys
sys.path.insert(0, r'c:\cb6_bot')
from dotenv import dotenv_values
env = dotenv_values(r'c:\cb6_bot\.env')
for k, v in env.items():
    if k not in os.environ:
        os.environ[k] = v

import MetaTrader5 as mt5

accounts = [
    {
        'label': 'FTMO $10K',
        'login': int(os.environ['MT5_LOGIN']),
        'password': os.environ['MT5_PASSWORD'],
        'server': os.environ['MT5_SERVER'],
        'path': os.environ.get('MT5_TERMINAL_FTMO', ''),
    },
    {
        'label': 'GFT $5K 2-Step',
        'login': int(os.environ['GFT_2STEP_LOGIN']),
        'password': os.environ['GFT_2STEP_PASSWORD'],
        'server': os.environ['GFT_2STEP_SERVER'],
        'path': os.environ.get('MT5_TERMINAL_GFT', ''),
    },
]

for acc in accounts:
    mt5.shutdown()
    kwargs = {
        'login': acc['login'],
        'password': acc['password'],
        'server': acc['server'],
    }
    if acc['path']:
        kwargs['path'] = acc['path']
    ok = mt5.initialize(**kwargs)
    if not ok:
        print(acc['label'] + ': CONNECT FAILED — ' + str(mt5.last_error()))
        continue
    info = mt5.account_info()
    if not info:
        print(acc['label'] + ': account_info() returned None')
        mt5.shutdown()
        continue
    positions = mt5.positions_get()
    open_count = len(positions) if positions else 0
    open_pnl = sum(p.profit for p in positions) if positions else 0.0
    print('--- ' + acc['label'] + ' ---')
    print('  Balance  : $' + str(round(info.balance, 2)))
    print('  Equity   : $' + str(round(info.equity, 2)))
    print('  Open PnL : $' + str(round(open_pnl, 2)) + '  (' + str(open_count) + ' open position/s)')
    print('  Margin   : $' + str(round(info.margin, 2)) + '  Free: $' + str(round(info.margin_free, 2)))
    mt5.shutdown()
