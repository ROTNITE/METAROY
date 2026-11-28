"""Bounded native plan repair using the existing public RescueProblem matrices.

This is a native adaptation, not the planar model or evidence of 3D superiority.
No accepted command or cargo is changed. Routes represent future assignments only.
"""
from __future__ import annotations

from controller.optimization import genome_check

MAX_CANDIDATES = 32
MAX_TARGETS = 20
MAX_CREW = 6
EPS = 1e-8


def evaluate(problem, genome):
    objectives, tasks = problem.simulate(genome)
    count = len({j for route in tasks for j, _, _, _ in route})
    return (*objectives, count), tasks


def acceptable(candidate, incumbent):
    loss, time, energy, count = candidate
    old_loss, old_time, old_energy, old_count = incumbent
    # Native admission is deliberately stricter than planar: use the existing
    # censored weighted completion and absolute Wh, including when count grows.
    if (count < old_count or loss > old_loss + EPS or time > old_time + EPS
            or energy > old_energy + EPS):
        return False
    return (count > old_count or loss < old_loss - EPS
            or time < old_time - EPS or energy < old_energy - EPS)


def key(score):
    loss, time, energy, count = score
    return loss, -count, time, energy


def neighbors(problem, genome, tasks):
    """Deterministic small neighborhood: insert missed targets, swap, reassign.

    Prioritize currently missed work. No candidate can add a target outside the
    problem or change a protected commitment omitted by ProblemBuilder.
    """
    order, assignment = map(tuple, genome)
    saved = {j for route in tasks for j, _, _, _ in route}
    missed = sorted(set(order) - saved, key=lambda j: (-problem.targets[j].priority, j))
    for j in missed:
        front = (j,) + tuple(i for i in order if i != j)
        for r in range(problem.m):
            if problem.first[r][j] is not None or problem.trips[r][j] is not None:
                a = list(assignment); a[j] = r
                yield front, tuple(a)
    for i, j in enumerate(order):
        if i + 1 < len(order):
            k = order[i + 1]
            o = list(order); o[i], o[i + 1] = o[i + 1], o[i]
            yield tuple(o), assignment
            a = list(assignment); a[j], a[k] = a[k], a[j]
            yield order, tuple(a)
        for r in range(problem.m):
            if r != assignment[j] and (problem.first[r][j] is not None or problem.trips[r][j] is not None):
                a = list(assignment); a[j] = r
                yield order, tuple(a)


def refine_plan(problem, plan):
    """Return a new plan plus trace; cap total matrix simulations at 32.

    Existing NSGA-II front and statistics remain its own evidence. The selected
    genome/objectives/routes are recomputed together; the repair is not inserted
    into the original Pareto front or claimed as an executed rescue.
    """
    trace = {'model': 'native-fullfleet-repair-v1', 'evaluated': 0, 'changed': False}
    if not (1 <= problem.m <= MAX_CREW and 2 <= problem.n <= MAX_TARGETS and 'genome' in plan):
        return {**plan, 'fullfleet': {**trace, 'reason': 'outside_search_budget'}}
    if any(not free and c.ready_at <= problem.now for c, free in zip(problem.crew, problem.available_now)):
        return {**plan, 'fullfleet': {**trace, 'reason': 'unknown_crew_release'}}
    first = tuple(map(tuple, plan['genome']))
    genome_check(first, problem.n, problem.m)
    baseline, initial_tasks = evaluate(problem, first)
    best, best_genome, best_tasks = baseline, first, initial_tasks
    seen = {first}
    trace['evaluated'] = 1
    for candidate in neighbors(problem, first, initial_tasks):
        if candidate in seen:
            continue
        seen.add(candidate)
        score, tasks = evaluate(problem, candidate)
        trace['evaluated'] += 1
        if acceptable(score, baseline) and key(score) < key(best):
            best, best_genome, best_tasks = score, candidate, tasks
        if trace['evaluated'] >= MAX_CANDIDATES:
            break
    trace.update(before=list(baseline), after=list(best), changed=best_genome != first,
                 reason='strict_predicted_improvement' if best_genome != first else 'incumbent_retained')
    if best_genome == first:
        return {**plan, 'fullfleet': trace}
    routes = {c.id: [problem.targets[j].id for j, _, _, _ in best_tasks[r]]
              for r, c in enumerate(problem.crew)}
    result = {**plan, 'genome': [list(row) for row in best_genome], 'routes': routes,
              'selected_objectives': list(best[:3]), 'fullfleet': trace}
    if 'initializer' in result:
        result['initializer'] = {**result['initializer'], 'selected_genome_matches': False}
    return result


def delayed_forecast(problem, tasks):
    """Replay the selected future routes under public time/Wh stress.

    Each leg is evaluated again against remaining energy, horizon and individual
    deadline. After a skipped leg the next leg still starts from the actual
    projected release position, not the skipped target. Already reserved work
    stays excluded and no credit for it is invented here.
    """
    scenarios = []
    for name, multiplier, weight in (('nominal', 1., .45), ('delay_10pct', 1.10, .35), ('delay_25pct', 1.25, .20)):
        completed = {}; energy = 0.
        for r, route in enumerate(tasks):
            crew = problem.crew[r]
            clock = problem.now + (max(problem.now, crew.ready_at) - problem.now) * multiplier
            remaining = crew.usable_wh
            used = False
            for j, _, _, _ in route:
                trip = problem.trips[r][j] if used else problem.first[r][j]
                if trip is None:
                    continue
                finish = clock + trip.duration_s * multiplier
                cost = trip.energy_wh * multiplier
                deadline = problem.targets[j].deadline
                if finish > problem.horizon or (deadline is not None and finish > deadline):
                    continue
                if remaining is not None and cost > remaining + 1e-9:
                    continue
                completed[j] = finish; clock = finish; used = True; energy += cost
                if remaining is not None:
                    remaining -= cost
        priority = sum(problem.targets[j].priority for j in completed)
        completion = sum(t.priority * completed.get(j, problem.horizon) for j, t in enumerate(problem.targets))
        scenarios.append({'scenario': name, 'weight': weight, 'saved': len(completed),
                          'priority_saved': round(priority, 3), 'weighted_completion': round(completion, 2),
                          'energy_Wh': round(energy, 2)})
    expected = {key: round(sum(s[key] * s['weight'] for s in scenarios), 3)
                for key in ('saved', 'priority_saved', 'weighted_completion', 'energy_Wh')}
    return scenarios, expected
