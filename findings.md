# Findings & Decisions: Trading Knowledge Base (KB) System

## Requirements
- Create persistent KB system mirroring plan-with-files structure
- Write daily KB entries summarizing trading and performance
- Include recap of AI decisions: what went right/wrong
- Analyze luck vs skill for each decision
- Enable RAG-style retrieval of past memories
- Consult previous errors and learnings in subsequent days

## KB Design: Luck vs Skill Analysis

### What is Skill?
- **Decision aligned with indicators**: RSI, VWAP, moving averages all pointed same direction
- **Correct timing**: Entry/exit at optimal points based on available data
- **Risk management**: Proper position sizing, not over-concentrating
- **Pattern recognition**: Similar past situations were handled correctly

### What is Luck?
- **Unexpected price movements**: Earnings surprises, news events, market-wide swings
- **External factors**: Fed announcements, geopolitical events
- **Indicator disagreement**: Indicators pointed different directions, but trade worked anyway
- **Random walk benefit**: Price moved favorably despite no clear signal

### Scoring System
```
Decision Score = (Skill Score * 0.6) + (Outcome Score * 0.4)

Skill Score (0-100):
- Indicator alignment: +30 (if RSI, MA, VWAP agree)
- Proper position size: +20 (within min/max amounts)
- Risk/reward ratio: +25 (based on expected vs actual)
- Historical pattern match: +25 (similar past decisions worked)

Outcome Score (0-100):
- Profitable trade: +50
- Beat market average: +25
- Minimal drawdown: +25
```

## KB Directory Structure
```
kb/
├── master_index.md           # Aggregated learnings, patterns, rules
├── lessons_learned.md        # Accumulated wisdom from all days
├── sessions/
│   └── YYYY-MM-DD/
│       ├── daily_summary.md  # Performance + decisions for that day
│       ├── decisions.json    # Structured decision data
│       └── analysis.md       # Luck vs skill breakdown
└── patterns/
    ├── bullish_patterns.md   # Successful buy patterns
    ├── bearish_patterns.md   # Successful sell patterns
    └── mistakes.md           # Common errors to avoid
```

## Daily Entry Template Structure

### daily_summary.md
```markdown
# Trading Day Summary: YYYY-MM-DD

## Performance Metrics
- Starting Value: $X
- Ending Value: $Y
- Day Return: +/-Z%
- Trades Executed: N

## Decisions Made
| Symbol | Action | Qty | Price | Outcome | Skill Score | Luck Factor |
|--------|--------|-----|-------|---------|-------------|-------------|

## What Went Right
- [List of correct decisions with reasoning]

## What Went Wrong
- [List of incorrect decisions with analysis]

## Luck vs Skill Analysis
- Total Skill Score: X/100
- Luck Factor: Y%
- Key Learning: [Main takeaway]

## Lessons for Tomorrow
- [Actionable insights to carry forward]
```

## RAG Integration Approach

### Before Each Trading Day
1. Read `master_index.md` for accumulated rules
2. Read last 5 `daily_summary.md` files for recent context
3. Search `patterns/` for similar market conditions
4. Search `mistakes.md` for errors to avoid
5. Build context string for AI prompt

### After Each Trading Day
1. Evaluate each decision (skill score, outcome)
2. Write `daily_summary.md`
3. Update `master_index.md` with new patterns
4. Update `lessons_learned.md` if significant learning
5. Update `mistakes.md` if error occurred

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Markdown files for KB | Human-readable, version-controllable, easy to inspect |
| JSON for structured data | Machine-parseable for analytics |
| Session-based directories | Easy to find specific day's data |
| Master index pattern | Quick RAG retrieval without reading all files |
| 60/40 skill/outcome weighting | Rewards good process even if outcome bad |

## Files to Create
1. `src/kb/__init__.py` - Module exports
2. `src/kb/writer.py` - Write daily summaries
3. `src/kb/reader.py` - RAG retrieval
4. `src/kb/analyzer.py` - Luck vs skill scoring
5. `src/kb/templates.py` - MD templates

## Integration Points in engine.py
- `run()` method: Call KB reader before trading day loop
- `make_ai_decisions()`: Include KB context in prompt
- `execute_decisions()`: Track decisions for scoring
- After daily loop: Call KB writer to save summary

---
*Update this file after every 2 view/browser/search operations*
