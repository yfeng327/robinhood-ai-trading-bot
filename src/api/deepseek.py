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


def make_deepseek_request(prompt: str, max_tokens: int = 2048) -> str:
    """
    Make a request to DeepSeek API.

    Args:
        prompt: The prompt to send
        max_tokens: Maximum tokens in response

    Returns:
        Response text from DeepSeek
    """
    client = _get_client()

    logger.info(f"[DeepSeek] Request to {DEEPSEEK_MODEL_NAME}, prompt length: {len(prompt)} chars")
    logger.debug(f"[DeepSeek] Full prompt:\n{prompt}")

    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.7,
        stream=False
    )

    result = response.choices[0].message.content.strip()

    logger.info(f"[DeepSeek] Response length: {len(result)} chars")
    logger.debug(f"[DeepSeek] Full response:\n{result}")

    return result


def is_deepseek_configured() -> bool:
    """Check if DeepSeek API key is configured."""
    return bool(DEEPSEEK_API_KEY)
