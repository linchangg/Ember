# Next tasks after the Rust adapter

Work in this order. Each item has a concrete implementation and acceptance gate.
The backend is available now; synthetic forecast skill and real-time speed still
require work. Do not substitute fixture output for historical evaluation.

## 1. Finish Cody's forcing/runtime assembly (Cody + Abby, highest priority)

Current `main` contains HRRR writing, replay sources, risk/alerts, cache, clock and
asset summarization. The Rust integration now exercises those existing functions.
`ForcingProvider` and the top-level replay orchestrator remain the missing assembly.

- Use `hazard.factory.create_hazard_model()` and the exact `hazard.api` dataclasses.
  For the surface backend explicitly configure `spotting=False`; retain N=16 as a
  visibly labeled development profile and N=128 as the target profile.
- `weather_from_hrrr(ds,grid,issue_ms)` accepts the current `hrrr.forcing_dataset`
  format, checks cell centers and earth-relative wind, removes unavailable fxx bands,
  and returns the release-time audit. Select the latest initialization with eligible
  bands, falling back to older files. Persist file checksum + selected-band audit.
  `valid_time > issue` is legal for a forecast; `available_time > issue` is not.
- In each 5-minute cycle collect `(previous_issue,issue]` observations once, activate
  eligible forcing, call `step`, derive zone/Area X arrivals with frozen masks, call
  the existing summarizer and risk/alert functions, then atomically publish the bundle.
  Build the cache key from previous key, observation evidence hashes, model identity,
  scenario/config hash and forcing audit; IDs alone do not cover changed evidence.
- Checkpoint every 30 simulation minutes using the existing cache API. For a branch,
  load the newest compatible checkpoint at/before the injected report, then replay
  evidence and forcing by availability. Preserve operator alert state outside physics.
- Gate: native-output integration test remains green; one full replay through the
  real orchestrator survives restart, weather replacement, delayed report, backward
  seek and branch creation; parent and identical branch match bit-for-bit.

## 2. Connect Linda's existing dashboard (Linda + Cody)

- Implement/finish her `FileSource` behind the agreed data-source interface. Read
  `static/<scenario>/manifest.json`, sorted cache index, and the latest bundle at/before
  the current replay clock. Resolve asset URLs relative to the fixture hosting root.
- Decode `arrival_q.bin` as `(5,ny,nx)` north-first uint8. `255` is unreached, `0` is
  burned, other values are two-minute leads. Derive lead selection from forecast issue,
  not browser wall time. Use exact corner mapping to avoid north/south flips.
- Cancellation/version tokens must prevent a slow old request overwriting a new seek.
  Display ESS, tempering, simulation time, data-through time and synthetic provenance.
  Reuse the dashboard's design; the debug `demo.html` is only a reference decoder.
- Add component/browser checks that load the generated **Rust** fixture, select all
  quantiles and a 120-minute lead, seek across pre-ignition, and inspect an assimilated
  report and an alert. Verify missing asset handling and quantile 255 transparency.
- Gate: `tools/check_hazard_frontend.mjs` passes via HTTP and Linda's real UI renders
  the same selected raster/zone values. Capture dashboard proof; test SpacetimeSource
  separately against the published hub after the FileSource path works.

## 3. Resolve parameter identifiability and pass the twin gate (Abby)

Run `python -m emberline.hazard.evaluate_assimilation --out ... --members 32`, then
repeat with 128 and predeclared seeds. Hidden truth is ROS×1.6, wind bias +20° and
urban head rate 2 m/min; observations use five-minute GOES footprints and two VIIRS
passes. The current evaluator records failures rather than asserting success.

- Separate likelihood-only, reanchor-only, and combined runs. Track which member
  trajectories are corrected, parameter ancestry, alpha, likelihood contributions,
  reseed events, and posterior correlation of ROS/wind/moisture. Reanchoring can
  explain geometry without identifying spread parameters; do not equate the two.
- Check that prior particles cover the truth. Quantify degeneracy between wind speed,
  moisture and ROS. Compare hierarchical/fixed-nuisance variants with the same evidence;
  do not tune toward a hidden truth known only to the evaluator.
- Add clear/negative VIIRS samples and realistic detection errors/availability delays,
  and vary fuel, slope and boundary distance. Record sensitivity to GOES temporal
  correlation; cap/composite correlated evidence only under an explicit calibrated policy.
- Investigate progressive tempering that consumes the remaining likelihood with
  rejuvenation/reforecast between stages, or parameter moves conditioned on corrected
  state. Compare calibration and runtime against the present one-stage policy.
- Gate from spec/02: angle within 8°, ROS within 25% after two hours; ESS ≥.2N on
  ≥80% cycles; PF beats open-loop Sørensen at +2h. Report all metrics over seeds.
  Fix implementation defects with targeted tests; distinguish statistical limitations.

## 4. Measure and reconcile the low-trust report gate (Abby + product owner)

The reproducible report experiment places a trust-.25 hot report 1 km ahead of the
burned q50 head at minute 60, compares the q50 +2h contours, and reports symmetric
mean nearest-boundary distance plus east-head displacement over seeds 7/8/9.

- Expand to at least ten seeds and several report directions/radii. Compare near-front,
  outside-support and clearly contradicted reports; plot q10/q30/q50/q70/q90 changes
  and changed probability mass, not just one quantile.
- With only .25 of members reanchored, a 300 m q50 movement is not guaranteed.
  Check whether the intended product criterion concerns head displacement, local
  probability, Hausdorff distance, or mean boundary movement. Preserve the recorded
  definition when comparing results; changing the gate needs a documented decision.
- Do not raise trust, shift every particle, or alter tests merely to force a dramatic
  q50 response. Calibrate report likelihood/radius/false-positive rate against evidence.
- Gate: honest repeated measured effect, bounded false-report influence, branch-only
  change, unchanged parent, no future use; then explicitly pass or revise the 300 m target.

## 5. Replace full replay with persistent/incremental Rust state (Abby, performance P0)

- Establish an isolated 128×340×340, 90 m, 6 h baseline using `hazard.benchmark`.
  Measure p50/p95 across ten cycles, with and without resampling/late anchors; measure
  simultaneous process-tree RSS separately from individual process peaks.
- Keep member native states at the current issue and snapshot before forecast extension.
  Reuse projected static fields, fuel catalogs, neighbor/boundary structure and weather
  allocations. Forecast from the snapshot without advancing the authoritative issue
  state into the future. A persistent worker or PyO3 binding can remove file/process I/O.
- For delayed observations, restore a compatible earlier state and replay only affected
  members/intervals. If jitter changes parameters, explicitly choose full replay or a
  checkpoint-conditioned parameter policy; never keep old arrivals under a new model
  without documenting the approximation.
- Introduce a narrow band or active-cell tile list only with front-speed halo bounds,
  water-wall/edge tests and parity against the full-grid solver. Benchmark the real
  40-fuel heterogeneous landscape; homogeneous behavior-cache wins do not generalize.
- Benchmark an explicitly versioned Rust arrival-time/MTT alternative against LF.
  For stationary forcing the anisotropic arrival equation is `H(grad(T))=1`, which
  may avoid hundreds of full-grid time steps. Changing weather violates a stationary
  solve's assumptions: retain timed forcing, causality and delayed-evidence replay,
  and compare fronts/arrivals before choosing it. Keep the Python contract unchanged.
- Gate: unchanged arrival/quantile/assimilation tests, same seeded branch behavior,
  bounded memory, deadline recovery, then measured ≤0.5 s/cycle on the named demo
  laptop or an explicit revised target. Do not advertise instantaneous full forecasts.

## 6. Historical Eaton evaluation with frozen inputs (data team + Abby)

- Obtain actual replay manifest, per-pixel GOES/VIIRS tables, forcing availability,
  LANDFIRE/DEM/urban rasters and independent timestamped fire perimeters. Checksum
  and version every source; ensure evaluation perimeter observations were not also
  assimilated or used to tune that run.
- Run `none`, `reinit`, `pf` with identical seed, forcing, domain and observations.
  Record forecast issue/availability and horizons 30/60/120/360 min. Compute Sørensen,
  IoU, burned-area bias, boundary distances, arrival error and probability calibration.
- Freeze Area X, report eligibility, latency assumptions, forecast config and held-out
  split before comparing the honest-number result. Publish uncertainty intervals and
  failed cases. Existing synthetic fixture numbers cannot support historical claims.
- Gate: reproduce spec/02 historical comparison, diagnose spread/boundary/observation
  failures separately, and document a baseline improvement without hindsight leakage.

## 7. Physics completeness and independent checks (Abby)

- Preserve all pinned Behave oracle cases and rerun their regeneration on source changes.
  Add a separately installed pyretechnics comparison for the frozen spec's 2% gate;
  record matched fuel/moisture/wind-cap/dynamic-curing conventions. Passing Behave
  references is useful evidence but is not a pyretechnics comparison.
- Expand 5 m convergence, slope/wind interaction, footprint geometry, angular-front and
  discontinuous forcing tests. Distinguish time-integration error, grid diffusion and
  uncertain physics. Add saturated reinit-domain support or an explicit domain-growth API.
- Validate NB91 head/back/flank speeds and barriers against a stated heuristic dataset.
  Building fraction currently does not alter urban spread; canopy height currently
  does not alter WAF. Temperature/RH are validated/retained but moisture is sampled
  directly. Implement additional dependence only with units, tests and evidence.
- Spotting is absent and rejected. If added, use a separate explicit feature version,
  branch-independent particle RNG, ember transport/ignition equations, nonburnable
  landing checks and clear uncertainty/provenance. Keep it off until tested.

## 8. Versioning, packaging and operations (Abby + Cody)

- Build a supported release artifact containing the Python package, catalogs, source
  identity manifest and platform-specific native binary. Current source-tree lookup
  is intentional and must be replaced before standalone wheel deployment.
- Add a checkpoint inspection/migration command. Never silently load changed numerical
  code or dependency versions; supported migrations must compare next-step forecasts
  and preserve evidence/forcing provenance. Provide a restart-from-original-input path.
- Extend cache keys with weather and model fingerprints; publish source identity in
  cycle provenance. Stream structured diagnostics for runtime, ESS, alpha, resampling,
  anchors, missing data, domain truncation and backend failure.
- Gate future Cody/Linda changes with the API probe, all tests, full fixture, Node HTTP
  consumer and actual dashboard integration. Add malformed/corrupted response and
  process-crash tests before running a long-lived native service.

### Reconcile the parallel research adapter

The last fetch also found `research/surface-v1-adapter` at `8e4055b`, based on the
same `8cea5fc` main. It adds 38 research tests and an explicit CLI compatibility map;
it does not implement HazardModel, weighted inference, SB40/urban mapping, or live
assimilation. Keep its research role distinct from the production-facing factory.
Its single-member 64×64/one-hour smoke timing is not comparable to the 128-member
340×340/six-hour benchmark in this change.

Before combining that branch, compare and port useful malformed-output, origin,
time-precision, censoring and checkpoint-reference cases. Our operational policy
rejects incomplete native ensembles, retains no-arrival mass, explicitly seeds
modeled ignition history and selects the owner-approved surface backend. Reconcile
the research document's former “awaiting decision” entries against this guide;
do not overwrite the newly implemented public API or change CLI research semantics
silently. Run both test sets if the research utilities are imported.

## 9. Learned correction models only after the above (research, P2)

There is no implemented deep-learning component. Start with a calibrated physical
baseline and incident-disjoint train/validation/test sets. A constrained residual
spread multiplier or likelihood calibration model is a smaller first target than
replacing propagation. Inputs may use only information available at issue; outputs
must retain nonnegative spread, nonburnable barriers and uncertainty.

Compare against `none`, `reinit`, `pf` on held-out fires, including calibration,
latency and missing-sensor robustness. Export a versioned ONNX model only after a
measured benefit; add Rust/Python inference parity, feature normalization metadata,
an out-of-distribution fallback and model-file checksum. Do not describe this as a
current engine capability or require it to unblock the working fixture.
