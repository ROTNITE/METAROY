"""RescueQuarter counterfactual survey using delivered observations and native routes.

Planar trees were trained on a 200 s mission, a small fleet and a few obstacles.
Native alternatives use flight, road, service and energy estimates in physical
units. Their survey utility is a proxy, not a prediction of tournament M1.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from controller.counterfactual_options import DURATION, OPTION_PHASES
from controller.native_patrol import SCAN_S, flight_estimator
from controller.navigation import point
from controller.net65 import horizon_of
from controller.problem_builder import mobiles, usable_energy


MODEL_VERSION = 'native-survey-value-v1'
PHASE_WINDOW = .10
MIN_SCORE_GAIN = .003
COORDINATION_S = 12.
MIN_REPEAT_GAP_S = 20.
SCOUT_ENERGY_PRICE = 1.
# Survey windows use the same option lifetimes and energy caps as the winner.
BUDGETS = {'high': .12, 'wave': .12, 'economy': .10}
WAVE_FRACTIONS = {'wave': .25, 'economy': .30}
SHORTLIST_PER_OPTION = 3


@dataclass(frozen=True)
class SurveyLeg:
    sector: str
    duration_s: float
    energy_wh: float
    utility: float

    def score(self, capacity_wh):
        return self.utility - SCOUT_ENERGY_PRICE * self.energy_wh / capacity_wh


@dataclass(frozen=True)
class SurveyOption:
    name: str
    until: float
    consumed_wh: float


def allowed(observation):
    mission = observation['mission']
    return not (observation.get('constraints') or mission.get('constraints') or
                mission.get('directives') or observation.get('observation_quality', {}).get('contradiction_count') or
                any(a.get('constraints') for a in observation['agents']))


def recent(agent, now, limit=5.):
    at = agent.get('freshness', {}).get('position', {}).get('observed_at')
    return type(at) in (int, float) and 0 <= now - at <= limit


class NativeCounterfactual:
    def __init__(self):
        self.reset()

    def reset(self):
        self.phases = set()
        self.options = {}
        self.applied = {}
        self.deferred = set()
        self.delivered_moves = {}
        self.arrival_receipts = {}
        self.arrivals = 0

    def survey(self, observation, builder, agent, scanned_at, excluded, baseline, started):
        """Return an optional sector override and a phase audit record."""
        now, aid = observation['time'], agent['id']
        horizon = horizon_of(observation['mission'])
        if (horizon is None or not started < horizon or not scanned_at or agent.get('status') not in ('active', 'degraded')
                or 'sense' not in agent['capabilities'] or not recent(agent, now) or not allowed(observation)):
            self.options.pop(aid, None)
            return None, None
        active = self.options.get(aid)
        if active and now >= active.until:
            self.options.pop(aid)
            active = None
        fraction = (now - started) / (horizon - started)
        phase = next((i for i, lower in enumerate(OPTION_PHASES)
                      if lower <= fraction < lower + PHASE_WINDOW and i not in self.phases), None)
        if phase is None and not active:
            return None, None
        physical = agent['physical']
        capacity = physical.get('capacity_wh', 0.)
        elapsed_energy = physical.get('consumed_wh', 0.)
        altitude, rates = builder.altitudes.get(aid), tuple(
            physical.get(k, 0.) for k in ('speed_limit_m_s', 'climb_rate_m_s', 'descent_rate_m_s'))
        if altitude is None or not all(type(v) in (int, float) and math.isfinite(v) and v > 0
                                       for v in (capacity, *rates)) or physical.get('sensor_factor', 1.) <= 0:
            return None, None
        if active and active.name == 'pause':
            if builder.positions.keys() <= scanned_at.keys():
                return (None, active.name), None
            self.options.pop(aid)
            active = None
            if phase is None:
                return None, None
        carriers = [a for a in mobiles(observation, 'ground') if 'carry' in a['capabilities']
                    and recent(a, now, 12.) and a['physical'].get('speed_limit_m_s', 0.) > 0]
        if not carriers:
            return None, None
        try:
            dock = point(physical['dock_position_m'])
        except (KeyError, ValueError):
            return None, None
        estimate = flight_estimator(builder, rates, altitude, physical.get('radius_m', 1.65))
        origin = tuple(agent['position'])
        usable = usable_energy(agent)
        waiting = sum(t.get('kind') == 'person' and t.get('status') == 'confirmed' and
                      t.get('evacuation_state', 'waiting') == 'waiting' for t in observation['targets'])
        horizon_length = horizon - started
        included = builder.positions.keys() - excluded
        sector_count = len(builder.positions)
        # The initial coverage has priority. A bounded learned-phase decision
        # may change its order, but does not suppress an unvisited sector.
        unvisited = included - scanned_at.keys()
        candidates = unvisited or included
        if not candidates:
            return None, None
        service_factor = 1. / (1. + waiting / len(carriers))

        def uncertainty(sid):
            scan = scanned_at.get(sid)
            age = min(1., (now - scan) / max(MIN_REPEAT_GAP_S, horizon_length * .25)) if scan is not None else 1.
            return max(0., age) * builder.nav.sector_visibility(builder.positions[sid])

        def measure(sid, until):
            if sid is None:
                return SurveyLeg('', 0., 0., 0.)
            xy = builder.positions[sid]
            goal = (*xy, altitude)
            route = estimate(origin, goal)
            if route is None or now + route[0] + SCAN_S >= until:
                return None
            duration = route[0] + SCAN_S
            energy = route[1] + .55
            if usable is not None:
                back = estimate(goal, dock)
                if back is None or energy + back[1] > usable:
                    return None
            target = (*xy, builder.nav.ground_z(*xy))
            time_left = horizon - now - duration
            rescue_eta = min((eta for a in carriers if (eta := builder.survey_rescue_eta(a, target, time_left)) is not None),
                             default=math.inf)
            slack = max(0., min(1., (time_left - rescue_eta) / max(1., time_left * .5)))
            value = uncertainty(sid) * slack * service_factor / sector_count
            return SurveyLeg(sid, duration, energy, value)

        def alternative(option, until, max_wh):
            cycle = horizon_length * WAVE_FRACTIONS.get(option, .25)
            wave_started = started + math.floor((now - started) / cycle) * cycle
            eligible = (sid for sid in candidates if sid not in scanned_at or
                        (now - scanned_at[sid] >= MIN_REPEAT_GAP_S and
                         (option == 'high' or scanned_at[sid] < wave_started)))
            def upper(sid):
                distance = math.dist(origin[:2], builder.positions[sid])
                return (-uncertainty(sid) / (SCAN_S + distance / rates[0]), sid)
            best = [leg for sid in sorted(eligible, key=upper)[:SHORTLIST_PER_OPTION]
                    if (leg := measure(sid, until)) is not None and leg.energy_wh <= max_wh]
            return max(best, key=lambda leg: (leg.score(capacity), -leg.duration_s, leg.sector), default=None)

        if phase is None:
            budget = capacity * BUDGETS[active.name] - max(0., elapsed_energy - active.consumed_wh)
            leg = alternative(active.name, active.until, budget)
            return ((leg.sector, active.name) if leg else None), None

        self.phases.add(phase)
        until = min(horizon, now + DURATION)
        control = measure(baseline, horizon)
        choices = {name: alternative(name, until, cap * capacity) for name, cap in BUDGETS.items()}
        if builder.positions.keys() <= scanned_at.keys():
            choices['pause'] = measure(None, until)
        observed = {name: {'sector': leg.sector or None, 'utility_proxy': leg.utility,
                           'energy_wh': leg.energy_wh, 'value': leg.score(capacity)}
                    for name, leg in {'continue': control, **choices}.items() if leg is not None}
        best = max(((leg.score(capacity), name, leg) for name, leg in choices.items() if leg is not None),
                   default=None)
        selected = ('continue', control) if not best or control is None or (
            best[0] - control.score(capacity) <= MIN_SCORE_GAIN) else (best[1], best[2])
        name, leg = selected
        audit = {'agent_id': aid, 'phase': phase, 'option': name, 'until': until, 'alternatives': observed,
                 'model': MODEL_VERSION, 'observation_id': observation['request_id']}
        if name == 'continue':
            self.options.pop(aid, None)
            return None, audit
        self.options[aid] = SurveyOption(name, until, elapsed_energy)
        self.applied[name] = self.applied.get(name, 0) + 1
        return (leg.sector or None, name), audit

    def defer(self, observation, builder, agent, target, started, scanned_at):
        if (observation['time'] - started >= COORDINATION_S or scanned_at or not allowed(observation)
                or agent['physical'].get('unlimited_energy') or not recent(agent, observation['time'])
                or math.dist(agent['position'], target['position']) <= 3.7):
            return False
        usable = usable_energy(agent)
        capacity = agent['physical'].get('capacity_wh', 0.)
        if not usable or not capacity or usable / capacity >= .25:
            return False
        if not any('sense' in scout['capabilities'] and recent(scout, observation['time']) for scout in
                   mobiles(observation, 'aerial')):
            return False
        trip = builder.observed_rescue_trip(agent, target['position'])
        return trip is not None and trip.energy_wh > .5 * usable

    def reconcile_motion(self, response, jobs):
        observation = response['observation']
        results = response.get('command_results', ())
        now = observation['time']
        by_command = {(r['agent_id'], r['decision_id'], r['command_index']): r for r in results}
        terminal = {'completed', 'failed', 'cancelled'}
        for key, result in by_command.items():
            if result['execution_status'] in terminal or result['delivery_status'] in ('lost', 'expired'):
                receipt = self.arrival_receipts.pop(key, None)
                if receipt is not None:
                    yield {'type': 'native_counterfactual_arrival_receipt', 'agent_id': key[0],
                           'command_id': f'{key[1]}:{key[2]}', 'execution_status': result['execution_status']}
        if not allowed(observation):
            self.delivered_moves.clear()
            return
        active = set()
        for agent in mobiles(observation, 'ground'):
            aid = agent['id']
            job = jobs.get(aid)
            if (not job or not job.pending or job.action != 'follow_route' or job.charge_station
                    or job.next_phase not in ('approach', 'pickup', 'handover')):
                continue
            key = aid, job.pending, job.pending_index
            active.add(key)
            result = by_command.get(key)
            if result and (result['execution_status'] in terminal or result['validation_status'] == 'rejected'
                           or result['delivery_status'] in ('lost', 'expired')):
                continue  # Let the ordinary acknowledgement path handle the terminal result.
            if result and result['validation_status'] == 'accepted' and result['delivery_status'] == 'delivered':
                self.delivered_moves.setdefault(key, result['updated_at'])
            accepted_at = self.delivered_moves.get(key)
            physical = agent['physical']
            command = f'{key[1]}:{key[2]}'
            if (accepted_at is None or not recent(agent, now) or physical.get('route_finished') is not True
                    or physical.get('state') != 'idle' or physical.get('active_command_id') != command):
                continue
            observed_at = agent['freshness']['position']['observed_at']
            if observed_at < accepted_at or (job.next_phase == 'handover' and
                                             physical.get('cargo_target_id') != job.target):
                continue
            job.phase, job.pending, job.next_phase = job.next_phase, None, None
            job.acknowledgement_unknown_at, job.using_dock_exit = None, False
            self.arrivals += 1
            self.arrival_receipts[key] = observed_at
            if len(self.arrival_receipts) > 128:
                self.arrival_receipts.pop(next(iter(self.arrival_receipts)))
            yield {'type': 'native_counterfactual_arrival', 'agent_id': aid, 'command_id': command,
                   'observation_id': observation['request_id'], 'observed_at': observed_at, 'next_phase': job.phase}
        self.delivered_moves = {key: at for key, at in self.delivered_moves.items() if key in active}
