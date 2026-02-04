"""
Flask Web Server - Dashboard for trading bot.

Provides:
- Dashboard HTML page
- API endpoints for trading data
- SSE endpoint for live updates
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from flask import Flask, Response, jsonify, render_template, request

from .event_bus import get_event_bus

logger = logging.getLogger(__name__)

# Shared state for trading bot integration
_trading_state: Dict = {
    'mode': 'unknown',
    'running': False,
    'last_cycle_time': None,
    'last_cycle_results': {},
    'eod_reviewer': None,
    'decision_buffer': None,
    'kb_reader': None,
}
_state_lock = threading.Lock()


def create_app(static_folder: str = None, template_folder: str = None) -> Flask:
    """
    Create and configure the Flask application.
    
    Args:
        static_folder: Path to static files
        template_folder: Path to templates
        
    Returns:
        Configured Flask app
    """
    # Default paths relative to this file
    base_dir = Path(__file__).parent
    if static_folder is None:
        static_folder = str(base_dir / "static")
    if template_folder is None:
        template_folder = str(base_dir / "templates")
    
    app = Flask(
        __name__,
        static_folder=static_folder,
        template_folder=template_folder,
    )
    
    # Register routes
    register_routes(app)
    
    return app


def register_routes(app: Flask):
    """Register all routes on the Flask app."""
    
    @app.route('/')
    def dashboard():
        """Serve the main dashboard page."""
        return render_template('dashboard.html')
    
    @app.route('/api/status')
    def api_status():
        """Get current trading status."""
        with _state_lock:
            status = {
                'mode': _trading_state.get('mode', 'unknown'),
                'running': _trading_state.get('running', False),
                'last_cycle_time': _trading_state.get('last_cycle_time'),
                'buffered_decisions': 0,
            }
            
            # Get buffered decision count
            buffer = _trading_state.get('decision_buffer')
            if buffer:
                status['buffered_decisions'] = buffer.get_decision_count()
        
        # Merge with event bus status
        event_status = get_event_bus().get_status()
        status.update(event_status)
        
        return jsonify(status)
    
    @app.route('/api/decisions')
    def api_decisions():
        """Get buffered decisions awaiting EOD review."""
        with _state_lock:
            buffer = _trading_state.get('decision_buffer')
            if not buffer:
                return jsonify({'decisions': [], 'count': 0})
            
            data = buffer.get_decisions_for_eod()
            return jsonify({
                'decisions': data.get('decisions', [])[:50],  # Limit to 50
                'count': len(data.get('decisions', [])),
                'date': data.get('date'),
                'start_value': data.get('start_of_day_value'),
            })
    
    @app.route('/api/lessons')
    def api_lessons():
        """Get lessons learned from KB."""
        with _state_lock:
            kb_reader = _trading_state.get('kb_reader')
            if not kb_reader:
                return jsonify({
                    'what_works': [],
                    'what_doesnt': [],
                    'total': 0,
                })
        
        lessons = _load_lessons(kb_reader)
        return jsonify(lessons)
    
    @app.route('/api/history')
    def api_history():
        """Get recent trading event history."""
        count = request.args.get('count', 20, type=int)
        events = get_event_bus().get_history(count)
        return jsonify({'events': events})
    
    @app.route('/api/eod-review', methods=['POST'])
    def api_trigger_eod():
        """Trigger EOD review manually."""
        with _state_lock:
            reviewer = _trading_state.get('eod_reviewer')
            if not reviewer:
                return jsonify({
                    'success': False,
                    'error': 'EOD reviewer not initialized',
                }), 500
        
        try:
            logger.info("Manual EOD review triggered from UI")
            results = reviewer.run()
            
            # Publish to event bus
            get_event_bus().publish('eod_review', results)
            
            return jsonify({
                'success': True,
                'results': results,
            })
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"EOD review failed: {e}\n{tb}")
            return jsonify({
                'success': False,
                'error': str(e),
                'traceback': tb,
            }), 500
    
    @app.route('/api/stream')
    def api_stream():
        """SSE endpoint for live updates."""
        def generate():
            for event in get_event_bus().get_event_stream():
                yield event
        
        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            }
        )


def _load_lessons(kb_reader) -> Dict:
    """Load lessons from KB files."""
    what_works = []
    what_doesnt = []
    
    try:
        lessons_path = kb_reader.kb_root / "lessons_learned.md"
        if lessons_path.exists():
            content = lessons_path.read_text()
            
            # Parse "What Works" section
            works_start = content.find("### What Works")
            doesnt_start = content.find("### What Doesn't Work")
            
            if works_start >= 0 and doesnt_start >= 0:
                works_section = content[works_start:doesnt_start]
                for line in works_section.split('\n'):
                    if line.strip().startswith('-'):
                        what_works.append(line.strip()[2:])
            
            if doesnt_start >= 0:
                doesnt_section = content[doesnt_start:]
                next_section = doesnt_section.find('\n###', 10)
                if next_section > 0:
                    doesnt_section = doesnt_section[:next_section]
                for line in doesnt_section.split('\n'):
                    if line.strip().startswith('-'):
                        what_doesnt.append(line.strip()[2:])
        
        # Also check master_index.md for recent lessons
        master_path = kb_reader.kb_root / "master_index.md"
        if master_path.exists() and (not what_works and not what_doesnt):
            content = master_path.read_text()
            lessons_start = content.find("## Recent Lessons")
            if lessons_start >= 0:
                lessons_end = content.find("##", lessons_start + 10)
                if lessons_end < 0:
                    lessons_end = len(content)
                
                lessons_section = content[lessons_start:lessons_end]
                for line in lessons_section.split('\n'):
                    if line.strip().startswith('-'):
                        lesson = line.strip()[2:]
                        if '[Q1]' in lesson or '[Q3]' in lesson:
                            what_works.append(lesson)
                        elif '[Q2]' in lesson or '[Q4]' in lesson:
                            what_doesnt.append(lesson)
                        else:
                            what_works.append(lesson)  # Default to works
    
    except Exception as e:
        logger.error(f"Error loading lessons: {e}")
    
    return {
        'what_works': what_works[:20],  # Limit to 20 each
        'what_doesnt': what_doesnt[:20],
        'total': len(what_works) + len(what_doesnt),
    }


def set_trading_state(**kwargs):
    """Set trading state from the main bot."""
    with _state_lock:
        _trading_state.update(kwargs)


def get_trading_state() -> Dict:
    """Get current trading state."""
    with _state_lock:
        return _trading_state.copy()


def run_server(
    host: str = '0.0.0.0',
    port: int = 5000,
    debug: bool = False,
    threaded: bool = True,
):
    """
    Run the Flask server.
    
    Args:
        host: Host to bind to
        port: Port to bind to
        debug: Enable debug mode
        threaded: Enable threaded mode
    """
    app = create_app()
    logger.info(f"Starting web dashboard at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=threaded)


def start_server_thread(
    host: str = '0.0.0.0',
    port: int = 5000,
) -> threading.Thread:
    """
    Start the server in a background thread.
    
    Args:
        host: Host to bind to
        port: Port to bind to
        
    Returns:
        The server thread
    """
    def run():
        # Suppress Flask's default logging in production
        import logging as stdlib_logging
        stdlib_logging.getLogger('werkzeug').setLevel(stdlib_logging.WARNING)
        
        app = create_app()
        app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
    
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    logger.info(f"Web dashboard started at http://localhost:{port}")
    return thread
