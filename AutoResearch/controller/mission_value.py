"""Experimental marginal rescue-value ordering of primary survey waypoints.
Only public geometry and confirmed observations are used. No new actions.
"""
import math
from controller.adaptive_survey import AdaptiveSurveyPolicy
from controller.portable import _path_clear, _allowed, horizon_of

class MissionValuePolicy(AdaptiveSurveyPolicy):
    def __init__(self,*args,value_mode='coverage',**kwargs):
        self.value_mode=value_mode
        super().__init__(*args,**kwargs)
    def _reset(self,episode):
        super()._reset(episode)
        self.view_now=None
        self.sample_key=None
        self.samples=None
    def decide(self, observation):
        if self.episode != observation['episode_id']:
            self._reset(observation['episode_id']);self.started=observation['time']
        self.view_now=observation
        return super().decide(observation)
    def _scout_points(self,zones,agent):
        points=list(super()._scout_points(zones,agent))
        obs=self.view_now
        if not (obs and self.scout_enabled and self._profile() and agent['kind']=='aerial' and len(points)<=32 and sum(a['kind']=='aerial' for a in obs['agents'])==1):return points
        carriers=[a for a in obs['agents'] if a['kind']=='ground' and 'carry' in a['capabilities'] and a['position'] is not None and a['communication'].get('reachable',True)]
        if len(carriers)<2:return points
        remaining=[p for p in points if ('coverage',p) not in self.completed and p not in self.scanned_at]
        if len(remaining)<2:return points
        air=[b for z,b in zones if b and z['type']=='air' and _allowed(z,'aerial') and not z['risk'] and not z['constraints']]
        stations=[s['position'] for s in obs['infrastructure'] if s['kind']=='base_station' and s['status']=='active']
        if len(air)!=1 or not stations or air[0].minimum[2]!=air[0].maximum[2]:return points
        b=air[0];x,y,z=b.minimum;X,Y,_=b.maximum
        radius=self._observe_parameters().get('radius',0)-1
        if radius<=0 or max(X-x,Y-y)>120:return points
        key=(tuple(b.minimum),tuple(b.maximum),radius,tuple(sorted((v['type'],tuple(bb.minimum),tuple(bb.maximum)) for v,bb in zones if bb)),tuple(map(tuple,stations)))
        if key!=self.sample_key:
            samples=[]
            for i in range(10):
                for j in range(10):
                    q=(x+(i+.5)*(X-x)/10,y+(j+.5)*(Y-y)/10,z)
                    back=min((math.dist(q,s) for s in stations if _path_clear(zones,{'kind':'ground','position':q},s)),default=math.inf)
                    if math.isfinite(back):samples.append((q,back))
            self.samples=samples;self.sample_key=key
        unknown=[(q,back) for q,back in self.samples if not any(math.dist(q,p)<=radius for p in self.scanned_at)]
        if not unknown:return points
        h=horizon_of(obs['mission']); now=obs['time']; travel_speed=self.capabilities.get('patrol_estimates',{}).get('speed_m_s')
        if not h or not isinstance(travel_speed,(int,float)) or travel_speed<=0:return points
        # Default 1 m/s prior only for ranking, never for a feasibility guarantee.
        values=[]
        for q,back in unknown:
            eta=min((math.dist(a['position'],q)+back)/max(.1,self.speeds.get(a['id'],1.)) for a in carriers)
            if self.value_mode.startswith('rescue'):
                value=max(.05,1-eta/max(1,h-now))
            else:value=1.
            values.append((q,value))
        scores=[]
        for p in remaining:
            if not self._motion_clear(obs,zones,agent,p):continue
            d=math.dist(agent['position'],p)
            gain=sum(v for q,v in values if math.dist(p,q)<=radius)
            exponent=.5 if self.value_mode.endswith('soft') else 1.
            scores.append((gain/(2+d/travel_speed)**exponent,-d,p))
        if not scores or max(scores)[0]<=0:return points
        chosen=max(scores)[2]
        # Only the order changes: once this point is confirmed, all other
        # unvisited points become candidates again. Never mark them covered.
        self.traces.append({'kind':'mission_value_order','episode_id':self.episode,'time':now,'agent_id':agent['id'],'chosen':chosen,'mode':self.value_mode,'score':max(scores)[0]})
        return [p for p in points if p==chosen or p not in remaining]

class DeadlineSurveyPolicy(MissionValuePolicy):
    """Suppress late repeated observations with negligible estimated rescue value."""
    def __init__(self,*args,value_mode='guard',**kw):
        super().__init__(*args,value_mode=value_mode,**kw)
    def _scout_points(self,zones,agent):
        if self.value_mode.startswith('guard'):
            return list(AdaptiveSurveyPolicy._scout_points(self,zones,agent))
        return super()._scout_points(zones,agent)
    def _repeat_sector(self, observation,zones,agent,points,claimed,scouts):
        goal,reason=super()._repeat_sector(observation,zones,agent,points,claimed,scouts)
        if goal is None or not self.scout_enabled or not self._profile() or len(scouts)!=1:return goal,reason
        carriers=[a for a in observation['agents'] if a['kind']=='ground' and 'carry' in a['capabilities'] and a['position'] is not None and a['status'] in ('active','busy') and a['communication'].get('reachable',True)]
        if len(carriers)<2 or any(a['id'] not in self.speeds for a in carriers):return goal,reason
        stations=[s['position'] for s in observation['infrastructure'] if s['kind']=='base_station' and s['status']=='active']
        if not stations:return goal,reason
        now=observation['time'];h=horizon_of(observation['mission']);est=self.capabilities.get('patrol_estimates',{});speed=est.get('speed_m_s');scan=est.get('scan_s');radius=self._observe_parameters().get('radius')
        if h is None or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in (speed,scan,radius)):return goal,reason
        left=h-now-math.dist(agent['position'],goal)/speed-scan
        # Optimistic continuous lower bound: triangle inequality plus distance
        # from each endpoint to sensing disk. No claim of certified speed bound.
        fastest=max(self.speeds[a['id']] for a in carriers)*1.1
        lower=min(max(0.,math.dist(a['position'],goal)-radius)+max(0.,math.dist(goal,s)-radius) for a in carriers for s in stations)/fastest
        lower=max(lower,min(math.dist(a['position'],s) for a in carriers for s in stations)/fastest)
        margin=0 if self.value_mode.endswith('0') else 8 if self.value_mode.endswith('8') else 4
        if left < lower+margin:
            self.traces.append({'kind':'deadline_survey_guard','episode_id':self.episode,'time':now,'agent_id':agent['id'],'remaining_after_scan':left,'optimistic_rescue_s':lower,'margin_s':margin})
            return None,'deadline_survey_guard'
        return goal,reason
