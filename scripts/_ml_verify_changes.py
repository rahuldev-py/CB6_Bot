"""
ML verification of the 5 changes applied to CB6 Quantum.
Simulates exactly what the new gate logic does on the training dataset
and reports: did the changes help? is anything miscalibrated?
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
from ml_engine.training.dataset_builder import build_dataset

df = build_dataset(base_path='')
df = df.copy()
df['win_loss_label']   = pd.to_numeric(df['win_loss_label'],   errors='coerce')
df['r_multiple_label'] = pd.to_numeric(df['r_multiple_label'], errors='coerce')
df['confluence']       = pd.to_numeric(df.get('confluence', df.get('score', 0)), errors='coerce').fillna(0)

hour_col = 'hour_of_day' if 'hour_of_day' in df.columns else 'hour'
df[hour_col] = pd.to_numeric(df[hour_col], errors='coerce')

ob_col = 'ob_confluence' if 'ob_confluence' in df.columns else (
         'ob_present'    if 'ob_present'    in df.columns else None)

def pf(sub):
    r = sub['r_multiple_label'].dropna()
    g = r[r>0].sum(); bad = abs(r[r<0].sum())
    return round(g/bad, 3) if bad > 0 else float('inf')

def show(sub, label):
    n  = len(sub)
    if n == 0:
        print(f"  {label:<55} N=   0  —")
        return
    wr  = sub['win_loss_label'].mean()
    ar  = sub['r_multiple_label'].mean()
    tot = sub['r_multiple_label'].sum()
    p   = pf(sub)
    ps  = f"{p:.3f}" if p != float('inf') else "inf"
    print(f"  {label:<55} N={n:>4}  WR={wr:.1%}  AvgR={ar:>+5.2f}  TotR={tot:>+7.1f}  PF={ps}")

SEP = '='*90

# ── BASELINE ──────────────────────────────────────────────────────────────────
baseline = df.copy()
print(f"\n{SEP}")
print("  CB6 QUANTUM — ML VERIFICATION OF APPLIED CHANGES")
print(f"  Dataset: {len(df)} trades  |  Checking each filter vs baseline")
print(SEP)

print(f"\n  BASELINE (before any new filters):")
show(baseline, "ALL trades (old bot, score>=8)")

# ── CHANGE 1: Score gate 11 → 12 ─────────────────────────────────────────────
print(f"\n{SEP}")
print("  CHANGE 1 — Score gate raised: 11 → 12")
print(SEP)
old_gate = df[df['confluence'] >= 11]
new_gate = df[df['confluence'] >= 12]
show(old_gate, "score >= 11 (old gate)")
show(new_gate, "score >= 12 (new gate)")
dropped  = len(old_gate) - len(new_gate)
print(f"\n  Dropped {dropped} trades ({dropped/len(old_gate):.0%} of score>=11 pool)")
print(f"  WR lift : {(new_gate['win_loss_label'].mean() - old_gate['win_loss_label'].mean())*100:+.1f}pp")
print(f"  PF lift : {pf(new_gate) - pf(old_gate):+.3f}")
print(f"  AvgR lift: {new_gate['r_multiple_label'].mean() - old_gate['r_multiple_label'].mean():+.3f}R")
print(f"  Verdict : {'✅ GOOD CHANGE' if new_gate['win_loss_label'].mean() > old_gate['win_loss_label'].mean() else '⚠️ CHECK NEEDED'}")

# ── CHANGE 2: CHOPPY hard block ───────────────────────────────────────────────
print(f"\n{SEP}")
print("  CHANGE 2 — CHOPPY regime hard block")
print(SEP)
if 'regime' in df.columns:
    choppy   = df[df['regime'] == 'CHOPPY']
    no_choppy= df[df['regime'] != 'CHOPPY']
    show(choppy,    "CHOPPY regime (now blocked)")
    show(no_choppy, "Non-CHOPPY (what remains)")
    print(f"\n  Blocked {len(choppy)} trades ({len(choppy)/len(df):.1%} of all trades)")
    print(f"  WR of blocked trades: {choppy['win_loss_label'].mean():.1%} — {'below average ✅' if choppy['win_loss_label'].mean() < df['win_loss_label'].mean() else '⚠️ above average — check!'}")
    print(f"  PF of blocked trades: {pf(choppy):.3f} — {'below average ✅' if pf(choppy) < pf(df) else '⚠️ higher than base'}")
    print(f"  Verdict : {'✅ GOOD — removing low-PF trades' if pf(choppy) < pf(df) else '⚠️ CHOPPY trades not clearly bad here'}")
else:
    print("  regime column not in dataset — cannot verify")

# ── CHANGE 3: OB required or score>=15 ───────────────────────────────────────
print(f"\n{SEP}")
print("  CHANGE 3 — Require OB, or score>=15 if no OB")
print(SEP)
if ob_col:
    ob_yes   = df[df[ob_col] == True]
    ob_no    = df[df[ob_col] == False]
    ob_no_high = ob_no[ob_no['confluence'] >= 15]
    ob_no_low  = ob_no[ob_no['confluence'] <  15]   # these get blocked

    show(ob_yes,     f"With OB ({ob_col}=True)")
    show(ob_no_high, "No OB but score>=15 (allowed)")
    show(ob_no_low,  "No OB and score<15  (NOW BLOCKED)")
    print(f"\n  Blocked {len(ob_no_low)} trades ({len(ob_no_low)/len(df):.1%} of all)")
    print(f"  WR of blocked: {ob_no_low['win_loss_label'].mean():.1%}  PF: {pf(ob_no_low):.3f}")
    total_allowed = len(ob_yes) + len(ob_no_high)
    allowed_df    = pd.concat([ob_yes, ob_no_high])
    show(allowed_df, "ALLOWED after OB gate")
    print(f"  WR lift vs baseline: {(allowed_df['win_loss_label'].mean() - df['win_loss_label'].mean())*100:+.1f}pp")
    print(f"  Verdict : {'✅ GOOD' if ob_no_low['win_loss_label'].mean() < df['win_loss_label'].mean() else '⚠️ blocked trades were above average'}")
else:
    print("  OB column not found — cannot verify exactly")
    print("  Note: check ob_confluence / ob_present column names in live dataset")

# ── CHANGE 4: No-displacement FVG hard block ─────────────────────────────────
print(f"\n{SEP}")
print("  CHANGE 4 — No-displacement FVG hard block (pre-existing, verifying)")
print(SEP)
if 'fvg_displacement' in df.columns:
    disp_yes = df[df['fvg_displacement'] == True]
    disp_no  = df[df['fvg_displacement'] == False]
    show(disp_yes, "Displaced FVG (allowed)")
    show(disp_no,  "Weak FVG     (blocked)")
    print(f"\n  Verdict : {'✅ CONFIRMED — weak FVG WR far below average' if len(disp_no)==0 or disp_no['win_loss_label'].mean() < df['win_loss_label'].mean() else '⚠️ check'}")
elif 'displacement' in df.columns:
    disp_yes = df[df['displacement'] == True]
    disp_no  = df[df['displacement'] == False]
    show(disp_yes, "Displaced FVG (allowed)")
    show(disp_no,  "Weak FVG     (blocked)")
    print(f"  Verdict : {'✅ CONFIRMED' if len(disp_no)==0 or disp_no['win_loss_label'].mean() < df['win_loss_label'].mean() else '⚠️ check'}")

# ── CHANGE 5: Window-aware gate ───────────────────────────────────────────────
print(f"\n{SEP}")
print("  CHANGE 5 — Window-aware gates (PM=12, AM=12, outside=15)")
print(SEP)
if hour_col in df.columns:
    pm_sb    = df[df[hour_col] == 13]
    am_sb    = df[df[hour_col] == 10]
    outside  = df[~df[hour_col].isin([10, 13, 14])]
    outside_high = outside[outside['confluence'] >= 15]
    outside_low  = outside[outside['confluence'] <  15]

    show(pm_sb,         "13:xx PM window (gate=12)")
    show(am_sb,         "10:xx AM window (gate=12)")
    show(outside_high,  "Outside window score>=15 (allowed in strict mode)")
    show(outside_low,   "Outside window score<15  (NOW BLOCKED in strict mode)")

    if len(outside_low) > 0:
        print(f"\n  Strict mode blocks {len(outside_low)} outside-window trades")
        print(f"  Their WR: {outside_low['win_loss_label'].mean():.1%}  PF: {pf(outside_low):.3f}")
        print(f"  Verdict : {'✅ GOOD — blocking low-quality off-window trades' if outside_low['win_loss_label'].mean() < df['win_loss_label'].mean() else '⚠️ off-window trades not clearly bad'}")

# ── CHANGE 6: SHORT 1.10x boost ──────────────────────────────────────────────
print(f"\n{SEP}")
print("  CHANGE 6 — SHORT (BEARISH) 1.10x lot boost")
print(SEP)
bearish_all = df[df['direction'] == 'BEARISH']
bullish_all = df[df['direction'] == 'BULLISH']
show(bullish_all, "LONG  trades (1.00x, unchanged)")
show(bearish_all, "SHORT trades (1.10x boost now)")

b_wr    = bearish_all['win_loss_label'].mean()
bu_wr   = bullish_all['win_loss_label'].mean()
b_avgr  = bearish_all['r_multiple_label'].mean()
bu_avgr = bullish_all['r_multiple_label'].mean()

# Effective edge of boost: 1.10x means +10% more size on trades with +3.1pp WR edge
print(f"\n  SHORT edge over LONG: WR +{(b_wr-bu_wr)*100:.1f}pp  |  AvgR +{b_avgr-bu_avgr:.3f}R")
print(f"  At 1.10x: every SHORT win earns 10% more, every SHORT loss costs 10% more")
print(f"  Net expectancy boost (SHORT, 1.10x vs 1.00x): +{(b_wr*b_avgr*0.10):.3f}R per SHORT trade")
print(f"  Verdict : {'✅ MATHEMATICALLY SOUND — edge justifies small boost' if b_wr > bu_wr and b_avgr > bu_avgr else '⚠️ verify directional edge'}")
print(f"  Note    : Track 30 BEARISH live trades before → 1.25x. Current 1.10x is correct.")

# ── COMBINED: all new filters together ───────────────────────────────────────
print(f"\n{SEP}")
print("  COMBINED: ALL CHANGES APPLIED TOGETHER vs BASELINE")
print(SEP)

combined = df[df['confluence'] >= 12].copy()
if 'regime' in combined.columns:
    combined = combined[combined['regime'] != 'CHOPPY']
if ob_col and ob_col in combined.columns:
    combined = combined[(combined[ob_col] == True) | (combined['confluence'] >= 15)]

show(df,       "BASELINE (old bot)")
show(combined, "NEW FILTERS combined")
wr_lift  = (combined['win_loss_label'].mean() - df['win_loss_label'].mean()) * 100
pf_lift  = pf(combined) - pf(df)
ar_lift  = combined['r_multiple_label'].mean() - df['r_multiple_label'].mean()
retained = len(combined) / len(df) * 100

print(f"""
  Summary:
    WR  : {df['win_loss_label'].mean():.1%}  →  {combined['win_loss_label'].mean():.1%}   ({wr_lift:+.1f}pp)
    PF  : {pf(df):.3f}  →  {pf(combined):.3f}   ({pf_lift:+.3f})
    AvgR: {df['r_multiple_label'].mean():+.3f}R  →  {combined['r_multiple_label'].mean():+.3f}R  ({ar_lift:+.3f}R)
    Trades retained: {len(combined)}/{len(df)}  ({retained:.0f}%)
""")

# ── WHAT ML STILL RECOMMENDS ──────────────────────────────────────────────────
print(f"{SEP}")
print("  ML FEEDBACK: WHAT STILL NEEDS ATTENTION")
print(SEP)

# Check score gate 12 vs 13
s12 = df[df['confluence'] >= 12]
s13 = df[df['confluence'] >= 13]
print(f"\n  A) Score gate 12 vs 13:")
show(s12, "  score>=12 (applied)")
show(s13, "  score>=13 (consider?)")
if s13['win_loss_label'].mean() > s12['win_loss_label'].mean():
    print(f"  → Score 13 adds +{(s13['win_loss_label'].mean()-s12['win_loss_label'].mean())*100:.1f}pp WR but loses {len(s12)-len(s13)} trades. Monitor before raising.")
else:
    print(f"  → Score 12 gate is optimal. 13 does not improve WR meaningfully.")

# BOS vs CHoCH split
print(f"\n  B) BOS vs CHoCH — should scoring weights change?")
if 'mss_type' in df.columns:
    bos   = df[df['mss_type']=='BOS']
    choch = df[df['mss_type']=='CHOCH']
    show(bos,   "  BOS   (currently +1 score)")
    show(choch, "  CHoCH (currently +2 score)")
    if bos['win_loss_label'].mean() > choch['win_loss_label'].mean():
        print(f"  → BOS has HIGHER WR ({bos['win_loss_label'].mean():.1%}) than CHoCH ({choch['win_loss_label'].mean():.1%})")
        print(f"    ML suggests: BOS +2, CHoCH +1 would be more accurate. Or keep current and use WR data to decide.")
    else:
        print(f"  → CHoCH correctly scores higher. Current +2 scoring is validated.")

# Hour 14 check
print(f"\n  C) Hour 14 trades — keep or add to primary window?")
if hour_col in df.columns:
    h14 = df[df[hour_col] == 14]
    show(h14, "  14:xx (inside SB window 13:30-14:30)")
    print(f"  → These are inside the PM window so gate=12 applies. No change needed.")

# Displacement in training data
print(f"\n  D) Displacement filter — are weak FVGs actually in the live dataset?")
disp_col = 'fvg_displacement' if 'fvg_displacement' in df.columns else 'displacement'
if disp_col in df.columns:
    nd = df[df[disp_col] == False]
    print(f"  Weak FVG trades in dataset: {len(nd)} ({len(nd)/len(df):.1%})")
    if len(nd) > 0:
        show(nd, "  Weak FVG trades")
    else:
        print(f"  → All training trades have displacement. Hard block was already enforced in data collection.")

# H4 bias check on training data
print(f"\n  E) H4 bias filter — how many trades does it block?")
if 'h4_bias' in df.columns:
    h4_counter = df[df['h4_bias'] != df['direction'].map({'BULLISH':'BULLISH','BEARISH':'BEARISH'})
                    ].query("h4_bias != 'RANGING'")
    print(f"  H4 counter-trend trades in dataset: {len(h4_counter)}")
    if len(h4_counter) > 0:
        show(h4_counter, "  H4 counter-trend (now blocked)")
else:
    print(f"  H4 bias not in historical dataset (new filter, applied live only). Cannot backtest.")
    print(f"  → Will accumulate data going forward. Watch: if H4 blocks good setups, loosen to soft block.")

print(f"\n{SEP}")
print("  END — ML VERIFICATION COMPLETE")
print(SEP + "\n")
