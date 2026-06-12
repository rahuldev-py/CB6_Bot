import json

d = json.load(open('c:/cb6_bot/data/gft_5k/state.json', encoding='utf-8'))
print('daily_trades:', d.get('daily_trades'))
print('daily_loss:  ', d.get('daily_loss'))
print('risk_mode:   ', d.get('risk_mode'))
print('total_pnl:   ', d.get('total_pnl'))

today = [t for t in d.get('closed_trades', []) if '2026-06-12' in t.get('entry_time', '')]
print(f'\nToday trades ({len(today)}):')
for t in today:
    print(f"  {t['entry_time']} | {t['symbol']} {t['direction']} {t['lots']}L | pnl=${t['pnl_usd']} | {t['exit_reason']}")
print('Today total PnL: $' + str(round(sum(t['pnl_usd'] for t in today), 2)))
