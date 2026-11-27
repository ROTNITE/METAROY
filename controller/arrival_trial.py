"""Research-only independent movement release on authoritative public arrival.
Does not synthesize ACKs or infer pickup/handover/observe completion.
"""
import math
from collections import deque
from controller.route_value import RouteValuePolicy
class ArrivalPolicy(RouteValuePolicy):
    def __init__(self,*args,arrival_mode='arrival-all',**kw):
        self.arrival_mode=arrival_mode
        super().__init__(*args,route_mode='gate-1.8',**kw)
    def _reset(self,episode):
        super()._reset(episode);self.accepted_moves={};self.arrival_released={};self.arrival_disabled=False
    def observe_response(self,response):
        request,intents=self.proposed
        for i,c in enumerate(request['commands']):
            if c['action']!='move':continue
            key=(request['decision_id'],i)
            if any(x.get('decision_id')==key[0] and x.get('command_index')==i and x.get('agent_id')==c['agent_id'] and x.get('validation_status')=='accepted' and x.get('delivery_status')=='delivered' for x in response['command_results']):
                self.accepted_moves[key]=(c['agent_id'],tuple(c['parameters']['target']))
        for item in response['command_results']:
            key=(item.get('decision_id'),item.get('command_index'))
            if item.get('execution_status') in ('completed','failed','cancelled'):
                self.accepted_moves.pop(key,None)
                if key in self.arrival_released:
                    aid=self.arrival_released.pop(key)
                    self.traces.append({'kind':'arrival_receipt','type':'arrival_receipt','episode_id':self.episode,'time':response['time'],'decision_id':key[0],'command_index':key[1],'agent_id':aid,'execution_status':item['execution_status']})
                    if item['execution_status']!='completed':self.arrival_disabled=True
        super().observe_response(response)
        obs=response.get('observation')
        if self.arrival_disabled or not obs or not self._profile() or self.capabilities.get('environment_id')!='metarescue_planar_reference' or self.program or request['operation']=='safe_stop' or obs['constraints'] or obs['mission']['constraints'] or obs['mission'].get('directives') or obs['observation_quality']['contradiction_count']:return
        if self.arrival_mode=='arrival-radio' and obs['observation_quality'].get('maximum_delay',0)<=1:return
        for a in obs['agents']:
            aid=a['id'];p=self.pending.get(aid)
            if not p or p[3][1]!='move' or a['status']!='active' or a['constraints'] or not a['communication'].get('reachable',False) or a.get('position') is None or not any(x.get('action')=='move' for x in a['available_actions']):continue
            if self.arrival_mode=='arrival-ground' and a['kind']!='ground':continue
            accepted=self.accepted_moves.get(p[:2]);intent=p[2]
            if not accepted or accepted[0]!=aid or math.dist(a['position'],accepted[1])>1e-8 or not intent or intent[0] not in ('casualty','coverage','repeat'):continue
            # No service or cargo state is inferred. Keep original receipt key for audit.
            if self.arrival_mode=='arrival-sample' and a['kind']=='ground':
                now=obs['time']; sig=self.resource_signature(a); prev=self.previous_reading.get(aid); loaded=aid in self.cargo
                if sig is not None and prev and 0<now-prev[0]<=2 and prev[3:]==(sig,loaded):
                    length=math.dist(a['position'],prev[1]);spent=prev[2]-a['resources']['energy']['value']
                    if length>.1 and spent>0 and math.isfinite(spent/length):
                        self.energy_samples.setdefault((aid,sig,loaded),deque(maxlen=12)).append(spent/length)
                prior=self.last_motion.get(aid)
                if prior and 0<now-prior[0]<=2:
                    speed=math.dist(a['position'],prior[1])/(now-prior[0])
                    if math.isfinite(speed) and speed>1e-6:self.speeds[aid]=max(speed,self.speeds.get(aid,0))
            self.pending.pop(aid);self.arrival_released[p[:2]]=aid
            if intent[0]=='repeat':self.repeat_goals[aid]=intent[1]
            self.traces.append({'kind':'arrival_observed','type':'arrival_observed','episode_id':self.episode,'time':response['time'],'request_id':obs['request_id'],'decision_id':p[0],'command_index':p[1],'agent_id':aid,'position':a['position'],'basis':'accepted_move_and_public_active_at_destination_not_terminal_ack'})
