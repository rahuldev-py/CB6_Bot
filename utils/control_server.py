# utils/control_server.py
# Lightweight REST control plane — stdlib only, no Flask/FastAPI dependency.
# Supplements the Telegram bot when the network is unreachable.
#
# Endpoints (all on localhost:7373 by default):
#   GET  /status          — engine health + daily P&L + open positions
#   POST /emergency_stop  — write EMERGENCY_STOP.flag (same as /estop Telegram command)
#   POST /resume          — remove EMERGENCY_STOP.flag
#   GET  /positions       — list all open trades from state
#   GET  /audit           — last 50 lines of today's audit log
#
# Security: binds to 127.0.0.1 only.  Remote access via SSH tunnel if needed.

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Optional

from utils.logger import logger

_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ESTOP_FLAG = os.path.join(_ROOT, 'EMERGENCY_STOP.flag')
_AUDIT_DIR  = os.path.join(_ROOT, 'data', 'audit')

# Injected by the owner engine so /status + /positions are live
_state_loader: Optional[Callable] = None


def set_state_loader(fn: Callable):
    """Wire in a callable that returns the current state dict."""
    global _state_loader
    _state_loader = fn


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.debug(f"[ControlServer] {fmt % args}")

    def _send(self, code: int, body: dict):
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/status':
            state = _state_loader() if _state_loader else {}
            self._send(200, {
                'ts'            : datetime.now(timezone.utc).isoformat(),
                'emergency_stop': os.path.exists(_ESTOP_FLAG),
                'daily_pnl'     : state.get('daily_pnl', 0),
                'floating_pnl'  : state.get('floating_pnl', 0),
                'live_equity'   : state.get('live_equity', 0),
                'open_trades'   : len(state.get('open_trades', [])),
                'capital'       : state.get('capital', 0),
                'phase'         : state.get('current_phase', 'unknown'),
            })

        elif self.path == '/positions':
            state = _state_loader() if _state_loader else {}
            self._send(200, {'open_trades': state.get('open_trades', [])})

        elif self.path == '/audit':
            lines = []
            try:
                date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                path = os.path.join(_AUDIT_DIR, f'orders_{date_str}.jsonl')
                if os.path.exists(path):
                    with open(path, encoding='utf-8') as f:
                        lines = f.readlines()[-50:]
            except Exception:
                pass
            self._send(200, {'lines': [json.loads(l) for l in lines if l.strip()]})

        else:
            self._send(404, {'error': 'not found'})

    def do_POST(self):
        if self.path == '/emergency_stop':
            try:
                with open(_ESTOP_FLAG, 'w') as f:
                    f.write(f"REST stop {datetime.now(timezone.utc).isoformat()}")
                logger.warning("[ControlServer] EMERGENCY_STOP flag written via REST")
                self._send(200, {'ok': True, 'message': 'EMERGENCY_STOP flag set'})
            except Exception as e:
                self._send(500, {'ok': False, 'error': str(e)})

        elif self.path == '/resume':
            try:
                if os.path.exists(_ESTOP_FLAG):
                    os.remove(_ESTOP_FLAG)
                logger.info("[ControlServer] EMERGENCY_STOP flag cleared via REST")
                self._send(200, {'ok': True, 'message': 'Resume — emergency stop cleared'})
            except Exception as e:
                self._send(500, {'ok': False, 'error': str(e)})

        else:
            self._send(404, {'error': 'not found'})


def start(port: int = 7373):
    """Start the control server in a daemon thread. Call once at engine startup."""
    def _run():
        try:
            srv = HTTPServer(('127.0.0.1', port), _Handler)
            logger.info(f"CB6 control server listening on http://127.0.0.1:{port}")
            srv.serve_forever()
        except Exception as e:
            logger.error(f"CB6 control server failed to start: {e}")

    t = threading.Thread(target=_run, daemon=True, name="ControlServer")
    t.start()
