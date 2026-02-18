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
from .data_feed import QQQDataFeed, get_market_session
from .strategy_nodes import run_strategy_nodes
from .synthesizer import synthesize_final_slider, format_slider_for_display
from .kb_materializer import SliderKBWriter
from .benchmark import BenchmarkTracker

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
        self.benchmark_tracker = BenchmarkTracker(initial_capital=demo_pool)  # Benchmark tracking
        
        self.current_slider = 0.0
        self.running = False
        
        logger.info(f"SliderBot initialized: ${demo_pool:,.0f} demo pool, {interval_seconds}s interval")
    
    def run(self):
        """Main loop — run until stopped."""
        self.running = True
        logger.info("SliderBot starting...")
        
        while self.running:
            # Check if we're in a tradable session
            session = get_market_session()
            tradable, reason = self._is_tradable_hours(session)
            
            if not tradable:
                logger.info(f"Not tradable: {reason}. Waiting 60s...")
                time.sleep(60)
                continue
            
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"Cycle error: {e}")
            
            # Dynamic interval based on session
            interval = self._get_session_interval(session)
            logger.info(f"[{session['session_name'].upper()}] Sleeping {interval}s until next cycle...")
            time.sleep(interval)
        
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
        
        # 2. Run strategy nodes concurrently
        import config
        strategy_results = run_strategy_nodes(
            market_data=market_data_str,
            extra_context={
                "opening_range": opening_range_str,
                "gap_info": gap_info_str,
            },
            active_strategies=config.ACTIVE_STRATEGIES,
        )
        
        # 3. Update Benchmark Tracker
        # Fetch current prices
        qqq_price = market_data.get('current_price', 0)
        tqqq_price = self._get_price(TQQQ_SYMBOL)
        sqqq_price = self._get_price(SQQQ_SYMBOL)
        voo_price = self._get_price("VOO")
        
        self.benchmark_tracker.update({
            "TQQQ": tqqq_price,
            "QQQ": qqq_price,
            "VOO": voo_price
        })

        # 4. Synthesize final slider
        # Pass full market data (same as strategies receive) for DeepSeek to analyze
        synthesis = synthesize_final_slider(strategy_results, market_data_str)
        
        new_slider = synthesis.get("final_slider", 0.0)
        confidence = synthesis.get("confidence", 0.0)
        
        logger.info("\n" + format_slider_for_display(synthesis, total_strategies=len(strategy_results)))
        
        # 5. Check if rebalance needed
        slider_change = abs(new_slider - self.current_slider)
        
        if slider_change >= self.min_slider_change:
            logger.info(f"Slider change {slider_change:.2f} >= threshold, rebalancing...")
            self._rebalance(new_slider, tqqq_price, sqqq_price)
            self.current_slider = new_slider
        else:
            logger.info(f"Slider change {slider_change:.2f} < threshold, holding position")
        
        # 6. Calculate current PnL and Compare
        # Use valid prices fetched earlier
        total_value = self.position.get_total_value(tqqq_price, sqqq_price)
        pnl = total_value - self.demo_pool
        pnl_pct = (pnl / self.demo_pool) * 100
        
        # Log Performance Comparison
        logger.info("\n" + self.benchmark_tracker.format_comparison(total_value))
        
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
                bot_pnl_pct=pnl_pct,
                benchmark_data=self.benchmark_tracker.get_performance(),
                sqqq_price=sqqq_price,
            )
        except Exception as e:
            logger.warning(f"KB materialization failed: {e}")
        
        # 8. Write status file for UI
        self._write_status_file(
            cycle_start, new_slider, confidence, pnl, pnl_pct, 
            total_value, market_data, strategy_results, synthesis
        )

        return {
            "timestamp": cycle_start.isoformat(),
            "slider": new_slider,
            "confidence": confidence,
            "rebalanced": slider_change >= self.min_slider_change,
            "pnl": pnl,
            "total_value": total_value,
        }
    
    def _write_status_file(
        self, timestamp: datetime, slider: float, confidence: float,
        pnl: float, pnl_pct: float, total_value: float,
        market_data: Dict, strategy_results: Dict, synthesis: Dict
    ):
        """Write current status to JSON for UI consumption."""
        try:
            status_file = Path("kb/slider_status.json")
            status_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Format strategies list
            strategies = []
            for name, res in strategy_results.items():
                if not res.get("success"): continue
                strategies.append({
                    "name": name,
                    "slider": res.get("slider", 0),
                    "confidence": res.get("confidence", 0),
                    "reasoning": res.get("reasoning", "")[:100] + "..." if len(res.get("reasoning", "")) > 100 else res.get("reasoning", ""),
                    "direction": res.get("direction", "neutral")
                })
            
            # Get benchmarks
            benchmarks = self.benchmark_tracker.get_performance()
            
            data = {
                "timestamp": timestamp.isoformat(),
                "slider": slider,
                "confidence": confidence,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "portfolio": {
                    "tqqq_shares": self.position.tqqq_shares,
                    "sqqq_shares": self.position.sqqq_shares,
                    "cash": self.position.cash,
                    "total_value": total_value,
                },
                "market": {
                    "current_price": market_data.get("current_price", 0),
                    # Get session directly from data_feed if possible, or re-fetch
                    "session": get_market_session()["session_name"]
                },
                "strategies": strategies,
                "benchmarks": benchmarks,
                "action": self._infer_action(slider),
                "agreement": synthesis.get("strategy_agreement", 0),
            }
            
            with open(status_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to write status file: {e}")
    
    def _rebalance(self, target_slider: float, tqqq_price: float, sqqq_price: float):
        """
        Rebalance demo portfolio to match target slider.

        Args:
            target_slider: Target slider (-1 to +1)
            tqqq_price: Current TQQQ price
            sqqq_price: Current SQQQ price
        """
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
        """Get current price for symbol with 3-tier resolution.

        Price resolution order (outside regular hours):
        1. last_extended_hours_trade_price (available ~4:00-20:00 ET)
        2. bid/ask midpoint (available during 24hr market for eligible ETFs)
        3. last_trade_price (regular session close — final fallback)

        During regular hours (09:30-16:00), uses last_trade_price directly.
        """
        try:
            import robin_stocks.robinhood as rh
            quote = rh.stocks.get_stock_quote_by_symbol(symbol)

            # Check if we're in extended hours
            now = datetime.now(self.et_tz)
            market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
            market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
            is_extended_hours = now < market_open or now > market_close

            if is_extended_hours:
                # Tier 1: Extended hours trade price (works ~4:00-20:00 ET)
                extended_price = quote.get('last_extended_hours_trade_price')
                if extended_price:
                    price = float(extended_price)
                    if price > 0:
                        return price

                # Tier 2: Bid/ask midpoint (24hr market may keep bid/ask alive)
                bid = float(quote.get('bid_price', 0) or 0)
                ask = float(quote.get('ask_price', 0) or 0)
                if bid > 0 and ask > 0:
                    midpoint = (bid + ask) / 2
                    logger.debug(f"{symbol} using bid/ask midpoint ${midpoint:.2f} (bid=${bid:.2f}, ask=${ask:.2f})")
                    return midpoint

            # Tier 3 / Regular hours: last trade price
            return float(quote.get('last_trade_price', 0))
        except Exception as e:
            logger.warning(f"Failed to get price for {symbol}: {e}")
            return 0.0
    
    def _is_tradable_hours(self, session: Dict = None) -> Tuple[bool, str]:
        """
        Check if currently in tradable hours (including extended hours).
        
        Returns:
            Tuple of (is_tradable, reason)
        """
        now = datetime.now(self.et_tz)
        
        # Weekend check
        if now.weekday() >= 5:  # Saturday or Sunday
            return False, "Weekend - markets closed"
        
        # All sessions are tradable (overnight, pre_market, market_open, etc.)
        # Only 'closed' session is not tradable, but we've eliminated that
        if session is None:
            session = get_market_session()
        
        session_name = session.get('session_name', 'unknown')
        
        # All known sessions are tradable
        if session_name in ['overnight', 'pre_market', 'market_open', 'lunch', 'power_hour', 'after_market']:
            return True, f"In {session_name} session"
        
        return False, f"Unknown session: {session_name}"
    
    def _get_session_interval(self, session: Dict) -> int:
        """
        Get appropriate poll interval based on current session.
        
        - Overnight: 15 min (900s) - slow mode
        - Pre/After Market: 10 min (600s)
        - Regular hours: 5 min (300s) or configured interval
        """
        session_name = session.get('session_name', 'market_open')
        
        if session_name == 'overnight':
            return 900  # 15 minutes
        elif session_name in ['pre_market', 'after_market']:
            return 600  # 10 minutes
        else:
            return self.interval_seconds  # Default (5 min)
    
    def stop(self):
        """Stop the bot."""
        self.running = False
        logger.info("Stop requested")

    def reset(self, new_capital: float = None):
        """
        Reset the bot to initial state.

        - Resets demo position to cash only
        - Resets benchmark tracker and immediately initializes with current prices
        - Clears slider history
        - Updates status file

        Args:
            new_capital: Optional new starting capital (default: DEMO_POOL_SIZE)
        """
        capital = new_capital if new_capital is not None else DEMO_POOL_SIZE

        # Reset position
        self.position = DemoPosition(cash=capital)
        self.demo_pool = capital
        self.current_slider = 0.0

        # Reset benchmark tracker
        self.benchmark_tracker.reset(capital)

        # Immediately initialize benchmarks with current prices so returns start at 0%
        qqq_price = 0
        try:
            tqqq_price = self._get_price(TQQQ_SYMBOL)
            qqq_price = self._get_price("QQQ")
            voo_price = self._get_price("VOO")

            if tqqq_price > 0 and qqq_price > 0 and voo_price > 0:
                self.benchmark_tracker.initialize({
                    "TQQQ": tqqq_price,
                    "QQQ": qqq_price,
                    "VOO": voo_price,
                })
                # Update with same prices so current_price matches start_price
                self.benchmark_tracker.update({
                    "TQQQ": tqqq_price,
                    "QQQ": qqq_price,
                    "VOO": voo_price,
                })
                logger.info(f"Benchmarks initialized at TQQQ=${tqqq_price:.2f}, QQQ=${qqq_price:.2f}, VOO=${voo_price:.2f}")
        except Exception as e:
            logger.warning(f"Failed to initialize benchmarks on reset: {e}")

        # Clear history
        self.history = SliderHistory()
        if self.history_path.exists():
            try:
                self.history_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete history file: {e}")

        # Write fresh status file
        now = datetime.now(self.et_tz)
        self._write_status_file(
            timestamp=now,
            slider=0.0,
            confidence=0.0,
            pnl=0.0,
            pnl_pct=0.0,
            total_value=capital,
            market_data={'current_price': qqq_price},
            strategy_results={},
            synthesis={'strategy_agreement': 0}
        )

        logger.info(f"SliderBot reset complete. Capital: ${capital:,.2f}")
    
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


def run_demo(dry_run: bool = False, with_ui: bool = True):
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

    # Start web UI and register bot for API access
    if with_ui:
        try:
            from src.web import start_server_thread, set_trading_state
            set_trading_state(
                mode="slider",
                running=True,
                slider_bot=bot,  # Register for reset API
            )
            start_server_thread(host='0.0.0.0', port=5000)
            logger.info("Web dashboard available at http://localhost:5000")
        except ImportError:
            logger.warning("Web UI not available")

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
    parser.add_argument("--no-ui", action="store_true", help="Disable web UI")
    args = parser.parse_args()

    run_demo(dry_run=args.dry_run, with_ui=not args.no_ui)
