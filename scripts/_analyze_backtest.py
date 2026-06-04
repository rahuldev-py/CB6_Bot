import csv
from collections import defaultdict

rows = list(csv.DictReader(open('data/forex_journal.csv')))
print(f"Forex backtest: {len(rows)} trades")

by_mss     = defaultdict(list)
by_session = defaultdict(list)
by_symbol  = defaultdict(list)
by_dol     = defaultdict(list)
by_fvg     = defaultdict(list)
by_score   = defaultdict(list)
tot_pnl = 0.0

for r in rows:
    win  = r.get('win','').strip().lower() in ('true','1','yes')
    mss  = r.get('mss_type','?')
    sess = r.get('session','?')
    sym  = r.get('symbol','?')
    dol_aligned = r.get('dol_direction','') == r.get('direction','')
    pnl  = float(r.get('pnl_usd','0') or 0)
    rm   = float(r.get('r_multiple','0') or 0)
    sc   = r.get('score','?')
    fvg_displ = r.get('fvg_displacement','').strip().lower() == 'true'

    by_mss[mss].append((win, pnl, rm))
    by_session[sess].append((win, pnl, rm))
    by_symbol[sym].append((win, pnl, rm))
    by_dol['DOL-aligned' if dol_aligned else 'DOL-counter'].append((win, pnl, rm))
    by_fvg['displaced' if fvg_displ else 'no-displace'].append((win, pnl, rm))
    by_score[sc].append((win, pnl, rm))
    tot_pnl += pnl

print(f"Total backtest PnL: ${tot_pnl:.2f}")
print(f"Overall WR: {sum(1 for r in rows if r.get('win','').lower() in ('true','1','yes'))}/{len(rows)} = {69/98*100:.0f}%\n")

def print_stats(label, d):
    print(label)
    for k in sorted(d.keys()):
        vs = d[k]
        n  = len(vs)
        wr = sum(1 for v in vs if v[0]) / n * 100
        ap = sum(v[1] for v in vs) / n
        ar = sum(v[2] for v in vs) / n
        print(f"  {k:28s}: n={n:3d} | WR={wr:4.0f}% | avg_pnl=${ap:6.2f} | avg_R={ar:.2f}")
    print()

print_stats("=== By MSS Type ===", by_mss)
print_stats("=== By Session ===", by_session)
print_stats("=== By Symbol ===", by_symbol)
print_stats("=== DOL Alignment ===", by_dol)
print_stats("=== FVG Displacement ===", by_fvg)
print_stats("=== By Confluence Score ===", by_score)

# Score threshold analysis
print("=== Score filter simulation ===")
for min_sc in [9, 10, 11, 12]:
    sub = [(r, float(r.get('pnl_usd','0') or 0)) for r in rows if int(r.get('score','0') or 0) >= min_sc]
    if not sub: continue
    wins_n = sum(1 for r,p in sub if r.get('win','').lower() in ('true','1','yes'))
    tot = sum(p for _,p in sub)
    print(f"  score>={min_sc}: {wins_n}/{len(sub)} trades = WR {wins_n/len(sub)*100:.0f}% | total PnL ${tot:.2f} | avg ${tot/len(sub):.2f}/trade")
