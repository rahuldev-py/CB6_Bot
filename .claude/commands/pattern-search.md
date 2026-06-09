# /pattern-search — Query Historical Trade Patterns

Search the trade pattern database for setups matching current or specified market conditions. Answers: "Have we seen this setup before? What happened?"

## Usage
```
/pattern-search XAGUSD BEARISH London H4=BULLISH
/pattern-search NIFTY LONG h4=bearish confluence>=12
/pattern-search fvg_body>50 session=ny outcome=win
```

## Steps

### 1. Parse Query
Extract filter criteria from arguments:
- symbol, direction, session (London/NY/Morning/Afternoon)
- h4_bias (BULLISH/BEARISH/RANGING), confluence (>=N), fvg_body_pct (>=N%), outcome

### 2. Query Trade Pattern DB
```python
db = sqlite3.connect('ml_engine/memory/trade_pattern_db.sqlite')
# FTS5 for text fields, standard WHERE for numeric fields
```
Fallback if DB not populated: parse `data/gft_5k/state.json` + `data/gft_1k_instant/state.json`
closed_trades arrays + `data/trade_journal.csv`

### 3. Statistical Summary
From matching trades:
- Count, Win rate, Average R
- Best sub-condition: "When h4_bias=BEARISH, WR rises to 75%"
- Worst sub-condition: "When session=Asia, WR drops to 33%"

### 4. Most Recent Matches
Show 3 most recent: date | entry | exit | R multiple | exit reason

### 5. Actionable Verdict
One sentence: "Based on [N] similar past setups, this pattern has [X]% WR — [Proceed / Caution / Historically weak]."
