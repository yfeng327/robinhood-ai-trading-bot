"""
Event Bus - Simple pub/sub for trading events.

Allows the trading bot to publish events that the web UI can subscribe to.
Uses a thread-safe queue for events.
"""

import json
import logging
import queue
import threading
from datetime import datetime
from typing import Dict, Generator, Optional, Any

logger = logging.getLogger(__name__)

# Global event bus instance
_event_bus: Optional['EventBus'] = None
_event_bus_lock = threading.Lock()


class EventBus:
    """
    Simple event bus for publishing trading events to web UI.
    
    Events are published by the trading bot and consumed by SSE clients.
    Thread-safe for use across bot and web server threads.
    """
    
    def __init__(self, max_events: int = 100):
        """
        Initialize event bus.
        
        Args:
            max_events: Maximum events to keep in history
        """
        self._subscribers: Dict[int, queue.Queue] = {}
        self._subscriber_lock = threading.Lock()
        self._next_id = 0
        self._event_history: list = []
        self._max_events = max_events
        self._latest_status: Dict[str, Any] = {
            'mode': 'unknown',
            'running': False,
            'last_cycle': None,
            'buffered_decisions': 0,
        }
    
    def publish(self, event_type: str, data: Dict):
        """
        Publish an event to all subscribers.
        
        Args:
            event_type: Type of event (e.g., 'trade', 'cycle_complete', 'eod_review')
            data: Event data dict
        """
        event = {
            'type': event_type,
            'data': data,
            'timestamp': datetime.now().isoformat(),
        }
        
        # Add to history
        self._event_history.append(event)
        if len(self._event_history) > self._max_events:
            self._event_history = self._event_history[-self._max_events:]
        
        # Send to all subscribers
        with self._subscriber_lock:
            dead_subscribers = []
            for sub_id, sub_queue in self._subscribers.items():
                try:
                    sub_queue.put_nowait(event)
                except queue.Full:
                    dead_subscribers.append(sub_id)
            
            # Remove dead subscribers
            for sub_id in dead_subscribers:
                del self._subscribers[sub_id]
        
        logger.debug(f"Published event: {event_type}")
    
    def subscribe(self) -> tuple:
        """
        Subscribe to events.
        
        Returns:
            Tuple of (subscriber_id, queue)
        """
        with self._subscriber_lock:
            sub_id = self._next_id
            self._next_id += 1
            sub_queue = queue.Queue(maxsize=50)
            self._subscribers[sub_id] = sub_queue
            logger.debug(f"New subscriber: {sub_id}")
            return sub_id, sub_queue
    
    def unsubscribe(self, subscriber_id: int):
        """
        Unsubscribe from events.
        
        Args:
            subscriber_id: ID returned from subscribe()
        """
        with self._subscriber_lock:
            if subscriber_id in self._subscribers:
                del self._subscribers[subscriber_id]
                logger.debug(f"Removed subscriber: {subscriber_id}")
    
    def get_event_stream(self, timeout: float = 30.0) -> Generator[str, None, None]:
        """
        Get SSE event stream.
        
        Yields:
            SSE-formatted event strings
        """
        sub_id, sub_queue = self.subscribe()
        
        try:
            # First, send any recent events
            for event in self._event_history[-10:]:
                yield f"data: {json.dumps(event)}\n\n"
            
            # Then stream new events
            while True:
                try:
                    event = sub_queue.get(timeout=timeout)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    # Send keepalive
                    yield f": keepalive\n\n"
        finally:
            self.unsubscribe(sub_id)
    
    def get_history(self, count: int = 20) -> list:
        """Get recent event history."""
        return self._event_history[-count:]
    
    def update_status(self, **kwargs):
        """Update latest status."""
        self._latest_status.update(kwargs)
    
    def get_status(self) -> Dict:
        """Get latest status."""
        return self._latest_status.copy()


def get_event_bus() -> EventBus:
    """Get or create the global event bus instance."""
    global _event_bus
    
    with _event_bus_lock:
        if _event_bus is None:
            _event_bus = EventBus()
        return _event_bus


def publish_trade(symbol: str, action: str, quantity: float, result: str, details: str = ""):
    """Convenience function to publish a trade event."""
    get_event_bus().publish('trade', {
        'symbol': symbol,
        'action': action,
        'quantity': quantity,
        'result': result,
        'details': details,
    })


def publish_cycle_complete(decisions: int, sold: list, bought: list, errors: list):
    """Convenience function to publish cycle completion."""
    get_event_bus().publish('cycle_complete', {
        'decisions': decisions,
        'sold': sold,
        'bought': bought,
        'errors': errors,
    })
    get_event_bus().update_status(last_cycle=datetime.now().isoformat())


def publish_eod_review(results: Dict):
    """Convenience function to publish EOD review results."""
    get_event_bus().publish('eod_review', results)
