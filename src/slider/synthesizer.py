"""
Synthesizer — Combines 4 strategy outputs into final slider recommendation.

Uses confluence scoring with:
- Weighted averaging based on strategy weights
- Agreement bonus/penalty
- Time-of-day weight adjustments
- LLM-based synthesis with fallback to weighted average
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from pytz import timezone
from src.api import ai

logger = logging.getLogger(__name__)

# Path to prompts directory
PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

# Base weights for each strategy (must sum to 1.0)
BASE_WEIGHTS = {
    "ttm_squeeze": 0.20,
    "orb": 0.25,
    "mean_reversion": 0.20,
    "gap_trading": 0.15,
    "deepseek": 0.20,
}


def _load_synthesizer_prompt() -> str:
    """Load synthesizer prompt template."""
    path = PROMPTS_DIR / "slider_synthesizer.md"
    if not path.exists():
        logger.error(f"Synthesizer prompt not found: {path}")
        return ""
    return path.read_text(encoding='utf-8')


def _get_time_adjusted_weights() -> Dict[str, float]:
    """Adjust strategy weights based on time of day."""
    et_tz = timezone('US/Eastern')
    now = datetime.now(et_tz)
    hour = now.hour
    minute = now.minute
    current_time = hour + minute / 60

    weights = BASE_WEIGHTS.copy()

    # 9:30-10:30 ET: Boost ORB and Gap (opening dynamics)
    if 9.5 <= current_time < 10.5:
        weights["orb"] += 0.15
        weights["gap_trading"] += 0.10
        weights["ttm_squeeze"] -= 0.10
        weights["mean_reversion"] -= 0.10
        weights["deepseek"] -= 0.05

    # 10:30-12:00 ET: Boost TTM Squeeze (momentum builds)
    elif 10.5 <= current_time < 12.0:
        weights["ttm_squeeze"] += 0.10
        weights["gap_trading"] -= 0.10

    # 12:00-15:00 ET: Boost Mean Reversion and DeepSeek (lunchtime chop - need holistic view)
    elif 12.0 <= current_time < 15.0:
        weights["mean_reversion"] += 0.10
        weights["deepseek"] += 0.05
        weights["orb"] -= 0.10
        weights["gap_trading"] -= 0.05

    # 15:00-16:00 ET: Late day - boost DeepSeek for end-of-day positioning
    elif 15.0 <= current_time < 16.0:
        weights["deepseek"] += 0.10
        weights["gap_trading"] -= 0.05
        weights["orb"] -= 0.05

    # Normalize weights to sum to 1.0
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def synthesize_final_slider(
    strategy_results: Dict[str, Dict],
    market_summary: str = ""
) -> Dict:
    """
    Synthesize final slider from strategy outputs.
    
    Tries LLM synthesis first, falls back to weighted average.
    
    Args:
        strategy_results: Dict mapping strategy name to result dict
        market_summary: Brief current market state summary
    
    Returns:
        Dict with final_slider, confidence, regime, reasoning
    """
    # Try LLM synthesis
    try:
        result = _llm_synthesize(strategy_results, market_summary)
        if result.get("success"):
            logger.info(f"LLM synthesis: slider={result['final_slider']:.2f}")
            return result
    except Exception as e:
        logger.warning(f"LLM synthesis failed: {e}")
    
    # Fallback to weighted average
    logger.info("Using weighted average fallback")
    return _weighted_average_synthesis(strategy_results)


def _llm_synthesize(
    strategy_results: Dict[str, Dict],
    market_summary: str
) -> Dict:
    """Run LLM-based synthesis."""
    prompt_template = _load_synthesizer_prompt()
    if not prompt_template:
        return {"success": False}
    
    # Format strategy outputs
    from .strategy_nodes import format_strategy_outputs_for_synthesizer
    strategy_table = format_strategy_outputs_for_synthesizer(strategy_results)
    
    # Build prompt
    prompt = prompt_template.replace("{strategy_outputs}", strategy_table)
    prompt = prompt.replace("{market_summary}", market_summary or "No additional market context")
    
    try:
        response = ai.make_ai_request(prompt)
        raw = ai.get_raw_response_content(response)
        return _parse_synthesizer_output(raw)
    except Exception as e:
        logger.error(f"Synthesizer LLM error: {e}")
        return {"success": False}


def _parse_synthesizer_output(raw: str) -> Dict:
    """Parse synthesizer LLM output."""
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()
        
        result = json.loads(cleaned)
        
        final_slider = float(result.get("final_slider", 0))
        final_slider = max(-1.0, min(1.0, final_slider))
        
        confidence = float(result.get("confidence", 0))
        confidence = max(0.0, min(1.0, confidence))
        
        return {
            "final_slider": final_slider,
            "confidence": confidence,
            "regime": result.get("regime", "unknown"),
            "strategy_agreement": result.get("strategy_agreement", 0),
            "reasoning": result.get("reasoning", ""),
            "success": True,
        }
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to parse synthesizer output: {e}")
        return {"success": False}


def _weighted_average_synthesis(strategy_results: Dict[str, Dict]) -> Dict:
    """
    Fallback: weighted average of strategy sliders.
    
    Applies time-adjusted weights and agreement bonus/penalty.
    """
    weights = _get_time_adjusted_weights()
    
    weighted_sum = 0.0
    weight_sum = 0.0
    directions = []
    
    for strategy_name, result in strategy_results.items():
        if not result.get("success"):
            continue
        
        slider = result.get("slider", 0)
        confidence = result.get("confidence", 0)
        weight = weights.get(strategy_name, 0.25)
        
        # Weight by both base weight and confidence
        effective_weight = weight * confidence
        weighted_sum += slider * effective_weight
        weight_sum += effective_weight
        
        # Track directions for agreement
        if slider > 0.1:
            directions.append("bullish")
        elif slider < -0.1:
            directions.append("bearish")
        else:
            directions.append("neutral")
    
    # Calculate raw slider
    if weight_sum > 0:
        raw_slider = weighted_sum / weight_sum
    else:
        raw_slider = 0.0
    
    # Agreement bonus/penalty (now 5 strategies)
    bullish_count = directions.count("bullish")
    bearish_count = directions.count("bearish")
    neutral_count = directions.count("neutral")

    if bullish_count >= 4 or bearish_count >= 4:
        # Very strong agreement (4+ of 5) - significant boost
        raw_slider *= 1.3
    elif bullish_count >= 3 or bearish_count >= 3:
        # Good agreement (3 of 5) - moderate boost
        raw_slider *= 1.15
    elif bullish_count == 2 and bearish_count == 2:
        # Split (2 vs 2 + 1 neutral) - reduce
        raw_slider *= 0.5
    elif neutral_count >= 3:
        # Most neutral - reduce
        raw_slider *= 0.3
    
    # Apply Half-Kelly dampening
    final_slider = raw_slider * 0.5
    final_slider = max(-1.0, min(1.0, final_slider))
    
    # Calculate average confidence
    confidences = [r.get("confidence", 0) for r in strategy_results.values() if r.get("success")]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0
    
    return {
        "final_slider": round(final_slider, 3),
        "confidence": round(avg_confidence, 3),
        "regime": "unknown",
        "strategy_agreement": max(bullish_count, bearish_count),
        "reasoning": f"Weighted avg: {len([d for d in directions if d != 'neutral'])}/4 strategies active",
        "success": True,
    }


def format_slider_for_display(result: Dict) -> str:
    """Format synthesizer result for logging/display."""
    slider = result.get("final_slider", 0)
    conf = result.get("confidence", 0)
    regime = result.get("regime", "unknown")
    agreement = result.get("strategy_agreement", 0)
    
    # Direction description
    if slider > 0.5:
        direction = "STRONG BULLISH"
    elif slider > 0.2:
        direction = "BULLISH"
    elif slider > 0.05:
        direction = "SLIGHT BULLISH"
    elif slider < -0.5:
        direction = "STRONG BEARISH"
    elif slider < -0.2:
        direction = "BEARISH"
    elif slider < -0.05:
        direction = "SLIGHT BEARISH"
    else:
        direction = "NEUTRAL"
    
    return (
        f"Final Slider: {slider:+.2f} ({direction})\n"
        f"Confidence: {conf:.0%}\n"
        f"Regime: {regime}\n"
        f"Strategy Agreement: {agreement}/5"
    )
