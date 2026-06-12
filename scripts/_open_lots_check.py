"""Check open positions and real lot sizes on GFT $5K and $10K accounts."""
import MetaTrader5 as mt5
import sys

ACCOUNTS = [
    {
        "label"   : "GFT $5K 2-Step",
        "login"   : 514129762,
        "password": "YV5Yf76B!u",
        "server"  : "GoatFunded-Server3",
        "terminal": "C:/CB6_MT5/MT5_GFT_5K/terminal64.exe",
    },
    {
        "label"   : "GFT $10K Instant",
        "login"   : 514294187,
        "password": "r3PqS0F5!i",
        "server"  : "GoatFunded-Server3",
        "terminal": "C:/CB6_MT5/MT5_GFT_10K/terminal64.exe",
    },
]

for acc in ACCOUNTS:
    print(f"\n{'='*50}")
    print(f"  {acc['label']}  (login={acc['login']})")
    print(f"{'='*50}")

    ok = mt5.initialize(
        path    = acc["terminal"],
        login   = acc["login"],
        password= acc["password"],
        server  = acc["server"],
    )
    if not ok:
        print(f"  MT5 init FAILED: {mt5.last_error()}")
        mt5.shutdown()
        continue

    ai = mt5.account_info()
    if ai:
        print(f"  Balance : ${ai.balance:,.2f}")
        print(f"  Equity  : ${ai.equity:,.2f}")
        print(f"  Float   : ${ai.profit:+,.2f}")

    positions = mt5.positions_get()
    pos_list  = list(positions) if positions else []
    print(f"  Open positions: {len(pos_list)}")

    if pos_list:
        for p in pos_list:
            side   = "BUY" if p.type == 0 else "SELL"
            sl_str = f"SL={p.sl:.5f}" if p.sl else "no SL"
            print(
                f"    {p.symbol:<12} {side}  lots={p.volume:.2f}"
                f"  open={p.price_open:.5f}  {sl_str}"
                f"  PnL=${p.profit:+.2f}"
            )
    else:
        print("    (no open trades)")

    mt5.shutdown()

print("\nDone.")
