"""
EOD Reviewer - End-of-day analysis and KB writing.

Runs once at market close to:
1. Collect all decisions from DecisionBuffer
2. Get next-day prices (or current prices for evaluation)
3. Perform 4-quadrant analysis (luck vs skill)
4. Generate deduplicated lessons
5. Write consolidated daily summary to KB
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from src.api import robinhood
from src.kb import KBWriter, KBReader, DecisionAnalyzer
from src.kb.analyzer import analyze_day_decisions, DecisionAnalysis
from src.kb.lesson_generator import generate_lessons_with_llm
from src.day_trading.decision_buffer import DecisionBuffer
from .deduplicator import LessonDeduplicator

logger = logging.getLogger(__name__)


class EODReviewer:
    """
    Performs end-of-day review of trading decisions.
    
    This is the ONLY place where KB writes happen.
    Separates luck from skill using 4-quadrant analysis:
    - Q1: Decision RIGHT + Luck FAVORABLE → REPEAT
    - Q2: Decision WRONG + Luck FAVORABLE → AVOID (got lucky)
    - Q3: Decision RIGHT + Luck UNFAVORABLE → REPEAT (unlucky)
    - Q4: Decision WRONG + Luck UNFAVORABLE → AVOID
    """
    
    def __init__(
        self,
        kb_writer: KBWriter,
        kb_reader: KBReader,
        decision_buffer: DecisionBuffer,
        min_buy: float = 1.0,
        max_buy: float = 10000.0,
        min_sell: float = 1.0,
        max_sell: float = 10000.0,
    ):
        """
        Initialize the EOD reviewer.
        
        Args:
            kb_writer: KBWriter for writing to KB
            kb_reader: KBReader for reading existing lessons
            decision_buffer: DecisionBuffer with day's decisions
            min_buy/max_buy: Buy amount limits
            min_sell/max_sell: Sell amount limits
        """
        self.kb_writer = kb_writer
        self.kb_reader = kb_reader
        self.decision_buffer = decision_buffer
        self.analyzer = DecisionAnalyzer(min_buy, max_buy, min_sell, max_sell)
    
    def run(self, end_of_day_value: Optional[float] = None) -> Dict:
        """
        Run end-of-day review.
        
        Args:
            end_of_day_value: Portfolio value at end of day.
                              If None, will be calculated from current holdings.
        
        Returns:
            Dict with review results including analyses and lessons written
        """
        logger.info("Starting end-of-day review...")
        
        # Get buffered decisions
        buffer_data = self.decision_buffer.get_decisions_for_eod()
        decisions = buffer_data['decisions']
        trade_results = buffer_data['trade_results']
        start_value = buffer_data['start_of_day_value']
        trade_date = buffer_data['date'] or datetime.now().strftime('%Y-%m-%d')
        
        if not decisions:
            logger.info("No decisions to review for today")
            return {'decisions': 0, 'lessons_written': 0}
        
        logger.info(f"Reviewing {len(decisions)} decisions from {trade_date}")
        
        # Filter to successful trades only (with all required fields)
        successful_decisions = []
        for d in decisions:
            symbol = d.get('symbol')
            if not symbol:
                continue
            if symbol not in trade_results:
                continue
            if trade_results[symbol].get('result') != 'success':
                continue
            # Ensure stock_data exists
            if d.get('stock_data') is None:
                logger.debug(f"Skipping {symbol}: no stock_data")
                continue
            successful_decisions.append(d)
        
        if not successful_decisions:
            logger.info("No successful trades to analyze")
            self.decision_buffer.clear_buffer()
            return {'decisions': len(decisions), 'successful': 0, 'lessons_written': 0}
        
        logger.info(f"Analyzing {len(successful_decisions)} successful trades")
        
        # Get current prices for evaluation
        current_prices = self._get_current_prices(successful_decisions)
        
        # Get current portfolio value if not provided
        if end_of_day_value is None:
            end_of_day_value = self._calculate_portfolio_value()
        
        # Build stock data dict from buffered decisions (now guaranteed non-None)
        stock_data = {d['symbol']: d['stock_data'] for d in successful_decisions}
        
        # Get past patterns for analysis
        symbols = [d['symbol'] for d in successful_decisions]
        past_patterns = self.kb_reader.get_past_patterns(symbols, limit=20)
        
        # Fetch intraday timelines for each symbol (for LLM context)
        intraday_timelines = self._fetch_intraday_timelines(symbols)
        
        # Analyze decisions
        analyses = self._analyze_decisions(
            successful_decisions, stock_data, current_prices, past_patterns
        )
        
        # Get current holdings
        current_holdings = self._get_current_holdings()
        current_cash = self._get_current_cash()
        
        # Generate lessons from analyses (uses LLM with intraday context)
        lessons = self._generate_lessons(analyses, intraday_timelines)
        
        # Deduplicate lessons
        unique_lessons, duplicates = self._deduplicate_lessons(lessons)
        
        logger.info(f"Generated {len(lessons)} lessons, {len(unique_lessons)} unique after dedup")
        
        # Write to KB
        self._write_to_kb(
            date=trade_date,
            start_value=start_value or end_of_day_value,
            end_value=end_of_day_value,
            analyses=analyses,
            holdings=current_holdings,
            cash=current_cash,
            unique_lessons=unique_lessons
        )
        
        # Clear the decision buffer
        self.decision_buffer.clear_buffer()
        
        # Run KB compaction to clean up duplicates
        logger.info("Running KB compaction...")
        self.kb_writer.compact_kb_files()
        
        return {
            'date': trade_date,
            'decisions': len(decisions),
            'successful': len(successful_decisions),
            'analyses': len(analyses),
            'lessons_generated': len(lessons),
            'lessons_written': len(unique_lessons),
            'duplicates_removed': len(duplicates),
        }
    
    def _get_current_prices(self, decisions: List[Dict]) -> Dict[str, float]:
        """Get current prices for symbols in decisions."""
        prices = {}
        for d in decisions:
            symbol = d['symbol']
            try:
                quote = robinhood.get_quote(symbol)
                if quote and 'last_trade_price' in quote:
                    prices[symbol] = float(quote['last_trade_price'])
                else:
                    # Fall back to decision price
                    prices[symbol] = d.get('price', 0)
            except Exception as e:
                logger.debug(f"Could not get price for {symbol}: {e}")
                prices[symbol] = d.get('price', 0)
        return prices
    
    def _calculate_portfolio_value(self) -> float:
        """Calculate current portfolio value."""
        try:
            account = robinhood.get_account_info()
            portfolio = robinhood.get_portfolio_stocks()
            
            value = float(account.get('buying_power', 0))
            for sym, data in portfolio.items():
                value += float(data.get('price', 0)) * float(data.get('quantity', 0))
            
            return value
        except Exception as e:
            logger.error(f"Could not calculate portfolio value: {e}")
            return 0
    
    def _get_current_holdings(self) -> Dict[str, float]:
        """Get current portfolio holdings."""
        try:
            portfolio = robinhood.get_portfolio_stocks()
            return {sym: float(data.get('quantity', 0)) for sym, data in portfolio.items()}
        except Exception as e:
            logger.error(f"Could not get holdings: {e}")
            return {}
    
    def _get_current_cash(self) -> float:
        """Get current cash balance."""
        try:
            account = robinhood.get_account_info()
            return float(account.get('buying_power', 0))
        except Exception as e:
            logger.error(f"Could not get cash: {e}")
            return 0
    
    def _fetch_intraday_timelines(self, symbols: List[str]) -> Dict[str, str]:
        """
        Fetch intraday price/volume timelines for each symbol.
        
        Uses the same format as trading decisions to give LLM
        context on how prices moved throughout the day.
        
        Args:
            symbols: List of stock symbols to fetch data for
            
        Returns:
            Dict mapping symbol to intraday markdown table
        """
        timelines = {}
        
        for symbol in symbols:
            try:
                historical_day = robinhood.get_historical_data(
                    symbol, interval="5minute", span="day"
                )
                if historical_day:
                    timeline = robinhood.build_intraday_summary(historical_day, symbol)
                    if timeline:
                        timelines[symbol] = timeline
            except Exception as e:
                logger.debug(f"Could not fetch intraday data for {symbol}: {e}")
        
        return timelines
    
    def _analyze_decisions(
        self,
        decisions: List[Dict],
        stock_data: Dict[str, Dict],
        next_prices: Dict[str, float],
        past_patterns: List[Dict]
    ) -> List[DecisionAnalysis]:
        """
        Analyze decisions using 4-quadrant methodology.
        
        Args:
            decisions: List of trading decisions
            stock_data: Stock data at time of decisions
            next_prices: Current prices for outcome evaluation
            past_patterns: Past decision patterns from KB
            
        Returns:
            List of DecisionAnalysis objects
        """
        analyses = []
        
        for d in decisions:
            symbol = d['symbol']
            # Transform to analyzer format
            decision_dict = {
                'symbol': symbol,
                'decision': d['decision'],
                'quantity': d['quantity']
            }
            
            analysis = self.analyzer.analyze_decision(
                decision=decision_dict,
                stock_data=stock_data.get(symbol, {}),
                next_day_price=next_prices.get(symbol),
                market_return=0.0,  # Could enhance with SPY comparison
                past_patterns=past_patterns
            )
            
            analyses.append(analysis)
            logger.debug(
                f"{symbol}: {d['decision']} | skill={analysis.skill_score}, "
                f"outcome={analysis.outcome_score}, luck={analysis.luck_factor:.1%}"
            )
        
        return analyses
    
    def _generate_lessons(
        self, 
        analyses: List[DecisionAnalysis],
        intraday_timelines: Dict[str, str] = None
    ) -> List[str]:
        """
        Generate rich lessons from analyses using LLM.
        
        Keeps all statistics programmatic (skill, luck, quadrant),
        but generates lessons/advice via LLM for better context.
        
        Args:
            analyses: List of DecisionAnalysis objects
            intraday_timelines: Dict mapping symbol to intraday price/volume table
        
        Falls back to basic lessons if LLM fails.
        """
        if not analyses:
            return []
        
        try:
            # Use LLM to generate rich, contextual lessons (ai.py handles logging)
            lessons = generate_lessons_with_llm(analyses, intraday_timelines or {})
            return lessons
        except Exception as e:
            logger.warning(f"LLM lesson generation failed, using fallback: {e}")
            return self._generate_basic_lessons(analyses)
    
    def _generate_basic_lessons(self, analyses: List[DecisionAnalysis]) -> List[str]:
        """
        Generate basic fallback lessons when LLM is unavailable.
        
        Uses pre-computed stats from DecisionAnalysis.
        """
        lessons = []
        
        for a in analyses:
            skill = a.skill_score
            outcome = a.outcome_score
            
            # Build price context string
            price_str = f"@ ${a.price:.2f}" if a.price else ""
            
            # Position relative to day range
            pos_parts = []
            if a.pct_from_high is not None and a.pct_from_high > 0.5:
                pos_parts.append(f"↓{a.pct_from_high:.1f}% from high")
            if a.pct_from_low is not None and a.pct_from_low > 0.5:
                pos_parts.append(f"↑{a.pct_from_low:.1f}% from low")
            if a.rsi_at_decision is not None:
                pos_parts.append(f"RSI={a.rsi_at_decision:.0f}")
            
            context_str = f"({', '.join(pos_parts)})" if pos_parts else ""
            
            # Prefix with symbol and action
            header = f"{a.symbol}: {a.action.upper()} {price_str} {context_str}".strip()
            
            # Determine quadrant
            if skill >= 60 and outcome >= 50:
                quadrant = "Q1"
            elif skill >= 60 and outcome < 50:
                quadrant = "Q3"
            elif skill < 60 and outcome >= 50:
                quadrant = "Q2"
            else:
                quadrant = "Q4"
            
            # Use the lesson_learned from DecisionAnalysis (placeholder with stats)
            lesson = f"[{quadrant}] {header}. {a.lesson_learned}"
            lessons.append(lesson)
        
        return lessons
    
    def _deduplicate_lessons(self, lessons: List[str]) -> tuple:
        """
        Remove duplicate lessons using pattern matching.
        
        Args:
            lessons: New lessons to deduplicate
            
        Returns:
            Tuple of (unique lessons, duplicates)
        """
        # Load existing lessons from KB
        existing = self._load_existing_lessons()
        
        deduplicator = LessonDeduplicator(existing)
        unique, duplicates = deduplicator.filter_duplicates(lessons)
        
        # Consolidate similar new lessons
        unique = deduplicator.consolidate_similar(unique)
        
        return unique, duplicates
    
    def _load_existing_lessons(self) -> List[str]:
        """Load existing lessons from KB files."""
        lessons = []
        
        try:
            # Read from master_index.md Recent Lessons section
            master_path = self.kb_reader.kb_root / "master_index.md"
            if master_path.exists():
                content = master_path.read_text()
                
                # Extract lessons from "Recent Lessons" section
                lessons_start = content.find("## Recent Lessons")
                if lessons_start >= 0:
                    lessons_end = content.find("##", lessons_start + 10)
                    if lessons_end < 0:
                        lessons_end = len(content)
                    
                    lessons_section = content[lessons_start:lessons_end]
                    for line in lessons_section.split('\n'):
                        if line.strip().startswith('-'):
                            lessons.append(line.strip()[2:])  # Remove "- " prefix
            
            # Also read from lessons_learned.md
            lessons_path = self.kb_reader.kb_root / "lessons_learned.md"
            if lessons_path.exists():
                content = lessons_path.read_text()
                for line in content.split('\n'):
                    if line.strip().startswith('-') and len(line.strip()) > 5:
                        lessons.append(line.strip()[2:])
        
        except Exception as e:
            logger.debug(f"Error loading existing lessons: {e}")
        
        logger.debug(f"Loaded {len(lessons)} existing lessons from KB")
        return lessons
    
    def _write_to_kb(
        self,
        date: str,
        start_value: float,
        end_value: float,
        analyses: List[DecisionAnalysis],
        holdings: Dict[str, float],
        cash: float,
        unique_lessons: List[str]
    ):
        """Write consolidated daily summary to KB."""
        logger.info(f"Writing daily summary for {date} to KB...")
        
        # Use existing KBWriter method for daily summary
        self.kb_writer.write_daily_summary(
            date=date,
            starting_value=start_value,
            ending_value=end_value,
            analyses=analyses,
            portfolio_holdings=holdings,
            cash=cash
        )
        
        # Write unique lessons to lessons_learned.md
        if unique_lessons:
            self._append_lessons(unique_lessons, date)
        
        logger.info(f"KB write complete: {len(analyses)} analyses, {len(unique_lessons)} lessons")
    
    def _append_lessons(self, lessons: List[str], date: str):
        """Append unique lessons to lessons_learned.md."""
        lessons_path = self.kb_reader.kb_root / "lessons_learned.md"
        
        try:
            content = lessons_path.read_text() if lessons_path.exists() else ""
            
            # Find the "What Works" section
            works_idx = content.find("### What Works")
            doesnt_work_idx = content.find("### What Doesn't Work")
            
            # Separate lessons by quadrant
            q1_q3_lessons = [l for l in lessons if l.startswith('[Q1]') or l.startswith('[Q3]')]
            q2_q4_lessons = [l for l in lessons if l.startswith('[Q2]') or l.startswith('[Q4]')]
            
            # Insert Q1/Q3 lessons into "What Works"
            if works_idx >= 0 and q1_q3_lessons:
                insert_pos = content.find('\n', works_idx) + 1
                new_entries = '\n'.join(f"- [{date}] {l}" for l in q1_q3_lessons) + '\n'
                content = content[:insert_pos] + new_entries + content[insert_pos:]
                
                # Recalculate doesnt_work_idx after insertion
                doesnt_work_idx = content.find("### What Doesn't Work")
            
            # Insert Q2/Q4 lessons into "What Doesn't Work"
            if doesnt_work_idx >= 0 and q2_q4_lessons:
                insert_pos = content.find('\n', doesnt_work_idx) + 1
                new_entries = '\n'.join(f"- [{date}] {l}" for l in q2_q4_lessons) + '\n'
                content = content[:insert_pos] + new_entries + content[insert_pos:]
            
            lessons_path.write_text(content)
            logger.info(f"Appended {len(lessons)} lessons to lessons_learned.md")
            
        except Exception as e:
            logger.error(f"Error appending lessons: {e}")


def run_eod_review(
    kb_writer: KBWriter,
    kb_reader: KBReader,
    decision_buffer: DecisionBuffer,
    min_buy: float = 1.0,
    max_buy: float = 10000.0,
    min_sell: float = 1.0,
    max_sell: float = 10000.0,
) -> Dict:
    """
    Convenience function to run EOD review.
    
    Args:
        kb_writer: KBWriter instance
        kb_reader: KBReader instance
        decision_buffer: DecisionBuffer with day's decisions
        min_buy/max_buy: Buy amount limits
        min_sell/max_sell: Sell amount limits
        
    Returns:
        Review results dict
    """
    reviewer = EODReviewer(
        kb_writer=kb_writer,
        kb_reader=kb_reader,
        decision_buffer=decision_buffer,
        min_buy=min_buy,
        max_buy=max_buy,
        min_sell=min_sell,
        max_sell=max_sell,
    )
    return reviewer.run()
