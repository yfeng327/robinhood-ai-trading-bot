import re
import json
import logging
from config import AI_PROVIDER
from src.utils.text_sanitizer import sanitize_llm_output

logger = logging.getLogger(__name__)

# Initialize the appropriate AI client based on provider
if AI_PROVIDER == "gemini":
    from google import genai
    from config import GEMINI_API_KEY, GEMINI_MODEL_NAME
    client = genai.Client(api_key=GEMINI_API_KEY)
    model_name = GEMINI_MODEL_NAME
elif AI_PROVIDER == "anthropic":
    from anthropic import Anthropic
    from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL_NAME
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    model_name = ANTHROPIC_MODEL_NAME
elif AI_PROVIDER == "openai":
    from openai import OpenAI
    from config import OPENAI_API_KEY, OPENAI_MODEL_NAME
    client = OpenAI(api_key=OPENAI_API_KEY)
    model_name = OPENAI_MODEL_NAME
else:
    raise ValueError(f"Unsupported AI provider: {AI_PROVIDER}. Use 'openai', 'anthropic', or 'gemini'.")


def make_ai_request(prompt):
    """Make AI request to the configured provider."""
    logger.info(f"[LLM REQUEST] Provider: {AI_PROVIDER}, Model: {model_name}, Prompt length: {len(prompt)} chars")
    # Log prompt preview at INFO level for visibility
    prompt_preview = prompt[:800] + "\n... [truncated]" if len(prompt) > 800 else prompt
    logger.info(f"[LLM REQUEST] Prompt preview:\n{prompt_preview}")
    logger.debug(f"[LLM REQUEST] Full prompt:\n{prompt}")

    if AI_PROVIDER == "gemini":
        ai_resp = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
    elif AI_PROVIDER == "anthropic":
        ai_resp = client.messages.create(
            model=model_name,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
    else:  # openai
        ai_resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}]
        )

    raw_response = get_raw_response_content(ai_resp)
    logger.info(f"[LLM RESPONSE] Length: {len(raw_response)} chars")
    logger.debug(f"[LLM RESPONSE] Full response:\n{raw_response}")

    return ai_resp


def parse_ai_response(ai_response):
    """Parse AI response from the configured provider."""
    try:
        if AI_PROVIDER == "gemini":
            ai_content = ai_response.text.strip()
        elif AI_PROVIDER == "anthropic":
            ai_content = ai_response.content[0].text.strip()
        else:  # openai
            ai_content = ai_response.choices[0].message.content.strip()

        # Sanitize LLM output - remove emojis for locale compatibility
        ai_content = sanitize_llm_output(ai_content)

        # Strip markdown code blocks if present
        ai_content = re.sub(r'```json|```', '', ai_content)
        decisions = json.loads(ai_content)
    except json.JSONDecodeError:
        raw_content = get_raw_response_content(ai_response)
        raise Exception(f"Invalid JSON response from {AI_PROVIDER}: " + raw_content)
    return decisions


def get_raw_response_content(ai_response):
    """Get raw text content from AI response for logging."""
    if AI_PROVIDER == "gemini":
        raw = ai_response.text.strip()
    elif AI_PROVIDER == "anthropic":
        raw = ai_response.content[0].text.strip()
    else:  # openai
        raw = ai_response.choices[0].message.content.strip()
    # Sanitize for safe logging/error messages
    return sanitize_llm_output(raw)
