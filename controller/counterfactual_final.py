"""Frozen learned research profiles. Models are numeric JSON, never pickles."""
import json
from pathlib import Path
from controller.counterfactual_options import OptionPolicy
from controller.early_coordination import EarlyCoordinationPolicy
from controller.adaptive_wave import AdaptiveWavePolicy
from controller.counterfactual_value import FEATURES
class CounterfactualValuePolicy(OptionPolicy,EarlyCoordinationPolicy):
    def __init__(self,*args,cf_profile='value',**kw):
        if cf_profile not in ('value','economy','survey-only'):raise ValueError(cf_profile)
        self.cf_profile=cf_profile
        model=json.loads((Path(__file__).parent/'models'/('counterfactual_economy.json' if cf_profile=='economy' else 'counterfactual_value.json')).read_text())
        if any(m['features']!=list(FEATURES) for m in model['models'].values()):raise ValueError('counterfactual model feature contract')
        super().__init__(*args,option_models=model,option_threshold=model['threshold'],coord_mode='coord-12-400',**kw)
    def _flow_wave(self,obs,zones,agents,claimed,orders):
        if self.cf_profile=='survey-only':return AdaptiveWavePolicy._flow_wave(self,obs,zones,agents,claimed,orders)
        return super()._flow_wave(obs,zones,agents,claimed,orders)
