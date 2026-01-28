"""
KB Writer - writes daily trading summaries and updates knowledge base.
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

from .analyzer import DecisionAnalysis


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
        (self.kb_root / "master_index.md").write_text(content)

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
        (self.kb_root / "lessons_learned.md").write_text(content)

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
        (self.kb_root / "patterns" / "mistakes.md").write_text(content)

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
        session_dir = self.kb_root / "sessions" / date
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
        (session_dir / "daily_summary.md").write_text(summary_content)

        # Write decisions.json
        decisions_data = [
            {
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
            for a in analyses
        ]
        (session_dir / "decisions.json").write_text(
            json.dumps(decisions_data, indent=2)
        )

        # Write analysis.md
        analysis_content = self._generate_analysis(date, analyses)
        (session_dir / "analysis.md").write_text(analysis_content)

        # Update master index
        self._update_master_index(date, analyses, avg_skill, avg_luck)

        # Update lessons learned if significant
        self._update_lessons_if_significant(date, analyses)

        # Update mistakes if any errors
        self._update_mistakes(date, analyses)

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
            return "✅ Excellent - Good process led to good outcome"
        elif skill >= 70 and outcome < 50:
            return "⚠️ Unlucky - Good process but poor outcome (don't change strategy)"
        elif skill < 50 and outcome >= 70:
            return "🎲 Lucky - Poor process but good outcome (don't repeat)"
        else:
            return "❌ Poor - Needs improvement in both process and execution"

    def _update_master_index(
        self,
        date: str,
        analyses: List[DecisionAnalysis],
        avg_skill: float,
        avg_luck: float
    ):
        """Update master index with new learnings."""
        master_path = self.kb_root / "master_index.md"
        content = master_path.read_text()

        # Find high-skill successful patterns
        good_patterns = [a for a in analyses if a.skill_score >= 70 and a.profitable]

        # Update statistics section
        # This is a simplified update - in production would parse and update properly
        new_lesson = f"\n- [{date}] Avg Skill: {avg_skill:.0f}, Luck: {avg_luck:.0%}"

        if "## Recent Lessons (Last 5)" in content:
            # Add new lesson
            content = content.replace(
                "## Recent Lessons (Last 5)\n<!-- Most recent learnings -->\n",
                f"## Recent Lessons (Last 5)\n<!-- Most recent learnings -->\n{new_lesson}\n"
            )

        # Update last updated timestamp
        content = content.replace(
            "*Last updated: Never*",
            f"*Last updated: {datetime.now().isoformat()}*"
        )
        content = content.replace(
            f"*Last updated:",
            f"*Last updated: {datetime.now().isoformat()}*\n*Previously updated:"
        )

        master_path.write_text(content)

    def _update_lessons_if_significant(self, date: str, analyses: List[DecisionAnalysis]):
        """Update lessons learned if there are significant learnings."""
        # Significant = very high skill and profitable, or very low skill and loss
        significant = [
            a for a in analyses
            if (a.skill_score >= 80 and a.profitable) or (a.skill_score < 40 and not a.profitable)
        ]

        if not significant:
            return

        lessons_path = self.kb_root / "lessons_learned.md"
        content = lessons_path.read_text()

        for a in significant:
            new_entry = f"\n| {date} | {a.skill_score} | {a.luck_factor:.0%} | {a.lesson_learned} |"

            if "## Decision Quality Trends" in content:
                # Add to trends table
                content = content.replace(
                    "| Date | Avg Skill Score | Avg Luck Factor | Key Learning |\n|------|-----------------|-----------------|--------------|",
                    f"| Date | Avg Skill Score | Avg Luck Factor | Key Learning |\n|------|-----------------|-----------------|--------------|{new_entry}"
                )

        lessons_path.write_text(content)

    def _update_mistakes(self, date: str, analyses: List[DecisionAnalysis]):
        """Update mistakes file with any errors."""
        errors = [a for a in analyses if not a.profitable and a.skill_score < 50]

        if not errors:
            return

        mistakes_path = self.kb_root / "patterns" / "mistakes.md"
        content = mistakes_path.read_text()

        for a in errors:
            new_entry = f"\n| {date} | {a.symbol} | {a.what_went_wrong[:50]} | ${a.profit_loss:.2f} | {a.lesson_learned[:50]} |"

            if "## Critical Errors" in content:
                content = content.replace(
                    "| Date | Symbol | Error | Loss | Lesson |\n|------|--------|-------|------|--------|",
                    f"| Date | Symbol | Error | Loss | Lesson |\n|------|--------|-------|------|--------|{new_entry}"
                )

        mistakes_path.write_text(content)

    def get_session_path(self, date: str) -> Path:
        """Get the path to a session directory."""
        return self.kb_root / "sessions" / date
