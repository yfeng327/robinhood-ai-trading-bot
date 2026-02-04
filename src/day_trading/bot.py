"""
Day Trading Bot - Core intraday trading logic.

Runs during market hours, making AI-driven trading decisions.
Reads from KB for context but does NOT write to KB during trading.
Buffers all decisions for end-of-day review.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.api import robinhood, ai
from src.kb import KBReader
from .decision_buffer import DecisionBuffer

logger = logging.getLogger(__name__)


class DayTradingBot:
    """
    Handles intraday trading operations.
    
    Key difference from original trading_bot():
    - Does NOT write to KB during trading cycles
    - Buffers all decisions for end-of-day review
    - Uses DecisionBuffer instead of LiveKBTracker for writes
    """
    
    def __init__(
        self,
        kb_reader: KBReader,
        decision_buffer: DecisionBuffer,
        portfolio_limit: int = 10,
        min_buy: float = 1.0,
        max_buy: float = 10000.0,
        min_sell: float = 1.0,
        max_sell: float = 10000.0,
        trade_exceptions: List[str] = None,
        watchlist_names: List[str] = None,
        watchlist_limit: int = 10,
        run_interval: int = 600,
    ):
        """
        Initialize the day trading bot.
        
        Args:
            kb_reader: KBReader for getting context
            decision_buffer: DecisionBuffer for storing decisions
            portfolio_limit: Max stocks to hold
            min_buy/max_buy: Buy amount limits
            min_sell/max_sell: Sell amount limits
            trade_exceptions: Symbols to exclude from trading
            watchlist_names: Watchlist names to monitor
            watchlist_limit: Max watchlist stocks to analyze per cycle
            run_interval: Seconds between cycles
        """
        self.kb_reader = kb_reader
        self.decision_buffer = decision_buffer
        self.portfolio_limit = portfolio_limit
        self.min_buy = min_buy
        self.max_buy = max_buy
        self.min_sell = min_sell
        self.max_sell = max_sell
        self.trade_exceptions = trade_exceptions or []
        self.watchlist_names = watchlist_names or []
        self.watchlist_limit = watchlist_limit
        self.run_interval = run_interval
    
    def run_cycle(self) -> Dict:
        """
        Execute one trading cycle.
        
        Returns:
            Dict of trading results by symbol
        """
        logger.info("Getting account info...")
        account_info = robinhood.get_account_info()
        
        logger.info("Getting portfolio stocks...")
        portfolio_stocks = robinhood.get_portfolio_stocks()
        
        # Calculate current portfolio value
        current_portfolio_value = float(account_info.get('buying_power', 0))
        current_holdings = {}
        for symbol, stock in portfolio_stocks.items():
            qty = float(stock.get('quantity', 0))
            price = float(stock.get('price', 0))
            current_portfolio_value += qty * price
            current_holdings[symbol] = qty
        
        # Log portfolio composition
        portfolio_stocks_value = sum(
            float(s['price']) * float(s['quantity']) 
            for s in portfolio_stocks.values()
        )
        if portfolio_stocks_value > 0:
            portfolio = [
                f"{sym} ({round(float(s['price']) * float(s['quantity']) / portfolio_stocks_value * 100, 2)}%)"
                for sym, s in portfolio_stocks.items()
            ]
            logger.info(f"Portfolio: {', '.join(portfolio) if portfolio else 'None'}")
        
        # Prepare portfolio overview
        logger.info("Preparing portfolio for AI analysis...")
        portfolio_overview, intraday_summaries = self._prepare_stock_overview(
            portfolio_stocks, is_portfolio=True
        )
        
        # Get watchlist stocks
        watchlist_overview = {}
        if self.watchlist_names:
            watchlist_stocks = self._get_watchlist_stocks(portfolio_stocks)
            if watchlist_stocks:
                logger.info("Preparing watchlist for AI analysis...")
                watchlist_overview, watchlist_intraday = self._prepare_stock_overview(
                    {s['symbol']: s for s in watchlist_stocks}, is_portfolio=False
                )
                intraday_summaries.update(watchlist_intraday)
        
        if not portfolio_overview and not watchlist_overview:
            logger.warning("No stocks to analyze, skipping AI decision-making...")
            return {}
        
        # Make AI decisions
        all_stock_data = {**portfolio_overview, **watchlist_overview}
        
        try:
            logger.info("Making AI-based decisions...")
            decisions = self._make_ai_decisions(
                account_info, portfolio_overview, watchlist_overview, intraday_summaries
            )
        except Exception as e:
            logger.error(f"Error making AI decisions: {e}")
            decisions = []
        
        # Filter hallucinations
        logger.info("Filtering AI hallucinations...")
        decisions = self._filter_decisions(
            account_info, portfolio_overview, watchlist_overview, decisions
        )
        
        if not decisions:
            logger.info("No decisions to execute")
            return {}
        
        # Execute decisions and buffer for EOD review
        logger.info("Executing decisions...")
        trading_results = self._execute_decisions(decisions, all_stock_data)
        
        return trading_results
    
    def _prepare_stock_overview(
        self, 
        stocks: Dict, 
        is_portfolio: bool
    ) -> Tuple[Dict, Dict]:
        """
        Prepare stock data for AI analysis.
        
        Args:
            stocks: Stock data dict
            is_portfolio: True if portfolio stocks, False if watchlist
            
        Returns:
            Tuple of (overview dict, intraday summaries dict)
        """
        overview = {}
        intraday_summaries = {}
        
        for symbol, stock_data in stocks.items():
            historical_day = robinhood.get_historical_data(symbol, interval="5minute", span="day")
            historical_year = robinhood.get_historical_data(symbol, interval="day", span="year")
            ratings = robinhood.get_ratings(symbol)
            
            if is_portfolio:
                overview[symbol] = robinhood.extract_my_stocks_data(stock_data)
            else:
                overview[symbol] = robinhood.extract_watchlist_data(stock_data)
            
            # Enrich with indicators
            overview[symbol] = robinhood.enrich_with_rsi(overview[symbol], historical_day, symbol)
            overview[symbol] = robinhood.enrich_with_vwap(overview[symbol], historical_day, symbol)
            overview[symbol] = robinhood.enrich_with_relative_volume(
                overview[symbol], historical_day, historical_year, symbol
            )
            overview[symbol] = robinhood.enrich_with_moving_averages(
                overview[symbol], historical_year, symbol
            )
            overview[symbol] = robinhood.enrich_with_analyst_ratings(overview[symbol], ratings)
            overview[symbol] = robinhood.enrich_with_pdt_restrictions(overview[symbol], symbol)
            
            # Build intraday summary
            intraday = robinhood.build_intraday_summary(historical_day, symbol)
            if intraday:
                intraday_summaries[symbol] = intraday
        
        return overview, intraday_summaries
    
    def _get_watchlist_stocks(self, portfolio_stocks: Dict) -> List[Dict]:
        """Get watchlist stocks, excluding those already in portfolio."""
        watchlist_stocks = []
        
        for watchlist_name in self.watchlist_names:
            try:
                stocks = robinhood.get_watchlist_stocks(watchlist_name)
                watchlist_stocks.extend(stocks)
                # Dedupe
                watchlist_stocks = [dict(t) for t in {tuple(d.items()) for d in watchlist_stocks}]
            except Exception as e:
                logger.error(f"Error getting watchlist '{watchlist_name}': {e}")
        
        # Limit and exclude portfolio stocks
        if len(watchlist_stocks) > self.watchlist_limit:
            watchlist_stocks = self._limit_by_month(watchlist_stocks, self.watchlist_limit)
        
        watchlist_stocks = [
            s for s in watchlist_stocks 
            if s['symbol'] not in portfolio_stocks
        ]
        
        if watchlist_stocks:
            logger.info(f"Watchlist: {', '.join(s['symbol'] for s in watchlist_stocks)}")
        
        return watchlist_stocks
    
    def _limit_by_month(self, stocks: List[Dict], limit: int) -> List[Dict]:
        """Limit watchlist stocks by rotating based on month."""
        if len(stocks) <= limit:
            return stocks
        
        stocks = sorted(stocks, key=lambda x: x['symbol'])
        current_month = datetime.now().month
        num_parts = (len(stocks) + limit - 1) // limit
        part_index = (current_month - 1) % num_parts
        start = part_index * limit
        end = min(start + limit, len(stocks))
        
        return stocks[start:end]
    
    def _get_amount_guidelines(self) -> Tuple[Optional[str], Optional[str]]:
        """Get AI amount guidelines for buy/sell."""
        sell_parts = []
        if self.min_sell:
            sell_parts.append(f"Minimum amount {self.min_sell} USD")
        if self.max_sell:
            sell_parts.append(f"Maximum amount {self.max_sell} USD")
        sell_guidelines = ", ".join(sell_parts) if sell_parts else None
        
        buy_parts = []
        if self.min_buy:
            buy_parts.append(f"Minimum amount {self.min_buy} USD")
        if self.max_buy:
            buy_parts.append(f"Maximum amount {self.max_buy} USD")
        buy_guidelines = ", ".join(buy_parts) if buy_parts else None
        
        return sell_guidelines, buy_guidelines
    
    def _get_kb_context(self, symbols: List[str]) -> str:
        """Get KB context for AI prompt."""
        if not self.kb_reader.kb_exists():
            return ""
        
        try:
            current_date = datetime.now().strftime('%Y-%m-%d')
            return self.kb_reader.get_context_for_trading(
                symbols=symbols,
                current_date=current_date,
                max_history_days=5,
                max_context_chars=4800
            )
        except Exception as e:
            logger.debug(f"Error getting KB context: {e}")
            return ""
    
    def _make_ai_decisions(
        self,
        account_info: Dict,
        portfolio_overview: Dict,
        watchlist_overview: Dict,
        intraday_summaries: Dict
    ) -> List[Dict]:
        """Make AI-based trading decisions."""
        # Build constraints
        constraints = [
            f"- Initial budget: {account_info['buying_power']} USD",
            f"- Max portfolio size: {self.portfolio_limit} stocks",
        ]
        
        sell_guide, buy_guide = self._get_amount_guidelines()
        if sell_guide:
            constraints.append(f"- Sell Amounts Guidelines: {sell_guide}")
        if buy_guide:
            constraints.append(f"- Buy Amounts Guidelines: {buy_guide}")
        if self.trade_exceptions:
            constraints.append(f"- Excluded stocks: {', '.join(self.trade_exceptions)}")
        
        # Get KB context
        all_symbols = list(portfolio_overview.keys()) + list(watchlist_overview.keys())
        kb_context = self._get_kb_context(all_symbols)
        
        # Build prompt
        prompt = (
            "**Context:**\n"
            f"Today is {datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')}.\n"
            f"You are a short-term investment advisor managing a stock portfolio.\n"
            f"You analyze market conditions every {self.run_interval} seconds and make investment decisions.\n\n"
        )
        
        if kb_context:
            prompt += f"{kb_context}\n\n"
        
        prompt += (
            "**Constraints:**\n"
            f"{chr(10).join(constraints)}\n\n"
            "**Stock Data:**\n"
            "```json\n"
            f"{json.dumps({**portfolio_overview, **watchlist_overview}, indent=1)}\n"
            "```\n\n"
        )
        
        if intraday_summaries:
            prompt += "**Intraday Price & Volume:**\n"
            for symbol, table in intraday_summaries.items():
                prompt += f"{table}\n\n"
        
        prompt += (
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
        
        logger.debug(f"AI prompt:\n{prompt}")
        response = ai.make_ai_request(prompt)
        logger.debug(f"AI response:\n{ai.get_raw_response_content(response)}")
        
        return ai.parse_ai_response(response)
    
    def _filter_decisions(
        self,
        account_info: Dict,
        portfolio_overview: Dict,
        watchlist_overview: Dict,
        decisions: List[Dict]
    ) -> List[Dict]:
        """Filter out invalid or hallucinated decisions."""
        filtered = []
        
        for d in decisions:
            symbol = d.get('symbol')
            decision_type = d.get('decision')
            quantity = d.get('quantity', 0)
            
            # Filter trade exceptions
            if symbol in self.trade_exceptions:
                logger.debug(f"Filtering {symbol} - in trade exceptions")
                continue
            
            # Filter zero quantity
            if quantity == 0:
                logger.debug(f"Filtering {symbol} - zero quantity")
                continue
            
            # Get stock data
            stock_data = portfolio_overview.get(symbol) or watchlist_overview.get(symbol)
            if not stock_data:
                logger.debug(f"Filtering {symbol} - not in portfolio or watchlist")
                continue
            
            # Filter PDT restrictions
            if decision_type == "buy" and stock_data.get("is_buy_pdt_restricted"):
                logger.debug(f"Filtering {symbol} - PDT buy restricted")
                continue
            if decision_type == "sell" and stock_data.get("is_sell_pdt_restricted"):
                logger.debug(f"Filtering {symbol} - PDT sell restricted")
                continue
            
            filtered.append(d)
        
        logger.debug(f"Filtered {len(decisions) - len(filtered)} decisions")
        return filtered
    
    def _execute_decisions(
        self,
        decisions: List[Dict],
        stock_data: Dict
    ) -> Dict:
        """
        Execute trading decisions and buffer for EOD review.
        
        Args:
            decisions: List of AI decisions
            stock_data: All stock data
            
        Returns:
            Dict of trading results by symbol
        """
        trading_results = {}
        trade_ts = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        
        for d in decisions:
            symbol = d['symbol']
            decision = d['decision']
            quantity = d['quantity']
            
            logger.info(f"[{trade_ts}] {symbol} > Decision: {decision} of {quantity}")
            
            # Buffer the decision for EOD review (regardless of execution result)
            self.decision_buffer.record_decision(
                symbol=symbol,
                decision=decision,
                quantity=quantity,
                stock_data=stock_data.get(symbol, {}),
                timestamp=trade_ts
            )
            
            # Execute the trade
            result = None
            if decision == "sell":
                result = self._execute_sell(symbol, quantity, trade_ts)
            elif decision == "buy":
                result = self._execute_buy(symbol, quantity, trade_ts)
            
            if result:
                trading_results[symbol] = result
                self.decision_buffer.record_trade_result(
                    symbol=symbol,
                    result=result.get('result', 'unknown'),
                    details=result
                )
        
        return trading_results
    
    def _execute_sell(self, symbol: str, quantity: float, trade_ts: str) -> Dict:
        """Execute a sell order."""
        try:
            resp = robinhood.sell_stock(symbol, quantity)
            if resp and 'id' in resp:
                if resp['id'] == "demo":
                    logger.info(f"[{trade_ts}] {symbol} > Demo > Sold {quantity}")
                    return {"symbol": symbol, "quantity": quantity, "decision": "sell", 
                            "result": "success", "details": "Demo mode", "timestamp": trade_ts}
                elif resp['id'] == "cancelled":
                    logger.info(f"[{trade_ts}] {symbol} > Sell cancelled")
                    return {"symbol": symbol, "quantity": quantity, "decision": "sell",
                            "result": "cancelled", "details": "Cancelled by user", "timestamp": trade_ts}
                else:
                    details = robinhood.extract_sell_response_data(resp)
                    logger.info(f"[{trade_ts}] {symbol} > Sold {quantity}")
                    return {"symbol": symbol, "quantity": quantity, "decision": "sell",
                            "result": "success", "details": details, "timestamp": trade_ts}
            else:
                details = resp.get('detail', resp) if resp else "Unknown error"
                logger.error(f"[{trade_ts}] {symbol} > Error selling: {details}")
                return {"symbol": symbol, "quantity": quantity, "decision": "sell",
                        "result": "error", "details": details, "timestamp": trade_ts}
        except Exception as e:
            logger.error(f"[{trade_ts}] {symbol} > Error selling: {e}")
            return {"symbol": symbol, "quantity": quantity, "decision": "sell",
                    "result": "error", "details": str(e), "timestamp": trade_ts}
    
    def _execute_buy(self, symbol: str, quantity: float, trade_ts: str) -> Dict:
        """Execute a buy order."""
        try:
            resp = robinhood.buy_stock(symbol, quantity)
            if resp and 'id' in resp:
                if resp['id'] == "demo":
                    logger.info(f"[{trade_ts}] {symbol} > Demo > Bought {quantity}")
                    return {"symbol": symbol, "quantity": quantity, "decision": "buy",
                            "result": "success", "details": "Demo mode", "timestamp": trade_ts}
                elif resp['id'] == "cancelled":
                    logger.info(f"[{trade_ts}] {symbol} > Buy cancelled")
                    return {"symbol": symbol, "quantity": quantity, "decision": "buy",
                            "result": "cancelled", "details": "Cancelled by user", "timestamp": trade_ts}
                else:
                    details = robinhood.extract_buy_response_data(resp)
                    logger.info(f"[{trade_ts}] {symbol} > Bought {quantity}")
                    return {"symbol": symbol, "quantity": quantity, "decision": "buy",
                            "result": "success", "details": details, "timestamp": trade_ts}
            else:
                details = resp.get('detail', resp) if resp else "Unknown error"
                logger.error(f"[{trade_ts}] {symbol} > Error buying: {details}")
                return {"symbol": symbol, "quantity": quantity, "decision": "buy",
                        "result": "error", "details": details, "timestamp": trade_ts}
        except Exception as e:
            logger.error(f"[{trade_ts}] {symbol} > Error buying: {e}")
            return {"symbol": symbol, "quantity": quantity, "decision": "buy",
                    "result": "error", "details": str(e), "timestamp": trade_ts}
