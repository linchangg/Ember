# Dashboard–engine connection plan

Status updated 2026-10-25: **Phases A–E are implemented and merged** in
[PR #11](https://github.com/linchangg/Legendary-Fire-Project/pull/11), main `0308ba8`.
The connected forecast view runs at `/dashboard` in explicit file or hub mode.
Linda's homepage globe remains preserved. The phase instructions below retain the
original implementation sequence; they are not a list of unstarted phases.
See [release readiness](RELEASE_READINESS.md) and [next tasks](RUST_HAZARD_NEXT_TASKS.md)
for current acceptance gaps and [Phase E](DASHBOARD_PHASE_E.md) for launch commands.

## 1. Readiness and release boundaries

| Capability | Current evidence | Status |
|---|---|---|
| Rust behind Python HazardModel | Native propagation, assimilation, checkpoints; 36 forecast + 3 pre-ignition fixture cycles and report branch | Implemented |
| File/hub forecast dashboard | All five PR checks passed, including Linux/macOS fixtures and live-hub restart/two-browser acceptance | Merged at `/dashboard`; real-data rehearsal remains |
| Eaton inputs | 22 HRRR checksums; causal selection at 157 issues; two-member real-input PF smoke uses 186 observations | Inputs present; committed main cache covers 80/157 issues |
| Scientific calibration | Synthetic improvement does not establish parameter recovery or held-out skill | Incomplete |
| Historical validation | Scoring framework exists; independent timestamped burned-perimeter truth absent | Not established |
| Performance | One cold N=128, 340×340, six-hour forecast: 94.649 s vs 0.5 s target | Target not met; not a p95 |
| Report/notification service | Local report path, request builder and call cards; Vercel endpoints/callbacks absent | Incomplete; no external delivery tested |

Latest local engine suite: 311 passed / 18 skipped; canonical web suite: 48 passed /
1 paired-fixture skip. Typecheck, lint and production build passed. Software checks
are separate from predictive skill. Spotting remains disabled; no deep-learning
component exists. Future phases still require the owner's direct confirmation.

## 2. Data path and ownership

```text
Eligible weather + observations
  -> Python runtime / HazardModel
  -> Rust subprocess bridge and native propagation
  -> Python assimilation, weighted arrival quantiles, risk/alert calculations
  -> immutable CycleBundle + binary/GeoJSON assets
  -> FileSource initially, SpacetimeSource later
  -> shared dashboard state and hooks
  -> Linda’s existing map, timeline and sidebar
```

Abby owns engine output/decoder parity; Cody owns runtime, asset hosting and hub
publication; Linda owns the data-source hooks and rendering integration. Renderers
must not launch Rust, recompute spread or manufacture risk values. Use the operational
`engine/emberline/hazard/` factory, not the separate unweighted research CLI adapter.

Read `spec/00_contracts.md` §§3–4, `spec/06_frontend.md` §§2–4, the existing
`web/src/contracts.ts`, `tools/check_hazard_frontend.mjs`, and
`docs/RUST_HAZARD_BACKEND.md`. Existing shared contracts are authoritative. Older
frontend renderer/route suggestions are not a reason to replace the current UI.

## 3. Phase A — establish a reproducible file-backed fixture

1. Build native release code and generate the Rust fixture, using the locked Python
   environment:

   ```sh
   cargo build --manifest-path physics/surface_v1/Cargo.toml -p fire-bridge --release --locked
   engine/.venv/bin/python -m emberline.hazard.demo --out /tmp/emberline-dashboard-fixture --members 16
   node tools/check_hazard_frontend.mjs /tmp/emberline-dashboard-fixture
   ```

2. Serve that directory as an asset root. It must expose
   `static/rust-fixture/manifest.json`, `cache/rust-fixture/main/index.json`, and
   every relative asset referenced by its bundles. Do not overwrite canonical
   `fixtures/` or reuse synthetic files under a historical-data label.
3. Configure the existing Vite application using `VITE_DATA_SOURCE=file`,
   `VITE_SCENARIO_ID=rust-fixture`, and `VITE_ASSET_BASE` pointing to that root.
   These variables are the planned selection mechanism and need wiring into the
   application. Use a same-origin development proxy or explicit allowed-origin CORS
   on the asset host; do not rely on an ordinary cross-origin static server allowing it.
4. Run the Node consumer against the actual HTTP asset URL before browser work.
   It must accept all 36 forecast and 3 pre-ignition entries.

Acceptance: a repeatable generation command and HTTP root pass the existing decoder;
missing files return visible failures rather than substituted mock predictions.

## 4. Phase B — shared FileSource and consistent time selection

Add these modules without changing shared wire types:

- `web/src/data/source.ts`: internal DataSource and snapshot/subscription interfaces.
  Expose manifest, clock, branches, cycles and bundle-derived collections, plus
  disposal and actions. Use types from `contracts.ts`; isolate mock/file/hub selection.
- `web/src/data/fileSource.ts`: fetch manifest/index, validate schema and IDs, load
  bundles, implement local replay and the specified offline alert transitions.
- `web/src/data/store.ts` and `hooks.ts`: implement the hooks in frontend spec §2,
  so components no longer fetch forecast data themselves.
- `web/src/data/arrivalGrid.ts`: validated binary loading/decoding with a four-entry
  LRU keyed by scenario, branch, cycle, model/config identity and asset URL.
- `web/src/data/assetUrl.ts`: resolve ASSET_BASE-relative paths centrally. Reject
  absolute/traversing/unexpected-origin asset references.

Implementation rules:

1. Validate schema version 2, scenario/branch identity, grid dimensions, quantile
   order, time fields and asset response status before exposing a snapshot.
2. Sort index entries by `knowledge_sim_ms`; reject duplicates/inconsistent entries.
   At simulated now `s` and view time `v`, select the latest eligible cycle with
   `knowledge_sim_ms <= min(s,v)`. For a future view use the latest cycle known at
   `s`, and render only within its forecast horizon.
3. Start the local clock paused at manifest replay start, speed 240, bounds from the
   manifest. Reuse the clock anchor math and epoch semantics in `contracts.ts`.
   Scrubbing the view does not silently change the shared simulation clock.
4. Pre-ignition bundles have `cycle: null`: show observations/events as eligible,
   clear forecast layers and show “No forecast yet.” Do not carry a later forecast back.
5. Stage each bundle and required map assets, then commit one consistent view.
   Panel/map values must refer to the same cycle. Use AbortController plus a request
   generation token; abort alone does not prevent a late decoded result from committing.
6. Dispose requests/subscriptions on route exit. Reset scenario/branch/epoch-sensitive
   caches on switches; prefetch only a bounded number of nearby eligible assets.
7. Offline alert approval is local only. Preserve approved alerts, use only permitted
   proposal/superseded transitions and honor pause-on-proposal. Do not trigger messages.

Acceptance: backward/forward seeks, pre-ignition and deliberately reordered responses
never reveal future evidence or mix cycles. A slow old response cannot replace a new seek.

## 5. Phase C — decoder and map layers

For dimensions nx, ny, let C = nx*ny. Require exactly `5*C` bytes. At quantile
index q, row r and column c, the offset is `q*C + r*nx + c`. Row zero is north.
Quantile order is [0.1, 0.3, 0.5, 0.7, 0.9].

For byte b and issue time I (epoch milliseconds):

- b=255: no arrival within the modeled horizon; never draw it as burned.
- b=0: reached by issue time.
- 1<=b<=254: encoded arrival time `A = I + 120000*b`.

At view time t, compute

`k(cell,t) = sum_q 1[b_q != 255 and I + 120000*b_q <= t]`.

k=0 has no supported arrival class; k=1..5 corresponds to the threshold lower bound
QUANTILES[k-1]. These are quantile-derived probability classes, not an exact continuous
probability such as 63%. Cells with all five bytes zero use the burned style. For a
single chosen quantile, render its reached mask separately; do not confuse the two views.

- Add a pure `forecastRaster.ts` decoder and renderer adapter. Compare its output
  against the existing Node decoder before connecting React.
- Use manifest outer corners, not cell-center bounds. MapLibre image corner order is
  [tl,tr,br,bl]; deck.gl BitmapLayer uses [bl,tl,tr,br]. For the current custom globe,
  map raster positions through its existing geographic projection and clip at the limb;
  do not paste the raster in screen coordinates. Honor the projected-grid geometry;
  use a georeferenced mesh/reprojection if a four-corner approximation exceeds one cell.
- Render the published burned/front perimeter and q50 isochrones. Never regenerate
  them from decorative ellipses. Validate a known northwest/southeast marker raster
  to detect mirrored axes or transposed dimensions.
- Connect one existing incident/dashboard view first. Keep the homepage’s decorative
  events separate until its renderer can consume the same validated forecast state.
  Experimental map prototypes need not all be integrated in the first deliverable.
- Rebuild textures on data/lead/class changes, not every animation frame. Keep the
  selected buffer cached and panel subscriptions at minute/cycle granularity.

Acceptance: all five quantiles and +30/+120/+360 min match the reference; all-255
cells remain unpainted, all-zero cells appear burned, and map placement is within one
cell of a reference georeferenced rendering.

## 6. Phase D — panels, provenance and assimilation review

Replace each mock value in the connected view with bundle data:

- Zone ranking/probability: `zone_risk`, selecting the published lead array correctly.
- Burned area, growth and heading: the selected Cycle; retain units from contracts.
- Observations/events: eligible bundle observations and activity feed.
- Alerts/briefings: published records; no invented recommendations or auto-send action.
- Status: forecast issue, view time/lead, data-through time, model version, member count,
  ESS and tempering. Keep “Synthetic replay / experimental model” visible.

When a value is absent, show unavailable rather than falling back to mock numbers.
Distinguish unavailable, pre-ignition, forecast computation pending and stale output.
Retain old output only with its original issue time and explicit stale status.

Use parent/branch replay for the report demonstration. Show observation timestamp,
availability, trust and provenance; compare branches at the same issue/lead. Changing
branches must not mutate the parent. Do not claim every report moves the q50 boundary
by 300 m: the current low-trust experiment fails that gate.

Acceptance: map, zone panel, observation feed and alert card use one cycle/branch;
synthetic inputs and unavailable results remain visible and correctly labeled.

## 7. Phase E — continuously updating runtime and hub

This follows a working FileSource integration; it is additional runtime work.

1. Cody assembles ForcingProvider plus the five-minute orchestrator using the existing
   HazardModel factory. Enforce `available_sim_ms <= issue` for every assimilated input.
   Weather valid in the future is allowed only if the forecast was available at issue.
2. Preserve authoritative issue state separately from forecast-extension state.
   Run at most one authoritative update per scenario/branch worker. Coalesce/queue
   new work with documented handling of delayed observations; expose backlog status.
3. Write immutable assets first; verify sizes/hashes, write bundle, then atomically
   publish the file index/head or use the existing hub publish reducer. Never point
   the dashboard at half-written assets. Maintain model/config/evidence provenance.
4. Add `web/src/data/spacetimeSource.ts` using existing generated bindings and pinned
   SDK 2.13.0. Adapt camelCase rows to snake_case, undefined to null and JSON columns
   to parsed values. Apply branch lineage rules from the contracts before rendering.
5. Subscribe to committed cycle heads and obtain assets through the same loader as
   FileSource. Reconnect with a full consistent snapshot; prevent duplicate event,
   alert and observation application. Preserve offline file mode as an explicit choice.
6. Add checkpoint/restart and branch command handling. Keep operator approvals in
   operator state; restoring a physics checkpoint must not revert an approval.

Acceptance: two browsers agree on the committed cycle and allowed alert transitions;
reconnect/restart produces no duplicate effects; interrupted publication exposes no
partial cycle. Live service acceptance must run against an actual published hub.

## 8. Required tests and final handoff

Add unit tests for byte offsets, 0/255 handling, quantile order, horizon bounds,
corner mapping, lead indexing, malformed manifest, missing/truncated binary, stale
requests, clock/branch selection, pre-ignition and alert transitions.

Add browser tests that use the generated Rust fixture (not hand-authored substitute
predictions): load all 39 entries, select all quantiles, scrub backward, choose +120
min, inspect the delayed report and an alert, change branches, simulate asset failure,
and navigate away during loading. Assert decoded masks and sidebar values in addition
to taking screenshots. Check the browser console for lifecycle errors.

Keep existing engine/research/Rust/Node checks, run web lint/test/build, and include
browser tests in CI. For hub mode also verify two-browser propagation, reconnection,
source ordering and service failures. The dashboard should show published results
promptly (proposed UI target: p95 <=1 s after receiving the committed head on the named
demo machine); benchmark UI fetch/decode/render separately from physics computation.

Handoff includes exact commands/environment selection, hosted asset root, model and
config identity, sample cycle IDs, test evidence and screenshots, plus the open
scientific and performance gates. Do not call it a validated real-time predictor.

## 9. Parallel science and performance work

These do not block clearly labeled replay integration but do block validated-use claims:

- Calibration: predeclared multi-seed twin tests; wind error <=8°, ROS error <=25%,
  ESS >=0.2N on >=80% cycles, improved +2 h outline skill over open loop. Separate
  likelihood effects from geometric reanchoring and test negative observations.
- Historical validation: frozen checksum inputs and availability times; independent
  perimeter truth excluded from assimilation/tuning; held-out incidents; compare
  none/reinit/PF at 30/60/120/360 min, including calibration and boundary/arrival errors.
- Performance: isolated target-size p50/p95 and process-tree RSS, then incremental
  native state, cached statics and bounded active regions with parity tests. Preserve
  causal replay after parameter changes; revise the target explicitly if necessary.
- Physics: independent pyretechnics parity, convergence/terrain/barrier tests and
  documented urban heuristic limits. Keep unsupported spotting disabled.

Detailed existing tasks: `docs/RUST_HAZARD_NEXT_TASKS.md` in the repository.
