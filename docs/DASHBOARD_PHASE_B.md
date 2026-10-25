# Dashboard connection — Phase B

Owner confirmation: “yes” to implementing FileSource, shared dashboard state,
clock selection and validated asset loading. Stop before Phase C map rendering.

## Implemented

- `web/src/data/source.ts`: internal DataSource, immutable snapshot conventions,
  manifest and selected-map-asset types; frozen wire types remain imported from
  `web/src/contracts.ts` without modification.
- `assetUrl.ts`: confines asset URLs to an explicit HTTP(S) directory, rejects
  traversal/encoded escapes, omits credentials, rejects redirects, and bounds streamed
  JSON/binary response sizes. Missing assets are errors, never mock substitutes.
- `validation.ts`: validates schema version, required record fields, identities,
  integer times, input availability, grid/corner conventions, ensemble diagnostics,
  configuration identity, observation geometry and GeoJSON before publication.
- `arrivalGrid.ts`: validates `(5,ny,nx)` byte length, ordered quantiles, horizon/255
  encodings and perimeter IDs; stages binary/perimeter/isochrones together. Maximum
  four cached grids, keyed by scenario/branch/cycle/model/config/assets/grid identity.
  This validates the wire encoding; probability-mask rendering remains Phase C.
- `fileSource.ts`: loads manifest/index/bundles, publishes consistent snapshots and
  uses AbortController plus request generations to reject obsolete work. Errors clear
  forecast/risk selection. Disposal cancels requests and timers. Seeks re-read the
  index but keep bundles (keyed by index entry) and grids; branch changes invalidate
  both. Bundles load 8 at a time and the active branch is prefetched in the background.
  While an earlier time loads, a later forecast is never left on screen.
- `store.ts`: explicit environment-based selection and source lifecycle leases,
  including React StrictMode cleanup/reacquire and disposal when a source changes.
- `hooks.ts`: prepared provider and hooks for manifest, clock, view, selected cycle,
  arrival bytes, zones, risks, alerts, observations, scans, sensors, people, report
  acceptance summaries, events, metrics, branches, cycle head and local actions.
- Strict TypeScript checks added for the data boundary; TypeScript 5.6.3 matches the
  existing hub toolchain. There is no new production dependency.

The app entry point, current renderers, layout and shared contracts are unchanged.
The data modules are tested consumers ready for Phase C; setting environment variables
alone does not yet replace existing mock/simulated UI values.

## Time, state and offline semantics

The replay clock starts paused at manifest replay start with speed 240 and declared
bounds. The view can scrub independently. Past views select the newest eligible
cycle at/before view time; future views use the latest cycle known at simulation now.
Future inputs are rejected, not merely hidden. Beyond-horizon views expose an explicit
status and no raster. Pre-ignition selections contain no forecast or zone-risk result.

During loading the last committed snapshot remains intact, with a separate pending
target; the next snapshot commits the cycle, raster, vectors and panel rows together.
Consumers must display loading/error state using `useDataStatus`, not relabel old
output with the pending issue time. On error selected forecasts and risk rows clear.

Branch descriptors are supplied explicitly to FileSource options. Ancestor cycles
are limited by fork times; observation/scan/alert/metric/event rows use the frozen
lineage time columns. Each branch needs its own index and bundle assets. Unknown
branches or missing indexes fail rather than silently returning main. Metadata after
an ancestor fork may contain an eligible earlier row, so filtering is by row time,
not just the bundle issue. Branchless sensor/person records remain shared snapshots.
Report acceptance summaries and briefings lack full lineage timestamps on the wire;
these are conservatively inherited only through the eligible ancestor bundles.
Full report documents require the later hub/API integration.

Alert decisions are local: proposals can be approved/declined and approved decisions
survive seeking. New proposals pause playback when enabled. No messages or calls are
sent. Reset deliberately clears local decisions. Evacuation-duration changes are
exposed as operator overrides; engine-produced risk and act-by values are not fabricated
or silently recalculated. Tap-report assimilation rejects with an explicit message
until the engine command API is connected in a later phase.

The source is one scenario per instance. Replace/dispose it to change scenarios.
Use the hooks for selected data; `useArrivalGrid(cycle)` returns bytes only for the
atomically committed selected cycle. It does not fetch a second competing selection.
No forecast interpolation or decorative spread is computed by this layer.

## Current main integration

This branch combines Phase A `cce64dc` and main `2181e1b`. Main now includes Cody’s
runtime/model host/publisher/local API and factory-backed Rust integration. Statements
in the earlier Phase A plan about those modules being absent describe its older
baseline. Phase B verifies bundle consumption, not production historical replay,
notification services, or scientific/performance acceptance.

## Reproduce

Node 22.18+ (native TypeScript stripping) or current Node 24; from `web/`:

```sh
npm ci --ignore-scripts
npm run typecheck
npm run lint -- --deny-warnings
npm test
npm run build
```

The default tests consume the current canonical fixture. Test the independently
generated Rust-backed fixture from the repository root:

```sh
EMBER_FIXTURE_DIR=/private/tmp/ember-dashboard-phase-a \
EMBER_SCENARIO_ID=rust-fixture npm --prefix web test
```

With the Phase A host running, verify the real FileSource over HTTP:

```sh
node tools/check_dashboard_source.mts http://127.0.0.1:8790/ rust-fixture
```

`tools/check_hazard_frontend.mjs` remains an independent wire decoder. The new source
checker exercises actual FileSource state, atomic selections, available evidence,
future lead selection, backward seek and disposal without mounting a map.

Tests cover all 39 entries, all transport validations, delayed/out-of-order requests,
corrupt and missing assets, future input rejection, clock controls, pre-ignition,
local decisions, branch isolation, epoch invalidation, source replacement, failure
retry behavior and React hook consumption. CI runs strict data type checking and the
web suite; Linux/macOS native fixture jobs now also run the FileSource HTTP checker.

## Next confirmation required

Phase B ends here. Phase C would add the probability decoder/rendering adapter and
connect one existing map to the published raster and vector layers. It would also
verify georeferencing, north-first orientation, quantile/lead controls and unavailable
states in the actual browser. That work requires new direct owner confirmation.
Panels and live hub/engine command wiring remain their later phases.
