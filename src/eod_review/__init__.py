"""
End-of-Day Review Module - Analyzes daily trading and writes to KB.

This module runs once at market close (or on-demand) and:
- Collects all day's decisions from DecisionBuffer
- Performs 4-quadrant analysis (Q1-Q4 luck vs skill)
- Generates consolidated, deduplicated lessons
- Writes to KB (the ONLY place KB writes happen)
"""

from .reviewer import EODReviewer, run_eod_review
from .deduplicator import deduplicate_lessons_with_llm

__all__ = [
    'EODReviewer',
    'run_eod_review',
    'deduplicate_lessons_with_llm',
]
