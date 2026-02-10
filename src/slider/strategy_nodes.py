"""
Strategy Nodes — 4 concurrent LLM strategy analyzers.

Each node:
1. Loads its prompt template from prompts/
2. Injects market data
3. Calls LLM
4. Returns slider, confidence, and reasoning
"""

import json
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from src.api import ai

logger = logging.getLogger(__name__)

# Path to prompts directory
PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Load prompt template from file."""
    path = PROMPTS_DIR / filename
    if not path.exists():
        logger.error(f"Prompt file not found: {path}")
        return ""
    return path.read_text(encoding='utf-8')


def _run_strategy_llm(
    prompt_file: str,
    market_data: str,
    extra_context: Dict[str, str] = None
) -> Dict:
    """
    Run a single strategy through LLM.
    
    Args:
        prompt_file: Prompt template filename
        market_data: Formatted market data string
        extra_context: Additional context to inject (e.g., opening_range, gap_info)
    
    Returns:
        Dict with slider, confidence, direction, reasoning
    """
    prompt_template = _load_prompt(prompt_file)
    if not prompt_template:
        return _default_output("Prompt file not found")
    
    # Inject market data
    prompt = prompt_template.replace("{market_data}", market_data)
    
    # Inject any extra context placeholders
    if extra_context:
        for key, value in extra_context.items():
            prompt = prompt.replace(f"{{{key}}}", value)
    
    # Log the full prompt being sent to LLM
    logger.info(f"[{prompt_file}] Input prompt ({len(prompt)} chars):")
    logger.debug(f"[{prompt_file}] Full prompt:\n{prompt}")
    # Also log a truncated version at INFO level for visibility
    prompt_preview = prompt[:500] + "..." if len(prompt) > 500 else prompt
    logger.info(f"[{prompt_file}] Prompt preview:\n{prompt_preview}")
    
    try:
        response = ai.make_ai_request(prompt)
        raw = ai.get_raw_response_content(response)
        logger.info(f"[{prompt_file}] LLM response ({len(raw)} chars): {raw[:200]}...")
        return _parse_strategy_output(raw)
    except Exception as e:
        logger.error(f"Strategy LLM failed ({prompt_file}): {e}")
        return _default_output(f"LLM error: {e}")


def _parse_strategy_output(raw: str) -> Dict:
    """Parse LLM JSON output into strategy result."""
    try:
        # Strip markdown code blocks if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()
        
        result = json.loads(cleaned)
        
        # Validate and normalize
        slider = float(result.get("slider", 0))
        slider = max(-1.0, min(1.0, slider))  # Clamp to [-1, 1]
        
        confidence = float(result.get("confidence", 0))
        confidence = max(0.0, min(1.0, confidence))  # Clamp to [0, 1]
        
        return {
            "slider": slider,
            "confidence": confidence,
            "direction": result.get("direction", "neutral"),
            "reasoning": result.get("reasoning", ""),
            "mode": result.get("mode"),  # For gap trading
            "success": True,
        }
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to parse strategy output: {e}")
        return _default_output(f"Parse error: {e}")


def _default_output(error_msg: str = "") -> Dict:
    """Return neutral output on error."""
    return {
        "slider": 0.0,
        "confidence": 0.0,
        "direction": "neutral",
        "reasoning": error_msg or "No signal",
        "success": False,
    }


# =============================================================================
# INDIVIDUAL STRATEGY NODES
# =============================================================================

def run_ttm_squeeze_node(market_data: str, **kwargs) -> Dict:
    """Run TTM Squeeze strategy analysis."""
    logger.info("Running TTM Squeeze node...")
    result = _run_strategy_llm("slider_ttm_squeeze.md", market_data)
    result["strategy"] = "ttm_squeeze"
    return result


def run_orb_node(market_data: str, opening_range: str = "", **kwargs) -> Dict:
    """Run Opening Range Breakout strategy analysis."""
    logger.info("Running ORB node...")
    result = _run_strategy_llm(
        "slider_orb.md",
        market_data,
        {"opening_range": opening_range}
    )
    result["strategy"] = "orb"
    return result


def run_mean_reversion_node(market_data: str, **kwargs) -> Dict:
    """Run Mean Reversion strategy analysis."""
    logger.info("Running Mean Reversion node...")
    result = _run_strategy_llm("slider_mean_reversion.md", market_data)
    result["strategy"] = "mean_reversion"
    return result


def run_gap_node(market_data: str, gap_info: str = "", **kwargs) -> Dict:
    """Run Gap Trading strategy analysis."""
    logger.info("Running Gap Trading node...")
    result = _run_strategy_llm(
        "slider_gap_trading.md",
        market_data,
        {"gap_info": gap_info}
    )
    result["strategy"] = "gap_trading"
    return result


def run_overnight_node(market_data: str, **kwargs) -> Dict:
    """
    Run Overnight strategy (Asian Range / London Breakout).
    
    Only active during overnight session (20:00-04:00 ET).
    Returns neutral during regular market hours.
    """
    from .data_feed import get_market_session
    
    logger.info("Running Overnight node...")
    
    # Check if we're in overnight session
    session = get_market_session()
    if session["session_name"] != "overnight":
        logger.info(f"Not in overnight session ({session['session_name']}), returning neutral")
        return {
            "slider": 0.0,
            "confidence": 0.0,
            "direction": "neutral",
            "reasoning": f"Overnight strategy inactive during {session['session_name']} session",
            "success": True,
            "strategy": "overnight",
        }
    
    result = _run_strategy_llm("slider_overnight.md", market_data)
    result["strategy"] = "overnight"
    return result


# =============================================================================
# CONCURRENT EXECUTION
# =============================================================================

def run_all_strategy_nodes(
    market_data: str,
    opening_range: str = "",
    gap_info: str = ""
) -> Dict[str, Dict]:
    """
    Run all 5 strategy nodes concurrently.

    Args:
        market_data: Formatted market data string
        opening_range: Formatted opening range info for ORB
        gap_info: Formatted gap info for gap trading

    Returns:
        Dict mapping strategy name to result dict
    """
    logger.info("Running all 5 strategy nodes concurrently...")

    results = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(run_ttm_squeeze_node, market_data): "ttm_squeeze",
            executor.submit(run_orb_node, market_data, opening_range): "orb",
            executor.submit(run_mean_reversion_node, market_data): "mean_reversion",
            executor.submit(run_gap_node, market_data, gap_info): "gap_trading",
            executor.submit(run_overnight_node, market_data): "overnight",
        }

        for future in as_completed(futures):
            strategy_name = futures[future]
            try:
                result = future.result(timeout=60)
                results[strategy_name] = result
                logger.info(
                    f"  {strategy_name}: slider={result['slider']:.2f}, "
                    f"confidence={result['confidence']:.2f}"
                )
            except Exception as e:
                logger.error(f"  {strategy_name} failed: {e}")
                results[strategy_name] = _default_output(str(e))
                results[strategy_name]["strategy"] = strategy_name

    logger.info(f"All strategies complete. {sum(1 for r in results.values() if r.get('success'))} succeeded.")
    return results


def format_strategy_outputs_for_synthesizer(results: Dict[str, Dict]) -> str:
    """Format strategy results for the synthesizer prompt."""
    lines = ["| Strategy | Slider | Confidence | Direction | Key Reasoning |",
             "|----------|--------|------------|-----------|---------------|"]
    
    for name, result in results.items():
        slider = f"{result.get('slider', 0):+.2f}"
        conf = f"{result.get('confidence', 0):.0%}"
        direction = result.get('direction', 'neutral')
        reasoning = result.get('reasoning', '')[:50] + "..." if len(result.get('reasoning', '')) > 50 else result.get('reasoning', '')
        lines.append(f"| {name} | {slider} | {conf} | {direction} | {reasoning} |")
    
    return "\n".join(lines)
