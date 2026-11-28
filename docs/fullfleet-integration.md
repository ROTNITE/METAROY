# CF FullFleet Fast — integration and release gates

The new opt-in controller profile is `cf-full-fleet-fast`. The current
`counterfactual-value` and `standard` profiles, Net65 baseline, historical
450-mission plans, default product selection and physics are retained.

## Implementation

- `controller/fullfleet/`: frozen planar finalist logic moved into the
  controller package. Six finalist settings are fixed at the entry point:
  depth=2, per_rescue, all_routes, track_speed, service_estimate, tick_service.
  The original numeric CF model is reused, not replaced with a native model.
- Portable use is limited to the supported planar reference. Exact redundant
  agent-scoped rectangular domain bounds are recognized without deleting or
  rewriting any observation or disabling hard command checks. Unknown or
  stricter restrictions keep the inherited fallback. Foreign environments
  continue with the normal public Core policy.
- Native: existing NSGA-II and NativeCounterfactual are retained. The bounded
  matrix repair evaluates at most 32 genomes, up to 20 targets / 6 ground
  rescuers, preserves reservations excluded by ProblemBuilder and respects
  ready_at, deadline, horizon and usable Wh. The admission criterion is
  intentionally stricter than planar: no predicted loss of priority/count,
  increase in censored weighted completion or absolute Wh.
  This is a new native adaptation, NOT the validated planar algorithm's
  identical physics or proof of native superiority.
- Global candidate forecasts replay feasible nominal future routes under
  nominal/+10%/+25% duration and energy multipliers, rechecking individual
  deadlines and energy after each planned leg. They are hypothetical adaptive
  continuations, not measured mission outcomes. Local AI and physical command
  validation/ACK paths are not replaced.
- Existing NSGA-II Pareto front remains unchanged; repair trace distinguishes
  a post-search selected genome from the original front. Portable effective
  controller/AI mode are recorded in the mission manifest for the new profile.

## Selection

Use the existing product configuration with
`"controller_profile": "cf-full-fleet-fast"` and
`"policy": "nsga2_nsga2", "scout_patrol": "net65"`, or select
**CF FullFleet Fast** in the controller-profile combo (requires rebuilt UE).
CLI: add `--controller-profile cf-full-fleet-fast` to the normal controller
or mission runner. New integrated-series plans may be generated with
`python tools/series_plan.py <new-output> --integrated --controller-profile cf-full-fleet-fast`.
Do not regenerate or resume historical plans under the new revision.

## Evidence and outstanding acceptance

Original research archive: MetaRescue_CF_Successor_Research_2026-11-28.zip.
Reported planar finalist M1 0.744942 vs 0.740122 (1,000 paired maps).
During integration, all 1,677 archive manifest entries passed SHA-256;
10 archive original-vs-fast paired maps passed, and 30 archive original-fast
vs relocated-fast pairs matched decisions, physical outcomes and trace
counters (5,814 decisions per side). Nine targeted local tests passed against
the archive runtime, including 500 randomized native matrix fixtures. These
are archive-runtime and matrix tests, not current main's full regression
nor native physical measurements.

Current-repository integration and regressions are checked separately in CI.
The new profile is opt-in until native Windows/UE build and closed paired
missions confirm its benefit, actual compute time, safety, energy and M1-M7.
A shared charging/exit resource calendar, finer native service calibration,
full stress validation and official contest interoperability remain open.
Do not infer M2-M7 scores or native superiority from planar results.
