# Aegis Fire: Detailed Build Plan

Status: proposed implementation specification, October 24, 2026.

## 1. Objective and release scope

Build a Rust-centered system that estimates the spread of an existing wildfire, updates that estimate when new observations arrive, and identifies infrastructure that may be exposed. Aegis-Geo is the spatial exposure subsystem, not the complete prediction model.

The first release answers:

> Given the evidence available at a specified time, where could this fire spread over the next 30 minutes, 1 hour, 3 hours, and 6 hours, and which assets could it reach?

These are candidate forecast horizons. Only horizons that meet validation gates should be presented as supported operational forecasts. The system is initially a research and analyst decision-support tool. It does not issue evacuation orders or claim to predict structural damage.

Start with one pilot region selected for available incident progression, weather, terrain, fuel, and infrastructure data. Select a region with several independent historical incidents, not only a convenient final burn perimeter. Document the pilot boundary, supported fuel regimes, intended user, available hardware, and evaluation incidents before tuning the model.

First-release scope:

- Historical replay and a reproducible evaluation dataset.
- Surface-fire spread under heterogeneous fuels, terrain, and changing weather.
- Observation-aware updates with explicit source age and confidence.
- Ensemble scenarios for input and parameter uncertainty.
- Infrastructure exposure for buildings, roads, and power infrastructure.
- A map showing observations, estimated state, forecasts, and data freshness.
- An API with asynchronous forecast jobs and subscriptions.

Deferred work:

- New-ignition probability modeling.
- Validated crown-fire and long-range spotting models.
- Building-to-building urban fire propagation.
- Structural damage, evacuation routing, and suppression optimization.
- A learned world model or neural surrogate in the production forecast path.
- Browser-side authoritative simulation and global coverage.

Unsupported behavior must be visible in forecast metadata. A surface-fire-only forecast in conditions favorable to crown fire or spotting needs an explicit applicability flag; ensemble variation cannot compensate for missing physics.

## 2. Design principles

1. Distinguish observed evidence, estimated current fire state, and future forecast.
2. Record observation acquisition time and system availability time separately.
3. Treat source resolution and uncertainty as part of the data, not display details.
4. Keep deterministic simulation reproducible from an immutable input manifest.
5. Validate forecast usefulness before optimizing low-level runtime.
6. Separate compute jobs from request handling and map rendering.
7. Report geometric exposure separately from intensity, vulnerability, and damage.
8. Keep new observations responsive even when a regional simulation is running.
9. Make every performance target conditional on a documented workload and hardware.
10. Keep external scientific models behind replaceable interfaces.

## 3. Architecture and workspace

Use a Cargo workspace. Select a supported Rust edition, minimum supported compiler, and exact dependency versions at project initialization after checking compatibility. Do not copy the reference prompt's versions without verification.

```text
aegis-fire/
  Cargo.toml
  Cargo.lock
  rust-toolchain.toml
  crates/
    aegis-types/       Shared domain types, identifiers, units, schemas
    aegis-data/        Source adapters, normalization, quality checks
    aegis-landscape/   Raster alignment, tiles, projection, static layers
    aegis-model/       Spread rates, propagation, state and snapshots
    aegis-assim/       Observation reconciliation and state correction
    aegis-ensemble/   Scenario sampling and probability products
    aegis-geo/        Asset indexes and exact exposure calculations
    aegis-store/       Manifests, metadata, artifacts, checkpoints
    aegis-server/      HTTP, WebSocket subscriptions, scheduling
    aegis-replay/      Replay runner and scientific evaluation
    aegis-cli/         Import, inspect, simulate, replay, benchmark
    aegis-wasm/        Optional later client utilities
  frontend/
  config/
    pilot-region.toml
    source-policies.toml
    model-defaults.toml
  schemas/
  fixtures/            Small redistributable test cases
  benchmarks/
  docs/
    scientific-model.md
    data-catalog.md
    evaluation-protocol.md
    operations.md
  artifacts/           Generated local results, excluded from source control
```

Logical pipeline:

```text
Source adapters -> immutable observations -> normalized landscape and weather
                                          -> estimated current fire state
                                          -> scenario simulations
                                          -> probability/arrival products
                                          -> asset exposure
                                          -> versioned API and map updates
```

Use Tokio/Axum for I/O and bounded dedicated workers for CPU work. Rayon is a candidate for independent scenarios and batched spatial work. Avoid unconstrained nested parallelism; a shared CPU budget should prevent ensemble workers and geometry queries from oversubscribing the machine.

For a local prototype, use filesystem artifacts and SQLite metadata. Keep storage interfaces replaceable for object storage and a shared metadata database when deployment requires multiple workers. A distributed job system is not a first-release prerequisite.

## 4. Domain contracts

Implement and version contracts before source-specific processing.

### Observation

Required fields:

- observation_id, source_id, incident_id when known.
- observed_at: acquisition/event time in UTC.
- available_at: time this system could first access the observation.
- ingested_at: local receipt time.
- observation_kind: perimeter, hotspot, weather, sensor, or reviewed report.
- geometry or raster artifact reference, CRS, units, and spatial resolution.
- quality flags, cloud/no-data flags where relevant, and uncertainty description.
- source revision, checksum, provenance, and superseded observation identifier.

Do not use one universal confidence number as if all sources were calibrated alike. Preserve source-native quality fields and map them through a documented source policy.

### LandscapeSnapshot

Fields include boundary, projected CRS, grid transform, cell size, no-data masks, elevation/fuel layer references, fuel mapping version, creation time, and content hash. Weather and moisture snapshots are separately versioned because they change more often than terrain.

### FireState

Store estimated burned area, active-front representation, current state time, source evidence references, model state, and applicability flags. Burned area is not equivalent to actively burning area. A growth solver must not propagate outward from every old interior hotspot as a new ignition.

### ForecastRequest

Fields include incident, as_of time, state snapshot, requested horizons, landscape/weather snapshots, model configuration, ensemble configuration, seed, and computational budget.

### ForecastResult

Fields include forecast_id, issue time, valid times, input manifest, model/build versions, scenario counts, compute duration, coverage masks, assumptions, quality/applicability flags, raster/vector artifact references, and supersession status.

### Asset and AssetExposure

Assets carry stable IDs, source version, geometry, type, and optional vulnerability metadata. Preserve building footprints and line/multipart geometries.

Exposure includes probability of intersection by horizon, exposed length or area where meaningful, arrival distribution, the fraction of scenarios with no arrival, and the forecast version. Deterministic exposure is a separate field from ensemble exposure. Damage probability remains absent unless a separately validated damage model is introduced.

All schemas must specify null semantics, boundary-touch rules, coordinate conventions, and unit conversions. Use typed units or strongly named newtypes for meters, seconds, wind speed, and angles.

## 5. Phase 0 — Pilot and evaluation specification

Tasks:

1. Select a pilot region and primary user workflow.
2. Inventory historical fire progression, weather observations and forecasts, fuel/terrain, and assets.
3. Confirm access, licensing, update cadence, retention, and redistribution rules.
4. Identify whether historical forecast vintages and publication times exist.
5. Define supported fire regimes and known failure modes.
6. Reserve training/tuning and held-out incidents before modeling.
7. Define a baseline hardware profile and latency workloads.
8. Write numerical scientific acceptance thresholds with a fire-domain reviewer before seeing final held-out results.

Deliverables: data catalog, pilot configuration, split manifest, evaluation protocol, and model applicability statement.

Exit gate: the team can reconstruct multiple independent incidents with sufficient progression and input data. If only final perimeters exist, narrow the validation claim and acquire better progression evidence before claiming short-term arrival accuracy.

## 6. Phase 1 — Reproducible ingestion and landscape preparation

Implement file-based adapters first, followed by live equivalents using the same normalization contracts.

Data priorities:

- Terrain: elevation, slope, aspect, masks, and projection.
- Fuel: supported fuel classes, load/structure variables required by the chosen formulation, and layer age.
- Weather: wind components, temperature, humidity, precipitation, and forecast issue/valid times.
- Moisture: explicit live/dead fuel-moisture estimates with provenance and uncertainty.
- Fire evidence: reviewed perimeters and source-specific hotspot products.
- Assets: stable IDs, footprints, roads, and available power infrastructure.

Implementation details:

- Preserve original source payloads and create normalized derivatives.
- Reproject to an appropriate regional metric CRS before metric calculations.
- Use appropriate resampling for each variable: categorical fuels must not be interpolated like continuous elevation.
- Align rasters to one explicit grid and retain no-data masks.
- Begin by evaluating 30–100 m regional grids; choose the working resolution through sensitivity and runtime experiments. Fine output cells do not increase the information content of coarse inputs.
- Convert wind conventions explicitly: meteorological direction is commonly the direction wind comes from, whereas propagation uses a movement direction.
- Record observation height and distinguish forecast/reference wind from midflame wind; implement a documented conversion required by the spread model.
- Handle sensor duplicates, out-of-order arrival, invalid coordinates, unit mismatches, and implausible values.
- Quarantine unusable geometry rather than silently repairing it. Record any approved repair and its effect.
- Retain provider quality flags for satellite detections. Do not interpret a hotspot footprint as a measured fire perimeter.

Meaningful verification: unit conversion fixtures, known coordinate transforms, raster alignment checks, no-data behavior, and deterministic source normalization.

Exit gate: a historical incident produces a versioned input manifest, aligned landscape, weather sequence, and observation timeline with no hidden substitutions.

## 7. Phase 2 — Historical replay before live forecasting

Build a command-line replay runner that advances a simulated clock and releases each observation only when it would have been available.

Tasks:

- Separate observed_at from available_at in replay scheduling.
- Use archived weather forecasts issued before each forecast origin. If unavailable, label reanalysis or perfect-weather experiments as retrospective upper-bound experiments.
- Save each forecast origin, evidence set, configuration, outputs, runtime, and metrics.
- Implement persistence and labeled heuristic wind-envelope baselines.
- Evaluate matching horizons rather than comparing a one-hour forecast with an eventual final burn scar.
- Mask unavailable truth and document perimeter acquisition uncertainty.

Exit gate: rerunning the same manifest and seed reproduces results within declared numerical tolerances, and the evaluation prevents future observations from leaking into earlier forecasts.

## 8. Phase 3 — Deterministic scientific baseline

Implement a surface-fire spread-rate module based on a documented established formulation. Match its fuel parameterization and units exactly. Do not invent an approximate equation and describe it as an established implementation.

Implementation sequence:

1. Implement local rate-of-spread calculations with independent reference cases.
2. Validate wind, slope, and moisture response in their supported ranges.
3. Build homogeneous synthetic landscapes with known expected symmetry or directional behavior.
4. Implement constant-condition anisotropic minimum-travel-time propagation.
5. Check directional/grid bias using rotated versions of the same test.
6. Add heterogeneous fuels and terrain, boundaries, and no-data handling.
7. Implement time-varying propagation as a separate verified capability.
8. Add snapshots, restart, cancellation, and forecast contours.

Recommended progression: use static travel-time propagation as a baseline, then a time-stepped front solver with directional spread and documented numerical stability conditions for changing weather. Select the production solver after comparing accuracy, directional bias, memory, and runtime. A static shortest-path calculation does not automatically become a time-dependent solver by replacing its input arrays.

Represent nonburnable cells explicitly, but do not assume every road or fuel break is an absolute barrier. Spotting and crown-fire omissions must remain visible.

Compare selected cases to a documented reference configuration of FARSITE/FlamMap where access and licensing permit. Reference tools are comparison models, not observational truth.

Exit gate: deterministic forecasts pass physical/numerical checks and show useful improvement over the agreed baselines on development incidents. Unsupported regimes are flagged.

## 9. Phase 4 — Rust infrastructure exposure engine

This phase can start after domain contracts are stable; it does not depend on completing the spread solver.

Tasks:

- Bulk-load immutable regional asset snapshots.
- Benchmark a packed static index against an R-tree using actual asset geometries.
- Retain exact geometries outside the bounding-box index and query candidates first.
- Apply exact point, line, and polygon intersection after candidate filtering.
- Deduplicate results by incident, asset, horizon, and forecast version.
- Compute intersected road length and building area where useful.
- Define whether boundary touching counts as exposed and test that rule.
- Partition assets into spatial tiles for bounded memory and regional loading.
- Publish new index snapshots atomically while old queries finish.

For ensemble exposure, query each scenario's spread footprint when exact geometric exposure is required and aggregate per-asset outcomes. Do not estimate a building's exposure by simply summing correlated cell probabilities. Raster sampling may be used as a documented approximation after measuring its error.

Do not interpret a road intersection as road closure or a power-line intersection as network outage. Those require separate operating rules and network models.

Exit gate: indexed results match brute-force exact geometry checks on representative fixtures, and performance is measured on points, dense footprints, long lines, complex perimeters, and large result sets.

## 10. Phase 5 — Observation reconciliation

Begin with a transparent correction scheme before attempting a sophisticated filter.

Tasks:

1. Associate evidence with incidents using spatial/temporal gates and reviewed ambiguity handling.
2. Preserve evidence as immutable records.
3. Reconcile reliable perimeter observations with the state at their acquisition time.
4. Treat hotspots as evidence within a sensor footprint, accounting for quality and resolution.
5. Never interpret a missing detection as proof of an extinguished fire without a validated observation model.
6. Replay from checkpoints for late observations within a configured window.
7. Preserve historical outputs and publish a new state/forecast version.
8. Expose correction magnitude, supporting evidence, and unresolved conflicts.

Evaluate a controlled reset to trusted observed geometry against a weighted state update. Do not automatically force monotonically increasing mapped area when a new observation corrects an earlier mapping error; preserve the distinction between physical burn history and revised knowledge.

Exit gate: replay demonstrates whether updates improve forecasts over a no-update model, with correction failures and ambiguous incident merges explicitly measured.

## 11. Phase 6 — Ensembles and probability calibration

Create scenarios for uncertain wind, moisture, front location, and selected defensible parameters. Use physically plausible joint perturbations rather than unrelated random noise in every cell.

Start with a small ensemble for development and expand based on convergence experiments and resource budgets. Report requested, completed, and failed scenario counts. Do not present a partial ensemble as equivalent to a complete one.

Outputs:

- Conditional burn probability by horizon.
- Arrival-time distribution and probability of no arrival within the horizon.
- Scenario perimeter contours and coverage masks.
- Asset exposure frequency/probability.
- Uncertainty contributors and model applicability flags.

Use development incidents for calibration and held-out incidents for final evaluation. Evaluate reliability, Brier score, threshold-based missed exposures, and false-alert burden. Quantify ensemble sampling error. Explicitly distinguish uncertain inputs from missing processes such as spotting.

Exit gate: ensembles provide measurable value over deterministic forecasts, and any displayed probability is labeled appropriately as raw scenario frequency or calibrated probability.

## 12. Phase 7 — Service and live scheduling

Proposed endpoints:

```text
POST /api/v1/asset-imports
POST /api/v1/observations
POST /api/v1/forecasts
GET  /api/v1/jobs/{job_id}
GET  /api/v1/forecasts/{forecast_id}
GET  /api/v1/forecasts/{forecast_id}/exposure
GET  /api/v1/incidents/{incident_id}/state
GET  /api/v1/stream
GET  /health/live
GET  /health/ready
```

Large imports and forecasts return job identifiers. Responses include versions, timestamps, quality flags, and completeness. Add pagination or artifact downloads for large exposure lists.

Scheduling rules:

- Trigger on meaningful evidence/weather change and periodic refresh.
- Coalesce repeated updates within a configured window.
- Bound worker count and memory.
- Prioritize new incident evidence and interactive local requests.
- Cancel or mark superseded jobs when appropriate.
- Keep the latest completed forecast accessible with visible age while replacement work runs.
- Isolate source failures; enforce source-specific stale thresholds.
- Make ingestion idempotent with source keys and checksums.

Add reconnect/resume semantics for subscriptions using event IDs. HTTP retrieval remains the source of truth after dropped streaming messages. Authenticate deployments, limit payload sizes, validate artifacts, and protect import endpoints before external exposure.

Initial latency goals, subject to the Phase 0 workload definition:

- Normalize lightweight supported sensor messages at p95 under one second after receipt.
- Acknowledge queued forecast requests at p95 under 500 ms under the defined load.
- Target p95 under 50 ms for warm, regional exposure queries whose geometry and output limits are specified.
- Establish full-forecast budgets from measured landscape and ensemble workloads; do not claim sub-millisecond forecasting.

Measure source acquisition-to-availability delay separately from processing delay. Upstream feed cadence is not a service performance guarantee.

## 13. Phase 8 — Analyst dashboard

Use a web map with server-generated raster/vector tiles or bounded regional artifacts. Stream changes and metadata rather than millions of raw geometries.

Required views:

- Observed evidence with acquisition time and source.
- Estimated current front and its uncertainty.
- Forecast horizon selector.
- Burn probability and arrival-time products.
- Infrastructure exposure table linked to the map.
- Data-source health, forecast age, and quality/applicability flags.
- Historical replay with an explicit replay clock.

The interface must distinguish no data, unmodeled area, and low predicted probability. Show whether a probability is conditional on the current fire. Do not visually interpolate between satellite passes in a way that implies a new observation; label model animation as forecast progression.

WASM is optional for geometry inspection, local filtering, or compact offline tools after profiling. A 60 FPS target applies to interaction on specified devices, not to the scientific forecast update cadence.

Exit gate: an analyst can identify the latest evidence, forecast time, exposed assets, and major limitations without inspecting logs.

## 14. Scientific evaluation and release gates

Pre-register the evaluation protocol and numerical thresholds before final held-out testing.

Metrics:

- Spread: IoU/Dice, boundary distance, missed/overpredicted area, and arrival-time error where time-resolved truth exists.
- Probability: Brier score, reliability diagrams, and precision/recall at declared operating thresholds.
- Assets: missed threatened assets, false exposure alerts, arrival error, and lead time.
- Reliability: stale feeds, ingestion failures, incomplete forecasts, recovery time, and reproducibility.
- Runtime: p50/p95/p99 latency, peak memory, CPU utilization, candidate counts, and serialization size.

Use entire incidents as units for splits and uncertainty estimates. Nearby tiles from one incident must not appear across tuning and final test partitions. Include unusual winds and fuel regimes in a separately reported stress set. Report per-incident and per-horizon results, not only a favorable aggregate.

Required experiments:

1. Persistence versus heuristic envelope versus deterministic spread.
2. Fixed versus time-varying weather.
3. No assimilation versus observation updates.
4. Deterministic versus ensemble forecasts.
5. Resolution sensitivity and directional bias.
6. Missing/delayed source sensitivity.
7. Exact versus approximate exposure calculations, if approximations are used.

Release gates:

- Correctness and reproducibility checks pass.
- The model improves agreed decision-relevant metrics over baselines on independent incidents.
- Probability claims match demonstrated calibration.
- No-data/stale-data behavior is visible and tested.
- Runtime goals hold on documented hardware and representative workloads.
- Failure recovery preserves audit history and does not silently publish incomplete results.

A failed scientific gate triggers model/data revision or narrower supported scope, not a lower threshold selected after inspecting held-out results.

## 15. Deployment and operations

Package the server, worker, and frontend together initially with configurable worker isolation. Record build ID, model configuration, source policies, landscape versions, and dependency lockfiles.

Required operational controls:

- Structured logs keyed by observation, incident, job, forecast, and asset snapshot.
- Metrics for queue age, feed lag, worker memory, failed scenarios, and publish delay.
- Checkpointed jobs and recoverable metadata.
- Backups and a tested restore workflow.
- Retention policies for raw data and generated artifacts.
- Immutable published manifests and explicit supersession links.
- Rollback to a previous compatible build/configuration.
- A documented degraded mode for missing weather or observations.

Use synthetic fault injection for dropped sources, duplicate messages, worker crashes, full storage, and interrupted publication. The UI should continue serving the last complete version with an honest freshness status.

## 16. Later research tracks

Only begin these after replay and baseline evaluation are stable:

- Bézier/EWP front solver: compare front accuracy, topology robustness, and runtime against the raster baseline on the same incidents.
- Learned correction/surrogate: compare against the deterministic model, examine rollout drift, validate out-of-region behavior, and retain a fallback.
- PhysFire-WM reproduction: confirm data/code access and match input/output semantics before local adaptation.
- Regional occurrence model: separate labels, prediction horizons, calibration, and evaluation from active-fire spread.
- Suppression/asset-network optimization: consume forecast scenarios and clearly stated intervention assumptions.
- Crown fire and spotting: use independently tested modules and expand applicability claims only after validation.

Keep challengers behind shared interfaces. A more complex model replaces the baseline only when accuracy, uncertainty, runtime, and maintenance results justify it.

## 17. Milestone order and indicative effort

These are planning ranges for one experienced engineer, assuming usable data access and periodic domain review. They are not delivery commitments; historical data preparation and scientific validation can dominate effort.

| Milestone | Indicative effort | Completion artifact |
|---|---:|---|
| Pilot and evaluation specification | 1–2 weeks | Frozen pilot/split/evaluation documents |
| Data normalization and replay | 2–4 weeks | Reproducible incident replay |
| Deterministic spread baseline | 3–6 weeks | Verified solver and comparison report |
| Exposure engine | 1–3 weeks | Correctness and realistic benchmark report |
| Observation reconciliation | 2–4 weeks | Assimilation ablation report |
| Ensembles and calibration | 2–4 weeks | Probability evaluation report |
| Service, dashboard, operations | 3–5 weeks | End-to-end analyst pilot |
| Independent validation and revision | 2–4 weeks | Release decision and limitations report |

Some work can overlap after contracts stabilize. A planning envelope is approximately four to eight months for an evaluated pilot, with wider uncertainty if data or domain support is limited. A visible prototype can arrive earlier, but it should not be confused with a validated forecasting product.

## 18. First implementation backlog

1. Select the pilot region and document available incident progression.
2. Create the Cargo workspace and shared typed contracts.
3. Write one immutable observation manifest and fixture incident.
4. Implement import/inspect commands and timestamp/CRS validation.
5. Prepare one aligned landscape tile and weather sequence.
6. Implement the replay clock and availability-based data release.
7. Generate persistence and heuristic-envelope baseline outputs.
8. Implement reference-checked local spread-rate calculations.
9. Build a constant-condition propagation fixture and evaluate grid bias.
10. Load one real asset tile and verify indexed versus brute-force intersections.
11. Publish the first replay evaluation report with clear missing-data limitations.
12. Use measured failures to set the next solver and ingestion priorities.

The first reviewable milestone is a reproducible historical incident flowing from evidence through a baseline forecast to asset exposure, accompanied by an evaluation report. Live feeds, larger ensembles, and UI polish follow that foundation.

## Reference basis

- NOAA GOES fire product characteristics: https://vlab.noaa.gov/web/towr-s/goes-fhs
- US Forest Service FlamMap/FARSITE overview: https://research.fs.usda.gov/firelab/products/dataandtools/flammap
- MTT assumptions: https://owfflammaphelp62.firenet.gov/Tech_Topics/Tech_MTT.htm
- Local research extracts reviewed in the preceding planning discussion: tmp/pdfs/p1.txt through p9.txt. These are research references, not implemented modules or reproduced results.
