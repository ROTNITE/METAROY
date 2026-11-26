# Three additional physical fixtures — candidate, not executed

No current C++ file was changed. Apply `safety-extra-fixtures.patch` only after
the original seven-fixture batch stops. The patch depends on a **public**
`int32 VanContacts=0` field in MRWorld, to be added by the parent owning that file.
Add air_obstacle / hold_resume / van_conflict to launcher and runner choices.
Use static environment, external dispatcher, 100 model seconds per fixture.

## air_obstacle

The first apartment is centred (2150,2180,24) cm, damage2; its intact ground
floor south facade is y1730. Ground window centre x1850,z160 has a real wall
opening. aerial_0 flies from (1850,1000,160) towards (1850,2400,160), MaxSpeed250.
The 230cm rotor sweep is wider than the window opening; the narrow central body
could fit it. Expect actual `obstacle_on_route`, `stop_obstacle` naming the wall,
safe stationary position y<1615, z~160, no contacts. Continuous obstructed
interval must last >=10s with <2cm displacement; unfulfilled command preserved.

aerial_1 flies independently up the clear parallel street x1000 from y1000 to
4000 at z160. Require y>3900 and holding, zero contacts. This distinguishes a
real obstacle stop from globally disabled movement. Do not replace the facade
by a synthetic trigger. Inspect the real collision mesh in the first run; if
the source hole fits the rotor span, use a measured narrower existing opening.

## hold_resume

ground_0 starts (5500,7100,53), carries patient0 attached at local(0,0,15), mass320.
Initial command goes to (5500,14000,49). At t20 a real HoldAgent call applies
probe_hold; a repeated hold is accepted idempotently and a route using that
hold ID is rejected. At t35 SubmitRouteMeters applies resumed_loaded_route to
the same endpoint; replaying the old hold must leave the newer route active.
Both transitions check unchanged pose and carrying synchronously and emit
traffic_probe_hold_started / traffic_probe_hold_resume_invariants_verified.

Verify cargo and mass in every frame; no delivery; pose/distance/energy stable
after braking during t23..34 (hold is not route_blocked). On resume require
continued measured motion, final y>13700 and idle with the patient retained.
Energy must charge only real movement, not 15 seconds of waiting. Observe
state before and after rather than treating the invariant event alone as proof.

## van_conflict and required parent-owned instrumentation

ServiceVan starts (-650,2750,14), parks at (-650,2100,14), yaw90. ground_0 starts
at (-650,2100,65), yaw-90, held by van_clearance_wait. Its body does not overlap
the initial van, but obstructs the parking path. At t20 the real route goes
first to(-650,1500,49), then(600,1500,49); no fixture teleport after reset.

Parent should retain the kinematic, collision-enabled van and its swept box.
Give that box a modest 35cm clearance margin: (330,153,125) instead of
(295,118,125). The source body is 5.799m ×2.241m, so the existing box has only
~5cm front clearance. Without added margin it can stop so close to the rover's
physical belly that the rover's conservative whole-body sweep initially
overlaps the van and cannot clear the route. This is a physical clearance
policy, not ignored collision or fixture relocation. Confirm base parking still
completes in a static mission after this change.

Add per-frame `service_van` telemetry: position_cm, parked_position_cm,
remaining_cm, blocked, stop_reason, speed_cm_s, wheel_travel_cm, vehicle_contacts.
On a swept hit classify AMRAgent as yield_to_vehicle, other solid props as
obstacle_on_route. At every blocking transition emit van_safe_stop or van_resumed.
OnAgentHit walks OtherComponent attachment ancestors for MRAsset:Van, since
all van parts belong to the world actor. AgentContacts now includes such hits;
public MRWorld.VanContacts counts them separately, with agent_van_contact events.

Acceptance: t8..19 van stopped at a positive distance from its parking target,
ground_0 held and stationary, both contact counters0. After t20 ground0 clears
physically and van resumes to remaining<5cm, wheel travel agrees with actual
translation. A van already stuck against static infrastructure fails this
fixture; inspect stop_reason and the actual collider instead of skipping it.
