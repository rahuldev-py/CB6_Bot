import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import dotenv_values
from fyers_apiv3 import fyersModel
from datetime import datetime, timezone, timedelta

IST = timedelta(hours=5, minutes=30)
ist = datetime.now(timezone.utc) + IST

env   = dotenv_values(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
fyers = fyersModel.FyersModel(client_id=env.get('CLIENT_ID',''), token=env.get('ACCESS_TOKEN',''), is_async=False, log_path='')

SYMS = 'NSE:NIFTY26JUNFUT,NSE:BANKNIFTY26JUNFUT,NSE:FINNIFTY26JUNFUT'
resp = fyers.quotes({'symbols': SYMS})

if resp.get('s') != 'ok' or not resp.get('d'):
    print(f"Fyers error: {resp.get('message', resp.get('s', 'unknown'))} — token may be expired, run: python auto_token.py")
    sys.exit(1)

h, m = ist.hour, ist.minute
sb = ""
if 10 <= h < 11: sb = "  *** SB-1 ACTIVE ***"
elif h == 13: sb = "  *** SB-2 ACTIVE (13:00-14:00) ***"
elif h == 15 and m < 30: sb = "  *** SB-3 ACTIVE ***"
elif h > 15 or (h == 15 and m >= 30): sb = "  [market closed]"

print(f"[{ist.strftime('%H:%M:%S')} IST]{sb}")
print(f"  {'INDEX':<26} {'LTP':>10}  {'CHANGE':>12}  {'HIGH':>10}  {'LOW':>10}")
print(f"  {'-'*72}")
for item in resp.get('d', []):
    v   = item.get('v', {})
    sym = item.get('n','').replace('NSE:','').replace('26JUNFUT','')
    ltp = v.get('lp', 0)
    chg = v.get('ch', 0)
    chp = v.get('chp', 0)
    hi  = v.get('high_price', 0)
    lo  = v.get('low_price', 0)
    print(f"  {sym:<26} {ltp:>10.2f}  {chg:>+10.2f} ({chp:>+5.2f}%)  {hi:>10.2f}  {lo:>10.2f}")
