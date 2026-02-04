import robin_stocks.robinhood as rh
import robin_stocks.urls as rh_urls
import time
from datetime import datetime
from pytz import timezone
import pandas as pd

from . import onepassword
from ..utils import auth
from ..utils import logger
from config import MODE, ROBINHOOD_USERNAME, ROBINHOOD_PASSWORD
from config import OP_SERVICE_ACCOUNT_NAME, OP_SERVICE_ACCOUNT_TOKEN, OP_VAULT_NAME, OP_ITEM_NAME

account_info_cache = {}

# Main login function that orchestrates the login process
async def login_to_robinhood():
    try:
        # Try to get MFA code from secret first
        mfa_code = auth.get_mfa_code_from_secret()

        # If no MFA secret, try 1Password
        if not mfa_code and OP_SERVICE_ACCOUNT_NAME and OP_SERVICE_ACCOUNT_TOKEN and OP_VAULT_NAME and OP_ITEM_NAME:
            mfa_code = await onepassword.get_mfa_code_from_1password()

        if mfa_code:
            logger.debug("Attempting to login to Robinhood with MFA...")
            login_resp = rh.login(ROBINHOOD_USERNAME, ROBINHOOD_PASSWORD, mfa_code=mfa_code)
        else:
            logger.debug("Attempting to login to Robinhood without MFA...")
            login_resp = rh.login(ROBINHOOD_USERNAME, ROBINHOOD_PASSWORD)

        logger.debug(f"Robinhood login response: {login_resp}")

        if not login_resp:
            logger.error("Login failed - no response received")
            return None
        if 'access_token' in login_resp and 'expires_in' in login_resp:
            # Login successful - detail field may be present with info message
            if 'detail' in login_resp:
                logger.debug(f"Login info: {login_resp['detail']}")
            logger.debug("Robinhood login successful.")
            return login_resp
        if 'detail' in login_resp:
            logger.error(f"Login failed - {login_resp['detail']}")
            return None
        logger.error(f"Login failed - unexpected response: {login_resp}")
        return None

    except Exception as e:
        logger.error(f"An error occurred during Robinhood login: {e}")
        return None


# Run a Robinhood function with retries and delay between attempts (to handle rate limits)
def rh_run_with_retries(func, *args, max_retries=3, delay=60, **kwargs):
    for attempt in range(max_retries):
        result = func(*args, **kwargs)
        if result is not None:
            return result
        if attempt < max_retries - 1:  # Don't log on last attempt
            logger.warning(f"Robinhood API call {func.__name__} returned None, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(delay)
    return None


# Check if the market is open
def is_market_open():
    eastern = timezone('US/Eastern')
    now = datetime.now(eastern)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


# Round money
def round_money(price, decimals=2):
    if price is None:
        return None
    return round(float(price), decimals)


# Round quantity
def round_quantity(quantity, decimals=6):
    if quantity is None:
        return None
    return round(float(quantity), decimals)


# Extract data from my stocks
def extract_my_stocks_data(stock_data):
    return {
        "current_price": round_money(stock_data['price']),
        "my_quantity": round_quantity(stock_data['quantity']),
        "my_average_buy_price": round_money(stock_data['average_buy_price']),
    }


# Extract data from watchlist stocks
def extract_watchlist_data(stock_data):
    return {
        "current_price": round_money(stock_data['price']),
        "my_quantity": round_quantity(0),
        "my_average_buy_price": round_money(0),
    }


# Extract sell response data
def extract_sell_response_data(sell_resp):
    return {
        "quantity": round_quantity(sell_resp['quantity']),
        "price": round_money(sell_resp['price']),
    }


# Extract buy response data
def extract_buy_response_data(buy_resp):
    return {
        "quantity": round_quantity(buy_resp['quantity']),
        "price": round_money(buy_resp['price']),
    }


# Enrich stock data with Relative strength index (RSI)
def enrich_with_rsi(stock_data, historical_data, symbol):
    if len(historical_data) < 14:
        logger.debug(f"Not enough data to calculate RSI for {symbol}")
        return stock_data

    prices = [round_money(day['close_price']) for day in historical_data]
    delta = pd.Series(prices).diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean().iloc[-1]
    avg_loss = loss.rolling(window=14).mean().iloc[-1]
    if avg_loss == 0:
        rs = 100
    else:
        rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    stock_data["rsi"] = round(float(rsi), 2)
    return stock_data


# Enrich stock data with Volume-weighted average price (VWAP)
def enrich_with_vwap(stock_data, historical_data, symbol):
    if len(historical_data) < 1:
        logger.debug(f"Not enough data to calculate VWAP for {symbol}")
        return stock_data

    stock_history_df = pd.DataFrame(historical_data)
    stock_history_df["close_price"] = pd.to_numeric(stock_history_df["close_price"], errors="coerce")
    stock_history_df["high_price"] = pd.to_numeric(stock_history_df["high_price"], errors="coerce")
    stock_history_df["low_price"] = pd.to_numeric(stock_history_df["low_price"], errors="coerce")
    stock_history_df["volume"] = pd.to_numeric(stock_history_df["volume"], errors="coerce")

    # Drop rows where volume is zero or NaN
    stock_history_df = stock_history_df[stock_history_df["volume"] > 0]

    # Compute the Typical Price
    stock_history_df["typical_price"] = (stock_history_df["high_price"] + stock_history_df["low_price"] + stock_history_df["close_price"]) / 3

    # Compute VWAP
    sum_of_volumes = stock_history_df["volume"].sum()
    dot_product = stock_history_df["volume"].dot(stock_history_df["typical_price"])

    if sum_of_volumes == 0:  # Prevent division by zero
        logger.debug(f"Total volume is zero for {symbol}, cannot compute VWAP")
        return stock_data

    vwap = dot_product / sum_of_volumes
    stock_data["vwap"] = round_money(vwap)

    return stock_data


# Enrich stock data with Relative Volume (RVOL)
def enrich_with_relative_volume(stock_data, historical_data_day, historical_data_year, symbol):
    """
    Calculate relative volume: today's volume vs average daily volume, adjusted for time of day.
    1.0 = average, >1.0 = above average activity, <1.0 = below average.
    """
    if not historical_data_day or not historical_data_year:
        return stock_data

    try:
        # Today's cumulative volume from 5-min bars
        today_volume = sum(int(bar.get('volume', 0)) for bar in historical_data_day)
        if today_volume == 0:
            return stock_data

        # Average daily volume from yearly data
        yearly_volumes = [int(day.get('volume', 0)) for day in historical_data_year if int(day.get('volume', 0)) > 0]
        if not yearly_volumes:
            return stock_data
        avg_daily_volume = sum(yearly_volumes) / len(yearly_volumes)
        if avg_daily_volume == 0:
            return stock_data

        # Adjust for time of day: trading day has ~78 five-min bars (6.5 hrs)
        bars_elapsed = len(historical_data_day)
        total_bars = 78
        if bars_elapsed < total_bars:
            # Extrapolate today's volume to a full day
            projected_volume = today_volume * (total_bars / bars_elapsed)
        else:
            projected_volume = today_volume

        relative_volume = round(projected_volume / avg_daily_volume, 2)
        stock_data["relative_volume"] = relative_volume
    except Exception:
        pass

    return stock_data


# Enrich stock data with Moving average (MA)
def enrich_with_moving_averages(stock_data, historical_data, symbol):
    if len(historical_data) < 200:
        logger.debug(f"Not enough data to calculate moving averages for {symbol}")
        return stock_data

    prices = [round_money(day['close_price']) for day in historical_data]
    moving_avg_50 = pd.Series(prices).rolling(window=50).mean().iloc[-1]
    moving_avg_200 = pd.Series(prices).rolling(window=200).mean().iloc[-1]
    stock_data["50_day_mavg_price"] = round_money(moving_avg_50)
    stock_data["200_day_mavg_price"] = round_money(moving_avg_200)
    return stock_data


# Enrich stock data with Analyst ratings
def enrich_with_analyst_ratings(stock_data, ratings_data):
    stock_data["analyst_summary"] = ratings_data['summary']
    stock_data["analyst_ratings"] = list(map(lambda rating: {
        "published_at": rating['published_at'],
        "type": rating['type'],
        "text": rating['text'].decode('utf-8'),
    }, ratings_data['ratings']))
    return stock_data


# Build compressed intraday price+volume summary for LLM prompt
def build_intraday_summary(historical_data_day, symbol):
    """
    Aggregate 5-min bars into ~30-min buckets and return a compact markdown table.

    Returns a string like:
    | Time | O | H | L | C | Vol% |
    |------|---|---|---|---|------|
    | 09:30 | 150.2 | 151.0 | 149.8 | 150.5 | 142% |
    ...

    Vol% = this period's volume relative to average period volume.
    Returns empty string if insufficient data.
    """
    if not historical_data_day or len(historical_data_day) < 6:
        return ""

    try:
        bars_per_bucket = 6  # 6 x 5min = 30min
        buckets = []

        for i in range(0, len(historical_data_day), bars_per_bucket):
            chunk = historical_data_day[i:i + bars_per_bucket]
            if not chunk:
                break

            open_price = float(chunk[0].get('open_price', 0))
            high_price = max(float(b.get('high_price', 0)) for b in chunk)
            low_price = min(float(b.get('low_price', 0)) for b in chunk if float(b.get('low_price', 0)) > 0)
            close_price = float(chunk[-1].get('close_price', 0))
            volume = sum(int(b.get('volume', 0)) for b in chunk)

            # Extract time from begins_at
            begins_at = chunk[0].get('begins_at', '')
            if 'T' in begins_at:
                time_part = begins_at.split('T')[1][:5]  # HH:MM
            else:
                time_part = f"{i // bars_per_bucket * 30 // 60 + 9}:{i // bars_per_bucket * 30 % 60:02d}"

            buckets.append({
                'time': time_part,
                'o': open_price,
                'h': high_price,
                'l': low_price,
                'c': close_price,
                'vol': volume
            })

        if not buckets:
            return ""

        # Calculate average bucket volume for Vol%
        volumes = [b['vol'] for b in buckets]
        avg_vol = sum(volumes) / len(volumes) if volumes else 1

        # Build markdown table
        lines = [f"**{symbol} Intraday (30-min):**", "| Time | O | H | L | C | Vol% |", "|------|---|---|---|---|------|"]
        for b in buckets:
            vol_pct = int(b['vol'] / avg_vol * 100) if avg_vol > 0 else 0
            lines.append(f"| {b['time']} | {b['o']:.2f} | {b['h']:.2f} | {b['l']:.2f} | {b['c']:.2f} | {vol_pct}% |")

        return "\n".join(lines)
    except Exception:
        return ""


# Get PDT restrictions for a stock by symbol
def get_stock_day_trade_checks(symbol):
    stock_id = rh_run_with_retries(rh.helper.id_for_stock, symbol)
    url = account_info_cache["url"] + 'day_trade_checks'
    params = {
        "instrument": rh_urls.instruments() + stock_id + "/"
    }
    resp = rh_run_with_retries(rh.request_get, url, payload=params)
    return resp


# Enrich stock data with PDT restrictions
def enrich_with_pdt_restrictions(stock_data, symbol):
    day_trade_checks = get_stock_day_trade_checks(symbol)
    if day_trade_checks is None:
        return stock_data

    stock_data["is_buy_pdt_restricted"] = day_trade_checks['buy'] is not None or day_trade_checks['buy_extended'] is not None
    stock_data["is_sell_pdt_restricted"] = day_trade_checks['sell'] is not None or day_trade_checks['sell_extended'] is not None
    return stock_data


# Get my buying power and account info
def get_account_info():
    resp = rh_run_with_retries(rh.profiles.load_account_profile)
    if resp is None:
        raise Exception("Error getting profile data: No response")

    resp["buying_power"] = round_money(resp["buying_power"])
    account_info_cache["url"] = resp["url"]
    return resp

# Get portfolio stocks
def get_portfolio_stocks():
    resp = rh_run_with_retries(rh.build_holdings)
    if resp is None:
        raise Exception("Error getting portfolio stocks: No response")
    return resp


# Get watchlist stocks by name
def get_watchlist_stocks(name):
    resp = rh_run_with_retries(rh.get_watchlist_by_name, name)
    if resp is None or 'results' not in resp:
        raise Exception(f"Error getting watchlist {name}: No response")
    return resp['results']


# Get analyst ratings for a stock by symbol
def get_ratings(symbol):
    resp = rh_run_with_retries(rh.stocks.get_ratings, symbol)
    if resp is None:
        raise Exception(f"Error getting ratings for {symbol}: No response")
    return resp


# Get current quote for a stock by symbol
def get_quote(symbol):
    resp = rh_run_with_retries(rh.stocks.get_stock_quote_by_symbol, symbol)
    if resp is None:
        raise Exception(f"Error getting quote for {symbol}: No response")
    return resp


# Get historical stock data by symbol
def get_historical_data(symbol, interval="day", span="year"):
    resp = rh_run_with_retries(rh.stocks.get_stock_historicals, symbol, interval=interval, span=span)
    if resp is None:
        raise Exception(f"Error getting historical data for {symbol}: No response")
    return resp


# Sell a stock by symbol and quantity
def sell_stock(symbol, quantity):
    if MODE == "demo":
        return {"id": "demo"}

    if MODE == "manual":
        confirm = input(f"Confirm sell for {symbol} of {quantity}? (yes/no): ")
        if confirm.lower() != "yes":
            return {"id": "cancelled"}

    sell_resp = rh_run_with_retries(rh.orders.order_sell_market, symbol, quantity, timeInForce="gfd")
    if sell_resp is None:
        raise Exception(f"Error selling {symbol}: No response")
    return sell_resp


# Buy a stock by symbol and quantity
def buy_stock(symbol, quantity):
    if MODE == "demo":
        return {"id": "demo"}

    if MODE == "manual":
        confirm = input(f"Confirm buy for {symbol} of {quantity}? (yes/no): ")
        if confirm.lower() != "yes":
            return {"id": "cancelled"}

    buy_resp = rh_run_with_retries(rh.orders.order_buy_market, symbol, quantity, timeInForce="gfd")
    if buy_resp is None:
        raise Exception(f"Error buying {symbol}: No response")
    return buy_resp


