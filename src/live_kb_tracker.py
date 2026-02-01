"""
Live KB Tracker - Manages KB write operations for demo/live trading mode.

In backtest mode, we know the next day's prices immediately.
In live mode, we must wait until the next trading cycle to evaluate outcomes.

This module:
1. Stores pending decisions awaiting outcome evaluation
2. Evaluates previous decisions when new prices arrive
3. Writes analyzed decisions to KB
4. Maintains decision history for pattern analysis
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

from src.kb import KBWriter, KBReader, DecisionAnalyzer
from src.kb.analyzer import DecisionAnalysis

logger = logging.getLogger(__name__)

# File to store pending decisions between cycles
PENDING_DECISIONS_FILE = "kb/pending_decisions.json"


class LiveKBTracker:
    """
    Tracks and evaluates live trading decisions for KB integration.

    Flow:
    1. After AI makes decisions, call `record_decisions()`
    2. On next cycle, call `evaluate_pending_decisions()` with current prices
    3. This writes analyzed decisions to KB
    """

    def __init__(
        self,
        kb_root: str = "kb",
        min_buy: float = 1.0,
        max_buy: float = 10000.0,
        min_sell: float = 1.0,
        max_sell: float = 10000.0
    ):
        self.kb_root = Path(kb_root)
        self.kb_writer = KBWriter(kb_root)
        self.kb_reader = KBReader(kb_root)
        self.decision_analyzer = DecisionAnalyzer(
            min_buy=min_buy,
            max_buy=max_buy,
            min_sell=min_sell,
            max_sell=max_sell
        )
        self.pending_file = self.kb_root / "pending_decisions.json"
        self._ensure_structure()

    def _ensure_structure(self):
        """Ensure KB directory exists."""
        self.kb_root.mkdir(parents=True, exist_ok=True)

    def record_decisions(
        self,
        decisions: List[Dict],
        stock_data: Dict[str, Dict],
        portfolio_value: float,
        cash: float,
        holdings: Dict[str, float]
    ):
        """
        Record decisions made this cycle for later evaluation.

        Args:
            decisions: List of {symbol, decision, quantity} from AI
            stock_data: Current stock data (price, RSI, etc.)
            portfolio_value: Current total portfolio value
            cash: Current cash balance
            holdings: Current holdings {symbol: quantity}
        """
        if not decisions:
            return

        timestamp = datetime.now().isoformat()
        date = datetime.now().strftime("%Y-%m-%d %H:%M")

        pending_data = {
            "timestamp": timestamp,
            "date": date,
            "portfolio_value": portfolio_value,
            "cash": cash,
            "holdings": holdings,
            "decisions": []
        }

        for decision in decisions:
            symbol = decision.get("symbol")
            if symbol and symbol in stock_data:
                pending_data["decisions"].append({
                    "symbol": symbol,
                    "action": decision.get("decision", "hold"),
                    "quantity": decision.get("quantity", 0),
                    "price": stock_data[symbol].get("current_price", 0),
                    "stock_data": stock_data[symbol]
                })

        # Store pending decisions
        try:
            self.pending_file.write_text(
                json.dumps(pending_data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
            logger.debug(f"Recorded {len(pending_data['decisions'])} decisions for later evaluation")
        except Exception as e:
            logger.error(f"Failed to record pending decisions: {e}")

    def evaluate_pending_decisions(
        self,
        current_stock_data: Dict[str, Dict],
        current_portfolio_value: float,
        current_cash: float,
        current_holdings: Dict[str, float]
    ) -> bool:
        """
        Evaluate pending decisions from previous cycle and write to KB.

        Args:
            current_stock_data: Current stock prices (the "next day" prices)
            current_portfolio_value: Current portfolio value
            current_cash: Current cash
            current_holdings: Current holdings

        Returns:
            True if decisions were evaluated and written to KB
        """
        if not self.pending_file.exists():
            return False

        try:
            pending_data = json.loads(self.pending_file.read_text(encoding='utf-8'))
        except Exception as e:
            logger.error(f"Failed to read pending decisions: {e}")
            return False

        if not pending_data.get("decisions"):
            return False

        # Build next_day_prices from current data
        next_day_prices = {}
        for symbol, data in current_stock_data.items():
            next_day_prices[symbol] = data.get("current_price", 0)

        # Reconstruct stock_data from pending
        stock_data = {}
        for d in pending_data["decisions"]:
            symbol = d["symbol"]
            stock_data[symbol] = d["stock_data"]

        # Build decisions list
        decisions = [
            {
                "symbol": d["symbol"],
                "decision": d["action"],
                "quantity": d["quantity"]
            }
            for d in pending_data["decisions"]
        ]

        # Get past patterns for analysis
        symbols = [d["symbol"] for d in pending_data["decisions"]]
        past_patterns = self.kb_reader.get_past_patterns(symbols, limit=20)

        # Analyze decisions
        analyses = self._analyze_decisions(
            decisions=decisions,
            stock_data=stock_data,
            next_day_prices=next_day_prices,
            past_patterns=past_patterns
        )

        if analyses:
            # Write to KB
            try:
                self.kb_writer.write_daily_summary(
                    date=pending_data["date"],
                    starting_value=pending_data["portfolio_value"],
                    ending_value=current_portfolio_value,
                    analyses=analyses,
                    portfolio_holdings=current_holdings,
                    cash=current_cash
                )

                # Log summary
                q_counts = {"Q1": 0, "Q2": 0, "Q3": 0, "Q4": 0}
                for a in analyses:
                    if a.quadrant:
                        q_key = a.quadrant.split("_")[0]
                        q_counts[q_key] = q_counts.get(q_key, 0) + 1

                logger.info(
                    f"KB Updated: {len(analyses)} decisions analyzed | "
                    f"Q1:{q_counts['Q1']} Q2:{q_counts['Q2']} Q3:{q_counts['Q3']} Q4:{q_counts['Q4']}"
                )

            except Exception as e:
                logger.error(f"Failed to write KB summary: {e}")

        # Clear pending file
        try:
            self.pending_file.unlink()
        except Exception:
            pass

        return True

    def _analyze_decisions(
        self,
        decisions: List[Dict],
        stock_data: Dict[str, Dict],
        next_day_prices: Dict[str, float],
        past_patterns: List[Dict]
    ) -> List[DecisionAnalysis]:
        """Analyze a list of decisions."""
        analyses = []

        for decision in decisions:
            symbol = decision.get("symbol")
            if not symbol or symbol not in stock_data:
                continue

            next_price = next_day_prices.get(symbol)
            if next_price is None:
                continue

            try:
                analysis = self.decision_analyzer.analyze_decision(
                    decision=decision,
                    stock_data=stock_data[symbol],
                    next_day_price=next_price,
                    market_return=0.0,
                    past_patterns=past_patterns
                )
                analyses.append(analysis)
            except Exception as e:
                logger.debug(f"Failed to analyze {symbol} decision: {e}")

        return analyses

    def has_pending_decisions(self) -> bool:
        """Check if there are pending decisions to evaluate."""
        return self.pending_file.exists()

    def get_pending_count(self) -> int:
        """Get count of pending decisions."""
        if not self.pending_file.exists():
            return 0

        try:
            pending_data = json.loads(self.pending_file.read_text(encoding='utf-8'))
            return len(pending_data.get("decisions", []))
        except Exception:
            return 0
