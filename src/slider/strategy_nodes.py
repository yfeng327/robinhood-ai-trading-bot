"""
Strategy Nodes — Data-driven, pluggable strategy execution.

Each strategy is defined as an entry in STRATEGY_REGISTRY with:
  - prompt_file: the prompt template in prompts/
  - extra_context_keys: list of extra context placeholder names
  - session_gate: optional session name (strategy only runs during that session)

Adding a new strategy:
  1. Create prompts/slider_<name>.md
  2. Add an entry to STRATEGY_REGISTRY below
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


# =============================================================================
# STRATEGY REGISTRY
# =============================================================================
# To add a new strategy, add an entry here and create the prompt file.
# No other code changes needed.

STRATEGY_REGISTRY: Dict[str, Dict] = {
    "ttm_squeeze": {
        "prompt_file": "slider_ttm_squeeze.md",
        "extra_context_keys": [],
        "session_gate": None,
    },
    "orb": {
        "prompt_file": "slider_orb.md",
        "extra_context_keys": ["opening_range"],
        "session_gate": None,
    },
    "mean_reversion": {
        "prompt_file": "slider_mean_reversion.md",
        "extra_context_keys": [],
        "session_gate": None,
    },
    "gap_trading": {
        "prompt_file": "slider_gap_trading.md",
        "extra_context_keys": ["gap_info"],
        "session_gate": None,
    },
    "overnight": {
        "prompt_file": "slider_overnight.md",
        "extra_context_keys": [],
        "session_gate": "overnight",  # Only runs during overnight session
    },
    "vwap_trend": {
        "prompt_file": "slider_vwap_trend.md",
        "extra_context_keys": [],
        "session_gate": None,
    },
    "volatility_rotation": {
        "prompt_file": "slider_volatility_rotation.md",
        "extra_context_keys": [],
        "session_gate": None,
    },
}


# =============================================================================
# CORE ENGINE (no changes needed when adding strategies)
# =============================================================================

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


def _run_single_strategy(
    name: str,
    config: Dict,
    market_data: str,
    extra_context: Dict[str, str],
) -> Dict:
    """
    Run a single registered strategy.
    
    Handles session gating and extra context extraction automatically.
    """
    logger.info(f"Running {name} node...")
    
    # Session gating — return neutral if outside the required session
    session_gate = config.get("session_gate")
    if session_gate:
        from .data_feed import get_market_session
        session = get_market_session()
        if session["session_name"] != session_gate:
            logger.info(f"Not in {session_gate} session ({session['session_name']}), {name} returning neutral")
            return {
                "slider": 0.0,
                "confidence": 0.0,
                "direction": "neutral",
                "reasoning": f"{name} inactive during {session['session_name']} session",
                "success": True,
                "strategy": name,
            }
    
    # Extract only the extra context keys this strategy needs
    strategy_context = {}
    for key in config.get("extra_context_keys", []):
        if key in extra_context:
            strategy_context[key] = extra_context[key]
    
    result = _run_strategy_llm(
        config["prompt_file"],
        market_data,
        strategy_context or None,
    )
    result["strategy"] = name
    return result


# =============================================================================
# PUBLIC API
# =============================================================================

def get_registered_strategies() -> List[str]:
    """Return list of all registered strategy names."""
    return list(STRATEGY_REGISTRY.keys())


def run_strategy_nodes(
    market_data: str,
    extra_context: Dict[str, str] = None,
    active_strategies: List[str] = None,
) -> Dict[str, Dict]:
    """
    Run strategy nodes concurrently.
    
    Args:
        market_data: Formatted market data string
        extra_context: Dict of extra context values (e.g. opening_range, gap_info)
        active_strategies: List of strategy names to run. None = run all registered.
    
    Returns:
        Dict mapping strategy name to result dict
    """
    extra_context = extra_context or {}
    
    # Determine which strategies to run
    if active_strategies is not None:
        strategies = {
            name: STRATEGY_REGISTRY[name]
            for name in active_strategies
            if name in STRATEGY_REGISTRY
        }
        # Warn about unknown strategy names
        unknown = set(active_strategies) - set(STRATEGY_REGISTRY.keys())
        if unknown:
            logger.warning(f"Unknown strategies (skipped): {unknown}")
    else:
        strategies = STRATEGY_REGISTRY
    
    n = len(strategies)
    logger.info(f"Running {n} strategy node(s) concurrently: {list(strategies.keys())}")
    
    results = {}
    
    with ThreadPoolExecutor(max_workers=max(n, 1)) as executor:
        futures = {
            executor.submit(
                _run_single_strategy, name, config, market_data, extra_context
            ): name
            for name, config in strategies.items()
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
    
    succeeded = sum(1 for r in results.values() if r.get('success'))
    logger.info(f"All strategies complete. {succeeded}/{n} succeeded.")
    return results


def format_strategy_outputs_for_synthesizer(results: Dict[str, Dict]) -> str:
    """Format strategy results for the synthesizer prompt.

    Strategy prompts are configured to output ≤80 char reasoning with abbreviations.
    No truncation here — synthesizer receives full reasoning.
    """
    lines = ["| Strategy | Slider | Confidence | Direction | Reasoning |",
             "|----------|--------|------------|-----------|-----------|"]

    for name, result in results.items():
        slider = f"{result.get('slider', 0):+.2f}"
        conf = f"{result.get('confidence', 0):.0%}"
        direction = result.get('direction', 'neutral')
        reasoning = result.get('reasoning', '') or '-'
        # Escape pipe chars for markdown table
        reasoning = reasoning.replace('|', '\\|')
        lines.append(f"| {name} | {slider} | {conf} | {direction} | {reasoning} |")

    return "\n".join(lines)
