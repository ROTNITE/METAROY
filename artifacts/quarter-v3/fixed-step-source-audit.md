# Fixed model-time validation: evidence and limits

This is a source inspection, not a successful native run. No fingerprinted
source files were changed for this note. The explicit functional profile is
implemented separately from performance acceptance.

Verified installed UE5.8 source locations:

- `Engine/Source/Runtime/Launch/Private/LaunchEngineLoop.cpp:2457–2460`:
  UseFixedTimeStep selects the FApp fixed logical clock. The Deterministic
  shortcut also changes seed handling, so that shortcut is intentionally not
  used for this project profile.
- The same file, lines4725–4730: FPS sets the fixed delta to1/FPS. This value
  is a logical simulation tick rate, not a real render-rate limit.
- `Engine/Source/Runtime/Engine/Private/UnrealEngine.cpp:3005–3023`: in this mode
  DeltaTime is fixed and logical CurrentTime advances by that delta. The
  neighbouring real-time branch instead derives delta from platform time.
- Project physics configuration enables substepping, maximum substep0.008333s,
  maximum8substeps. A1/30s tick fits about4substeps; the actual controller still
  updates once per model tick, not once per physics substep.

The official argument reference also lists these options:
[Unreal Engine command-line arguments](https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-command-line-arguments-reference?application_version=5.8).

Consequences inferred from that verified code:

- Each requested480s functional run still executes14400actual model ticks,
  physics, rendering, sensors, event application and telemetry. It is not a
  JSON fixture or skipped-time simulation.
- The wall duration may be shorter or longer, depending on real execution
  cost. This does not provide an FPS measurement. The current model-time FPS
  calculation becomes approximately30by construction.
- The controller/sensor update frequency differs from the ordinary variable
  frame-time run. A previously safe real-time trajectory is not automatically
  safe at30Hz. Contacts, stops, loads and completion must be checked in each
  actual run; the event tape can remain identical while Chaos differs.
- Keep the seven collision fixtures, static270 rescue, ordinary showcase and
  at least one additional random480run in ordinary real time for performance
  and representative heavy-event evidence. Three fixed480runs cover the
  functional random-seed gate only when their real native evidence passes.

Required profile fields: time_step_mode, requested_fixed_fps, observed
model_delta_s_min/max, wall_elapsed_s, render_wall_fps and the recorded command.
The fixed profile publishes fps/p95_frame_ms/target_60fps_met as null and marks
performance_evaluation=not_evaluated_fixed_functional. Logical tick metrics
remain separately named model_tick_fps and model_step_p95_ms.

The optional flag is restricted to explicit random/replay/effects/zero
functional workflows. Event-centred inspection PNGs are allowed. Traffic,
static mission, window inspection, video capture and ordinary showcase do not
silently acquire a fixed clock.
