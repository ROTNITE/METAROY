"""Deterministic generated observation invariants (not mission success)."""
from copy import deepcopy
from random import Random
from unittest import TestCase

from conformance import check_tournament as checker
from controller.tournament import TournamentPolicy


class TournamentGeneratedProperties(TestCase):
    def test_thousand_normalized_observations(self):
        policy = TournamentPolicy()
        for seed in range(1000):
            with self.subTest(seed=seed):
                rng = Random(seed)
                base = checker.make_synthetic_obs(0, episode_id=f'generated-{seed}',
                                                  disturbed=seed % 5 == 0,
                                                  safe_stop=seed % 11 == 0)
                for agent in base['agents']:
                    agent['position'][:2] = [rng.uniform(1, 70), rng.uniform(1, 70)]
                    agent['resources']['energy']['value'] = rng.uniform(.15, 1.)
                base['targets'][0]['position'][:2] = [rng.uniform(1, 70), rng.uniform(1, 70)]
                expected = TournamentPolicy().decide(base)
                changed = deepcopy(base)
                changed['episode_id'] += '-normalized'
                for agent in changed['agents']:
                    names = [entry['action'] for entry in agent['available_actions']]
                    if seed % 3 == 0:
                        agent['available_actions'] = names
                    elif seed % 3 == 1:
                        agent['available_actions'] = [{'id': name} for name in names]
                    agent.pop('orientation', None)
                    agent['_author_specific'] = {'noise': rng.random()}
                changed['_author_specific'] = {'noise': rng.random()}
                norm = checker.PublicNormalizer(seed=seed)
                public = norm.forward_obs(changed)
                result = policy.decide({'observation': public})
                self.assertFalse(checker.validate_decision_struct(result, public, strict_axis=True)[0])
                self.assertEqual(policy.decide(public), result)
                self.assertEqual(checker._decision_signature(expected),
                                 checker._decision_signature(norm.backward_decision(result)))
                if seed % 11 == 0:
                    self.assertEqual(result['operation'], 'safe_stop')
                    self.assertFalse(result['commands'])
