"""
Decision Analyzer - evaluates trading decisions for luck vs skill.

Includes statistical analysis using KS/AD tests for quadrant classification:
- Q1 ([+][+]): Decision RIGHT + Luck FAVORABLE
- Q2 ([-][+]): Decision WRONG + Luck FAVORABLE
- Q3 ([+][-]): Decision RIGHT + Luck UNFAVORABLE
- Q4 ([-][-]): Decision WRONG + Luck UNFAVORABLE
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class DecisionAnalysis:
    """Analysis result for a single trading decision."""
    symbol: str
    action: str  # buy, sell, hold
    quantity: float
    price: float

    # Skill metrics
    indicator_alignment: int  # 0-30: How well indicators agreed
    position_sizing: int      # 0-20: Was size appropriate?
    risk_reward: int          # 0-25: Risk/reward ratio quality
    pattern_match: int        # 0-25: Similar to successful past patterns?

    # Outcome metrics
    profit_loss: float
    profitable: bool
    beat_market: bool

    # Computed scores
    skill_score: int          # 0-100
    outcome_score: int        # 0-100
    total_score: int          # Weighted combination
    luck_factor: float        # How much luck contributed (0-1)

    # Analysis text
    what_went_right: str
    what_went_wrong: str
    lesson_learned: str

    # Statistical quadrant analysis (added via enhance_with_statistics)
    quadrant: Optional[str] = None                # Q1, Q2, Q3, Q4
    quadrant_label: Optional[str] = None          # Human-readable quadrant
    statistical_skill_score: Optional[float] = None   # KS-based skill
    statistical_luck_pct: Optional[float] = None      # AD-based luck percentage
    ks_statistic: Optional[float] = None          # Kolmogorov-Smirnov stat
    ks_p_value: Optional[float] = None            # KS p-value
    ad_statistic: Optional[float] = None          # Anderson-Darling stat
    expected_return: Optional[float] = None       # What was expected
    actual_return: Optional[float] = None         # What happened
    return_z_score: Optional[float] = None        # How many std devs from mean
    quadrant_interpretation: Optional[str] = None # Detailed interpretation


class DecisionAnalyzer:
    """
    Analyzes trading decisions to determine skill vs luck.

    Skill Score (0-100):
    - Indicator alignment: +30 (if RSI, MA, VWAP agree with decision)
    - Proper position size: +20 (within guidelines)
    - Risk/reward ratio: +25 (favorable setup)
    - Historical pattern match: +25 (similar successful patterns)

    Outcome Score (0-100):
    - Profitable trade: +50
    - Beat market average: +25
    - Minimal drawdown: +25

    Total Score = (Skill * 0.6) + (Outcome * 0.4)
    """

    def __init__(self, min_buy: float, max_buy: float, min_sell: float, max_sell: float):
        self.min_buy = min_buy
        self.max_buy = max_buy
        self.min_sell = min_sell
        self.max_sell = max_sell

    def analyze_decision(
        self,
        decision: Dict,
        stock_data: Dict,
        next_day_price: Optional[float],
        market_return: float = 0.0,
        past_patterns: List[Dict] = None
    ) -> DecisionAnalysis:
        """
        Analyze a single trading decision.

        Args:
            decision: {symbol, decision, quantity} from AI
            stock_data: Stock data at time of decision (price, RSI, MAs, etc.)
            next_day_price: Price on the next trading day (to evaluate outcome)
            market_return: Market average return for comparison
            past_patterns: List of similar past decisions for pattern matching
        """
        symbol = decision['symbol']
        action = decision['decision']
        quantity = decision.get('quantity', 0)
        price = stock_data.get('current_price', 0)

        # Calculate skill components
        indicator_alignment = self._score_indicator_alignment(action, stock_data)
        position_sizing = self._score_position_sizing(action, quantity, price)
        risk_reward = self._score_risk_reward(action, stock_data)
        pattern_match = self._score_pattern_match(decision, stock_data, past_patterns or [])

        skill_score = indicator_alignment + position_sizing + risk_reward + pattern_match

        # Calculate outcome (if next day price available)
        profit_loss = 0.0
        profitable = False
        beat_market = False
        outcome_score = 50  # Default neutral

        if next_day_price and price > 0:
            price_change = (next_day_price - price) / price

            if action == 'buy':
                profit_loss = price_change * quantity * price
                profitable = price_change > 0
                beat_market = price_change > market_return
            elif action == 'sell':
                profit_loss = -price_change * quantity * price  # Selling avoids loss
                profitable = price_change < 0  # Good to sell if price dropped
                beat_market = -price_change > market_return

            # Outcome score
            outcome_score = 0
            if profitable:
                outcome_score += 50
            if beat_market:
                outcome_score += 25
            # Minimal drawdown bonus (if not a big loss)
            if profit_loss >= -0.02 * quantity * price:  # Less than 2% loss
                outcome_score += 25

        # Total score (60% skill, 40% outcome)
        total_score = int(skill_score * 0.6 + outcome_score * 0.4)

        # Luck factor: how much outcome differed from skill prediction
        # High skill + bad outcome = unlucky, Low skill + good outcome = lucky
        skill_normalized = skill_score / 100
        outcome_normalized = outcome_score / 100
        luck_factor = abs(outcome_normalized - skill_normalized)

        # Generate analysis text
        what_went_right = self._generate_right_analysis(
            action, indicator_alignment, position_sizing, profitable
        )
        what_went_wrong = self._generate_wrong_analysis(
            action, indicator_alignment, position_sizing, profitable, stock_data
        )
        lesson_learned = self._generate_lesson(
            action, skill_score, outcome_score, luck_factor, stock_data
        )

        return DecisionAnalysis(
            symbol=symbol,
            action=action,
            quantity=quantity,
            price=price,
            indicator_alignment=indicator_alignment,
            position_sizing=position_sizing,
            risk_reward=risk_reward,
            pattern_match=pattern_match,
            profit_loss=profit_loss,
            profitable=profitable,
            beat_market=beat_market,
            skill_score=skill_score,
            outcome_score=outcome_score,
            total_score=total_score,
            luck_factor=luck_factor,
            what_went_right=what_went_right,
            what_went_wrong=what_went_wrong,
            lesson_learned=lesson_learned
        )

    def _score_indicator_alignment(self, action: str, stock_data: Dict) -> int:
        """Score how well indicators align with the decision (0-30)."""
        score = 0
        signals = []

        # RSI analysis
        rsi = stock_data.get('rsi')
        if rsi is not None:
            if action == 'buy' and rsi < 30:
                signals.append('bullish')
                score += 10
            elif action == 'buy' and rsi > 70:
                signals.append('bearish')  # Buying overbought = bad
            elif action == 'sell' and rsi > 70:
                signals.append('bullish')  # For selling
                score += 10
            elif action == 'sell' and rsi < 30:
                signals.append('bearish')  # Selling oversold = bad
            else:
                score += 5  # Neutral

        # Moving average analysis
        price = stock_data.get('current_price', 0)
        ma_50 = stock_data.get('50_day_mavg_price')
        ma_200 = stock_data.get('200_day_mavg_price')

        if ma_50 and ma_200 and price:
            if action == 'buy':
                if price > ma_50 > ma_200:  # Uptrend
                    score += 10
                    signals.append('bullish')
                elif price < ma_50 < ma_200:  # Downtrend
                    signals.append('bearish')
                else:
                    score += 5
            elif action == 'sell':
                if price < ma_50 < ma_200:  # Downtrend
                    score += 10
                    signals.append('bullish')
                elif price > ma_50 > ma_200:  # Uptrend
                    signals.append('bearish')
                else:
                    score += 5

        # VWAP analysis
        vwap = stock_data.get('vwap')
        if vwap and price:
            if action == 'buy' and price < vwap:
                score += 10  # Buying below VWAP is good
            elif action == 'sell' and price > vwap:
                score += 10  # Selling above VWAP is good
            else:
                score += 5

        return min(score, 30)

    def _score_position_sizing(self, action: str, quantity: float, price: float) -> int:
        """Score position sizing appropriateness (0-20)."""
        if quantity <= 0 or price <= 0:
            return 0

        amount = quantity * price

        if action == 'buy':
            if self.min_buy <= amount <= self.max_buy:
                return 20  # Perfect sizing
            elif amount < self.min_buy:
                return 10  # Too small but not terrible
            else:
                return 5   # Too large
        elif action == 'sell':
            if self.min_sell <= amount <= self.max_sell:
                return 20
            elif amount < self.min_sell:
                return 10
            else:
                return 5

        return 10  # Hold gets neutral

    def _score_risk_reward(self, action: str, stock_data: Dict) -> int:
        """Score risk/reward setup (0-25)."""
        score = 12  # Start neutral

        rsi = stock_data.get('rsi')
        price = stock_data.get('current_price', 0)
        ma_50 = stock_data.get('50_day_mavg_price')
        ma_200 = stock_data.get('200_day_mavg_price')

        # Distance from moving averages indicates potential
        if price and ma_50:
            distance_from_ma = (price - ma_50) / ma_50

            if action == 'buy':
                # Buying when close to or below MA50 = good risk/reward
                if distance_from_ma < 0:
                    score += 8
                elif distance_from_ma < 0.05:
                    score += 4
            elif action == 'sell':
                # Selling when far above MA50 = good risk/reward
                if distance_from_ma > 0.1:
                    score += 8
                elif distance_from_ma > 0.05:
                    score += 4

        # RSI extremes suggest better risk/reward
        if rsi:
            if action == 'buy' and rsi < 40:
                score += 5
            elif action == 'sell' and rsi > 60:
                score += 5

        return min(score, 25)

    def _score_pattern_match(
        self,
        decision: Dict,
        stock_data: Dict,
        past_patterns: List[Dict]
    ) -> int:
        """Score similarity to successful past patterns (0-25)."""
        if not past_patterns:
            return 12  # Neutral if no history

        symbol = decision['symbol']
        action = decision['decision']

        # Find similar past decisions for this symbol
        similar = [
            p for p in past_patterns
            if p.get('symbol') == symbol and p.get('action') == action
        ]

        if not similar:
            return 12  # No similar patterns

        # Check success rate of similar patterns
        successful = sum(1 for p in similar if p.get('profitable', False))
        success_rate = successful / len(similar) if similar else 0

        if success_rate >= 0.7:
            return 25  # Strong historical success
        elif success_rate >= 0.5:
            return 18
        elif success_rate >= 0.3:
            return 12
        else:
            return 5   # Poor historical success

    def _generate_right_analysis(
        self,
        action: str,
        indicator_alignment: int,
        position_sizing: int,
        profitable: bool
    ) -> str:
        """Generate analysis of what went right."""
        points = []

        if indicator_alignment >= 20:
            points.append("Indicators strongly supported the decision")
        elif indicator_alignment >= 15:
            points.append("Indicators moderately aligned with decision")

        if position_sizing >= 15:
            points.append("Position size was appropriate")

        if profitable:
            points.append("Trade was profitable")

        if not points:
            points.append("Decision followed systematic process")

        return "; ".join(points)

    def _generate_wrong_analysis(
        self,
        action: str,
        indicator_alignment: int,
        position_sizing: int,
        profitable: bool,
        stock_data: Dict
    ) -> str:
        """Generate analysis of what went wrong."""
        points = []

        if indicator_alignment < 15:
            points.append("Indicators did not strongly support this decision")

        if position_sizing < 15:
            points.append("Position size was not optimal")

        if not profitable:
            points.append("Trade resulted in a loss")

        # Check for specific issues
        rsi = stock_data.get('rsi')
        if rsi:
            if action == 'buy' and rsi > 70:
                points.append("Bought when RSI indicated overbought conditions")
            elif action == 'sell' and rsi < 30:
                points.append("Sold when RSI indicated oversold conditions")

        if not points:
            points.append("No significant issues identified")

        return "; ".join(points)

    def _generate_lesson(
        self,
        action: str,
        skill_score: int,
        outcome_score: int,
        luck_factor: float,
        stock_data: Dict
    ) -> str:
        """Generate key lesson from this decision."""
        if skill_score >= 70 and outcome_score >= 70:
            return "Good process led to good outcome. Continue this approach."

        if skill_score >= 70 and outcome_score < 50:
            return f"Process was sound but outcome was unlucky (luck factor: {luck_factor:.0%}). Don't change strategy based on this."

        if skill_score < 50 and outcome_score >= 70:
            return f"Got lucky despite weak setup (luck factor: {luck_factor:.0%}). Don't repeat this decision type."

        if skill_score < 50 and outcome_score < 50:
            return "Poor process led to poor outcome. Review indicator analysis before similar decisions."

        # Moderate case
        rsi = stock_data.get('rsi')
        if rsi and (rsi > 70 or rsi < 30):
            return f"Consider RSI extremes more carefully. RSI was {rsi:.0f}."

        return "Mixed results. Continue monitoring similar setups."


def analyze_day_decisions(
    decisions: List[Dict],
    stock_data: Dict[str, Dict],
    next_day_prices: Dict[str, float],
    analyzer: DecisionAnalyzer,
    market_return: float = 0.0,
    past_patterns: List[Dict] = None
) -> List[DecisionAnalysis]:
    """
    Analyze all decisions for a trading day.

    Args:
        decisions: List of AI decisions
        stock_data: Stock data at time of decisions
        next_day_prices: Prices on next day for outcome evaluation
        analyzer: DecisionAnalyzer instance
        market_return: Market average return for comparison
        past_patterns: Historical patterns for matching

    Returns:
        List of DecisionAnalysis objects
    """
    analyses = []

    for decision in decisions:
        symbol = decision.get('symbol')
        if not symbol or symbol not in stock_data:
            continue

        next_price = next_day_prices.get(symbol)
        analysis = analyzer.analyze_decision(
            decision=decision,
            stock_data=stock_data[symbol],
            next_day_price=next_price,
            market_return=market_return,
            past_patterns=past_patterns
        )
        analyses.append(analysis)

    return analyses


def enhance_with_statistics(
    analysis: DecisionAnalysis,
    historical_returns: List[float],
    expected_return: float = None
) -> DecisionAnalysis:
    """
    Enhance a DecisionAnalysis with statistical quadrant classification.

    Uses Kolmogorov-Smirnov and Anderson-Darling tests to statistically
    separate luck from skill.

    Args:
        analysis: Existing DecisionAnalysis to enhance
        historical_returns: List of historical daily returns for baseline
        expected_return: Expected return for this trade (optional)

    Returns:
        Enhanced DecisionAnalysis with quadrant fields populated
    """
    from .luck_statistics import LuckStatisticsAnalyzer, Quadrant

    # Create statistical analyzer with historical data
    stat_analyzer = LuckStatisticsAnalyzer(historical_returns)

    # Calculate actual return
    if analysis.price > 0 and analysis.profit_loss is not None:
        actual_return = analysis.profit_loss / (analysis.price * analysis.quantity) if analysis.quantity > 0 else 0
    else:
        actual_return = 0

    # Use expected return if provided, otherwise estimate from indicators
    if expected_return is None:
        # Estimate expected return from indicator alignment
        # Higher alignment suggests higher expected return
        alignment_factor = analysis.indicator_alignment / 30  # 0-1
        expected_return = 0.01 * alignment_factor if analysis.action == 'buy' else -0.01 * alignment_factor

    # Determine if indicators were aligned
    indicators_aligned = analysis.indicator_alignment >= 20

    # Determine if position sized correctly
    position_sized_correctly = analysis.position_sizing >= 15

    # Calculate historical success rate from pattern match
    historical_success_rate = analysis.pattern_match / 25  # 0-1

    # Run statistical analysis
    stat_result = stat_analyzer.analyze_decision(
        actual_return=actual_return,
        expected_return=expected_return,
        indicators_aligned=indicators_aligned,
        position_sized_correctly=position_sized_correctly,
        historical_success_rate=historical_success_rate
    )

    # Populate the statistical fields
    analysis.quadrant = stat_result.quadrant.name
    analysis.quadrant_label = stat_result.quadrant.value
    analysis.statistical_skill_score = stat_result.skill_score
    analysis.statistical_luck_pct = stat_result.luck_percentage
    analysis.ks_statistic = stat_result.ks_statistic
    analysis.ks_p_value = stat_result.ks_p_value
    analysis.ad_statistic = stat_result.ad_statistic
    analysis.expected_return = stat_result.expected_return
    analysis.actual_return = stat_result.actual_return
    analysis.return_z_score = stat_result.return_deviation
    analysis.quadrant_interpretation = stat_result.interpretation

    return analysis


def analyze_day_with_statistics(
    decisions: List[Dict],
    stock_data: Dict[str, Dict],
    next_day_prices: Dict[str, float],
    analyzer: DecisionAnalyzer,
    historical_returns: Dict[str, List[float]],
    market_return: float = 0.0,
    past_patterns: List[Dict] = None
) -> List[DecisionAnalysis]:
    """
    Analyze all decisions for a trading day WITH statistical quadrant analysis.

    This is the enhanced version of analyze_day_decisions that includes
    KS/AD statistical tests for luck vs skill separation.

    Args:
        decisions: List of AI decisions
        stock_data: Stock data at time of decisions
        next_day_prices: Prices on next day for outcome evaluation
        analyzer: DecisionAnalyzer instance
        historical_returns: Dict of symbol -> list of historical returns
        market_return: Market average return for comparison
        past_patterns: Historical patterns for matching

    Returns:
        List of DecisionAnalysis objects with statistical fields populated
    """
    # First get basic analyses
    analyses = analyze_day_decisions(
        decisions=decisions,
        stock_data=stock_data,
        next_day_prices=next_day_prices,
        analyzer=analyzer,
        market_return=market_return,
        past_patterns=past_patterns
    )

    # Enhance each with statistical analysis
    for analysis in analyses:
        symbol = analysis.symbol
        symbol_returns = historical_returns.get(symbol, [])

        if len(symbol_returns) >= 10:  # Need minimum data for statistics
            enhance_with_statistics(analysis, symbol_returns)

    return analyses


def format_quadrant_recap(analysis: DecisionAnalysis) -> str:
    """
    Format a single decision's quadrant analysis for the recap.

    This is the key output for absorption - clearly shows the quadrant
    and interpretation for each decision.
    """
    if analysis.quadrant is None:
        return f"**{analysis.symbol}** ({analysis.action}): No statistical analysis available"

    # Build indicator based on quadrant
    quadrant_indicators = {
        "Q1_SKILL_LUCK": "[+][+]",
        "Q2_NOSKILL_LUCK": "[-][+]",
        "Q3_SKILL_NOLUCK": "[+][-]",
        "Q4_NOSKILL_NOLUCK": "[-][-]"
    }
    indicator = quadrant_indicators.get(analysis.quadrant, "[?][?]")

    lines = [
        f"### {analysis.symbol} ({analysis.action.upper()}) - {indicator} {analysis.quadrant}",
        "",
        f"**Quadrant:** {analysis.quadrant_label}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Decision Skill | {analysis.statistical_skill_score:.0f}/100 |",
        f"| Luck Percentage | {analysis.statistical_luck_pct:.0f}% |",
        f"| Expected Return | {analysis.expected_return*100:+.2f}% |",
        f"| Actual Return | {analysis.actual_return*100:+.2f}% |",
        f"| Return Z-Score | {analysis.return_z_score:+.2f}σ |",
        f"| KS Statistic | {analysis.ks_statistic:.4f} |",
        f"| AD Statistic | {analysis.ad_statistic:.4f} |",
        "",
        f"**Interpretation:** {analysis.quadrant_interpretation}",
        ""
    ]

    return "\n".join(lines)
