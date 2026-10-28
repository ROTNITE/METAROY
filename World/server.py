"""Минимальный HTTP API + статический HTML, без внешних Python-зависимостей."""
from __future__ import annotations
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

WEB_DIR = Path(__file__).resolve().parent / 'web'


def make_handler(app):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(WEB_DIR), **kwargs)

        def _json(self, obj, status=200):
            payload = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if urlparse(self.path).path == '/api/state':
                return self._json(app.state())
            return super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ('/api/step', '/api/reset'):
                return self._json({'error': 'Unknown endpoint'}, status=404)
            try:
                content_length = int(self.headers.get('Content-Length', '0'))
                if content_length > 8192:
                    return self._json({'error': 'Too large request'}, status=413)
                data = json.loads(self.rfile.read(content_length) or b'{}')
                if not isinstance(data, dict):
                    raise ValueError('JSON body must be an object')
                result = app.step() if path == '/api/step' else app.reset(data.get('seed'))
                return self._json(result)
            except (ValueError, TypeError) as exc:
                return self._json({'error': str(exc)}, status=400)

        def log_message(self, fmt, *args):
            # Не засоряем консоль запросами кадров и тиков.
            if '/api/' not in (args[0] if args else ''):
                super().log_message(fmt, *args)
    return Handler


def run_server(app, port=8000, host='127.0.0.1'):
    with ThreadingHTTPServer((host, port), make_handler(app)) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print('\nСервер остановлен.')
