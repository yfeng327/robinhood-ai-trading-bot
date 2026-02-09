"""
QQQ Data Feed — Fetches market data for slider strategy analysis.

Uses decaying time intervals:
- Last hour: 5-min bars
- 1-2 hours ago: 15-min bars (aggregated)
- 2+ hours ago: 30-min bars (aggregated)

This keeps total prompt rows reasonable (~30-40 rows max).
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pytz import timezone

import robin_stocks.robinhood as rh
from src.api import robinhood

logger = logging.getLogger(__name__)

# QQQ is our reference symbol for NASDAQ direction
REFERENCE_SYMBOL = "QQQ"

# Time buckets for decaying resolution
# (hours_ago_start, hours_ago_end, bucket_minutes)
TIME_BUCKETS = [
    (0, 1, 5),     # Last hour: 5-min resolution
    (1, 2, 15),    # 1-2 hours ago: 15-min resolution
    (2, 4, 30),    # 2-4 hours ago: 30-min resolution
]

# Market session definitions (Eastern Time)
# Each session: (start_hour, start_min, end_hour, end_min, name, character, kelly_fraction, strategies)
MARKET_SESSIONS = [
    (4, 0, 9, 30, "pre_market", "Thin liquidity, gaps, news reactions", 0.25,
     ["Gap Fade", "London Breakout"]),
    (9, 30, 11, 0, "market_open", "High volatility, momentum, opening drives", 0.50,
     ["Adaptive ORB", "Momentum"]),
    (11, 0, 14, 0, "lunch", "Low volatility, mean reversion, choppy", 0.10,
     ["TTM Squeeze", "Mean Reversion", "VWAP Mean Reversion"]),
    (14, 0, 16, 0, "power_hour", "Institutional flow, VWAP defense, MOC positioning", 0.50,
     ["VWAP Hold", "MOC Flow"]),
    (16, 0, 20, 0, "after_market", "Thin liquidity, earnings spikes", 0.25,
     ["Liquidity Void Fill"]),
]


def get_market_session() -> Dict:
    """
    Get current market session based on time of day.

    Returns dict with:
        - session_name: pre_market, market_open, lunch, power_hour, after_market, closed
        - session_start: Start time string (HH:MM ET)
        - session_end: End time string (HH:MM ET)
        - session_character: Description of session behavior
        - kelly_fraction: Recommended Kelly sizing (0.10 to 0.50)
        - recommended_strategies: List of strategies that work well in this session
        - minutes_remaining: Minutes until session ends
        - phase_specific_notes: Key trading considerations for this phase
    """
    et_tz = timezone('US/Eastern')
    now = datetime.now(et_tz)
    current_minutes = now.hour * 60 + now.minute

    for start_h, start_m, end_h, end_m, name, character, kelly, strategies in MARKET_SESSIONS:
        start_mins = start_h * 60 + start_m
        end_mins = end_h * 60 + end_m

        if start_mins <= current_minutes < end_mins:
            mins_remaining = end_mins - current_minutes
            return {
                "session_name": name,
                "session_start": f"{start_h:02d}:{start_m:02d} ET",
                "session_end": f"{end_h:02d}:{end_m:02d} ET",
                "session_character": character,
                "kelly_fraction": kelly,
                "recommended_strategies": strategies,
                "minutes_remaining": mins_remaining,
                "phase_specific_notes": _get_phase_notes(name),
            }

    # Market closed (before 4am or after 8pm ET)
    return {
        "session_name": "closed",
        "session_start": "N/A",
        "session_end": "N/A",
        "session_character": "Market closed",
        "kelly_fraction": 0.0,
        "recommended_strategies": [],
        "minutes_remaining": 0,
        "phase_specific_notes": "Market is closed. No trading recommended.",
    }


def _get_phase_notes(session_name: str) -> str:
    """Get phase-specific trading notes."""
    notes = {
        "pre_market": (
            "- Wide spreads indicate fake breakouts; check spread before entry\n"
            "- London Breakout (03:00-04:00 ET) often predicts NY direction\n"
            "- Gap Quality = (Volume / Avg) × (1 / Spread)\n"
            "- Use Quarter-Kelly (0.25f) sizing due to thin liquidity"
        ),
        "market_open": (
            "- VIX-adjusted ORB: High VIX = 2-5 min bars, Low VIX = 30 min bars\n"
            "- Use Fibonacci pullback entries (50%/61.8%) instead of chasing breakouts\n"
            "- TICK > 1000 signals institutional drive\n"
            "- Use Half-Kelly (0.5f) sizing for high-conviction setups"
        ),
        "lunch": (
            "- Tighten Bollinger Bands from 2.0 to 1.5 SD (lower volatility)\n"
            "- VWAP mean reversion is dominant strategy\n"
            "- Avoid momentum plays; favor mean reversion\n"
            "- Use Micro-Size (0.1f) - this is the worst time for directional bets"
        ),
        "power_hour": (
            "- MOC imbalances at 15:50 ET predict close direction\n"
            "- Institutions defend VWAP aggressively\n"
            "- Lunch ambiguity resolves with directional break\n"
            "- Use Half-Kelly (0.5f) for trend continuation plays"
        ),
        "after_market": (
            "- Thin liquidity creates volatile moves on earnings\n"
            "- Liquidity void fills are common\n"
            "- Wide spreads = higher slippage risk\n"
            "- Use Quarter-Kelly (0.25f) due to execution risk"
        ),
    }
    return notes.get(session_name, "No specific notes for this session.")


class QQQDataFeed:
    """Fetches and formats QQQ market data for slider analysis."""
    
    def __init__(self):
        self.et_tz = timezone('US/Eastern')
        self._cache = {}
        self._cache_time = None
        self._cache_ttl = timedelta(seconds=30)  # Cache for 30 seconds
    
    def get_market_data(self) -> Dict:
        """
        Get comprehensive QQQ market data for strategy nodes.
        
        Returns:
            Dict with current price, historical bars, indicators, gap info, etc.
        """
        now = datetime.now(self.et_tz)
        
        # Check cache
        if self._cache_time and (now - self._cache_time) < self._cache_ttl:
            return self._cache
        
        try:
            data = self._fetch_all_data()
            self._cache = data
            self._cache_time = now
            return data
        except Exception as e:
            logger.error(f"Failed to fetch QQQ data: {e}")
            return self._empty_data()
    
    def _fetch_all_data(self) -> Dict:
        """Fetch all required data from Robinhood."""
        symbol = REFERENCE_SYMBOL
        now = datetime.now(self.et_tz)
        
        # Get current quote
        try:
            quotes = rh.stocks.get_stock_quote_by_symbol(symbol)
            current_price = float(quotes.get('last_trade_price', 0))
            bid = float(quotes.get('bid_price', 0) or 0)
            ask = float(quotes.get('ask_price', 0) or 0)
        except Exception as e:
            logger.warning(f"Quote fetch failed: {e}")
            current_price, bid, ask = 0, 0, 0
        
        # Get intraday 5-min bars
        try:
            historicals_day = rh.stocks.get_stock_historicals(
                symbol, interval='5minute', span='day', bounds='regular'
            )
        except Exception as e:
            logger.warning(f"Intraday data fetch failed: {e}")
            historicals_day = []
        
        # Get previous day data for gap calculation
        try:
            historicals_week = rh.stocks.get_stock_historicals(
                symbol, interval='day', span='week', bounds='regular'
            )
            prev_day = historicals_week[-2] if len(historicals_week) >= 2 else None
            today = historicals_week[-1] if historicals_week else None
        except Exception as e:
            logger.warning(f"Daily data fetch failed: {e}")
            prev_day, today = None, None
        
        # Build decaying resolution table
        intraday_table = self._build_decaying_table(historicals_day, now)
        
        # Calculate indicators (if we have data)
        indicators = self._calculate_indicators(historicals_day)
        
        # Gap info
        gap_info = self._calculate_gap_info(prev_day, today, historicals_day)
        
        # Opening range (first 15 mins)
        opening_range = self._calculate_opening_range(historicals_day)
        
        return {
            "symbol": symbol,
            "current_price": current_price,
            "bid": bid,
            "ask": ask,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S ET"),
            "intraday_table": intraday_table,
            "indicators": indicators,
            "gap_info": gap_info,
            "opening_range": opening_range,
            "prev_day_close": float(prev_day['close_price']) if prev_day else None,
            "today_open": float(today['open_price']) if today else None,
        }
    
    def _build_decaying_table(self, bars: List, now: datetime) -> str:
        """
        Build markdown table with decaying time resolution.
        
        Recent data = high resolution, older data = aggregated.
        """
        if not bars:
            return "No intraday data available"
        
        # Parse all bars with timestamps
        parsed = []
        for bar in bars:
            try:
                ts = datetime.fromisoformat(bar['begins_at'].replace('Z', '+00:00'))
                ts_et = ts.astimezone(self.et_tz)
                parsed.append({
                    'time': ts_et,
                    'open': float(bar['open_price']),
                    'high': float(bar['high_price']),
                    'low': float(bar['low_price']),
                    'close': float(bar['close_price']),
                    'volume': int(bar['volume']),
                })
            except Exception:
                continue
        
        if not parsed:
            return "No valid bars"
        
        # Aggregate into buckets based on age
        aggregated = []
        for hours_start, hours_end, bucket_mins in TIME_BUCKETS:
            cutoff_start = now - timedelta(hours=hours_end)
            cutoff_end = now - timedelta(hours=hours_start)
            
            bucket_bars = [b for b in parsed if cutoff_start <= b['time'] < cutoff_end]
            if not bucket_bars:
                continue
            
            # Group into buckets of bucket_mins
            buckets = self._aggregate_bars(bucket_bars, bucket_mins)
            aggregated.extend(buckets)
        
        # Build markdown table
        lines = [
            "| Time (ET) | Open | High | Low | Close | Volume |",
            "|-----------|------|------|-----|-------|--------|",
        ]
        
        # Sort by time descending (most recent first), limit to 35 rows
        aggregated.sort(key=lambda x: x['time'], reverse=True)
        for row in aggregated[:35]:
            time_str = row['time'].strftime("%H:%M")
            lines.append(
                f"| {time_str} | {row['open']:.2f} | {row['high']:.2f} | "
                f"{row['low']:.2f} | {row['close']:.2f} | {row['volume']:,} |"
            )
        
        return "\n".join(lines)
    
    def _aggregate_bars(self, bars: List, bucket_mins: int) -> List:
        """Aggregate bars into larger time buckets."""
        if not bars or bucket_mins <= 5:
            return bars  # Already 5-min, no aggregation needed
        
        # Group by bucket
        buckets = {}
        for bar in bars:
            bucket_key = bar['time'].replace(
                minute=(bar['time'].minute // bucket_mins) * bucket_mins,
                second=0, microsecond=0
            )
            if bucket_key not in buckets:
                buckets[bucket_key] = []
            buckets[bucket_key].append(bar)
        
        # Aggregate each bucket
        aggregated = []
        for bucket_time, bucket_bars in buckets.items():
            if not bucket_bars:
                continue
            aggregated.append({
                'time': bucket_time,
                'open': bucket_bars[0]['open'],
                'high': max(b['high'] for b in bucket_bars),
                'low': min(b['low'] for b in bucket_bars),
                'close': bucket_bars[-1]['close'],
                'volume': sum(b['volume'] for b in bucket_bars),
            })
        
        return aggregated
    
    def _calculate_indicators(self, bars: List) -> Dict:
        """Calculate technical indicators from bar data."""
        if not bars or len(bars) < 14:
            return {}
        
        closes = [float(b['close_price']) for b in bars]
        volumes = [int(b['volume']) for b in bars]
        
        # Simple RSI calculation
        rsi = self._calculate_rsi(closes, period=14)
        rsi_2 = self._calculate_rsi(closes, period=2) if len(closes) >= 3 else None
        
        # VWAP (simplified - sum of price*vol / sum of vol)
        vwap = self._calculate_vwap(bars)
        
        # Moving averages
        sma_20 = sum(closes[-20:]) / min(20, len(closes)) if closes else None
        sma_50 = sum(closes[-50:]) / min(50, len(closes)) if len(closes) >= 20 else None
        
        # Average volume
        avg_volume = sum(volumes) / len(volumes) if volumes else 0
        current_volume = volumes[-1] if volumes else 0
        rvol = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        # Simple ATR approximation (average of high-low)
        highs = [float(b['high_price']) for b in bars]
        lows = [float(b['low_price']) for b in bars]
        atr = sum(h - l for h, l in zip(highs[-14:], lows[-14:])) / min(14, len(bars))
        
        return {
            "rsi_14": round(rsi, 1) if rsi else None,
            "rsi_2": round(rsi_2, 1) if rsi_2 else None,
            "vwap": round(vwap, 2) if vwap else None,
            "sma_20": round(sma_20, 2) if sma_20 else None,
            "sma_50": round(sma_50, 2) if sma_50 else None,
            "rvol": round(rvol, 2),
            "atr": round(atr, 2) if atr else None,
            "current_volume": current_volume,
            "avg_volume": int(avg_volume),
        }
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        """Calculate RSI from price series."""
        if len(prices) < period + 1:
            return None
        
        changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [max(0, c) for c in changes[-period:]]
        losses = [abs(min(0, c)) for c in changes[-period:]]
        
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def _calculate_vwap(self, bars: List) -> Optional[float]:
        """Calculate VWAP from bar data."""
        if not bars:
            return None
        
        total_pv = 0
        total_vol = 0
        
        for bar in bars:
            typical = (float(bar['high_price']) + float(bar['low_price']) + float(bar['close_price'])) / 3
            vol = int(bar['volume'])
            total_pv += typical * vol
            total_vol += vol
        
        return total_pv / total_vol if total_vol > 0 else None
    
    def _calculate_gap_info(self, prev_day: Dict, today: Dict, intraday: List) -> Dict:
        """Calculate gap information for gap trading strategy."""
        if not prev_day or not today:
            return {"gap_exists": False}
        
        prev_close = float(prev_day['close_price'])
        today_open = float(today['open_price'])
        gap = today_open - prev_close
        gap_pct = (gap / prev_close) * 100
        
        # Estimate ATR from prev day range
        prev_range = float(prev_day['high_price']) - float(prev_day['low_price'])
        gap_atr_multiple = abs(gap) / prev_range if prev_range > 0 else 0
        
        # First 5-min candle info
        first_candle = intraday[0] if intraday else None
        first_candle_info = None
        if first_candle:
            body = abs(float(first_candle['close_price']) - float(first_candle['open_price']))
            range_ = float(first_candle['high_price']) - float(first_candle['low_price'])
            body_pct = (body / range_ * 100) if range_ > 0 else 0
            first_candle_info = {
                "body_pct": round(body_pct, 1),
                "volume": int(first_candle['volume']),
                "is_doji": body_pct < 20,
            }
        
        return {
            "gap_exists": True,
            "gap_dollars": round(gap, 2),
            "gap_pct": round(gap_pct, 2),
            "gap_atr_multiple": round(gap_atr_multiple, 2),
            "direction": "up" if gap > 0 else "down",
            "prev_close": prev_close,
            "today_open": today_open,
            "first_candle": first_candle_info,
        }
    
    def _calculate_opening_range(self, intraday: List) -> Dict:
        """Calculate opening range (first 15 minutes)."""
        if not intraday:
            return {"or_defined": False}
        
        # First 3 bars = first 15 minutes (5-min bars)
        or_bars = intraday[:3]
        if len(or_bars) < 3:
            return {"or_defined": False}
        
        or_high = max(float(b['high_price']) for b in or_bars)
        or_low = min(float(b['low_price']) for b in or_bars)
        or_width = or_high - or_low
        
        # Current price position relative to OR
        current = float(intraday[-1]['close_price']) if intraday else None
        position = "inside"
        if current:
            if current > or_high:
                position = "above"
            elif current < or_low:
                position = "below"
        
        return {
            "or_defined": True,
            "or_high": round(or_high, 2),
            "or_low": round(or_low, 2),
            "or_width": round(or_width, 2),
            "or_mid": round((or_high + or_low) / 2, 2),
            "current_position": position,
        }
    
    def _empty_data(self) -> Dict:
        """Return empty data structure on error."""
        return {
            "symbol": REFERENCE_SYMBOL,
            "current_price": 0,
            "timestamp": datetime.now(self.et_tz).strftime("%Y-%m-%d %H:%M:%S ET"),
            "intraday_table": "Data unavailable",
            "indicators": {},
            "gap_info": {"gap_exists": False},
            "opening_range": {"or_defined": False},
        }
    
    def format_for_prompt(self, data: Dict) -> str:
        """Format market data as a string for LLM prompts."""
        # Get current market session
        session = get_market_session()

        lines = [
            f"**QQQ Current Price:** ${data['current_price']:.2f}",
            f"**Timestamp:** {data['timestamp']}",
            "",
            "## MARKET SESSION",
            f"**Current Phase:** {session['session_name'].upper().replace('_', ' ')}",
            f"**Time Window:** {session['session_start']} - {session['session_end']} ({session['minutes_remaining']} min remaining)",
            f"**Character:** {session['session_character']}",
            f"**Kelly Sizing:** {session['kelly_fraction']:.0%} (use this fraction of normal position size)",
            f"**Best Strategies:** {', '.join(session['recommended_strategies']) if session['recommended_strategies'] else 'None'}",
            "",
            "**Phase-Specific Notes:**",
            session['phase_specific_notes'],
            "",
            "### Intraday Price Action (Decaying Resolution)",
            data['intraday_table'],
            "",
            "### Technical Indicators",
        ]

        ind = data.get('indicators', {})
        if ind:
            lines.extend([
                f"- RSI(14): {ind.get('rsi_14', 'N/A')}",
                f"- RSI(2): {ind.get('rsi_2', 'N/A')}",
                f"- VWAP: ${ind.get('vwap', 'N/A')}",
                f"- SMA(20): ${ind.get('sma_20', 'N/A')}",
                f"- Relative Volume: {ind.get('rvol', 'N/A')}x average",
                f"- ATR: ${ind.get('atr', 'N/A')}",
            ])

        return "\n".join(lines)
    
    def format_gap_info(self, data: Dict) -> str:
        """Format gap information for gap trading prompt."""
        gap = data.get('gap_info', {})
        if not gap.get('gap_exists'):
            return "No gap data available"
        
        lines = [
            f"**Gap Direction:** {gap.get('direction', 'N/A').upper()}",
            f"**Gap Size:** ${gap.get('gap_dollars', 0):.2f} ({gap.get('gap_pct', 0):.2f}%)",
            f"**Gap ATR Multiple:** {gap.get('gap_atr_multiple', 0):.2f}x",
            f"**Previous Close:** ${gap.get('prev_close', 0):.2f}",
            f"**Today Open:** ${gap.get('today_open', 0):.2f}",
        ]
        
        fc = gap.get('first_candle')
        if fc:
            lines.extend([
                "",
                "**First 5-min Candle:**",
                f"- Body %: {fc.get('body_pct', 0):.1f}%",
                f"- Is Doji: {'Yes' if fc.get('is_doji') else 'No'}",
                f"- Volume: {fc.get('volume', 0):,}",
            ])
        
        return "\n".join(lines)
    
    def format_opening_range(self, data: Dict) -> str:
        """Format opening range for ORB prompt."""
        orng = data.get('opening_range', {})
        if not orng.get('or_defined'):
            return "Opening range not yet established"
        
        return "\n".join([
            f"**Opening Range (First 15 min):**",
            f"- OR High: ${orng.get('or_high', 0):.2f}",
            f"- OR Low: ${orng.get('or_low', 0):.2f}",
            f"- OR Width: ${orng.get('or_width', 0):.2f}",
            f"- OR Mid: ${orng.get('or_mid', 0):.2f}",
            f"- Current Position: **{orng.get('current_position', 'unknown').upper()}**",
        ])
