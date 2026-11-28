"""Core 1.0 HTTP controller driven by an independent tournament orchestrator."""
from argparse import ArgumentParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from urllib.parse import urlsplit

from contracts.protocol import MAX_BODY_BYTES, OPERATIONS, ProtocolError, encode, strict_json
from controller.tournament import TournamentPolicy
from controller.public_core import CORE_MODES


class TournamentServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port=8100, host='127.0.0.1', *, core_mode='strict'):
        self.policy = TournamentPolicy(core_mode)
        super().__init__((host, port), Handler)

    def server_bind(self):
        import socket
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def log_message(self, *args):
        pass

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == '/v1/controller/health':
            self._send(200, {'protocol_version': '1.0', 'component': 'controller', 'status': 'ready',
                             'controller_id': 'metarescue_public_tournament', 'supported_operations': list(OPERATIONS)})
        elif route == '/v1/controller/logs':
            with self.server.policy.lock:
                records = list(self.server.policy.records)
            self._send(200, {'protocol_version': '1.0', 'records': records})
        else:
            self._send(404, ProtocolError('INVALID_REQUEST', 'Unknown controller route').response())

    def do_POST(self):
        if urlsplit(self.path).path != '/v1/controller/decide':
            self._send(404, ProtocolError('INVALID_REQUEST', 'Unknown controller route').response())
            return
        obs = {}
        try:
            if self.headers.get_content_type() != 'application/json':
                raise ProtocolError('INVALID_REQUEST', 'Expected application/json')
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or self.headers.get('Transfer-Encoding') is not None:
                raise ProtocolError('INVALID_REQUEST', 'Expected exactly one Content-Length and no Transfer-Encoding')
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY_BYTES:
                raise ProtocolError('INVALID_REQUEST', 'Expected a bounded observation body')
            wire = self.rfile.read(length)
            if len(wire) != length:
                raise ProtocolError('INVALID_REQUEST', 'Incomplete observation body')
            body = strict_json(wire)
            if isinstance(body, dict):
                obs = body.get('observation', body)
                obs = obs if isinstance(obs, dict) else {}
            self._send(200, self.server.policy.decide(body))
        except (ProtocolError, ValueError) as error:
            error = error if isinstance(error, ProtocolError) else ProtocolError('INVALID_REQUEST', 'Malformed observation')
            self._send(422, error.response(obs.get('request_id') if isinstance(obs.get('request_id'), str) else None,
                                           obs.get('episode_id') if isinstance(obs.get('episode_id'), str) else None))
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception:
            logging.exception('Unexpected controller failure')
            self._send(500, ProtocolError('INTERNAL_ERROR', 'Controller decision failed').response())

    def _send(self, status, value):
        data = encode(value)
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionError, TimeoutError):
            self.close_connection = True


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8100)
    parser.add_argument('--host', default='127.0.0.1', help='Bind only to a trusted tournament network')
    parser.add_argument('--core-mode', choices=CORE_MODES, default='strict')
    args = parser.parse_args()
    with TournamentServer(args.port, args.host, core_mode=args.core_mode) as server:
        print(f'http://{args.host}:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
