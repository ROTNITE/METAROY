"""Loopback HTTP entry point for the planar Core trial environment."""
from argparse import ArgumentParser
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
import uuid

from contracts.protocol import ID, MAX_BODY_BYTES, ProtocolError, VERSION, encode, object_schema, strict_json, validate
from environment.world import CASES, World, capabilities


PREFIX = '/v1/environment/'
RESET = object_schema({'protocol_version': {'const': VERSION}, 'request_id': ID,
                       'seed': {'type': 'integer', 'minimum': 0, 'maximum': 0xFFFFFFFF},
                       'mission': object_schema({'id': {'const': 'search_and_rescue'}, 'parameters': {'type': 'object'}})})
CLOSE = object_schema({'protocol_version': {'const': VERSION}, 'request_id': ID, 'episode_id': ID})


class PlanarServer(HTTPServer):
    def __init__(self, port, *, estimates=True):
        self.declarations = capabilities(estimates=estimates)
        self.episode = None
        super().__init__(('127.0.0.1', port), Handler)

    def server_bind(self):
        import socket
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def log_message(self, *args):
        pass

    def do_GET(self):
        self._request()

    def do_POST(self):
        self._request()

    def _request(self):
        path = urlsplit(self.path)
        route = path.path.removeprefix(PREFIX) if path.path.startswith(PREFIX) else ''
        query = {key: values[-1] for key, values in parse_qs(path.query).items()}
        identifier = query.get('request_id', uuid.uuid4().hex)
        body = {}
        try:
            wire = b''
            if self.command == 'POST':
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= MAX_BODY_BYTES:
                    raise ProtocolError('INVALID_REQUEST', 'Expected a bounded Core request body')
                wire = self.rfile.read(length)
                if len(wire) != length:
                    raise ProtocolError('INVALID_REQUEST', 'Incomplete Core request body')
                body = strict_json(wire)
                if not isinstance(body, dict):
                    raise ProtocolError('INVALID_REQUEST', 'Body must be an object')
                if body.get('protocol_version') != VERSION:
                    raise ProtocolError('UNSUPPORTED_PROTOCOL_VERSION', 'Expected Core Protocol 1.0')
                identifier = body.get('request_id', identifier)
            validate(identifier, ID)
            answer = self._route(route, query, body, identifier, wire)
            self._send(200, answer)
        except (ProtocolError, ValueError) as error:
            episode = self.server.episode
            code = error.code if isinstance(error, ProtocolError) else 'INVALID_REQUEST'
            self._send(404 if code == 'UNKNOWN_EPISODE' else 422,
                       {'protocol_version': VERSION, 'episode_id': episode.id if episode else None,
                        'request_id': identifier if isinstance(identifier, str) else None,
                        'error': {'code': code, 'message': str(error), 'details': getattr(error, 'details', {})}})
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def _route(self, route, query, body, identifier, wire):
        if self.command == 'GET' and route == 'health':
            return {'protocol_version': VERSION, 'component': 'environment', 'status': 'ready',
                    'environment_id': self.server.declarations['environment_id'], 'capabilities_url': PREFIX + 'capabilities'}
        if self.command == 'GET' and route == 'capabilities':
            return self.server.declarations
        episode = self.server.episode
        if self.command == 'POST' and route == 'reset':
            validate(body, RESET)
            if episode and not episode.closed and episode.status == 'running':
                raise ProtocolError('INVALID_REQUEST', 'Close the active 2D episode before resetting')
            parameters = body['mission']['parameters']
            opaque = parameters.get('opaque', False)
            if type(opaque) is not bool or parameters.keys() - {'case', 'opaque'}:
                raise ProtocolError('INVALID_REQUEST', 'Unknown 2D mission parameters')
            case = parameters.get('case', CASES[0])
            episode = self.server.episode = World(body['seed'], case, opaque=opaque)
            return episode.response(identifier)
        if episode is None or query.get('episode_id', episode.id) != episode.id:
            raise ProtocolError('UNKNOWN_EPISODE', 'No matching episode')
        if self.command == 'GET' and route == 'observation':
            return episode.observation(identifier)
        if self.command == 'GET' and route == 'logs':
            cursor = int(query.get('cursor', 0))
            if not 0 <= cursor <= len(episode.journal):
                raise ProtocolError('INVALID_REQUEST', 'Invalid journal cursor')
            next_cursor = min(cursor + 50, len(episode.journal))
            return {'protocol_version': VERSION, 'episode_id': episode.id,
                    'records': episode.journal[cursor:next_cursor],
                    'next_cursor': next_cursor if next_cursor < len(episode.journal) else None}
        if self.command == 'POST' and route == 'step':
            return episode.step(body, hashlib.sha256(wire).hexdigest(), self.server.declarations)
        if self.command == 'POST' and route == 'close':
            validate(body, CLOSE)
            if body['episode_id'] != episode.id:
                raise ProtocolError('UNKNOWN_EPISODE', 'Close refers to another episode')
            episode.closed = True
            return {'protocol_version': VERSION, 'episode_id': episode.id, 'request_id': identifier,
                    'time': episode.time, 'status': episode.status, 'closed': True}
        raise ProtocolError('INVALID_REQUEST', 'Unknown environment operation')

    def _send(self, status, value):
        wire = encode(value)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(wire)))
        self.end_headers()
        self.wfile.write(wire)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8142)
    parser.add_argument('--without-patrol-estimates', action='store_true')
    args = parser.parse_args()
    with PlanarServer(args.port, estimates=not args.without_patrol_estimates) as server:
        print(f'http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
