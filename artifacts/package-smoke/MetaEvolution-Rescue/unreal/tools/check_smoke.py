"""Assert physical outcomes from the actual engine run, never fixture data."""
import json
import math
from pathlib import Path

SAVED=Path(__file__).resolve().parents[1]/'MetaRescue'/'Saved'
request=json.loads((SAVED/'Automation/smoke-run.json').read_text(encoding='utf-8'))
started=request['started_at']
for file in ['Telemetry/latest.json','Telemetry/events.jsonl','Screenshots/physics-smoke.png']:
    assert (SAVED/file).stat().st_mtime>=started,'stale evidence: '+file
state=json.loads((SAVED/'Telemetry/latest.json').read_text(encoding='utf-8-sig'))
events=[json.loads(s) for s in (SAVED/'Telemetry/events.jsonl').read_text(encoding='utf-8-sig').splitlines() if s]
assert state['time_s']>=26,state['time_s']
required_seconds=request.get('requested_seconds',90)
assert state['time_s']>=required_seconds-.1,'run ended early'
assert state['collapse_released']
assert state['contacts']>0,'no rigid-body contacts recorded'
assert len(state['physics_bodies'])==23
assert all(b['simulating'] for b in state['physics_bodies'])
assert any(b['initial_z_cm']-b['z_cm']>300 for b in state['physics_bodies'][-5:]),'collapse did not fall'
assert all(b['z_cm']>-100 for b in state['physics_bodies']),'debris fell through ground'
assert state['discovered']>0,'no actual sensor detection'
assert len(state['agents'])==7
for a in state['agents']:
    assert a['simulating']
    assert a['energy']>=0
    assert all(math.isfinite(a[k]) for k in ['x_cm','y_cm','z_cm','distance_cm'])
    if not a['aerial']:assert -20<a['z_cm']<500,'ground agent left the surface'
assert any(a['distance_cm']>1000 for a in state['agents'] if a['aerial']),'no drone movement'
assert any(a['distance_cm']>1000 for a in state['agents'] if not a['aerial']),'no ground movement'
assert any(e['type']=='demo_collapse_released' for e in events)
assert (SAVED/'Screenshots/physics-smoke.png').is_file(),'no real render capture'
if required_seconds>=90:
    assert state['delivered']>=1,'no completed physical rescue in the long smoke'
print(json.dumps({'passed':True,'time_s':state['time_s'],'contacts':state['contacts'],
                  'discovered':state['discovered'],'delivered':state['delivered'],
                  'physics_bodies':len(state['physics_bodies']),'agents':len(state['agents'])},ensure_ascii=False))
