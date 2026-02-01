# Findings: Demo/Live Trading Enhancements

## Current Workflow (main.py trading_bot())

### Data Flow:
```
1. get_account_info() -> buying_power
2. get_portfolio_stocks() -> portfolio_stocks
3. For each stock:
   a. get_historical_data(symbol, "5minute", "day")  -> intraday 5-min bars
   b. get_historical_data(symbol, "day", "year")      -> daily bars
   c. get_ratings(symbol)                              -> analyst ratings
   d. enrich_with_rsi(stock_data, historical_data_day)
   e. enrich_with_vwap(stock_data, historical_data_day)
   f. enrich_with_moving_averages(stock_data, historical_data_year)
   g. enrich_with_analyst_ratings(stock_data, ratings_data)
   h. enrich_with_pdt_restrictions(stock_data, symbol)
4. kb_tracker.evaluate_pending_decisions()  -> writes KB from previous cycle
5. make_ai_decisions(account_info, portfolio, watchlist)
   - Builds prompt with: Context + KB Context + Constraints + Stock Data JSON
   - Stock Data JSON: {symbol: {current_price, my_quantity, avg_buy_price, rsi, vwap, 50d_ma, 200d_ma, analyst_summary, analyst_ratings, pdt_flags}}
   - MISSING: No intraday price trajectory, no volume data, no volume comparison
6. Execute trades
7. kb_tracker.record_decisions()
```

### What LLM Currently SEES in Stock Data:
```json
{
  "VOO": {
    "current_price": 635.54,
    "my_quantity": 239.0,
    "my_average_buy_price": 520.0,
    "rsi": 45.2,
    "vwap": 633.10,
    "50_day_mavg_price": 610.0,
    "200_day_mavg_price": 580.0,
    "analyst_summary": {...},
    "analyst_ratings": [...]
  }
}
```

### What LLM SHOULD Also See:
```
Today's Intraday Data (VOO):
Time    | Price  | Vol   | VolRatio
09:30   | 634.20 | 120K  | 1.2x
10:00   | 634.80 | 95K   | 0.9x
10:30   | 635.10 | 88K   | 0.8x
...     | ...    | ...   | ...
```

## Robinhood API Data Structure

### 5-minute historical bar (from get_stock_historicals):
```python
{
    "begins_at": "2026-01-29T14:30:00Z",
    "close_price": "635.54",
    "high_price": "636.10",
    "low_price": "634.90",
    "open_price": "635.20",
    "volume": 12345,
    "session": "reg",         # regular trading hours
    "interpolated": False
}
```

### Available intervals: "5minute", "10minute", "hour", "day", "week"
### Available spans: "day", "week", "month", "3month", "year", "5year"

## KB Timestamp Issues

### Current format in KB entries:
- `[2026-01-29] NVDA: BUY worked (skill=58...)`
- `[2026-01-29] Avg Skill: 65, Luck: 30%`

### Problem:
In demo/live mode with 600s cycles, there are ~39 cycles per trading day (9:30-16:00).
Each cycle may produce entries, but they all show the same date `[2026-01-29]`.
No way to distinguish decisions made at 9:30 vs 15:30.

### Fix:
Use `[2026-01-29 14:30]` format to include the time of the decision.

## "Recent Lessons" Problem

### Current code (writer.py _update_master_index, line 942):
```python
new_lesson = f"\n- [{date}] Avg Skill: {avg_skill:.0f}, Luck: {avg_luck:.0%}"
```

### Problem:
This is NOT a lesson. It's just statistics. A lesson should be actionable:
- "BUY NVDA at RSI<30 worked well (Q1: skill+luck)"
- "SELL TQQQ on high volume days reduces losses"
- "Holding IREN through earnings was a mistake (Q4)"

### Fix:
Generate actual lesson text from the best/worst decisions of the day:
- Q1 (skill+luck): Extract what made it a good decision -> positive lesson
- Q4 (no skill+no luck): Extract what went wrong -> avoidance lesson
- Use the `lesson_learned` field from DecisionAnalysis

## Volume Analysis Approach

### What to compute:
1. **Current period volume**: Sum of volume in last 30 minutes
2. **Average period volume**: Average 30-min volume from historical_data_day
3. **Volume ratio**: current / average (>1.0 = higher than usual)

### Why it matters:
- High volume + price move = stronger signal (institutional activity)
- Low volume + price move = weaker signal (noise)
- Unusual volume spikes can indicate breakouts or reversals

---
*Updated: 2026-01-29*
