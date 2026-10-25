# Wildfire Physics Engine Build Plan

## Scope and ownership

This plan focuses on the Rust wildfire physics and simulation engine. Data acquisition/normalization and visualization are owned by other teams. The broader architecture is retained in SYSTEM_REFERENCE_PLAN.md as a coordination reference.

The engine accepts versioned, normalized landscape, weather, moisture, and initial-fire-state inputs. It produces reproducible spread forecasts, arrival times, intensity estimates where supported by the formulation, uncertainty products, and diagnostic metadata.

The primary scientific question is: given an existing fire and specified environmental conditions, how does its modeled front propagate across a heterogeneous landscape over time?

### Ownership boundary

| Component | Owner | Physics team's responsibility |
|---|---|---|
| Downloading weather, satellite, terrain, fuels, and asset data | Data team | Specify required variables, units, resolution, timestamps, and validation rules |
| Reprojection, raster alignment, source normalization | Data team | Validate incoming grid contracts and reject incompatible inputs |
| Fuel and moisture semantics | Shared | Physics team defines model requirements; data team supplies compatible values with provenance |
| Spread rates and fire-front propagation | Physics team | Implement, verify, benchmark, and document |
| Observation-to-state reconciliation | Shared interface | Physics team supports checkpoint/restart and validated state replacement; data team supplies interpreted observations |
| Ensemble simulation and scientific evaluation | Physics team | Implement scenario execution, numerical diagnostics, and model evaluation |
| Infrastructure exposure | Separate spatial module/team, unless assigned | Export forecast footprints and arrival products with explicit semantics |
| API, map rendering, tiles, dashboards | Service/visualization team | Supply stable result schemas and small integration fixtures |

Do not build live-feed connectors, a dashboard, WebAssembly bindings, or an asset index as prerequisites for the physics engine. Do build input validation, a command-line runner, test fixtures, and result export so the engine can be used independently.

## 1. Scientific scope

First version:

- Surface-fire spread driven by supported fuel models, fuel moisture, wind, and slope.
- Heterogeneous landscapes with explicit burnability and no-data masks.
- Constant-condition propagation followed by time-varying weather propagation.
- Deterministic restartable simulation and ensemble execution.
- Forecast snapshots at configurable horizons, initially evaluated at 30 minutes, 1 hour, 3 hours, and 6 hours.

Explicit exclusions until separately implemented and validated:

- Crown fire, long-range spotting, plume-driven weather, and coupled atmospheric dynamics.
- Structure-to-structure propagation, suppression effectiveness, structural damage, or evacuation decisions.
- New-ignition prediction and autonomous interpretation of raw satellite imagery.

This is a semi-empirical fire-behavior and propagation engine, not initially a computational-fluid-dynamics solver. Its applicability must remain visible. Varying inputs in an ensemble does not repair missing physical processes.

## 2. Rust workspace

```text
crates/
  fire-types/       Typed units, grids, fuel/weather/state/output contracts
  fire-behavior/    Fuel parameterization, moisture response, spread rates
  fire-propagation/ Directional travel times and time-varying front evolution
  fire-state/       Burn state, checkpoints, restart and state replacement
  fire-ensemble/    Scenario sampling, bounded execution, aggregation
  fire-io/          Reading normalized inputs and writing model results
  fire-eval/        Reference cases, historical replay, scientific metrics
  fire-cli/         inspect-inputs, simulate, resume, evaluate, benchmark
fixtures/
benchmarks/
docs/
```

Keep scientific calculations independent of networking, source download libraries, and rendering. Use a supported compiler/toolchain and lock dependencies at implementation time. Profile before adding unsafe code or specialized acceleration.

## 3. Integration contracts: agree these first

### Landscape input

Require a projected metric CRS, grid dimensions, cell size, transform, common alignment, elevation, fuel model identifiers, burnability, and valid-data masks. Specify whether slope/aspect are supplied or derived and use one consistent convention.

Document the fuel parameter table and version. Each selected spread formulation requires a specific set of fuel properties; a vegetation label alone is insufficient. Mark unsupported fuel classes explicitly rather than silently substituting a convenient class.

### Weather and moisture input

Require UTC valid times and, for replay, forecast issue/availability times. Inputs include wind with height/reference definition, temperature, humidity, precipitation where used, and the live/dead moisture categories required by the fuel model.

Define whether moisture is supplied as a time series or computed by a separate moisture module. Initially accept externally supplied, physically bounded moisture values; implement a dynamic moisture model only as an explicit later milestone. Do not imply that humidity alone equals fuel moisture.

Convert wind direction and height in one documented location. Distinguish meteorological wind-from direction from the direction of movement. Treat moisture fractions, percentages, and mass conventions explicitly.

### Initial fire state

Accept a timestamped ignition geometry or estimated burned area with active-front information. Define how geometry is rasterized and whether input geometry represents accumulated burned area or currently active burning. An interior historic hotspot must not automatically become a new active ignition.

### Output

Export:

- Burned/active state snapshots with defined semantics.
- First-arrival-time grid and a separate never-arrived/no-data mask.
- Spread-rate products with direction/time definitions.
- Fireline intensity only where its calculation and assumptions are implemented and verified.
- Forecast contours with extraction tolerances.
- Input/configuration hashes, model version, seed, numerical settings, assumptions, and applicability flags.
- For ensembles: burn frequency/probability, arrival distributions, and completed/failed scenario counts.

Do not use one numeric sentinel to conflate no arrival, outside coverage, and missing data. Provide a tiny complete input/output fixture to both collaborating teams before implementing the full solver.

## 4. Milestone A — Reference specification and test harness

Tasks:

1. Select and document the surface-fire formulation and supported fuel parameterization.
2. Establish reference equations, units, assumptions, parameter ranges, and independent numerical examples.
3. Write a model specification that connects each equation to implementation inputs and outputs.
4. Create homogeneous synthetic landscapes, slope cases, changing-wind cases, heterogeneous patches, and no-data boundaries.
5. Define numerical tolerances and evaluation procedures before tuning.
6. Reserve independent historical incidents for final evaluation with the data team.

Exit gate: the engine can validate a complete input manifest and run the fixture harness; scientific reference values are independent of the code being tested.

## 5. Milestone B — Local fire-behavior calculations

Implement a documented Rothermel-based surface-fire baseline or another explicitly selected established formulation. Match the formulation's units and fuel assumptions exactly.

Build in layers:

1. Fuel parameter lookup and validation.
2. Moisture damping and other required intermediate terms.
3. No-wind, no-slope spread calculation.
4. Wind and slope response using the chosen formulation's combination rules.
5. Directional spread representation consistent with the chosen propagation method.
6. Optional intensity calculation with verified heat-release and consumption assumptions.

Verification:

- Compare intermediate terms and final results with independent reference cases.
- Test zero wind/slope, opposing and aligned wind/slope, angular wraparound, and allowed moisture limits.
- Reject invalid units and unsupported parameters.
- Test expected behavior only within the formulation's valid range; avoid invented universal monotonicity rules.

Exit gate: local behavior matches references within declared tolerances. No propagation optimization begins before these checks pass.

## 6. Milestone C — Constant-condition propagation baseline

Implement an anisotropic minimum-travel-time baseline on the raster landscape.

Tasks:

- Define connectivity/stencil and directional transition-time calculation.
- Support point, line, and polygon ignitions through documented initialization.
- Model heterogeneous fuels and terrain.
- Define behavior at burnable boundaries and no-data areas.
- Produce arrival times and horizon snapshots.
- Record solver diagnostics and numerical parameters.

Verification:

- Check isotropic symmetry and expected directional growth on homogeneous landscapes.
- Rotate equivalent cases to measure grid-direction bias.
- Run grid refinement and stencil sensitivity experiments.
- Check travel-time consistency and unreachable regions.
- Compare selected configurations with a reference model where available.

A road is not automatically an absolute fire barrier. Barrier behavior must follow explicit landscape inputs and supported model assumptions.

Exit gate: the baseline is reproducible, numerically characterized, and useful as a reference for the time-varying solver.

## 7. Milestone D — Time-varying propagation

Implement a solver that explicitly evolves the front under changing weather and moisture. A static shortest-path solution with periodically replaced input arrays is not sufficient without a consistent time-dependent formulation.

Before implementation, compare a time-dependent front solver and an appropriately formulated travel-time approach using a small prototype. Choose based on directional accuracy, stability, heterogeneous terrain behavior, restart support, and runtime. Record the decision.

Tasks:

- Define temporal interpolation/holding rules for supplied environmental fields.
- Define time step selection and numerical stability requirements.
- Advance front state across weather changes and output boundaries.
- Support adaptive substeps where required.
- Separate accumulated burned state from active propagation state.
- Add cancellation at safe step boundaries and checkpoint/restart.

Verification:

- Recover the constant-condition baseline within declared numerical differences.
- Test abrupt wind shifts and time-series gaps.
- Check convergence as spatial and temporal resolutions change.
- Ensure no propagation backward in time and no unintended re-ignition of burned interiors.
- Compare uninterrupted and restarted runs.

Exit gate: time-dependent results pass numerical checks and improve relevant development-incident forecasts over fixed-weather runs.

## 8. Milestone E — State correction interface

The physics engine should support observation updates without owning satellite ingestion.

Define an update request containing the corrected state time, interpreted geometry/state, uncertainty, provenance, and quality flags.

Tasks:

- Load a checkpoint preceding an observation's acquisition time.
- Advance to that time and apply an explicitly selected correction policy.
- Preserve burned-area versus active-front semantics.
- Re-simulate forward with the corresponding environmental history.
- Produce a new version rather than overwriting old results.
- Reject incompatible grids and temporally inconsistent updates.

Begin with trusted reviewed perimeter replacement as a transparent baseline. More complex weighting/filtering is a later scientific feature. Keep source interpretation outside the solver and test whether corrections improve forecasts against a no-update baseline.

Exit gate: the engine accepts a correction fixture, resumes reproducibly, and preserves an audit trail.

## 9. Milestone F — Ensembles

Run independent scenarios with reproducible seeds and bounded workers. Sample plausible joint uncertainty in wind, moisture, initial-front position, and selected parameters. Avoid independent noise in every grid cell unless scientifically justified.

Tasks:

- Define distributions and spatial/temporal correlations.
- Share immutable landscape data across scenarios.
- Allocate mutable simulation state per scenario.
- Limit concurrent memory and CPU consumption.
- Aggregate burn frequencies and arrival distributions.
- Preserve the probability of no arrival within each horizon.
- Report partial or failed ensembles explicitly.

Use development incidents to assess calibration and ensemble convergence. Raw scenario frequencies must not be described as validated probabilities without evidence. Probabilities are conditional on the modeled incident and assumptions.

Exit gate: ensemble products improve uncertainty representation against independent outcomes and fit the documented resource budget.

## 10. Milestone G — Historical scientific evaluation

The data team supplies normalized incident timelines and historical forecast vintages. The physics team owns reproducible simulation and model evaluation.

Compare:

- Persistence.
- A labeled heuristic wind envelope.
- Constant-condition propagation.
- Time-varying propagation.
- With and without state corrections.
- Deterministic and ensemble outputs.

Measure perimeter overlap, boundary error, missed/overpredicted area, arrival-time error where time-resolved truth exists, and probability calibration. Compare matched forecast horizons rather than short-term predictions against eventual final perimeters.

Prevent future-data leakage: release inputs by availability time in replay. Using retrospective weather analyses is a separately labeled experiment, not an operational forecast test.

Hold out whole incidents, years, and preferably a geographic stress set. Report per-incident/horizon performance and truth uncertainty. Set numerical acceptance thresholds before final testing with domain input; do not invent universal accuracy targets without the pilot data.

Exit gate: documented improvement over baselines on held-out incidents, reproducible evaluation, and explicit supported/unsupported regimes.

## 11. Milestone H — Performance optimization and delivery

Optimize only after a correct baseline exists.

Priority order:

1. Profile local spread calculations, propagation queues/front updates, memory traffic, and export.
2. Cache immutable fuel and terrain terms where mathematically valid.
3. Use contiguous arrays and reuse work buffers.
4. Share read-only landscape snapshots across scenarios.
5. Parallelize independent scenarios within a defined budget.
6. Investigate spatial parallelism only if profiling justifies its complexity.
7. Evaluate reduced-resolution/adaptive methods against accuracy loss before adoption.

Do not promise sub-millisecond regional forecasts. Benchmark a documented matrix of landscape extent, cell size, horizon, terrain/fuel complexity, ensemble size, hardware, and concurrency. Include peak memory, wall time, cancellation delay, and export cost.

Deliver a CLI, a library API, versioned file schemas, integration fixtures, a scientific model document, benchmark results, and an evaluation report. Other teams can wrap the library in a service or consume result artifacts.

## 12. Model API outline

```text
validate_inputs(manifest) -> validation_report
prepare_landscape(snapshot) -> prepared_landscape
simulate(prepared_landscape, environment, initial_state, config) -> run_result
checkpoint(run_state) -> checkpoint_artifact
resume(checkpoint, environment, config) -> run_result
apply_state_update(checkpoint, update) -> corrected_state
run_ensemble(inputs, scenario_config) -> ensemble_result
evaluate(predictions, reference_observations, protocol) -> evaluation_report
```

Use immutable manifests and explicit errors. Define floating-point reproducibility tolerances across platforms; a fixed seed alone does not guarantee bitwise-identical parallel results.

## 13. Provisional schedule

| Milestone | Planning range | Dependency |
|---|---:|---|
| Contracts, reference specification, fixtures | 1–2 weeks | Agreement with data team |
| Local behavior calculations | 2–3 weeks | Chosen formulation and reference cases |
| Constant-condition propagation | 2–3 weeks | Verified local rates |
| Time-varying solver | 3–5 weeks | Baseline and solver selection |
| State correction/restart | 1–2 weeks | Time-dependent state semantics |
| Ensembles | 2–3 weeks | Stable deterministic solver |
| Historical evaluation and revisions | 3–5 weeks | Suitable incident progression data |
| Profiling, integration, documentation | 1–3 weeks | Stable evaluated outputs |

Approximately four to six months is a provisional planning range for one experienced engineer with domain review and timely normalized inputs. Scientific revisions and data gaps can extend it. Synthetic fixtures allow engine development to begin before all real data is available.

## 14. Immediate backlog

1. Agree the ownership table and integration contracts with both teams.
2. Select the surface-fire formulation and reference fuel parameter set.
3. Build a tiny complete fixture with one fuel, flat terrain, constant weather, and an ignition.
4. Implement typed units, input validation, and the CLI skeleton.
5. Verify local no-wind/no-slope spread values against independent references.
6. Add directional wind/slope cases.
7. Implement the first constant-condition arrival-time simulation.
8. Export a documented arrival grid and forecast snapshot for visualization integration.
9. Measure rotation/grid bias before adding complexity.
10. Add heterogeneous inputs, then time-varying weather.

The first milestone is a verified Rust simulation of a controlled fire-growth case, exported in a format the visualization team can read. The next milestone is a reproducible historical incident forecast, evaluated against observed progression.
