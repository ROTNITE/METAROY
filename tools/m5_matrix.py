"""Read-only diagnostic matrix; configuration groups are not official Q or M5.

Rows keep the seed, mode, scenario, endpoint identity and timeout separate.
Repeated attempts are counted as attempts, not independent pair evaluations.
Only compact series summaries are consumed; no full report or service reads.
"""
import re
import json

IDENTIFIER=re.compile(r'[a-zA-Z0-9_-]{1,48}\Z')


def build_matrix(runs):
    groups={}
    excluded=0
    # Caller provides newest series first, preserving the latest trial link.
    for run in runs:
        spec=run.get('spec')
        if not isinstance(spec,dict):
            excluded+=len(run['rows']);continue
        c,e=spec.get('controller'),spec.get('environment')
        if not all(isinstance(v,str) and IDENTIFIER.fullmatch(v) for v in (c,e)):
            excluded+=len(run['rows']);continue
        profile=spec.get('profile') if e=='reference' else None
        if e=='reference' and profile not in ('A','K'):
            excluded+=len(run['rows']);continue
        co,eo=spec.get('controller_origin'),spec.get('environment_origin')
        path=spec.get('decide_path') if co else None
        rpc=spec.get('decide_profile') if co else None
        timeout=spec.get('timeout')
        decision_timeout=spec.get('decision_timeout',10) if co else None
        mission_id=spec.get('mission_id','search_and_rescue')
        params=spec.get('mission_parameters',{})
        if not isinstance(mission_id,str) or not mission_id or len(mission_id)>128 or not isinstance(params,dict):
            excluded+=len(run['rows']);continue
        try:
            parameter_key=json.dumps(params,sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(',',':'))
        except (ValueError,TypeError,RecursionError):
            excluded+=len(run['rows']);continue
        if len(parameter_key)>1024:
            excluded+=len(run['rows']);continue
        if (any(v is not None and (not isinstance(v,str) or len(v)>128) for v in (co,eo,path,rpc))
                or (timeout is not None and (type(timeout) is not int or not 5<=timeout<=300))
                or (decision_timeout is not None and (type(decision_timeout) is not int or not 1<=decision_timeout<=60))):
            excluded+=len(run['rows']);continue
        for row in run['rows']:
            if not isinstance(row,dict):
                excluded+=1;continue
            seed,mode,index=row.get('seed'),row.get('mode'),row.get('index')
            if (type(seed) is not int or not 0<=seed<=2147483647 or mode not in ('strict','declared')
                    or type(index) is not int or not 0<=index<=999):
                excluded+=1;continue
            key=(c,e,profile,mode,seed,co,eo,path,rpc,timeout,decision_timeout,mission_id,parameter_key)
            if key not in groups:
                groups[key]={'controller':c,'environment':e,'profile':profile,'mode':mode,'seed':seed,
                             'controller_origin':co,'environment_origin':eo,'decide_path':path,'decide_profile':rpc,
                             'timeout':timeout,'decision_timeout':decision_timeout,'mission_id':mission_id,
                             'mission_parameters':json.loads(parameter_key),'attempts':0,'finished':0,'exchange_passed':0,
                             'mission_success':0,'truth_trials':0,'all_evacuated':0,'inputs_preserved':0,
                             'latest_series_id':run['id'],'latest_trial_index':index,
                             'latest_state':row.get('state')}
            cell=groups[key];cell['attempts']+=1
            if row.get('state') in ('queued','running','stopping'):
                continue
            cell['finished']+=1
            result=row.get('result')
            if not isinstance(result,dict):
                continue
            cell['exchange_passed']+=int(result.get('exchange')=='passed')
            cell['mission_success']+=int(result.get('status')=='success')
            cell['inputs_preserved']+=int(result.get('inputs_preserved') is True)
            truth=result.get('independent_truth')
            if isinstance(truth,dict) and type(truth.get('physical_handovers')) is int:
                cell['truth_trials']+=1
                cell['all_evacuated']+=int(truth.get('all_evacuated') is True)
    def order(cell):
        return (cell['controller'],cell['environment'],cell['profile'] or '',cell['mode'],cell['seed'],
                cell['controller_origin'] or '',cell['environment_origin'] or '',cell['decide_path'] or '',
                cell['decide_profile'] or '',cell['timeout'] or 0,cell['decision_timeout'] or 0,
                cell['mission_id'],json.dumps(cell['mission_parameters'],sort_keys=True,ensure_ascii=True))
    return sorted(groups.values(),key=order),excluded


def pair_grid(cells, controllers, environments, row_offset=0, column_offset=0, limit=8):
    """Bounded pair overview. Empty cells mean no matching diagnostic trials."""
    if (type(row_offset) is not int or type(column_offset) is not int or
            not 0<=row_offset<=1000000 or not 0<=column_offset<=1000000 or
            type(limit) is not int or not 1<=limit<=12):
        raise ValueError('Invalid pair grid page')
    rows=sorted(set(controllers)|{c['controller'] for c in cells})
    columns=sorted(set(environments)|{c['environment'] for c in cells})
    visible_rows=rows[row_offset:row_offset+limit]
    visible_columns=columns[column_offset:column_offset+limit]
    row_ids,column_ids=set(visible_rows),set(visible_columns)
    pairs={}
    for c in cells:
        if c['controller'] not in row_ids or c['environment'] not in column_ids:
            continue
        key=(c['controller'],c['environment'])
        if key not in pairs:
            pairs[key]={'controller':key[0],'environment':key[1],'configurations':0,
                        'attempts':0,'finished':0,'exchange_passed':0,'mission_success':0,
                        'truth_trials':0,'all_evacuated':0,'inputs_preserved':0}
        pair=pairs[key]
        pair['configurations']+=1
        for field in ('attempts','finished','exchange_passed','mission_success',
                      'truth_trials','all_evacuated','inputs_preserved'):
            pair[field]+=c[field]
    return {'controllers':visible_rows,'environments':visible_columns,
            'controller_total':len(rows),'environment_total':len(columns),
            'row_offset':row_offset,'column_offset':column_offset,'limit':limit,
            'pairs':list(pairs.values()),'official_m5':None}
