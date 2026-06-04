import sys, json
from collections import defaultdict
sys.path.insert(0, '.')

with open('data/paper_state.json') as f:
    s = json.load(f)

trades = s.get('closed_trades', [])
today  = [t for t in trades if '2026-05-18' in str(t.get('exit_time', ''))]

wins   = [t for t in today if t.get('pnl', 0) > 0]
losses = [t for t in today if t.get('pnl', 0) <= 0]
total_pnl = sum(t.get('pnl', 0) for t in today)

avg_profit = sum(t['pnl'] for t in wins)   / len(wins)   if wins   else 0
avg_loss   = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
avg_rr     = round(abs(avg_profit / avg_loss), 2) if avg_loss else 0

max_profit_trade = max(today, key=lambda t: t.get('pnl', 0))
max_loss_trade   = min(today, key=lambda t: t.get('pnl', 0))

index_pnl    = defaultdict(float)
index_trades = defaultdict(int)
index_wins   = defaultdict(int)

for t in today:
    sym = t.get('symbol', '').replace('NSE:', '').replace('26MAYFUT', '')
    index_pnl[sym]    += t.get('pnl', 0)
    index_trades[sym] += 1
    if t.get('pnl', 0) > 0:
        index_wins[sym] += 1

best_index  = max(index_pnl, key=index_pnl.get)
worst_index = min(index_pnl, key=index_pnl.get)

lines = []
lines.append("")
lines.append("=" * 55)
lines.append("   CB6 QUANTUM - NSE SESSION REPORT  (18 May 2026)")
lines.append("=" * 55)
lines.append("")
lines.append("OVERVIEW")
lines.append(f"  Total Trades   : {len(today)}")
lines.append(f"  Wins           : {len(wins)}   Losses : {len(losses)}")
lines.append(f"  Win Rate       : {round(len(wins)/len(today)*100, 1)}%")
lines.append(f"  Total PnL      : Rs {round(total_pnl, 2):,}")
lines.append(f"  Return         : {round(total_pnl/200000*100, 2)}%  (on Rs 2,00,000)")
lines.append("")
lines.append("TRADE QUALITY")
lines.append(f"  Avg Profit     : Rs {round(avg_profit, 2):,}")
lines.append(f"  Avg Loss       : Rs {round(avg_loss, 2):,}")
lines.append(f"  Avg RR         : 1:{avg_rr}")
mp_sym = max_profit_trade['symbol'].replace('NSE:','').replace('26MAYFUT','')
ml_sym = max_loss_trade['symbol'].replace('NSE:','').replace('26MAYFUT','')
lines.append(f"  Max Profit     : Rs {round(max_profit_trade['pnl'],2):,}  ({mp_sym} {max_profit_trade['direction']})")
lines.append(f"  Max Loss       : Rs {round(max_loss_trade['pnl'],2):,}  ({ml_sym} {max_loss_trade['direction']})")
lines.append("")
lines.append("INDEX BREAKDOWN")
lines.append(f"  {'Index':<15} {'Trades':>6} {'Wins':>5} {'WR%':>6} {'PnL (Rs)':>12}  Note")
lines.append(f"  {'-'*15} {'-'*6} {'-'*5} {'-'*6} {'-'*12}  {'-'*10}")
for idx in sorted(index_pnl, key=index_pnl.get, reverse=True):
    tr  = index_trades[idx]
    wr  = round(index_wins[idx] / tr * 100, 1)
    pn  = round(index_pnl[idx], 2)
    tag = '<-- MAX PROFIT' if idx == best_index else ('<-- MIN' if idx == worst_index else '')
    lines.append(f"  {idx:<15} {tr:>6} {index_wins[idx]:>5} {wr:>5}% {pn:>12,}  {tag}")
lines.append("")
lines.append("TRADE LOG")
lines.append(f"  {'#':<3} {'Index':<12} {'Dir':<8} {'Entry':>10} {'Exit':>10} {'PnL':>10}  Result")
lines.append(f"  {'-'*3} {'-'*12} {'-'*8} {'-'*10} {'-'*10} {'-'*10}  {'-'*15}")
for i, t in enumerate(today, 1):
    sym    = t.get('symbol', '').replace('NSE:', '').replace('26MAYFUT', '')
    d      = t.get('direction', '')
    entry  = t.get('entry_price', 0)
    exit_p = t.get('exit_price', 'N/A')
    pnl    = round(t.get('pnl', 0), 2)
    status = t.get('status', '')
    result = 'WIN' if pnl > 0 else 'LOSS'
    lines.append(f"  {i:<3} {sym:<12} {d:<8} {entry:>10} {str(exit_p):>10} {pnl:>10,}  {result} ({status})")
lines.append("")
lines.append("=" * 55)
lines.append("  Mode           : PAPER TRADING")
lines.append("  Next Session   : Tomorrow 9:15 AM IST")
lines.append("=" * 55)
lines.append("")

print('\n'.join(lines))
