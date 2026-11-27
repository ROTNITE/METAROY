"""Opt-in disaster integration and legacy isolation."""
from copy import deepcopy
import unittest

from contracts.protocol import ProtocolError
from environment.config import Settings, EVENT_DEFAULTS
from environment.events import DETAILED_PHYSICS, REVISED_PHYSICS, generate_tape, validate_tape
from environment.layout import generate, fingerprint, public_map


class RevisedDisasterTests(unittest.TestCase):
    def test_legacy_serialization_and_primary_trials_stay_unchanged(self):
        for profile in ('v052','v3'):
            values={'city_revision':'2.3','seed':2026,'boats':0,'horizon':480.,
                    'event_profile':profile,'probabilities':dict.fromkeys(EVENT_DEFAULTS[profile],100)}
            with self.subTest(profile=profile):
                old=generate(Settings.parse(values),include_events=True)
                same=generate(Settings.parse({**values,'disaster_revision':0}),include_events=True)
                self.assertEqual(fingerprint(old),fingerprint(same))
                self.assertNotIn('disaster_revision',old['settings'])
                new=generate(Settings.parse({**values,'disaster_revision':1}),include_events=True)
                self.assertEqual(public_map(old),public_map(new))
                self.assertEqual(old['event_model']['physics_profile'],DETAILED_PHYSICS)
                tape=new['event_model']
                self.assertEqual(tape['physics_profile'],REVISED_PHYSICS)
                def primary(data):
                    return [r for r in data['events'] if not r['spread'] and 'cause' not in r]
                self.assertEqual(primary(old['event_model']),primary(tape))
                self.assertEqual(tape,generate_tape(new))
                self.assertTrue(new['preflight']['passed'])
                rows={r['id']:r for r in tape['events']}
                children=[r for r in tape['events'] if 'cause' in r]
                self.assertTrue(children)
                for child in children:
                    parent=rows[child['parent_id']]
                    self.assertEqual(child['source_object_id'],parent['source_object_id'])
                    self.assertLess(child['time'],parent['expires_at'])
                    bad=deepcopy(tape)
                    next(r for r in bad['events'] if r['id']==child['id'])['source_object_id']='unknown'
                    with self.assertRaises(ProtocolError): validate_tape(bad,new)
                for row in rows.values():
                    if row['kind']=='fire' and row['spread']:
                        self.assertEqual(row['expires_at'],rows[row['parent_id']]['expires_at'])
                with self.assertRaises(ProtocolError): validate_tape(tape,old)

    def test_opt_in_only_in_23(self):
        for v in (True,2,-1):
            with self.assertRaises(ProtocolError):Settings.parse({'city_revision':'2.3','disaster_revision':v})
        with self.assertRaises(ProtocolError):Settings.parse({'city_revision':'2.2','disaster_revision':1})

    def test_zero_probability_creates_no_failures(self):
        s=Settings.parse({'city_revision':'2.3','disaster_revision':1,'boats':0,
                          'probabilities':dict.fromkeys(EVENT_DEFAULTS['v052'],0)})
        w=generate(s,include_events=True)
        self.assertEqual(w['event_model']['events'],[])
        validate_tape(w['event_model'],w)

    def test_scale_and_combined_hazards(self):
        for size in (25,75,150):
            with self.subTest(size=size):
                settings=Settings.parse({'city_revision':'2.3','disaster_revision':1,
                    'map_size':size,'seed':2026,'horizon':480.,'event_profile':'v3',
                    'probabilities':dict.fromkeys(EVENT_DEFAULTS['v3'],100),
                    'boats':0,**({'scouts':48,'rescuers':40,'people':600} if size==150 else {})})
                world=generate(settings,include_events=True)
                tape=world['event_model']
                self.assertTrue(world['preflight']['passed'])
                self.assertEqual(tape,validate_tape(tape,world))
                admitted={r['kind'] for r in tape['events'] if not r['rejection']}
                self.assertTrue({'fire','collapse','smoke','road_damage','flood','mud'} <= admitted)
                self.assertNotIn('event_model',public_map(world))
