"""
Synthesizer — DeepSeek-powered confluence synthesizer.

Uses DeepSeek's judgement to combine strategy outputs into final slider.
NO formula-based voting or weighted averaging — pure AI reasoning.

Fallback: Simple weighted average (emergency only).
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict

from pytz import timezone

from src.api import deepseek

logger = logging.getLogger(__name__)

# Path to prompts directory
PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def _load_synthesizer_prompt() -> str:
    """Load synthesizer prompt template."""
    path = PROMPTS_DIR / "slider_synthesizer.md"
    if not path.exists():
        logger.error(f"Synthesizer prompt not found: {path}")
        return ""
    return path.read_text(encoding='utf-8')


def synthesize_final_slider(
    strategy_results: Dict[str, Dict],
    market_summary: str = ""
) -> Dict:
    """
    Synthesize final slider from strategy outputs using DeepSeek.

    Args:
        strategy_results: Dict mapping strategy name to result dict
        market_summary: Brief current market state summary

    Returns:
        Dict with final_slider, confidence, regime, reasoning
    """
    # Try DeepSeek synthesis
    if deepseek.is_deepseek_configured():
        try:
            result = _deepseek_synthesize(strategy_results, market_summary)
            if result.get("success"):
                logger.info(f"DeepSeek synthesis: slider={result['final_slider']:.2f}")
                return result
        except Exception as e:
            logger.warning(f"DeepSeek synthesis failed: {e}")
    else:
        logger.warning("DeepSeek not configured, using fallback")

    # Fallback to simple weighted average (emergency only)
    logger.info("Using simple weighted average fallback")
    return _simple_weighted_average(strategy_results)


def _deepseek_synthesize(
    strategy_results: Dict[str, Dict],
    market_summary: str
) -> Dict:
    """Run DeepSeek-based synthesis."""
    prompt_template = _load_synthesizer_prompt()
    if not prompt_template:
        return {"success": False}

    # Format strategy outputs with compressed reasoning
    from .strategy_nodes import format_strategy_outputs_for_synthesizer
    strategy_table = format_strategy_outputs_for_synthesizer(strategy_results)

    # Build prompt
    prompt = prompt_template.replace("{strategy_outputs}", strategy_table)
    prompt = prompt.replace("{market_summary}", market_summary or "No additional market context")

    logger.info(f"[synthesizer] Sending to DeepSeek ({len(prompt)} chars)")
    logger.debug(f"[synthesizer] Full prompt:\n{prompt}")

    try:
        # Use max tokens for reasoner model (includes CoT + final answer)
        raw = deepseek.make_deepseek_request(prompt)
        logger.info(f"[synthesizer] DeepSeek response ({len(raw)} chars): {raw[:300]}...")
        return _parse_synthesizer_output(raw)
    except Exception as e:
        logger.error(f"Synthesizer DeepSeek error: {e}")
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


def _simple_weighted_average(strategy_results: Dict[str, Dict]) -> Dict:
    """
    Emergency fallback: simple weighted average.

    No agreement bonuses, no phase adjustments — just raw average.
    """
    # Simple equal weights for active strategies
    total_slider = 0.0
    total_conf = 0.0
    active_count = 0

    for result in strategy_results.values():
        if result.get("success") and result.get("confidence", 0) > 0.1:
            total_slider += result.get("slider", 0) * result.get("confidence", 0)
            total_conf += result.get("confidence", 0)
            active_count += 1

    if total_conf > 0:
        final_slider = total_slider / total_conf
    else:
        final_slider = 0.0

    # Apply conservative dampening (since this is fallback mode)
    final_slider = final_slider * 0.5
    final_slider = max(-1.0, min(1.0, final_slider))

    avg_conf = total_conf / active_count if active_count > 0 else 0

    return {
        "final_slider": round(final_slider, 3),
        "confidence": round(avg_conf, 3),
        "regime": "unknown",
        "strategy_agreement": active_count,
        "reasoning": f"Fallback: weighted avg of {active_count} strategies",
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
