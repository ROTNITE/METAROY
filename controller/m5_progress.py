"""Small optional public progress sidecar; never a source of physical truth."""
import json
import math
import os
from pathlib import Path
import re
import time

SOURCES = frozenset(('metarescue', 'reference', 'interop'))
KINDS = frozenset(('reset_response', 'observation', 'decision', 'step_response',
                   'close_response', 'uncertain_step', 'controller_failure',
                   'environment_failure', 'environment_rejection'))
MAX_FILE = 8 * 1024 * 1024
MAX_LINE = 2048
TOKEN = re.compile(r'[A-Za-z0-9_.:/-]{1,128}\Z')


def _token(value):
    return value if isinstance(value, str) and TOKEN.fullmatch(value) else None


def _number(value):
    return value if type(value) in (int, float) and abs(value) <= 1e15 and math.isfinite(value) else None


def _safe_fields(item):
    result = {}
    if not isinstance(item, dict):
        return result
    for name in ('episode_id', 'request_id', 'decision_id', 'status', 'operation', 'code', 'stage'):
        value = _token(item.get(name))
        if value is not None:
            result[name] = value
    for name in ('time', 'model_time', 'planning_wall_s', 'wall_s', 'commands', 'command_results', 'events'):
        value = _number(item.get(name))
        if value is not None:
            result[name] = value
    return result


def summarize(kind, payload, source, elapsed):
    if kind not in KINDS or source not in SOURCES or not isinstance(payload, dict):
        return None
    data = payload.get('decision', {}) if kind == 'decision' else payload
    if not isinstance(data, dict):
        return None
    row = _safe_fields(data)
    if kind == 'decision':
        row.update(_safe_fields({'planning_wall_s': payload.get('planning_wall_s')}))
    for name in ('commands', 'command_results', 'events'):
        if isinstance(data.get(name), list):
            row[name] = len(data[name])
    row.update(kind=kind, source=source, wall_s=round(elapsed, 6))
    return row


class Progress:
    """Only an explicitly configured laboratory run writes this derived log.

    A bounded sidecar failure disables the display, never the mission.
    The authoritative journal remains unchanged.
    """
    def __init__(self, source, path=None):
        value = path if path is not None else os.environ.get('METARESCUE_M5_PROGRESS_FILE')
        self.path = Path(value) if value else None
        self.source = source
        self.started = time.monotonic()
        self.written = 0

    def record(self, kind, payload):
        if self.path is None:
            return
        try:
            row = summarize(kind, payload, self.source, time.monotonic() - self.started)
            if row is None:
                return
            wire = json.dumps(row, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii') + b'\n'
            if len(wire) > MAX_LINE or self.written + len(wire) > MAX_FILE:
                self.path = None
                return
            with self.path.open('ab') as stream:
                stream.write(wire)
            self.written += len(wire)
        except (OSError, ValueError, TypeError, OverflowError):
            self.path = None


def read_page(path, cursor=0, limit=50):
    """Bounded byte cursor; do not emit a concurrently written partial line."""
    if type(cursor) is not int or not 0 <= cursor <= MAX_FILE or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid progress page')
    if not path.is_file():
        return {'available': False, 'rows': [], 'cursor': cursor, 'more': False}
    rows = []
    with path.open('rb') as stream:
        size = min(path.stat().st_size, MAX_FILE)
        if cursor > size:
            raise ValueError('Progress cursor beyond file')
        if cursor:
            stream.seek(cursor - 1)
            if stream.read(1) != b'\n':
                raise ValueError('Progress cursor is not a record boundary')
        stream.seek(cursor)
        while len(rows) < limit and stream.tell() < size:
            start = stream.tell()
            line = stream.readline(min(MAX_LINE + 1, size - start))
            if len(line) > MAX_LINE:
                raise ValueError('Oversized progress row')
            if not line.endswith(b'\n'):
                stream.seek(start)
                break
            row = json.loads(line)
            if not isinstance(row, dict) or row.get('kind') not in KINDS or row.get('source') not in SOURCES:
                raise ValueError('Invalid progress row')
            rows.append({**_safe_fields(row), 'kind': row['kind'], 'source': row['source']})
        cursor = stream.tell()
    return {'available': True, 'rows': rows, 'cursor': cursor, 'more': cursor < size}
