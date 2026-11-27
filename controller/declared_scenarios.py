"""Declared scenario eligibility and role matching for the portable controller."""
from contracts.expressions import Expression, ExpressionError
from contracts.protocol import ProtocolError, validate
from controller.assignment import AssignmentError, minimum_assignment
from controller.scenarios import configuration, risk_of


SCENARIO_SEMANTICS = 'metaroy.scenario_semantics.v1'
GOALS = {'search','rescue','deliver','safety','maintain_connectivity'}


def required_roles(roles, agents, preferred):
    slots = []
    known = set()
    for role in roles:
        identifier = role.get('id')
        minimum, maximum = role.get('minimum'), role.get('maximum')
        required = role.get('required_capabilities', [])
        if (not isinstance(identifier,str) or identifier in known or type(minimum) is not int or
                type(maximum) is not int or not 0<=minimum<=maximum or not isinstance(required,list) or
                any(not isinstance(capability,str) for capability in required)):
            return None
        if len(slots)+minimum>len(agents):
            return None
        known.add(identifier)
        slots.extend((identifier,set(required)) for _ in range(minimum))
    if not slots:
        return {}
    costs = [
        [int(preferred.get(a['id']) != role) if required <= set(a['capabilities']) else None for a in agents]
        for role,required in slots
    ]
    try:
        assignment,_,_,_=minimum_assignment(costs)
    except AssignmentError:
        return None
    if any(costs[i][j] is None for i,j in enumerate(assignment)):
        return None
    return {agents[j]['id']:slots[i][0] for i,j in enumerate(assignment)}


def parameters_for(declaration, binding, observation, objectives):
    schema = declaration.get('parameter_schema')
    sources = binding.get('parameter_sources', {})
    if not isinstance(schema,dict) or not isinstance(sources,dict):
        raise ValueError('Invalid parameter binding')
    properties = schema.get('properties', {})
    candidate = {}
    for parameter, source in sources.items():
        if parameter not in properties or not isinstance(source,str):
            raise ValueError('Unknown parameter binding')
        if source=='mission_id':
            candidate[parameter]=observation['mission']['id']
        elif source=='horizon' and 'horizon' in observation['mission']['termination']:
            candidate[parameter]=observation['mission']['termination']['horizon']
        elif source in ('target_id','objective_id') and objectives:
            key='target_id' if source=='target_id' else 'id'
            if key in objectives[0]:
                candidate[parameter]=objectives[0][key]
        elif source=='default' and 'default' in properties[parameter]:
            candidate[parameter]=properties[parameter]['default']
    return validate(candidate,schema,'INVALID_SCENARIO')


def applicable(declaration, observation, phase):
    rules=declaration.get('applicability',{}).get(phase,[])
    hard=[]
    for constraint in declaration.get('constraints',[]):
        if constraint.get('severity','hard')!='hard':
            continue
        if constraint.get('scope',{}).get('kind','global')!='global' or 'predicate' not in constraint:
            return False
        hard.append(constraint['predicate'])
    return all(Expression(node).evaluate(observation) is True for node in (*rules,*hard))


def choose_declared(policy, observation, agents, old):
    """Use public declarations in the optional scenario compatibility profile."""
    descriptions=policy.capabilities.get('scenario_semantics',{})
    if not isinstance(descriptions,dict) or len(descriptions)>128:
        return 'keep',None,None,[]
    reports=[]
    candidates=[]
    preferred=policy.previous['role_assignments'] if policy.previous else {}
    for identifier,declaration in policy.scenarios.items():
        desc=descriptions.get(identifier)
        if (not isinstance(desc,dict) or not isinstance(desc.get('goal'),str) or desc['goal'] not in GOALS
                or identifier in policy.unavailable_scenarios):
            continue
        report={'scenario_id':identifier,'applicable':False,'utility':0.,
                'risk':risk_of(observation),'selected':False,'rejected_reason_codes':[]}
        reports.append(report)
        objectives=[item for item in observation['mission']['objectives']
                    if item.get('type')==desc['goal'] and item.get('status') not in ('completed','cancelled','failed')]
        try:
            roles=required_roles(declaration.get('roles',[]),agents,preferred)
            model=declaration.get('game_model',{})
            if roles is None or not model.get('class'):
                raise ValueError('Unavailable roles or game model')
            if not applicable(declaration, observation, 'continuation' if identifier in old else 'entry'):
                raise ValueError('Entry condition false')
            parameters=parameters_for(declaration,desc,observation,objectives)
        except (ProtocolError,ExpressionError,ValueError,TypeError,AttributeError):
            report['rejected_reason_codes']=['inapplicable_public_declaration']
            continue
        utility=max((o.get('priority',0.) for o in objectives if type(o.get('priority')) in (int,float)),default=0.)
        report.update(applicable=True,utility=utility)
        candidates.append((utility,identifier,parameters,roles,report))
    if not candidates:
        return 'keep',None,None,reports
    ranked=sorted(candidates,key=lambda row:(row[0],row[1] in old,row[1]),reverse=True)
    utility,identifier,parameters,roles,report=ranked[0]
    primary=policy.scenarios[identifier]
    if 'compose' in policy.operations and utility>0:
        for other,other_id,other_parameters,other_roles,other_report in ranked[1:]:
            if other<=0 or descriptions[other_id]['goal']==descriptions[identifier]['goal']:
                continue
            secondary=policy.scenarios[other_id]
            if (identifier not in secondary.get('compatible_with',[]) or
                    other_id not in primary.get('compatible_with',[])):
                continue
            joint=required_roles([*primary.get('roles',[]),*secondary.get('roles',[])],agents,preferred)
            if joint is None:
                continue
            pair=sorted((identifier,other_id))
            selected=configuration(pair,{'by_scenario':{identifier:parameters,other_id:other_parameters}})
            if old==pair:
                changed=selected!=policy.previous['selected_configuration'] or joint!=preferred
                operation='modify' if changed else 'keep'
            else:
                operation='compose'
            if operation in policy.operations:
                report['selected']=other_report['selected']=True
                return (operation,selected,joint,reports) if operation!='keep' else ('keep',None,None,reports)
    ids=[identifier]
    selected=configuration(ids,parameters)
    if old==ids:
        if selected==policy.previous['selected_configuration'] and preferred==roles:
            report['selected']=True
            return 'keep',None,None,reports
        operation='modify'
    else:
        try:
            if old and any(identifier not in policy.scenarios.get(name,{}).get('transition_targets',[]) or
                           not applicable(policy.scenarios[name],observation,'exit') for name in old):
                return 'keep',None,None,reports
        except (ProtocolError,ExpressionError,ValueError,TypeError,KeyError):
            return 'keep',None,None,reports
        operation='switch' if old else 'select'
    if operation not in policy.operations:
        return 'keep',None,None,reports
    report['selected']=True
    return operation,selected,roles,reports
