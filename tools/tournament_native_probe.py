"""Exercise native plan state and safety accounting through the tournament HTTP boundary."""
from argparse import ArgumentParser
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from conformance import check_tournament as checker


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--env-url', required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-safety', type=int, default=0)
    args = parser.parse_args()
    tag = uuid4().hex
    history = []

    def call(method, name, body=None, *, error=None):
        status, result, raw, elapsed = checker.http_json(
            args.env_url, method, '/v1/environment/'+name, body, timeout=15.)
        row = {'endpoint': name, 'http_status': status, 'seconds': elapsed, 'request': body}
        if 200 <= status < 300:
            obs = checker.unwrap_obs(result)
            row.update({key: obs[key] for key in ('episode_id', 'time', 'metrics') if key in obs})
            row['plan'] = obs.get('metaevolution', {}).get('plan')
            row['status'] = result.get('status')
        else:
            row['error'] = result.get('error') if isinstance(result, dict) else raw[:2048]
        history.append(row)
        if error:
            assert 400 <= status < 500 and row['error']['code'] == error, row
        else:
            assert status == 200 and isinstance(result, dict), row
        return result

    def reset(index):
        return call('POST', 'reset', {'protocol_version': '1.0', 'request_id': f'{tag}-reset-{index}',
                                     'seed': args.seed, 'mission': {'id': 'search_and_rescue', 'parameters': {}}})

    try:
        result = reset(0)
        episode = result['episode_id']
        assert result['observation']['metrics']['safety_violations'] == 0
        config = []
        index = 0

        def send(operation, scenario_ids, *, parameters=None, declaration=None):
            nonlocal result, config, index
            index += 1
            request = checker.make_clean_decision(episode, index, result['time'], operation, scenario_ids,
                                                 config, params=parameters, synth=declaration)
            # Prefix all transport IDs in this probe without touching the organizer.
            request['request_id'] = f'{tag}-request-{index}'
            request['decision_id'] = f'{tag}-decision-{index}'
            result = call('POST', 'step', request)
            config = scenario_ids
            assert result['observation']['metaevolution']['plan']['accepted_configuration']['scenario_ids'] == config
            return request

        select = send('select', ['coordination_reconfiguration'])
        replay = call('POST', 'step', select)
        assert replay == result
        reused_request = {**select, 'decision_id': tag+'-conflict', 'time': result['time'],
                          'operation': 'keep', 'previous_configuration': {'scenario_ids': config, 'role_assignments': {}}}
        call('POST', 'step', reused_request, error='INVALID_REQUEST')
        assert call('GET', 'observation')['time'] == 1
        send('modify', config, parameters={'priority_bias': 1.2})
        accepted = result['observation']['metaevolution']['plan']['accepted_configuration']
        assert accepted['parameters'] == {'priority_bias': 1.2}
        send('keep', config, parameters=accepted['parameters'])
        send('compose', ['coordination_reconfiguration', 'coverage_search'])
        send('switch', ['coverage_search'])
        synth = deepcopy(checker.MIN_SYNTH_SCENARIO)
        send('synthesize', [synth['id']], declaration=synth)
        assert result['observation']['metrics']['safety_violations'] == args.expected_safety, history
        send('safe_stop', config)
        for _ in range(12):
            if result['status'] != 'running':
                break
            send('keep', config, declaration=synth)
        assert result['status'] == 'safe_stopped'
        assert result['observation']['metrics']['safety_violations'] == args.expected_safety
        stop_episode = result['episode_id']
        restarted = reset(1)
        assert restarted['episode_id'] != stop_episode
        obs = restarted['observation']
        assert obs['time'] == 0 and obs['metrics']['safety_violations'] == 0
        assert obs['metaevolution']['plan']['accepted_configuration']['scenario_ids'] == []
        call('POST', 'step', select, error='UNKNOWN_EPISODE')
        call('POST', 'close', {'protocol_version': '1.0', 'request_id': tag+'-close',
                               'episode_id': restarted['episode_id']})
    finally:
        args.output.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Native plan, replay, emergency stop, safety metrics and reset passed')


if __name__ == '__main__':
    main()
