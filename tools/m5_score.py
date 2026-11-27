"""Aggregate externally assessed Q values without inventing a scoring rubric.

Main specification section 12.6: sum of cross-team Q / (|C| * |E|).
A team ID determines diagonal exclusion; all component IDs are distinct
within each role. Missing evidence is not a zero and never a verified score.
"""
from collections.abc import Mapping
from math import fsum,isfinite


def _registry(items,role):
    if not isinstance(items,Mapping) or not items:
        raise ValueError(f'{role} registry must be a nonempty ID-to-team mapping')
    if not all(isinstance(k,str) and k.strip() and isinstance(v,str) and v.strip()
               for k,v in items.items()):
        raise ValueError(f'{role} component IDs and team IDs must be nonempty strings')
    return dict(items)


def aggregate_m5(controllers,environments,assessments,*,method_id=None):
    controllers=_registry(controllers,'controller')
    environments=_registry(environments,'environment')
    if method_id is not None and (not isinstance(method_id,str) or not method_id.strip()):
        raise ValueError('method_id must be a nonempty string')
    pairs={(c,e) for c,ct in controllers.items() for e,et in environments.items() if ct!=et}
    values={}
    for item in assessments:
        if not isinstance(item,Mapping):
            raise ValueError('Expected assessment object')
        key=item.get('controller_id'),item.get('environment_id')
        if not all(isinstance(x,str) for x in key) or key not in pairs:
            raise ValueError('Assessment must belong to a registered cross-team pair')
        if key in values:
            raise ValueError('Duplicate pair assessment; aggregate seeds upstream')
        q=item.get('q')
        if type(q) not in (int,float) or not 0<=q<=1 or not isfinite(q):
            raise ValueError('Q must be a finite number in [0,1]')
        if method_id is None or item.get('method_id')!=method_id:
            raise ValueError('Assessment requires the same explicitly supplied method_id')
        evidence=item.get('evidence')
        if not isinstance(evidence,list) or not evidence or not all(isinstance(s,str) and s.strip() for s in evidence):
            raise ValueError('Assessment must cite nonempty evidence references')
        values[key]=q
    missing=sorted(pairs-values.keys())
    denominator=len(controllers)*len(environments)
    total=fsum(values.values())
    ready=bool(pairs) and not missing and method_id is not None
    return {'value':total/denominator if ready else None,
            'status':'calculated_from_supplied_q' if ready else 'unverified',
            'official':False,'method_id':method_id,
            'numerator':total if ready else None,'denominator':denominator,
            'required_pairs':len(pairs),'assessed_pairs':len(values),
            'missing_pairs':[{'controller_id':c,'environment_id':e} for c,e in missing],
            'excluded_same_team_pairs':denominator-len(pairs)}
