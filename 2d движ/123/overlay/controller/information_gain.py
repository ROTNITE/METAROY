"""Experimental planar survey-point geometry, not a sensor coverage certificate."""
import math
from controller.adaptive_survey import AdaptiveSurveyPolicy
from controller.portable import _path_clear, _allowed, horizon_of

class InformationPolicy(AdaptiveSurveyPolicy):
    def __init__(self,*args,grid='center',**kw):
        self.grid=grid
        super().__init__(*args,**kw)
    def _reset(self,episode):
        super()._reset(episode)
        self.geometry_key=None
        self.geometry_points=None
        self.info_eligible=False
    def decide(self, observation):
        if self.episode != observation['episode_id']:
            self._reset(observation['episode_id']); self.started=observation['time']
            carriers=[a for a in observation['agents'] if a['kind']=='ground' and 'carry' in a['capabilities']]
            self.info_eligible=(len(carriers)>=2 and self._profile() and self.public.identity and
                all((e:=self._usable_energy(a)) is not None and e>=.25 for a in carriers))
        return super().decide(observation)
    def _scout_points(self,zones,agent):
        original=list(super()._scout_points(zones,agent))
        if self.grid.startswith("repeat") or self.grid=="base": return original
        if self.grid=="split-only" and not any(z["type"]=="obstacle" for z,b in zones): return original
        if not (self.info_eligible and self.scout_enabled and agent['kind']=='aerial' and self._wide_radius(super(AdaptiveSurveyPolicy,self)._observe_parameters()) is not None):
            return original
        air=[b for z,b in zones if b and z['type']=='air' and _allowed(z,'aerial') and not z['risk'] and not z['constraints']]
        if len(air)!=1 or len(zones)>8: return original
        b=air[0]; x0,y0,z0=b.minimum; x1,y1,z1=b.maximum
        if z0!=z1 or min(x1-x0,y1-y0)<=0: return original
        r=self._observe_parameters()['radius']; key=(agent['id'],tuple(b.minimum),tuple(b.maximum),r,tuple(sorted((z['type'],tuple(bb.minimum),tuple(bb.maximum)) for z,bb in zones if bb)))
        if self.geometry_key==key: return self.geometry_points
        if max(x1-x0,y1-y0)>120 or r<4: return original
        rectangles=[(x0,y0,x1,y1)]
        if self.grid.startswith(('split','cover')):
            for zone,box in zones:
                if box is None or zone['type']!='obstacle' or not box.minimum[2]<=z0<=box.maximum[2]: continue
                a,c,_=box.minimum; d,f,_=box.maximum
                if c<=y0 and f>=y1 and x0<a<d<x1: rectangles=[(x0,y0,a-.05,y1),(d+.05,y0,x1,y1)]
                elif a<=x0 and d>=x1 and y0<c<f<y1: rectangles=[(x0,y0,x1,c-.05),(x0,f+.05,x1,y1)]
        result=[]
        for a,c,d,f in rectangles:
            if d<=a or f<=c: return original
            spacing=math.sqrt(2)*(r-1)*(.85 if self.grid.endswith('dense') else 1.)
            nx=max(1,math.ceil((d-a)/spacing)); ny=max(1,math.ceil((f-c)/spacing))
            if nx*ny>64: return original
            pts=[(a+(i+.5)*(d-a)/nx,c+(j+.5)*(f-c)/ny,z0) for i in range(nx) for j in range(ny)]
            if self.grid.startswith('cover'):
                sx=max(2,math.ceil((d-a)/3)); sy=max(2,math.ceil((f-c)/3))
                samples=[(a+i*(d-a)/sx,c+j*(f-c)/sy,z0) for i in range(sx+1) for j in range(sy+1)]
                candidates=sorted(set(pts+[(a+(i+.5)*(d-a)/(2*nx),c+(j+.5)*(f-c)/(2*ny),z0) for i in range(2*nx) for j in range(2*ny)]))
                cover=[{i for i,p in enumerate(samples) if math.dist(p,q)<=r-1} for q in candidates]
                remaining=set(range(len(samples))); selected=[]
                while remaining:
                    idx=max(range(len(candidates)),key=lambda i:(len(cover[i]&remaining),-math.dist(agent['position'],candidates[i]),tuple(-v for v in candidates[i])))
                    gain=cover[idx]&remaining
                    if not gain: break
                    selected.append(candidates[idx]);remaining-=gain
                if not remaining and len(selected)<=len(pts): pts=selected
            result.extend(pts)
        if len(result)>64: return original
        reached=[]; frontier=[tuple(agent['position'])]; remaining=set(result)
        while frontier:
            start=frontier.pop(); accessible=sorted(p for p in remaining if _path_clear(zones,{**agent,'position':start},p))
            reached.extend(accessible);frontier.extend(accessible);remaining.difference_update(accessible)
        if not reached: return original
        self.geometry_key=key;self.geometry_points=tuple(reached)
        return self.geometry_points

    def _repeat_sector(self, observation, zones, agent, points, claimed, scouts):
        baseline, reason=super()._repeat_sector(observation,zones,agent,points,claimed,scouts)
        if baseline is None or not self.grid.startswith('repeat') or not self.info_eligible or not self.scout_enabled:
            return baseline, reason
        # Keep the parent's demand gate, reserve and feasible baseline. Score
        # alternatives by approximate spatial freshness, not by hidden targets.
        air=[b for z,b in zones if b and z['type']=='air' and _allowed(z,'aerial') and not z['risk'] and not z['constraints']]
        if len(air)!=1 or len(zones)>8 or air[0].minimum[2]!=air[0].maximum[2] or len(scouts)!=1:
            return baseline, reason
        b=air[0]; x0,y0,z0=b.minimum; x1,y1,_=b.maximum
        if max(x1-x0,y1-y0)>120: return baseline,reason
        estimates=self.capabilities.get('patrol_estimates',{})
        speed,scan_s,rate,scan_energy,side=(estimates.get(k) for k in ('speed_m_s','scan_s','move_fraction_per_m','scan_fraction','reference_distance_m'))
        if any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in (speed,scan_s,rate,scan_energy,side)):
            return baseline,reason
        now=observation['time']; horizon=horizon_of(observation['mission']); r=self._observe_parameters().get('radius',0)-1
        if horizon is None or r<=0:return baseline,reason
        energy=agent['resources'].get('energy'); available=energy['value']-energy['minimum'] if energy else -1
        stations=[s['position'] for s in observation['infrastructure'] if s['kind']=='base_station' and s['status']=='active']
        carriers=[a for a in observation['agents'] if a['kind']=='ground' and 'carry' in a['capabilities'] and a['position'] is not None and a['status']=='active' and a['communication'].get('reachable',True)]
        if not stations:return baseline,reason
        samples=[(x0+(i+.5)*(x1-x0)/12,y0+(j+.5)*(y1-y0)/12,z0) for i in range(12) for j in range(12)]
        age=[max(0.,now-max((stamp for p,stamp in self.scanned_at.items() if math.dist(p,q)<=r),default=self.started)) for q in samples]
        cycle=len(points)*(scan_s+side/speed)
        scores=[]
        for p in points:
            if p not in self.scanned_at or self.visits.get(p,0)>0 or ('repeat',p) in claimed or now-self.scanned_at[p]<max(6.,.35*cycle):continue
            if not _path_clear(zones,agent,p):continue
            distance=math.dist(agent['position'],p); duration=distance/speed+scan_s
            if now+duration>=horizon:continue
            back=min((math.dist(p,s) for s in stations if _path_clear(zones,{**agent,'position':p},s)),default=math.inf)
            if (distance+back)*rate+scan_energy>available:continue
            gain=sum(a for a,q in zip(age,samples) if math.dist(p,q)<=r)
            if (self.grid=='repeat-rescue' or self.grid.startswith('repeat-tune-')) and carriers:
                # Approximate time left for a subsequent ground round trip.
                trip=min(math.dist(p,a['position'])+min(math.dist(p,s) for s in stations) for a in carriers)
                v=max((self.speeds.get(a['id'],1.) for a in carriers),default=1.)
                weight=max(.05,min(1.,(horizon-now-duration-trip/v)/max(1.,horizon-now)))
                exponent=float(self.grid.split('-')[3]) if self.grid.startswith('repeat-tune-') else 1.
                gain*=weight**exponent
            exponent=float(self.grid.split('-')[2]) if self.grid.startswith('repeat-tune-') else (0. if self.grid=='repeat-age' else 1.)
            denom=duration**exponent
            scores.append((gain/denom,-distance,p))
        if not scores:return baseline,reason
        chosen=max(scores)[2]
        return chosen,'information_repeat_candidate'
