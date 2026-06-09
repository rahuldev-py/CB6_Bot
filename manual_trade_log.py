"""
CB6 Quantum — Manual Trade Logger
===================================
Use this whenever the bot missed a trade you took manually.
Feeds the trade into:
  1. data/ml/nse/trades.jsonl       — NSE ML training data
  2. data/ml/forex/gft_trades.jsonl — GFT ML training data
  3. data/trade_journal.csv         — CSV trade journal
  4. agent_reports/manual_trades/   — Agent-readable explanation

Usage:
    python manual_trade_log.py

Just answer the prompts. All fields have smart defaults.
"""
import csv
import json
import os
import uuid
from datetime import datetime, timezone

ROOT         = os.path.dirname(os.path.abspath(__file__))
NSE_JSONL    = os.path.join(ROOT, 'data', 'ml', 'nse', 'trades.jsonl')
GFT_JSONL    = os.path.join(ROOT, 'data', 'ml', 'forex', 'gft_trades.jsonl')
FTMO_JSONL   = os.path.join(ROOT, 'data', 'ml', 'forex', 'ftmo_trades.jsonl')
CSV_JOURNAL  = os.path.join(ROOT, 'data', 'trade_journal.csv')
REPORTS_DIR  = os.path.join(ROOT, 'agent_reports', 'manual_trades')
os.makedirs(REPORTS_DIR, exist_ok=True)

SEP = "=" * 55


def ask(prompt, default=None, choices=None):
    hint = f" [{default}]" if default is not None else ""
    if choices:
        hint += f" ({'/'.join(choices)})"
    while True:
        val = input(f"  {prompt}{hint}: ").strip()
        if not val and default is not None:
            return str(default)
        if choices and val.upper() not in [c.upper() for c in choices]:
            print(f"    Please enter one of: {', '.join(choices)}")
            continue
        if val:
            return val


def ask_float(prompt, default=None):
    while True:
        raw = ask(prompt, default)
        try:
            return float(raw)
        except ValueError:
            print("    Please enter a number.")


def ask_date(prompt, default=None):
    if default is None:
        default = datetime.now().strftime('%Y-%m-%d')
    while True:
        raw = ask(prompt, default)
        try:
            datetime.strptime(raw, '%Y-%m-%d')
            return raw
        except ValueError:
            print("    Format: YYYY-MM-DD")


def ask_time(prompt, default=None):
    if default is None:
        default = datetime.now().strftime('%H:%M')
    while True:
        raw = ask(prompt, default)
        try:
            datetime.strptime(raw, '%H:%M')
            return raw + ':00'
        except ValueError:
            print("    Format: HH:MM")


# ── CSV journal helpers ────────────────────────────────────────────────────────

def _csv_headers():
    try:
        with open(CSV_JOURNAL, encoding='utf-8') as f:
            return next(csv.DictReader(f)).keys()
    except Exception:
        return []


def write_csv(row: dict):
    headers = list(_csv_headers())
    if not headers:
        print("  CSV journal not found — skipping CSV write")
        return
    with open(CSV_JOURNAL, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writerow({k: row.get(k, '') for k in headers})


# ── JSONL helpers ──────────────────────────────────────────────────────────────

def write_jsonl(path: str, records: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with open(path, 'a', encoding='utf-8') as f:
        for r in records:
            r['_written_at'] = now
            f.write(json.dumps(r, default=str) + '\n')


# ── Agent report ───────────────────────────────────────────────────────────────

def write_agent_report(trade_id: str, data: dict):
    fname = os.path.join(REPORTS_DIR, f"{trade_id}.md")
    mkt   = data['market']
    d     = data['direction']
    sym   = data['symbol']
    pnl_label = f"Rs {data['pnl']:.0f}" if mkt == 'NSE' else f"${data['pnl']:.2f}"
    result = 'WIN' if data['pnl'] > 0 else 'LOSS'

    lines = [
        f"# Manual Trade — {sym} {d} | {data['date']}",
        f"## For: ML / NEXUS / CIPHER / SHADOW",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| Trade ID | {trade_id} |",
        f"| Market | {mkt} |",
        f"| Symbol | {sym} |",
        f"| Direction | {d} |",
        f"| Entry time | {data['entry_time']} |",
        f"| Exit time | {data['exit_time']} |",
        f"| Entry price | {data['entry_price']} |",
        f"| Exit price | {data['exit_price']} |",
        f"| Stop loss | {data['stop_loss']} |",
        f"| T1 / T2 / T3 | {data['t1']} / {data['t2']} / {data['t3']} |",
        f"| Realized PnL | {pnl_label} |",
        f"| Result | **{result}** |",
        f"| Exit reason | {data['exit_reason']} |",
        f"| H4 bias | {data['h4_bias']} |",
        f"| Score | {data['score']} |",
        f"| Sweep confirmed | {data['sweep_confirmed']} |",
        f"| BOS level | {data['bos_level']} |",
        f"| FVG zone | {data['fvg_low']} — {data['fvg_high']} |",
        f"| DOL level | {data['dol_level']} |",
        f"| Notes | {data['notes']} |",
        "",
        "## Why Bot Missed It",
        f"> {data['why_missed']}",
        "",
        "## ML Feature Label",
        f"```json",
        json.dumps({
            "trade_id"    : trade_id,
            "result"      : result,
            "direction"   : d,
            "h4_bias"     : data['h4_bias'],
            "score"       : data['score'],
            "sweep_confirmed": data['sweep_confirmed'],
            "fvg_present" : True,
            "bos_confirmed": True,
            "counter_trend": data['h4_bias'] != d.replace('BULLISH','BULLISH').replace('BEARISH','BEARISH'),
            "pnl"         : data['pnl'],
        }, indent=2),
        "```",
    ]
    with open(fname, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return fname


# ── NSE flow ───────────────────────────────────────────────────────────────────

def log_nse_trade():
    print(f"\n{SEP}")
    print("  NSE TRADE — Enter Details")
    print(SEP)

    date       = ask_date("Trade date")
    entry_time = ask_time("Entry time (IST)")
    exit_time  = ask_time("Exit time (IST)")

    print("\n  --- Underlying ---")
    index   = ask("Index", "NIFTY", ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"])
    und_entry = ask_float(f"Underlying price at ENTRY")
    und_exit  = ask_float(f"Underlying price at EXIT")
    direction = ask("Direction", "BULLISH", ["BULLISH","BEARISH"])

    print("\n  --- Option Details ---")
    instr  = ask("Instrument type", "CE", ["CE","PE","FUT"])
    if instr != 'FUT':
        strike     = ask_float("Strike price")
        opt_entry  = ask_float("Option entry price (premium paid)")
        opt_exit   = ask_float("Option exit price (premium received)")
        expiry     = ask_date("Expiry date")
        lot_size   = ask_float("Lot size", 65 if index=="NIFTY" else 30 if index=="BANKNIFTY" else 60 if index=="FINNIFTY" else 120)
        lots       = ask_float("Number of lots", 1)
        qty        = int(lot_size * lots)
        pnl        = round((opt_exit - opt_entry) * qty, 2)
        symbol     = f"NSE:{index}{date.replace('-','')[2:6]}{strike:.0f}{instr}"
    else:
        strike, opt_entry, opt_exit, expiry = 0, und_entry, und_exit, date
        lot_size   = ask_float("Lot size", 50)
        lots       = ask_float("Number of lots", 1)
        qty        = int(lot_size * lots)
        pnl        = round((und_exit - und_entry) * qty * (1 if direction=='BULLISH' else -1), 2)
        symbol     = f"NSE:{index}JUNFUT"

    print(f"\n  Calculated PnL: Rs {pnl:.2f}")
    pnl_confirm = ask_float("Actual realized PnL (INR) — confirm or correct", pnl)

    print("\n  --- ICT Setup Details ---")
    sl_level   = ask_float("Stop loss level (underlying)", round(und_entry - 60 if direction=='BULLISH' else und_entry + 60))
    t1         = ask_float("Target 1 (underlying)")
    t2         = ask_float("Target 2 (underlying)", round(t1 + (t1 - sl_level)))
    t3         = ask_float("Target 3 (underlying)", round(t2 + (t1 - sl_level)))
    dol_level  = ask_float("DOL level swept (swing high/low)")
    bos_level  = ask_float("BOS level (CHoCH/BOS candle close)")
    fvg_low    = ask_float("FVG bottom")
    fvg_high   = ask_float("FVG top")
    h4_bias    = ask("H4 bias", "BEARISH", ["BULLISH","BEARISH","RANGING"])
    score      = ask_float("Setup confluence score (out of 15)", 13)
    sweep_conf = ask("Sweep confirmed?", "YES", ["YES","NO"])
    exit_reason= ask("Exit reason", "MANUAL_EARLY",
                     ["T1","T2","T3","SL","MANUAL_EARLY","MANUAL","EOD"])

    print("\n  --- Context ---")
    window    = ask("Silver Bullet window", "AFTERNOON",
                    ["MORNING","AFTERNOON","CLOSE","OTHER"])
    why_missed= ask("Why did the bot miss this trade?",
                    "WebSocket feed issue or equilibrium filter rejected FVG")
    notes     = ask("Any notes", "Counter-trend trade, 50% size applied")

    trade_id = f"manual_{date.replace('-','')}_{entry_time[:5].replace(':','')}"

    # ── Build records ──────────────────────────────────────────────────────────
    ist_h, ist_m = map(int, entry_time[:5].split(':'))
    utc_h = (ist_h - 5) % 24
    utc_m = max(0, ist_m - 30)

    entry_record = {
        "_type"              : "ENTRY",
        "_schema_version"    : 2,
        "market"             : "NSE",
        "mode"               : "live",
        "trade_id"           : trade_id,
        "symbol"             : symbol,
        "underlying"         : f"NSE:{index}50-INDEX",
        "instrument_type"    : instr,
        "direction"          : direction,
        "timeframe"          : "3min",
        "entry_price"        : opt_entry if instr != 'FUT' else und_entry,
        "stop_loss"          : sl_level,
        "target_1"           : t1,
        "target_2"           : t2,
        "target_3"           : t3,
        "sl_distance"        : round(abs(und_entry - sl_level), 2),
        "in_fvg"             : True,
        "mss_type"           : "BOS",
        "sweep_confirmed"    : sweep_conf == "YES",
        "fvg_low"            : fvg_low,
        "fvg_high"           : fvg_high,
        "fvg_size"           : round(fvg_high - fvg_low, 2),
        "dol_direction"      : "SELL_SIDE" if direction == "BULLISH" else "BUY_SIDE",
        "dol_price"          : dol_level,
        "dol_mss_match"      : True,
        "score"              : int(score),
        "h4_bias"            : h4_bias,
        "ist_hour"           : ist_h,
        "ist_minute"         : ist_m,
        "utc_hour"           : utc_h,
        "session"            : window.lower() + "_silver_bullet",
        "outcome"            : None,
        "strike"             : strike,
        "underlying_at_entry": und_entry,
        "bos_level"          : bos_level,
        "counter_trend"      : (direction=="BULLISH" and h4_bias=="BEARISH") or (direction=="BEARISH" and h4_bias=="BULLISH"),
    }

    targets_hit = []
    if instr != 'FUT':
        if und_exit >= t1: targets_hit.append("T1")
        if und_exit >= t2: targets_hit.append("T2")
        if und_exit >= t3: targets_hit.append("T3")
    result = "WIN" if pnl_confirm > 0 else ("LOSS" if pnl_confirm < 0 else "BREAKEVEN")

    sl_dist = abs(und_entry - sl_level)
    r_mult  = round(pnl_confirm / (sl_dist * qty / (lots * lot_size)), 2) if sl_dist > 0 else 0

    entry_t   = f"{date}T{entry_time}"
    exit_t_dt = datetime.strptime(f"{date} {exit_time}", "%Y-%m-%d %H:%M:%S")
    entr_t_dt = datetime.strptime(f"{date} {entry_time}", "%Y-%m-%d %H:%M:%S")
    hold_mins = int((exit_t_dt - entr_t_dt).total_seconds() / 60)

    outcome_record = {
        "_type"    : "OUTCOME",
        "trade_id" : trade_id,
        "outcome"  : {
            "exit_reason"        : exit_reason,
            "exit_price"         : opt_exit if instr != 'FUT' else und_exit,
            "underlying_at_exit" : und_exit,
            "pnl_inr"            : pnl_confirm,
            "r_multiple"         : r_mult,
            "targets_hit"        : targets_hit,
            "hold_time_minutes"  : hold_mins,
            "result"             : result,
            "timestamp_exit_ist" : f"{date} {exit_time}",
            "notes"              : notes,
        }
    }

    csv_row = {
        "date"         : date,
        "entry_time"   : entry_time[:8],
        "exit_time"    : exit_time[:8],
        "symbol"       : symbol,
        "underlying"   : f"NSE:{index}50-INDEX",
        "direction"    : direction,
        "window"       : window.capitalize() + " Silver Bullet",
        "score"        : int(score),
        "strike"       : strike,
        "expiry"       : expiry,
        "ltp_at_entry" : opt_entry if instr != 'FUT' else und_entry,
        "entry_price"  : opt_entry if instr != 'FUT' else und_entry,
        "stop_loss"    : sl_level,
        "target1"      : t1,
        "target2"      : t2,
        "target3"      : t3,
        "exit_price"   : opt_exit if instr != 'FUT' else und_exit,
        "exit_reason"  : exit_reason,
        "lots"         : int(lots),
        "lot_size"     : int(lot_size),
        "qty"          : qty,
        "realized_pnl" : pnl_confirm,
        "mins_in_fvg"  : hold_mins,
        "theta_burn"   : "YES" if (hold_mins > 20 and not targets_hit) else "NO",
        "displacement" : "True",
        "in_fvg"       : "True",
    }

    agent_data = {
        "market": "NSE", "date": date, "symbol": symbol,
        "direction": direction, "entry_time": f"{date} {entry_time}",
        "exit_time": f"{date} {exit_time}", "entry_price": opt_entry,
        "exit_price": opt_exit, "stop_loss": sl_level,
        "t1": t1, "t2": t2, "t3": t3,
        "pnl": pnl_confirm, "exit_reason": exit_reason,
        "h4_bias": h4_bias, "score": int(score),
        "sweep_confirmed": sweep_conf == "YES",
        "bos_level": bos_level, "fvg_low": fvg_low,
        "fvg_high": fvg_high, "dol_level": dol_level,
        "notes": notes, "why_missed": why_missed,
    }

    # ── Write everything ───────────────────────────────────────────────────────
    write_jsonl(NSE_JSONL, [entry_record, outcome_record])
    write_csv(csv_row)
    report = write_agent_report(trade_id, agent_data)

    print(f"\n{SEP}")
    print(f"  TRADE LOGGED — {trade_id}")
    print(SEP)
    print(f"  ML JSONL  : data/ml/nse/trades.jsonl  (+ENTRY +OUTCOME)")
    print(f"  CSV       : data/trade_journal.csv    (+1 row)")
    print(f"  Report    : {os.path.relpath(report)}")
    print(f"  Result    : {result}  |  PnL: Rs {pnl_confirm:.0f}  |  R={r_mult}")
    return trade_id


# ── Forex flow ─────────────────────────────────────────────────────────────────

def log_forex_trade():
    print(f"\n{SEP}")
    print("  FOREX TRADE — Enter Details")
    print(SEP)

    account   = ask("Account", "GFT_5K", ["GFT_5K","GFT_1K","FTMO"])
    date      = ask_date("Trade date")
    entry_time= ask_time("Entry time (UTC)")
    exit_time = ask_time("Exit time (UTC)")
    symbol    = ask("Symbol", "XAGUSD", ["XAGUSD","USOIL","EURUSD"])
    direction = ask("Direction", "BULLISH", ["BULLISH","BEARISH"])
    lots      = ask_float("Lot size", 0.01)
    entry_px  = ask_float("Entry price")
    exit_px   = ask_float("Exit price")
    sl        = ask_float("Stop loss price")
    t1        = ask_float("Target 1")
    t2        = ask_float("Target 2", round(t1 + abs(t1 - entry_px), 4))
    t3        = ask_float("Target 3", round(t2 + abs(t1 - entry_px), 4))
    pnl       = ask_float("Realized PnL (USD)")
    h4_bias   = ask("H4 bias", "BULLISH", ["BULLISH","BEARISH","RANGING"])
    score     = ask_float("Confluence score", 13)
    dol_level = ask_float("DOL level swept")
    bos_level = ask_float("BOS level")
    fvg_low   = ask_float("FVG bottom")
    fvg_high  = ask_float("FVG top")
    sweep_conf= ask("Sweep confirmed?", "YES", ["YES","NO"])
    exit_reason = ask("Exit reason", "MANUAL_EARLY",
                      ["T1","T2","T3","SL","MANUAL_EARLY","MANUAL","TIME_EXIT"])
    why_missed = ask("Why did the bot miss this?",
                     "WebSocket issue or H4 counter-trend block")
    notes      = ask("Notes", "")

    trade_id = f"manual_{account.lower()}_{date.replace('-','')}_{entry_time[:5].replace(':','')}"
    result   = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

    entry_record = {
        "_type"        : "ENTRY",
        "_schema_version": 2,
        "market"       : "forex",
        "account"      : account.lower(),
        "mode"         : "live",
        "trade_id"     : trade_id,
        "symbol"       : symbol,
        "direction"    : direction,
        "lots"         : lots,
        "entry_price"  : entry_px,
        "stop_loss"    : sl,
        "target_1"     : t1,
        "target_2"     : t2,
        "target_3"     : t3,
        "h4_bias"      : h4_bias,
        "score"        : int(score),
        "sweep_confirmed": sweep_conf == "YES",
        "fvg_low"      : fvg_low,
        "fvg_high"     : fvg_high,
        "dol_price"    : dol_level,
        "bos_level"    : bos_level,
        "entry_time_utc": f"{date}T{entry_time}",
        "outcome"      : None,
    }

    utc_h = int(entry_time[:2])
    sl_pts = round(abs(entry_px - sl), 5)
    r_mult = round(pnl / (sl_pts * lots * 100), 2) if sl_pts > 0 else 0
    entry_dt = datetime.strptime(f"{date} {entry_time}", "%Y-%m-%d %H:%M:%S")
    exit_dt  = datetime.strptime(f"{date} {exit_time}",  "%Y-%m-%d %H:%M:%S")
    hold_min = int((exit_dt - entry_dt).total_seconds() / 60)

    outcome_record = {
        "_type"    : "OUTCOME",
        "trade_id" : trade_id,
        "outcome"  : {
            "exit_reason"     : exit_reason,
            "exit_price"      : exit_px,
            "pnl_usd"         : pnl,
            "r_multiple"      : r_mult,
            "hold_mins"       : hold_min,
            "result"          : result,
            "exit_time_utc"   : f"{date}T{exit_time}",
            "notes"           : notes,
        }
    }

    jsonl_path = GFT_JSONL if "GFT" in account else FTMO_JSONL
    write_jsonl(jsonl_path, [entry_record, outcome_record])

    agent_data = {
        "market": "FOREX", "date": date, "symbol": symbol,
        "direction": direction, "entry_time": f"{date} {entry_time}",
        "exit_time": f"{date} {exit_time}", "entry_price": entry_px,
        "exit_price": exit_px, "stop_loss": sl,
        "t1": t1, "t2": t2, "t3": t3,
        "pnl": pnl, "exit_reason": exit_reason,
        "h4_bias": h4_bias, "score": int(score),
        "sweep_confirmed": sweep_conf == "YES",
        "bos_level": bos_level, "fvg_low": fvg_low,
        "fvg_high": fvg_high, "dol_level": dol_level,
        "notes": notes, "why_missed": why_missed,
    }
    report = write_agent_report(trade_id, agent_data)

    print(f"\n{SEP}")
    print(f"  TRADE LOGGED — {trade_id}")
    print(SEP)
    print(f"  ML JSONL  : {os.path.relpath(jsonl_path)}  (+ENTRY +OUTCOME)")
    print(f"  Report    : {os.path.relpath(report)}")
    print(f"  Result    : {result}  |  PnL: ${pnl:.2f}  |  R={r_mult}")
    return trade_id


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{SEP}")
    print("  CB6 Quantum — Manual Trade Logger")
    print("  Feeds missed trades into ML + CSV + Agent reports")
    print(SEP)

    while True:
        market = ask("\nMarket", "NSE", ["NSE","FOREX","QUIT"])
        if market.upper() == "QUIT":
            print("\n  Done. All trades saved.")
            break
        elif market.upper() == "NSE":
            log_nse_trade()
        elif market.upper() == "FOREX":
            log_forex_trade()

        another = ask("\nLog another trade?", "NO", ["YES","NO"])
        if another.upper() != "YES":
            print("\n  Done. All trades saved.")
            break


if __name__ == "__main__":
    main()
