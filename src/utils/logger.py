import logging
from datetime import datetime
from config import LOG_LEVEL

# Print log message
def log(level, msg):
    log_levels = {"DEBUG": 1, "INFO": 2, "WARNING": 3, "ERROR": 4}
    level_color_codes = {
        "DEBUG": "\033[94m",
        "INFO": "\033[92m",
        "WARNING": "\033[93m",
        "ERROR": "\033[91m"
    }
    timestamp_color_code = "\033[96m"
    reset_color_code = "\033[0m"
    if log_levels.get(level, 2) >= log_levels.get(LOG_LEVEL, 2):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        level_space = " " * (8 - len(level))
        print(f"{timestamp_color_code}[{timestamp}] {level_color_codes[level]}[{level}]{reset_color_code}{level_space}{msg}")


# Print debug log message
def debug(msg):
    log("DEBUG", msg)


# Print info log message
def info(msg):
    log("INFO", msg)


# Print warning log message
def warning(msg):
    log("WARNING", msg)


# Print error log message
def error(msg):
    log("ERROR", msg)


# ============================================================================
# Bridge: Route Python's standard logging module through the custom logger
# ============================================================================
# Modules like src.api.ai, src.day_trading.bot, src.eod_review.reviewer, and
# src.kb.lesson_generator use logging.getLogger(__name__). Without a handler
# configured on the root logger, all their output (including LLM I/O logs)
# is silently discarded. This handler bridges the two systems.

class _BridgeHandler(logging.Handler):
    """Routes standard logging records through the custom log() function."""
    def emit(self, record):
        level = record.levelname
        # Map standard logging levels to our custom levels
        if level == "CRITICAL":
            level = "ERROR"
        log(level, record.getMessage())


# Map our LOG_LEVEL string to a standard logging level
_level_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
_std_level = _level_map.get(LOG_LEVEL, logging.INFO)

# Configure the root logger so all getLogger(__name__) loggers inherit this
_bridge = _BridgeHandler()
_bridge.setLevel(_std_level)
logging.root.addHandler(_bridge)
logging.root.setLevel(_std_level)
