import MetaTrader5 as mt5
from datetime import datetime, timezone, timedelta
IST = timedelta(hours=5, minutes=30)
mt5.initialize()
mt5.symbol_select('XAUUSD.x', True)
tick = mt5.symbol_info_tick('XAUUSD.x')
if tick:
    ist = datetime.now(timezone.utc) + IST
    mid = (tick.bid + tick.ask) / 2
    print(f"GOLD  Bid:{tick.bid:.2f}  Ask:{tick.ask:.2f}  Mid:{mid:.2f}  [{ist.strftime('%H:%M:%S')} IST]")
mt5.shutdown()
