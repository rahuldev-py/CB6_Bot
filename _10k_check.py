import json

for fname, label in [('gft_10k', 'GFT 10K'), ('gft_1k_instant', 'GFT 1K')]:
    d = json.load(open(f'c:/cb6_bot/data/{fname}/state.json', encoding='utf-8'))
    print(f'\n=== {label} ===')
    print(f'  daily_trades: {d.get("daily_trades")}')
    print(f'  risk_mode: {d.get("risk_mode")}')
    all_trades = d.get('closed_trades', []) + d.get('open_trades', [])
    today = [t for t in all_trades if '2026-06-12' in str(t.get('entry_time', ''))]
    print(f'  Today trades ({len(today)}):')
    for t in today:
        pnl = t.get('pnl_usd', 'open')
        reason = t.get('exit_reason', 'OPEN')
        print(f'    {t["entry_time"]} | {t["symbol"]} {t["direction"]} {t["lots"]}L | pnl=${pnl} | {reason} | ticket={t.get("ticket")}')
