"""Short public-information coordination window before expensive initial trips.
Only low-resource route profile; no private cost or future-target access.
"""
from controller.adaptive_wave import AdaptiveWavePolicy
from controller.portable import horizon_of
class EarlyCoordinationPolicy(AdaptiveWavePolicy):
    def __init__(self,*args,coord_mode='coord-10-400',**kw):
        _,window,ratio=coord_mode.split('-');self.coord_window=float(window);self.coord_ratio=float(ratio)
        super().__init__(*args,**kw)
    def _flow_wave(self,obs,zones,agents,claimed,orders):
        base=super()._flow_wave(obs,zones,agents,claimed,orders)
        if not self.route_active or not self.low_initial or not base or orders or self.program:return base
        if obs['time']-self.started>=self.coord_window or self.scanned_at:return base
        if not any(a['kind']=='aerial' and 'sense' in a['capabilities'] and a['communication'].get('reachable',False) and a['status'] in ('active','busy') for a in obs['agents']):return base
        targets={t['id']:t for t in obs['targets']};out=base.copy()
        for a in agents:
            tid=base.get(a['id']);e=a['resources'].get('energy');t=targets.get(tid)
            if not t or not e or e['value']<=e['minimum']:continue
            v=self._estimate(obs,a,t)
            if v is None or v[0]!='move':continue
            if (v[4]+v[5])/(e['value']-e['minimum'])>self.coord_ratio:
                out[a['id']]=None
        if out!=base:self.traces.append({'kind':'early_coordination','type':'early_coordination','episode_id':obs['episode_id'],'time':obs['time'],'request_id':obs['request_id'],'before':base,'after':out,'basis':'wait_for_first_public_scout_observation'})
        return out
