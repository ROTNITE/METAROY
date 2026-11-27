"""Bounded public command trace for M5; reported evidence is not evaluator truth."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

MAX_FILE = 16 * 1024 * 1024
MAX_LINE = 256 * 1024
MAX_REFERENCE_FILE = 64 * 1024 * 1024
MAX_REFERENCE_LINE = 512 * 1024
MAX_READ = 2 * 1024 * 1024
MAX_SCAN = 64
MAX_DETAILS = 64
SOURCES = ('metarescue', 'interop', 'reference')
KINDS = ('reset_response', 'observation', 'decision', 'step_response',
         'close_response', 'uncertain_step', 'controller_failure',
         'environment_failure', 'environment_rejection')
TEXT = ('episode_id', 'request_id', 'decision_id', 'agent_id', 'action',
        'status', 'operation', 'code', 'stage', 'validation_status',
        'delivery_status', 'execution_status', 'reason_code', 'event_id',
        'event_type', 'event_source', 'reported_state', 'target_id')
IDENTITIES = ('episode_id', 'request_id', 'decision_id')
DETAIL_TYPES = ('command', 'receipt', 'reported_event')


def identifier(value):
    return value if isinstance(value, str) and 0 < len(value) <= 128 and value.isprintable() else None


def fields(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in TEXT:
        text = identifier(value.get(key))
        if text is not None:
            result[key] = text
    for key in ('time', 'model_time', 'updated_at', 'wall_s'):
        number = value.get(key)
        if type(number) in (int, float) and abs(number) <= 1e15 and math.isfinite(number):
            result[key] = number
    index = value.get('command_index')
    if type(index) is int and 0 <= index < 256:
        result['command_index'] = index
    return result


def detail(kind, value):
    if not isinstance(value, dict):
        return None
    if kind == 'command':
        allowed = ('command_index', 'agent_id', 'action')
    elif kind == 'receipt':
        allowed = ('episode_id', 'decision_id', 'command_index', 'agent_id', 'action',
                   'validation_status', 'delivery_status', 'execution_status', 'status',
                   'reason_code', 'updated_at')
    elif kind == 'reported_event':
        allowed = ('episode_id', 'decision_id', 'command_index', 'event_id', 'event_type',
                   'event_source', 'status', 'reported_state', 'target_id', 'time')
    else:
        return None
    item = {key: val for key, val in fields(value).items() if key in allowed}
    return {'type': kind, **item} if item else None


def project(kind, payload, source):
    if kind not in KINDS or source not in SOURCES or not isinstance(payload, dict):
        return None
    data = payload.get('decision') if kind == 'decision' else payload
    if not isinstance(data, dict):
        return None
    header = {k:v for k,v in fields(data).items()
              if k in (*IDENTITIES, 'time', 'model_time', 'wall_s', 'status', 'operation', 'code', 'stage')}
    rows = []
    omitted = 0

    def add(typ, value):
        nonlocal omitted
        item = detail(typ, value)
        if item is not None and len(rows) < MAX_DETAILS:
            rows.append(item)
        else:
            omitted += 1

    if kind == 'decision':
        commands = data.get('commands')
        if isinstance(commands, list):
            for index, command in enumerate(commands[:MAX_DETAILS]):
                if isinstance(command, dict):
                    # Only the original array index, never a peer-provided index.
                    add('command', {'command_index': index, 'agent_id':command.get('agent_id'),
                                    'action':command.get('action')})
                else:
                    omitted += 1
            omitted += max(0, len(commands)-MAX_DETAILS)
    elif kind == 'step_response':
        receipts = data.get('command_results')
        if isinstance(receipts, list):
            for receipt in receipts[:MAX_DETAILS]:
                # A delayed ACK keeps its own decision ID; do not attach
                # an unscoped ACK to the newest decision.
                add('receipt', receipt)
            omitted += max(0, len(receipts)-MAX_DETAILS)
        events = data.get('events')
        if isinstance(events, list):
            for event in events[:MAX_DETAILS]:
                if not isinstance(event, dict):
                    omitted += 1
                    continue
                params = event.get('parameters')
                params = params if isinstance(params, dict) else {}
                targets = event.get('targets')
                target = targets[0] if isinstance(targets,list) and len(targets)==1 else None
                add('reported_event', {'event_id':event.get('id'),'event_type':event.get('type'),
                    'event_source':event.get('source'),'status':event.get('status'),'time':event.get('time'),
                    'target_id':target,'decision_id':params.get('decision_id'),
                    'command_index':params.get('command_index'),
                    'reported_state':params.get('evacuation_state')})
            omitted += max(0, len(events)-MAX_DETAILS)
    return {'kind':kind,'source':source,**header,'details':rows,'omitted':omitted}


def sanitize(row):
    """Do not trust a derived file on disk to contain only public fields."""
    if not isinstance(row,dict) or row.get('kind') not in KINDS or row.get('source') not in SOURCES:
        raise ValueError('Invalid trace record')
    values = row.get('details')
    if not isinstance(values,list) or len(values)>MAX_DETAILS:
        raise ValueError('Invalid trace details')
    details = []
    for value in values:
        item = detail(value.get('type'),value) if isinstance(value,dict) else None
        if item is None:
            raise ValueError('Invalid trace item')
        details.append(item)
    omitted = row.get('omitted',0)
    if type(omitted) is not int or not 0 <= omitted <= 1000000000:
        raise ValueError('Invalid omitted count')
    header = {k:v for k,v in fields(row).items()
              if k in (*IDENTITIES,'time','model_time','wall_s','status','operation','code','stage')}
    return {'kind':row['kind'],'source':row['source'],**header,'details':details,'omitted':omitted}


def matching(row, query):
    if query is None or any(row.get(k)==query for k in ('episode_id','request_id')):
        return row
    if row['kind']=='decision' and row.get('decision_id')==query:
        return row
    details = [item for item in row['details'] if item.get('decision_id')==query]
    if details or row.get('decision_id')==query:
        return {**row,'details':details}
    return None


class Trace:
    def __init__(self, source, path=None):
        value = path if path is not None else os.environ.get('METARESCUE_M5_TRACE_FILE')
        self.path = Path(value) if value else None
        self.source = source

    def record(self, kind, payload):
        if self.path is None:
            return
        try:
            row = project(kind,payload,self.source)
            if row is None:
                return
            wire = json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')+b'\n'
            marker = b'{"trace_limit":true}\n'
            with self.path.open('ab') as stream:
                size = os.fstat(stream.fileno()).st_size
                if len(wire)>MAX_LINE or size+len(wire)+len(marker)>MAX_FILE:
                    if size+len(marker)<=MAX_FILE:
                        stream.write(marker)
                    self.path = None
                else:
                    stream.write(wire)
        except (OSError,ValueError,TypeError,OverflowError,RecursionError):
            # The authoritative mission must not depend on diagnostic I/O.
            self.path = None


def read_trace(path, cursor=0, limit=10, *, query=None, reference=False):
    """Page by complete source lines; for reference each line may yield two groups."""
    file_limit = MAX_REFERENCE_FILE if reference else MAX_FILE
    line_limit = MAX_REFERENCE_LINE if reference else MAX_LINE
    if (type(cursor) is not int or not 0<=cursor<=file_limit or
            type(limit) is not int or not 1<=limit<=10 or
            query is not None and identifier(query) is None):
        raise ValueError('Invalid trace page')
    result={'available':False,'rows':[],'cursor':cursor,'more':False,'scanned':0,'capped':False}
    if not path.is_file():
        return result
    result['available']=True
    with path.open('rb') as stream:
        actual=os.fstat(stream.fileno()).st_size
        size=min(actual,file_limit)
        result['capped']=actual>size
        if cursor>size:
            raise ValueError('Trace cursor beyond file')
        if cursor:
            stream.seek(cursor-1)
            if stream.read(1)!=b'\n':
                raise ValueError('Trace cursor is not a record boundary')
        stream.seek(cursor)
        matched_lines=0
        while stream.tell()<size and result['scanned']<MAX_SCAN and matched_lines<limit:
            start=stream.tell()
            line=stream.readline(min(line_limit+1,size-start))
            if len(line)>line_limit:
                raise ValueError('Oversized trace row; use the full archive')
            if not line.endswith(b'\n') or stream.tell()-cursor>MAX_READ:
                stream.seek(start)
                break
            try:
                raw=json.loads(line)
            except (ValueError,RecursionError) as error:
                raise ValueError('Invalid trace JSON') from error
            if raw=={'trace_limit':True} and not reference:
                result['capped']=True
                break
            if reference:
                if not isinstance(raw,dict) or not isinstance(raw.get('decision'),dict) or not isinstance(raw.get('response'),dict):
                    raise ValueError('Invalid reference exchange')
                groups=[project('decision',{'decision':raw['decision']},'reference'),
                        project('step_response',raw['response'],'reference')]
            else:
                groups=[sanitize(raw)]
            result['scanned']+=1
            selected=[matching(row,query) for row in groups if row is not None]
            selected=[row for row in selected if row is not None]
            if selected:
                matched_lines+=1
                result['rows'].extend(selected)
        result['cursor']=stream.tell()
        result['more']=result['cursor']<size and not (result['capped'] and result['cursor']>=size)
    return result
