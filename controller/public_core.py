"""Public capability declarations and coordinate frames for Core clients."""
from copy import deepcopy

from contracts.protocol import ID, NUMBER, OPERATIONS, POINT, ProtocolError, object_schema, validate


STEP_DEDUPLICATION = 'metaevolution.step_deduplication.v1'
RESET_DEDUPLICATION = 'metaevolution.reset_deduplication.v1'
PATROL_ESTIMATES = 'metarescue.patrol_estimates.v1'

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
    def __init__(self, capabilities):
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
        self.short_actions = 'supported_actions' not in capabilities
        self.actions = capabilities.get('supported_actions', {}) or _short_action_schemas(capabilities, self.extensions)
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
        if 'legacy_request_aliases' not in self.extensions or not isinstance(observation, dict):
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
                    entry = matches[0]
            prepared.append(entry)
        return {**observation, 'mission': {**mission, 'constraints': prepared}}

    def view(self, observation):
        if self.identity:
            return observation
        view = deepcopy(observation)
        for key in ('agents', 'targets', 'infrastructure'):
            for entity in view[key]:
                if entity.get('position') is not None:
                    entity['position'] = self._point(entity['position'], self.indices, self.scale)
        for zone in view['zones']:
            geometry = zone['geometry']
            for name in ('min', 'max', 'center'):
                if name in geometry:
                    geometry[name] = self._point(geometry[name], self.indices, self.scale)
        converted_constraints = set()
        for scope in (view, view['mission'], *view['agents']):
            for constraint in scope['constraints']:
                if constraint.get('type') == 'position_bounds' and id(constraint) not in converted_constraints:
                    converted_constraints.add(id(constraint))
                    for field in ('min', 'max'):
                        if field in constraint:
                            constraint[field] = self._point(constraint[field], self.indices, self.scale)
        return view

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
