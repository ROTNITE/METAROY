"""New tournament RPC against the unchanged independent standard-library model."""
from collections import Counter
import json
from threading import Thread
from unittest import TestCase

from conformance.check_tournament import check_pair
from conformance.reference_environment import serve
from controller.server import TournamentServer


class TournamentIndependentMatrix(TestCase):
    def test_reference_profiles(self):
        rows = []
        # The reference model predates the new regulation; some profiles
        # deliberately use opaque action/scenario names or centimetres/Y-up.
        # Measure those honestly rather than adapting the independent peer.
        with TournamentServer(0) as controller:
            worker = Thread(target=controller.serve_forever, daemon=True)
            worker.start()
            try:
                for profile in 'ABCDEFGHIJK':
                    for seed in (89725161, 89725187, 89725213):
                        with serve(profile, 0) as environment:
                            peer = Thread(target=environment.serve_forever, daemon=True)
                            peer.start()
                            try:
                                report = check_pair(f'http://127.0.0.1:{environment.server_port}',
                                                    f'http://127.0.0.1:{controller.server_port}',
                                                    seed=seed, max_steps=200, normalize=True, strict=True)
                                trial = environment.trial
                                total = len(getattr(trial, 'targets', {}))
                                handovers = len(getattr(trial, 'private_handover', []))
                                row = {'profile': profile, 'seed': seed, 'admitted': report.admitted,
                                       'status': trial.status if trial.episode else None,
                                       'target_count': total, 'physical_handovers': handovers,
                                       'goal_verified': bool(total and handovers == total and trial.status == 'success'),
                                       'steps': getattr(trial, 't', 0),
                                       'operations': dict(Counter(record['payload']['operation']
                                           for record in getattr(trial, 'journal', [])
                                           if record['record_type'] == 'decision')),
                                       'failures': [item['detail'] for item in report.items
                                                    if item['status'] in ('WARN', 'FAIL')]}
                                rows.append(row)
                                print('TOURNAMENT_REFERENCE ' + json.dumps(row, ensure_ascii=False), flush=True)
                            finally:
                                environment.shutdown()
                                peer.join()
            finally:
                controller.shutdown()
                worker.join()
        # The canonical baseline and ID permutation must deliver every
        # casualty, not merely pass the transport checks. Other profiles are
        # measured independently, with negative results retained in CI logs.
        for row in rows:
            if row['profile'] in 'AB':
                with self.subTest(profile=row['profile'], seed=row['seed']):
                    self.assertTrue(row['admitted'], row)
                    self.assertTrue(row['goal_verified'], row)
        self.assertEqual(len(rows), 33)
