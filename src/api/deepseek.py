"""
DeepSeek API client for slider trading strategy node.

Uses OpenAI SDK with DeepSeek's base URL.
Runs independently of the main AI provider config.
"""

import logging
from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_NAME

logger = logging.getLogger(__name__)

# Initialize DeepSeek client (OpenAI-compatible)
_client = None


def _get_client() -> OpenAI:
    """Get or create DeepSeek client."""
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY not configured in config.py")
        _client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com"
        )
    return _client


def make_deepseek_request(prompt: str, max_tokens: int = 64000) -> str:
    """
    Make a request to DeepSeek API.

    Supports both deepseek-chat and deepseek-reasoner models.
    For reasoner: reasoning_content has CoT, content has final answer.

    IMPORTANT: For deepseek-reasoner, max_tokens includes BOTH the
    chain-of-thought reasoning AND the final answer. Default is 64000
    (the API maximum) to ensure room for complete output.

    Args:
        prompt: The prompt to send
        max_tokens: Maximum tokens for TOTAL output (CoT + answer). Default 64000.

    Returns:
        Response text from DeepSeek (the final answer/content)
    """
    client = _get_client()

    logger.info(f"[DeepSeek] Request to {DEEPSEEK_MODEL_NAME}, prompt length: {len(prompt)} chars, max_tokens: {max_tokens}")
    logger.debug(f"[DeepSeek] Full prompt:\n{prompt}")

    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        stream=False
        # Note: temperature has no effect in thinking/reasoner mode
    )

    message = response.choices[0].message

    # DeepSeek-Reasoner: reasoning_content = CoT, content = final answer
    # DeepSeek-Chat: only content (reasoning_content is None)
    reasoning_content = getattr(message, 'reasoning_content', None)
    content = message.content

    if reasoning_content:
        logger.info(f"[DeepSeek] Reasoning length: {len(reasoning_content)} chars")
        logger.debug(f"[DeepSeek] Reasoning preview: {reasoning_content[:500]}...")

    # The content field should have the final answer
    if content:
        result = content.strip()
        logger.info(f"[DeepSeek] Final answer length: {len(result)} chars")
        logger.debug(f"[DeepSeek] Final answer:\n{result}")
    else:
        # If content is empty but we have reasoning, try to extract JSON from reasoning
        logger.warning("[DeepSeek] Content field is empty, attempting to extract from reasoning")
        result = ""
        if reasoning_content:
            import re
            # Look for JSON in the reasoning
            json_match = re.search(r'```json\s*([\s\S]*?)\s*```', reasoning_content)
            if json_match:
                result = json_match.group(1).strip()
                logger.info(f"[DeepSeek] Extracted JSON from reasoning: {len(result)} chars")
            else:
                # Try to find raw JSON object with final_slider
                json_match = re.search(r'\{[^{}]*"final_slider"[^{}]*\}', reasoning_content)
                if json_match:
                    result = json_match.group(0)
                    logger.info(f"[DeepSeek] Extracted raw JSON from reasoning: {len(result)} chars")

    return result


def is_deepseek_configured() -> bool:
    """Check if DeepSeek API key is configured."""
    return bool(DEEPSEEK_API_KEY)
