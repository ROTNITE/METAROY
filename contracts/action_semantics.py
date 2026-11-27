"""Explicit mappings for optional public action semantics.

The official Core action remains an opaque wire ID. This optional declaration
binds a recognized intent and its argument names to one public action schema.
"""
from dataclasses import dataclass

from contracts.protocol import ProtocolError


ACTION_SEMANTICS = 'metaroy.action_semantics.v1'
CANONICAL_ACTIONS = frozenset(('move','follow_route','observe','inspect','confirm','deliver','pickup',
                              'handover','rescue','hold','charge','relay','send_message'))


@dataclass(frozen=True)
class ActionBinding:
    wire: str
    names: dict[str, str]


def action_bindings(capabilities, extensions, declarations):
    """Resolve unique declared semantic bindings. Preserve other wire actions."""
    if ACTION_SEMANTICS not in extensions:
        return declarations, {}
    semantics = capabilities.get('action_semantics')
    if not isinstance(semantics, dict):
        raise ProtocolError('INVALID_REQUEST', 'Expected an object for declared action semantics')
    aliases = {}
    occupied = {wire for wire in declarations if wire in CANONICAL_ACTIONS and wire not in semantics}
    for wire, description in semantics.items():
        if wire not in declarations or not isinstance(description, dict):
            raise ProtocolError('INVALID_REQUEST', 'Action semantics refer to an undeclared action')
        name = description.get('semantic_id')
        if not isinstance(name, str):
            raise ProtocolError('INVALID_REQUEST', 'Invalid action semantic identifier')
        if name not in CANONICAL_ACTIONS:
            continue
        names = description.get('bindings', {})
        schema = declarations[wire].get('parameters_schema', {})
        properties = schema.get('properties', {}) if isinstance(schema, dict) else {}
        if (not isinstance(names, dict) or not isinstance(properties, dict)
                or not all(isinstance(key, str) and key and isinstance(value, str) and value in properties
                           for key, value in names.items())
                or len(names) != len(set(names.values())) or name in occupied):
            raise ProtocolError('INVALID_REQUEST', 'Ambiguous action semantic binding')
        occupied.add(name)
        aliases[name] = ActionBinding(wire, dict(names))
    mapped = {wire: item for wire, item in declarations.items() if wire not in semantics}
    mapped.update({name: declarations[binding.wire] for name, binding in aliases.items()})
    return mapped, aliases


def declared_goal_effects(capabilities):
    """Only explicitly described result semantics may certify a goal."""
    if ACTION_SEMANTICS not in capabilities.get('extensions', ()):
        return {}
    descriptions=capabilities.get('action_semantics', {})
    if not isinstance(descriptions,dict):
        return {}
    result = {}
    for wire, item in descriptions.items():
        if isinstance(item, dict) and item.get('effect') in ('aid_delivered', 'evacuated', 'located'):
            result[wire] = item['effect']
    return result
