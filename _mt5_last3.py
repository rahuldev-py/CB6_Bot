import MetaTrader5 as mt5
from datetime import datetime

accounts = [
    ('GFT $5K 2-Step',   514129762, 'YV5Yf76B!u', 'GoatFunded-Server3'),
    ('GFT $1K Instant',  314983765, 'Ut514@V3m0', 'GoatFunded-Server'),
    ('GFT $10K Instant', 514294187, 'r3PqS0F5!i', 'GoatFunded-Server3'),
]

for name, login, password, server in accounts:
    print(f'\n=== {name} (#{login}) ===')
    if not mt5.initialize(login=login, password=password, server=server):
        print(f'  INIT FAILED: {mt5.last_error()}')
        continue
    info = mt5.account_info()
    if info:
        print(f'  Balance: ${info.balance:.2f} | Equity: ${info.equity:.2f} | Floating P&L: ${info.profit:.2f}')
    deals = mt5.history_deals_get(datetime(2026, 1, 1), datetime.now())
    if deals:
        closed = [d for d in deals if d.type in (0, 1) and d.entry == 1]
        last3 = sorted(closed, key=lambda d: d.time)[-3:]
        if last3:
            for d in last3:
                t = datetime.fromtimestamp(d.time).strftime('%Y-%m-%d %H:%M')
                side = 'BUY' if d.type == 0 else 'SELL'
                print(f'  {t} | {d.symbol:8s} {side} {d.volume}L | exit={d.price:.5f} | profit=${d.profit:.2f} | {d.comment}')
        else:
            print('  No closed trades found')
    else:
        print(f'  No deals history: {mt5.last_error()}')
    mt5.shutdown()
