"""Experimental resource-aware retry isolation using public telemetry only."""
from statistics import median
import math
import hashlib
import json
from controller.information_gain import InformationPolicy

class ReliablePolicy(InformationPolicy):
    def __init__(self,*args,reliability='isolate',**kw):
        self.reliability=reliability
        super().__init__(*args,grid='repeat-tune-3-2.5',**kw)
    def _reset(self,episode):
        super()._reset(episode)
        self.low_initial=False
        self.refusal_memory={}
        self.frame={}
        self.last_retry_time=None
        self.context=None
        self.isolation_allowed=False
        self.refusal_ttl=60.
    def rate(self,agent,loaded):
        if self.low_initial and self.reliability.startswith('early') and self.reliability!='early-only':
            n,factor=self.reliability.split('-')[:2]; n=int(n[5:]);factor=float(factor)
            sig=self.resource_signature(agent)
            s=self.energy_samples.get((agent['id'],sig,loaded),())
            if len(s)>=n:return median(s)
            if loaded:
                empty=self.energy_samples.get((agent['id'],sig,False),())
                if len(empty)>=n:return median(empty)*factor
            if self.reliability.endswith('-peer'):
                peers=[median(v) for (aid,signature,load),v in self.energy_samples.items() if signature==sig and not load and len(v)>=n]
                if peers:return max(peers)*(factor if loaded else 1.)
        return super().rate(agent,loaded)
    def _state(self,a):
        e=a['resources'].get('energy')
        if e is None or a.get('position') is None:return None
        return (self.resource_signature(a), e['value'],tuple(a['position']),self.cargo.get(a['id']))
    @staticmethod
    def _context(observation):
        return hashlib.sha256(json.dumps([observation.get(k) for k in ('zones','infrastructure','constraints')]+[observation['mission'].get('constraints'),observation['mission'].get('directives')],sort_keys=True).encode()).hexdigest()
    def decide(self,observation):
        if self.episode!=observation['episode_id']:
            self._reset(observation['episode_id']);self.started=observation['time']
            carriers=[a for a in observation['agents'] if a['kind']=='ground' and 'carry' in a['capabilities']]
            self.low_initial=self._profile() and any((e:=self._usable_energy(a)) is not None and e<.25 for a in carriers)
            # InformationPolicy initializes this once per episode too.
            self.info_eligible=(len(carriers)>=2 and self._profile() and self.public.identity and all((e:=self._usable_energy(a)) is not None and e>=.25 for a in carriers))
        self.frame={a['id']:a for a in observation['agents']}
        # Changes in public geography, infrastructure or constraints invalidate negative evidence.
        self.context=self._context(observation)
        self.isolation_allowed=self._profile() and not observation['mission'].get('directives') and not observation['observation_quality']['contradiction_count']
        for sig,(state,context,expiry) in list(self.refusal_memory.items()):
            a=self.frame.get(sig[0])
            if not self.isolation_allowed or a is None or self._state(a)!=state or self.context!=context or observation['time']>=expiry:
                del self.refusal_memory[sig]
                self.cooldowns.pop(sig,None)
            else:self.cooldowns[sig]=observation['time']+1
        return super().decide(observation)
    def reject(self,now,code,*,fresh=None):
        request,intents=self.proposed
        if (self.reliability=='early-only' or not self.isolation_allowed or self.program or code!='RESOURCE_EXHAUSTED' or request['time']!=now or request['operation']!='keep' or len(request['commands'])!=len(intents) or request.get('metaevolution',{}).get('command_groups') or any(c['action'] not in ('move','observe','pickup','handover','inspect') for c in request['commands'])):
            return super().reject(now,code)
        if fresh is not None:
            agents={a['id']:a for a in fresh['agents']}
            if fresh['episode_id']!=self.episode or self._context(fresh)!=self.context or any(c['agent_id'] not in agents or self.frame.get(c['agent_id'])!=agents[c['agent_id']] for c in request['commands']):
                return super().reject(now,code)
        pairs=list(zip(request['commands'],intents))
        if len(pairs)==1:
            c,(_,sig)=pairs[0]; a=self.frame.get(c['agent_id']);state=self._state(a) if a else None
            if state is not None and c['action'] in ('move','observe'):
                self.refusal_memory[sig]=(state,self.context,now+self.refusal_ttl)
                self.traces.append({'kind':'reliable_refusal','type':'reliable_refusal','episode_id':self.episode,'time':now,'decision_id':request['decision_id'],'agent_id':c['agent_id'],'basis':'single_command_resource_refusal'})
            return super().reject(now,code)
        # Only one isolation attempt per model tick; subsequent rejection falls
        # back to the original conservative cooldown. No success is inferred.
        if self.reliability=='memory' or self.last_retry_time==now or not pairs:return super().reject(now,code)
        moves=[(c,sig) for c,(_,sig) in pairs if c['action'] in ('move','observe')]
        if not moves:return super().reject(now,code)
        def risk(pair):
            c,_=pair;a=self.frame[c['agent_id']];e=a['resources'].get('energy'); available=e['value']-e['minimum'] if e else math.inf
            length=math.dist(a['position'],c['parameters']['target']) if c['action']=='move' else 1.
            rate=self.rate(a,a['id'] in self.cargo) or self.rate(a,False)
            if rate is None and a['kind']=='aerial' and e and e.get('unit')=='normalized' and 'metarescue.patrol_estimates.v1' in self.public.extensions:
                estimates=self.capabilities.get('patrol_estimates',{})
                rate=estimates.get('move_fraction_per_m') if c['action']=='move' else estimates.get('scan_fraction')
            if rate is None and e:
                samples=[median(v) for (_,signature,loaded),v in self.energy_samples.items() if signature==self.resource_signature(a) and not loaded and len(v)>=3]
                rate=max(samples,default=0.)
            rate=rate if type(rate) in (int,float) and math.isfinite(rate) and rate>0 else 0.
            return (length*rate/max(available,1e-12),-self._usable_energy(a) if self._usable_energy(a) is not None else -1.,c['agent_id'])
        c,sig=max(moves,key=risk)
        self.cooldowns[sig]=now+10.;self.last_retry_time=now
        self.traces.append({'kind':'reliable_isolation','type':'reliable_isolation','episode_id':self.episode,'time':now,'decision_id':request['decision_id'],'agent_id':c['agent_id'],'basis':'suspected_resource_risk_not_confirmed'})
