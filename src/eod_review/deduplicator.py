"""
Lesson Deduplicator - Prevents duplicate lessons in KB.

Checks existing KB entries before writing new lessons to avoid
the repetitive entries problem observed in the original implementation.
"""

import re
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LessonDeduplicator:
    """
    Detects and prevents duplicate lesson entries in KB.
    
    Uses pattern matching to identify semantically similar lessons:
    - Same symbol + same action = potential duplicate
    - Similar conditions (RSI ranges, price patterns) = potential duplicate
    """
    
    def __init__(self, existing_lessons: List[str] = None):
        """
        Initialize with existing lessons from KB.
        
        Args:
            existing_lessons: List of lesson strings already in KB
        """
        self.existing_lessons = existing_lessons or []
        self._pattern_cache: Dict[str, str] = {}
        
        # Pre-compute patterns for existing lessons
        for lesson in self.existing_lessons:
            pattern = self._extract_pattern(lesson)
            if pattern:
                self._pattern_cache[pattern] = lesson
    
    def _extract_pattern(self, lesson: str) -> Optional[str]:
        """
        Extract a normalized pattern key from a lesson.
        
        Examples:
        - "NVDA: BUY worked (skill=58...)" -> "BUY_NVDA"
        - "Consider RSI extremes. RSI was 77." -> "RSI_EXTREME_77"
        - "Mixed results. Continue monitoring." -> "MONITORING"
        
        Returns:
            Normalized pattern key or None if unparseable
        """
        lesson_upper = lesson.upper()
        
        # Extract symbol if present
        symbol_match = re.search(r'\b([A-Z]{2,5})\b:?\s*(BUY|SELL|HOLD)?', lesson_upper)
        symbol = symbol_match.group(1) if symbol_match else None
        action = symbol_match.group(2) if symbol_match and symbol_match.group(2) else None
        
        # Check for specific pattern types
        if "RSI" in lesson_upper:
            rsi_match = re.search(r'RSI\s*(?:WAS|=|:)?\s*(\d+)', lesson_upper)
            if rsi_match:
                rsi_val = int(rsi_match.group(1))
                # Bucket RSI values: <30 = oversold, >70 = overbought
                if rsi_val < 30:
                    return f"RSI_OVERSOLD_{symbol or 'ANY'}"
                elif rsi_val > 70:
                    return f"RSI_OVERBOUGHT_{symbol or 'ANY'}"
                else:
                    return f"RSI_NEUTRAL_{symbol or 'ANY'}"
        
        if "MIXED RESULTS" in lesson_upper or "CONTINUE MONITORING" in lesson_upper:
            if symbol:
                return f"MONITORING_{symbol}"
            return "MONITORING_GENERIC"
        
        if "GOOD PROCESS" in lesson_upper or "POOR PROCESS" in lesson_upper:
            quality = "GOOD" if "GOOD PROCESS" in lesson_upper else "POOR"
            if symbol and action:
                return f"{quality}_PROCESS_{action}_{symbol}"
            elif symbol:
                return f"{quality}_PROCESS_{symbol}"
            return f"{quality}_PROCESS_GENERIC"
        
        if symbol and action:
            return f"{action}_{symbol}"
        
        if symbol:
            return f"SYMBOL_{symbol}"
        
        return None
    
    def is_duplicate(self, lesson: str) -> bool:
        """
        Check if a lesson duplicates an existing one.
        
        Args:
            lesson: New lesson to check
            
        Returns:
            True if lesson is a duplicate
        """
        pattern = self._extract_pattern(lesson)
        if pattern is None:
            # Can't extract pattern, allow through
            return False
        
        return pattern in self._pattern_cache
    
    def get_similar_lessons(self, lesson: str) -> List[str]:
        """
        Find existing lessons similar to the given one.
        
        Args:
            lesson: Lesson to find matches for
            
        Returns:
            List of similar existing lessons
        """
        pattern = self._extract_pattern(lesson)
        if pattern is None:
            return []
        
        similar = []
        if pattern in self._pattern_cache:
            similar.append(self._pattern_cache[pattern])
        
        # Also check for partial matches
        pattern_parts = pattern.split('_')
        for existing_pattern, existing_lesson in self._pattern_cache.items():
            if existing_pattern == pattern:
                continue  # Already added above
            existing_parts = existing_pattern.split('_')
            # If symbol matches, could be related
            if len(pattern_parts) > 1 and len(existing_parts) > 1:
                if pattern_parts[-1] == existing_parts[-1]:  # Same symbol
                    similar.append(existing_lesson)
        
        return similar[:5]  # Limit to 5
    
    def add_lesson(self, lesson: str):
        """
        Add a lesson to the tracked set.
        
        Call this after writing a new lesson to KB.
        
        Args:
            lesson: Lesson that was written
        """
        pattern = self._extract_pattern(lesson)
        if pattern:
            self._pattern_cache[pattern] = lesson
            self.existing_lessons.append(lesson)
    
    def filter_duplicates(self, lessons: List[str]) -> Tuple[List[str], List[str]]:
        """
        Filter a list of lessons to remove duplicates.
        
        Args:
            lessons: List of new lessons to filter
            
        Returns:
            Tuple of (unique lessons, duplicate lessons)
        """
        unique = []
        duplicates = []
        
        # Track patterns seen in this batch too
        batch_patterns = set()
        
        for lesson in lessons:
            pattern = self._extract_pattern(lesson)
            
            if pattern is None:
                # Can't check, allow through
                unique.append(lesson)
                continue
            
            if pattern in self._pattern_cache:
                duplicates.append(lesson)
                logger.debug(f"Duplicate lesson (matches KB): {lesson[:50]}...")
                continue
            
            if pattern in batch_patterns:
                duplicates.append(lesson)
                logger.debug(f"Duplicate lesson (matches batch): {lesson[:50]}...")
                continue
            
            batch_patterns.add(pattern)
            unique.append(lesson)
        
        logger.info(f"Deduplicated lessons: {len(unique)} unique, {len(duplicates)} duplicates removed")
        return unique, duplicates
    
    def consolidate_similar(self, lessons: List[str]) -> List[str]:
        """
        Consolidate similar lessons into stronger statements.
        
        If the same pattern appears multiple times, combine into one
        more assertive rule.
        
        Args:
            lessons: List of lessons to consolidate
            
        Returns:
            List of consolidated lessons
        """
        pattern_groups: Dict[str, List[str]] = {}
        
        for lesson in lessons:
            pattern = self._extract_pattern(lesson)
            if pattern is None:
                continue
            if pattern not in pattern_groups:
                pattern_groups[pattern] = []
            pattern_groups[pattern].append(lesson)
        
        consolidated = []
        for pattern, group in pattern_groups.items():
            if len(group) == 1:
                consolidated.append(group[0])
            else:
                # Multiple lessons with same pattern - take the most specific one
                # (longest lesson is usually most detailed)
                best = max(group, key=len)
                consolidated.append(best)
        
        return consolidated
