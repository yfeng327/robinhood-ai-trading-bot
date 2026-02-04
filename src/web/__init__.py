"""
Web Dashboard Module - Provides UI for trading bot.

Features:
- Live trading view with SSE updates
- Lessons learned viewer
- EOD review trigger button
- Decision buffer status
"""

from .server import create_app, run_server, start_server_thread, set_trading_state
from .event_bus import EventBus, get_event_bus

__all__ = [
    'create_app',
    'run_server',
    'start_server_thread',
    'set_trading_state',
    'EventBus',
    'get_event_bus',
]
