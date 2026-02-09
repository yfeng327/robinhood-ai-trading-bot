"""
SliderBot — TQQQ/SQQQ allocation bot based on multi-strategy LLM analysis.

Demo-only mode with $10k pool, disjoint from existing holdings.
Runs on 5-minute intervals.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pytz import timezone

from src.api import robinhood
from .data_feed import QQQDataFeed
from .strategy_nodes import run_all_strategy_nodes
from .synthesizer import synthesize_final_slider, format_slider_for_display
from .kb_materializer import SliderKBWriter

logger = logging.getLogger(__name__)

# Configuration
DEMO_POOL_SIZE = 10000.0  # $10k demo pool
TQQQ_SYMBOL = "TQQQ"
SQQQ_SYMBOL = "SQQQ"
DEFAULT_INTERVAL_SECONDS = 300  # 5 minutes


@dataclass
class DemoPosition:
    """Track demo positions without real trading."""
    tqqq_shares: float = 0.0
    tqqq_avg_cost: float = 0.0
    sqqq_shares: float = 0.0
    sqqq_avg_cost: float = 0.0
    cash: float = DEMO_POOL_SIZE
    
    def get_tqqq_value(self, current_price: float) -> float:
        return self.tqqq_shares * current_price
    
    def get_sqqq_value(self, current_price: float) -> float:
        return self.sqqq_shares * current_price
    
    def get_total_value(self, tqqq_price: float, sqqq_price: float) -> float:
        return self.cash + self.get_tqqq_value(tqqq_price) + self.get_sqqq_value(sqqq_price)
    
    def get_current_slider(self, tqqq_price: float, sqqq_price: float) -> float:
        """Calculate current slider based on position."""
        total = self.get_total_value(tqqq_price, sqqq_price)
        if total <= 0:
            return 0.0
        
        tqqq_pct = self.get_tqqq_value(tqqq_price) / total
        sqqq_pct = self.get_sqqq_value(sqqq_price) / total
        
        # slider = tqqq_pct - sqqq_pct (ranges from -1 to +1)
        return tqqq_pct - sqqq_pct


@dataclass
class SliderHistory:
    """Track slider history for analysis."""
    entries: List[Dict] = field(default_factory=list)
    max_entries: int = 288  # ~24 hours at 5-min intervals
    
    def add(self, timestamp: datetime, slider: float, confidence: float, 
            strategy_results: Dict, pnl: float):
        self.entries.append({
            "timestamp": timestamp.isoformat(),
            "slider": slider,
            "confidence": confidence,
            "strategy_results": strategy_results,
            "pnl": pnl,
        })
        # Trim to max entries
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries:]
    
    def save(self, path: Path):
        """Save history to file."""
        with open(path, 'w') as f:
            json.dump(self.entries, f, indent=2)


class SliderBot:
    """
    TQQQ/SQQQ slider trading bot.
    
    Demo-only mode — no real trades are executed.
    All trades are simulated with a $10k pool.
    """
    
    def __init__(
        self,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        demo_pool: float = DEMO_POOL_SIZE,
        min_slider_change: float = 0.05,  # Minimum change to trigger rebalance
        history_path: Optional[Path] = None,
    ):
        """
        Initialize slider bot.
        
        Args:
            interval_seconds: Seconds between cycles (default 300 = 5 min)
            demo_pool: Initial demo pool size
            min_slider_change: Minimum slider change to trigger rebalance
            history_path: Path to save slider history
        """
        self.interval_seconds = interval_seconds
        self.demo_pool = demo_pool
        self.min_slider_change = min_slider_change
        self.history_path = history_path or Path("slider_history.json")
        
        self.et_tz = timezone('US/Eastern')
        self.data_feed = QQQDataFeed()
        self.position = DemoPosition(cash=demo_pool)
        self.history = SliderHistory()
        self.kb_writer = SliderKBWriter()  # KB materialization
        
        self.current_slider = 0.0
        self.running = False
        
        logger.info(f"SliderBot initialized: ${demo_pool:,.0f} demo pool, {interval_seconds}s interval")
    
    def run(self):
        """Main loop — run until market close or stopped."""
        self.running = True
        logger.info("SliderBot starting...")
        
        while self.running:
            # Check if market is open
            if not self._is_market_hours():
                logger.info("Outside market hours, waiting...")
                time.sleep(60)
                continue
            
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"Cycle error: {e}")
            
            # Wait for next cycle
            logger.info(f"Sleeping {self.interval_seconds} seconds until next cycle...")
            time.sleep(self.interval_seconds)
        
        logger.info("SliderBot stopped")
    
    def run_cycle(self) -> Dict:
        """
        Execute one slider cycle.
        
        Returns:
            Dict with cycle results
        """
        cycle_start = datetime.now(self.et_tz)
        logger.info(f"=== Slider Cycle @ {cycle_start.strftime('%H:%M:%S')} ===")
        
        # 1. Fetch market data
        market_data = self.data_feed.get_market_data()
        market_data_str = self.data_feed.format_for_prompt(market_data)
        opening_range_str = self.data_feed.format_opening_range(market_data)
        gap_info_str = self.data_feed.format_gap_info(market_data)
        
        # 2. Run all strategy nodes concurrently
        strategy_results = run_all_strategy_nodes(
            market_data=market_data_str,
            opening_range=opening_range_str,
            gap_info=gap_info_str,
        )
        
        # 3. Synthesize final slider
        market_summary = f"QQQ @ ${market_data['current_price']:.2f}"
        synthesis = synthesize_final_slider(strategy_results, market_summary)
        
        new_slider = synthesis.get("final_slider", 0.0)
        confidence = synthesis.get("confidence", 0.0)
        
        logger.info("\n" + format_slider_for_display(synthesis))
        
        # 4. Check if rebalance needed
        slider_change = abs(new_slider - self.current_slider)
        
        if slider_change >= self.min_slider_change:
            logger.info(f"Slider change {slider_change:.2f} >= threshold, rebalancing...")
            self._rebalance(new_slider, market_data)
            self.current_slider = new_slider
        else:
            logger.info(f"Slider change {slider_change:.2f} < threshold, holding position")
        
        # 5. Calculate current PnL
        tqqq_price = self._get_price(TQQQ_SYMBOL)
        sqqq_price = self._get_price(SQQQ_SYMBOL)
        total_value = self.position.get_total_value(tqqq_price, sqqq_price)
        pnl = total_value - self.demo_pool
        pnl_pct = (pnl / self.demo_pool) * 100
        
        logger.info(f"Demo Portfolio: ${total_value:,.2f} (PnL: ${pnl:+,.2f} / {pnl_pct:+.2f}%)")
        
        # 6. Save to history
        self.history.add(
            timestamp=cycle_start,
            slider=new_slider,
            confidence=confidence,
            strategy_results={k: v.get("slider", 0) for k, v in strategy_results.items()},
            pnl=pnl,
        )
        self.history.save(self.history_path)
        
        # 7. Materialize to KB
        action_taken = self._infer_action(new_slider) if slider_change >= self.min_slider_change else "HOLD"
        try:
            self.kb_writer.append_decision(
                strategy_results=strategy_results,
                synthesis_result=synthesis,
                current_price=market_data.get('current_price', 0),
                action_taken=action_taken,
            )
        except Exception as e:
            logger.warning(f"KB materialization failed: {e}")
        
        return {
            "timestamp": cycle_start.isoformat(),
            "slider": new_slider,
            "confidence": confidence,
            "rebalanced": slider_change >= self.min_slider_change,
            "pnl": pnl,
            "total_value": total_value,
        }
    
    def _rebalance(self, target_slider: float, market_data: Dict):
        """
        Rebalance demo portfolio to match target slider.
        
        Args:
            target_slider: Target slider (-1 to +1)
            market_data: Current market data
        """
        tqqq_price = self._get_price(TQQQ_SYMBOL)
        sqqq_price = self._get_price(SQQQ_SYMBOL)
        
        if tqqq_price <= 0 or sqqq_price <= 0:
            logger.error("Cannot rebalance: invalid prices")
            return
        
        # Calculate target allocations
        total_value = self.position.get_total_value(tqqq_price, sqqq_price)
        
        if target_slider > 0:
            # Bullish: allocate to TQQQ
            tqqq_target_value = total_value * target_slider
            sqqq_target_value = 0.0
        elif target_slider < 0:
            # Bearish: allocate to SQQQ
            tqqq_target_value = 0.0
            sqqq_target_value = total_value * abs(target_slider)
        else:
            # Neutral: all cash
            tqqq_target_value = 0.0
            sqqq_target_value = 0.0
        
        cash_target = total_value - tqqq_target_value - sqqq_target_value
        
        # Log intended trades
        current_tqqq_value = self.position.get_tqqq_value(tqqq_price)
        current_sqqq_value = self.position.get_sqqq_value(sqqq_price)
        
        tqqq_delta = tqqq_target_value - current_tqqq_value
        sqqq_delta = sqqq_target_value - current_sqqq_value
        
        logger.info(f"[DEMO] Rebalancing to slider={target_slider:+.2f}:")
        logger.info(f"  TQQQ: ${current_tqqq_value:,.2f} → ${tqqq_target_value:,.2f} (Δ${tqqq_delta:+,.2f})")
        logger.info(f"  SQQQ: ${current_sqqq_value:,.2f} → ${sqqq_target_value:,.2f} (Δ${sqqq_delta:+,.2f})")
        logger.info(f"  Cash: ${self.position.cash:,.2f} → ${cash_target:,.2f}")
        
        # Execute demo trades
        tqqq_target_shares = tqqq_target_value / tqqq_price if tqqq_price > 0 else 0
        sqqq_target_shares = sqqq_target_value / sqqq_price if sqqq_price > 0 else 0
        
        self.position.tqqq_shares = tqqq_target_shares
        self.position.tqqq_avg_cost = tqqq_price if tqqq_target_shares > 0 else 0
        self.position.sqqq_shares = sqqq_target_shares
        self.position.sqqq_avg_cost = sqqq_price if sqqq_target_shares > 0 else 0
        self.position.cash = cash_target
        
        logger.info(f"[DEMO] Rebalance complete: {tqqq_target_shares:.4f} TQQQ, {sqqq_target_shares:.4f} SQQQ")
    
    def _get_price(self, symbol: str) -> float:
        """Get current price for symbol."""
        try:
            import robin_stocks.robinhood as rh
            quote = rh.stocks.get_stock_quote_by_symbol(symbol)
            return float(quote.get('last_trade_price', 0))
        except Exception as e:
            logger.warning(f"Failed to get price for {symbol}: {e}")
            return 0.0
    
    def _is_market_hours(self) -> bool:
        """Check if currently in market hours."""
        now = datetime.now(self.et_tz)
        
        # Weekday check
        if now.weekday() >= 5:  # Saturday or Sunday
            return False
        
        # Time check (9:30 AM - 4:00 PM ET)
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        
        return market_open <= now <= market_close
    
    def stop(self):
        """Stop the bot."""
        self.running = False
        logger.info("Stop requested")
    
    def get_status(self) -> Dict:
        """Get current bot status."""
        tqqq_price = self._get_price(TQQQ_SYMBOL)
        sqqq_price = self._get_price(SQQQ_SYMBOL)
        total_value = self.position.get_total_value(tqqq_price, sqqq_price)
        
        return {
            "running": self.running,
            "current_slider": self.current_slider,
            "position": {
                "tqqq_shares": self.position.tqqq_shares,
                "sqqq_shares": self.position.sqqq_shares,
                "cash": self.position.cash,
            },
            "total_value": total_value,
            "pnl": total_value - self.demo_pool,
            "pnl_pct": ((total_value - self.demo_pool) / self.demo_pool) * 100,
        }
    
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


def run_demo(dry_run: bool = False):
    """Run the slider bot in demo mode."""
    import sys
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Login to Robinhood
    logger.info("Logging in to Robinhood...")
    robinhood.login_to_robinhood()
    
    bot = SliderBot()
    
    if dry_run:
        logger.info("DRY RUN: Running single cycle only")
        result = bot.run_cycle()
        print(json.dumps(result, indent=2))
    else:
        logger.info("Starting continuous demo mode...")
        try:
            bot.run()
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            bot.stop()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="TQQQ/SQQQ Slider Bot")
    parser.add_argument("--demo", action="store_true", help="Run in demo mode (no real trades)")
    parser.add_argument("--dry-run", action="store_true", help="Run single cycle and exit")
    args = parser.parse_args()
    
    run_demo(dry_run=args.dry_run)
