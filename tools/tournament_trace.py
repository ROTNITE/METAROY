"""Record HTTP evidence without changing the organizer pair checker."""
from argparse import ArgumentParser
import json
from pathlib import Path
import sys

from conformance import check_tournament


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    args, checker_args = parser.parse_known_args()
    request = check_tournament.http_json
    with args.trace.open('x', encoding='utf-8') as trace:
        def record(url, method, endpoint, payload=None, timeout=10.0):
            code, body, raw, elapsed = request(url, method, endpoint, payload, timeout)
            row = {'endpoint': endpoint, 'method': method, 'http_status': code, 'seconds': elapsed}
            if not 200 <= code < 300:
                row.update(request=payload, response=body if body is not None else raw[:4096])
            elif isinstance(body, dict):
                observation = check_tournament.unwrap_obs(body)
                row.update({key: body[key] for key in ('episode_id', 'request_id', 'time', 'status', 'operation')
                            if key in body})
                if 'metrics' in observation:
                    row['metrics'] = observation['metrics']
                if 'command_results' in body:
                    row['command_results'] = body['command_results']
                if 'targets' in observation:
                    row['observable_target_states'] = [
                        {key: target[key] for key in ('id', 'status', 'evacuation_state', 'carrier_id') if key in target}
                        for target in observation['targets'] if isinstance(target, dict)
                    ]
            trace.write(json.dumps(row, ensure_ascii=False) + '\n')
            trace.flush()
            return code, body, raw, elapsed
        check_tournament.http_json = record
        sys.argv[1:] = checker_args
        return check_tournament.main()


if __name__ == '__main__':
    raise SystemExit(main())
