"""
KB Writer - writes daily trading summaries and updates knowledge base.

Includes LLM-based compaction to prevent duplicate entries and consolidate
similar patterns into assertive, actionable rules.
"""

import os
import json
import re
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from .analyzer import DecisionAnalysis

logger = logging.getLogger(__name__)

# Section limits to prevent unbounded growth
SECTION_LIMITS = {
    'buy_rules': 15,
    'sell_rules': 15,
    'hold_rules': 10,
    'recent_lessons': 5,
    'what_works': 20,
    'what_doesnt_work': 20,
    'market_patterns': 10,
    'critical_errors': 25,
    'buying_errors': 15,
    'selling_errors': 15,
    'never_repeat_per_symbol': 3,
    'error_frequency': 15,
    'high_confidence_patterns': 20,
}


class KBWriter:
    """
    Writes trading knowledge base entries.

    Directory Structure:
    kb/
    ├── master_index.md           # Aggregated learnings, patterns, rules
    ├── lessons_learned.md        # Accumulated wisdom from all days
    ├── sessions/
    │   └── YYYY-MM-DD/
    │       ├── daily_summary.md  # Performance + decisions
    │       ├── decisions.json    # Structured decision data
    │       └── analysis.md       # Luck vs skill breakdown
    └── patterns/
        ├── bullish_patterns.md
        ├── bearish_patterns.md
        └── mistakes.md
    """

    def __init__(self, kb_root: str = "kb"):
        self.kb_root = Path(kb_root)
        self._ensure_structure()
        # Track patterns seen to avoid duplicates within a session
        self._seen_patterns: Dict[str, set] = {
            'buy_rules': set(),
            'sell_rules': set(),
            'never_repeat': set(),
            'critical_errors': set(),
        }

    def _ensure_structure(self):
        """Create KB directory structure if it doesn't exist."""
        dirs = [
            self.kb_root,
            self.kb_root / "sessions",
            self.kb_root / "patterns"
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        # Create master files if they don't exist
        master_index = self.kb_root / "master_index.md"
        if not master_index.exists():
            self._write_initial_master_index()

        lessons = self.kb_root / "lessons_learned.md"
        if not lessons.exists():
            self._write_initial_lessons()

        mistakes = self.kb_root / "patterns" / "mistakes.md"
        if not mistakes.exists():
            self._write_initial_mistakes()

    def _write_initial_master_index(self):
        """Write initial master index file."""
        content = """# Trading Knowledge Base - Master Index

## Overview
This file contains aggregated learnings, patterns, and rules from all trading days.
The AI should consult this before making decisions.

## Trading Rules (Learned from Experience)
<!-- Rules added automatically based on successful patterns -->

### Buy Rules
- [ ] No rules learned yet

### Sell Rules
- [ ] No rules learned yet

### Hold Rules
- [ ] No rules learned yet

## High-Confidence Patterns
<!-- Patterns that have worked multiple times -->

| Pattern | Success Rate | Occurrences | Description |
|---------|--------------|-------------|-------------|

## Key Statistics
- Total Trading Days: 0
- Total Decisions Analyzed: 0
- Average Skill Score: N/A
- Average Luck Factor: N/A

## Recent Lessons (Last 5)
<!-- Most recent learnings -->

---
*Last updated: Never*
"""
        (self.kb_root / "master_index.md").write_text(content, encoding='utf-8')

    def _write_initial_lessons(self):
        """Write initial lessons learned file."""
        content = """# Lessons Learned

## Accumulated Wisdom
<!-- Key insights from all trading days -->

### What Works
- (No entries yet)

### What Doesn't Work
- (No entries yet)

### Market Patterns Observed
- (No entries yet)

## Decision Quality Trends
<!-- How decision quality has improved over time -->

| Date | Avg Skill Score | Avg Luck Factor | Key Learning |
|------|-----------------|-----------------|--------------|

---
*This file is updated after each trading day with significant learnings.*
"""
        (self.kb_root / "lessons_learned.md").write_text(content, encoding='utf-8')

    def _write_initial_mistakes(self):
        """Write initial mistakes file."""
        content = """# Trading Mistakes to Avoid

## Critical Errors
<!-- Mistakes that led to significant losses -->

| Date | Symbol | Error | Loss | Lesson |
|------|--------|-------|------|--------|

## Common Pitfalls
<!-- Recurring errors to watch for -->

### Buying Errors
- (No entries yet)

### Selling Errors
- (No entries yet)

### Timing Errors
- (No entries yet)

## Error Frequency
| Error Type | Occurrences | Last Seen |
|------------|-------------|-----------|

---
*Consult this file before every trading decision.*
"""
        (self.kb_root / "patterns" / "mistakes.md").write_text(content, encoding='utf-8')

    # =========================================================================
    # DEDUPLICATION HELPERS
    # =========================================================================

    def _extract_pattern_key(self, entry: str, entry_type: str) -> str:
        """
        Extract a normalized pattern key for deduplication.

        Extracts symbol + action + core condition from entries like:
        - "[2025-10-22] NVDA: BUY worked (skill=58...)" -> "BUY_NVDA"
        - "BUY NVDA when skill<60" -> "BUY_NVDA_skill<60"
        """
        entry_upper = entry.upper()

        # Extract symbol (NVDA, TQQQ, IREN, AMAT, etc.)
        symbols = ['NVDA', 'TQQQ', 'IREN', 'AMAT', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'TSLA']
        symbol = None
        for sym in symbols:
            if sym in entry_upper:
                symbol = sym
                break

        if not symbol:
            # Try to extract any ticker-like pattern
            match = re.search(r'\b([A-Z]{2,5})\b', entry_upper)
            symbol = match.group(1) if match else 'UNKNOWN'

        # Extract action
        action = 'UNKNOWN'
        if 'BUY' in entry_upper:
            action = 'BUY'
        elif 'SELL' in entry_upper:
            action = 'SELL'
        elif 'HOLD' in entry_upper:
            action = 'HOLD'

        # Extract condition for never-repeat rules
        condition = ''
        if 'SKILL<60' in entry_upper.replace(' ', '') or 'SKILL < 60' in entry_upper:
            condition = '_skill<60'
        elif 'SKILL<50' in entry_upper.replace(' ', ''):
            condition = '_skill<50'

        return f"{action}_{symbol}{condition}"

    def _is_duplicate_pattern(self, entry: str, entry_type: str, existing_content: str) -> bool:
        """
        Check if entry is a duplicate of existing patterns.

        Uses pattern extraction to detect semantic duplicates like:
        - "BUY NVDA when skill<60" on 2025-10-13
        - "BUY NVDA when skill<60" on 2025-09-16
        These are the SAME pattern, just different dates.
        """
        pattern_key = self._extract_pattern_key(entry, entry_type)

        # Check in-session cache first
        if entry_type in self._seen_patterns:
            if pattern_key in self._seen_patterns[entry_type]:
                return True
            self._seen_patterns[entry_type].add(pattern_key)

        # Check existing content for similar patterns
        existing_keys = set()
        for line in existing_content.split('\n'):
            if line.strip() and not line.startswith('#') and not line.startswith('|--'):
                existing_key = self._extract_pattern_key(line, entry_type)
                existing_keys.add(existing_key)

        return pattern_key in existing_keys

    def _count_entries_in_section(self, content: str, section_marker: str, end_marker: str = '###') -> int:
        """Count the number of entries in a markdown section."""
        if section_marker not in content:
            return 0

        section = content.split(section_marker)[1]
        if end_marker in section:
            section = section.split(end_marker)[0]

        # Count list items or table rows
        count = 0
        for line in section.strip().split('\n'):
            line = line.strip()
            if line.startswith('- [') or line.startswith('- (No entries'):
                count += 1
            elif line.startswith('|') and '---' not in line and 'Date' not in line and 'Pattern' not in line:
                count += 1

        return count

    def _enforce_section_limit(self, content: str, section_marker: str, end_marker: str, max_entries: int) -> str:
        """
        Enforce maximum entries in a section by removing oldest entries.
        Keeps the most recent entries (assumes newest are at top).
        """
        if section_marker not in content:
            return content

        parts = content.split(section_marker)
        before_section = parts[0]
        section_and_after = parts[1]

        if end_marker in section_and_after:
            section_parts = section_and_after.split(end_marker, 1)
            section_content = section_parts[0]
            after_section = end_marker + section_parts[1]
        else:
            section_content = section_and_after
            after_section = ''

        # Split into lines and keep only max_entries
        lines = section_content.strip().split('\n')
        entry_lines = []
        non_entry_lines = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('- [') or (stripped.startswith('|') and '---' not in stripped and 'Date' not in stripped and 'Pattern' not in stripped and 'Error' not in stripped):
                entry_lines.append(line)
            else:
                non_entry_lines.append(line)

        # Keep only the first max_entries (most recent since we prepend)
        trimmed_entries = entry_lines[:max_entries]

        # Rebuild section
        rebuilt_section = '\n'.join(non_entry_lines + trimmed_entries)

        return before_section + section_marker + rebuilt_section + '\n' + after_section

    # =========================================================================
    # LLM-BASED COMPACTION
    # =========================================================================

    def compact_kb_files(self):
        """
        Run end-of-day compaction using LLM to consolidate duplicate entries.

        This method:
        1. Reads each KB file
        2. Identifies sections with excessive entries
        3. Uses LLM to consolidate similar entries into assertive rules
        4. Rewrites files with compacted content
        """
        try:
            self._compact_trade_errors()
            self._compact_master_index()
            self._compact_lessons_learned()
            self._compact_mistakes()
            logger.info("KB compaction completed successfully")
        except Exception as e:
            logger.error(f"KB compaction failed: {e}")

    def _compact_trade_errors(self):
        """Compact trade_errors.md Never Repeat Rules using pattern consolidation."""
        errors_path = self.kb_root / "patterns" / "trade_errors.md"
        if not errors_path.exists():
            return

        try:
            content = errors_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return

        # Extract Never Repeat Rules section
        if "## Never Repeat Rules" not in content:
            return

        before_section = content.split("## Never Repeat Rules")[0]
        section_and_after = content.split("## Never Repeat Rules")[1]

        # Find the table
        if "| Pattern | Why to Avoid | Learned On |" not in section_and_after:
            return

        # Extract all table rows
        lines = section_and_after.split('\n')
        header_lines = []
        data_rows = []
        after_table = []
        in_table = False
        table_done = False

        for line in lines:
            if '| Pattern |' in line:
                header_lines.append(line)
                in_table = True
            elif '|------' in line and in_table:
                header_lines.append(line)
            elif line.startswith('|') and in_table and not table_done:
                data_rows.append(line)
            elif in_table and not line.startswith('|'):
                table_done = True
                after_table.append(line)
            elif table_done:
                after_table.append(line)

        # Consolidate duplicates by pattern
        pattern_counts: Dict[str, Tuple[str, str, int, str, str]] = {}  # key -> (pattern, reason, count, first_date, last_date)

        for row in data_rows:
            parts = [p.strip() for p in row.split('|')]
            if len(parts) >= 4:
                pattern = parts[1]  # e.g., "BUY NVDA when skill<60"
                reason = parts[2]   # e.g., "Bad decision + bad luck"
                date = parts[3]     # e.g., "2025-10-13"

                key = self._extract_pattern_key(pattern, 'never_repeat')

                if key in pattern_counts:
                    existing = pattern_counts[key]
                    # Update count and date range
                    new_count = existing[2] + 1
                    first_date = min(existing[3], date) if existing[3] else date
                    last_date = max(existing[4], date) if existing[4] else date
                    pattern_counts[key] = (pattern, reason, new_count, first_date, last_date)
                else:
                    pattern_counts[key] = (pattern, reason, 1, date, date)

        # Build consolidated table with assertive language
        new_rows = []
        for key, (pattern, reason, count, first_date, last_date) in sorted(
            pattern_counts.items(),
            key=lambda x: -x[1][2]  # Sort by count descending
        )[:SECTION_LIMITS['never_repeat_per_symbol'] * 5]:  # Allow 5 symbols * 3 patterns
            if count > 1:
                # Consolidate with count
                date_range = f"{first_date[:7]} to {last_date[:7]}" if first_date != last_date else first_date
                assertive_pattern = f"[CRITICAL] {pattern}"
                assertive_reason = f"Failed {count}x - {reason}"
                new_rows.append(f"| {assertive_pattern} | {assertive_reason} | {date_range} |")
            else:
                new_rows.append(f"| {pattern} | {reason} | {first_date} |")

        # Rebuild content
        new_table = '\n'.join(header_lines + new_rows)
        new_content = before_section + "## Never Repeat Rules\n<!-- Actions that should NEVER be repeated based on past failures -->\n\n" + new_table + '\n' + '\n'.join(after_table)

        errors_path.write_text(new_content, encoding='utf-8')
        logger.debug(f"Compacted trade_errors.md: {len(data_rows)} rows -> {len(new_rows)} consolidated rules")

    def _compact_master_index(self):
        """Compact master_index.md by enforcing section limits."""
        master_path = self.kb_root / "master_index.md"
        if not master_path.exists():
            return

        try:
            content = master_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return

        # Enforce limits on Recent Lessons
        content = self._enforce_section_limit(
            content,
            "## Recent Lessons (Last 5)\n<!-- Most recent learnings -->\n",
            "---",
            SECTION_LIMITS['recent_lessons']
        )

        # Enforce limits on Buy Rules
        content = self._enforce_section_limit(
            content,
            "### Buy Rules\n",
            "### Sell Rules",
            SECTION_LIMITS['buy_rules']
        )

        # Enforce limits on Sell Rules
        content = self._enforce_section_limit(
            content,
            "### Sell Rules\n",
            "### Hold Rules",
            SECTION_LIMITS['sell_rules']
        )

        master_path.write_text(content, encoding='utf-8')

    def _compact_lessons_learned(self):
        """Compact lessons_learned.md by enforcing section limits."""
        lessons_path = self.kb_root / "lessons_learned.md"
        if not lessons_path.exists():
            return

        try:
            content = lessons_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return

        # Enforce limits on What Works
        content = self._enforce_section_limit(
            content,
            "### What Works\n",
            "### What Doesn't Work",
            SECTION_LIMITS['what_works']
        )

        # Enforce limits on What Doesn't Work
        content = self._enforce_section_limit(
            content,
            "### What Doesn't Work\n",
            "### Market Patterns",
            SECTION_LIMITS['what_doesnt_work']
        )

        # Enforce limits on Market Patterns
        content = self._enforce_section_limit(
            content,
            "### Market Patterns Observed\n",
            "## Decision Quality",
            SECTION_LIMITS['market_patterns']
        )

        lessons_path.write_text(content, encoding='utf-8')

    def _compact_mistakes(self):
        """Compact mistakes.md by enforcing section limits."""
        mistakes_path = self.kb_root / "patterns" / "mistakes.md"
        if not mistakes_path.exists():
            return

        try:
            content = mistakes_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return

        # Enforce limits on Buying Errors
        content = self._enforce_section_limit(
            content,
            "### Buying Errors\n",
            "### Selling Errors",
            SECTION_LIMITS['buying_errors']
        )

        # Enforce limits on Selling Errors
        content = self._enforce_section_limit(
            content,
            "### Selling Errors\n",
            "### Timing Errors",
            SECTION_LIMITS['selling_errors']
        )

        mistakes_path.write_text(content, encoding='utf-8')

    def write_daily_summary(
        self,
        date: str,
        starting_value: float,
        ending_value: float,
        analyses: List[DecisionAnalysis],
        portfolio_holdings: Dict[str, float],
        cash: float
    ):
        """
        Write the daily trading summary.

        Args:
            date: Trading date (YYYY-MM-DD)
            starting_value: Portfolio value at start of day
            ending_value: Portfolio value at end of day
            analyses: List of DecisionAnalysis for each decision
            portfolio_holdings: Current holdings {symbol: quantity}
            cash: Current cash balance
        """
        # Sanitize date for directory name (Windows paths can't have colons)
        dir_name = date.replace(":", "").replace(" ", "_")
        session_dir = self.kb_root / "sessions" / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)

        # Calculate metrics
        day_return = ((ending_value - starting_value) / starting_value * 100) if starting_value > 0 else 0
        total_trades = len([a for a in analyses if a.action in ['buy', 'sell']])

        avg_skill = sum(a.skill_score for a in analyses) / len(analyses) if analyses else 0
        avg_luck = sum(a.luck_factor for a in analyses) / len(analyses) if analyses else 0

        # Write daily_summary.md
        summary_content = self._generate_daily_summary(
            date, starting_value, ending_value, day_return,
            total_trades, analyses, avg_skill, avg_luck,
            portfolio_holdings, cash
        )
        (session_dir / "daily_summary.md").write_text(summary_content, encoding='utf-8')

        # Write decisions.json (with statistical quadrant data if available)
        decisions_data = []
        for a in analyses:
            decision_entry = {
                "symbol": a.symbol,
                "action": a.action,
                "quantity": a.quantity,
                "price": a.price,
                "skill_score": a.skill_score,
                "outcome_score": a.outcome_score,
                "total_score": a.total_score,
                "luck_factor": a.luck_factor,
                "profitable": a.profitable,
                "profit_loss": a.profit_loss,
                "lesson": a.lesson_learned
            }
            # Add statistical quadrant data if available
            if a.quadrant is not None:
                decision_entry.update({
                    "quadrant": a.quadrant,
                    "quadrant_label": a.quadrant_label,
                    "statistical_skill": a.statistical_skill_score,
                    "statistical_luck_pct": a.statistical_luck_pct,
                    "ks_statistic": a.ks_statistic,
                    "ks_p_value": a.ks_p_value,
                    "ad_statistic": a.ad_statistic,
                    "expected_return": a.expected_return,
                    "actual_return": a.actual_return,
                    "return_z_score": a.return_z_score,
                    "interpretation": a.quadrant_interpretation
                })
            decisions_data.append(decision_entry)

        (session_dir / "decisions.json").write_text(
            json.dumps(decisions_data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )

        # Write quadrant_analysis.md if statistical data available
        analyses_with_quadrants = [a for a in analyses if a.quadrant is not None]
        if analyses_with_quadrants:
            quadrant_content = self._generate_quadrant_analysis(date, analyses_with_quadrants)
            (session_dir / "quadrant_analysis.md").write_text(quadrant_content, encoding='utf-8')

        # Write analysis.md
        analysis_content = self._generate_analysis(date, analyses)
        (session_dir / "analysis.md").write_text(analysis_content, encoding='utf-8')

        # Update master index with rules and patterns
        self._update_master_index(date, analyses, avg_skill, avg_luck)

        # Update lessons learned (What Works / What Doesn't Work)
        self._update_lessons_if_significant(date, analyses)

        # Update mistakes (Common Pitfalls)
        self._update_mistakes(date, analyses)

        # Update trade errors (Never Repeat Rules)
        self._update_trade_errors(date, analyses)

        # Run end-of-day compaction to consolidate duplicates and enforce limits
        self.compact_kb_files()

    def _generate_daily_summary(
        self,
        date: str,
        starting_value: float,
        ending_value: float,
        day_return: float,
        total_trades: int,
        analyses: List[DecisionAnalysis],
        avg_skill: float,
        avg_luck: float,
        holdings: Dict[str, float],
        cash: float
    ) -> str:
        """Generate daily summary markdown."""

        # Build decisions table
        decisions_table = "| Symbol | Action | Qty | Price | P/L | Skill | Luck |\n"
        decisions_table += "|--------|--------|-----|-------|-----|-------|------|\n"

        for a in analyses:
            pl_str = f"${a.profit_loss:+.2f}" if a.profit_loss else "N/A"
            decisions_table += f"| {a.symbol} | {a.action} | {a.quantity:.4f} | ${a.price:.2f} | {pl_str} | {a.skill_score} | {a.luck_factor:.0%} |\n"

        # What went right
        right_list = "\n".join([f"- **{a.symbol}**: {a.what_went_right}" for a in analyses if a.profitable])
        if not right_list:
            right_list = "- No clearly successful decisions today"

        # What went wrong
        wrong_list = "\n".join([f"- **{a.symbol}**: {a.what_went_wrong}" for a in analyses if not a.profitable])
        if not wrong_list:
            wrong_list = "- No significant errors today"

        # Lessons for tomorrow
        lessons = set(a.lesson_learned for a in analyses)
        lessons_list = "\n".join([f"- {l}" for l in lessons])

        # Holdings
        holdings_str = "\n".join([f"- {sym}: {qty:.4f} shares" for sym, qty in holdings.items()])
        if not holdings_str:
            holdings_str = "- No holdings"

        return f"""# Trading Day Summary: {date}

## Performance Metrics
- **Starting Value:** ${starting_value:,.2f}
- **Ending Value:** ${ending_value:,.2f}
- **Day Return:** {day_return:+.2f}%
- **Trades Executed:** {total_trades}
- **Cash:** ${cash:,.2f}

## Current Holdings
{holdings_str}

## Decisions Made
{decisions_table}

## What Went Right
{right_list}

## What Went Wrong
{wrong_list}

## Luck vs Skill Analysis
- **Average Skill Score:** {avg_skill:.0f}/100
- **Average Luck Factor:** {avg_luck:.0%}
- **Interpretation:** {"Skill-driven day" if avg_luck < 0.3 else "Luck played significant role" if avg_luck > 0.5 else "Mixed skill and luck"}

## Lessons for Tomorrow
{lessons_list}

---
*Generated: {datetime.now().isoformat()}*
"""

    def _generate_analysis(self, date: str, analyses: List[DecisionAnalysis]) -> str:
        """Generate detailed luck vs skill analysis."""

        content = f"""# Luck vs Skill Analysis: {date}

## Decision Breakdown

"""
        for a in analyses:
            content += f"""### {a.symbol} - {a.action.upper()}

**Skill Components:**
- Indicator Alignment: {a.indicator_alignment}/30
- Position Sizing: {a.position_sizing}/20
- Risk/Reward Setup: {a.risk_reward}/25
- Pattern Match: {a.pattern_match}/25
- **Total Skill Score: {a.skill_score}/100**

**Outcome Components:**
- Profitable: {"Yes" if a.profitable else "No"}
- Beat Market: {"Yes" if a.beat_market else "No"}
- P/L: ${a.profit_loss:+.2f}
- **Total Outcome Score: {a.outcome_score}/100**

**Combined Analysis:**
- Total Score: {a.total_score}/100
- Luck Factor: {a.luck_factor:.0%}

**Assessment:** {self._get_assessment(a.skill_score, a.outcome_score, a.luck_factor)}

---

"""
        return content

    def _get_assessment(self, skill: int, outcome: int, luck: float) -> str:
        """Get assessment text based on scores."""
        if skill >= 70 and outcome >= 70:
            return "[OK] Excellent - Good process led to good outcome"
        elif skill >= 70 and outcome < 50:
            return "[!] Unlucky - Good process but poor outcome (don't change strategy)"
        elif skill < 50 and outcome >= 70:
            return "[LUCK] Lucky - Poor process but good outcome (don't repeat)"
        else:
            return "[FAIL] Poor - Needs improvement in both process and execution"

    def _generate_quadrant_analysis(self, date: str, analyses: List[DecisionAnalysis]) -> str:
        """
        Generate statistical quadrant analysis report.

        This is the KEY OUTPUT for absorption - clearly shows each decision's
        quadrant classification using KS/AD statistical tests.
        """
        from .luck_statistics import Quadrant

        content = f"""# Statistical Quadrant Analysis: {date}

## The Four Quadrants (KS/AD Statistical Analysis)

```
                    LUCK FAVORABLE
                          |
         Q2 ([-][+])      |        Q1 ([+][+])
      Wrong + Lucky       |     Right + Lucky
   -----------------------+----------------------
         Q4 ([-][-])      |        Q3 ([+][-])
   Wrong + Unlucky        |    Right + Unlucky
                          |
                   LUCK UNFAVORABLE
                    <-- DECISION WRONG    DECISION RIGHT -->
```

## Statistical Methods Used
- **Kolmogorov-Smirnov (KS) Test**: Compares returns to expected distribution (skill assessment)
- **Anderson-Darling (AD) Test**: Detects tail events and anomalies (luck detection)

## Today's Quadrant Summary

"""
        # Count quadrants
        q_counts = {"Q1_SKILL_LUCK": 0, "Q2_NOSKILL_LUCK": 0, "Q3_SKILL_NOLUCK": 0, "Q4_NOSKILL_NOLUCK": 0}
        for a in analyses:
            if a.quadrant in q_counts:
                q_counts[a.quadrant] += 1

        total = len(analyses)
        content += "| Quadrant | Count | % | Description |\n"
        content += "|----------|-------|---|-------------|\n"
        content += f"| [+][+] Q1 | {q_counts['Q1_SKILL_LUCK']} | {q_counts['Q1_SKILL_LUCK']/total*100:.0f}% | Right + Lucky (Ideal) |\n"
        content += f"| [-][+] Q2 | {q_counts['Q2_NOSKILL_LUCK']} | {q_counts['Q2_NOSKILL_LUCK']/total*100:.0f}% | Wrong + Lucky (WARNING) |\n"
        content += f"| [+][-] Q3 | {q_counts['Q3_SKILL_NOLUCK']} | {q_counts['Q3_SKILL_NOLUCK']/total*100:.0f}% | Right + Unlucky (Don't abandon) |\n"
        content += f"| [-][-] Q4 | {q_counts['Q4_NOSKILL_NOLUCK']} | {q_counts['Q4_NOSKILL_NOLUCK']/total*100:.0f}% | Wrong + Unlucky (Learn) |\n"

        content += "\n## Individual Decision Analysis\n\n"

        for a in analyses:
            # Build indicator based on quadrant
            quadrant_indicators = {
                "Q1_SKILL_LUCK": "[+][+]",
                "Q2_NOSKILL_LUCK": "[-][+]",
                "Q3_SKILL_NOLUCK": "[+][-]",
                "Q4_NOSKILL_NOLUCK": "[-][-]"
            }
            indicator = quadrant_indicators.get(a.quadrant, "[?][?]")

            # Safe formatting for potentially None statistical values
            skill_str = f"{a.statistical_skill_score:.0f}" if a.statistical_skill_score is not None else "N/A"
            luck_str = f"{a.statistical_luck_pct:.0f}" if a.statistical_luck_pct is not None else "N/A"
            exp_ret_str = f"{a.expected_return*100:+.2f}" if a.expected_return is not None else "N/A"
            act_ret_str = f"{a.actual_return*100:+.2f}" if a.actual_return is not None else "N/A"
            z_score_str = f"{a.return_z_score:+.2f}" if a.return_z_score is not None else "N/A"
            ks_str = f"{a.ks_statistic:.4f}" if a.ks_statistic is not None else "N/A"
            ks_p_str = f"{a.ks_p_value:.4f}" if a.ks_p_value is not None else "N/A"
            ad_str = f"{a.ad_statistic:.4f}" if a.ad_statistic is not None else "N/A"

            content += f"""### {a.symbol} ({a.action.upper()}) - {indicator} {a.quadrant}

**Quadrant:** {a.quadrant_label}

| Metric | Value |
|--------|-------|
| Decision Skill | {skill_str}/100 |
| Luck Percentage | {luck_str}% |
| Expected Return | {exp_ret_str}% |
| Actual Return | {act_ret_str}% |
| Return Z-Score | {z_score_str}σ |
| KS Statistic | {ks_str} (p={ks_p_str}) |
| AD Statistic | {ad_str} |

**Interpretation:** {a.quadrant_interpretation or 'No interpretation available'}

---

"""

        # Add key takeaways
        content += """## Key Takeaways for Tomorrow

"""
        # Q2 warning
        if q_counts['Q2_NOSKILL_LUCK'] > 0:
            content += f"[!] **WARNING:** {q_counts['Q2_NOSKILL_LUCK']} trade(s) were lucky despite poor decisions. Do NOT repeat these patterns.\n\n"

        # Q3 encouragement
        if q_counts['Q3_SKILL_NOLUCK'] > 0:
            content += f"[STRONG] **PERSIST:** {q_counts['Q3_SKILL_NOLUCK']} trade(s) were good decisions that got unlucky. Continue this approach.\n\n"

        # Q1 reinforcement
        if q_counts['Q1_SKILL_LUCK'] > 0:
            content += f"[OK] **REINFORCE:** {q_counts['Q1_SKILL_LUCK']} trade(s) showed both skill and favorable luck. Study these patterns.\n\n"

        # Q4 learning
        if q_counts['Q4_NOSKILL_NOLUCK'] > 0:
            content += f"[LEARN] **LEARN:** {q_counts['Q4_NOSKILL_NOLUCK']} trade(s) need improvement. Review the decision criteria.\n\n"

        content += f"---\n*Generated: {datetime.now().isoformat()}*\n"

        return content

    def _derive_lesson_text(self, date: str, analyses: List[DecisionAnalysis]) -> List[str]:
        """
        Generate actionable lesson entries from quadrant-classified decisions.

        Groups decisions by quadrant and generates max 2 concise lessons:
        - Q4 (wrong+unlucky): "LEARN: NVDA - Review setup criteria"
        - Q2 (wrong+lucky): "WARNING: TQQQ profited by luck, not skill"
        - Q3 (right+unlucky): "PERSIST: AMAT - good analysis got unlucky"
        - Q1 (right+lucky): "REINFORCE: VOO - skill+luck aligned"
        """
        lessons = []

        # Group by quadrant
        q4 = [a for a in analyses if getattr(a, 'quadrant', None) == 'Q4_NOSKILL_NOLUCK']
        q2 = [a for a in analyses if getattr(a, 'quadrant', None) == 'Q2_NOSKILL_LUCK']
        q3 = [a for a in analyses if getattr(a, 'quadrant', None) == 'Q3_SKILL_NOLUCK']
        q1 = [a for a in analyses if getattr(a, 'quadrant', None) == 'Q1_SKILL_LUCK']

        # Priority order: Q4 > Q2 > Q3 > Q1 (worst mistakes first)
        for a in q4[:1]:
            lessons.append(f"- [{date}] LEARN: {a.symbol} - Review setup criteria")
        for a in q2[:1]:
            lessons.append(f"- [{date}] WARNING: {a.symbol} profited by luck, not skill")
        for a in q3[:1]:
            lessons.append(f"- [{date}] PERSIST: {a.symbol} - good analysis got unlucky")
        for a in q1[:1]:
            lessons.append(f"- [{date}] REINFORCE: {a.symbol} - skill+luck aligned")

        # Cap at 2 lessons per cycle
        if lessons:
            return lessons[:2]

        # Fallback: use lesson_learned from most significant decision
        if analyses:
            best = max(analyses, key=lambda a: abs(a.profit_loss) if a.profit_loss else 0)
            if best.lesson_learned:
                return [f"- [{date}] {best.symbol}: {best.lesson_learned[:80]}"]

        return []

    def _update_master_index(
        self,
        date: str,
        analyses: List[DecisionAnalysis],
        avg_skill: float,
        avg_luck: float
    ):
        """Update master index with new learnings, rules, and patterns.

        Now includes duplicate checking to prevent bloat.
        """
        master_path = self.kb_root / "master_index.md"
        try:
            content = master_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            self._write_initial_master_index()
            content = master_path.read_text(encoding='utf-8')

        # === 1. Extract trading rules from good decisions (Q1/Q3 quadrants) ===
        # Only add if not a duplicate pattern
        for a in analyses:
            quadrant = getattr(a, 'quadrant', None)
            # Q1 (skill + luck) or Q3 (skill, no luck) = LEARNINGS - extract rules
            if quadrant in ['Q1_SKILL_LUCK', 'Q3_SKILL_NOLUCK'] or (a.skill_score >= 65 and a.profitable):
                rule = f"- [x] [{date}] {a.symbol}: {a.action.upper()} worked (skill={a.skill_score}, {a.lesson_learned[:60]})"

                # Check for duplicate before adding
                rule_type = 'buy_rules' if a.action == 'buy' else 'sell_rules'
                if self._is_duplicate_pattern(rule, rule_type, content):
                    logger.debug(f"Skipping duplicate rule: {a.action.upper()} {a.symbol}")
                    continue

                if a.action == 'buy' and "### Buy Rules\n- [ ] No rules learned yet" in content:
                    content = content.replace(
                        "### Buy Rules\n- [ ] No rules learned yet",
                        f"### Buy Rules\n{rule}"
                    )
                elif a.action == 'buy' and "### Buy Rules\n" in content:
                    content = content.replace("### Buy Rules\n", f"### Buy Rules\n{rule}\n")

                elif a.action == 'sell' and "### Sell Rules\n- [ ] No rules learned yet" in content:
                    content = content.replace(
                        "### Sell Rules\n- [ ] No rules learned yet",
                        f"### Sell Rules\n{rule}"
                    )
                elif a.action == 'sell' and "### Sell Rules\n" in content:
                    content = content.replace("### Sell Rules\n", f"### Sell Rules\n{rule}\n")

        # === 2. Update High-Confidence Patterns table ===
        # Only add if not a duplicate pattern
        for a in analyses:
            if a.skill_score >= 70:
                pattern_desc = f"{a.action.upper()} {a.symbol}"
                success = "Yes" if a.profitable else "No"

                # Check for duplicate pattern
                pattern_key = self._extract_pattern_key(pattern_desc, 'high_confidence')
                if pattern_key in self._seen_patterns.get('high_confidence', set()):
                    continue
                self._seen_patterns.setdefault('high_confidence', set()).add(pattern_key)

                new_pattern = f"\n| {pattern_desc} | {success} | 1 | Skill={a.skill_score}, Q={getattr(a, 'quadrant', 'N/A')[:2] if getattr(a, 'quadrant', None) else 'N/A'} |"

                if "| Pattern | Success Rate | Occurrences | Description |\n|---------|--------------|-------------|-------------|" in content:
                    content = content.replace(
                        "| Pattern | Success Rate | Occurrences | Description |\n|---------|--------------|-------------|-------------|",
                        f"| Pattern | Success Rate | Occurrences | Description |\n|---------|--------------|-------------|-------------|{new_pattern}"
                    )

        # === 3. Update Key Statistics ===
        # Count total sessions
        sessions_dir = self.kb_root / "sessions"
        total_days = len([d for d in sessions_dir.iterdir() if d.is_dir()]) if sessions_dir.exists() else 0
        total_decisions = len(analyses)

        # Update stats in content
        import re
        content = re.sub(r'Total Trading Days: \d+', f'Total Trading Days: {total_days}', content)
        content = re.sub(r'Total Decisions Analyzed: \d+', f'Total Decisions Analyzed: {total_decisions}+', content)
        content = re.sub(r'Average Skill Score: (N/A|\d+)', f'Average Skill Score: {avg_skill:.0f}', content)
        content = re.sub(r'Average Luck Factor: (N/A|\d+%?)', f'Average Luck Factor: {avg_luck:.0%}', content)

        # === 4. Add to Recent Lessons ===
        lesson_lines = self._derive_lesson_text(date, analyses)
        if lesson_lines and "## Recent Lessons (Last 5)" in content:
            lessons_block = "\n".join(lesson_lines)
            content = content.replace(
                "## Recent Lessons (Last 5)\n<!-- Most recent learnings -->\n",
                f"## Recent Lessons (Last 5)\n<!-- Most recent learnings -->\n{lessons_block}\n"
            )

        # === 5. Update timestamp ===
        content = content.replace(
            "*Last updated: Never*",
            f"*Last updated: {datetime.now().isoformat()}*"
        )
        content = re.sub(
            r'\*Last updated: [^*]+\*',
            f'*Last updated: {datetime.now().isoformat()}*',
            content,
            count=1
        )

        master_path.write_text(content, encoding='utf-8')

    def _update_lessons_if_significant(self, date: str, analyses: List[DecisionAnalysis]):
        """Update lessons learned based on quadrant classification."""
        lessons_path = self.kb_root / "lessons_learned.md"
        try:
            content = lessons_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            self._write_initial_lessons()
            content = lessons_path.read_text(encoding='utf-8')

        # === 1. Categorize by quadrant for What Works / What Doesn't Work ===
        for a in analyses:
            quadrant = getattr(a, 'quadrant', None)

            # Q1/Q3 = LEARNINGS (What Works) - skill was good
            if quadrant in ['Q1_SKILL_LUCK', 'Q3_SKILL_NOLUCK'] or a.skill_score >= 65:
                entry = f"- [{date}] {a.symbol} {a.action}: {a.lesson_learned[:80]}"
                if "### What Works\n- (No entries yet)" in content:
                    content = content.replace("### What Works\n- (No entries yet)", f"### What Works\n{entry}")
                elif "### What Works\n" in content:
                    content = content.replace("### What Works\n", f"### What Works\n{entry}\n")

            # Q2/Q4 = ERRORS (What Doesn't Work) - skill was poor
            elif quadrant in ['Q2_NOSKILL_LUCK', 'Q4_NOSKILL_NOLUCK'] or a.skill_score < 55:
                entry = f"- [{date}] {a.symbol} {a.action}: AVOID - {a.what_went_wrong[:60]}"
                if "### What Doesn't Work\n- (No entries yet)" in content:
                    content = content.replace("### What Doesn't Work\n- (No entries yet)", f"### What Doesn't Work\n{entry}")
                elif "### What Doesn't Work\n" in content:
                    content = content.replace("### What Doesn't Work\n", f"### What Doesn't Work\n{entry}\n")

        # === 2. Add market patterns from high-luck decisions ===
        high_luck = [a for a in analyses if a.luck_factor > 0.4]
        for a in high_luck:
            pattern = f"- [{date}] {a.symbol}: High luck factor ({a.luck_factor:.0%}) - market was volatile"
            if "### Market Patterns Observed\n- (No entries yet)" in content:
                content = content.replace("### Market Patterns Observed\n- (No entries yet)", f"### Market Patterns Observed\n{pattern}")
            elif "### Market Patterns Observed\n" in content and pattern not in content:
                content = content.replace("### Market Patterns Observed\n", f"### Market Patterns Observed\n{pattern}\n")

        # === 3. Update Decision Quality Trends table (ALL decisions, not just significant) ===
        avg_skill = sum(a.skill_score for a in analyses) / len(analyses) if analyses else 0
        avg_luck = sum(a.luck_factor for a in analyses) / len(analyses) if analyses else 0

        # Get dominant quadrant
        q_counts = {}
        for a in analyses:
            q = getattr(a, 'quadrant', None)
            if q is not None:  # Only count non-None quadrants
                q_counts[q] = q_counts.get(q, 0) + 1
        dominant_q = max(q_counts, key=q_counts.get) if q_counts else None

        # Safe formatting for dominant quadrant (handles None)
        dominant_str = dominant_q[:2] if dominant_q else 'N/A'
        new_entry = f"\n| {date} | {avg_skill:.0f} | {avg_luck:.0%} | Dominant: {dominant_str} |"
        if "| Date | Avg Skill Score | Avg Luck Factor | Key Learning |\n|------|-----------------|-----------------|--------------|" in content:
            content = content.replace(
                "| Date | Avg Skill Score | Avg Luck Factor | Key Learning |\n|------|-----------------|-----------------|--------------|",
                f"| Date | Avg Skill Score | Avg Luck Factor | Key Learning |\n|------|-----------------|-----------------|--------------|{new_entry}"
            )

        lessons_path.write_text(content, encoding='utf-8')

    def _update_mistakes(self, date: str, analyses: List[DecisionAnalysis]):
        """Update mistakes file based on Q2/Q4 quadrants (poor decisions)."""
        mistakes_path = self.kb_root / "patterns" / "mistakes.md"
        try:
            content = mistakes_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            self._write_initial_mistakes()
            content = mistakes_path.read_text(encoding='utf-8')

        # === 1. Add to Critical Errors table (Q2/Q4 or skill < 60) ===
        for a in analyses:
            quadrant = getattr(a, 'quadrant', None)

            # Q2 = got lucky with bad decision, Q4 = bad decision with bad luck
            is_error = quadrant in ['Q2_NOSKILL_LUCK', 'Q4_NOSKILL_NOLUCK'] or a.skill_score < 60

            if is_error:
                what_wrong = a.what_went_wrong[:40] if a.what_went_wrong else "Poor decision"
                lesson = a.lesson_learned[:40] if a.lesson_learned else "Review setup"

                new_entry = f"\n| {date} | {a.symbol} | {what_wrong} | ${a.profit_loss:.2f} | {lesson} |"
                if "| Date | Symbol | Error | Loss | Lesson |\n|------|--------|-------|------|--------|" in content:
                    content = content.replace(
                        "| Date | Symbol | Error | Loss | Lesson |\n|------|--------|-------|------|--------|",
                        f"| Date | Symbol | Error | Loss | Lesson |\n|------|--------|-------|------|--------|{new_entry}"
                    )

                # === 2. Categorize into Common Pitfalls ===
                if a.action == 'buy':
                    pitfall = f"- [{date}] {a.symbol}: {what_wrong}"
                    if "### Buying Errors\n- (No entries yet)" in content:
                        content = content.replace("### Buying Errors\n- (No entries yet)", f"### Buying Errors\n{pitfall}")
                    elif "### Buying Errors\n" in content and pitfall not in content:
                        content = content.replace("### Buying Errors\n", f"### Buying Errors\n{pitfall}\n")

                elif a.action == 'sell':
                    pitfall = f"- [{date}] {a.symbol}: {what_wrong}"
                    if "### Selling Errors\n- (No entries yet)" in content:
                        content = content.replace("### Selling Errors\n- (No entries yet)", f"### Selling Errors\n{pitfall}")
                    elif "### Selling Errors\n" in content and pitfall not in content:
                        content = content.replace("### Selling Errors\n", f"### Selling Errors\n{pitfall}\n")

        # === 3. Update Error Frequency table ===
        q2_count = sum(1 for a in analyses if getattr(a, 'quadrant', None) == 'Q2_NOSKILL_LUCK')
        q4_count = sum(1 for a in analyses if getattr(a, 'quadrant', None) == 'Q4_NOSKILL_NOLUCK')

        if q2_count > 0 or q4_count > 0:
            freq_entry = f"\n| Q2 (Lucky Error) | {q2_count} | {date} |" if q2_count else ""
            freq_entry += f"\n| Q4 (Unlucky Error) | {q4_count} | {date} |" if q4_count else ""

            if "| Error Type | Occurrences | Last Seen |\n|------------|-------------|-----------|" in content:
                content = content.replace(
                    "| Error Type | Occurrences | Last Seen |\n|------------|-------------|-----------|",
                    f"| Error Type | Occurrences | Last Seen |\n|------------|-------------|-----------|{freq_entry}"
                )

        mistakes_path.write_text(content, encoding='utf-8')

    def _update_trade_errors(self, date: str, analyses: List[DecisionAnalysis]):
        """Update trade_errors.md with Q2/Q4 patterns to avoid.

        Now includes duplicate checking to prevent the same pattern
        (e.g., "BUY NVDA when skill<60") from being added multiple times.
        """
        errors_path = self.kb_root / "patterns" / "trade_errors.md"
        if not errors_path.exists():
            return

        try:
            content = errors_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return

        # Add Q2/Q4 decisions to "Never Repeat Rules" - with duplicate checking
        for a in analyses:
            quadrant = getattr(a, 'quadrant', None)
            if quadrant in ['Q2_NOSKILL_LUCK', 'Q4_NOSKILL_NOLUCK']:
                reason = "Got lucky but bad decision" if quadrant == 'Q2_NOSKILL_LUCK' else "Bad decision + bad luck"
                pattern = f"{a.action.upper()} {a.symbol} when skill<60"
                rule = f"\n| {pattern} | {reason} | {date} |"

                # Check for duplicate pattern before adding
                if self._is_duplicate_pattern(pattern, 'never_repeat', content):
                    logger.debug(f"Skipping duplicate Never Repeat rule: {pattern}")
                    continue

                if "| Pattern | Why to Avoid | Learned On |\n|---------|--------------|------------|" in content:
                    content = content.replace(
                        "| Pattern | Why to Avoid | Learned On |\n|---------|--------------|------------|",
                        f"| Pattern | Why to Avoid | Learned On |\n|---------|--------------|------------|{rule}"
                    )

        errors_path.write_text(content, encoding='utf-8')

    def get_session_path(self, date: str) -> Path:
        """Get the path to a session directory."""
        return self.kb_root / "sessions" / date
