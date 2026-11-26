"""Late repeat patrol over confirmed public observations and route estimates."""
from __future__ import annotations

from dataclasses import dataclass
import math


PATROL_MODES = ('core', 'net65')
ACTIVATION = .65
BEAM_WIDTH = 6
DEPTH = 3
MAX_CANDIDATES = 12
MAX_PLAN_AGE_S = 5


@dataclass(frozen=True)
class Candidate:
    sector: str | tuple[float, float, float]
    position: tuple[float, float, float]
    scanned_at: float
    weight: float = 1.
    ownership: float = 1.


def horizon_of(mission):
    termination = mission['termination']
    horizon = next((termination[key] for key in ('time_limit', 'horizon', 'time_limit_seconds', 'max_time')
                    if key in termination), None)
    return horizon if type(horizon) in (int, float) and math.isfinite(horizon) else None


def due(now, start, horizon, coverage_complete):
    return coverage_complete and horizon is not None and start < horizon and now >= start + ACTIVATION * (horizon - start)


def choose_sector(candidates, *, start, now, horizon, cycle, scan_s, scan_energy,
                  energy_scale, estimate, available_energy=math.inf, return_energy=None):
    """Return the first sector of the best positive beam, or wait.

    Estimates are (seconds, energy) in the same units as the scan, return
    reserve and energy scale. An unavailable leg returns None.
    """
    initial = (0., (), start, now, 0.)
    beam = [initial]
    best = initial
    for _ in range(DEPTH):
        expanded = []
        for score, sectors, position, time, energy in beam:
            for candidate in candidates:
                if candidate.sector in sectors or not 0 <= candidate.scanned_at <= now:
                    continue
                leg = estimate(position, candidate.position)
                if leg is None:
                    continue
                duration, travel_energy = leg
                finish = time + duration + scan_s
                if finish >= horizon:
                    continue
                expense = travel_energy + scan_energy
                spent = energy + expense
                reserve = return_energy(candidate.position) if return_energy else 0.
                if not math.isfinite(reserve) or spent + reserve > available_energy:
                    continue
                gain = (finish - candidate.scanned_at) * (horizon - finish) / (cycle * cycle)
                value = score + gain * candidate.weight * candidate.ownership - expense / energy_scale
                expanded.append((value, sectors + (candidate.sector,), candidate.position, finish, spent))
        if not expanded:
            break
        beam = sorted(expanded, key=lambda node: (-node[0], node[1]))[:BEAM_WIDTH]
        if beam[0][0] > best[0] + 1e-9:
            best = beam[0]
    return best[1][0] if best[1] else None


def scan_ages(observed_at, now):
    ages = [now - timestamp for timestamp in observed_at.values() if 0 <= timestamp <= now]
    return {'sectors': len(ages), 'mean_s': sum(ages) / len(ages) if ages else None,
            'max_s': max(ages, default=None)}
