"""Aggregate externally assessed Q values without inventing a scoring rubric.

Historical main specification section 12.6: sum of cross-team Q / (|C| * |E|).
The November 28 tournament regulation adds a per-team 70/30 directional
weighting; use aggregate_tournament_m5 for that explicitly labelled estimate.
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


def aggregate_tournament_m5(controllers,environments,assessments,*,team_id,method_id=None):
    """Directional estimate from supplied Q, not an official organizer score.

    REGULATION_TECH section 5: 70% outbound, 30% inbound. The precise
    organizer Q rubric is not provided. Average across registered external
    pairs in each direction only; missing required assessments remain unknown.
    """
    controllers=_registry(controllers,'controller')
    environments=_registry(environments,'environment')
    if not isinstance(team_id,str) or not team_id.strip():
        raise ValueError('team_id must be a nonempty string')
    rows=list(assessments)
    # Reuse the legacy input/provenance validation, but not its denominator.
    aggregate_m5(controllers,environments,rows,method_id=method_id)
    values={(row['controller_id'],row['environment_id']):row['q'] for row in rows}
    def direction(outbound):
        pairs=sorted((c,e) for c,ct in controllers.items() for e,et in environments.items()
                     if ((ct==team_id and et!=team_id) if outbound else (ct!=team_id and et==team_id)))
        missing=[(c,e) for c,e in pairs if (c,e) not in values]
        return {'value':fsum(values[p] for p in pairs)/len(pairs) if pairs and not missing else None,
                'required_pairs':len(pairs),'assessed_pairs':len(pairs)-len(missing),
                'missing_pairs':[{'controller_id':c,'environment_id':e} for c,e in missing]}
    outbound,inbound=direction(True),direction(False)
    ready=outbound['value'] is not None and inbound['value'] is not None
    return {'value':.7*outbound['value']+.3*inbound['value'] if ready else None,
            'status':'estimated_from_supplied_q' if ready else 'unverified',
            'official':False,'team_id':team_id,'method_id':method_id,
            'weights':{'outbound':.7,'inbound':.3},'outbound':outbound,'inbound':inbound}
