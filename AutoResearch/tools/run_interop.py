"""Run and archive a two-sided Core Protocol compatibility trial."""
from __future__ import annotations

import argparse
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import http.client
import math
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.protocol import DECISION, MAX_RESPONSE_BYTES, ProtocolError, encode, strict_json, validate
from controller.ai import atomic_json, file_lock
from controller.client import EnvironmentClient, TransportFailure
from controller.health import require_port_available
from controller.net65 import PATROL_MODES
from controller.public_core import PublicCore
from environment.main import DEFAULT_OUTPUT, existing_unreal, native_command
from environment.server import PlanarServer
from environment.world import CASES
from tools.native_archive import file_sha256, verify_archive
from tools.run_mission import hashes as native_hashes


SEEDS = (17331, 17332, 17333)


def percentile(samples, fraction):
    if not samples:
        return None
    return sorted(samples)[math.ceil(len(samples) * fraction) - 1]


class Evidence:
    def __init__(self):
        self.transfers = {}
        self.step_ms = []
        self.planning_ms = []
        self.first_error = None
        self.decisions = OrderedDict()
        self.last_decision_id = None
        self.completed_targets = set()

    def record(self, kind, data):
        if kind == 'decision':
            decision = data['decision']
            identifier = decision.get('decision_id')
            if isinstance(identifier, str):
                self.decisions[identifier] = decision
                self.last_decision_id = identifier
                if len(self.decisions) > 512:
                    self.decisions.popitem(last=False)
            if 'planning_wall_s' in data:
                self.planning_ms.append(data['planning_wall_s'] * 1000)
        if kind == 'http_exchange' and data.get('path') == '/v1/environment/step':
            self.step_ms.append(data['elapsed_s'] * 1000)
        if kind == 'step_response':
            observation = data.get('observation', {})
            finished = {(target['id'], target['status']) for target in observation.get('targets', ())
                        if target.get('id') and target.get('status') in ('rescued', 'delivered')}
            new_targets = finished - self.completed_targets
            self.completed_targets.update(finished)
            current = self.decisions.get(self.last_decision_id, {})
            for receipt in data['command_results']:
                identifier, index = receipt.get('decision_id'), receipt.get('command_index')
                submitted = self.decisions.get(identifier, {}) if isinstance(identifier, str) else {}
                commands = submitted.get('commands', ())
                if (receipt.get('validation_status') == 'accepted' and receipt.get('delivery_status') == 'delivered'
                        and receipt.get('execution_status') == 'completed' and receipt.get('action') in ('handover', 'deliver', 'rescue')
                        and type(index) is int and 0 <= index < len(commands)
                        and all(commands[index][key] == receipt.get(key) for key in ('agent_id', 'action'))):
                    self.transfers[(identifier, index)] = receipt
                if (receipt.get('status') in ('applied', 'accepted') and receipt.get('action') in ('rescue', 'deliver') and
                        data.get('request_id') == current.get('request_id') and current):
                    indices = [(index, cmd['parameters'].get('target_id'))
                               for index, cmd in enumerate(current.get('commands', ()))
                               if cmd['agent_id'] == receipt.get('agent_id') and cmd['action'] == receipt['action']]
                    if len(indices) == 1 and (indices[0][1], 'rescued' if receipt['action'] == 'rescue' else 'delivered') in new_targets:
                        index, target_id = indices[0]
                        key = (current['decision_id'], index)
                        self.transfers[key] = {'decision_id': key[0], 'command_index': index, 'target_id': target_id,
                                               'basis': 'public_target_transition', 'receipt': receipt,
                                               'observation_request_id': observation.get('request_id')}
        if self.first_error is None and kind in ('controller_failure', 'environment_failure',
                                                 'environment_rejection', 'uncertain_step'):
            self.first_error = {'kind': kind, **data}

    def summary(self):
        return {'confirmed_command_receipts': list(self.transfers.values()), 'first_protocol_error': self.first_error,
                'planning_p95_ms': percentile(self.planning_ms, .95), 'step_http_p95_ms': percentile(self.step_ms, .95)}


def public_logs(client, episode, destination, evidence=None):
    cursor = None
    seen = set()
    with destination.open('xb') as target:
        while True:
            page = client.logs(episode, cursor=cursor)
            if page.get('episode_id') != episode or not isinstance(page.get('records'), list):
                raise ProtocolError('INVALID_REQUEST', 'Public journal has a different episode or no records array')
            target.write(encode(page) + b'\n')
            if evidence is not None:
                for record in page['records']:
                    if record.get('type') == 'step' and 'response' in record:
                        if 'decision' in record:
                            evidence.record('decision', {'decision': record['decision']})
                        evidence.record('step_response', record['response'])
                    if record.get('record_type') == 'decision':
                        evidence.record('decision', {'decision': record['payload']})
                    if record.get('record_type') == 'command_result':
                        evidence.record('step_response', {'command_results': [record['payload']]})
            cursor = page.get('next_cursor')
            if cursor is None or not page['records']:
                break
            if type(cursor) not in (str, int) or cursor in seen or len(seen) >= 100000:
                raise ProtocolError('INVALID_REQUEST', 'Invalid or repeated journal cursor')
            seen.add(cursor)


def assert_ready(health, component):
    if health.get('protocol_version') != '1.0' or health.get('component') != component:
        raise ProtocolError('INVALID_REQUEST', f'Invalid {component} health profile')


def scenario_parameter_error(decision, capabilities):
    """Explain a rejected parameter set against the advertised public schema."""
    config = decision['selected_configuration']
    ids = config['scenario_ids']
    parameters = config['parameters']
    catalog = capabilities.get('supported_scenarios', {})
    for scenario_id in ids:
        schema = catalog.get(scenario_id, {}).get('parameter_schema')
        if not isinstance(schema, dict):
            continue
        path = ['selected_configuration', 'parameters']
        scenario_parameters = parameters
        if len(ids) > 1:
            group = parameters.get('by_scenario')
            if not isinstance(group, dict) or scenario_id not in group:
                continue
            path.extend(('by_scenario', scenario_id))
            scenario_parameters = group[scenario_id]
        try:
            validate(scenario_parameters, schema, 'INVALID_SCENARIO')
        except ProtocolError as error:
            details = error.details or {}
            relative_path = details.get('path', [])
            rule = details.get('rule')
            mismatch = {'scenario_id': scenario_id, 'path': path + relative_path, 'rule': rule}
            if (rule == 'additionalProperties' and not relative_path and isinstance(scenario_parameters, dict)
                    and 'patternProperties' not in schema):
                mismatch['unexpected_keys'] = sorted(scenario_parameters.keys() - schema.get('properties', {}).keys())
            return mismatch
    return None


def run_controller(url, output, seed, mode, mission, timeout, portable_dispatch="greedy"):
    output.mkdir(exist_ok=False)
    params = output / 'mission-parameters.json'
    atomic_json(params, mission['parameters'])
    report = {'seed': seed, 'environment': url, 'mission': mission, 'scout_patrol': mode, 'portable_dispatch': portable_dispatch}
    client = EnvironmentClient(url, timeout_s=10)
    evidence = Evidence()
    try:
        health = client.health()
        assert_ready(health, 'environment')
        if health.get('status') != 'ready':
            raise ProtocolError('INVALID_REQUEST', 'Environment is not ready')
        caps = client.capabilities()
        PublicCore(caps)
        atomic_json(output / 'capabilities.json', caps)
        command = [sys.executable, '-B', '-X', 'utf8', '-m', 'controller.main', '--environment', url,
                   '--health-port', '0', '--seed', str(seed), '--scout-patrol', mode, '--ai-mode', 'OFF',
                   '--public-core', '--portable-dispatch', portable_dispatch, '--mission-id', mission['id'], '--mission-parameters', str(params),
                   '--output', str(output / 'controller')]
        with (output / 'controller.log').open('xb') as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        report['exit_code'] = result.returncode
        journal = output / 'controller' / 'manifest.json'
        if not journal.is_file():
            raise RuntimeError('Controller did not write a run manifest')
        manifest = strict_json(journal.read_bytes())
        journal = journal.with_name(manifest['journal'])
        if journal.is_file():
            with gzip.open(journal, 'rb') as stream:
                for line in stream:
                    row = strict_json(line, maximum_bytes=128 * 1024 * 1024)
                    evidence.record(row['record_type'], row['payload'])
        report.update(evidence.summary(), status=manifest.get('episode_status'), metrics=manifest.get('metrics'),
                      patrol=manifest.get('patrol'), episode_id=manifest.get('episode_id'))
        if result.returncode != 0:
            raise RuntimeError(manifest.get('error', 'Controller failed. See controller.log.'))
        if manifest.get('close_error'):
            raise RuntimeError(manifest['close_error'])
        public_logs(client, manifest['episode_id'], output / 'environment-logs.jsonl')
        report['exchange'] = 'passed'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report.update(exchange='failed', error=f'{type(error).__name__}: {error}')
        if not report.get('first_protocol_error'):
            report['first_protocol_error'] = {'message': str(error), 'details': getattr(error, 'details', {})}
    finally:
        client.disconnect()
        atomic_json(output / 'report.json', report)
    return report


def controller_call(origin, endpoint, body=None, *, timeout=10):
    target = EnvironmentClient(origin, timeout_s=timeout)
    transport = http.client.HTTPSConnection if target.target.scheme == 'https' else http.client.HTTPConnection
    connection = transport(target.target.hostname, target.target.port, timeout=timeout)
    try:
        connection.request('GET' if body is None else 'POST', '/v1/controller/' + endpoint,
                           body=encode(body) if body is not None else None,
                           headers={'Content-Type': 'application/json', 'Connection': 'close'})
        reply = connection.getresponse()
        data = strict_json(reply.read(MAX_RESPONSE_BYTES + 1), maximum_bytes=MAX_RESPONSE_BYTES)
        if not isinstance(data, dict):
            raise ProtocolError('INVALID_REQUEST', 'Controller reply must be an object')
        if reply.status >= 400:
            error = data.get('error', {})
            if not isinstance(error, dict):
                raise ProtocolError('INVALID_REQUEST', 'Malformed controller error')
            raise ProtocolError(error.get('code', 'INTERNAL_ERROR'), error.get('message', f'HTTP {reply.status}'),
                                error.get('details'))
        return data
    finally:
        connection.close()


def wait_environment(client, process, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Native environment exited during startup ({process.returncode})')
        try:
            health = client.health()
            assert_ready(health, 'environment')
            if health.get('status') == 'ready':
                return
        except (OSError, TransportFailure):
            pass
        time.sleep(.25)
    raise TimeoutError('Native environment readiness timed out')


@contextmanager
def planar(*, estimates=True, port=0):
    with PlanarServer(port, estimates=estimates) as server:
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            server.shutdown()
            worker.join()


@contextmanager
def native(output, args):
    with file_lock(DEFAULT_OUTPUT / 'locks/unreal-owner.lock'):
        if existing_unreal():
            raise RuntimeError('Close the running Unreal instance before starting a compatibility trial')
        require_port_available(args.environment_port)
        command = native_command(layout=args.layout, port=args.environment_port, output=output,
                                 visible=args.visible, headless=not args.visible)
        exit_request = output / 'exit-native'
        command.append('-MRCoreExitRequestFile=' + str(exit_request))
        with (output / 'environment-stdout.log').open('xb') as journal:
            with subprocess.Popen(command, cwd=ROOT, stdout=journal, stderr=subprocess.STDOUT) as process:
                url = f'http://127.0.0.1:{args.environment_port}'
                client = EnvironmentClient(url, timeout_s=2)
                try:
                    wait_environment(client, process, min(600, args.timeout))
                    yield url
                finally:
                    client.disconnect()
                    if process.poll() is None:
                        exit_request.touch(exist_ok=False)
                        try:
                            process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                            process.wait(timeout=30)


def run_external(args, client, output, mission):
    caps = client.capabilities()
    PublicCore(caps)
    atomic_json(output / 'capabilities.json', caps)
    reset = caps.get('reset_parameters', {})
    seed = args.seed if args.seed is not None else reset.get('seed', SEEDS[0])
    if reset.get('startup_configuration_only') and (
            seed != reset.get('seed') or mission['parameters']):
        raise ProtocolError('INVALID_REQUEST', f'Native launch accepts its startup seed {reset["seed"]} and empty parameters')
    print(f'Core environment: http://127.0.0.1:{args.environment_port}', flush=True)
    if args.mode == 'driver':
        print(f'Autonomous controller owns reset/step/close. Requested seed: {seed}', flush=True)
    started = time.monotonic()
    while True:
        try:
            health = controller_call(args.controller, 'health')
            assert_ready(health, 'controller')
            if health.get('status') in ('ready', 'degraded'):
                break
            if args.mode == 'decide':
                raise ProtocolError('INVALID_REQUEST', 'Controller is not ready')
        except (OSError, http.client.HTTPException):
            if args.mode == 'decide':
                raise
        if time.monotonic() - started >= args.timeout:
            raise TimeoutError('External controller health did not become ready')
        time.sleep(.25)
    atomic_json(output / 'controller-health.json', health)
    last_feedback = None
    episode = None
    final = None
    failure = None
    stage = 'reset'
    submitted = None
    model_time = None
    evidence = Evidence()
    with (output / 'exchange.jsonl').open('xb') as journal:
        def record(kind, body):
            journal.write(encode({'kind': kind, 'data': body}) + b'\n')
            journal.flush()
            evidence.record(kind, body)

        try:
            if args.mode == 'decide':
                result = client.reset(seed=seed, mission=mission)
                episode = result['episode_id']
                record('reset_response', result)
                while result['status'] == 'running' and time.monotonic() - started < args.timeout:
                    observation = result['observation']
                    stage = 'decide'
                    submitted = None
                    model_time = observation['time']
                    payload = {'protocol_version': '1.0', 'observation': observation, 'capabilities': caps,
                               'feedback': result if last_feedback is not None else None}
                    if args.decide_profile == 'metaroi-v9':
                        payload = {'profile': 'core_public_v1', 'policy': 'adaptive', 'vertical_axis': 'z',
                                   'feedback': last_feedback,
                                   'observation': {k: v for k, v in observation.items() if k not in ('metaevolution', 'extensions')},
                                   'capabilities': {k: v for k, v in caps.items() if k != 'extensions'}}
                    before = time.perf_counter()
                    timeout = min(args.decision_timeout, max(.001, args.timeout - (time.monotonic() - started)))
                    try:
                        answer = controller_call(args.controller, args.decide_path, payload, timeout=timeout)
                    except TimeoutError as error:
                        raise TimeoutError(f'/v1/controller/{args.decide_path} exceeded {timeout:g} s '
                                           f'at model time {observation["time"]}') from error
                    stage = 'decision_schema'
                    record('decision', {'decision': answer, 'planning_wall_s': time.perf_counter() - before})
                    validate(answer, DECISION)
                    submitted = answer
                    stage = 'step'
                    before = time.perf_counter()
                    result = client.step(answer)
                    evidence.step_ms.append((time.perf_counter() - before) * 1000)
                    record('step_response', result)
                    stage = 'step_response'
                    if (result['episode_id'] != episode or result['request_id'] != answer['request_id'] or
                            result['time'] < observation['time'] or
                            result['status'] == 'running' and result['time'] <= observation['time']):
                        raise ProtocolError('INVALID_REQUEST', 'Step response does not correlate with the submitted decision')
                    last_feedback = {'decision_id': answer['decision_id'], 'step_completed': True,
                                     'submitted_decision_id': answer['decision_id'], 'command_results': result['command_results']}
                if result['status'] == 'running':
                    stage = 'episode'
                    raise TimeoutError('Decide trial exceeded the wall timeout')
                final = {'episode_id': episode, 'status': result['status'], 'time': result['time'],
                         'metrics': result['observation']['metrics']}
                stage = 'logs'
                public_logs(client, episode, output / 'environment-logs.jsonl')
            else:
                stage = 'observation'
                while time.monotonic() - started < args.timeout:
                    try:
                        observation = client.observation()
                        if observation['mission']['status'] in ('completed', 'failed', 'safe_stopped'):
                            episode = observation['episode_id']
                            final = {'episode_id': episode, 'status': observation['mission']['status'],
                                     'time': observation['time'], 'metrics': observation['metrics']}
                            stage = 'logs'
                            public_logs(client, episode, output / 'environment-logs.jsonl', evidence)
                            break
                    except ProtocolError as error:
                        if error.code != 'UNKNOWN_EPISODE':
                            raise
                    time.sleep(.25)
                if final is None:
                    raise TimeoutError('External autonomous controller did not reach a terminal observation')
        except (OSError, ValueError, RuntimeError, http.client.HTTPException) as error:
            failure = str(error)
            context = {'message': failure, 'details': getattr(error, 'details', None) or {}, 'stage': stage}
            if model_time is not None:
                context['model_time'] = model_time
            if submitted is not None and stage in ('step', 'step_response'):
                context.update(decision_id=submitted['decision_id'], request_id=submitted['request_id'])
            kind = 'controller_failure' if stage in ('decide', 'decision_schema', 'episode') else 'environment_failure'
            if isinstance(error, ProtocolError):
                context['code'] = error.code
                if stage in ('reset', 'step'):
                    kind = 'environment_rejection'
                if stage == 'step' and error.code == 'INVALID_SCENARIO':
                    mismatch = scenario_parameter_error(submitted, caps)
                    if mismatch:
                        context['scenario_parameters'] = mismatch
            elif stage == 'step' and isinstance(error, TransportFailure) and error.state_may_have_changed:
                kind = 'uncertain_step'
            record(kind, context)
            if episode and not (output / 'environment-logs.jsonl').exists():
                try:
                    public_logs(client, episode, output / 'environment-logs.jsonl')
                except (OSError, ValueError, RuntimeError) as log_error:
                    record('environment_failure', {'message': str(log_error), 'stage': 'logs'})
        finally:
            if episode and args.mode == 'decide':
                try:
                    record('close_response', client.close(episode))
                except (OSError, ValueError, RuntimeError) as error:
                    record('environment_failure', {'message': str(error), 'stage': 'close'})
    report = {**(final or {}), **evidence.summary(), 'seed': seed,
              'exchange': 'failed' if evidence.first_error else 'passed'}
    if failure:
        report['error'] = failure
    if args.engine == 'unreal' and episode:
        archive = DEFAULT_OUTPUT / 'core-runs' / episode
        if args.mode == 'driver':
            for _ in range(60):
                if strict_json((archive / 'manifest.json').read_bytes())['state'] == 'complete':
                    break
                time.sleep(.25)
        try:
            report['native_archive'] = verify_archive(archive)
        except (OSError, ValueError) as error:
            report['exchange'] = 'failed'
            report['archive_error'] = str(error)
            if report['first_protocol_error'] is None:
                report['first_protocol_error'] = {'message': str(error), 'stage': 'archive'}
    return report


def self_test(output, matrix, timeout):
    cases = CASES if matrix else ('late_target',)
    seeds = SEEDS if matrix else SEEDS[:1]
    runs = []
    with planar() as url:
        for case in cases:
            for seed in seeds:
                mission = {'id': 'search_and_rescue', 'parameters': {'case': case, 'opaque': seed == SEEDS[-1]}}
                for mode in PATROL_MODES:
                    runs.append(run_controller(url, output / f'{case}-{seed}-{mode}', seed, mode, mission, timeout))
    with planar(estimates=False) as url:
        mission = {'id': 'search_and_rescue', 'parameters': {'case': 'normal', 'opaque': True}}
        stripped = run_controller(url, output / 'minimal-core', seeds[0], 'net65', mission, timeout)
    late = [run for run in runs if run['mission']['parameters']['case'] == 'late_target' and run['scout_patrol'] == 'net65']
    passed = (all(run['exchange'] == 'passed' for run in runs + [stripped]) and
              stripped['metrics']['confirmed_evacuations'] == 1 and
              all(run['metrics']['confirmed_evacuations'] == 2 and run['patrol']['net65_repeats'] > 0 for run in late))
    return {'exchange': 'passed' if passed else 'failed', 'runs': runs, 'minimal_core': stripped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--output', type=Path)
    common.add_argument('--timeout', type=float, default=600.)
    trial = argparse.ArgumentParser(add_help=False, parents=[common])
    trial.add_argument('--seed', type=int)
    trial.add_argument('--mission-id', default='search_and_rescue')
    trial.add_argument('--mission-parameters', type=Path)
    trial.add_argument('--input-package', type=Path, action='append', default=[])
    trial.add_argument('--require-goal', action='store_true')
    sub = parser.add_subparsers(dest='operation', required=True)
    check = sub.add_parser('self-test', parents=[common])
    check.add_argument('--matrix', action='store_true')
    outbound = sub.add_parser('our-controller', parents=[trial])
    outbound.add_argument('--environment', required=True)
    outbound.add_argument('--scout-patrol', choices=PATROL_MODES, default='net65')
    outbound.add_argument('--portable-dispatch',choices=('greedy','flow','bottleneck','bottleneck-patrol','resource','adaptive-survey','repeat-value'),default='greedy')
    inbound = sub.add_parser('our-environment', parents=[trial])
    inbound.add_argument('--controller', required=True)
    inbound.add_argument('--mode', choices=('driver', 'decide'), default='driver')
    inbound.add_argument('--decide-path', choices=('decide', 'decision'), default='decide')
    inbound.add_argument('--decide-profile', choices=('core', 'metaroi-v9'), default='core')
    inbound.add_argument('--decision-timeout', type=float, default=10., help='Per-request RPC timeout in seconds')
    inbound.add_argument('--engine', choices=('planar', 'unreal'), default='unreal')
    inbound.add_argument('--environment-port', type=int, default=18440)
    inbound.add_argument('--layout', type=Path)
    inbound.add_argument('--visible', action='store_true')
    args = parser.parse_args()
    if not 0 < args.timeout < math.inf:
        parser.error('--timeout must be positive and finite')
    if args.operation == 'our-environment' and not 0 < args.decision_timeout < math.inf:
        parser.error('--decision-timeout must be positive and finite')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = (args.output or DEFAULT_OUTPUT / 'checks' / f'net65-interop-{stamp}').resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__).resolve()]
    sources += list((ROOT / 'contracts').glob('*.py')) + list((ROOT / 'controller').rglob('*.py'))
    if args.operation == 'self-test' or args.operation == 'our-environment' and args.engine == 'planar':
        sources += list((ROOT / 'environment').glob('*.py'))
    packages = getattr(args, 'input_package', ())
    frozen = {str(p.resolve(strict=True)): file_sha256(p) for p in (*sources, *packages)}
    if args.operation == 'our-environment' and args.engine == 'unreal':
        frozen.update(native_hashes(args.layout or ROOT / 'unreal/MetaRescue/MetaRescue.uproject'))
    report = {'operation': args.operation, 'input_sha256': frozen}
    try:
        if args.operation == 'self-test':
            report.update(self_test(output, args.matrix, args.timeout))
        else:
            params = strict_json(args.mission_parameters.read_bytes()) if args.mission_parameters else {}
            if not isinstance(params, dict):
                raise ValueError('Mission parameters must be an object')
            mission = {'id': args.mission_id, 'parameters': params}
            if args.operation == 'our-controller':
                seed = SEEDS[0] if args.seed is None else args.seed
                report.update(run_controller(args.environment, output / 'run', seed, args.scout_patrol, mission, args.timeout, args.portable_dispatch))
            else:
                scope = planar(port=args.environment_port) if args.engine == 'planar' else native(output, args)
                with scope as url:
                    client = EnvironmentClient(url, timeout_s=30)
                    try:
                        report.update(run_external(args, client, output, mission))
                    finally:
                        client.disconnect()
            if args.require_goal:
                metrics = report.get('metrics') or {}
                report['goal_verified'] = bool(report.get('confirmed_command_receipts') and
                                               (metrics.get('confirmed_evacuations', 0) > 0 or
                                                metrics.get('actual_delivery_count', 0) > 0 or
                                                metrics.get('targets_rescued', 0) > 0 or metrics.get('delivery_ratio', 0) > 0 or
                                                report.get('status') == 'success'))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, http.client.HTTPException) as error:
        report.update(exchange='failed', error=f'{type(error).__name__}: {error}',
                      first_protocol_error={'message': str(error), 'details': getattr(error, 'details', {})})
    finally:
        report['inputs_preserved'] = all(Path(name).is_file() and file_sha256(Path(name)) == digest for name, digest in frozen.items())
        atomic_json(output / 'report.json', report)
        print(str(output / 'report.json'), flush=True)
    return 0 if report['exchange'] == 'passed' and report['inputs_preserved'] and report.get('goal_verified', True) else 1


if __name__ == '__main__':
    raise SystemExit(main())
