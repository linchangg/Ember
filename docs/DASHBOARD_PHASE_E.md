# Phase E — runtime and live hub connection

Post-merge status (2026-10-25): merged through PR #11 at main `0308ba8`; all five
PR checks passed. The scope description below records what Phase E originally changed.
Current remaining gates are in `RELEASE_READINESS.md`.

Approved by the owner's “yes” after Phase D. Based on `6c1abe7`; implemented on
`feat/dashboard-phase-e`. This extends the restored original A–D plan. It does not
merge main, change frozen shared wire types, deploy a service, or establish scientific
skill. Fixture acceptance uses real Rust-generated forecasts with simulated inputs.

## What runs

`/dashboard` now explicitly supports `VITE_DATA_SOURCE=stdb` as well as `file`.
`SpacetimeSource` uses generated bindings and the existing pinned SDK 2.13.0. A single
subscription covers scenario, zones, clock, branches, cycle/head, risk, observation,
scan, alert, briefing, sensor, person, report, metric and activity tables. Components
still use the same DataSource hooks; the renderer never calls Rust or estimates risk.

`hubConnection.ts` normalizes camelCase, JSON columns, options and identities. It
waits for subscription `onApplied`, then stages transaction-cache snapshots. Queued
callbacks collapse into one full cache read; table primary keys prevent duplicate
application. A disconnected connection is closed and replaced with a new full
subscription, with 1/2/4/8/16/30-second bounded backoff. No reducer is automatically
retried by the browser: a lost response can have an unknown outcome, so inspect the
resubscribed operator state before reviewing again.

The [official TypeScript reference](https://spacetimedb.com/docs/clients/typescript/)
and [subscription guide](https://spacetimedb.com/docs/clients/subscriptions/), together
with the installed 2.13.0 types, informed this implementation. It does not depend on
newer SDK reconnect APIs.

## Snapshot rules

- Static manifest and zone geometry still come from the configured asset root. The
  hub manifest is informational. An incompatible cycle config/hash fails validation.
- A forecast must have a committed head referencing an existing cycle. Select the
  newest lineage-visible cycle at or before `min(shared simulated now, local view)`.
  Every ancestor cutoff is the minimum fork time on its path. At equal issue times,
  the descendant wins. Reject missing ancestors, cycles in lineage and broken heads.
- Use the same ArrivalGridCache, frozen bundle validator, safe asset URL resolver,
  binary decoder and georeferencing as file mode. Stage assets before committing
  matching map/panel values. Abort and sequence checks reject superseded downloads.
- No stale image is presented as a new head. Loading, disconnect and malformed/missing
  assets hide forecast numbers. Shared `computing`/`catching_up` and a forecast behind
  the clock are labelled while retaining the last committed issue time.
- Observations/scans/events are time- and lineage-filtered. An observation whose
  assimilation-cycle metadata belongs to a later issue is omitted in an earlier view.
  Future sensor status is omitted. Briefings must link to an eligible alert.
- Hub rows contain current operational alert state, without a simulated timestamp for
  every approval transition. Inherited parent alert state is therefore omitted in child
  review. File mode retains its frozen pre-fork alert snapshots. Hub past views are
  not a complete historical archive of operator transitions.
- Local view/lead changes require no PIN and do not pause the shared clock. Play,
  pause, issue seek, speed, reset, approval/decline and evacuation overrides invoke
  existing hub reducers with the operator PIN. Approval is not optimistic; both
  browsers wait for authoritative rows. PIN stays in page memory and clears on reload.
- Branch selection is engine-owned in hub mode. The existing runtime report/branch
  command path changes the shared branch, which every browser follows. The dropdown
  is disabled there. Parent comparison remains read-only at the exact issue/lead.

## Runtime changes and guarantees

The existing `Loop`, `NcForcing`, `ModelHost`, `Poller` and HazardModel factory remain
the runtime path. Forcing availability and every assimilated observation are asserted
at issue; five-minute authoritative steps remain separate from six-hour forecast
extension state. Native checkpoint/determinism/delayed-report tests exercise that path.
No new physics equations, calibration parameters or learning component were added.

1. Forecast assets now live at `cache/{scenario}/{branch}/{issue}/assets/{physics-key}/`.
   Each file commits by atomic replacement and refuses different bytes at an existing
   immutable path. A receipt records SHA-256 and byte count for all three assets. The
   writer verifies the receipt and `5*ny*nx` binary size before returning cycle metadata.
   An interrupted directory is unreachable until a complete cycle is published.
2. File publication writes a content-addressed immutable bundle version before the
   compatibility `bundle.json` pointer. The atomic index references the immutable
   version. An older index continues to read its exact original bundle bytes.
3. `StdbSink` surfaces failed cycle publication. `Loop` retains that exact pending
   batch, holds its publication index, and retries after bounded one-second backpressure
   rather than recomputing or skipping cycles. A seek discards pending old work and
   rebuilds the requested lineage. Hub reducers preserve approved state when the same
   batch is submitted again. Process restart rebuilds from cached physics/checkpoints
   and the hub's operator state, rather than restoring approvals from physics.
4. `/healthz` adds internal diagnostics `backlog_cycles` and `publication_pending`.
   Existing health fields remain. They expose lag; no faster-than-real-time claim follows.

Run one authoritative runtime process for each scenario database. The in-process
worker lock is not a distributed lease. The current frozen module has global primary
keys such as branch ID `main` and several alert IDs; **use a separate database per
scenario**. Loading another scenario in that database repoints its `main` row. A
multi-scenario module needs a separately approved contract migration.

With parallel control/treatment publication, a shared observation ID can retain only
one branch's current assimilation metadata in the frozen hub table. Do not claim the
hub feed contains separate assimilation histories for the same observation on two
branches. Cycle model outputs and file-mode evidence retain their own provenance.

## Reproduce a local published-hub demo

Prerequisites: Python 3.12, Node 22+, Rust toolchain 1.99.1, SpacetimeDB CLI/standalone
2.13.0. The exact official Linux binary download/setup is in
`.github/workflows/dashboard-hub.yml`; macOS uses the matching official
`spacetime-aarch64-apple-darwin.tar.gz` release asset. Keep credentials outside Git.

From repository root:

```sh
uv sync --project engine --locked
npm --prefix web ci --ignore-scripts
npm --prefix stdb ci --ignore-scripts
cargo build --manifest-path physics/surface_v1/Cargo.toml -p fire-bridge --release --locked
uv run --project engine python -m emberline.hazard.demo \
  --out /tmp/phase-e-fixture --members 16 --report-branch
```

Start a fresh local hub with `spacetime start --listen-addr 127.0.0.1:3017` (or use
standalone with an isolated data directory and JWT key pair, exactly as the workflow).
Publish the existing module to a new disposable database:

```sh
spacetime publish phase-e-demo --server http://127.0.0.1:3017 --module-path stdb --anonymous --yes
PYTHONPATH=engine uv run --project engine python tools/seed_dashboard_hub.py \
  /tmp/phase-e-fixture rust-fixture /tmp/phase-e-credentials.json
uv run --project engine python -m emberline.hazard.fixture_server \
  --root /tmp/phase-e-fixture --port 8796 --allow-origin http://127.0.0.1:5190
```

Seeding only permits a localhost hub. It creates disposable engine/service identities
and a random operator PIN, writes them with mode 0600, publishes real fixture bundles,
and deliberately submits one event twice to check idempotence. It starts no notifier.
`--reset` resets only the named scenario in this disposable database; do not use that
flag with a shared database. Use the credential file to obtain the local operator PIN,
never a Vite variable containing an engine/service token.

In another terminal:

```sh
VITE_DATA_SOURCE=stdb VITE_STDB_URI=http://127.0.0.1:3017 VITE_STDB_DB=phase-e-demo \
VITE_SCENARIO_ID=rust-fixture VITE_ASSET_BASE=http://127.0.0.1:8796/ \
npm --prefix web run dev -- --host 127.0.0.1 --port 5190
```

Open `/dashboard` in two browsers. Select +120 minutes. They must agree on cycle and
published risk. Enter the test PIN in one page, review one proposal, and verify its
approved state in the other page and after reload. Hub approval can be consumed by a
notification worker in a separately configured runtime; this test runs no such worker.
The immutable fixture path is a hub-connected replay, not a newly arriving weather feed.

For actual runtime production, configure the existing engine's `STDB_URI`, `STDB_DB`,
`STDB_TOKEN`, `OPERATOR_PIN` and asset host. Once the scenario's real statics, forcing
and replay manifest exist, use `uv run --project engine python -m emberline.run
--scenario SCENARIO --profile online --members-profile dev`. The target profile uses
128 members; dev uses 16 and is labelled. There is no fabricated fallback landscape
or forcing when those files are absent. The runtime publishes new heads as eligible
cycles complete; the hub source needs no polling of file indexes.

## Verification and limits

Local evidence is in `docs/evidence/dashboard-phase-e.json`. The tests include:

- Adapter unit cases: row conversion, lineage, committed-head validation, causal cutoff,
  stale asset cancellation, disconnect, disposal and non-optimistic operator actions.
- Existing engine suite, including native Rust orchestration, checkpoint byte parity,
  forcing selection and delayed observation availability; new publication retry,
  immutable bundle and asset integrity/interruption cases.
- Existing published hub reducer/runtime acceptance in a fresh dedicated database.
- Two actual SDK clients: same cycle, shared approval, duplicate review rejection,
  full reconnect, actual server restart, no duplicate event, engine-owned branch
  switch and exact-issue parent comparison.
- Separate two-browser Playwright acceptance against the published module and Rust
  fixture. The workflow starts a real local hub; no fake WebSocket responses or
  invented forecast rows are used. Hub tracing is off so PIN input is not archived.
  File-mode browser acceptance remains in the existing workflows.

Final code `b5a01fb` passed [dashboard/hub checks](https://github.com/linchangg/Legendary-Fire-Project/actions/runs/40174753913),
[native integration on Linux and macOS](https://github.com/linchangg/Legendary-Fire-Project/actions/runs/40174753907),
and [actual published-hub restart/two-browser acceptance](https://github.com/linchangg/Legendary-Fire-Project/actions/runs/40174753904).
Local results: 287 engine tests passed, 19 configured skips; 15 dedicated published-hub
checks passed; 49 native-fixture dashboard tests passed; canonical 48 passed/1 expected
skip. Types, lint and production build passed (existing large map-chunk warning). UI p95 <=1 second on a named demo machine, historical data
skill and target-size physics performance are still separate, unestablished gates.

## Follow-up tasks

1. **Real-data rehearsal:** Cody supplies a frozen scenario package with forcing issue
   and availability times, statics and checksums. Run the same runtime in a dedicated
   scenario database; record input hashes, model/config identity and every committed
   head. Test delayed forcing, missing files and a native timeout without inventing data.
2. **Dashboard latency:** on the actual demo laptop, capture 50 head receipt-to-render
   samples while two browsers view +120 minutes. Report p50/p95, CPU/RSS and fetch,
   decode and rendering separately. Do not include physics time in the UI measurement.
3. **Report/notification services:** implement the absent `web/api/report-tap`/voice
   service using the existing service-only reducer and timestamp/trust validation.
   Test report → command → child forecast with a runtime worker before adding a UI
   submission control. Rehearse outbound calls only with an explicitly selected test
   recipient and configured notifier; approval state alone is not delivery evidence.
4. **Stronger persistence/concurrency:** add a durable publication outbox if exact
   restart ordering across external side effects is required, plus an explicit lease
   for multiple engine processes. Exercise crash points before/after reducer commit.
   Approve a schema migration before introducing multi-scenario/global-ID changes or
   per-branch assimilation/decision history.
5. **Science:** continue held-out historical evaluation and predeclared calibration
   gates from `RUST_HAZARD_NEXT_TASKS.md`. Compare none/reinit/PF on the same availability
   stream and independent truth. Software integration is not evidence of forecast skill.
