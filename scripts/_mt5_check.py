import os, sys
sys.path.insert(0, '.')
from dotenv import dotenv_values
_env = dotenv_values('.env')
for k, v in _env.items():
    if k not in os.environ:
        os.environ[k] = v

import MetaTrader5 as mt5
from datetime import datetime, timedelta

login    = int(os.getenv('MT5_LOGIN', '0'))
password = os.getenv('MT5_PASSWORD', '')
server   = os.getenv('MT5_SERVER', '')

ok = mt5.initialize(login=login, password=password, server=server)
print(f"MT5 init: {ok}  error={mt5.last_error()}")

if ok:
    info = mt5.account_info()
    print(f"\nAccount: {info.login}  Balance: ${info.balance:.2f}  Equity: ${info.equity:.2f}  Profit: ${info.profit:.2f}")
    print(f"Server: {info.server}")

    now   = datetime.now()
    since = now - timedelta(days=3)
    deals = mt5.history_deals_get(since, now)

    if deals:
        print(f"\n=== DEAL HISTORY ({len(deals)} deals) ===")
        total_profit = 0.0
        for d in deals:
            t = datetime.fromtimestamp(d.time)
            entry_str = {0: 'IN', 1: 'OUT', 2: 'INOUT'}.get(d.entry, str(d.entry))
            type_str  = {0: 'BUY', 1: 'SELL'}.get(d.type, str(d.type))
            print(f"  {t.strftime('%m-%d %H:%M')} | {d.symbol:8s} | {type_str:4s} | {entry_str:5s} | vol={d.volume:.2f} | price={d.price:.3f} | profit=${d.profit:.2f} | commission=${d.commission:.2f} | swap=${d.swap:.2f}")
            if d.entry == 1:  # OUT deals contribute profit
                total_profit += d.profit
        print(f"\nTotal realized profit (OUT deals): ${total_profit:.2f}")
    else:
        print("No deals found")

    # Open positions
    positions = mt5.positions_get()
    if positions:
        print(f"\n=== OPEN POSITIONS ({len(positions)}) ===")
        for p in positions:
            t = datetime.fromtimestamp(p.time)
            ptype = 'LONG' if p.type == 0 else 'SHORT'
            print(f"  #{p.ticket} {p.symbol:8s} {ptype} | vol={p.volume:.2f} | open={p.price_open:.3f} | cur={p.price_current:.3f} | sl={p.sl:.3f} | tp={p.tp:.3f} | profit=${p.profit:.2f}")
    else:
        print("\nNo open positions")

    mt5.shutdown()
