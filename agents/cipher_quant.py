"""
CIPHER — CB6 Quant Analyst
Reads ALL 4 CB6 accounts: NSE Fyers, GFT $5K, GFT $1K, FTMO.
Produces real statistical analysis with specific recommendations per account.
Read-only. Never modifies anything.
"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from agents.config import call_agent, safe_parse, REPORTS_DIR, CB6_ROOT

SYSTEM = """You are CIPHER, the Quant Analyst of CB6 Quantum.
You manage 4 accounts: NSE Fyers (₹26K real), GFT $1K Instant (real funded), GFT $5K 2-Step (prop challenge), FTMO $10K (prop challenge).

Priority order: GFT $1K (real money first) > NSE (real money) > GFT $5K (prop) > FTMO (prop).

Give SPECIFIC recommendations per account. Use real numbers. Reference actual symbols and sessions.
Flag any H4 bias violations — they are hard rule violations.

Return JSON only:
{
  "summary": "specific summary with real numbers for all 4 accounts",
  "win_rate_overall": 0.0,
  "win_rate_by_symbol": {},
  "win_rate_by_session": {},
  "avg_rr_by_symbol": {},
  "direction_edge": {},
  "account_status": {
    "nse_fyers": {"capital_inr": 0, "open_trades": 0, "status": "", "actions": []},
    "gft_1k_instant": {"capital": 0, "daily_pnl": 0, "trades_today": 0, "status": "", "actions": []},
    "gft_5k_2step": {"capital": 0, "pnl": 0, "phase": "phase_1", "phase_target": 400, "progress_pct": 0, "status": "", "actions": [], "h4_violations": 0},
    "ftmo_10k": {"capital": 0, "pnl": 0, "target": 500, "deadline": "", "status": "", "actions": []}
  },
  "symbols_to_disable": [],
  "symbols_to_prioritize": [],
  "session_recommendations": [],
  "h4_violations_found": [],
  "ml_retrain_needed": false,
  "ml_retrain_reason": "",
  "specific_actions": [],
  "alerts": [],
  "key_insights": []
}"""


def _load_forex_journal() -> dict:
    path = CB6_ROOT / 'data' / 'forex_journal.csv'
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
        stats = {"total_trades": len(df), "win_rate": round(df['win'].mean(), 4) if 'win' in df.columns else None,
                 "by_symbol": {}, "by_session": {}, "by_direction": {}, "avg_rr": {}}
        if 'symbol' in df.columns and 'win' in df.columns:
            for sym, grp in df.groupby('symbol'):
                stats["by_symbol"][sym] = {"count": len(grp), "win_rate": round(grp['win'].mean(), 4)}
                if 'r_multiple' in df.columns:
                    stats["avg_rr"][sym] = round(grp['r_multiple'].mean(), 3)
        if 'session' in df.columns and 'win' in df.columns:
            for sess, grp in df.groupby('session'):
                stats["by_session"][sess] = {"count": len(grp), "win_rate": round(grp['win'].mean(), 4)}
        if 'direction' in df.columns and 'symbol' in df.columns and 'win' in df.columns:
            for (sym, dire), grp in df.groupby(['symbol', 'direction']):
                stats["by_direction"][f"{sym}_{dire}"] = {"count": len(grp), "win_rate": round(grp['win'].mean(), 4)}
        return stats
    except Exception as e:
        return {"error": str(e)}


def _load_nse_journal() -> dict:
    path = CB6_ROOT / 'data' / 'trade_journal.csv'
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
        open_trades = df[df['exit_price'].isna()] if 'exit_price' in df.columns else df
        closed = df[df['exit_price'].notna()] if 'exit_price' in df.columns else pd.DataFrame()
        by_symbol = {}
        if 'underlying' in df.columns:
            for sym, grp in df.groupby('underlying'):
                by_symbol[sym] = {"count": len(grp), "open": len(grp[grp['exit_price'].isna()]) if 'exit_price' in grp.columns else len(grp)}
        return {
            "total_entries": len(df),
            "open_trades": len(open_trades),
            "closed_trades": len(closed),
            "by_symbol": by_symbol,
            "columns": list(df.columns),
            "note": "NSE journal has exits missing — exit tracking may be broken",
        }
    except Exception as e:
        return {"error": str(e)}


def _load_bot_memory() -> dict:
    path = CB6_ROOT / 'data' / 'bot_memory.json'
    if not path.exists():
        return {}
    try:
        bm = json.loads(path.read_text(encoding='utf-8'))
        return {
            "total_trades": bm.get('total_trades', 0),
            "winning": bm.get('winning_trades', 0),
            "losing": bm.get('losing_trades', 0),
            "trade_history_count": len(bm.get('trade_history', [])),
        }
    except Exception:
        return {}


def _load_state(path: str) -> dict:
    p = CB6_ROOT / path
    if not p.exists():
        return {}
    try:
        s = json.loads(p.read_text(encoding='utf-8'))
        closed = s.get('closed_trades', [])
        h4_violations = []
        for t in closed:
            reason = t.get('entry_reason', '')
            direction = t.get('direction', '')
            if 'H4=BULLISH' in reason and direction == 'BEARISH':
                h4_violations.append(f"{t.get('symbol')} BEARISH entry with H4=BULLISH on {t.get('entry_time','?')}")
            elif 'H4=BEARISH' in reason and direction == 'BULLISH':
                h4_violations.append(f"{t.get('symbol')} BULLISH entry with H4=BEARISH on {t.get('entry_time','?')}")
        return {
            "capital": s.get('capital', 0),
            "starting_capital": s.get('starting_capital', 0),
            "total_pnl": s.get('total_pnl', 0),
            "daily_pnl": s.get('daily_pnl', 0),
            "paused": s.get('paused', False),
            "risk_mode": s.get('risk_mode', 'normal'),
            "phase": s.get('phase', ''),
            "closed_trade_count": len(closed),
            "open_trade_count": len(s.get('open_trades', [])),
            "recent_closed": closed[-5:],
            "h4_violations": h4_violations,
        }
    except Exception:
        return {}


def _load_ml_metrics() -> dict:
    metrics = {}
    for model in ['cnn', 'dnn', 'rnn']:
        for market in ['nse', 'ftmo', 'gft']:
            p = CB6_ROOT / f'ml/models/{market}/{model}_meta_latest.json'
            if p.exists():
                try:
                    data = json.loads(p.read_text())
                    if data:
                        metrics[f"{model}_{market}"] = {
                            "trained_at": data.get('trained_at'),
                            "test_acc": data.get('test_acc'),
                            "test_prec": data.get('test_prec'),
                            "val_loss": data.get('val_loss'),
                        }
                except Exception:
                    pass
    return metrics


def run() -> dict:
    journal   = _load_forex_journal()
    nse_j     = _load_nse_journal()
    nse_mem   = _load_bot_memory()
    ftmo      = _load_state('data/ftmo_10k/state.json')
    gft_5k    = _load_state('data/gft_5k/state.json')
    gft_1k    = _load_state('data/gft_1k_instant/state.json')
    ml_stats  = _load_ml_metrics()

    all_h4_violations = ftmo.get('h4_violations', []) + gft_5k.get('h4_violations', []) + gft_1k.get('h4_violations', [])

    user = f"""REAL CB6 DATA — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

=== ACCOUNT 1: NSE FYERS (₹26,000 REAL MONEY) ===
Bot memory: {json.dumps(nse_mem)}
Trade journal: {json.dumps(nse_j)}
Status: 38 trades entered, 0 exits recorded — exit tracking broken?
Capital: ₹26,000 (~$310). Strategy: ICT Silver Bullet.

=== ACCOUNT 2: GFT $1K INSTANT (REAL LIVE FUNDED) ===
{json.dumps(gft_1k, indent=2, default=str)}
Rules: Daily DD $30 max | Risk/trade $2.50 | Max lot 0.01
Status: 0 closed trades — bot running but no signals yet

=== ACCOUNT 3: GFT $5K 2-STEP (PROP CHALLENGE) ===
{json.dumps(gft_5k, indent=2, default=str)}
Phase 1 target: +$400 | Current PnL: -$33 | Need: +$433 more
H4 VIOLATIONS FOUND: {gft_5k.get('h4_violations', [])}
Both closed trades LOST. Trade 2 = counter-trend entry (BEARISH with H4=BULLISH).

=== ACCOUNT 4: FTMO $10K (PROP CHALLENGE — DEADLINE JUNE 6) ===
{json.dumps(ftmo, indent=2, default=str)}
Target: +$500 | Need: +$608 more | DEADLINE: ~June 6 2026 (URGENT)

=== FOREX JOURNAL (199 trades — training data) ===
Win rate: {journal.get('win_rate')}
By symbol: {json.dumps(journal.get('by_symbol',{}))}
By session: {json.dumps(journal.get('by_session',{}))}
Avg R:R: {json.dumps(journal.get('avg_rr',{}))}
Direction edge: {json.dumps(journal.get('by_direction',{}))}

=== ML MODELS ===
{json.dumps(ml_stats)}

KEY STATS TO REFERENCE:
- XAGUSD: 80% WR, 1.81 avg R:R — TOP performer
- XAUUSD: 83.7% WR — great but disabled on GFT
- USOIL: 63% WR, 1.29 R:R
- GBPUSD: 33% WR — DISABLE immediately
- London session: 76.9% WR | NY: 63.5% | Overlap: 47.8%
- BEARISH edge: XAGUSD BEARISH=88.2% vs BULLISH=66.7%

Give SPECIFIC actions for EACH account. Flag H4 violations. Return JSON."""

    fallback = {
        "summary": f"4 accounts active. NSE ₹26K (0 exits recorded). GFT 1K live ($0 PnL). GFT 5K -$33 (H4 violation found). FTMO needs +$608 by June 6.",
        "win_rate_overall": journal.get('win_rate', 0) or 0,
        "win_rate_by_symbol": {k: v['win_rate'] for k, v in journal.get('by_symbol', {}).items()},
        "win_rate_by_session": {k: v['win_rate'] for k, v in journal.get('by_session', {}).items()},
        "avg_rr_by_symbol": journal.get('avg_rr', {}),
        "direction_edge": {k: v['win_rate'] for k, v in journal.get('by_direction', {}).items()},
        "account_status": {
            "nse_fyers": {"capital_inr": 26000, "open_trades": nse_j.get('open_trades', 0), "status": "EXIT_TRACKING_BROKEN", "actions": ["Fix exit tracking in NSE journal", "Verify Fyers API position close is recording exits"]},
            "gft_1k_instant": {"capital": gft_1k.get('capital', 1000), "daily_pnl": gft_1k.get('daily_pnl', 0), "trades_today": gft_1k.get('closed_trade_count', 0), "status": "LIVE_NO_TRADES_YET", "actions": ["Monitor for first signal", "Verify bot connected to MT5"]},
            "gft_5k_2step": {"capital": gft_5k.get('capital', 4967), "pnl": gft_5k.get('total_pnl', -33), "phase": "phase_1", "phase_target": 400, "progress_pct": round(-33/400*100, 1), "status": "BEHIND_H4_VIOLATION", "actions": ["Fix H4 bias filter — no counter-trend entries ever", "Disable GBPUSD"], "h4_violations": len(gft_5k.get('h4_violations', []))},
            "ftmo_10k": {"capital": ftmo.get('capital', 9891), "pnl": ftmo.get('total_pnl', 0), "target": 500, "deadline": "June 6 2026", "status": "URGENT", "actions": ["Focus on XAGUSD London BEARISH setups", "Avoid NY overlap session (47.8% WR)"]},
        },
        "symbols_to_disable": ["GBPUSD"],
        "symbols_to_prioritize": ["XAGUSD", "USOIL"],
        "session_recommendations": ["Prioritize London 07-12 UTC (76.9% WR)", "Reduce NY overlap (47.8% WR)", "Cap NY session exposure"],
        "h4_violations_found": all_h4_violations,
        "ml_retrain_needed": True,
        "ml_retrain_reason": "Models trained May 27 — 8 days old, retrain with June live data",
        "specific_actions": [
            "URGENT: Fix H4 bias filter in gft_5k_2step.py — counter-trend entry found",
            "Disable GBPUSD in forex_instruments.py — 33% WR unacceptable",
            "Fix NSE exit tracking — 38 open trades with no exits recorded",
            "GFT 1K: verify MT5 connection and signal generation",
            "FTMO: focus XAGUSD London session BEARISH only until June 6",
        ],
        "alerts": [
            f"H4 VIOLATION: {v}" for v in all_h4_violations
        ] + (["NSE exit tracking broken — 38 open positions with no exits"] if nse_j.get('open_trades', 0) > 30 else []),
        "key_insights": [
            "XAGUSD BEARISH 88.2% WR — best edge in the system",
            "London session 76.9% WR — 13pts better than NY",
            "GBPUSD 33% WR — destroying expectancy, disable now",
            "GFT $5K: H4 counter-trend entry cost -$33.60 on one trade",
            "NSE: real ₹26K at risk with no exit tracking confirmed",
        ],
    }

    try:
        raw = call_agent('cipher', SYSTEM, user)
        result = safe_parse(raw, fallback)
    except Exception as e:
        fallback['alerts'].append(str(e))
        result = fallback

    # Always inject real computed data
    result['win_rate_by_symbol']  = {k: v['win_rate'] for k, v in journal.get('by_symbol', {}).items()}
    result['win_rate_by_session'] = {k: v['win_rate'] for k, v in journal.get('by_session', {}).items()}
    result['avg_rr_by_symbol']    = journal.get('avg_rr', {})
    result['h4_violations_found'] = all_h4_violations
    result['ml_metrics']          = ml_stats
    result['raw_data']            = {"forex_journal": journal, "nse_journal": nse_j, "nse_memory": nse_mem}

    (REPORTS_DIR / 'quant_report.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')

    with open(REPORTS_DIR / 'quant_report.md', 'w', encoding='utf-8') as f:
        f.write(f"# CIPHER Quant Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"## Summary\n{result.get('summary','')}\n\n")

        f.write("## Account Status\n")
        for acct, data in result.get('account_status', {}).items():
            if isinstance(data, dict):
                status = data.get('status', '?')
                icon = "🔴" if any(x in status for x in ['URGENT','VIOLATION','BROKEN']) else "🟡" if 'BEHIND' in status else "🟢"
                f.write(f"\n### {icon} {acct.upper().replace('_',' ')}\n")
                for k, v in data.items():
                    if k != 'actions':
                        f.write(f"- **{k}:** {v}\n")
                if data.get('actions'):
                    f.write("**Actions:**\n")
                    for a in data['actions']:
                        f.write(f"  - {a}\n")

        f.write("\n## Symbol Win Rates\n| Symbol | WR | Avg R:R |\n|--------|----|---------|\n")
        for sym, wr in result['win_rate_by_symbol'].items():
            rr = result['avg_rr_by_symbol'].get(sym, 'N/A')
            flag = " ← DISABLE" if sym in result.get('symbols_to_disable', []) else " ← PRIORITIZE" if sym in result.get('symbols_to_prioritize', []) else ""
            f.write(f"| {sym} | {wr:.1%} | {rr} |{flag}\n")

        f.write("\n## Session Win Rates\n")
        for sess, wr in result['win_rate_by_session'].items():
            f.write(f"- {sess}: {wr:.1%}\n")

        if result.get('h4_violations_found'):
            f.write("\n## ⚠️ H4 VIOLATIONS (Hard Rule Breach)\n")
            for v in result['h4_violations_found']:
                f.write(f"- 🔴 {v}\n")

        f.write("\n## Specific Actions\n")
        for a in result.get('specific_actions', []):
            f.write(f"- {a}\n")

        if result.get('alerts'):
            f.write("\n## Alerts\n")
            for a in result['alerts']:
                f.write(f"- ⚠️ {a}\n")

    top = max(result['win_rate_by_symbol'].items(), key=lambda x: x[1])[0] if result['win_rate_by_symbol'] else 'N/A'
    print(f"[CIPHER] 4 accounts analyzed | Top: {top} | Actions: {len(result.get('specific_actions',[]))} | H4 violations: {len(all_h4_violations)} | Alerts: {len(result.get('alerts',[]))}")
    return result


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, default=str))
