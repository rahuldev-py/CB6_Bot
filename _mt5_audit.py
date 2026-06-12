import MetaTrader5 as mt5
from datetime import datetime

# Check account 514129762 (5K) directly with credentials — look for today's XAGUSD
print("=== Checking 514129762 ($5K) with credentials ===")
mt5.initialize(login=514129762, password='YV5Yf76B!u', server='GoatFunded-Server3')
info = mt5.account_info()
print(f"Login: {info.login} | Balance: ${info.balance:.2f} | Equity: ${info.equity:.2f}")
deals = mt5.history_deals_get(datetime(2026, 6, 12), datetime.now())
if deals:
    real = [d for d in deals if d.type in (0,1)]
    print(f"Deals today: {len(real)}")
    for d in sorted(real, key=lambda d: d.time):
        t = datetime.fromtimestamp(d.time).strftime('%H:%M:%S')
        side = 'BUY' if d.type==0 else 'SELL'
        etype = 'open' if d.entry==0 else 'close'
        print(f"  {t} | ticket={d.ticket} | {d.symbol} {side} {etype} {d.volume}L | profit=${d.profit:.2f}")
else:
    print(f"No deals today: {mt5.last_error()}")
mt5.shutdown()

print()
print("=== Checking 514294187 ($10K) — looking for XAGUSD today ===")
mt5.initialize(login=514294187, password='r3PqS0F5!i', server='GoatFunded-Server3')
info = mt5.account_info()
print(f"Login: {info.login} | Balance: ${info.balance:.2f} | Equity: ${info.equity:.2f}")
deals = mt5.history_deals_get(datetime(2026, 6, 12), datetime.now())
if deals:
    real = [d for d in deals if d.type in (0,1)]
    print(f"Deals today: {len(real)}")
    for d in sorted(real, key=lambda d: d.time):
        t = datetime.fromtimestamp(d.time).strftime('%H:%M:%S')
        side = 'BUY' if d.type==0 else 'SELL'
        etype = 'open' if d.entry==0 else 'close'
        print(f"  {t} | ticket={d.ticket} | {d.symbol} {side} {etype} {d.volume}L | profit=${d.profit:.2f}")
else:
    print(f"No deals today: {mt5.last_error()}")
mt5.shutdown()
