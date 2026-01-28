# Task Plan: Implement Trading Knowledge Base (KB) System

## Goal
Create a persistent KB system that logs daily trading summaries, analyzes luck vs skill, tracks AI decision quality, and enables RAG-style consultation of past memories for improved future decisions.

## Current Phase
Phase 1

## Phases

### Phase 1: Design KB Structure
- [ ] Define KB directory structure
- [ ] Design daily entry template (luck/skill analysis, decisions, outcomes)
- [ ] Design master index for RAG retrieval
- **Status:** in_progress

### Phase 2: Create KB Templates
- [ ] Create kb_daily_entry.md template
- [ ] Create kb_master_index.md template
- [ ] Create kb_lessons_learned.md template
- **Status:** pending

### Phase 3: Implement KB Writer Module
- [ ] Create src/kb/writer.py - writes daily summaries after each trading day
- [ ] Implement luck vs skill analysis logic
- [ ] Implement decision quality scoring
- **Status:** pending

### Phase 4: Implement KB Reader/RAG Module
- [ ] Create src/kb/reader.py - reads and summarizes past entries
- [ ] Implement relevance-based retrieval (similar market conditions)
- [ ] Create context builder for AI prompts
- **Status:** pending

### Phase 5: Integrate with Backtest Engine
- [ ] Modify engine.py to call KB writer after each day
- [ ] Modify engine.py to call KB reader before AI decisions
- [ ] Add KB context to AI prompts
- **Status:** pending

### Phase 6: Testing & Verification
- [ ] Run backtest with KB system
- [ ] Verify KB files are created correctly
- [ ] Verify AI consults KB in decisions
- **Status:** pending

## Key Questions
1. How to objectively measure luck vs skill? → Price moved in predicted direction = skill, unexpected moves = luck
2. How to structure RAG retrieval? → By date, by symbol, by market conditions
3. What metrics define "correct" decisions? → Profit/loss, alignment with indicators, timing

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| File-based KB (markdown) | Human-readable, easy to inspect, consistent with plan-with-files |
| Daily entries + master index | Granular history + quick lookups |
| Luck/skill scoring system | Objective measurement of decision quality |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
|       | 1       |            |

## Notes
- KB should be created in project root under `kb/` directory
- Each backtest run creates a new KB subdirectory by date
- Master index aggregates learnings across all days
