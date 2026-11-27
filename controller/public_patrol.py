"""Public and observed travel/energy estimates for the Net65 search."""
from collections import defaultdict, deque
import math

from controller.public_core import PATROL_ESTIMATES


MOTION_ESTIMATES = 'metaroy.motion_estimates.v1'
KEYS = ('speed_m_s','scan_s','move_fraction_per_m','scan_fraction','reference_distance_m')


def numbers(data, keys=KEYS):
    if not isinstance(data,dict):
        return None
    fields=tuple(data.get(key) for key in keys)
    return fields if all(type(x) in (int,float) and math.isfinite(x) and x>0 for x in fields) else None


def snapshot(agent, now):
    if not agent:
        return None
    position = agent.get('position')
    stamps = agent.get('freshness',{})
    at = stamps.get('position',{}).get('observed_at')
    resource_at = stamps.get('resources',{}).get('observed_at')
    value = agent.get('resources',{}).get('energy',{}).get('value')
    if (position is not None and type(at) in (int,float) and at==resource_at and 0<=at<=now
            and now-at<=5 and type(value) in (int,float)):
        return tuple(position),at,value
    return None


class PublicPatrolEstimates:
    def __init__(self, capabilities, public):
        self.public=public
        self.stock=numbers(capabilities.get('patrol_estimates')) if PATROL_ESTIMATES in public.extensions else None
        declared=capabilities.get('motion_estimates',{}) if MOTION_ESTIMATES in public.extensions else {}
        self.declared=declared if isinstance(declared,dict) else {}
        self.reset()

    def reset(self):
        self.pending={}
        self.moves=defaultdict(lambda:deque(maxlen=12))
        self.scans=defaultdict(lambda:deque(maxlen=12))

    def begin(self, decision, observation):
        if self.stock or not decision['commands']:
            return
        agents={a['id']:a for a in observation['agents']}
        for index,command in enumerate(decision['commands']):
            aid=command['agent_id']; agent=agents.get(aid)
            semantic=self.public.semantic_ids.get(command['action'],command['action'])
            if not agent or agent['kind']!='aerial' or semantic not in ('move','observe'):
                continue
            origin=snapshot(agent,observation['time'])
            if origin:
                key=(decision['episode_id'],decision['decision_id'],index)
                self.pending[key]=(aid,semantic,origin)
        while len(self.pending)>512:
            self.pending.pop(next(iter(self.pending)))

    def finish(self, response):
        if self.stock or not self.pending:
            return
        obs=self.public.view(response['observation'])
        agents={a['id']:a for a in obs['agents']}
        for receipt in response.get('command_results',()):
            key=(obs['episode_id'],receipt.get('decision_id'),receipt.get('command_index'))
            intent=self.pending.get(key)
            if not intent:
                continue
            state=receipt.get('execution_status')
            if state not in ('completed','cancelled','failed') and receipt.get('delivery_status') not in ('expired','lost'):
                continue
            self.pending.pop(key)
            aid,action,before=intent
            if (receipt.get('agent_id')!=aid or receipt.get('validation_status')!='accepted'
                    or receipt.get('delivery_status')!='delivered' or state!='completed'):
                continue
            after=snapshot(agents.get(aid),obs['time'])
            if not after:
                continue
            distance=math.dist(before[0],after[0])
            elapsed, energy=after[1]-before[1],before[2]-after[2]
            if elapsed<=0 or energy<=0:
                continue
            if action=='move' and distance>.25:
                self.moves[aid].append((distance/elapsed,energy/distance))
            elif action=='observe' and distance<.25:
                self.scans[aid].append((elapsed,energy))

    def values(self, agent, points):
        if self.stock:
            return self.stock,'declared_patrol_estimates'
        if values:=numbers(self.declared.get(agent['kind'])):
            return values,'public_geometry'
        aid=agent['id']; moves=self.moves.get(aid,()); scans=self.scans.get(aid,())
        if len(moves)<3 or len(scans)<3 or len(points)<2:
            return None,None
        side=min((math.dist(points[0],q) for q in points[1:] if math.dist(points[0],q)>0),default=0.)
        if not side:
            return None,None
        speed=min(x[0] for x in moves)*.85
        move_energy=max(x[1] for x in moves)*1.2
        scan_s=max(x[0] for x in scans)
        scan_energy=max(x[1] for x in scans)*1.2
        return (speed,scan_s,move_energy,scan_energy,side),'observed_public_actions'
