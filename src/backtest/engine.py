"""
Backtest Engine - orchestrates historical trading simulation.
Includes Knowledge Base (KB) integration for learning from past decisions.
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd

from .portfolio import VirtualPortfolio
from .metrics import calculate_metrics, format_report
from ..utils import logger
from ..kb import KBWriter, KBReader, DecisionAnalyzer, TradePlanner, cleanup_kb, log_kb_state
from ..kb.analyzer import analyze_day_decisions, analyze_day_with_statistics


class BacktestEngine:
    """
    Runs backtests on historical market data.
    Simulates trading by stepping through historical data and calling AI for decisions.
    Integrates with KB system for learning from past decisions.
    """

    def __init__(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        starting_cash: float,
        transaction_fee: float = 0.0,
        portfolio_limit: int = 10,
        min_buy_amount: float = 1.0,
        max_buy_amount: float = 10.0,
        min_sell_amount: float = 1.0,
        max_sell_amount: float = 10.0,
        enable_kb: bool = True,
        clean_kb: bool = False,
    ):
        """
        Initialize the backtest engine.

        Args:
            symbols: List of stock symbols to backtest
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            starting_cash: Initial cash amount
            transaction_fee: Fee per transaction as decimal
            portfolio_limit: Max stocks to hold
            min_buy_amount: Minimum buy amount in USD
            max_buy_amount: Maximum buy amount in USD
            min_sell_amount: Minimum sell amount in USD
            max_sell_amount: Maximum sell amount in USD
            enable_kb: Whether to enable Knowledge Base tracking
            clean_kb: Whether to clean KB before starting (fresh run)
        """
        self.symbols = symbols
        self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = datetime.strptime(end_date, "%Y-%m-%d")
        self.starting_cash = starting_cash
        self.transaction_fee = transaction_fee
        self.portfolio_limit = portfolio_limit
        self.min_buy_amount = min_buy_amount
        self.max_buy_amount = max_buy_amount
        self.min_sell_amount = min_sell_amount
        self.max_sell_amount = max_sell_amount

        self.portfolio = VirtualPortfolio(starting_cash, transaction_fee)
        self.historical_data: Dict[str, pd.DataFrame] = {}
        self.trading_days: List[str] = []

        # Knowledge Base integration
        self.enable_kb = enable_kb
        if enable_kb:
            # Clean KB if requested (fresh backtest run)
            if clean_kb:
                logger.info("Cleaning KB for fresh backtest run...")
                cleanup_kb(kb_root="kb", preserve_strategies=True)

            # Log current KB state
            log_kb_state(kb_root="kb")
            self.kb_writer = KBWriter("kb")
            self.kb_reader = KBReader("kb")
            self.decision_analyzer = DecisionAnalyzer(
                min_buy=min_buy_amount,
                max_buy=max_buy_amount,
                min_sell=min_sell_amount,
                max_sell=max_sell_amount
            )
            # Trade planner for planning-with-files methodology
            self.trade_planner = TradePlanner("kb")

        # Track decisions for KB analysis
        self.daily_decisions: Dict[str, List[dict]] = {}  # date -> decisions
        self.daily_stock_data: Dict[str, Dict[str, dict]] = {}  # date -> stock_data
        self.trade_action_count: int = 0  # For 2-action rule

    def fetch_historical_data(self):
        """
        Fetch historical data for all symbols.
        Uses Robinhood API via robin_stocks.
        """
        from ..api import robinhood

        logger.info(f"Fetching historical data for {len(self.symbols)} symbols...")

        for symbol in self.symbols:
            try:
                # Fetch 1 year of daily data
                raw_data = robinhood.get_historical_data(symbol, interval="day", span="year")
                if raw_data:
                    df = pd.DataFrame(raw_data)
                    df['begins_at'] = pd.to_datetime(df['begins_at']).dt.strftime('%Y-%m-%d')
                    df['close_price'] = pd.to_numeric(df['close_price'], errors='coerce')
                    df['open_price'] = pd.to_numeric(df['open_price'], errors='coerce')
                    df['high_price'] = pd.to_numeric(df['high_price'], errors='coerce')
                    df['low_price'] = pd.to_numeric(df['low_price'], errors='coerce')
                    df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
                    df = df.set_index('begins_at')
                    self.historical_data[symbol] = df
                    logger.debug(f"Fetched {len(df)} days of data for {symbol}")
                else:
                    logger.warning(f"No historical data available for {symbol}")
            except Exception as e:
                logger.error(f"Error fetching data for {symbol}: {e}")

        # Build list of trading days within date range
        if self.historical_data:
            # Use the first symbol's dates as reference
            first_symbol = list(self.historical_data.keys())[0]
            all_dates = self.historical_data[first_symbol].index.tolist()

            start_str = self.start_date.strftime('%Y-%m-%d')
            end_str = self.end_date.strftime('%Y-%m-%d')

            self.trading_days = [
                d for d in all_dates
                if start_str <= d <= end_str
            ]
            logger.info(f"Found {len(self.trading_days)} trading days in range")

    def get_price_on_date(self, symbol: str, date: str) -> Optional[float]:
        """Get closing price for a symbol on a specific date."""
        if symbol not in self.historical_data:
            return None
        df = self.historical_data[symbol]
        if date in df.index:
            return float(df.loc[date, 'close_price'])
        return None

    def get_prices_on_date(self, date: str) -> Dict[str, float]:
        """Get all prices for a specific date."""
        prices = {}
        for symbol in self.symbols:
            price = self.get_price_on_date(symbol, date)
            if price:
                prices[symbol] = price
        return prices

    def calculate_rsi(self, symbol: str, date: str, period: int = 14) -> Optional[float]:
        """Calculate RSI for a symbol up to a specific date."""
        if symbol not in self.historical_data:
            return None

        df = self.historical_data[symbol]
        # Get data up to and including the date
        mask = df.index <= date
        prices = df.loc[mask, 'close_price'].tolist()

        if len(prices) < period + 1:
            return None

        # Calculate RSI
        deltas = pd.Series(prices).diff()
        gain = deltas.where(deltas > 0, 0)
        loss = -deltas.where(deltas < 0, 0)

        avg_gain = gain.rolling(window=period).mean().iloc[-1]
        avg_loss = loss.rolling(window=period).mean().iloc[-1]

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(float(rsi), 2)

    def calculate_moving_averages(self, symbol: str, date: str) -> Dict[str, Optional[float]]:
        """Calculate 50-day and 200-day moving averages up to a specific date."""
        if symbol not in self.historical_data:
            return {"ma_50": None, "ma_200": None}

        df = self.historical_data[symbol]
        mask = df.index <= date
        prices = df.loc[mask, 'close_price']

        ma_50 = None
        ma_200 = None

        if len(prices) >= 50:
            ma_50 = round(float(prices.rolling(window=50).mean().iloc[-1]), 2)
        if len(prices) >= 200:
            ma_200 = round(float(prices.rolling(window=200).mean().iloc[-1]), 2)

        return {"ma_50": ma_50, "ma_200": ma_200}

    def build_stock_data(self, date: str) -> Dict[str, dict]:
        """
        Build stock data for AI analysis on a specific date.
        Includes current holdings and available stocks.
        """
        prices = self.get_prices_on_date(date)
        stock_data = {}

        # Get current holdings from portfolio
        holdings = self.portfolio.get_holdings_data(prices)

        for symbol in self.symbols:
            price = prices.get(symbol)
            if price is None:
                continue

            # Calculate indicators
            rsi = self.calculate_rsi(symbol, date)
            mas = self.calculate_moving_averages(symbol, date)

            data = {
                "current_price": round(price, 2),
                "my_quantity": 0,
                "my_average_buy_price": 0,
            }

            # If we hold this stock, add our position info
            if symbol in holdings:
                data["my_quantity"] = round(holdings[symbol]["quantity"], 6)
                data["my_average_buy_price"] = round(holdings[symbol]["average_buy_price"], 2)

            # Add indicators
            if rsi is not None:
                data["rsi"] = rsi
            if mas["ma_50"] is not None:
                data["50_day_mavg_price"] = mas["ma_50"]
            if mas["ma_200"] is not None:
                data["200_day_mavg_price"] = mas["ma_200"]

            stock_data[symbol] = data

        return stock_data

    def get_kb_context(self, date: str) -> str:
        """Get Knowledge Base context for AI prompt."""
        if not self.enable_kb or not self.kb_reader.kb_exists():
            return ""

        try:
            context = self.kb_reader.get_context_for_trading(
                symbols=self.symbols,
                current_date=date,
                max_history_days=5,
                max_context_chars=3000
            )
            return context
        except Exception as e:
            logger.debug(f"Error getting KB context: {e}")
            return ""

    def make_ai_decisions(self, date: str, stock_data: Dict[str, dict]) -> List[dict]:
        """
        Call AI to make trading decisions.
        Includes KB context for informed decision-making.
        """
        from ..api import ai

        # Build constraints
        prices = self.get_prices_on_date(date)
        buying_power = self.portfolio.cash

        constraints = [
            f"- Initial budget: {buying_power:.2f} USD",
            f"- Max portfolio size: {self.portfolio_limit} stocks",
        ]

        if self.min_sell_amount or self.max_sell_amount:
            sell_guide = []
            if self.min_sell_amount:
                sell_guide.append(f"Minimum amount {self.min_sell_amount} USD")
            if self.max_sell_amount:
                sell_guide.append(f"Maximum amount {self.max_sell_amount} USD")
            constraints.append(f"- Sell Amounts Guidelines: {', '.join(sell_guide)}")

        if self.min_buy_amount or self.max_buy_amount:
            buy_guide = []
            if self.min_buy_amount:
                buy_guide.append(f"Minimum amount {self.min_buy_amount} USD")
            if self.max_buy_amount:
                buy_guide.append(f"Maximum amount {self.max_buy_amount} USD")
            constraints.append(f"- Buy Amounts Guidelines: {', '.join(buy_guide)}")

        # Get KB context (learnings from past days)
        kb_context = self.get_kb_context(date)

        ai_prompt = (
            "**Context:**\n"
            f"Today is {date}.\n"
            "You are a short-term investment advisor managing a stock portfolio.\n"
            "You analyze market conditions daily and make investment decisions.\n\n"
        )

        # Add KB context if available
        if kb_context:
            ai_prompt += f"{kb_context}\n"

        ai_prompt += (
            "**Constraints:**\n"
            f"{chr(10).join(constraints)}"
            "\n\n"
            "**Stock Data:**\n"
            "```json\n"
            f"{json.dumps(stock_data, indent=1)}\n"
            "```\n\n"
            "**Response Format:**\n"
            "Return your decisions in a JSON array with this structure:\n"
            "```json\n"
            "[\n"
            '  {"symbol": <symbol>, "decision": <decision>, "quantity": <quantity>},\n'
            "  ...\n"
            "]\n"
            "```\n"
            "- <symbol>: Stock symbol.\n"
            "- <decision>: One of `buy`, `sell`, or `hold`.\n"
            "- <quantity>: Recommended transaction quantity.\n\n"
            "**Instructions:**\n"
            "- Provide only the JSON output with no additional text.\n"
            "- Return an empty array if no actions are necessary."
        )

        logger.debug(f"[{date}] Calling AI for decisions...")

        try:
            ai_response = ai.make_ai_request(ai_prompt)
            decisions = ai.parse_ai_response(ai_response)
            return decisions
        except Exception as e:
            logger.error(f"[{date}] AI decision error: {e}")
            return []

    def filter_decisions(self, stock_data: Dict[str, dict], decisions: List[dict]) -> List[dict]:
        """Filter invalid or hallucinated decisions."""
        filtered = []

        for decision in decisions:
            symbol = decision.get('symbol')
            decision_type = decision.get('decision')
            quantity = decision.get('quantity', 0)

            # Filter decisions with 0 quantity
            if quantity <= 0:
                continue

            # Filter decisions for unknown symbols
            if symbol not in stock_data:
                continue

            # Filter sell decisions for stocks we don't own
            if decision_type == "sell":
                held_qty = self.portfolio.holdings.get(symbol, 0)
                if held_qty <= 0:
                    continue
                # Cap quantity to what we actually hold
                if quantity > held_qty:
                    decision['quantity'] = held_qty

            filtered.append(decision)

        return filtered

    def execute_decisions(self, date: str, decisions: List[dict], prices: Dict[str, float], stock_data: Dict[str, dict]):
        """
        Execute trading decisions with planning-with-files methodology.

        For each trade:
        1. CREATE PLAN FIRST - Build trade plan with 5-question check
        2. READ BEFORE DECIDE - Validate plan, check for blocked trades
        3. EXECUTE - Run the trade
        4. UPDATE AFTER ACT - Record outcome immediately
        """
        for decision in decisions:
            symbol = decision['symbol']
            decision_type = decision['decision']
            quantity = decision['quantity']
            price = prices.get(symbol)

            if not price:
                continue

            if decision_type not in ['buy', 'sell']:
                continue

            # ===== Phase 1: CREATE PLAN FIRST =====
            if self.enable_kb:
                # Get KB context and past trades
                kb_context = self.get_kb_context(date)
                past_patterns = self.kb_reader.get_past_patterns([symbol], limit=10)

                # Build portfolio state for 5-question check
                portfolio_state = {
                    'cash': self.portfolio.cash,
                    'holdings': dict(self.portfolio.holdings)
                }

                # Create trade plan
                plan = self.trade_planner.create_trade_plan(
                    symbol=symbol,
                    action=decision_type,
                    quantity=quantity,
                    price=price,
                    date=date,
                    portfolio_state=portfolio_state,
                    kb_context=kb_context,
                    past_trades=past_patterns
                )

                # ===== Phase 2: READ BEFORE DECIDE (Validate) =====
                is_valid, reason = self.trade_planner.validate_trade_plan(symbol)

                if not is_valid:
                    logger.warning(f"[{date}] {symbol} {decision_type} BLOCKED: {reason}")
                    # Record as error (3-strike tracking)
                    self.trade_planner.record_execution_error(
                        symbol=symbol,
                        error_type="validation_blocked",
                        error_message=reason,
                        date=date
                    )
                    continue

                # ===== Phase 3: EXECUTE =====
                self.trade_planner.mark_executing(symbol)

            # Execute the trade
            if decision_type == "sell":
                result = self.portfolio.sell(symbol, quantity, price, date)
                success = result["success"]
                if success:
                    logger.info(f"[{date}] SELL {symbol}: {quantity:.4f} @ ${price:.2f}")
                else:
                    error_msg = result.get('error', 'Unknown error')
                    logger.debug(f"[{date}] Sell {symbol} failed: {error_msg}")
                    # Log error with attempts (never repeat failures)
                    if self.enable_kb:
                        self.trade_planner.record_execution_error(
                            symbol=symbol,
                            error_type="sell_failed",
                            error_message=error_msg,
                            date=date
                        )

            elif decision_type == "buy":
                result = self.portfolio.buy(symbol, quantity, price, date)
                success = result["success"]
                if success:
                    logger.info(f"[{date}] BUY {symbol}: {quantity:.4f} @ ${price:.2f}")
                else:
                    error_msg = result.get('error', 'Unknown error')
                    logger.debug(f"[{date}] Buy {symbol} failed: {error_msg}")
                    # Log error with attempts (never repeat failures)
                    if self.enable_kb:
                        self.trade_planner.record_execution_error(
                            symbol=symbol,
                            error_type="buy_failed",
                            error_message=error_msg,
                            date=date
                        )

            # ===== Phase 4: UPDATE AFTER ACT =====
            if self.enable_kb:
                self.trade_planner.complete_trade(
                    symbol=symbol,
                    success=success,
                    outcome_price=price if success else None
                )

                # 2-Action Rule: Update KB after every 2 trades
                self.trade_action_count += 1
                if self.trade_action_count % 2 == 0:
                    logger.debug(f"[{date}] 2-Action Rule: KB update triggered after {self.trade_action_count} trades")

    def get_historical_returns(self, date: str) -> Dict[str, List[float]]:
        """
        Get historical daily returns for each symbol up to the given date.
        Used for statistical analysis (KS/AD tests).
        """
        returns = {}

        for symbol in self.symbols:
            if symbol not in self.historical_data:
                continue

            df = self.historical_data[symbol]
            mask = df.index < date  # Only data before this date
            prices = df.loc[mask, 'close_price'].tolist()

            # Calculate daily returns
            if len(prices) >= 2:
                daily_returns = [
                    (prices[i] - prices[i-1]) / prices[i-1]
                    for i in range(1, len(prices))
                    if prices[i-1] > 0
                ]
                returns[symbol] = daily_returns

        return returns

    def analyze_and_write_kb(self, date: str, decisions: List[dict], stock_data: Dict[str, dict]):
        """
        Analyze decisions and write to Knowledge Base.
        Called at end of each trading day.

        Uses statistical analysis (KS/AD tests) for quadrant classification:
        - Q1/Q3 (correct decisions) -> saved as "learnings"
        - Q2/Q4 (incorrect decisions) -> saved as "errors"
        """
        if not self.enable_kb or not decisions:
            return

        # Get next day's prices for outcome evaluation
        date_idx = self.trading_days.index(date) if date in self.trading_days else -1
        next_day_prices = {}

        if date_idx >= 0 and date_idx < len(self.trading_days) - 1:
            next_date = self.trading_days[date_idx + 1]
            next_day_prices = self.get_prices_on_date(next_date)

        # Get past patterns for pattern matching
        past_patterns = self.kb_reader.get_past_patterns(self.symbols, limit=20)

        # Get historical returns for statistical analysis
        historical_returns = self.get_historical_returns(date)

        # Analyze all decisions WITH statistical quadrant analysis
        try:
            analyses = analyze_day_with_statistics(
                decisions=decisions,
                stock_data=stock_data,
                next_day_prices=next_day_prices,
                analyzer=self.decision_analyzer,
                historical_returns=historical_returns,
                market_return=0.0,
                past_patterns=past_patterns
            )

            if analyses:
                # Get portfolio state
                prices = self.get_prices_on_date(date)
                starting_value = self.portfolio.portfolio_history[-2]["total_value"] if len(self.portfolio.portfolio_history) > 1 else self.starting_cash
                ending_value = self.portfolio.get_portfolio_value(prices)

                # Write to KB
                self.kb_writer.write_daily_summary(
                    date=date,
                    starting_value=starting_value,
                    ending_value=ending_value,
                    analyses=analyses,
                    portfolio_holdings=dict(self.portfolio.holdings),
                    cash=self.portfolio.cash
                )

                # Log quadrant summary
                q_counts = {"Q1": 0, "Q2": 0, "Q3": 0, "Q4": 0}
                for a in analyses:
                    if a.quadrant:
                        q_key = a.quadrant.split("_")[0]
                        q_counts[q_key] = q_counts.get(q_key, 0) + 1

                logger.debug(f"[{date}] KB: {len(analyses)} decisions | Q1:{q_counts['Q1']} Q2:{q_counts['Q2']} Q3:{q_counts['Q3']} Q4:{q_counts['Q4']}")

        except Exception as e:
            logger.debug(f"[{date}] Error writing KB entry: {e}")

    def run(self) -> dict:
        """
        Run the backtest.

        Returns:
            Backtest results including metrics and report
        """
        logger.info("=" * 60)
        logger.info("STARTING BACKTEST")
        logger.info(f"Symbols: {', '.join(self.symbols)}")
        logger.info(f"Date range: {self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}")
        logger.info(f"Starting cash: ${self.starting_cash:,.2f}")
        logger.info(f"Knowledge Base: {'Enabled' if self.enable_kb else 'Disabled'}")
        logger.info("=" * 60)

        # Fetch historical data
        self.fetch_historical_data()

        if not self.trading_days:
            logger.error("No trading days found in date range")
            return {"error": "No trading days found"}

        # Step through each trading day
        for i, date in enumerate(self.trading_days):
            prices = self.get_prices_on_date(date)

            if not prices:
                continue

            # Build stock data for AI
            stock_data = self.build_stock_data(date)

            if not stock_data:
                continue

            # Store for KB analysis
            self.daily_stock_data[date] = stock_data

            # Get AI decisions (includes KB context)
            decisions = self.make_ai_decisions(date, stock_data)

            # Filter invalid decisions
            decisions = self.filter_decisions(stock_data, decisions)

            # Store decisions for KB
            self.daily_decisions[date] = decisions

            # Execute decisions (with trade planner for each action)
            if decisions:
                self.execute_decisions(date, decisions, prices, stock_data)

            # Record daily snapshot
            self.portfolio.record_daily_snapshot(date, prices)

            # Write KB entry for previous day (now we have outcome data)
            if i > 0:
                prev_date = self.trading_days[i - 1]
                prev_decisions = self.daily_decisions.get(prev_date, [])
                prev_stock_data = self.daily_stock_data.get(prev_date, {})
                if prev_decisions:
                    self.analyze_and_write_kb(prev_date, prev_decisions, prev_stock_data)

            # Progress logging
            if (i + 1) % 20 == 0 or i == len(self.trading_days) - 1:
                value = self.portfolio.get_portfolio_value(prices)
                logger.info(f"Progress: Day {i + 1}/{len(self.trading_days)} | Value: ${value:,.2f}")

        # Write KB entry for last day
        if self.trading_days:
            last_date = self.trading_days[-1]
            last_decisions = self.daily_decisions.get(last_date, [])
            last_stock_data = self.daily_stock_data.get(last_date, {})
            if last_decisions:
                self.analyze_and_write_kb(last_date, last_decisions, last_stock_data)

        # Calculate final metrics
        logger.info("Calculating metrics...")
        metrics = calculate_metrics(self.portfolio)

        # Generate report
        report = format_report(metrics, self.portfolio)

        logger.info("\n" + report)

        # Log KB statistics if enabled
        if self.enable_kb:
            try:
                kb_stats = self.kb_reader.get_statistics()
                logger.info(f"\nKB Statistics: {kb_stats['total_days']} days, {kb_stats['total_decisions']} decisions analyzed")
                if kb_stats['total_decisions'] > 0:
                    logger.info(f"Avg Skill Score: {kb_stats['avg_skill_score']:.0f}, Win Rate: {kb_stats['win_rate']:.1f}%")
            except Exception:
                pass

        return {
            "metrics": metrics,
            "report": report,
            "transactions": self.portfolio.transactions,
            "portfolio_history": self.portfolio.portfolio_history
        }


async def run_backtest(
    symbols: List[str],
    start_date: str,
    end_date: str,
    starting_cash: float,
    transaction_fee: float = 0.0,
    portfolio_limit: int = 10,
    min_buy_amount: float = 1.0,
    max_buy_amount: float = 10.0,
    min_sell_amount: float = 1.0,
    max_sell_amount: float = 10.0,
    enable_kb: bool = True,
    clean_kb: bool = False,
) -> dict:
    """
    Convenience function to run a backtest.
    Handles Robinhood login before starting.
    """
    from ..api import robinhood

    logger.info("Logging in to Robinhood for historical data access...")
    login_resp = await robinhood.login_to_robinhood()

    if not login_resp:
        return {"error": "Failed to login to Robinhood"}

    engine = BacktestEngine(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        starting_cash=starting_cash,
        transaction_fee=transaction_fee,
        portfolio_limit=portfolio_limit,
        min_buy_amount=min_buy_amount,
        max_buy_amount=max_buy_amount,
        min_sell_amount=min_sell_amount,
        max_sell_amount=max_sell_amount,
        enable_kb=enable_kb,
        clean_kb=clean_kb,
    )

    return engine.run()
