"""
KB Reader - reads and retrieves knowledge base entries for RAG.

Includes token-aware retrieval with priority-based truncation.
Priority order: Ad-hoc Strategies > Never Repeat > KB Rules > History > Mistakes
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from .strategies import StrategyReader

logger = logging.getLogger(__name__)

# Token budget per section (chars, assuming ~4 chars/token)
SECTION_BUDGETS = {
    'adhoc_strategies': 1200,    # Highest priority
    'never_repeat': 1200,        # Critical mistakes
    'trading_rules': 800,        # Learned patterns
    'symbol_history': 600,       # Recent decisions
    'recent_summaries': 600,     # Past performance
    'mistakes': 400,             # Additional errors
}


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

        Priority order (highest to lowest):
        1. Ad-hoc Strategies (user-defined, highest priority)
        2. Never Repeat Rules (critical mistakes)
        3. Trading Rules (learned patterns)
        4. Symbol History (recent decisions)
        5. Recent Summaries (past performance)
        6. Mistakes Summary (additional errors)

        Uses token-aware retrieval to stay within budget while
        preserving highest priority content.

        Args:
            symbols: Stock symbols being considered today
            current_date: Current trading date (YYYY-MM-DD)
            max_history_days: Maximum number of past days to include
            max_context_chars: Maximum characters for context

        Returns:
            Formatted context string to include in AI prompt
        """
        sections = []
        remaining_budget = max_context_chars

        # 1. [HIGHEST] Ad-hoc Strategies - User-defined, always included first
        if self.strategy_reader.strategies_exist():
            strategies = self.strategy_reader.format_for_prompt(
                max_chars=SECTION_BUDGETS['adhoc_strategies']
            )
            if strategies:
                sections.append(("USER STRATEGIES", strategies))
                remaining_budget -= len(strategies) + 50  # 50 for header

        # 2. [CRITICAL] Never Repeat Rules - Must not repeat past mistakes
        if remaining_budget > 200:
            never_repeat = self._get_never_repeat_rules()
            if never_repeat:
                truncated = never_repeat[:min(len(never_repeat), SECTION_BUDGETS['never_repeat'])]
                sections.append(("[!] NEVER REPEAT - CRITICAL MISTAKES", truncated))
                remaining_budget -= len(truncated) + 50

        # 3. Trading rules from master index
        if remaining_budget > 200 and self.kb_exists():
            rules = self._get_trading_rules()
            if rules:
                truncated = rules[:min(len(rules), SECTION_BUDGETS['trading_rules'])]
                sections.append(("Trading Rules (from experience)", truncated))
                remaining_budget -= len(truncated) + 50

        # 4. Past decisions for these symbols
        if remaining_budget > 200 and self.kb_exists():
            symbol_history = self._get_symbol_history(symbols, current_date, limit=3)
            if symbol_history:
                truncated = symbol_history[:min(len(symbol_history), SECTION_BUDGETS['symbol_history'])]
                sections.append(("Past Decisions for These Symbols", truncated))
                remaining_budget -= len(truncated) + 50

        # 5. Recent daily summaries (lower priority)
        if remaining_budget > 200 and self.kb_exists():
            recent = self._get_recent_summaries(current_date, max_history_days)
            if recent:
                truncated = recent[:min(len(recent), SECTION_BUDGETS['recent_summaries'])]
                sections.append(("Recent Trading History", truncated))
                remaining_budget -= len(truncated) + 50

        # 6. Mistakes to avoid (lowest priority - included if space remains)
        if remaining_budget > 100 and self.kb_exists():
            mistakes = self._get_mistakes_summary()
            if mistakes:
                truncated = mistakes[:min(len(mistakes), remaining_budget - 50)]
                sections.append(("Mistakes to Avoid", truncated))

        # Build final context
        context = self._build_context(sections, max_context_chars)

        logger.debug(f"KB context built: {len(context)} chars, {len(sections)} sections")
        return context

    def _get_trading_rules(self) -> str:
        """Extract trading rules from master index."""
        master_path = self.kb_root / "master_index.md"
        if not master_path.exists():
            return ""

        try:
            content = master_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return ""  # Skip corrupted file

        # Extract rules section
        rules = []

        if "### Buy Rules" in content:
            buy_section = content.split("### Buy Rules")[1]
            buy_section = buy_section.split("###")[0]  # Until next section
            for line in buy_section.strip().split("\n"):
                if line.startswith("- [x]"):  # Only learned rules
                    rules.append(f"BUY: {line[6:].strip()}")

        if "### Sell Rules" in content:
            sell_section = content.split("### Sell Rules")[1]
            sell_section = sell_section.split("###")[0]
            for line in sell_section.strip().split("\n"):
                if line.startswith("- [x]"):
                    rules.append(f"SELL: {line[6:].strip()}")

        if "### Hold Rules" in content:
            hold_section = content.split("### Hold Rules")[1]
            hold_section = hold_section.split("##")[0]
            for line in hold_section.strip().split("\n"):
                if line.startswith("- [x]"):
                    rules.append(f"HOLD: {line[6:].strip()}")

        if not rules:
            return ""

        return "\n".join(rules)

    def _get_recent_summaries(self, current_date: str, max_days: int) -> str:
        """Get summaries from recent trading days."""
        sessions_dir = self.kb_root / "sessions"
        if not sessions_dir.exists():
            return ""

        # Get all session dates
        try:
            dates = sorted([
                d.name for d in sessions_dir.iterdir()
                if d.is_dir() and d.name < current_date
            ], reverse=True)
        except Exception:
            return ""

        if not dates:
            return ""

        summaries = []
        for date in dates[:max_days]:
            summary_path = sessions_dir / date / "daily_summary.md"
            if summary_path.exists():
                try:
                    content = summary_path.read_text(encoding='utf-8')
                except UnicodeDecodeError:
                    continue  # Skip corrupted file

                # Extract key metrics
                metrics = self._extract_metrics(content, date)
                if metrics:
                    summaries.append(metrics)

        if not summaries:
            return ""

        return "\n".join(summaries)

    def _extract_metrics(self, content: str, date: str) -> str:
        """Extract key metrics from daily summary."""
        lines = []
        lines.append(f"[{date}]")

        # Extract return
        for line in content.split("\n"):
            if "Day Return:" in line:
                lines.append(f"  Return: {line.split(':')[1].strip()}")
            elif "Average Skill Score:" in line:
                lines.append(f"  Skill: {line.split(':')[1].strip()}")
            elif "Average Luck Factor:" in line:
                lines.append(f"  Luck: {line.split(':')[1].strip()}")

        # Get lessons
        if "## Lessons for Tomorrow" in content:
            lessons_section = content.split("## Lessons for Tomorrow")[1]
            lessons_section = lessons_section.split("---")[0]
            for line in lessons_section.strip().split("\n"):
                if line.startswith("- "):
                    lines.append(f"  Lesson: {line[2:].strip()}")
                    break  # Just first lesson

        return "\n".join(lines) if len(lines) > 1 else ""

    def _get_symbol_history(
        self,
        symbols: List[str],
        current_date: str,
        limit: int = 3
    ) -> str:
        """Get past decisions for specific symbols."""
        sessions_dir = self.kb_root / "sessions"
        if not sessions_dir.exists():
            return ""

        history = {sym: [] for sym in symbols}

        try:
            dates = sorted([
                d.name for d in sessions_dir.iterdir()
                if d.is_dir() and d.name < current_date
            ], reverse=True)
        except Exception:
            return ""

        for date in dates[:10]:  # Search last 10 days
            decisions_path = sessions_dir / date / "decisions.json"
            if not decisions_path.exists():
                continue

            try:
                decisions = json.loads(decisions_path.read_text(encoding='utf-8'))
                for d in decisions:
                    sym = d.get('symbol')
                    if sym in history and len(history[sym]) < limit:
                        history[sym].append({
                            "date": date,
                            "action": d.get('action'),
                            "skill": d.get('skill_score'),
                            "profitable": d.get('profitable'),
                            "lesson": d.get('lesson', '')[:50]
                        })
            except Exception:
                continue

        # Format output
        lines = []
        for sym, entries in history.items():
            if entries:
                lines.append(f"{sym}:")
                for e in entries:
                    status = "[Y]" if e['profitable'] else "[N]"
                    lines.append(
                        f"  [{e['date']}] {e['action']} (skill:{e['skill']}) {status}"
                    )

        return "\n".join(lines)

    def _get_mistakes_summary(self) -> str:
        """Get summary of mistakes to avoid."""
        mistakes_path = self.kb_root / "patterns" / "mistakes.md"
        if not mistakes_path.exists():
            return ""

        try:
            content = mistakes_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return ""  # Skip corrupted file

        # Extract recent critical errors
        mistakes = []

        if "## Critical Errors" in content:
            errors_section = content.split("## Critical Errors")[1]
            errors_section = errors_section.split("##")[0]

            # Parse table rows
            for line in errors_section.split("\n"):
                if line.startswith("|") and "Date" not in line and "---" not in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 5:
                        mistakes.append(f"- {parts[2]}: {parts[3]}")

        if not mistakes:
            return ""

        return "Recent errors to avoid:\n" + "\n".join(mistakes[:5])

    def _get_never_repeat_rules(self) -> str:
        """
        Get assertive Never Repeat Rules from trade_errors.md.

        Returns rules formatted with strong warning language to prevent
        the AI from making the same mistakes.
        """
        errors_path = self.kb_root / "patterns" / "trade_errors.md"
        if not errors_path.exists():
            return ""

        try:
            content = errors_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return ""

        if "## Never Repeat Rules" not in content:
            return ""

        rules_section = content.split("## Never Repeat Rules")[1]
        # Split on footer marker (standalone ---) not table separator (|---|)
        if "\n---\n" in rules_section:
            rules_section = rules_section.split("\n---\n")[0]
        elif "\n---" in rules_section and "|---" not in rules_section.split("\n---")[0][-10:]:
            rules_section = rules_section.split("\n---")[0]

        # Parse table rows and format assertively
        rules = []
        seen_patterns = set()

        for line in rules_section.split("\n"):
            if line.startswith("|") and "Pattern" not in line and "---" not in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4:
                    pattern = parts[1]  # e.g., "[CRITICAL] BUY NVDA when skill<60"
                    reason = parts[2]   # e.g., "Failed 18x - Bad decision + bad luck"

                    # Extract core pattern to avoid duplicates in output
                    core_pattern = pattern.replace("[CRITICAL] ", "").strip()
                    if core_pattern in seen_patterns:
                        continue
                    seen_patterns.add(core_pattern)

                    # Format assertively
                    if "[CRITICAL]" in pattern or "Failed" in reason:
                        rules.append(f"[!] NEVER: {core_pattern} - {reason}")
                    else:
                        rules.append(f"[WARNING] AVOID: {core_pattern} - {reason}")

        if not rules:
            return ""

        header = "[!][!] CRITICAL RULES - DO NOT VIOLATE [!][!]\n"
        return header + "\n".join(rules[:10])  # Limit to top 10 most important

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
