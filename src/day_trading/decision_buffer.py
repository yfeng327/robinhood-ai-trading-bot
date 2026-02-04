"""
Decision Buffer - Stores trading decisions in memory for EOD review.

Buffers decisions throughout the day without writing to KB.
At end of day, provides all decisions to EODReviewer for analysis.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Buffer file for persistence across restarts
BUFFER_FILE = "kb/decision_buffer.json"


class DecisionBuffer:
    """
    Buffers trading decisions in memory for end-of-day review.
    
    Unlike LiveKBTracker which writes after each cycle, this buffer
    accumulates all day's decisions and only provides them to EODReviewer
    at market close.
    """
    
    def __init__(self, buffer_file: str = BUFFER_FILE):
        """Initialize the decision buffer."""
        self.buffer_file = Path(buffer_file)
        self._decisions: List[Dict] = []
        self._trade_results: Dict[str, Dict] = {}
        self._start_of_day_value: Optional[float] = None
        self._current_date: Optional[str] = None
        
        # Load any existing buffer (for crash recovery)
        self._load_buffer()
    
    def _load_buffer(self):
        """Load buffer from disk if exists (crash recovery)."""
        if self.buffer_file.exists():
            try:
                with open(self.buffer_file, 'r') as f:
                    data = json.load(f)
                    self._decisions = data.get('decisions', [])
                    self._trade_results = data.get('trade_results', {})
                    self._start_of_day_value = data.get('start_of_day_value')
                    self._current_date = data.get('current_date')
                    logger.debug(f"Loaded {len(self._decisions)} buffered decisions from disk")
            except Exception as e:
                logger.debug(f"Could not load buffer: {e}")
    
    def _save_buffer(self):
        """Save buffer to disk for crash recovery."""
        try:
            self.buffer_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.buffer_file, 'w') as f:
                json.dump({
                    'decisions': self._decisions,
                    'trade_results': self._trade_results,
                    'start_of_day_value': self._start_of_day_value,
                    'current_date': self._current_date,
                }, f, indent=2)
        except Exception as e:
            logger.debug(f"Could not save buffer: {e}")
    
    def start_new_day(self, portfolio_value: float):
        """
        Start a new trading day.
        
        Args:
            portfolio_value: Portfolio value at start of day
        """
        today = datetime.now().strftime('%Y-%m-%d')
        
        # If we have buffered decisions from a previous day, log warning
        if self._current_date and self._current_date != today and self._decisions:
            logger.warning(
                f"Found {len(self._decisions)} unbuffered decisions from {self._current_date}. "
                f"EOD review may not have run. Clearing buffer for new day."
            )
        
        self._decisions = []
        self._trade_results = {}
        self._start_of_day_value = portfolio_value
        self._current_date = today
        self._save_buffer()
        logger.info(f"Started new trading day: {today}, portfolio value: ${portfolio_value:.2f}")
    
    def record_decision(
        self,
        symbol: str,
        decision: str,
        quantity: float,
        stock_data: Dict,
        timestamp: Optional[str] = None
    ):
        """
        Record a trading decision for later EOD review.
        
        Args:
            symbol: Stock symbol
            decision: 'buy', 'sell', or 'hold'
            quantity: Number of shares
            stock_data: Stock data at time of decision (price, RSI, MAs, etc.)
            timestamp: Optional timestamp, defaults to now
        """
        if timestamp is None:
            timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        
        decision_record = {
            'symbol': symbol,
            'decision': decision,
            'quantity': quantity,
            'stock_data': stock_data,
            'timestamp': timestamp,
            'price': stock_data.get('price', 0),
        }
        
        self._decisions.append(decision_record)
        self._save_buffer()
        logger.debug(f"Buffered decision: {decision} {quantity} {symbol} @ ${stock_data.get('price', 0):.2f}")
    
    def record_trade_result(
        self,
        symbol: str,
        result: str,
        details: Dict
    ):
        """
        Record the result of an executed trade.
        
        Args:
            symbol: Stock symbol
            result: 'success', 'error', or 'cancelled'
            details: Additional trade details
        """
        self._trade_results[symbol] = {
            'result': result,
            'details': details,
            'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        }
        self._save_buffer()
    
    def get_decisions_for_eod(self) -> Dict:
        """
        Get all buffered decisions for EOD review.
        
        Returns:
            Dict containing:
            - decisions: List of decision records
            - trade_results: Dict of trade execution results
            - start_of_day_value: Portfolio value at start of day
            - date: Trading date
        """
        return {
            'decisions': self._decisions.copy(),
            'trade_results': self._trade_results.copy(),
            'start_of_day_value': self._start_of_day_value,
            'date': self._current_date,
        }
    
    def clear_buffer(self):
        """Clear the buffer after EOD review completes."""
        self._decisions = []
        self._trade_results = {}
        self._start_of_day_value = None
        self._current_date = None
        
        # Remove buffer file
        if self.buffer_file.exists():
            try:
                self.buffer_file.unlink()
            except Exception as e:
                logger.debug(f"Could not remove buffer file: {e}")
        
        logger.info("Decision buffer cleared after EOD review")
    
    def get_decision_count(self) -> int:
        """Get number of buffered decisions."""
        return len(self._decisions)
    
    def get_successful_trades(self) -> List[Dict]:
        """Get only successful trade decisions."""
        successful = []
        for decision in self._decisions:
            symbol = decision['symbol']
            if symbol in self._trade_results:
                if self._trade_results[symbol].get('result') == 'success':
                    successful.append(decision)
        return successful
