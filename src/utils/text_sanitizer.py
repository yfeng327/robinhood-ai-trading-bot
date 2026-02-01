"""
Text Sanitizer - removes emojis and non-ASCII characters from text.

Used at LLM boundaries to ensure output is compatible with all system locales (e.g., GBK).
"""

import re


# Comprehensive emoji to ASCII replacement mapping
EMOJI_REPLACEMENTS = {
    # Checkmarks and X marks
    '\u2705': '[OK]',       # white heavy check mark
    '\u2611': '[OK]',       # ballot box with check
    '\u274c': '[FAIL]',     # cross mark
    '\u274e': '[FAIL]',     # cross mark outline
    '\u2714': '[Y]',        # heavy check mark
    '\u2714\ufe0f': '[Y]',  # heavy check mark with variation selector
    '\u2718': '[N]',        # heavy ballot X
    '\u2713': '[Y]',        # check mark
    '\u2717': '[N]',        # ballot X
    '\u2716': '[N]',        # heavy multiplication X
    '\u2716\ufe0f': '[N]',  # heavy multiplication X with variation selector
    '\u2612': '[N]',        # ballot box with X

    # Warning, info, and status
    '\u26a0': '[!]',        # warning sign
    '\u26a0\ufe0f': '[!]',  # warning sign with variation selector
    '\u2757': '[!]',        # heavy exclamation mark
    '\u2755': '[!]',        # white exclamation mark
    '\u203c': '[!!]',       # double exclamation mark
    '\u203c\ufe0f': '[!!]', # double exclamation mark with variation selector
    '\u2753': '[?]',        # question mark ornament
    '\u2754': '[?]',        # white question mark
    '\u2049': '[!?]',       # exclamation question mark
    '\u2049\ufe0f': '[!?]', # exclamation question mark with variation selector
    '\u26d4': '[STOP]',     # no entry
    '\u2b55': '[O]',        # hollow red circle
    '\U0001F6AB': '[NO]',   # prohibited
    '\U0001F7E2': '[OK]',   # green circle
    '\U0001F534': '[!]',    # red circle
    '\U0001F7E1': '[!]',    # yellow circle

    # Dice/luck/gambling
    '\U0001F3B2': '[LUCK]',   # game die
    '\U0001F3B0': '[LUCK]',   # slot machine
    '\U0001F0CF': '[LUCK]',   # joker card

    # Muscle/strength/power
    '\U0001F4AA': '[STRONG]', # flexed biceps
    '\U0001F4A5': '[IMPACT]', # collision/explosion
    '\U0001F525': '[HOT]',    # fire
    '\u26a1': '[POWER]',      # high voltage
    '\u26a1\ufe0f': '[POWER]',# high voltage with variation selector

    # Books/learning/info
    '\U0001F4DA': '[LEARN]',  # books
    '\U0001F4D6': '[LEARN]',  # open book
    '\U0001F4D3': '[LEARN]',  # notebook
    '\U0001F4D4': '[LEARN]',  # notebook with decorative cover
    '\U0001F4D5': '[LEARN]',  # closed book
    '\U0001F4D7': '[LEARN]',  # green book
    '\U0001F4D8': '[LEARN]',  # blue book
    '\U0001F4D9': '[LEARN]',  # orange book
    '\U0001F4DD': '[NOTE]',   # memo
    '\U0001F4CB': '[NOTE]',   # clipboard
    '\U0001F4C4': '[DOC]',    # page facing up
    '\U0001F4C3': '[DOC]',    # page with curl

    # Charts/graphs/data
    '\U0001F4CA': '[CHART]',  # bar chart
    '\U0001F4C8': '[UP]',     # chart increasing
    '\U0001F4C9': '[DOWN]',   # chart decreasing
    '\U0001F4B9': '[CHART]',  # chart with yen
    '\U0001F4B0': '[$]',      # money bag
    '\U0001F4B5': '[$]',      # dollar banknote
    '\U0001F4B8': '[$]',      # money with wings

    # Target/goal
    '\U0001F3AF': '[HIT]',    # direct hit/bullseye
    '\U0001F945': '[GOAL]',   # goal net

    # Stars and ratings
    '\u2B50': '[*]',          # star
    '\u2B50\ufe0f': '[*]',    # star with variation selector
    '\U0001F31F': '[*]',      # glowing star
    '\u2728': '[*]',          # sparkles
    '\U0001F4AB': '[*]',      # dizzy (stars)
    '\U0001F320': '[*]',      # shooting star

    # Thumbs and hands
    '\U0001F44D': '[+1]',     # thumbs up
    '\U0001F44D\ufe0f': '[+1]',# thumbs up with variation selector
    '\U0001F44E': '[-1]',     # thumbs down
    '\U0001F44E\ufe0f': '[-1]',# thumbs down with variation selector
    '\U0001F44F': '[CLAP]',   # clapping hands
    '\U0001F64C': '[YAY]',    # raising hands
    '\U0001F91D': '[DEAL]',   # handshake
    '\u270B': '[STOP]',       # raised hand
    '\u261D': '[1]',          # index pointing up
    '\u261D\ufe0f': '[1]',    # index pointing up with variation selector

    # Arrows
    '\u2192': '->',           # rightwards arrow
    '\u2190': '<-',           # leftwards arrow
    '\u2194': '<->',          # left right arrow
    '\u2191': '^',            # upwards arrow
    '\u2193': 'v',            # downwards arrow
    '\u21d2': '=>',           # rightwards double arrow
    '\u21d0': '<=',           # leftwards double arrow
    '\u27a1': '->',           # black rightwards arrow
    '\u27a1\ufe0f': '->',     # black rightwards arrow with variation selector
    '\u2b05': '<-',           # leftwards black arrow
    '\u2b05\ufe0f': '<-',     # leftwards black arrow with variation selector
    '\u2b06': '^',            # upwards black arrow
    '\u2b06\ufe0f': '^',      # upwards black arrow with variation selector
    '\u2b07': 'v',            # downwards black arrow
    '\u2b07\ufe0f': 'v',      # downwards black arrow with variation selector

    # Faces/emotions (common in LLM output)
    '\U0001F600': ':)',       # grinning face
    '\U0001F603': ':D',       # grinning face with big eyes
    '\U0001F604': ':D',       # grinning face with smiling eyes
    '\U0001F601': ':D',       # beaming face
    '\U0001F642': ':)',       # slightly smiling face
    '\U0001F609': ';)',       # winking face
    '\U0001F60A': ':)',       # smiling face with smiling eyes
    '\U0001F60E': 'B)',       # smiling face with sunglasses
    '\U0001F914': '[?]',      # thinking face
    '\U0001F928': '[?]',      # face with raised eyebrow
    '\U0001F62E': ':O',       # face with open mouth
    '\U0001F62F': ':O',       # hushed face
    '\U0001F615': ':|',       # confused face
    '\U0001F61F': ':(',       # worried face
    '\U0001F641': ':(',       # slightly frowning face
    '\U0001F622': ":'(",      # crying face
    '\U0001F44C': '[OK]',     # OK hand

    # Misc symbols
    '\u2022': '*',            # bullet
    '\u25cf': '*',            # black circle (bullet)
    '\u25cb': 'o',            # white circle
    '\u25a0': '[#]',          # black square
    '\u25a1': '[ ]',          # white square
    '\u25b2': '^',            # black up-pointing triangle
    '\u25bc': 'v',            # black down-pointing triangle
    '\u25c4': '<',            # black left-pointing pointer
    '\u25ba': '>',            # black right-pointing pointer
    '\U0001F510': '[LOCK]',   # locked with key
    '\U0001F512': '[LOCK]',   # locked
    '\U0001F513': '[UNLOCK]', # unlocked
    '\U0001F4A1': '[IDEA]',   # light bulb
    '\U0001F50D': '[SEARCH]', # magnifying glass left
    '\U0001F50E': '[SEARCH]', # magnifying glass right
    '\U0001F527': '[TOOL]',   # wrench
    '\U0001F528': '[TOOL]',   # hammer
    '\U0001F6E0': '[TOOL]',   # hammer and wrench
    '\U0001F6E0\ufe0f': '[TOOL]', # hammer and wrench with variation selector
    '\u2699': '[GEAR]',       # gear
    '\u2699\ufe0f': '[GEAR]', # gear with variation selector
    '\U0001F504': '[SYNC]',   # counterclockwise arrows
    '\U0001F503': '[SYNC]',   # clockwise vertical arrows
    '\U0001F501': '[REPEAT]', # repeat button
    '\U0001F502': '[REPEAT]', # repeat single button
}


# Precompile the comprehensive emoji removal pattern
# This catches virtually all emoji ranges in Unicode
# Note: Excludes box-drawing (U+2500-U+257F) to preserve ASCII tables
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
    "\U0001F680-\U0001F6FF"  # Transport and Map
    "\U0001F700-\U0001F77F"  # Alchemical Symbols
    "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
    "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"  # Chess Symbols
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\U0001F1E0-\U0001F1FF"  # Flags
    "\U00002300-\U000023FF"  # Misc Technical
    "\U00002600-\U000026FF"  # Misc Symbols (includes warning, check marks, etc.)
    "\U00002700-\U000027BF"  # Dingbats
    "\U0000FE00-\U0000FE0F"  # Variation Selectors
    "\U0001F000-\U0001F02F"  # Mahjong Tiles
    "\U0001F0A0-\U0001F0FF"  # Playing Cards
    "\U000024C2-\U000024FF"  # Enclosed alphanumerics (circled letters)
    "\U00002580-\U000025FF"  # Block elements and geometric shapes
    "\U00003000-\U0000303F"  # CJK Symbols
    "\U0000200D"             # Zero Width Joiner (used in compound emojis)
    "]+",
    flags=re.UNICODE
)


def strip_emojis(text: str) -> str:
    """
    Remove all emojis from text, replacing known ones with ASCII equivalents.

    This function:
    1. First replaces known emojis with meaningful ASCII equivalents
    2. Then removes any remaining emojis that weren't in the mapping

    Args:
        text: Input text that may contain emojis

    Returns:
        Text with emojis removed or replaced with ASCII equivalents
    """
    if not text:
        return text

    # First, replace known emojis with ASCII equivalents
    for emoji, replacement in EMOJI_REPLACEMENTS.items():
        text = text.replace(emoji, replacement)

    # Then remove any remaining emojis using the comprehensive regex
    text = _EMOJI_PATTERN.sub('', text)

    # Clean up any double spaces that may have resulted from removal
    text = re.sub(r'  +', ' ', text)

    return text


def sanitize_for_file(text: str) -> str:
    """
    Sanitize text for safe file writing on any locale.

    Removes emojis and ensures text is ASCII-compatible where possible,
    while preserving other Unicode characters that are widely supported.

    Args:
        text: Input text

    Returns:
        Sanitized text safe for file operations
    """
    return strip_emojis(text)


def sanitize_llm_output(text: str) -> str:
    """
    Sanitize LLM output before using it in the application.

    This is the main guard function to be called at LLM boundaries.
    Call this on any text received from an LLM before storing or displaying.

    Args:
        text: Raw LLM output text

    Returns:
        Sanitized text with emojis removed/replaced
    """
    return strip_emojis(text)


def is_safe_for_locale(text: str) -> bool:
    """
    Check if text is safe for the current system locale (no emojis).

    Args:
        text: Text to check

    Returns:
        True if text contains no emojis, False otherwise
    """
    if not text:
        return True
    return _EMOJI_PATTERN.search(text) is None
