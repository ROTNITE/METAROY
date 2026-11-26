# Physical surface probe — design, not executed evidence

Prepared 2026-11-10. No C++, generated assets, or fingerprinted verification
scripts were changed for this design. The seven road/flight safety fixtures
must be repaired and rerun before adding this fixture.

## Why another probe is necessary

`MovementCost`, `GroundSpeedFactor`, `WaterDepth`, and event samples demonstrate
state queries. They do not prove a moving rigid body slowed down, consumed
more energy per metre, crossed shallow water, or stopped before deep water.
The old `check_environment_probes.py` expects additional agent IDs100/101 and
hard-coded geometry (mud Y7900..8700, water stop<12050). Its old runtime spawning
has been removed. Running that checker against empty mechanics_probes cannot
validate the new quarter. Do not populate its rows from formulas or synthetic
fixtures to make it pass.

## Proposed fixture: four existing ground agents, three existing drones

Use the existing four AMRAgent rovers on four independent existing north–south
streets. Keep all seven agents in the world. Drones remain landed at their own
pads. The demo dispatcher is explicitly disabled, all real body collisions,
forces, torque, suspension, mass240kg and visual mechanisms remain enabled.
No dynamic actors are moved by SetActorLocation during execution.

| Rover | Start, cm | Heading | Forward goal, cm | Surface |
|---|---|---|---|---|
| ground_0 | (1000,6200,53) | +90° | (1000,9600,49) | dry asphalt reference |
| ground_1 | (5500,6200,53) | +90° | (5500,9600,49) | mud, cost2.25 |
| ground_2 | (10000,6200,53) | +90° | (10000,9600,49) | passable damaged asphalt, cost1.90 |
| ground_3 | (14500,6200,53) | +90° | (14500,9600,49) | shallow water on first crossing; deep water on return |

These are the real road centres. Adjacent probes are4500cm apart, beyond local
pair-avoidance interaction range. Inspect their actual collider clearances
before finalising coordinates; do not bypass a static blocker if one is found.
`ResetTestPose` is allowed only during fixture initialisation before the first
measurement, exactly like the traffic fixtures. Initial transform is written
to the manifest. Route commands at later times use SubmitRouteMeters normally.

### Explicit controlled environment profile

This is an authored physical fixture, not a sampled v0.3 experiment. Do not
inject mandatory events into a profile still labelled `static` or `random`.
Proposed internal mode is `surface_probe`, permitted only with the explicit
mechanics/traffic fixture flag. Normal random/static/showcase behaviour is
unchanged. The private manifest and HUD must say surface_probe, and expose
`generation_source=controlled_physical_surface_fixture`.

A minimal implementation can revive `--mechanics-probe` as an alias for this
fixture, instead of silently retaining its obsolete “two extra vehicles at90s”
help text. Alternatively use `--traffic-probe surface`. Whichever is chosen,
the request metadata, C++ mode and verifier must agree. Keep static270 as a
separate mission test; the fixture is not its replacement.

In the fixture only, skip random tape generation and initialise a three-entry
private tape. Each entry is a real FMRDisturbance applied by Environment.Advance
and ApplyDisturbance, not an ad-hoc change in the drive controller:

| ID | Time | Kind | Exact affected5m cells |
|---|---|---|---|
| P_surface_mud |30s | Mud | X10,11 × Y14,15,16 |
| P_surface_road |30s | RoadDamage | X19,20 × Y14,15,16 |
| P_surface_water |30s | Flood | X28,29 × Y14,15,16 |

These are full finite bands fromY7000 to8500, covering both sides of each road
centre. They avoid the grid-boundary ambiguity of a single guaranteed cell at
X5500/10000/14500. Building clipping and actual terrain queries remain in the
normal BuildHazardSurface code. Use the existing visual materials, leak,
normal flood rise(.8cm/s), cap and25cm movement limit. No “test-only friction”
or direct desired-velocity substitution is allowed. Only the location/timing
of these real existing effects is fixed.

All probabilities are explicitly recorded as0 because the fixture tape is
controlled, not probabilistic. The checker must exempt only this named fixture
from random0%-means-empty-tape; normal random0% gate stays mandatory.
Export the final fixture manifest after constructing its tape. Do not retain
an earlier empty reset manifest.

### Phases

- t0: place and hold the four existing bodies at the start positions. Record
  the fixture, initial poses, surface bands, route IDs and phase schedule.
- t30: normal event application creates mud, road damage and water. Inspect
  real geometry/ripple/leak and revision changes. The water begins shallow.
- t40: issue the four normal forward route commands. The water vehicle enters
  around t43 and crosses while depth is roughly10–16cm. The same force-based
  executor must traverse it at reduced speed. No route changes in this phase.
- Each vehicle finishes naturally and idles beyond the affected band. Neither
  a model teleport nor an artificial distance increment is used to return it.
- t80: issue ground_3 a new route back to(14500,6200,49). It physically rotates
  and approaches the same band from the north. By then the normal water has
  risen above25cm. It must brake and remain outside the deep water. Other
  three vehicles remain idle at their forward destinations.
- t110..170: observe stable deep-water stop, unchanged command and no invented
  waiting distance/energy. Run to180or200model seconds in ordinary real time.

One profile therefore proves shallow traversal and deep stop without changing
water depth parameters or running two different physical models. If the real
forward movement takes longer than expected, log that defect; do not delay the
water or move the agent to rescue the test. The three dry/mud/road endpoints
are at least1100cm beyond the band, leaving a steady measurement interval
before destination braking.

## Sampling and acceptance

Existing frame telemetry is every2s, too sparse to measure a5–15m patch reliably.
Add a dedicated `Telemetry/surface-probes.jsonl` sampled every0.1model seconds
only for this explicit fixture. Do not increase all mission telemetry volume.
Each row contains actual time, phase, key, pose, actual rigid-body linear
velocity, executed distance, energy, state, commandID, stop reason, carrying,
mass, support contacts, queried movement cost/depth and vehicle-contact count.
Call the same AccountExecutedMovement used by main telemetry before reading
these fields, so pose/distance are sampled at a consistent physics boundary.
Do not derive a measured speed from MaxSpeed or a queried coefficient.

Record a fixed deep reference query at(14500,8250,4), distinct from the stopped
rover's position. This proves that the obstacle it stopped before is water
above25cm, not an unrelated collider. Also include the ordinary per-cell and
flood-geometry audits, event revisions, command invariant events and public
observation history. Existing imagery should be captured near each actual
band, including the water crossing and its later stopped vehicle.

Measurements must be computed from archived real rows:

1. All four bodies retain ground support, finite poses, no overturning and
   zero vehicle contacts throughout. Forward dry/mud/road reachY>9400 and
   stateidle; water reachesY>9400 before its t80return command.
2. Compute median actual speed inY7600..8200 on the forward pass, excluding
   acceleration/entry and final braking. Require enough independent0.1s rows
   (at least5pervehicle). Mud speed<.70×dry; road and shallow-water speed<.80×dry.
   Also require positive traversal and reasonable lower bounds, so a stalled
   rover cannot pass by producing zero speed. Report observed ratios versus
   the nominal1/cost; do not claim equality of a traction simulation and a
   scalar formula. A20% relative band around nominal is a useful diagnostic,
   not a substitute for inspecting a failed trace.
3. Interpolate actual executed distance and energy at the two measurement
   boundaries. ComputeWh per actual metre, then ratios to the dry vehicle.
   Target mud2.25, road1.90, shallow water2.0 within8% measurement tolerance.
   Account for actual travelled distance, not just the600cm Ydifference.
4. The first water crossing has actual depth>4and<=25cm, traverses the patch,
   and reports the expected shallow movement cost. No blocked/deep-water
   state may be hidden by a successful later endpoint.
5. On the t80return, the deep reference depth is>25cm and blocked. Ground_3
   stops north of the band with its full leading footprint outside it. A
   conservative centre bound isY>=8625cm (125cm radius beyondY8500); verify
   this against the actual collider and safety margin, not an arbitrary pivot.
   There is a hazard_on_route reason and, after the existing4s persistence
   interval, route_blocked=true. It never enters the deep band.
6. During t110..170 its speed<15cm/s, position drift<25cm, cargo/command/state
   remain consistent, and distance/energy increments reflect only measured
   physical movement. Waiting must not accrue hundreds of centimetres or a
   synthetic route cost. Small physics settling is reported, not rounded into
   a fictitious zero-energy assertion.
7. All event tape entries, application times, actual clipping and queries agree.
   Surface effects must be visible in native PNG/video. State alone does not
   prove mud ruts, puddles, asphalt subsidence or water foam are visually good.

The new checker should take an immutable run archive path. It should not read
whatever currently happens to be in Saved, and it must write measured values,
profile/revision hashes and all failed gates. Keep the old checker as historical
or migrate it explicitly; do not mislabel the new seven-agent fixture as the old
nine-agent probe.

## Remaining independent requirements

This fixture does not replace collision fixtures, static rescue delivery,
random seeds, hidden target/visibility tests, van blocked-parking test, aerial
building/vertical obstacle hold, same-seed replay or visual acceptance. It also
does not addA*, route repair, water hydrodynamics, amphibious driving, charge,
cleanup or fire suppression.
