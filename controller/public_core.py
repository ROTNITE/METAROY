"""Public capability declarations and coordinate frames for Core clients."""
from copy import deepcopy

from contracts.action_semantics import action_bindings
from contracts.protocol import ID, NUMBER, OPERATIONS, POINT, ProtocolError, object_schema, validate


STEP_DEDUPLICATION = 'metaevolution.step_deduplication.v1'
RESET_DEDUPLICATION = 'metaevolution.reset_deduplication.v1'
PATROL_ESTIMATES = 'metarescue.patrol_estimates.v1'
COMPATIBILITY = 'metaroy.wire_compatibility.v1'
CORE_MODES = ('strict', 'declared')

# Shapes described by the public short action declarations. The move form
# appears in the task's protocol example; target service needs an advertised
# legacy action dialect or an explicit parameter declaration.
_BARE_PARAMETER_SCHEMAS = {'move': {'target': POINT}, 'observe': {}, 'inspect': {},
                          'confirm': {'target_id': ID}, 'deliver': {'target_id': ID},
                          'rescue': {'target_id': ID}, 'hold': {}}


def _short_action_schemas(capabilities, extensions):
    result = {}
    actions = capabilities.get('actions', ())
    if not isinstance(actions, list):
        return result
    for action in actions:
        if isinstance(action, str):
            if action != 'move' and 'legacy_request_aliases' not in extensions:
                continue
            properties = _BARE_PARAMETER_SCHEMAS.get(action)
            name = action
        elif isinstance(action, dict) and isinstance(action.get('action'), str):
            name = action['action']
            properties = action.get('parameters')
            if name not in _BARE_PARAMETER_SCHEMAS or not isinstance(properties, dict):
                continue
            if any(not isinstance(value, dict) for value in properties.values()):
                continue
            properties = {key: {'type': 'array', 'minItems': 3, 'maxItems': 3, 'items': NUMBER}
                          if value.get('type') == 'array' and value.get('length') == 3 else value
                          for key, value in properties.items()}
        else:
            continue
        if properties is not None:
            result[name] = {'parameters_schema': object_schema(properties)}
    return result


class PublicCore:
    def __init__(self, capabilities, *, mode='declared', audit=None):
        if mode not in CORE_MODES:
            raise ValueError('Unknown Core compatibility mode')
        self.mode, self.audit = mode, audit
        validate(capabilities, object_schema({
            'protocol_version': {'const': '1.0'},
            'supported_actions': {'type': 'object', 'additionalProperties': {'type': 'object'}},
            'supported_scenarios': {'type': 'object', 'additionalProperties': {'type': 'object'}},
            'supported_operations': {'type': 'array', 'items': {'enum': list(OPERATIONS)}},
            'extensions': {'type': 'array', 'items': {'type': 'string'}},
            'coordinates': {'type': 'object'},
            'coordinate_system': {'type': 'object'},
            'agent_kinds': {'type': 'array', 'items': {'type': 'string'}},
        }, required=['protocol_version']))
        self.extensions = set(capabilities.get('extensions', ()))
        self.allow_space = 'space' in capabilities.get('agent_kinds', ()) and bool(
            self.extensions & {'space', 'space_agent_kind'})
        self.compatibility = set()
        if mode == 'declared' and COMPATIBILITY in self.extensions:
            declared = capabilities.get('wire_compatibility')
            if not isinstance(declared, list) or not all(isinstance(item,str) for item in declared):
                raise ProtocolError('INVALID_REQUEST', 'Expected list of declared wire conversions')
            self.compatibility.update(declared)
        self.short_actions = 'supported_actions' not in capabilities and mode == 'declared'
        declarations = capabilities.get('supported_actions', {})
        if mode=='declared' and not declarations:
            declarations = _short_action_schemas(capabilities, self.extensions)
            if declarations:self._report('short_action_schemas', actions=sorted(declarations))
        self.actions, self.bindings = action_bindings(capabilities, self.extensions, declarations)
        self.semantic_ids = {value.wire: key for key,value in self.bindings.items()}
        self.wire_actions = frozenset(declarations)
        # An explicitly unknown intent must not masquerade as a canonical
        # action merely because its opaque wire ID happens to be the same.
        self.unsupported_semantics = (frozenset(capabilities['action_semantics']) - self.semantic_ids.keys()
                                      if 'metaroy.action_semantics.v1' in self.extensions else frozenset())

        coordinates = capabilities.get('coordinates', {})
        declared_frame = capabilities.get('coordinate_system', {})
        unit = coordinates.get('unit', declared_frame.get('units', 'meter'))
        axes = coordinates.get('axes', ['x', 'y', 'z'])
        if axes == ['x', 'up', 'z']:
            axes = ['x', 'z', 'y']
        scales = {'meter': 1., 'meters': 1., 'metre': 1., 'metres': 1., 'm': 1.,
                  'centimeter': .01, 'centimetre': .01, 'cm': .01}
        if (not isinstance(unit, str) or unit not in scales or not isinstance(axes, list) or
                len(axes) != 3 or any(not isinstance(axis, str) for axis in axes) or set(axes) != {'x', 'y', 'z'}):
            raise ProtocolError('INVALID_REQUEST', 'Unsupported public coordinate declaration')
        self.scale = scales[unit]
        self.indices = tuple(axes.index(axis) for axis in ('x', 'y', 'z'))
        self.inverse = tuple(self.indices.index(i) for i in range(3))
        self.identity = self.scale == 1. and self.indices == (0, 1, 2)

    def wire_observation(self, observation):
        # The legacy request-alias profile names constraints by public type or
        # ID. Resolve only unique matches to structured declarations.
        if self.mode != 'declared' or not isinstance(observation, dict):
            return observation
        if self.compatibility:
            observation = self._normalize_declaration(observation)
        if 'legacy_request_aliases' not in self.extensions:
            return observation
        mission = observation.get('mission')
        if not isinstance(mission, dict) or not isinstance(mission.get('constraints'), list):
            return observation
        constraints = observation.get('constraints', ())
        if not isinstance(constraints, list):
            return observation
        prepared = []
        for entry in mission['constraints']:
            if isinstance(entry, str):
                matches = [row for row in constraints if isinstance(row, dict) and
                           (row.get('id') == entry or row.get('type') == entry)]
                if len(matches) == 1:
                    self._report('mission_constraint_reference', observation=observation, reference=entry)
                    entry = matches[0]
            prepared.append(entry)
        return {**observation, 'mission': {**mission, 'constraints': prepared}}

    def _report(self, transformation, *, observation=None, **details):
        if self.audit:
            if observation:
                details.update(episode_id=observation.get('episode_id'),request_id=observation.get('request_id'))
            self.audit({'conversion':transformation, **details})

    def _normalize_declaration(self, observation):
        prepared = {}
        for collection in ('agents','infrastructure'):
            if not isinstance(observation.get(collection),list):
                continue
            changes = []
            for entry in observation[collection]:
                if not isinstance(entry,dict):
                    changes.append(entry)
                    continue
                updated = entry.copy()
                resources = entry.get('resources')
                if 'resource_id_from_key' in self.compatibility and isinstance(resources,dict):
                    replacements = {}
                    for identifier, resource in resources.items():
                        if (isinstance(resource,dict) and 'id' not in resource and resource.get('unit')=='normalized'
                                and all(type(resource.get(k)) in (int,float) for k in ('minimum','value','maximum'))
                                and 0<=resource['minimum']<=resource['value']<=resource['maximum']<=1):
                            resource = {**resource,'id':identifier}
                            self._report('resource_id_from_key',observation=observation,entity_id=entry.get('id'),resource_id=identifier)
                        replacements[identifier] = resource
                    updated['resources'] = replacements
                actions = entry.get('available_actions')
                if 'short_available_actions' in self.compatibility and isinstance(actions,list):
                    converted = [{'action': action} if isinstance(action,str) and
                                 action in self.wire_actions else action for action in actions]
                    if converted != actions:
                        self._report('short_available_actions',observation=observation,entity_id=entry.get('id'))
                        updated['available_actions'] = converted
                changes.append(updated)
            prepared[collection] = changes
        return {**observation, **prepared}

    def view(self, observation):
        if self.identity and not self.semantic_ids and not self.unsupported_semantics:
            return observation
        view = deepcopy(observation)
        if self.semantic_ids or self.unsupported_semantics:
            for agent in view['agents']:
                actions = []
                for action in agent['available_actions']:
                    wire = action['action']
                    if wire in self.unsupported_semantics or (wire in self.bindings and wire not in self.wire_actions):
                        continue
                    actions.append({**action, 'action': self.semantic_ids.get(wire, wire)})
                agent['available_actions'] = actions
        if self.identity:
            return view
        self._report('coordinate_view', observation=observation, scale=self.scale, indices=self.indices)
        for key in ('agents', 'targets', 'infrastructure'):
            for entity in view[key]:
                if entity.get('position') is not None:
                    entity['position'] = self._point(entity['position'], self.indices, self.scale)
        for zone in view['zones']:
            self._geometry(zone['geometry'])
        converted_constraints = set()
        for scope in (view, view['mission'], *view['agents']):
            for constraint in scope['constraints']:
                if constraint.get('type') == 'position_bounds' and id(constraint) not in converted_constraints:
                    converted_constraints.add(id(constraint))
                    for field in ('min', 'max'):
                        if field in constraint:
                            constraint[field] = self._point(constraint[field], self.indices, self.scale)
        return view

    def _geometry(self, geometry):
        for name in ('min','max','center'):
            if name in geometry:
                geometry[name] = self._point(geometry[name],self.indices,self.scale)
        for name in ('radius','z_min','z_max'):
            if type(geometry.get(name)) in (float,int):
                geometry[name] *= self.scale
        if 'parts' in geometry and isinstance(geometry['parts'],list):
            for part in geometry['parts']:
                if isinstance(part,dict):self._geometry(part)

    def wire_action(self, action):
        binding = self.bindings.get(action)
        return binding.wire if binding else action

    def command_parameters(self, action, parameters):
        value = self.parameters(action,parameters)
        binding = self.bindings.get(action)
        if not binding:
            return value
        mapped = {binding.names.get(key,key):item for key,item in value.items()}
        if len(mapped) != len(value):
            raise ProtocolError('INVALID_COMMAND','Ambiguous mapped command argument')
        return mapped

    @staticmethod
    def _point(values, indices, scale):
        if not isinstance(values, (list, tuple)) or len(values) != 3 or any(type(v) not in (int, float) for v in values):
            raise ProtocolError('INVALID_REQUEST', 'Invalid public spatial point')
        return [values[i] * scale for i in indices]

    def parameters(self, action, parameters):
        if self.identity:
            return parameters
        if action == 'move':
            return {**parameters, 'target': self._point(parameters['target'], self.inverse, 1. / self.scale)}
        if action == 'follow_route':
            return {**parameters, 'waypoints': [self._point(p, self.inverse, 1. / self.scale)
                                              for p in parameters['waypoints']]}
        if action == 'observe' and 'radius' in parameters:
            return {**parameters, 'radius': parameters['radius'] / self.scale}
        return parameters
