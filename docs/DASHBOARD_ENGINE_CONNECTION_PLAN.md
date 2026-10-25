# Dashboard–engine connection plan — Linda-based revision

Revision 2026-10-24. Baseline: Linda/main `2a62206` (scenario-backed globe, replay
clock, basemap, 50-member analytic fixture) plus the verified Rust backend and prior
science frameworks. Latest fetch found that same commit on main and Linda's Cursor
branch; it was already included in Phase C. This revision changes the architecture
of A–C to extend her implementation instead of maintaining a parallel application.

## 1. Authority and release boundaries

| Area | Authority / current status |
|---|---|
| UI layout, globe, interaction, terrain, basemap | Linda's `web/src/pages/GlobeNetwork.jsx`; preserve visual direction |
| Clock | Linda's `web/src/lib/replayClock.js`, frozen `simNow`; no second application clock |
| Scenario files | `lib/scenarioData.js` facade → validated `data/scenario.ts` |
| Wire types | Frozen `contracts.ts` / Python contracts; unchanged by this revision |
| Native model | Operational Python HazardModel → Rust bridge; existing assimilation/checkpoints |
| Fixture | Canonical analytic N=50 and separate generated native N=16; both synthetic |
| Hub | Existing generated bindings + Linda's clock connection; live forecast transport incomplete |
| Calibration / historical skill / latency | Not established; no validated-use claim |

The old `data/fileSource.ts`, store/hooks and tests remain compatibility/reference
code. No route mounts that provider. Removed Phase C's parallel FileDashboard,
MapLibre component, CSS and worker. `/` is the connected incident globe;
`/dashboard` remains the explicitly separate original mock. No model equations,
likelihood defaults, notifications or frozen schemas change as part of this work.

## 2. Implemented A–C, revised around Linda

| Phase | Implementation | Remaining acceptance |
|---|---|---|
| A | Preserve fixture generation, explicit loopback hosting, independent decoder; target Linda's `/` route | Refresh native artifact whenever model/config/evidence changes; never treat analytic fixture as native evidence |
| B | Linda scenario API + one Linda clock; shared validators, atomic staging, generation/abort, bounded cache, visible failures, disposal | Full hub clock adoption/reconnect/lineage acceptance belongs to E |
| C | Pure quantile decoder + exact projected cells on Linda's globe, published vectors, existing time strip plus modes/leads | Browser screenshots/console/rotation/limb checks and target-size frame timings |

Detailed commands and behavior: `DASHBOARD_PHASE_A.md`, `DASHBOARD_PHASE_B.md`,
`DASHBOARD_PHASE_C.md`. Do not use the earlier flat-map route instructions.

Active file flow:

```text
existing runtime / Rust HazardModel
  -> schema2 bundles + binary/GeoJSON assets
  -> Linda scenarioData facade / ScenarioFiles validation + selection
  -> Linda replayClock supplies now, branch, epoch; scrub supplies view
  -> one selected entry -> globe cells / published vectors / existing labels
```

Select issue `I=max{k | k<=min(now,view)}`. If no cycle, show not issued. Beyond
`I+60000*horizon_min`, show unavailable. Cache key includes scenario/context, issue
and validated model/config/assets identity. An epoch change invalidates files.
One stage either commits bundle+all required assets or exposes no entry. Never
reuse the former `lastEntry` under the new clock/view label. Missing values do not
become zero probability, a clear zone or a Unix-epoch act-by date.

## 3. Phase D — published decision data and assimilation review

**Planning only. Start D only after a new direct confirmation.** Confirmation for
this architectural revision authorizes A–C adjustments and D/E planning, not their
implementation. D remains file-backed; it does not add hub delivery or real alerts.
Before D, finish C's visual checklist or explicitly record the unresolved gate.

### D1. Define a shared selected incident snapshot

Owner: Linda, with Abby checking decoder semantics and Cody checking wire mappings.
Extend `data/scenario.ts` and a small React subscription adapter; do not introduce a
second clock or another app. Snapshot must include selected branch/epoch/issue/view,
cycle, map assets, zone risk, observation/scan/event history, alerts/briefings and status.
Use subscribe/getSnapshot or the existing 100 ms update loop initially. Keep one
selection token for map and panels. Components do not fetch independent bundles.

History cannot be inferred from the last bundle: publisher bundles can contain only
new observations/events. Read eligible earlier bundles bounded by the selected issue;
deduplicate immutable observations by obs_id and events by event_id. Preserve alert
state updates with allowed transitions and never downgrade an approved record. Keep
light metadata separately from the four raster entries; cap history/prefetch using
configured time windows. Invalidate history on epoch/scenario/evidence revision.

Acceptance: compare every displayed row with its fixture bundle; no future available
observation, no duplicate event, no mismatched cycle_id. Reordered and failed history
requests cannot alter a newer selection. Explicit loading state until required rows
are consistent; absent optional fields render unavailable.

### D2. Populate Linda's existing incident panel

Keep `gn__side` layout/style; replace/extend its incident details with published data.
Do not replace NASA event-magnitude “Risk level” with an unlabelled forecast probability;
separate external event metadata from forecast/zone results.

| UI item | Wire source / rule |
|---|---|
| Zone name/geometry | manifest zone FeatureCollection, stable zone_id |
| Rank/status/risk | selected cycle's `zone_risk`, sort numeric rank; preserve published status |
| Chance by +30…+360 | `p_by_lead[lead/30-1]`, leads [30,60,…,360]; +120 is index3 |
| ETA | published eta_q10/q50/q90_sim_ms; null means unavailable |
| Act by | published act_by_sim_ms and act_by_basis; never recompute from an editable form without engine output |
| Fire stats | cycle.burned_ha/front_km/growth_ha_per_h/head_bearing_deg with units/null handling |
| Model diagnostics | cycle model_version, config_hash, n_members, ess, tempering_alpha, filter_mode and data_through_sim_ms |
| Recommendation | published alert/briefing matched by IDs; no client LLM/numeric estimates |

Controls viewing +0 (“now”) must not index an absent lead0 probability. Show the
published lead labels separately from current view lead if between discrete steps.
Any evacuation-time override is displayed as an operator input awaiting recalculation;
never make published risk/act-by appear to incorporate an unsent override.

Acceptance: golden fixtures exercise null ETA, 0% chance, no alert, multiple alerts,
all leads, pre-ignition, outdated output and errors. A zero and a missing value remain
visibly different. Map and panel issue/branch identities agree after rapid scrubbing.

### D3. Provenance and observation/assimilation review

Add an expandable section to Linda's panel/time strip with observed and available
sim times, sensor/platform, kind, hot/clear flag, trust, provenance, source_ref,
assimilation status/influence when published, ESS and tempering. Explain observed vs
available delay. Badges distinguish real/simulated/injected/derived. Keep experimental
and synthetic labels persistent; mark urban spread heuristic when applicable.

Read-only branch comparison requires explicit branch descriptors and indexes. Port
eligible ancestor/fork-time filtering from tested legacy FileSource utilities into
ScenarioFiles, rather than mounting its clock. Selection key must include branch,
epoch, issue, model and config. Compare parent/child at **identical issue and view lead**;
if one side is absent, show unavailable, not a nearby cycle. Preserve immutable parent.
Do not promise a report shifts a boundary by 300 m; current low-trust evidence fails that gate.

Acceptance: delayed observations excluded before available_sim_ms; parent after fork
cannot leak into child; identical inputs reproduce output; negative/all-clear reports
and rejected reports retain explanations; branch switch during loading cannot mix masks.
Synthetic parent/child fixtures must be generated with the actual engine, not drawn by UI.

### D4. Alert review, tests and approval stop

File mode may provide local review state only, visibly “local demo; no delivery.”
Approval/decline affects operator state separately from cached physics and survives
seeks. No call/SMS/email or report submission endpoint is activated in D. Proposed,
approved, declined and superseded transitions must match frozen reducers; rejected
transitions show an error. If keeping controls read-only is preferable for Linda's
screen, ship read-only alert cards and defer mutations explicitly.

Required checks: existing web/decoder/native checks; new snapshot/history/panel tests;
real browser tests against Rust-generated data; screenshots and console review. Include
all quantiles, three representative leads, late loading, missing asset/retry, route exit,
backward seek and absent data. No arbitrary pixel snapshot alone proves scientific parity.
Record branch/commit/fixture hashes and browser evidence. **Stop after D and request
explicit confirmation before E.** Do not combine D acceptance with live delivery claims.

## 4. Phase E — continuous engine/hub delivery and operator commands

**Planning only; requires a separate direct confirmation after D.** Reuse Linda's
existing clock connection, generated SDK bindings and Cody's runtime/API; do not build
a parallel Rust server, invented WebSocket protocol or second hub client by default.
Pin the existing spacetimedb SDK 2.13.0. Verify current official SDK docs and generated
bindings at implementation time; the plan does not assume future API signatures.

### E1. Inventory and establish one connection owner

Inspect `replayClock.js`, generated `module_bindings`, `stdb/src`, runtime model_host,
loop/publisher/cache, `sync/stdb_sink.py`, `sync/cache_sink.py`, `api/local.py` and run.py.
Document actual deployment URI/database identity and scenario. Confirm engine health
using GET `/healthz`; its `hub_ok`, last_cycle_sim_ms, physics_ms and cache_coverage are
status, not guarantees that assets match the dashboard head.

Extract the existing connection into a single session owner shared by clock and
scenario transport. Do not create a second clock subscription that races adoption.
Implement connection state (connecting/ready/disconnected/recovering/error), teardown,
row validation and scenario checks. On disconnect, freeze the authoritative state and
show stale original issue/data-through; never silently switch to an advancing local
clock while implying live data. Explicit offline replay is a separate operator choice.

Acceptance: one connection/subscription set per mounted scenario, StrictMode-safe;
disposal before connection completes creates no orphan; reconnect does not duplicate
callbacks. Validate clock anchor/adoption on two clients, including epoch reset and
engine computing/catching_up statuses.

### E2. Consume committed cycle heads atomically

Subscribe to generated tables for selected scenario/lineage: clock, branch, cycle,
cycle_head, zone_risk, observation, scan_event, alert, briefing, report, sensor_info,
activity_event and metrics as named in current generated bindings. Verify names;
do not guess snake/camel case. SQL has no ORDER BY: sort deterministically client-side.
Adapt camelCase to frozen snake_case, options to null and *_json to parsed objects,
then run the same scenario/bundle/geometry/causality validators.

A cycle head triggers staging, not immediate relabeling. Resolve published asset URLs
through the same confined loader; commit cycle+all selected panel rows+assets as one
snapshot after verifying identities. Use generation/AbortController keyed by scenario,
branch, epoch, issue, model/config and asset identity. Ignore old heads and deduplicate
immutable rows; on reset/epoch revision invalidate same-URL bytes. Reconnect from a
complete subscribed snapshot before returning to ready. Never rely on the file index
being immutable or a hub clock alone to discover new forecasts.

Acceptance: deliberately delay binary, vectors and risk rows; no mixed-cycle screen.
Missing/corrupt assets expose explicit pending/error status. Duplicate/out-of-order
heads and events have no duplicate effects. Disconnect/reconnect preserves operator
approvals. Changing active branch clears wrong-lineage output before the new stage.

### E3. Verify Cody's publication/restart path rather than replacing it

Existing runtime/cache writes use temporary-file replacement; existing sinks/publisher
and ModelHost already implement substantial orchestration. Audit their actual commit
boundary with tests: assets complete and checksummed/sized before bundle/index/head
publication; failed writes must not expose a usable head. A bundle is either fully
committed or absent. If current same-URL revisions remain possible, keep revalidation
and epoch/evidence identity; do not assert immutable content-addressed URLs exist.

Enforce observation available_sim_ms<=issue, including rejected/future reports. Future
weather validity is allowed only from forecasts issued/available by the cycle cutoff.
Verify one authoritative model update per scenario/branch, bounded backlog/coalescing,
checkpoint identity and restart parity. Forecast extension must not advance the saved
assimilation issue state. Existing Rust HazardModel stays behind its Python factory;
no browser process invokes Rust. Diagnose late engine output separately from UI delays.

Acceptance: crash after each asset/bundle/head step, resume deterministically; no partial
head; unchanged cached evidence matches; changed forcing/config/observations invalidates
cache; physics restart does not restore an old operator approval decision.

### E4. Commands and human review

Use generated approved reducer signatures for play/pause/seek/speed and allowed alert
transitions. Obtain operator PIN through the existing flow; never put credentials in
asset URLs, docs, screenshots or logs. Preserve explicit operator identity where required.
UI errors must handle reducer rejection without pretending local success.

For reports, inspect existing POST `/dev/report`: current code sets branch_id="main".
It cannot be used for a child-branch demo without coordinated Cody work. Define branch
routing using existing authoritative state/contracts, or keep child report injection
disabled with an explanation. Do not silently add a wire field. Show submission ID,
accept/reject/pending assimilation and resulting cycle separately; acceptance of a
report is not proof that it changed the forecast. Existing `/dev/seek-preset` and
`/dev/duty-officer` are not replacements for clock/report semantics. Never auto-enable
outbound calls; notification testing needs separately authorized recipients and scope.

Acceptance: future/malformed report rejection, duplicate submit protection, main/child
routing, interrupted request, command rejection and no delivery on mere proposal.
Any frozen contract change goes through Cody, schema versioning and both language types.

### E5. Hosting, measurements, release gate

Use Cody's existing :8787 asset/API server or a same-origin proxy; Vite's development
scenario middleware does not exist in production. Its fixture cache fallback always
uses main, so it is not an acceptable child-branch asset server. Configure explicit
allowed UI origins; note current local API CORS allows http://localhost, while the
fixture host can explicitly allow http://127.0.0.1. Resolve that deployment detail
with Cody; do not bypass origin checks or assume both hostnames are interchangeable.
Keep hub/asset URLs configurable and credentials out of static artifacts.

Measure head-received → fetch → decode → projected paint separately from physics.
For each run record hardware/browser, grid, members, cache state, network and fixture
identity; report p50/p95 and peak browser/process RSS. Proposed UI target p95<=1 s
after committed head is received must be approved as a target and measured; it is not
established. The native <=0.5 s/cycle N=128/340×340/six-hour target is also unmet.
Canvas projection still touches many cells per frame: profile visible-cell bounds,
precomputed unit vectors/terrain samples and a worker for mask decode before replacing
Linda's renderer. Retain exact mask/coordinate parity for every optimization.

Release checks: two browsers same scenario/branch/head; new cycle automatically appears;
restart and reconnect; missing asset; branch/reset; report roundtrip; allowed alert
transitions; production URL/CORS; browser console, screenshots and timing evidence.
Keep clearly labeled file replay as fallback. No live or scientific-ready claim until
its corresponding gate is passed. Stop at the agreed E scope; do not send real alerts
or deploy services merely because local integration checks pass.

## 5. Independent science gates remain open

Calibration: predeclared multi-seed twins, wind error<=8°, ROS error<=25%, ESS>=.2N
on>=80% cycles, improved +2h skill over open-loop; isolate likelihood from reanchoring.
Historical validation: frozen checksummed inputs/availability, independent truth withheld
from tuning/assimilation, held-out incidents, none/reinit/PF/persistence at 30/60/120/360
minutes with calibration/boundary/arrival errors. See SCIENTIFIC_CALIBRATION.md and
HISTORICAL_VALIDATION.md. Performance: isolated target workload p50/p95/RSS and parity
before accepting optimization. Spotting stays disabled; no deep-learning component
is implied by this dashboard integration.

## Revision evidence

Local checks: 44/44 tests on each analytic and generated native fixture; strict
typecheck, warning-free lint and production file build passed. Existing large
map-library chunk warnings remain. The active Linda API HTTP checker accepted all
39 entries and all quantiles at +30/+120/+360. Independent wire decoder also passed.
Evidence: `docs/evidence/linda-dashboard-integration.json`; native artifact generation
evidence remains in Phase A. This revision did not rerun native propagation. Browser
control was unavailable, so visual acceptance, screenshots, browser CI and target-size
frame timings remain open. No D/E code or main merge is included.
