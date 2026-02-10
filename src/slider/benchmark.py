"""
Benchmark Tracker for SliderBot.

Tracks performance of the bot against static buy-and-hold benchmarks:
- TQQQ (100% allocation)
- QQQ (100% allocation)
- VOO (100% allocation)

Persists state to 'benchmark_state.json' to maintain valid comparisons
across bot restarts.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = "benchmark_state.json"


@dataclass
class BenchmarkItem:
    symbol: str
    start_price: float
    shares: float
    current_price: float = 0.0
    
    @property
    def current_value(self) -> float:
        return self.shares * self.current_price


@dataclass
class BenchmarkState:
    start_time: str
    initial_capital: float
    benchmarks: Dict[str, Dict] = field(default_factory=dict)


class BenchmarkTracker:
    def __init__(self, state_file: str = DEFAULT_STATE_FILE, initial_capital: float = 10000.0):
        self.state_file = Path(state_file)
        self.initial_capital = initial_capital
        self.items: Dict[str, BenchmarkItem] = {}
        self.initialized = False
        self._load_state()

    def _load_state(self):
        """Load benchmark state from file."""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)
            
            self.start_time = data.get("start_time")
            # If initial capital changed in config, we still use the saved one for consistency
            saved_capital = data.get("initial_capital", self.initial_capital)
            self.initial_capital = saved_capital
            
            for symbol, b_data in data.get("benchmarks", {}).items():
                self.items[symbol] = BenchmarkItem(
                    symbol=symbol,
                    start_price=b_data["start_price"],
                    shares=b_data["shares"]
                )
            
            if self.items:
                self.initialized = True
                logger.info(f"Loaded benchmark state from {self.start_time}")
                
        except Exception as e:
            logger.error(f"Failed to load benchmark state: {e}")

    def _save_state(self):
        """Save benchmark state to file."""
        data = {
            "start_time": datetime.now().isoformat(),
            "initial_capital": self.initial_capital,
            "benchmarks": {
                sym: {"start_price": item.start_price, "shares": item.shares}
                for sym, item in self.items.items()
            }
        }
        
        # Preserve original start time if we are just updating
        if hasattr(self, 'start_time') and self.start_time:
            data['start_time'] = self.start_time
            
        try:
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save benchmark state: {e}")

    def initialize(self, prices: Dict[str, float]):
        """
        Initialize benchmarks with current prices.
        Only runs if not already initialized.
        """
        if self.initialized:
            return

        # Ensure we have all needed prices
        required = ["TQQQ", "QQQ", "VOO"]
        if not all(sym in prices and prices[sym] > 0 for sym in required):
            logger.warning("Cannot initialize benchmarks: missing or invalid prices")
            return

        self.start_time = datetime.now().isoformat()
        
        for symbol in required:
            price = prices[symbol]
            shares = self.initial_capital / price
            self.items[symbol] = BenchmarkItem(
                symbol=symbol,
                start_price=price,
                shares=shares,
                current_price=price
            )
        
        self.initialized = True
        self._save_state()
        logger.info(f"Initialized benchmarks at {self.start_time} with ${self.initial_capital}")

    def update(self, prices: Dict[str, float]):
        """Update current prices for benchmarks."""
        if not self.initialized:
            self.initialize(prices)
            return

        for symbol, item in self.items.items():
            if symbol in prices:
                item.current_price = prices[symbol]

    def get_performance(self) -> Dict[str, Dict]:
        """
        Get performance stats for all benchmarks.
        Returns: { "TQQQ": { "value": 10500, "return_pct": 5.0 }, ... }
        """
        stats = {}
        for symbol, item in self.items.items():
            val = item.current_value
            pnl = val - self.initial_capital
            pct = (pnl / self.initial_capital) * 100
            stats[symbol] = {
                "value": val,
                "pnl": pnl,
                "return_pct": pct,
                "price": item.current_price
            }
        return stats

    def format_comparison(self, bot_value: float) -> str:
        """Format a comparison string for logging."""
        bot_pnl = bot_value - self.initial_capital
        bot_pct = (bot_pnl / self.initial_capital) * 100

        lines = [
            "--- Performance Comparison ---",
            f"BOT:   {bot_pct:+.2f}% (${bot_value:,.2f})",
        ]

        for symbol in ["TQQQ", "QQQ", "VOO"]:
            if symbol in self.items:
                stats = self.get_performance()[symbol]
                lines.append(f"{symbol}:  {stats['return_pct']:+.2f}% (${stats['value']:,.2f})")

        lines.append("------------------------------")
        return "\n".join(lines)

    def reset(self, new_capital: float = None):
        """
        Reset benchmarks to start fresh.

        Clears all benchmark data and marks as uninitialized.
        Next call to update() will reinitialize with current prices.

        Args:
            new_capital: Optional new initial capital (default: keep current)
        """
        if new_capital is not None:
            self.initial_capital = new_capital

        self.items.clear()
        self.initialized = False
        self.start_time = None

        # Delete state file
        if self.state_file.exists():
            try:
                self.state_file.unlink()
                logger.info(f"Deleted benchmark state file: {self.state_file}")
            except Exception as e:
                logger.error(f"Failed to delete benchmark state file: {e}")

        logger.info(f"Benchmark tracker reset. Capital: ${self.initial_capital:,.2f}")
