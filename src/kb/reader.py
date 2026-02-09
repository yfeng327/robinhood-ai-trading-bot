"""
KB Reader - reads and retrieves knowledge base entries for RAG.

Uses LLM synthesis to build context from all KB files.
Falls back to strategies-only context when LLM is unavailable.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from .strategies import StrategyReader

logger = logging.getLogger(__name__)

# Budget for ad-hoc strategies section (chars)
STRATEGIES_BUDGET = 1200



class KBReader:
    """
    Reads and retrieves knowledge base entries for RAG-style context building.

    Retrieval Strategy:
    1. Always include master_index.md (rules and patterns)
    2. Include last N daily summaries for recent context
    3. Search for relevant past decisions by symbol
    4. Include mistakes.md for error avoidance
    """

    def __init__(self, kb_root: str = "kb"):
        self.kb_root = Path(kb_root)
        self.strategy_reader = StrategyReader(kb_root)

    def kb_exists(self) -> bool:
        """Check if KB has been initialized."""
        return (self.kb_root / "master_index.md").exists()

    def get_context_for_trading(
        self,
        symbols: List[str],
        current_date: str,
        max_history_days: int = 5,
        max_context_chars: int = 4800
    ) -> str:
        """
        Build context string for AI trading decisions.

        Uses LLM to synthesize relevant context from all KB files.
        Falls back to keyword-based extraction if LLM fails.

        Args:
            symbols: Stock symbols being considered today
            current_date: Current trading date (YYYY-MM-DD)
            max_history_days: Maximum number of past days to include
            max_context_chars: Maximum characters for context

        Returns:
            Formatted context string to include in AI prompt
        """
        # User strategies are always included verbatim (highest priority, structured)
        strategies_text = ""
        if self.strategy_reader.strategies_exist():
            strategies_text = self.strategy_reader.format_for_prompt(
                max_chars=STRATEGIES_BUDGET
            )

        # Try LLM-based synthesis
        try:
            kb_content = self._gather_kb_content(current_date, max_history_days)
            if kb_content:
                llm_budget = max_context_chars - len(strategies_text) - 100
                synthesized = self._llm_synthesize_context(
                    kb_content, symbols, current_date, llm_budget
                )
                if synthesized:
                    sections = []
                    if strategies_text:
                        sections.append(("USER STRATEGIES", strategies_text))
                    sections.append(("KB INSIGHTS", synthesized))
                    context = self._build_context(sections, max_context_chars)
                    logger.info(f"KB context built via LLM: {len(context)} chars")
                    return context
        except Exception as e:
            logger.warning(f"LLM KB synthesis failed: {e}")

        # Fallback: return just user strategies (no brittle keyword parsing)
        if strategies_text:
            return self._build_context([("USER STRATEGIES", strategies_text)], max_context_chars)
        return ""

    def _gather_kb_content(self, current_date: str, max_history_days: int) -> Dict[str, str]:
        """
        Read all KB files into a dict for LLM synthesis.

        Returns:
            Dict mapping filename to content string. Empty files are excluded.
        """
        content = {}
        max_file_chars = 20000  # Truncate individual files

        # Master index (rules, patterns, stats)
        master_path = self.kb_root / "master_index.md"
        if master_path.exists():
            try:
                text = master_path.read_text(encoding='utf-8')
                if text.strip():
                    content["master_index.md"] = text[:max_file_chars]
            except UnicodeDecodeError:
                pass

        # Trade errors (never repeat rules)
        errors_path = self.kb_root / "patterns" / "trade_errors.md"
        if errors_path.exists():
            try:
                text = errors_path.read_text(encoding='utf-8')
                if text.strip():
                    content["trade_errors.md"] = text[:max_file_chars]
            except UnicodeDecodeError:
                pass

        # Mistakes
        mistakes_path = self.kb_root / "patterns" / "mistakes.md"
        if mistakes_path.exists():
            try:
                text = mistakes_path.read_text(encoding='utf-8')
                if text.strip():
                    content["mistakes.md"] = text[:max_file_chars]
            except UnicodeDecodeError:
                pass

        # Lessons learned
        lessons_path = self.kb_root / "lessons_learned.md"
        if lessons_path.exists():
            try:
                text = lessons_path.read_text(encoding='utf-8')
                if text.strip():
                    content["lessons_learned.md"] = text[:max_file_chars]
            except UnicodeDecodeError:
                pass

        # Recent sessions (daily summaries + decisions)
        sessions_dir = self.kb_root / "sessions"
        if sessions_dir.exists():
            try:
                dates = sorted([
                    d.name for d in sessions_dir.iterdir()
                    if d.is_dir() and d.name < current_date
                ], reverse=True)
            except Exception:
                dates = []

            for date in dates[:max_history_days]:
                # Daily summary
                summary_path = sessions_dir / date / "daily_summary.md"
                if summary_path.exists():
                    try:
                        text = summary_path.read_text(encoding='utf-8')
                        if text.strip():
                            content[f"sessions/{date}/daily_summary.md"] = text[:max_file_chars]
                    except UnicodeDecodeError:
                        pass

                # Decisions JSON
                decisions_path = sessions_dir / date / "decisions.json"
                if decisions_path.exists():
                    try:
                        text = decisions_path.read_text(encoding='utf-8')
                        if text.strip():
                            content[f"sessions/{date}/decisions.json"] = text[:max_file_chars]
                    except UnicodeDecodeError:
                        pass

        return content

    def _llm_synthesize_context(
        self,
        kb_content: Dict[str, str],
        symbols: List[str],
        current_date: str,
        max_chars: int
    ) -> str:
        """
        Use LLM to synthesize relevant trading context from raw KB files.

        Args:
            kb_content: Dict of {filename: content}
            symbols: Symbols being traded today
            current_date: Current date
            max_chars: Maximum characters for output

        Returns:
            Synthesized context string, or empty string on failure
        """
        from src.api import ai

        # Build the KB dump for the prompt
        kb_dump = ""
        for filename, text in kb_content.items():
            kb_dump += f"\n--- {filename} ---\n{text}\n"

        symbols_str = ", ".join(symbols) if symbols else "all watchlist symbols"

        prompt = f"""You are a trading knowledge base assistant. Today is {current_date}.
The trader is considering these symbols: {symbols_str}

Below is the full content of the trading knowledge base. Your job is to extract and synthesize
the most relevant information for today's trading decisions.

{kb_dump}

Produce a concise trading context summary (max {max_chars} characters) with these sections:

1. **CRITICAL RULES** - Any "never repeat" rules, critical errors, or blocked actions relevant to these symbols
2. **LEARNED PATTERNS** - Buy/sell/hold rules that have worked before, especially for these symbols
3. **RECENT HISTORY** - Key metrics from recent trading days (returns, skill scores, lessons)
4. **SYMBOL CONTEXT** - Past decisions and outcomes for the specific symbols being considered

Rules:
- Be concise and assertive. Use imperative language ("NEVER buy X when...", "SELL when...")
- Prioritize actionable rules over general observations
- If a mistake has been repeated multiple times, emphasize it strongly
- Include specific numbers (skill scores, P/L, dates) when relevant
- Skip sections that have no relevant content
- Do NOT wrap in markdown code blocks
- Output plain markdown text only"""

        response = ai.make_ai_request(prompt)
        result = ai.get_raw_response_content(response)

        # Truncate to budget
        if len(result) > max_chars:
            result = result[:max_chars - 3] + "..."

        return result

    def _build_context(
        self,
        sections: List[Tuple[str, str]],
        max_chars: int
    ) -> str:
        """Build final context string within character limit."""
        if not sections:
            return ""

        header = "## Knowledge Base Context (from past trading)\n"
        header += "The following is learned from previous trading sessions:\n\n"

        content_parts = []
        current_length = len(header)

        for title, content in sections:
            section_text = f"### {title}\n{content}\n\n"

            if current_length + len(section_text) <= max_chars:
                content_parts.append(section_text)
                current_length += len(section_text)
            else:
                # Try to fit partial content
                remaining = max_chars - current_length - 50
                if remaining > 100:
                    truncated = content[:remaining] + "..."
                    content_parts.append(f"### {title}\n{truncated}\n\n")
                break

        if not content_parts:
            return ""

        return header + "".join(content_parts)

    def get_past_patterns(self, symbols: List[str], limit: int = 20) -> List[Dict]:
        """
        Get past decision patterns for pattern matching in analyzer.

        Args:
            symbols: Symbols to get patterns for
            limit: Maximum patterns to return

        Returns:
            List of past decision dicts with outcomes
        """
        patterns = []
        sessions_dir = self.kb_root / "sessions"

        if not sessions_dir.exists():
            return patterns

        try:
            dates = sorted([
                d.name for d in sessions_dir.iterdir()
                if d.is_dir()
            ], reverse=True)
        except Exception:
            return patterns

        for date in dates:
            if len(patterns) >= limit:
                break

            decisions_path = sessions_dir / date / "decisions.json"
            if not decisions_path.exists():
                continue

            try:
                decisions = json.loads(decisions_path.read_text(encoding='utf-8'))
                for d in decisions:
                    if d.get('symbol') in symbols:
                        patterns.append(d)
            except Exception:
                continue

        return patterns[:limit]

    def get_statistics(self) -> Dict:
        """Get overall KB statistics."""
        stats = {
            "total_days": 0,
            "total_decisions": 0,
            "avg_skill_score": 0,
            "avg_luck_factor": 0,
            "win_rate": 0
        }

        sessions_dir = self.kb_root / "sessions"
        if not sessions_dir.exists():
            return stats

        all_decisions = []

        try:
            for session_dir in sessions_dir.iterdir():
                if not session_dir.is_dir():
                    continue

                stats["total_days"] += 1

                decisions_path = session_dir / "decisions.json"
                if decisions_path.exists():
                    decisions = json.loads(decisions_path.read_text(encoding='utf-8'))
                    all_decisions.extend(decisions)
        except Exception:
            return stats

        if all_decisions:
            stats["total_decisions"] = len(all_decisions)
            stats["avg_skill_score"] = sum(d.get('skill_score', 0) for d in all_decisions) / len(all_decisions)
            stats["avg_luck_factor"] = sum(d.get('luck_factor', 0) for d in all_decisions) / len(all_decisions)

            profitable = sum(1 for d in all_decisions if d.get('profitable'))
            stats["win_rate"] = profitable / len(all_decisions) * 100

        return stats
