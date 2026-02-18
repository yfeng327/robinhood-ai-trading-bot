"""
Slider KB Materializer — Writes slider decisions to knowledge base.

Creates/appends to kb/sessions/{date}/slider_decisions.md with:
1. Decision log table (compact view of all strategies + final slider)
2. Strategy reasoning table (detailed reasoning from each strategy)

Strategy prompts are configured to output ≤80 char reasoning with abbreviations.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from pytz import timezone

logger = logging.getLogger(__name__)


class SliderKBWriter:
    """Writes slider decisions to KB for analysis and review."""
    
    def __init__(self, kb_root: str = "kb"):
        self.kb_root = Path(kb_root)
        self.et_tz = timezone('US/Eastern')
        self._current_date = None
        self._initialized_today = False
    
    def append_decision(
        self,
        strategy_results: Dict[str, Dict],
        synthesis_result: Dict,
        current_price: float,
        action_taken: str = "",
        bot_pnl_pct: float = 0.0,
        benchmark_data: Optional[Dict[str, Dict]] = None,
        sqqq_price: float = 0.0,
    ):
        """
        Append one slider decision cycle to the KB.

        Args:
            strategy_results: Dict mapping strategy name to result dict
            synthesis_result: Dict with final_slider, confidence, reasoning
            current_price: Current QQQ price
            action_taken: Description of action taken (e.g., "BUY TQQQ 30%")
            bot_pnl_pct: Bot's current P/L percentage
            benchmark_data: Dict with benchmark performance {symbol: {return_pct: float, price: float}}
            sqqq_price: Current SQQQ price
        """
        now = datetime.now(self.et_tz)
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        
        # Get or create session directory
        session_dir = self.kb_root / "sessions" / date_str
        session_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = session_dir / "slider_decisions.md"
        
        # Initialize file with headers if new day or file doesn't exist
        if not file_path.exists() or self._current_date != date_str:
            self._initialize_file(file_path, date_str)
            self._current_date = date_str

        # Get synthesis reasoning (strategy prompts output ≤80 chars, fallback truncate)
        synthesis_reasoning = synthesis_result.get('reasoning', '')
        if len(synthesis_reasoning) > 80:
            compressed_synthesis_reason = synthesis_reasoning[:77] + "..."
        else:
            compressed_synthesis_reason = synthesis_reasoning

        # Append decision log row
        decision_row = self._format_decision_row(
            time_str, strategy_results, synthesis_result, action_taken,
            compressed_synthesis_reason
        )

        # Append strategy reasoning rows
        reasoning_rows = self._format_reasoning_rows(time_str, strategy_results)

        # Append asset track row
        asset_track_row = ""
        if benchmark_data:
            asset_track_row = self._format_asset_track_row(
                time_str, synthesis_result.get('final_slider', 0), bot_pnl_pct, benchmark_data, sqqq_price
            )

        # Read current content to find insertion points
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Insert decision row after Decision Log table header
        content = self._insert_after_marker(
            content, "| Time | TTM | ORB | MeanRev | Gap | Final | Conf | Action | Synthesis Reason |", decision_row
        )
        
        
        # Insert asset track row after Asset Track table header
        if asset_track_row:
            content = self._insert_after_marker(
                content, "| Time | Slider | Bot P/L | QQQ | QQQ $ | VOO | VOO $ | TQQQ | TQQQ $ | SQQQ $ |", asset_track_row
            )
        
        # Insert reasoning rows after Strategy Reasoning table header
        for row in reasoning_rows:
            content = self._insert_after_marker(
                content, "| Time | Strategy | Slider | Conf | Reasoning |", row
            )
        
        # Write updated content
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"Materialized slider decision to {file_path}")
    
    def _initialize_file(self, file_path: Path, date_str: str):
        """Initialize the slider decisions file with headers."""
        template = f"""# Slider Decisions: {date_str}

## Summary
- **Total Cycles:** 0
- **Avg Final Slider:** 0.00
- **Max Bullish:** 0.00
- **Max Bearish:** 0.00

## Decision Log
| Time | TTM | ORB | MeanRev | Gap | Final | Conf | Action | Synthesis Reason |
|------|-----|-----|---------|-----|-------|------|--------|------------------|

## Asset Track
| Time | Slider | Bot P/L | QQQ | QQQ $ | VOO | VOO $ | TQQQ | TQQQ $ | SQQQ $ |
|------|--------|---------|-----|-------|-----|-------|------|--------|--------|

## Strategy Reasoning
| Time | Strategy | Slider | Conf | Reasoning |
|------|----------|--------|------|-----------|

---
*Updated: {datetime.now(self.et_tz).isoformat()}*
"""
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(template)

        logger.info(f"Initialized slider decisions file: {file_path}")
    
    def _format_decision_row(
        self,
        time_str: str,
        strategy_results: Dict[str, Dict],
        synthesis_result: Dict,
        action_taken: str,
        compressed_synthesis_reason: str = ""
    ) -> str:
        """Format one row for the decision log table."""
        def fmt_strategy(name: str) -> str:
            r = strategy_results.get(name, {})
            slider = r.get('slider', 0)
            conf = r.get('confidence', 0)
            sign = '+' if slider > 0 else ''
            return f"{sign}{slider:.1f} ({conf:.0%})"

        ttm = fmt_strategy('ttm_squeeze')
        orb = fmt_strategy('orb')
        mean_rev = fmt_strategy('mean_reversion')
        gap = fmt_strategy('gap_trading')

        final = synthesis_result.get('final_slider', 0)
        conf = synthesis_result.get('confidence', 0)
        final_str = f"{'+' if final > 0 else ''}{final:.2f}"

        action = action_taken or self._infer_action(final)

        # Escape pipe characters in synthesis reason
        reason = compressed_synthesis_reason.replace('|', '\\|') if compressed_synthesis_reason else "-"

        return f"| {time_str} | {ttm} | {orb} | {mean_rev} | {gap} | {final_str} | {conf:.0%} | {action} | {reason} |"
    
    def _format_reasoning_rows(
        self,
        time_str: str,
        strategy_results: Dict[str, Dict]
    ) -> list:
        """Format reasoning rows for each strategy.

        Strategy prompts are configured to output ≤80 char reasoning with abbreviations.
        Fallback truncation if a strategy exceeds limit.
        """
        rows = []
        strategy_names = {
            'ttm_squeeze': 'TTM Squeeze',
            'orb': 'ORB',
            'mean_reversion': 'Mean Reversion',
            'gap_trading': 'Gap Trading',
            'overnight': 'Overnight',
        }

        for key, display_name in strategy_names.items():
            r = strategy_results.get(key, {})
            slider = r.get('slider', 0)
            conf = r.get('confidence', 0)
            reasoning = r.get('reasoning', 'No reasoning provided')

            # Fallback truncation if strategy exceeded 80 char limit
            if len(reasoning) > 80:
                reasoning = reasoning[:77] + "..."

            # Escape pipe characters in reasoning
            reasoning = reasoning.replace('|', '\\|')

            sign = '+' if slider > 0 else ''
            rows.append(
                f"| {time_str} | {display_name} | {sign}{slider:.2f} | {conf:.0%} | {reasoning} |"
            )

        return rows

    def _format_asset_track_row(
        self,
        time_str: str,
        slider_val: float,
        bot_pnl_pct: float,
        benchmark_data: Dict[str, Dict],
        sqqq_price: float = 0.0
    ) -> str:
        """Format one row for the asset track table with prices and percentages."""
        slider_str = f"{'+' if slider_val > 0 else ''}{slider_val:.2f}"
        bot_pnl_str = f"{'+' if bot_pnl_pct > 0 else ''}{bot_pnl_pct:.2f}%"

        def get_bench_pct(symbol: str) -> str:
            data = benchmark_data.get(symbol, {})
            val = data.get('return_pct', 0.0)
            return f"{'+' if val > 0 else ''}{val:.2f}%"

        def get_bench_price(symbol: str) -> str:
            data = benchmark_data.get(symbol, {})
            price = data.get('price', 0.0)
            return f"${price:.2f}"

        qqq_pct = get_bench_pct('QQQ')
        qqq_price = get_bench_price('QQQ')
        voo_pct = get_bench_pct('VOO')
        voo_price = get_bench_price('VOO')
        tqqq_pct = get_bench_pct('TQQQ')
        tqqq_price = get_bench_price('TQQQ')
        sqqq_price_str = f"${sqqq_price:.2f}"

        return f"| {time_str} | {slider_str} | {bot_pnl_str} | {qqq_pct} | {qqq_price} | {voo_pct} | {voo_price} | {tqqq_pct} | {tqqq_price} | {sqqq_price_str} |"

    def _infer_action(self, final_slider: float) -> str:
        """Infer action description from final slider value."""
        if final_slider > 0.5:
            return f"STRONG BUY TQQQ {abs(final_slider)*100:.0f}%"
        elif final_slider > 0.1:
            return f"BUY TQQQ {abs(final_slider)*100:.0f}%"
        elif final_slider > 0.05:
            return f"LIGHT TQQQ {abs(final_slider)*100:.0f}%"
        elif final_slider < -0.5:
            return f"STRONG BUY SQQQ {abs(final_slider)*100:.0f}%"
        elif final_slider < -0.1:
            return f"BUY SQQQ {abs(final_slider)*100:.0f}%"
        elif final_slider < -0.05:
            return f"LIGHT SQQQ {abs(final_slider)*100:.0f}%"
        else:
            return "NEUTRAL"
    
    def _insert_after_marker(self, content: str, marker: str, new_row: str) -> str:
        """Insert a new row after the marker line (table header separator)."""
        lines = content.split('\n')
        result = []
        marker_found = False
        separator_found = False
        
        for i, line in enumerate(lines):
            result.append(line)
            
            # Look for the marker line
            if marker in line and not marker_found:
                marker_found = True
                continue
            
            # After marker, look for the separator line (|---|---|...)
            if marker_found and not separator_found and line.startswith('|') and '---' in line:
                separator_found = True
                result.append(new_row)
        
        return '\n'.join(result)
    
    def update_summary(self, file_path: Path):
        """Update the summary section with aggregated stats."""
        # This could be called at end of day to compute averages
        # For now, we skip this and just log decisions
        pass
