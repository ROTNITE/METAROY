"""Measure real loopback read-only HTTP requests over a synthetic 450-run catalog.

Synthetic metadata is temporary and is not accepted as mission evidence.
Measure Python allocations separately from native memory and GPU performance.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
from pathlib import Path
import statistics
from tempfile import TemporaryDirectory
from threading import Thread
import time
import tracemalloc

from tools.m5_lab import Lab, Registry, Server


def fixture(root, count=450):
    for i in range(count):
        ident=f'{i:032x}'
        directory=root/ident
        directory.mkdir()
        (directory/'series.json').write_text(json.dumps({
            'id':ident,'created':f'{i:08d}','state':'completed',
            'spec':{'controller':'metarescue','environment':'reference','profile':'K','timeout':90},
            'rows':[{'index':0,'seed':i,'mode':'strict','state':'completed',
                     'result':{'exchange':'failed','status':'safe_stopped','error':'synthetic benchmark only'}}]
        }),encoding='utf-8')
    path=root/('0'*32)/'trial-000'
    path.mkdir()
    (path/'report.json').write_text(json.dumps({'synthetic_large_report':'x'*(6*1024*1024)}),encoding='utf-8')
    with (path/'progress.jsonl').open('wb') as stream:
        for i in range(4000):
            stream.write(json.dumps({'kind':'step_response','source':'metarescue','time':i,
                                     'status':'running','command_results':8,
                                     'request_id':f'request-{i}'}).encode()+b'\n')


def measure(server, paths):
    durations=[];sizes=[]
    for path in paths:
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            before=time.perf_counter()
            conn.request('GET',path)
            response=conn.getresponse();raw=response.read()
            durations.append((time.perf_counter()-before)*1000)
            if response.status!=200:
                raise RuntimeError(f'HTTP {response.status} for {path}')
            sizes.append(len(raw))
            payload=json.loads(raw)
            if path.startswith('/api/matrix') and len(payload['cells'])>30:
                raise RuntimeError('Matrix page exceeded its bound')
            if path.endswith('/summary') and (len(raw)>4096 or not payload['full_report_available']):
                raise RuntimeError('Trial summary exceeded its bound')
            if 'id=absent' in path and (payload['rows'] or payload['scanned']!=512 or not payload['more']):
                raise RuntimeError('Search did not respect its scan budget')
        finally:
            conn.close()
    ordered=sorted(durations)
    def percentile(p):
        return ordered[min(len(ordered)-1,int((len(ordered)-1)*p))]
    return {'requests':len(paths),'p50_ms':statistics.median(ordered),'p95_ms':percentile(.95),
            'p99_ms':percentile(.99),'max_response_bytes':max(sizes)}


def run():
    with TemporaryDirectory() as temp:
        root=Path(temp)
        fixture(root)
        before={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.suffix in ('.json','.jsonl') and p.is_file()}
        tracemalloc.start()
        started=time.perf_counter()
        lab=Lab(root,Registry())
        startup_ms=(time.perf_counter()-started)*1000
        baseline,peak=tracemalloc.get_traced_memory()
        with Server(('127.0.0.1',0),lab) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                # Warm caches once; report steady-state traffic explicitly.
                measure(server,['/api/series?limit=30','/api/matrix?limit=30'])
                tracemalloc.reset_peak()
                cpu_before=time.process_time()
                catalog=measure(server,[f'/api/series?offset={(i%15)*30}&limit=30' for i in range(150)])
                matrix=measure(server,[f'/api/matrix?offset={(i%15)*30}&limit=30&mode=strict' for i in range(150)])
                timeline=measure(server,[f"/api/series/{'0'*32}/trial/0/progress?limit=50" for _ in range(100)])
                search=measure(server,[f"/api/series/{'0'*32}/trial/0/progress?id=absent&limit=50" for _ in range(50)])
                summary=measure(server,[f"/api/series/{'0'*32}/trial/0/summary" for _ in range(100)])
                cpu_s=time.process_time()-cpu_before
                current,request_peak=tracemalloc.get_traced_memory()
            finally:
                server.shutdown();thread.join()
        tracemalloc.stop()
        after={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in root.rglob('*') if p.suffix in ('.json','.jsonl') and p.is_file()}
        if before!=after or lab.active is not None:
            raise RuntimeError('Read-only requests mutated the catalog or started a mission')
        return {'fixture':'synthetic metadata, not trial evidence','archives':450,'journal_records':4000,
                'startup_ms':startup_ms,'python_allocated_at_start_bytes':baseline,
                'python_startup_peak_bytes':peak,'python_request_peak_bytes':request_peak,
                'python_request_current_bytes':current,'process_cpu_s':cpu_s,
                'catalog':catalog,'matrix':matrix,'timeline':timeline,'id_search_no_match':search,
                'large_report_summary':summary,'read_only_verified':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
