# Phase B — Linda's scenario loader and single replay clock (revised)

Linda's main implementation `2a62206` replaces the earlier proposed application
store/hooks/clock architecture. The integration branch now builds on her files and
preserves her globe, controls, opening time, terrain, basemap and visual direction.

## Active application path

```text
GlobeNetwork.jsx
  -> scenarioData.js -> data/scenario.ts -> shared validation / ArrivalGridCache
  -> replayClock.js -> local anchor math or Linda's existing hub clock connection
  -> select(now, view, active_branch_id, epoch) -> one bundle + complete map assets
```

`scenarioData.js` supplies Vite asset configuration and page URL. `openScenario` is
independently testable with injected base/fetcher/signal. It exposes Linda's manifest,
statics, index, cell coordinates and published data, without creating another clock.
`FileSource`, old store/hooks and their tests remain compatibility/reference modules;
no active route mounts them. They are not the new application's state authority.

## Selection contract

Call `select(nowMs, viewMs, branchId, epoch)` from Linda's existing UI/animation loop.
It returns a nonblocking snapshot with status, error, issue and selected entry:

- Issue = newest index time <= `min(nowMs, viewMs)`; future view cannot select future knowledge.
- Entry contains validated bundle, raster, burned/front vectors, isochrones and zone-risk rows.
- Required assets are staged atomically; loading/error/not-issued/beyond-horizon expose no entry.
- Three `cycle:null` pre-ignition bundles are valid, not load failures.
- One request generation and AbortController prevent an obsolete response from replacing a seek.
- Epoch/context changes invalidate file caches. At most four complete entries are retained;
  the binary staging cache is cleared after staging, avoiding a second retained raster set.
- A failed selection stays failed until explicit Retry or a different target. It cannot retry at 60 fps.
- `dispose()` aborts staging, clears caches and prevents late publication. Startup accepts AbortSignal.
- Only `main` file lineage is currently configured. A different hub branch returns an explicit
  unavailable error; it never substitutes main. Branch inheritance is future D/E work.

The loader reuses schema2/identity/causality/URL/size/geometry/quantile validators from
Phase B. Manifest/index/statics are mandatory startup assets. Missing required files
are errors. Binary length is exactly `5*nx*ny`; 255 means no modeled arrival.

## Clock and lifecycle

`replayClock.js` remains the sole clock, using frozen `simNow` anchor math. Linda's
paused opening at ignition+90 minutes is retained; Restart seeks to replay start.
Scrubbing changes only view time. Local play/pause/seek/speed use the existing controls;
seek increments epoch and clears cached output. Invalid seek/speed inputs fail explicitly.
Unmount disposes both scenario and clock, including a pending/connected hub connection.

An existing hub clock does **not** provide live forecast updates. The UI labels that
combination “hub clock · file forecasts.” Explicit `VITE_DATA_SOURCE=stdb` remains
unsupported until Phase E and fails rather than pretending to be connected.

## Tests and limitations

Run `npm --prefix web test`, `npm --prefix web run typecheck`, and
`npm --prefix web run lint -- --deny-warnings`. Repeat tests with
`EMBERLINE_FIXTURE_DIR=/private/tmp/emberline-dashboard-phase-a` and
`EMBERLINE_SCENARIO_ID=rust-fixture`. The HTTP checker now calls Linda's scenario API,
not the old standalone FileSource. Native CI runs it after installing web dependencies.

New tests cover all 39 entries, atomic issue identity, no future observations, horizon,
pre-ignition, mask memoization, missing/truncated assets, stable errors/retry, ignored-abort
late responses, epoch cache invalidation, startup cancellation, disposal, exact grid geometry
and Linda's clock anchors/opening. Existing FileSource tests remain regression/reference
coverage; their alert actions and branch lineage are not connected UI capabilities.

Current file mode reads a fixed index once; no live index refresh, new branch lineage,
notifications, report submission or full observation history is implied. D/E address these
separately after direct confirmation.
