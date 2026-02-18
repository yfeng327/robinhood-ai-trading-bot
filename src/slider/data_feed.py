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

from src.api.market_data import (
    get_current_quote,
    get_intraday_bars,
    get_daily_bars,
    calculate_all_indicators,
)

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
# Note: overnight session wraps around midnight (20:00 -> 04:00)
MARKET_SESSIONS = [
    (4, 0, 9, 30, "pre_market", "Thin liquidity, gaps, London Breakout aftermath", 0.25,
     ["Gap Fade", "London Breakout", "Gap Quality Filter"]),
    (9, 30, 11, 0, "market_open", "High volatility, momentum, opening drives", 0.50,
     ["Adaptive ORB", "Momentum", "Gap & Go"]),
    (11, 0, 14, 0, "lunch", "Low volatility, mean reversion, choppy", 0.10,
     ["TTM Squeeze", "Mean Reversion", "VWAP Mean Reversion"]),
    (14, 0, 16, 0, "power_hour", "Institutional flow, VWAP defense, MOC positioning", 0.50,
     ["VWAP Hold", "MOC Flow", "Trend Continuation"]),
    (16, 0, 20, 0, "after_market", "Thin liquidity, earnings spikes, liquidity voids", 0.25,
     ["Liquidity Void Fill", "Earnings Fade"]),
    # Overnight is special: 20:00-04:00 (wraps around midnight)
    # We handle this with special logic in get_market_session()
]

# Overnight session constants (wraps around midnight)
OVERNIGHT_SESSION = {
    "name": "overnight",
    "start_hour": 20,
    "end_hour": 4,  # Next day
    "character": "Low volatility Asian session, range formation, London Breakout at 03:00",
    "kelly_fraction": 0.10,  # Micro-size
    "strategies": ["Asian Range", "London Breakout Prep", "Range Bound"],
}


def get_market_session() -> Dict:
    """
    Get current market session based on time of day.

    Returns dict with:
        - session_name: pre_market, market_open, lunch, power_hour, after_market, overnight
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
    current_hour = now.hour
    current_minutes = now.hour * 60 + now.minute

    # Check for overnight session first (wraps around midnight: 20:00-04:00)
    # Overnight is active if hour >= 20 OR hour < 4
    if current_hour >= 20 or current_hour < 4:
        if current_hour >= 20:
            # Before midnight: minutes until 04:00 next day
            mins_remaining = (24 - current_hour + 4) * 60 - now.minute
        else:
            # After midnight: minutes until 04:00
            mins_remaining = (4 - current_hour) * 60 - now.minute
        
        return {
            "session_name": OVERNIGHT_SESSION["name"],
            "session_start": f"{OVERNIGHT_SESSION['start_hour']:02d}:00 ET",
            "session_end": f"{OVERNIGHT_SESSION['end_hour']:02d}:00 ET",
            "session_character": OVERNIGHT_SESSION["character"],
            "kelly_fraction": OVERNIGHT_SESSION["kelly_fraction"],
            "recommended_strategies": OVERNIGHT_SESSION["strategies"],
            "minutes_remaining": mins_remaining,
            "phase_specific_notes": _get_phase_notes("overnight"),
        }

    # Check regular sessions (04:00-20:00)
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

    # This should not happen if overnight logic is correct, but fallback
    return {
        "session_name": "overnight",
        "session_start": "20:00 ET",
        "session_end": "04:00 ET",
        "session_character": OVERNIGHT_SESSION["character"],
        "kelly_fraction": OVERNIGHT_SESSION["kelly_fraction"],
        "recommended_strategies": OVERNIGHT_SESSION["strategies"],
        "minutes_remaining": 0,
        "phase_specific_notes": _get_phase_notes("overnight"),
    }


def _get_phase_notes(session_name: str) -> str:
    """Get phase-specific trading notes."""
    notes = {
        "overnight": (
            "- Asian session (18:00-03:00 ET) defines support/resistance range\n"
            "- London Breakout at 03:00 ET signals NY direction (70% accuracy)\n"
            "- If London breaks Asian range, NY typically continues that direction\n"
            "- Use Half-Kelly (0.5f) - do not hold TQQQ/SQQQ overnight\n"
            "- NQ futures preferred for overnight positioning"
        ),
        "pre_market": (
            "- Wide spreads indicate fake breakouts; check spread before entry\n"
            "- London Breakout (03:00-04:00 ET) often predicts NY direction\n"
            "- Gap Quality = (Volume / Avg) × (1 / Spread)\n"
            "- Use Full Kelly (1.0f) sizing for high-conviction setups"
        ),
        "market_open": (
            "- VIX-adjusted ORB: High VIX = 2-5 min bars, Low VIX = 30 min bars\n"
            "- Use Fibonacci pullback entries (50%/61.8%) instead of chasing breakouts\n"
            "- TICK > 1000 signals institutional drive\n"
            "- Use Full Kelly (1.0f) sizing for high-conviction setups"
        ),
        "lunch": (
            "- Tighten Bollinger Bands from 2.0 to 1.5 SD (lower volatility)\n"
            "- VWAP mean reversion is dominant strategy\n"
            "- Avoid momentum plays; favor mean reversion\n"
            "- Use Half-Kelly (0.5f) - this is the worst time for directional bets"
        ),
        "power_hour": (
            "- MOC imbalances at 15:50 ET predict close direction\n"
            "- Institutions defend VWAP aggressively\n"
            "- Lunch ambiguity resolves with directional break\n"
            "- Use Full Kelly (1.0f) sizing for high-conviction setups"
        ),
        "after_market": (
            "- Thin liquidity creates volatile moves on earnings\n"
            "- Liquidity void fills are common\n"
            "- Wide spreads = higher slippage risk\n"
            "-Use Full Kelly (1.0f) sizing for high-conviction setups"
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
        """Fetch all required data via market_data module."""
        symbol = REFERENCE_SYMBOL
        now = datetime.now(self.et_tz)

        # Get current quote (3-tier price resolution handled in market_data)
        quote = get_current_quote(symbol)
        current_price = quote.get('price', 0)
        bid = quote.get('bid', 0)
        ask = quote.get('ask', 0)

        # Get intraday 5-min bars (with extended hours for pre-market data)
        intraday_bars = get_intraday_bars(symbol, extended=True)

        # Get daily bars for gap/prev day calculation
        daily_bars = get_daily_bars(symbol)
        prev_day = daily_bars[-2] if len(daily_bars) >= 2 else None
        today = daily_bars[-1] if daily_bars else None

        # Build decaying resolution table from parsed bars
        intraday_table = self._build_decaying_table_from_parsed(intraday_bars, now)

        # Calculate ALL indicators (14+) from bar data
        indicators = calculate_all_indicators(intraday_bars, quote)

        # Gap info
        gap_info = self._calculate_gap_info(prev_day, today, intraday_bars)

        # Opening range (first 15 mins)
        opening_range = self._calculate_opening_range(intraday_bars)

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
            "prev_day_close": prev_day['close'] if prev_day else None,
            "today_open": today['open'] if today else None,
        }
    
    def _build_decaying_table_from_parsed(self, bars: List[Dict], now: datetime) -> str:
        """
        Build markdown table with decaying time resolution.
        
        Recent data = high resolution, older data = aggregated.
        Bars are already parsed dicts with 'time', 'open', 'high', 'low', 'close', 'volume'.
        """
        if not bars:
            return "No intraday data available"
        
        # Filter to regular hours for the table display
        regular_bars = [
            b for b in bars
            if (b['time'].hour > 9 or (b['time'].hour == 9 and b['time'].minute >= 30))
            and b['time'].hour < 16
        ]
        
        if not regular_bars:
            # Fall back to all bars if no regular hours bars
            regular_bars = bars
        
        if not regular_bars:
            return "No valid bars"
        
        # Aggregate into buckets based on age
        aggregated = []
        for hours_start, hours_end, bucket_mins in TIME_BUCKETS:
            cutoff_start = now - timedelta(hours=hours_end)
            cutoff_end = now - timedelta(hours=hours_start)
            
            bucket_bars = [b for b in regular_bars if cutoff_start <= b['time'] < cutoff_end]
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
    
    # NOTE: _calculate_indicators, _calculate_rsi, _calculate_vwap removed.
    # All indicator computation is now in src.api.market_data.calculate_all_indicators()
    
    def _calculate_gap_info(self, prev_day: Dict, today: Dict, intraday: List) -> Dict:
        """Calculate gap information for gap trading strategy.
        
        Args use parsed bar format: {'open': float, 'high': float, 'low': float, 'close': float, ...}
        """
        if not prev_day or not today:
            return {"gap_exists": False}
        
        prev_close = prev_day['close']
        today_open = today['open']
        gap = today_open - prev_close
        gap_pct = (gap / prev_close) * 100
        
        # Estimate ATR from prev day range
        prev_range = prev_day['high'] - prev_day['low']
        gap_atr_multiple = abs(gap) / prev_range if prev_range > 0 else 0
        
        # First 5-min candle info (filter to regular hours)
        regular_bars = [
            b for b in intraday
            if b['time'].hour > 9 or (b['time'].hour == 9 and b['time'].minute >= 30)
        ]
        first_candle = regular_bars[0] if regular_bars else None
        first_candle_info = None
        if first_candle:
            body = abs(first_candle['close'] - first_candle['open'])
            range_ = first_candle['high'] - first_candle['low']
            body_pct = (body / range_ * 100) if range_ > 0 else 0
            first_candle_info = {
                "body_pct": round(body_pct, 1),
                "volume": first_candle['volume'],
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
        """Calculate opening range (first 15 minutes).
        
        Uses parsed bar format: {'time': datetime, 'open': float, 'high': float, ...}
        """
        if not intraday:
            return {"or_defined": False}
        
        # Filter to regular hours bars, take first 3 (= first 15 min of 5-min bars)
        regular_bars = [
            b for b in intraday
            if b['time'].hour > 9 or (b['time'].hour == 9 and b['time'].minute >= 30)
        ]
        or_bars = regular_bars[:3]
        if len(or_bars) < 3:
            return {"or_defined": False}
        
        or_high = max(b['high'] for b in or_bars)
        or_low = min(b['low'] for b in or_bars)
        or_width = or_high - or_low
        
        # Current price position relative to OR
        current = regular_bars[-1]['close'] if regular_bars else None
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
            # Core indicators
            lines.extend([
                f"- RSI(14): {ind.get('rsi_14', 'N/A')}",
                f"- RSI(2): {ind.get('rsi_2', 'N/A')}",
                f"- VWAP: ${ind.get('vwap', 'N/A')}",
                f"- SMA(20): ${ind.get('sma_20', 'N/A')}",
                f"- SMA(50): ${ind.get('sma_50', 'N/A')}",
                f"- EMA(9): ${ind.get('ema_9', 'N/A')}",
                f"- EMA(20): ${ind.get('ema_20', 'N/A')}",
                f"- ATR(14): ${ind.get('atr', 'N/A')}",
                f"- Relative Volume: {ind.get('rvol', 'N/A')}x average",
                f"- ADX(14): {ind.get('adx', 'N/A')}",
            ])

            # Bollinger Bands
            if ind.get('bb_upper'):
                lines.extend([
                    "",
                    "**Bollinger Bands (20,2):**",
                    f"- BB Upper: ${ind['bb_upper']}",
                    f"- BB Middle: ${ind.get('bb_middle', 'N/A')}",
                    f"- BB Lower: ${ind.get('bb_lower', 'N/A')}",
                    f"- BB Width: {ind.get('bb_width', 'N/A')}",
                ])

            # Keltner Channels
            if ind.get('kc_upper'):
                lines.extend([
                    "",
                    "**Keltner Channels (EMA20, 1.5×ATR):**",
                    f"- KC Upper: ${ind['kc_upper']}",
                    f"- KC Middle: ${ind.get('kc_middle', 'N/A')}",
                    f"- KC Lower: ${ind.get('kc_lower', 'N/A')}",
                ])

            # Squeeze detection
            if ind.get('squeeze_on') is not None:
                squeeze_str = "🔴 ON (BB inside KC)" if ind['squeeze_on'] else "🟢 OFF (BB outside KC)"
                lines.append(f"- TTM Squeeze: {squeeze_str}")

            # VWAP Z-Score
            if ind.get('vwap_zscore') is not None:
                lines.extend([
                    "",
                    "**VWAP Statistics:**",
                    f"- VWAP Z-Score: {ind['vwap_zscore']}",
                    f"- VWAP Std Dev: ${ind.get('vwap_std', 'N/A')}",
                ])

            # SMA Slopes
            if ind.get('sma_20_slope') is not None:
                lines.extend([
                    "",
                    "**Trend Slopes (% per bar):**",
                    f"- SMA(20) Slope: {ind['sma_20_slope']}",
                    f"- SMA(50) Slope: {ind.get('sma_50_slope', 'N/A')}",
                ])

            # Price Range
            if ind.get('today_hod'):
                lines.extend([
                    "",
                    "**Price Range:**",
                    f"- HOD: ${ind['today_hod']}",
                    f"- LOD: ${ind.get('today_lod', 'N/A')}",
                ])
            if ind.get('premarket_high'):
                lines.extend([
                    f"- Pre-Market High: ${ind['premarket_high']}",
                    f"- Pre-Market Low: ${ind.get('premarket_low', 'N/A')}",
                ])

            # Market Microstructure
            if ind.get('spread') is not None:
                lines.extend([
                    "",
                    "**Market Microstructure:**",
                    f"- Bid-Ask Spread: ${ind['spread']}",
                    f"- Spread %: {ind.get('spread_pct', 'N/A')}%",
                    f"- Consecutive Direction: {ind.get('consec_direction', 0)} bars",
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
