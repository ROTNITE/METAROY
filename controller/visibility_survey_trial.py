"""Research-only planar occlusion-aware reconnaissance; public geometry only."""
import math
from controller.route_value import RouteValuePolicy, VisibilityRouter
from controller.portable import _path_clear, _allowed, horizon_of

class AirRouter(VisibilityRouter):
    def clear(self,a,b):return _path_clear(self.zones,{'kind':'aerial','position':a},b)

class VisibilitySurveyPolicy(RouteValuePolicy):
    def __init__(self,*args,survey_mode='vis-primary',**kw):
        self.survey_mode=survey_mode
        super().__init__(*args,route_mode='gate-1.8',**kw)
    def _reset(self,episode):
        super()._reset(episode);self.survey_key=None;self.survey=None
    def _build(self,zones,agent):
        air=[b for z,b in zones if b and z['type']=='air' and _allowed(z,'aerial') and not z['risk'] and not z['constraints']]
        if len(air)!=1:return None
        b=air[0];x0,y0,z0=b.minimum;x1,y1,z1=b.maximum
        if z0!=z1 or z0!=0 or max(x1-x0,y1-y0)>60:return None
        r=self._observe_parameters().get('radius',0)
        if r<8:return None
        key=(tuple((z['type'],b.minimum,b.maximum,z['risk']) for z,b in zones),r)
        if self.survey_key==key:return self.survey
        router=AirRouter(zones);nx=math.ceil((x1-x0)/5);ny=math.ceil((y1-y0)/5)
        grid=[(x0+(i+.5)*(x1-x0)/nx,y0+(j+.5)*(y1-y0)/ny,z0) for i in range(nx) for j in range(ny)]
        grid=[p for p in grid if router.clear(p,p)]
        candidates=sorted(set(grid+list(super()._scout_points(zones,agent))))
        cover={}
        for p in candidates:
            if not router.clear(p,p):continue
            mask=sum(1<<i for i,q in enumerate(grid) if math.dist(p,q)<=r-.5 and router.clear(p,q))
            if mask:cover[p]=mask
        # All samples are geometry proxies, not evidence of actual sensor coverage.
        self.survey=(router,grid,cover);self.survey_key=key;return self.survey
    def _target_service(self,obs,zones,a,claimed,issue):
        if super()._target_service(obs,zones,a,claimed,issue):return True
        if not self.route_active or a['kind']!='aerial' or 'sense' not in a['capabilities'] or not any(z['type'] in ('obstacle','restricted','hazard') for z,b in zones):return False
        data=self._build(zones,a)
        if data is None:return False
        router,grid,cover=data;now=obs['time'];h=horizon_of(obs['mission']);energy=a['resources'].get('energy');est=self.capabilities.get('patrol_estimates',{})
        speed=est.get('speed_m_s');scan=est.get('scan_s');rate=est.get('move_fraction_per_m');scan_e=est.get('scan_fraction')
        if h is None or energy is None or not all(isinstance(x,(int,float)) and x>0 for x in (speed,scan,rate,scan_e)):return False
        bases=[s['position'] for s in obs['infrastructure'] if s['kind']=='base_station' and s['status']=='active']
        if not bases:return False
        seen=0
        for p in self.scanned_at:seen|=cover.get(p,0)
        reserved=0
        for typ,p,*_ in claimed:
            if typ in ('coverage','repeat') and p in cover:reserved|=cover[p]
        missing=((1<<len(grid))-1)&~(seen|reserved)
        repeat=not missing
        carriers=[r for r in obs['agents'] if r['kind']=='ground' and 'carry' in r['capabilities'] and r['communication'].get('reachable',True) and r['status'] in ('active','busy') and (e:=r['resources'].get('energy')) and e['value']>e['minimum']]
        if not carriers:return False
        waiting=sum(t.get('kind')=='person' and t.get('status')=='confirmed' and t.get('evacuation_state')=='waiting' and ('casualty',t['id']) not in claimed for t in obs['targets'])
        free=sum(r['id'] not in self.pending and r['id'] not in self.cargo for r in carriers)
        if repeat:
            if self.survey_mode=='vis-primary':return False
            if now<self.started+.35*(h-self.started) or h-now<20 or free<=waiting and self.survey_mode=='vis-demand':return True
            if self.net65_repeats>= (8 if self.survey_mode=='vis-demand' else 12):return True
        age=[]
        if repeat:
            for i,q in enumerate(grid):
                stamp=max((t for p,t in self.scanned_at.items() if cover.get(p,0)>>i&1),default=self.started)
                age.append(max(0,now-stamp-12))
        choices=[]
        for p,mask in cover.items():
            intent=('repeat' if repeat else 'coverage',p)
            if intent in claimed or ('coverage',p) in claimed or ('repeat',p) in claimed:continue
            if repeat:
                gain=sum(age[i] for i in range(len(grid)) if mask>>i&1)
            else:gain=(mask&missing).bit_count()
            if gain<=0:continue
            path=router.path(a['position'],p)
            if not path:continue
            dist,nextp=path;duration=dist/speed+scan
            if now+duration>=h:continue
            back=min((v[0] for s in bases if (v:=router.path(p,s)) is not None),default=math.inf)
            if (dist+back)*rate+scan_e>energy['value']-energy['minimum']:continue
            if repeat:
                trip=min((math.dist(r['position'],p)+min(math.dist(p,s) for s in bases))/self.speeds.get(r['id'],1.3) for r in carriers)
                gain*=max(.05,min(1.,(h-now-duration-trip)/max(1,h-now)))
                if p in self.scanned_at and now-self.scanned_at[p]<20:continue
            score=gain/(duration+1.)
            choices.append((score,-dist,p,nextp,intent))
        for _,_,p,nextp,intent in sorted(choices,reverse=True):
            if math.dist(a['position'],p)<.01:
                if issue(a,'observe',self._observe_parameters(),intent):return True
            elif self._motion_clear(obs,zones,a,nextp) and issue(a,'move',{'target':list(nextp)},intent):return True
        return True if repeat and self.survey_mode!='vis-primary' else False
