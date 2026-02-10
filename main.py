"""
Robinhood AI Trading Bot - Main Entry Point

Architecture:
1. Day Trading Mode: Runs intraday cycles, reads KB for context, buffers decisions
2. EOD Review Mode: Runs at market close, analyzes decisions, writes to KB
3. Web Dashboard: Optional UI for live monitoring and manual EOD trigger

This separation prevents repetitive KB entries and ensures consolidated learnings.
"""

import sys
import time
from datetime import datetime
import asyncio

from config import *
from src.api import robinhood
from src.utils import logger
from src.kb import KBReader, KBWriter, cleanup_kb, log_kb_state
from src.day_trading import DayTradingBot, DecisionBuffer
from src.eod_review import EODReviewer, run_eod_review

# Web UI imports (optional, only if --with-ui flag is used)
WEB_UI_ENABLED = "--with-ui" in sys.argv or "--ui-only" in sys.argv
if WEB_UI_ENABLED:
    from src.web import start_server_thread, set_trading_state, get_event_bus
    from src.web.event_bus import publish_cycle_complete, publish_eod_review


# ============================================================================
# INITIALIZATION
# ============================================================================

# Initialize KB components
kb_reader = KBReader(kb_root="kb")
kb_writer = KBWriter(kb_root="kb")

# Initialize Decision Buffer (replaces per-cycle KB writes)
decision_buffer = DecisionBuffer(buffer_file="kb/decision_buffer.json")

# Initialize Day Trading Bot
day_trader = DayTradingBot(
    kb_reader=kb_reader,
    decision_buffer=decision_buffer,
    portfolio_limit=PORTFOLIO_LIMIT,
    min_buy=MIN_BUYING_AMOUNT_USD if MIN_BUYING_AMOUNT_USD else 1.0,
    max_buy=MAX_BUYING_AMOUNT_USD if MAX_BUYING_AMOUNT_USD else 10000.0,
    min_sell=MIN_SELLING_AMOUNT_USD if MIN_SELLING_AMOUNT_USD else 1.0,
    max_sell=MAX_SELLING_AMOUNT_USD if MAX_SELLING_AMOUNT_USD else 10000.0,
    trade_exceptions=TRADE_EXCEPTIONS,
    watchlist_names=WATCHLIST_NAMES,
    watchlist_limit=WATCHLIST_OVERVIEW_LIMIT,
    run_interval=RUN_INTERVAL_SECONDS,
)

# Initialize EOD Reviewer
eod_reviewer = EODReviewer(
    kb_writer=kb_writer,
    kb_reader=kb_reader,
    decision_buffer=decision_buffer,
    min_buy=MIN_BUYING_AMOUNT_USD if MIN_BUYING_AMOUNT_USD else 1.0,
    max_buy=MAX_BUYING_AMOUNT_USD if MAX_BUYING_AMOUNT_USD else 10000.0,
    min_sell=MIN_SELLING_AMOUNT_USD if MIN_SELLING_AMOUNT_USD else 1.0,
    max_sell=MAX_SELLING_AMOUNT_USD if MAX_SELLING_AMOUNT_USD else 10000.0,
)


# ============================================================================
# MARKET HOURS HELPERS
# ============================================================================

def is_market_open() -> bool:
    """Check if US stock market is currently open."""
    now = datetime.now()
    
    # Weekend check (0=Monday, 6=Sunday)
    if now.weekday() >= 5:
        return False
    
    # Market hours: 9:30 AM - 4:00 PM EST
    # Note: This is simplified; real implementation should handle timezones
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return market_open <= now <= market_close


def is_market_close() -> bool:
    """Check if we're at market close (4:00 PM)."""
    now = datetime.now()
    
    # Check if current time is between 4:00 PM and 4:05 PM
    close_start = now.replace(hour=16, minute=0, second=0, microsecond=0)
    close_end = now.replace(hour=16, minute=5, second=0, microsecond=0)
    
    return close_start <= now <= close_end


def should_run_eod() -> bool:
    """Determine if EOD review should run."""
    # Check for CLI flag
    if "--run-eod" in sys.argv:
        return True
    
    # Check if market just closed and we have buffered decisions
    if is_market_close() and decision_buffer.get_decision_count() > 0:
        return True
    
    return False


# ============================================================================
# TRADING BOT FUNCTIONS
# ============================================================================

def run_trading_cycle():
    """
    Run a single trading cycle using DayTradingBot.
    
    This method:
    - Gets market data and makes AI decisions
    - Executes trades
    - Buffers decisions for EOD review
    - Does NOT write to KB (that happens in EOD review)
    """
    logger.info(f"Running trading cycle in {MODE} mode...")
    
    trading_results = day_trader.run_cycle()
    
    # Log results
    sold = [f"{r['symbol']} ({r['quantity']})" 
            for r in trading_results.values() 
            if r.get('decision') == "sell" and r.get('result') == "success"]
    bought = [f"{r['symbol']} ({r['quantity']})" 
              for r in trading_results.values() 
              if r.get('decision') == "buy" and r.get('result') == "success"]
    errors = [f"{r['symbol']} ({r.get('details', 'unknown')})" 
              for r in trading_results.values() 
              if r.get('result') == "error"]
    
    logger.info(f"Sold: {', '.join(sold) if sold else 'None'}")
    logger.info(f"Bought: {', '.join(bought) if bought else 'None'}")
    if errors:
        logger.info(f"Errors: {', '.join(errors)}")
    
    # Report buffer status
    buffered = decision_buffer.get_decision_count()
    logger.info(f"Decisions buffered for EOD review: {buffered}")
    
    # Publish to web UI if enabled
    if WEB_UI_ENABLED:
        publish_cycle_complete(
            decisions=len(trading_results),
            sold=sold,
            bought=bought,
            errors=errors
        )
        get_event_bus().update_status(buffered_decisions=buffered)
    
    return trading_results


def run_eod_review_cycle():
    """
    Run end-of-day review.
    
    This method:
    - Collects all buffered decisions
    - Performs 4-quadrant analysis
    - Writes consolidated, deduplicated lessons to KB
    - Clears the decision buffer
    """
    logger.info("=" * 60)
    logger.info("RUNNING END-OF-DAY REVIEW")
    logger.info("=" * 60)
    
    results = eod_reviewer.run()
    
    logger.info(f"EOD Review Complete:")
    logger.info(f"  - Decisions analyzed: {results.get('successful', 0)}")
    logger.info(f"  - Lessons generated: {results.get('lessons_generated', 0)}")
    logger.info(f"  - Lessons written (new): {results.get('lessons_written', 0)}")
    logger.info(f"  - Lessons merged: {results.get('lessons_merged', 0)}")
    logger.info(f"  - Duplicates dropped: {results.get('duplicates_removed', 0)}")
    
    # Publish to web UI if enabled
    if WEB_UI_ENABLED:
        publish_eod_review(results)
    
    return results


# ============================================================================
# BACKTEST MODE
# ============================================================================

async def run_backtest_mode():
    """Run the backtest using historical data."""
    from src.backtest.engine import run_backtest

    logger.info("Starting backtest mode...")

    should_clean = KB_CLEAN_ON_START or "--kb-clean" in sys.argv
    results = await run_backtest(
        symbols=BACKTEST_SYMBOLS,
        start_date=BACKTEST_START_DATE,
        end_date=BACKTEST_END_DATE,
        starting_cash=BACKTEST_STARTING_CASH,
        transaction_fee=BACKTEST_TRANSACTION_FEE,
        portfolio_limit=PORTFOLIO_LIMIT,
        min_buy_amount=MIN_BUYING_AMOUNT_USD if MIN_BUYING_AMOUNT_USD else 0,
        max_buy_amount=MAX_BUYING_AMOUNT_USD if MAX_BUYING_AMOUNT_USD else float('inf'),
        min_sell_amount=MIN_SELLING_AMOUNT_USD if MIN_SELLING_AMOUNT_USD else 0,
        max_sell_amount=MAX_SELLING_AMOUNT_USD if MAX_SELLING_AMOUNT_USD else float('inf'),
        clean_kb=should_clean,
    )

    if "error" in results:
        logger.error(f"Backtest failed: {results['error']}")
    else:
        logger.info("Backtest completed successfully!")

    return results


# ============================================================================
# MAIN LOOP
# ============================================================================

async def main():
    """Main trading loop."""
    
    # Handle backtest mode separately
    if MODE == "backtest":
        await run_backtest_mode()
        return

    # Handle UI-only mode
    if "--ui-only" in sys.argv:
        logger.info("Starting in UI-Only mode (Slider Bot Dashboard)...")
        if WEB_UI_ENABLED:
            set_trading_state(mode="ui-only", running=True)
            start_server_thread(host='0.0.0.0', port=5000)
            logger.info("Web dashboard available at http://localhost:5000")
            logger.info("Server running. Press Ctrl+C to stop.")
            while True:
                await asyncio.sleep(1)
        return
    
    # Handle standalone EOD review
    if "--run-eod" in sys.argv:
        logger.info("Running standalone EOD review...")
        await robinhood.login_to_robinhood()
        run_eod_review_cycle()
        return
    
    # For demo/live: handle KB cleanup if requested
    should_clean_kb = KB_CLEAN_ON_START or "--kb-clean" in sys.argv
    if should_clean_kb:
        logger.info("Cleaning KB before start...")
        cleanup_kb(kb_root="kb", preserve_strategies=True)
    
    # Log KB state
    log_kb_state(kb_root="kb")
    
    # Start web UI if enabled
    if WEB_UI_ENABLED:
        logger.info("Starting web dashboard...")
        set_trading_state(
            mode=MODE,
            running=True,
            decision_buffer=decision_buffer,
            kb_reader=kb_reader,
            eod_reviewer=eod_reviewer,
        )
        start_server_thread(host='0.0.0.0', port=5000)
        logger.info("Web dashboard available at http://localhost:5000")
    
    robinhood_token_expiry = 0
    eod_ran_today = False
    last_date = None
    
    while True:
        try:
            current_date = datetime.now().strftime('%Y-%m-%d')
            
            # Reset EOD flag on new day
            if current_date != last_date:
                eod_ran_today = False
                last_date = current_date
                
                # Start new day in decision buffer
                account = robinhood.get_account_info() if robinhood_token_expiry > time.time() else None
                if account:
                    portfolio_value = float(account.get('buying_power', 0))
                    portfolio = robinhood.get_portfolio_stocks()
                    for s in portfolio.values():
                        portfolio_value += float(s.get('price', 0)) * float(s.get('quantity', 0))
                    decision_buffer.start_new_day(portfolio_value)
            
            # Check if Robinhood token needs refresh
            if time.time() >= robinhood_token_expiry - 300:
                logger.info("Login to Robinhood...")
                login_resp = await robinhood.login_to_robinhood()
                if not login_resp or 'expires_in' not in login_resp:
                    raise Exception("Failed to login to Robinhood")

                # robin_stocks returns expires_in=86400 even for pickle-cached
                # tokens, regardless of actual remaining validity. Use a
                # conservative expiry for pickle logins to catch stale tokens.
                detail = login_resp.get('detail', '')
                if 'logged in using authentication' in str(detail):
                    # Pickle-cached login — re-validate after 2 cycles
                    effective_expiry = RUN_INTERVAL_SECONDS * 2
                    logger.info(f"Logged in (cached). Will re-validate in {effective_expiry}s")
                else:
                    effective_expiry = login_resp['expires_in']
                    logger.info(f"Logged in (fresh). Token expires in {effective_expiry}s")

                robinhood_token_expiry = time.time() + effective_expiry
            
            run_interval = RUN_INTERVAL_SECONDS
            
            # Check if we should run EOD review
            if should_run_eod() and not eod_ran_today:
                run_eod_review_cycle()
                eod_ran_today = True
            else:
                # Run normal trading cycle
                run_trading_cycle()
            
        except Exception as e:
            run_interval = 60
            # Force re-login on next iteration — token may be expired/invalid
            robinhood_token_expiry = 0
            logger.error(f"Trading bot error: {e}")
        
        logger.info(f"Waiting for {run_interval} seconds...")
        time.sleep(run_interval)


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    # Handle --kb-clean-only: just clean KB and exit
    if "--kb-clean-only" in sys.argv:
        logger.info("Cleaning KB (standalone)...")
        log_kb_state(kb_root="kb")
        result = cleanup_kb(kb_root="kb", preserve_strategies=True)
        logger.info(f"Done. Removed {result['files']} files, {result['dirs']} directories.")
        sys.exit(0)
    
    # Handle --run-eod: run EOD review and exit
    if "--run-eod" in sys.argv:
        logger.info("Running standalone EOD review...")
        asyncio.run(main())
        sys.exit(0)
    
    # Handle --ui-only: just run main (which handles it) without confirm
    if "--ui-only" in sys.argv:
        asyncio.run(main())
        sys.exit(0)

    # Normal operation - confirm with user
    print(f"\n{'='*60}")
    print(f"Robinhood AI Trading Bot - {MODE.upper()} Mode")
    print(f"{'='*60}")
    print(f"\nArchitecture:")
    print(f"  - Day Trading: Runs every {RUN_INTERVAL_SECONDS}s, buffers decisions")
    print(f"  - EOD Review: Runs at market close, writes to KB")
    print(f"\nOptions:")
    print(f"  --with-ui : Start web dashboard at http://localhost:5000")
    print(f"  --ui-only : Start web dashboard ONLY (no trading loop)")
    print(f"  --run-eod : Run end-of-day review manually")
    print(f"  --kb-clean : Clean KB before starting")
    print(f"  --kb-clean-only : Clean KB and exit")
    print(f"\n")
    
    confirm = input(f"Are you sure you want to run the bot in {MODE} mode? (yes/no): ")
    if confirm.lower() != "yes":
        logger.warning("Exiting the bot...")
        exit()
    
    asyncio.run(main())
