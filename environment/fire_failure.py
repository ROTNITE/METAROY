"""Deterministic fire-induced structural failure for the opt-in v2 profile.

A bounded game damage model, not structural engineering. A derived collapse
is a consequence of a scheduled fire, not another probability trial.
"""
from copy import deepcopy
import math

CAUSE = 'fire_structural_failure'
DELAYS = (110.0, 85.0, 65.0, 55.0)


def failure_delay(building):
    variant = building.get('variant')
    floors = building.get('floors')
    if type(variant) is int and 0 <= variant < len(DELAYS) and type(floors) is int and floors >= 2:
        return DELAYS[variant]
    return None


def expected_failure(parent, building, horizon):
    delay = failure_delay(building)
    if (delay is None or parent.get('kind') != 'fire' or parent.get('spread') or
            parent.get('rejection') or parent.get('source_object_id') != building.get('id')):
        return None
    at = round(parent['time'] + delay, 8)
    if at >= horizon or parent.get('expires_at') is None or at >= parent['expires_at']:
        return None
    source = parent['source']
    cx, cy = (math.floor(v / 5.) for v in source[:2])
    cells = sorted([list(c) for c in parent['cells'] if (c[0]-cx)**2 + (c[1]-cy)**2 <= 5])
    if not cells:
        return None
    return {'id': parent['id'] + '-heat-collapse', 'kind': 'collapse', 'time': at,
            'source': list(source), 'source_object_id': building['id'],
            'parent_id': parent['id'], 'spread': False, 'cause': CAUSE,
            'cells': cells, 'expires_at': None, 'visibility_expires_at': at+24.,
            'rejection': None, 'smoke_emission_height_m': max(0., source[2]+.15)}


def append_fire_failures(tape, world, horizon):
    buildings = {b['id']: b for b in world['buildings']}
    claimed = set()
    children = []
    for parent in sorted(tape['events'], key=lambda row: (row['time'], row['id'])):
        site = parent.get('source_object_id')
        if site in claimed or site not in buildings:
            continue
        child = expected_failure(parent, buildings[site], horizon)
        if child is not None:
            children.append(child)
            claimed.add(site)
    tape['events'].extend(children)
    tape['events'].sort(key=lambda row: (row['time'], row['spread'], row['id']))


def valid_fire_failure(child, parent, building, horizon):
    expected = expected_failure(parent, building, horizon)
    if expected is None:
        return False
    comparable = deepcopy(child)
    try:
        comparable['cells'] = sorted(comparable['cells'])
    except (KeyError, TypeError):
        return False
    return comparable == expected
