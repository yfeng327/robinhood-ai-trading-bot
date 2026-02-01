# Task Plan: Demo/Live Trading Enhancements

## Goal
Enhance the demo/live trading workflow (600s refresh cycles) with:
1. Detailed timestamps on trade actions
2. Volume analysis (current vs historical periods)
3. Timestamps appended to dates in all KB file entries
4. Meaningful "Recent Lessons" generation (not just date+skill averages)
5. Intraday price+volume tables in LLM prompt for better decisions

## Current Phase
Phase 1: Investigation (complete) -> Phase 2: Implementation

## Phases

### Phase 1: Investigation
- [x] Map current data flow in main.py trading_bot()
- [x] Understand historical_data_day (5min interval, day span) structure
- [x] Review how KB entries use dates vs timestamps
- [x] Review "Recent Lessons" generation in writer.py
- [x] Review LLM prompt assembly in make_ai_decisions()
- **Status:** complete

### Phase 2: Intraday Data Compression & Volume Analysis
- [ ] Create `build_intraday_summary()` in main.py to compress 5-min data into a table
- [ ] Include price points (open/close/high/low) sampled at key intervals
- [ ] Include volume bars compared to average volume
- [ ] Add volume_ratio field to stock_data enrichment
- **Status:** pending

### Phase 3: LLM Prompt Enhancement
- [ ] Add compressed intraday table to AI prompt (price + volume by interval)
- [ ] Keep table compact (one row per ~30min period to limit tokens)
- [ ] Include volume comparison (current period vs avg)
- **Status:** pending

### Phase 4: Timestamp Enhancement
- [ ] Add ISO timestamps to trade action logs (main.py lines 305-354)
- [ ] Update KB writer to use timestamps (not just dates) in entries
- [ ] Format: `[YYYY-MM-DD HH:MM]` instead of `[YYYY-MM-DD]`
- **Status:** pending

### Phase 5: Recent Lessons Generation
- [ ] Fix _update_master_index() "Recent Lessons" - currently just averages
- [ ] Generate actual lesson text based on quadrant analysis
- [ ] Include actionable insights in prompt
- **Status:** pending

### Phase 6: Integration Testing
- [ ] Verify prompt token budget stays within limits with new data
- [ ] Verify KB writes include timestamps
- [ ] Verify volume data appears in LLM prompt
- **Status:** pending

## Key Findings

### Current Data Available
- `historical_data_day`: 5-min interval intraday data with fields:
  - `begins_at`, `close_price`, `open_price`, `high_price`, `low_price`, `volume`
  - Already fetched in main.py for RSI/VWAP calculation but NOT passed to LLM
- `historical_data_year`: daily bars with same fields (for moving averages)

### Current Issues
1. **No timestamps on trades**: Trade logs at main.py:309 just say "Decision: buy of 10"
2. **No volume analysis**: Volume data is fetched but only used for VWAP, not exposed to LLM
3. **KB entries use date only**: All KB entries use `[2026-01-29]` format, no time
4. **"Recent Lessons" is misleading**: master_index.md stores `[date] Avg Skill: X, Luck: Y%` - not actual lessons
5. **LLM sees no intraday data**: The prompt only gets current price, RSI, VWAP, MAs - no price/volume trajectory

### Token Budget for New Data
| Section | Current Budget | New Budget |
|---------|---------------|------------|
| Intraday Price+Volume | 0 | ~800 chars |
| KB Context | 4800 chars | 4800 chars |
| Stock Data (JSON) | ~2000 chars | ~2000 chars |
| Total prompt | ~7000 chars | ~7800 chars |

Gemini limit is ~30k tokens (~120k chars) so adding 800 chars is well within budget.

## Files to Modify

| File | Changes |
|------|---------|
| `main.py` | Add intraday summary builder, timestamps on trades, pass volume data |
| `src/api/robinhood.py` | Add `enrich_with_volume_analysis()` function |
| `src/kb/writer.py` | Use timestamps in KB entries, fix Recent Lessons generation |
| `src/live_kb_tracker.py` | Include timestamps in pending decisions |

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Compress to 30-min bars | 13 rows (9:30-16:00) keeps tokens low |
| Include volume_ratio | LLM needs relative volume, not absolute numbers |
| Use [YYYY-MM-DD HH:MM] | Distinguishes multi-cycle entries within same day |
| Generate lesson text from quadrants | Q1/Q3 = what worked, Q2/Q4 = what to avoid |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
