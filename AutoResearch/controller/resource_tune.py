"""Experimental calibration of learned energy reserve estimates."""
from controller.adaptive_survey import AdaptiveSurveyPolicy
class ResourceTunePolicy(AdaptiveSurveyPolicy):
    def __init__(self,*args,factor=1.,**kwargs):
        if not .90<=factor<=1.25:raise ValueError('factor outside research range')
        self.factor=factor
        super().__init__(*args,**kwargs)
    def rate(self,agent,loaded):
        rate=super().rate(agent,loaded)
        return None if rate is None else rate*self.factor
