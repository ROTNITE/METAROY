"""Canonical supported laboratory inputs; do not infer foreign mission schemas."""
from environment.world import CASES


def checked_mission_parameters(engine,controller,value):
    if not isinstance(value,dict):
        raise ValueError('Mission parameters must be an object')
    if not value:
        return {}
    if engine!='planar' or controller=='independent':
        raise ValueError('Custom mission parameters require the planar environment and a reset-owning controller')
    if set(value)-{'case','opaque'}:
        raise ValueError('Unknown planar mission parameter')
    case=value.get('case',CASES[0])
    opaque=value.get('opaque',False)
    if not isinstance(case,str) or case not in CASES or type(opaque) is not bool:
        raise ValueError('Invalid planar case or opaque flag')
    # The planar reset already supplies these defaults. Freeze the same
    # canonical wire object for explicit and implicit default values.
    return {**({'case':case} if case!=CASES[0] else {}),
            **({'opaque':True} if opaque else {})}
