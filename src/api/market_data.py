"""
Market Data Module — Centralized stock price, history, and technical indicator computation.

Provides a clean abstraction over robin_stocks for OHLCV data,
with all technical indicators computed client-side.

Used by the Slider Bot's data_feed.py — replacing direct robin_stocks usage.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from pytz import timezone

import robin_stocks.robinhood as rh

logger = logging.getLogger(__name__)

ET_TZ = timezone('US/Eastern')


# =============================================================================
# PRICE FETCHING
# =============================================================================

def get_current_quote(symbol: str) -> Dict:
    """
    Get current stock quote with 3-tier price resolution.

    Returns:
        Dict with: price, bid, ask, prev_close, extended_price, open_price,
                    high_price, low_price, volume

    Price tiers (outside regular hours):
        1. last_extended_hours_trade_price
        2. bid/ask midpoint
        3. last_trade_price (fallback)
    """
    try:
        quote = rh.stocks.get_stock_quote_by_symbol(symbol)
        if not quote:
            logger.warning(f"No quote returned for {symbol}")
            return _empty_quote(symbol)

        bid = float(quote.get('bid_price', 0) or 0)
        ask = float(quote.get('ask_price', 0) or 0)

        # Determine if we're in extended hours
        now = datetime.now(ET_TZ)
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        is_extended = now < market_open or now > market_close

        # 3-tier price resolution
        if is_extended:
            extended_price = quote.get('last_extended_hours_trade_price')
            if extended_price:
                current_price = float(extended_price)
            elif bid > 0 and ask > 0:
                current_price = (bid + ask) / 2
                logger.debug(f"{symbol} using bid/ask midpoint ${current_price:.2f}")
            else:
                current_price = float(quote.get('last_trade_price', 0))
        else:
            current_price = float(quote.get('last_trade_price', 0))

        return {
            "symbol": symbol,
            "price": current_price,
            "bid": bid,
            "ask": ask,
            "spread": ask - bid if bid > 0 and ask > 0 else 0,
            "spread_pct": ((ask - bid) / ((ask + bid) / 2) * 100) if bid > 0 and ask > 0 else 0,
            "prev_close": float(quote.get('previous_close', 0) or 0),
            "extended_price": float(quote.get('last_extended_hours_trade_price', 0) or 0),
            "is_extended_hours": is_extended,
        }
    except Exception as e:
        logger.error(f"Failed to get quote for {symbol}: {e}")
        return _empty_quote(symbol)


def _empty_quote(symbol: str) -> Dict:
    """Return empty quote structure on error."""
    return {
        "symbol": symbol,
        "price": 0,
        "bid": 0,
        "ask": 0,
        "spread": 0,
        "spread_pct": 0,
        "prev_close": 0,
        "extended_price": 0,
        "is_extended_hours": False,
    }


# =============================================================================
# HISTORY FETCHING
# =============================================================================

def get_intraday_bars(symbol: str, extended: bool = True) -> List[Dict]:
    """
    Fetch intraday 5-minute bars.

    Args:
        symbol: Stock ticker
        extended: If True, include pre/post-market hours (bounds='extended')

    Returns:
        List of dicts with: time, open, high, low, close, volume
    """
    bounds = 'extended' if extended else 'regular'
    try:
        raw = rh.stocks.get_stock_historicals(
            symbol, interval='5minute', span='day', bounds=bounds
        )
        if not raw:
            logger.warning(f"No intraday data returned for {symbol}")
            return []

        return _parse_bars(raw)
    except Exception as e:
        logger.error(f"Failed to fetch intraday bars for {symbol}: {e}")
        return []


def get_daily_bars(symbol: str, span: str = 'week') -> List[Dict]:
    """
    Fetch daily bars for gap calculation and previous day data.

    Args:
        symbol: Stock ticker
        span: 'week', 'month', etc.

    Returns:
        List of dicts with: time, open, high, low, close, volume
    """
    try:
        raw = rh.stocks.get_stock_historicals(
            symbol, interval='day', span=span, bounds='regular'
        )
        if not raw:
            logger.warning(f"No daily data returned for {symbol}")
            return []

        return _parse_bars(raw)
    except Exception as e:
        logger.error(f"Failed to fetch daily bars for {symbol}: {e}")
        return []


def _parse_bars(raw_bars: List) -> List[Dict]:
    """Parse robin_stocks bar data into clean dicts."""
    parsed = []
    for bar in raw_bars:
        try:
            ts = datetime.fromisoformat(bar['begins_at'].replace('Z', '+00:00'))
            ts_et = ts.astimezone(ET_TZ)
            parsed.append({
                'time': ts_et,
                'open': float(bar['open_price']),
                'high': float(bar['high_price']),
                'low': float(bar['low_price']),
                'close': float(bar['close_price']),
                'volume': int(bar['volume']),
            })
        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"Skipping malformed bar: {e}")
            continue
    return parsed


# =============================================================================
# TECHNICAL INDICATORS — All computed client-side from OHLCV
# =============================================================================

def calculate_all_indicators(bars: List[Dict], quote: Dict = None) -> Dict:
    """
    Calculate all technical indicators from bar data.

    Args:
        bars: List of OHLCV bar dicts (from get_intraday_bars)
        quote: Current quote dict (from get_current_quote) — for spread

    Returns:
        Dict with all computed indicator values
    """
    if not bars or len(bars) < 5:
        return {}

    closes = [b['close'] for b in bars]
    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    volumes = [b['volume'] for b in bars]

    indicators = {}

    # --- RSI ---
    indicators['rsi_14'] = _round(calculate_rsi(closes, 14))
    indicators['rsi_2'] = _round(calculate_rsi(closes, 2))

    # --- Moving Averages ---
    indicators['sma_20'] = _round(calculate_sma(closes, 20))
    indicators['sma_50'] = _round(calculate_sma(closes, 50))
    indicators['ema_9'] = _round(calculate_ema(closes, 9))
    indicators['ema_20'] = _round(calculate_ema(closes, 20))

    # --- SMA Slopes (change per bar over last 10 bars) ---
    indicators['sma_20_slope'] = _calculate_sma_slope(closes, 20, lookback=10)
    indicators['sma_50_slope'] = _calculate_sma_slope(closes, 50, lookback=10)

    # --- VWAP ---
    indicators['vwap'] = _round(calculate_vwap(bars))

    # --- VWAP Z-Score ---
    vwap = indicators.get('vwap')
    if vwap and vwap > 0:
        z_score, vwap_std = calculate_vwap_zscore(bars, vwap)
        indicators['vwap_zscore'] = _round(z_score, 2)
        indicators['vwap_std'] = _round(vwap_std, 4)
    else:
        indicators['vwap_zscore'] = None
        indicators['vwap_std'] = None

    # --- ATR (True Range) ---
    indicators['atr'] = _round(calculate_atr(bars, 14))

    # --- Bollinger Bands ---
    bb = calculate_bollinger_bands(closes, period=20, num_std=2.0)
    indicators['bb_upper'] = _round(bb.get('upper'))
    indicators['bb_middle'] = _round(bb.get('middle'))
    indicators['bb_lower'] = _round(bb.get('lower'))
    indicators['bb_width'] = _round(bb.get('width'))

    # --- Keltner Channels ---
    kc = calculate_keltner_channels(bars, ema_period=20, atr_period=14, atr_mult=1.5)
    indicators['kc_upper'] = _round(kc.get('upper'))
    indicators['kc_middle'] = _round(kc.get('middle'))
    indicators['kc_lower'] = _round(kc.get('lower'))

    # --- TTM Squeeze Detection ---
    if indicators.get('bb_upper') and indicators.get('kc_upper'):
        indicators['squeeze_on'] = (
            indicators['bb_upper'] < indicators['kc_upper'] and
            indicators['bb_lower'] > indicators['kc_lower']
        )
    else:
        indicators['squeeze_on'] = None

    # --- ADX ---
    indicators['adx'] = _round(calculate_adx(bars, 14))

    # --- Relative Volume ---
    avg_vol = sum(volumes) / len(volumes) if volumes else 0
    current_vol = volumes[-1] if volumes else 0
    indicators['rvol'] = round(current_vol / avg_vol, 2) if avg_vol > 0 else 1.0
    indicators['current_volume'] = current_vol
    indicators['avg_volume'] = int(avg_vol)

    # --- Today HOD/LOD ---
    indicators['today_hod'] = _round(max(highs)) if highs else None
    indicators['today_lod'] = _round(min(lows)) if lows else None

    # --- Pre-market High/Low ---
    pm_bars = [b for b in bars if b['time'].hour < 9 or (b['time'].hour == 9 and b['time'].minute < 30)]
    if pm_bars:
        indicators['premarket_high'] = _round(max(b['high'] for b in pm_bars))
        indicators['premarket_low'] = _round(min(b['low'] for b in pm_bars))
    else:
        indicators['premarket_high'] = None
        indicators['premarket_low'] = None

    # --- Consecutive Direction ---
    indicators['consec_direction'] = _calculate_consecutive_direction(closes)

    # --- Spread (from quote) ---
    if quote:
        indicators['spread'] = _round(quote.get('spread', 0), 4)
        indicators['spread_pct'] = _round(quote.get('spread_pct', 0), 3)
    else:
        indicators['spread'] = None
        indicators['spread_pct'] = None

    return indicators


# =============================================================================
# INDIVIDUAL INDICATOR FUNCTIONS
# =============================================================================

def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    """Calculate Relative Strength Index."""
    if len(prices) < period + 1:
        return None

    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent = changes[-period:]
    gains = [max(0, c) for c in recent]
    losses = [abs(min(0, c)) for c in recent]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_sma(prices: List[float], period: int) -> Optional[float]:
    """Calculate Simple Moving Average over last `period` bars."""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calculate_ema(prices: List[float], period: int) -> Optional[float]:
    """Calculate Exponential Moving Average."""
    if len(prices) < period:
        return None

    multiplier = 2.0 / (period + 1)

    # Seed with SMA
    ema = sum(prices[:period]) / period

    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def calculate_vwap(bars: List[Dict]) -> Optional[float]:
    """Calculate Volume Weighted Average Price."""
    if not bars:
        return None

    total_pv = 0.0
    total_vol = 0

    for bar in bars:
        typical = (bar['high'] + bar['low'] + bar['close']) / 3
        vol = bar['volume']
        total_pv += typical * vol
        total_vol += vol

    return total_pv / total_vol if total_vol > 0 else None


def calculate_vwap_zscore(bars: List[Dict], vwap: float) -> Tuple[Optional[float], Optional[float]]:
    """
    Calculate Z-Score of current price relative to VWAP.

    Returns:
        Tuple of (z_score, standard_deviation)
    """
    if not bars or not vwap or vwap <= 0:
        return None, None

    # Calculate standard deviation of typical price from VWAP
    typical_prices = [(b['high'] + b['low'] + b['close']) / 3 for b in bars]
    deviations = [(tp - vwap) ** 2 for tp in typical_prices]
    variance = sum(deviations) / len(deviations)
    std_dev = math.sqrt(variance) if variance > 0 else 0

    if std_dev == 0:
        return 0.0, 0.0

    current_price = bars[-1]['close']
    z_score = (current_price - vwap) / std_dev

    return z_score, std_dev


def calculate_atr(bars: List[Dict], period: int = 14) -> Optional[float]:
    """
    Calculate Average True Range using true range (not just high-low).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    """
    if len(bars) < period + 1:
        # Fall back to simple range if not enough bars
        if len(bars) >= period:
            return sum(b['high'] - b['low'] for b in bars[-period:]) / period
        return None

    true_ranges = []
    for i in range(1, len(bars)):
        high = bars[i]['high']
        low = bars[i]['low']
        prev_close = bars[i - 1]['close']

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)

    # Use last `period` true ranges
    return sum(true_ranges[-period:]) / period


def calculate_bollinger_bands(
    prices: List[float], period: int = 20, num_std: float = 2.0
) -> Dict:
    """
    Calculate Bollinger Bands.

    Returns:
        Dict with: upper, middle (SMA), lower, width
    """
    if len(prices) < period:
        return {}

    window = prices[-period:]
    middle = sum(window) / period
    variance = sum((p - middle) ** 2 for p in window) / period
    std_dev = math.sqrt(variance)

    upper = middle + num_std * std_dev
    lower = middle - num_std * std_dev
    width = (upper - lower) / middle if middle > 0 else 0

    return {
        'upper': upper,
        'middle': middle,
        'lower': lower,
        'width': width,
        'std_dev': std_dev,
    }


def calculate_keltner_channels(
    bars: List[Dict],
    ema_period: int = 20,
    atr_period: int = 14,
    atr_mult: float = 1.5
) -> Dict:
    """
    Calculate Keltner Channels.

    Middle = EMA(close, ema_period)
    Upper = Middle + atr_mult × ATR(atr_period)
    Lower = Middle - atr_mult × ATR(atr_period)
    """
    closes = [b['close'] for b in bars]
    ema = calculate_ema(closes, ema_period)
    atr = calculate_atr(bars, atr_period)

    if ema is None or atr is None:
        return {}

    return {
        'upper': ema + atr_mult * atr,
        'middle': ema,
        'lower': ema - atr_mult * atr,
    }


def calculate_adx(bars: List[Dict], period: int = 14) -> Optional[float]:
    """
    Calculate Average Directional Index (ADX).

    Uses +DI/-DI/DX smoothing from OHLC data.
    """
    if len(bars) < period * 2 + 1:
        return None

    plus_dm_list = []
    minus_dm_list = []
    tr_list = []

    for i in range(1, len(bars)):
        high = bars[i]['high']
        low = bars[i]['low']
        prev_high = bars[i - 1]['high']
        prev_low = bars[i - 1]['low']
        prev_close = bars[i - 1]['close']

        # True Range
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

        # Directional Movement
        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < period:
        return None

    # Smoothed averages (Wilder's smoothing)
    def wilder_smooth(values: List[float], period: int) -> List[float]:
        smoothed = [sum(values[:period])]
        for v in values[period:]:
            smoothed.append(smoothed[-1] - smoothed[-1] / period + v)
        return smoothed

    smoothed_tr = wilder_smooth(tr_list, period)
    smoothed_plus_dm = wilder_smooth(plus_dm_list, period)
    smoothed_minus_dm = wilder_smooth(minus_dm_list, period)

    # Calculate DI and DX
    dx_list = []
    for i in range(len(smoothed_tr)):
        if smoothed_tr[i] == 0:
            continue

        plus_di = (smoothed_plus_dm[i] / smoothed_tr[i]) * 100
        minus_di = (smoothed_minus_dm[i] / smoothed_tr[i]) * 100

        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_list.append(0)
        else:
            dx = abs(plus_di - minus_di) / di_sum * 100
            dx_list.append(dx)

    if len(dx_list) < period:
        return None

    # Smooth DX to get ADX
    adx_smoothed = wilder_smooth(dx_list, period)
    return adx_smoothed[-1] if adx_smoothed else None


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _round(value, decimals: int = 2) -> Optional[float]:
    """Round a value, returning None if input is None."""
    if value is None:
        return None
    return round(float(value), decimals)


def _calculate_sma_slope(prices: List[float], sma_period: int, lookback: int = 10) -> Optional[float]:
    """
    Calculate SMA slope: change per bar over lookback window.

    Positive = uptrend, Negative = downtrend.
    """
    if len(prices) < sma_period + lookback:
        return None

    current_sma = sum(prices[-sma_period:]) / sma_period
    past_prices = prices[-(sma_period + lookback):-(lookback)]
    past_sma = sum(past_prices[-sma_period:]) / sma_period if len(past_prices) >= sma_period else None

    if past_sma is None or past_sma == 0:
        return None

    # Slope as percentage change per bar
    slope = ((current_sma - past_sma) / past_sma) * 100 / lookback
    return round(slope, 4)


def _calculate_consecutive_direction(closes: List[float]) -> int:
    """
    Count consecutive same-direction closes from the end.

    Returns:
        Positive int for consecutive up-closes, negative for down-closes.
        0 if last close == previous close.
    """
    if len(closes) < 2:
        return 0

    count = 0
    # Determine direction from last close
    for i in range(len(closes) - 1, 0, -1):
        diff = closes[i] - closes[i - 1]
        if count == 0:
            if diff > 0:
                count = 1
            elif diff < 0:
                count = -1
            else:
                return 0
        elif count > 0:
            if diff > 0:
                count += 1
            else:
                break
        else:  # count < 0
            if diff < 0:
                count -= 1
            else:
                break

    return count
