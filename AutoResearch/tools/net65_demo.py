"""Author the same private late-casualty layout for a demo or a paired run."""
from argparse import ArgumentParser
import json
from pathlib import Path

from environment.config import EVENT_DEFAULTS, Settings
from environment.layout import generate, preflight


def demo_settings():
    return Settings.parse({'preset': 'open', 'scouts': 2, 'rescuers': 1, 'people': 3,
                           'horizon': 300., 'communication_range_m': 1000.,
                           'probabilities': dict.fromkeys(EVENT_DEFAULTS['v052'], 0)})


def demo_layout(settings):
    world = generate(settings, include_events=True)
    if settings.people != 3 or settings.horizon < 300:
        raise ValueError('The Net65 demo needs three people and a horizon of at least 300 seconds')
    for person, x in zip(world['people'], (80., 100., 120.)):
        person['position'] = [x, 10., .06]
    world['people'][0]['available_at'] = .66 * settings.horizon
    world['fixture'] = {'id': 'net65_demo', 'late_person_available_at': world['people'][0]['available_at']}
    world['preflight'] = preflight(world)
    return world


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    world = demo_layout(demo_settings())
    with args.output.open('x', encoding='utf-8') as target:
        json.dump(world, target, indent=2, ensure_ascii=False)


if __name__ == '__main__':
    main()
