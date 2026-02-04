"""
Day Trading Module - Handles intraday trading operations.

This module runs during market hours and:
- Reads KB context (adhoc strategies + previous learnings)
- Feeds LLM with intraday prices, indicators, volume
- Makes AI-driven trading decisions
- Records decisions for end-of-day review (does NOT write to KB)
"""

from .bot import DayTradingBot
from .decision_buffer import DecisionBuffer

__all__ = [
    'DayTradingBot',
    'DecisionBuffer',
]
