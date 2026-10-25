# Framework for the next six implementation phases

Baseline: PR #11 merged the runtime/dashboard connection; PR #12 reconciled the
plans. This framework adds release tooling and instructions, not new spread physics,
calibrated parameters, a full historical run or deployed services. Read
`AGENTS.md`, `SPEC.md`, `spec/00_contracts.md`, `docs/build_process.md` and the
assigned component spec before each phase. The public HazardModel and wire types
remain authoritative. Model changes require an explicit numerical identity/version.

## Work and approval protocol

**Stop before starting R1 and obtain the owner's direct confirmation.** Approve one
phase at a time; completing its acceptance checks does not authorize the next phase.
The six phases below are proposed work packages. Owners are coordination suggestions,
not assignments sent to teammates. External paid compute and actual calls need the
specific resources/recipient authorized before use.

For each approved phase:

1. Fetch current main and create a branch named `release/r1-eaton-replay`,
   `release/r2-dashboard-rehearsal`, etc. Record the exact base SHA.
2. Copy `docs/templates/release-phase-evidence.json` into a new run directory outside
   tracked input data. Fill its scope, approved phase, environment and input hashes.
   Never copy secrets, phone numbers, credentials or transcripts into public evidence.
3. Make one bounded change at a time; preserve the current fixture and homepage.
   Run its listed checks, retain failing cases, and record elapsed time/configuration.
4. Produce the artifacts below, a reviewable diff and a PR. State what passed, failed,
   remains unknown and how to reproduce it. Do not change an acceptance target to
   make a test pass. An approved target revision is a separately recorded decision.
5. After review/merge, update the evidence ledger and ask for the next phase's approval.
   A successful software check is not automatic scientific or delivery acceptance.

Record `not_run`, `blocked`, `failed` or `passed` per gate; unknown is not passed.
The JSON template is an internal evidence worksheet, not a frozen shared wire schema
or machine-verifiable proof of human approval. Git history and the owner's message
supply the actual approval record.

## Common setup and regression gate

From the repository root, using Python 3.12 and the locked toolchains:

```sh
uv sync --project engine --locked
npm --prefix web ci --ignore-scripts
npm --prefix stdb ci --ignore-scripts
cargo build --manifest-path physics/surface_v1/Cargo.toml -p fire-bridge --release --locked
uv run --project engine python -m ember.hazard.compatibility
uv run --project engine pytest engine/tests -q
PYTHONPATH=engine uv run --project engine python -m unittest discover \
  -s scripts/validation -p 'test_release_audit.py' -v
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run lint -- --deny-warnings
npm --prefix web run build
```

Run affected tests while implementing, then the full relevant gate before a phase
is complete. Physics changes additionally require `cargo test --manifest-path
physics/surface_v1/Cargo.toml --workspace --locked`. The CI workflows
`hazard-backend.yml`, `dashboard-hub.yml`, `web-and-hub.yml` and `surface-v1.yml`
define the existing platform/browser/native checks; preserve them. The new inventory
regressions also run in `release-framework.yml` when their code, contracts or fixtures change.

## R1 — complete and verify the Eaton replay (Cody + Abby)

**Goal:** complete reproducible caches and establish a usable real-data demo before
claiming accuracy. Current baseline: 80/157 main issues, 16-member development
forecasts, ending at 2025-01-08 08:35 UTC. Data smoke verified 22 HRRR files and causal
weather at 157 issues; it is not a full target-profile replay.

Existing code: `scripts/precompute.py`, `runtime/{loop,model_host,cache,sources}.py`,
`sync/cache_sink.py`, `data/{hrrr,statics}.py`; paths are under `engine/ember/`.
Use these components instead of adding another orchestrator.

1. Run `scripts/validation/eaton_data_smoke.py` with `PYTHONPATH=engine` and the
   engine environment. Review raw source/version and forcing availability metadata.
2. Audit the existing cache (exit 1 is expected while incomplete):

   ```sh
   PYTHONPATH=engine uv run --project engine python scripts/validation/release_audit.py \
     --scenario-dir data/scenarios/eaton --asset-root . --branch main --profile dev \
     --out /tmp/eaton-initial-audit.json
   ```

   Use a fresh `--out`; the tool refuses to overwrite evidence. `--profile target`
   additionally rejects member-count mismatch and development-labelled outputs.
   The audit reads existing files only and never computes forecasts or sends traffic.
3. Create a fresh run root, for example `/tmp/eaton-r1-run`. Store its metadata/logs
   there; reserve `cache/` beneath it for the model. Generate one development branch:

   ```sh
   uv run --project engine python -m ember.scripts.precompute --scenario eaton \
     --branch main --filter pf --profile dev --workers 2 --memory-gb 2 \
     --timeout-s 900 --no-briefings --cache-root /tmp/eaton-r1-run/cache
   ```

   This is a full potentially lengthy run, not a three-cycle timing option. For a
   bounded timing pilot, add and test an explicit cycle-limit option to precompute;
   no such CLI option is currently available. Do not edit the scenario's
   end time silently. Do not resume old physics under changed fingerprints.
4. Run the audit with `--asset-root /tmp/eaton-r1-run`. Cache URLs are rooted at
   `cache/{scenario}/...`; do not serve the `cache` directory itself as the URL root.
   Add `static/eaton` from the same scenario for dashboard use. Verify manifest/grid
   agreement and every referenced resource through the intended HTTP asset host.
5. Generate named `openloop`/`reinit`/`main` caches using `--filter none/reinit/pf`.
   Keep sensors, report policy, seed and input hashes equal; explicit overrides go in
   run metadata. Repeat with `--profile target` only after measuring resources and
   approving the run environment. Never label N=16 as N=128.
6. Rehearse checkpoint restart, backward seek, injected delayed report and identical
   child branch. Verify branch-independent RNG and unchanged parent bytes. Run
   `engine/tests/test_engine_native.py` plus the full engine suite.

Artifacts: branch indexes/bundles/assets, audit JSON for each branch/profile,
source/forcing identities, logs, checkpoint/branch comparison and resource measurements.
Gate: all intended 157 issues per full-length branch; no invalid/missing assets,
future evidence or unlabelled development data. A deliberately shorter report branch
must declare its fork/end/expected issues and gets a separate acceptance check; the
current inventory expects full-length branches. Preserve original precomputed caches.

## R2 — Eaton dashboard and hub rehearsal (Linda + Cody)

Dependency: R1's usable asset set; synthetic UI regression can be run independently.
Keep the homepage globe intact; verify `/dashboard` and each homepage widget separately.

1. Follow `DASHBOARD_PHASE_E.md` for exact synthetic fixture/hub startup and seeding.
   The existing seed helper is localhost-only and uses disposable credentials. Do not
   reset a shared database. Use one scenario per database under the current schema.
2. For Eaton file mode serve the R1 root with `static/eaton/manifest.json` and cache
   resources at their declared paths; set `VITE_DATA_SOURCE=file`,
   `VITE_SCENARIO_ID=eaton`, `VITE_ASSET_BASE` to the asset root. Verify CORS explicitly.
3. Add an Eaton rehearsal configuration to existing browser acceptance tests; reuse
   assertions from `web/e2e`, `tools/check_dashboard_source.mts` and
   `tools/check_dashboard_panels.mts`. Avoid brittle exact screen/pixel expectations.
4. Verify pre-ignition, first/final issue, all quantiles, +120 min lead, pause/play,
   rapid forward/backward seeks, branch comparison at identical issue/lead, visible
   provenance and `255` transparency. Missing/truncated assets must hide invalid
   numbers; a late response must not overwrite a newer selection.
5. In a dedicated local hub, run the actual runtime with Eaton inputs, then repeat
   with two browsers. `ember.run` currently uses the canonical cache root and
   fixture-only bundle replay. Add/test explicit cache-root or generic bundle-replay
   support if R1 artifacts must be consumed by the runtime; file mode already consumes
   a separate served root. Do not assume `run --cache-root` exists.
6. Verify shared review state, PIN rejection, worker-only publication, publication
   retry and a genuine hub restart. No notifier runs during this test.

Artifacts: browser results, selected cycle/risk/asset hashes, screenshots, restart log,
a page-by-page source matrix (live hub/file/model simulation/mock) and run commands.
Gate: both browsers agree after reconnect, no future/stale mixed issue, parent remains
unchanged, no secrets in browser assets. Run the existing five integration checks.

## R3 — independent historical validation (data team + Abby)

Dependency: independent truth and causal forecast export. Start truth acquisition in
parallel with R1 if separately approved; do not call assimilation pixels ground truth.

1. Implement an importer `hazard/truth_import.py` with tests for invalid geometry,
   CRS/grid mismatch, holes/multipolygons, timestamp errors and unknown coverage.
   Each source needs valid time, publication/source time, provenance and checksum.
   Rasterize on the frozen evaluation grid; outside mapped coverage remains unknown.
2. Predeclare incident-disjoint train/calibration/test splits, seeds, cases and leads
   30/60/120/360 min. With Eaton alone report a case study, not generalization.
3. Implement `hazard/historical_runner.py` using the existing runtime/model factory.
   Reuse its availability policy and evidence collection; isolate internal raw-member
   access in one exporter. Follow `HISTORICAL_VALIDATION.md` and the existing schema.
   Export absolute member arrival minutes since `t_ref`, matching weights, issue,
   grid identity, model/config hashes and every consumed input. Quantile rasters alone
   cannot recover proper probability scores. Preserve persistence plus none/reinit/pf.
4. Build the frozen experiment manifest and truth NPZs. Match truth valid time exactly
   to issue+lead; the current scorer does not interpolate. Exclude truth from forecast
   inputs by both ID and content hash. Record failures/missing cases, not just matches.
5. Run the scorer:

   ```sh
   uv run --project engine python -m ember.hazard.historical \
     --manifest /path/to/frozen-experiment/experiment.json \
     --out /path/to/reports/historical-report.json
   uv run --project engine pytest engine/tests/test_historical.py -q
   ```

Metrics on valid cells: `p_c = sum_m w_m I(T_mc <= target_time)`,
`BS = mean((p_c-y_c)^2)`, `IoU = intersection/union`,
`Sorensen = 2*intersection/(predicted_area+observed_area)`.
Include area bias, boundary mean/p95 and reliability bins. Aggregate within incidents
before averaging incidents; use incident-level bootstrap only with enough incidents.
Arrival error needs independent arrival brackets; do not infer it from one outline.

Artifacts: importer tests, frozen manifest/NPZs/source ledger, provenance audit,
per-case scores/failures and incident/lead summaries. Gate: reproducible leakage-free
scoring and explicit scientific review. Scorer success alone does not certify skill.

## R4 — controlled scientific calibration (Abby)

Dependency: R3's splits and truth for promotion; synthetic ablations may run first.
Implement one candidate per version; keep the default model fixed until approval.

1. Reproduce `hazard.evaluate_assimilation --out NEW.json --members 32 --workers 2`,
   then N=128 on the approved environment. The current CLI has no seed argument;
   add/test a configurable seed list or a dedicated experiment runner. Save all seeds.
2. Freeze search spaces, parameter units, objective and failure limits before viewing
   test data. Start with ROS/wind and fixed nuisance moisture/urban parameters; inspect
   support and correlations before adding more free variables.
3. Compare likelihood-only, reanchor-only and combined modes. Log parameter ancestry,
   ESS `1/sum(w_m^2)`, tempering alpha, reseeds, boundary changes and runtime. A corrected
   front does not establish recovery of true physical parameters.
4. Evaluate the opt-in heat/location candidate in `hazard/calibration.py` against the
   present likelihood, using coverage-verified negatives and time/overpass blocks.
   Fit calibration incidents only; score log loss/Brier/reliability on held-out data.
5. For wind adjustment or RK2 candidates follow `SCIENTIFIC_CALIBRATION.md` source
   equations, units and oracle/convergence requirements. Reverify original primary
   sources before coding; don't activate partial formulas or reuse sensor parameters
   from another instrument. Progressive tempering needs valid rejuvenation/reforecast.
6. Evaluate historical none/reinit/pf and candidate on frozen held-out cases, including
   sensor dropout and low-trust false-report stress tests. Quantify runtime tradeoffs.

Artifacts: predeclared experiment, fitted parameters with units/source, ablations,
reliability/skill plots, all seeds and model/checkpoint-version decision.
Gate: existing twin targets (wind <=8 degrees, ROS <=25%, ESS>=0.2N for >=80% cycles,
improved +2 h outline over open loop), independent historical evidence, oracle and
regression parity, and reviewed failure limits. A target revision requires approval.

## R5 — performance with numerical parity (Abby + Cody)

May be separately approved after R2; no need to wait for calibration to profile the
unchanged baseline. Current cold target-size measurement is 94.649 s, not a p95.

1. Run `hazard.benchmark --out NEW.json --members 128 --size 340 --workers 2` on a
   named machine. Capture OS/CPU/build identity, workers, active frames and memory.
   Profile actual Eaton forcing/fuels separately from the homogeneous synthetic case.
2. Add `hazard/benchmark_suite.py` with warmup and at least 10 measured sequential
   issues, controlled member count, cold/warm separation and p50/p95. Keep scenarios
   for no observations, PF resampling and delayed-anchor replay. Sample the full
   process tree concurrently; individual maximum RSS values cannot be summed into
   a simultaneous peak. Emit raw timings and failures before summary statistics.
3. Profile native propagation versus projection/serialization/process startup. Implement
   persistent worker and issue-time member snapshots first; forecast extensions must
   not advance authoritative assimilation state into the future. Cache immutable
   landscape/weather transforms by content identity. Bound queues and timeouts.
4. Introduce incremental advancement, then active tiles/narrow bands only with a
   sufficient propagation halo and full-grid parity. Changed parameters require a
   physically consistent replay policy, not old arrivals under new parameters.
5. Add parity cases for discontinuous weather, barriers, heterogeneous fuels, boundary
   exit, arrival interpolation, delayed evidence, restart and branch determinism.

Artifacts: raw/summary benchmark JSON, profiler output, architecture note, native
parity tests and recovery behavior. Gate: frozen numerical tolerance and branch
behavior pass; measured target <=0.5 s/cycle on named hardware or explicitly approved
revised target. Faster file display is not a faster full forecast.

## R6 — report and notification services (Cody + Linda)

Dependency: R2's operational report/branch path. Read `spec/05_ai_voice.md` and
`spec/07_phone.md`; verify current official provider APIs before implementation.

1. Inventory existing `api/local.py`, `notify/cartesia.py`, `ai/validate.py`, phone
   controls and hub reducers. Implement missing `web/api` handlers named by the spec
   using existing contracts; do not invent another report schema. Any required schema
   migration stops for Cody/owner approval.
2. Validate audio size/type/duration, rate limits and request shape. Derive trusted
   identity server-side; bind reports to scenario/person and validate observed time.
   Availability time comes from trusted receipt, never an arbitrary client value.
   Parse/read back a report without inventing a location; preserve provenance/trust.
3. Publish through service identity, consume exactly once by report ID, and fork only
   the intended branch. Test a delayed report, malformed location and duplicated
   request against the actual worker. Forecast numbers remain engine-owned.
4. Dispatch only an approved, current alert. Store an idempotency key based on alert,
   revision and channel. If a timeout leaves delivery uncertain, reconcile provider
   status before retrying; do not blindly place another call. Verify signed callbacks,
   replay protection, duplicate/out-of-order callbacks and bounded retry/backoff.
5. Test with a fake provider first: rejected auth, rate limit, expired proposal,
   timeout, 429/5xx, duplicate approval and failed delivery. Preserve grounded template
   fallback and human approval; never mark a request accepted as a delivered call.
6. Only after explicit recipient/provider approval run one real rehearsal. Store a
   redacted receipt and outcome. Keep tokens and phone numbers out of evidence/UI builds.

Artifacts: endpoint tests, provider stub, worker/report branch integration result,
callback/state-machine diagram, configuration checklist and redacted delivery receipt.
Gate: authenticated input -> correctly timed report -> branch -> published forecast;
approved alert -> at-most-once intended dispatch -> reconciled delivery status.
Provider guarantees and observed behavior must support any at-most-once claim; if not,
report the exact duplicate/unknown-outcome limitation. No public-safety claim follows.

## Evidence and handoff

Use `docs/templates/release-phase-evidence.json`; large artifacts belong outside Git
with checksums and retrieval instructions. Small audit summaries belong under
`docs/evidence/`. For every phase link its approval, exact Git revision, input hashes,
commands, test output, blockers, rollback and next request. Preserve historical plans
as records, but keep `RELEASE_READINESS.md` and `RUST_HAZARD_NEXT_TASKS.md` current.
No phase is currently authorized by this framework alone.
