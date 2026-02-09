"""
LLM-Driven Lesson Deduplicator - Prevents duplicate lessons in KB.

Uses an LLM to semantically compare new lessons against existing KB entries.
For each new lesson, the LLM classifies it as:
- "new"       — no semantic overlap, write as-is
- "duplicate" — already covered by an existing lesson, drop it
- "merge"     — overlaps with an existing lesson; returns a merged/upgraded
                 lesson that replaces the existing one

This replaces the old regex-based pattern matching which failed to catch
semantically identical lessons with minor numerical differences.
"""

import json
import re
import logging
from typing import List, Tuple

from src.api import ai
from src.utils.text_sanitizer import sanitize_llm_output

logger = logging.getLogger(__name__)


def deduplicate_lessons_with_llm(
    new_lessons: List[str],
    existing_lessons: List[str],
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """
    Deduplicate new lessons against existing KB lessons using LLM.

    Args:
        new_lessons: Lessons generated from today's decisions
        existing_lessons: Lessons already stored in KB

    Returns:
        Tuple of:
        - unique_lessons: New lessons to append (not duplicates)
        - merged_lessons: List of (old_lesson, new_merged_lesson) for replacement
    """
    if not new_lessons:
        return [], []

    if not existing_lessons:
        # Nothing to dedup against — all are new
        logger.info(f"No existing lessons in KB, all {len(new_lessons)} lessons are new")
        return new_lessons, []

    prompt = _build_dedup_prompt(new_lessons, existing_lessons)

    try:
        response = ai.make_ai_request(prompt)
        unique, merged = _parse_dedup_response(response, new_lessons, existing_lessons)
        logger.info(
            f"LLM dedup: {len(unique)} new, {len(merged)} merged, "
            f"{len(new_lessons) - len(unique) - len(merged)} dropped"
        )
        return unique, merged
    except Exception as e:
        logger.error(f"LLM dedup failed, using fallback: {e}")
        return _fallback_dedup(new_lessons, existing_lessons)


def _build_dedup_prompt(
    new_lessons: List[str],
    existing_lessons: List[str],
) -> str:
    """Build the LLM prompt for deduplication."""
    existing_json = json.dumps(
        [{"id": i, "lesson": l} for i, l in enumerate(existing_lessons)],
        indent=1,
    )
    new_json = json.dumps(
        [{"id": i, "lesson": l} for i, l in enumerate(new_lessons)],
        indent=1,
    )

    return f'''You are a knowledge base curator for a trading bot. Your job is to prevent duplicate lessons from piling up.

## EXISTING LESSONS IN KB:
{existing_json}

## NEW LESSONS TO EVALUATE:
{new_json}

## TASK:
For each new lesson, decide:
1. **"new"** — The lesson covers a genuinely different insight, symbol, action, or condition not already in the KB. Keep it.
2. **"duplicate"** — The lesson says essentially the same thing as an existing lesson (same symbol, same action, same advice), even if numbers differ slightly. Drop it.
3. **"merge"** — The lesson overlaps with an existing lesson but adds new evidence or a refinement. Combine them into one stronger lesson that:
   - Keeps the best specific rules/thresholds from both
   - Notes repeated confirmation if the same pattern keeps working
   - Updates numbers to the most recent data
   - Stays concise (1-3 sentences max)

## RULES:
- Two lessons about the SAME symbol + SAME action + SAME general advice = duplicate or merge, NOT new
- Minor score differences (skill=70 vs skill=75) do NOT make a lesson unique
- Different symbols or genuinely different strategies = new
- When merging, the merged lesson REPLACES the existing one (provide the existing_id)

## RESPONSE FORMAT (JSON array, one entry per new lesson):
```json
[
  {{"new_id": 0, "action": "new"}},
  {{"new_id": 1, "action": "duplicate", "existing_id": 3}},
  {{"new_id": 2, "action": "merge", "existing_id": 5, "merged_lesson": "[Q1] REMX: SELL ... (consolidated rule with updated evidence)"}}
]
```

Return ONLY the JSON array.'''


def _parse_dedup_response(
    response,
    new_lessons: List[str],
    existing_lessons: List[str],
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Parse LLM dedup response into unique and merged lists."""
    raw_content = ai.get_raw_response_content(response)
    raw_content = sanitize_llm_output(raw_content)

    # Strip markdown code blocks
    raw_content = re.sub(r'```json\s*', '', raw_content)
    raw_content = re.sub(r'```\s*', '', raw_content)

    results = json.loads(raw_content)

    unique = []
    merged = []

    for item in results:
        new_id = item.get('new_id')
        action = item.get('action')

        if new_id is None or new_id >= len(new_lessons):
            continue

        if action == 'new':
            unique.append(new_lessons[new_id])

        elif action == 'merge':
            existing_id = item.get('existing_id')
            merged_lesson = item.get('merged_lesson', '')
            if (
                existing_id is not None
                and existing_id < len(existing_lessons)
                and merged_lesson
            ):
                old_lesson = existing_lessons[existing_id]
                merged.append((old_lesson, merged_lesson))
            else:
                # Malformed merge — treat as new to avoid data loss
                unique.append(new_lessons[new_id])

        elif action == 'duplicate':
            logger.debug(f"Dropped duplicate: {new_lessons[new_id][:80]}...")

        else:
            # Unknown action — keep the lesson to be safe
            unique.append(new_lessons[new_id])

    return unique, merged


def _fallback_dedup(
    new_lessons: List[str],
    existing_lessons: List[str],
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """
    Simple fallback dedup when LLM is unavailable.

    Uses exact substring matching: if the core of a new lesson
    (symbol + action + quadrant) already appears in any existing lesson,
    treat it as a duplicate.
    """
    existing_lower = [l.lower() for l in existing_lessons]
    unique = []

    for lesson in new_lessons:
        # Extract key signature: [Q*] SYMBOL: ACTION
        sig_match = re.search(r'\[Q\d\]\s+(\w+):\s+(BUY|SELL|HOLD)', lesson, re.IGNORECASE)
        if sig_match:
            sig = sig_match.group(0).lower()
            if any(sig in ex for ex in existing_lower):
                logger.debug(f"Fallback dropped duplicate: {lesson[:80]}...")
                continue

        unique.append(lesson)

    logger.info(f"Fallback dedup: {len(unique)} unique, {len(new_lessons) - len(unique)} dropped")
    return unique, []
