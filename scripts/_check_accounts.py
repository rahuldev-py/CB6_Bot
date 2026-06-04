"""Quick account stats checker for FTMO MT5."""
import MetaTrader5 as mt5, os, sys
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
env = dotenv_values(ROOT / ".env")
for k, v in env.items():
    if k not in os.environ:
        os.environ[k] = v

mt5.initialize()
ai = mt5.account_info()
print("=== FTMO ACCOUNT ===")
print(f"Account : {ai.login}")
print(f"Balance : ${ai.balance:,.2f}")
print(f"Equity  : ${ai.equity:,.2f}")
print(f"Float P : ${ai.profit:+,.2f}")
print(f"Free Mgn: ${ai.margin_free:,.2f}")
print(f"Leverage: 1:{ai.leverage}")
print(f"Broker  : {ai.company}")

positions = mt5.positions_get()
pos_list = list(positions) if positions else []
print(f"\nOpen Positions: {len(pos_list)}")
for p in pos_list:
    side = "BUY" if p.type == 0 else "SELL"
    print(f"  {p.symbol:<12} {side}  vol={p.volume}  open={p.price_open:.5f}  PnL=${p.profit:+.2f}")

import datetime
since = datetime.datetime(2026, 1, 1)
until = datetime.datetime.now()
hist = mt5.history_deals_get(since, until)
if hist:
    import pandas as pd
    df = pd.DataFrame([d._asdict() for d in hist])
    exits = df[df["entry"] == 1].copy()   # entry==1 means closing deal
    if not exits.empty:
        wins = exits[exits["profit"] > 0]
        losses = exits[exits["profit"] < 0]
        net = round(exits["profit"].sum(), 2)
        wr = round(len(wins) / len(exits) * 100, 1)
        print(f"\nClosed trades (2026): {len(exits)}")
        print(f"Win Rate            : {wr}%  ({len(wins)}W / {len(losses)}L)")
        print(f"Net PnL             : ${net:+,.2f}")
        print(f"Best trade          : ${exits['profit'].max():+,.2f}")
        print(f"Worst trade         : ${exits['profit'].min():+,.2f}")
        by_sym = exits.groupby("symbol")["profit"].sum().sort_values(ascending=False)
        print("\nBy symbol:")
        for sym, pnl in by_sym.items():
            print(f"  {sym:<14} ${pnl:+,.2f}")
else:
    print("\nNo closed trades found in history.")

mt5.shutdown()
