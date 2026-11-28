"""Narrow opt-in compatibility for redundant public planar domain bounds.

Only canonical agent-scoped rectangular bounds covering the entire declared
land/air domain are supported. Any additional rule retains the old fallback.
The original observation is never modified; PortablePolicy still checks each
issued movement and evaluates the actual hard predicates.
"""
from controller.navigation import Box, point


def supported_bounds(observation):
    try:
        if observation['constraints'] or observation['mission']['constraints']:
            return False
        agents = observation['agents']
        if not any(a['constraints'] for a in agents):
            return True
        media = []
        for zone in observation['zones']:
            if zone['type'] not in ('land', 'air'):
                continue
            geometry = zone['geometry']
            if geometry.get('type', geometry.get('kind')) != 'box':
                return False
            box = Box.parse({**geometry, 'kind': 'box'})
            if box.minimum[2] != 0 or box.maximum[2] != 0:
                return False
            media.append((zone['type'], box))
        if sorted(kind for kind, _ in media) != ['air', 'land']:
            return False
        positions = [point(row['position']) for row in
                     (*agents, *observation['targets'], *observation['infrastructure'])
                     if row.get('position') is not None]
        for agent in agents:
            rules = agent['constraints']
            if not rules:
                continue
            if len(rules) != 1 or agent['kind'] not in ('ground', 'aerial') or agent.get('position') is None:
                return False
            rule = rules[0]
            if (rule.get('type') != 'position_bounds' or rule.get('severity') != 'hard' or
                    rule.get('scope') != {'kind': 'agent', 'agent_id': agent['id']}):
                return False
            bounds = Box.parse({**rule, 'kind': 'box'})
            predicate = {'op': 'and', 'args': [
                {'op': op, 'left': {'path': 'position.' + str(axis)}, 'right': {'value': value}}
                for axis in range(3) for op, value in
                (('ge', bounds.minimum[axis]), ('le', bounds.maximum[axis]))]}
            if rule.get('predicate') != predicate:
                return False
            if any(bounds.interval(p, p) is None for p in positions):
                return False
            if any(bounds.interval(box.minimum, box.minimum) is None or
                   bounds.interval(box.maximum, box.maximum) is None for _, box in media):
                return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False
