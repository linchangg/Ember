# Main integration review — 2026-10-24

## Decision and branch inventory

The owner requested review and combination of updated main, Cursor work and the
Rust-backed HazardModel. Integrate as an experimental component; passing software
checks does not establish historical forecast skill or operational safety.

- Main reviewed: `9e00a67`, including the Cursor command dashboard and Eaton globe.
- `cursor/emberline-command-dashboard-3b97` is already merged into main; its remote
  branch was deleted. Preserve its UI and routes.
- `cursor/surface-v1-physics-engine` remains `99b7f45`, an unrelated root history.
  Its relocated, corrected engine already landed through PR #7. Do not overlay the
  old root workspace again.
- Merge `research/surface-v1-adapter` (`8e4055b`) additively. Its CLI research adapter
  and the operational HazardModel adapter have different responsibilities and tests.
- Merge the existing `feat/rust-hazard-backend` work through PR #8 after checks pass.
  SPEC D3 now records owner authorization for Rust behind the frozen Python API.
  Shared wire types, canonical fixtures and the UI layout remain compatible.

## Section-by-section findings

| Section | Present and checked | Remaining work / limits |
|---|---|---|
| `engine/emberline/hazard/` | Python API, persistent Rust bridge, weighted arrivals, PF likelihoods/tempering/resampling, observation availability checks, checkpoint identity and restart parity, complete synthetic replay/export | Calibration and latency gates fail; no historical skill claim; spotting explicitly disabled; no deep-learning model |
| `physics/surface_v1/crates/` | Rothermel surface behavior, level-set solver, ensemble and bridge/CLI; release tests pass | NB91 urban behavior is heuristic; pyretechnics parity and independent historical evaluation remain gates |
| `physics/surface_v1/adapter/` | Independent research CLI adapter, unit/origin/time conversions, validation, smoke benchmark, 38 tests | Unweighted research interface, not the HazardModel factory or live assimilation path; retain separately |
| `engine/emberline/data/` | Grid/config, HRRR and GOES helpers, Area X code and fixture data | Production ForcingProvider, complete static-data/scenario assembly and end-to-end real-source replay still need completion |
| `engine/emberline/runtime/` | Replay/synthetic sources, cache, clock and summarization | Missing top-level replay/online orchestrator, availability-audited forcing assembly and atomic publishing loop |
| `engine/emberline/risk/` | Risk and alert functions exercised by the fixture | Preserve alert state across branch/restart; prove complete orchestration and sensor failure behavior |
| `engine/emberline/sync/`, `stdb/` | HTTP hub helpers and strict TypeScript module; SDK remains pinned to 2.13.0 | No local SpacetimeDB executable was available for live publish/subscription acceptance; type checking is not service verification |
| `engine/emberline/ai/` | Briefing/validation and prompt code | Live API-dependent checks skipped; report parsing, voice/notification and full API endpoints remain follow-ups per spec |
| `web/` | Globe, dashboard and three comparison routes; local browser smoke; lint/build and new regression tests | Globe uses historical EONET plus explicitly simulated spread. Dashboard is a mock. Neither is wired to Rust forecast bundles or hub subscriptions yet |
| `fixtures/`, `data/scenarios/`, `tools/` | Canonical fixture preserved; synthetic Rust demo exports 39 entries; Node validates raster decoding | Scenario metadata is not evidence of a complete historical replay. Keep synthetic and real provenance visible |
| `.github/workflows/`, `docs/`, `spec/` | Existing cross-platform physics/backend checks retained; web/hub checks added; historical research docs labeled | Frozen API probe remains required; update compatibility fixtures deliberately when Cody/Linda change interfaces |

## Defects resolved in this integration

1. NASA empty/failed event feeds could call `newHeli([])` and dereference a missing
   event. Extract a guarded helper, clamp floating-point cosine inputs, test empty,
   coincident, antipodal and normal flights; show an unavailable-feed status.
2. Globe render-time ref mutation and FPS render-time clock reads triggered lint
   warnings. Move synchronization/clock initialization into effects.
3. Cancel NASA fetches and terrain queues at teardown. Avoid Cesium setup after
   asynchronous terrain loading into a destroyed viewer. Clear MapLibre's pending
   scene timer on route exit.
4. Eager comparison engines inflated the initial bundle to 1.94 MB. Lazy route
   imports reduce the entry to about 302 KB (99 KB gzip). Large lazy map-engine
   chunks remain; Vite's size warning is retained rather than suppressed.
5. Hub development runner depended on an affected esbuild version. Update tsx to
   4.23.15; npm audit reports zero vulnerabilities for the hub lockfile. Keep the
   frozen SpacetimeDB SDK unchanged.
6. Correct misleading live-event and deep-learning descriptions. Record the Rust
   implementation decision and distinguish historical research verification from
   the currently available HazardModel.

## Verification of the combined tree

macOS arm64, Python 3.12.14, Rust 1.99.1; locked engine dependencies.

- Engine: 148 passed, 17 skipped. Skips require optional network/live services;
  they are not acceptance evidence for those services.
- Research CLI adapter: 38 passed using the release native executable.
- Rust workspace release: 50 passed; build succeeded. CI additionally checks debug,
  formatting and clippy on Linux/macOS.
- Full N=16 synthetic fixture: 36 forecast + 3 pre-ignition cycles, 111 observations,
  checkpoint next-step parity, 7 alerts; 114.405 s total on this local run.
- Node fixture decoder accepts all 39 entries and 72,000-byte arrival rasters.
- Web: zero lint warnings, three helper tests, production build. Hub: TypeScript
  check and zero npm-audit vulnerabilities after the development dependency update.
- Browser: homepage focused on Eaton, timeline playback, dashboard, comparison
  navigation and Cesium scene load checked locally. External tile availability and
  full long-running browser behavior are not covered by this smoke check.
- Frozen HazardModel API compatibility probe passes.

Reproduce from the repository root (Python dependencies installed in `engine/.venv`):

```sh
cargo build --manifest-path physics/surface_v1/Cargo.toml --workspace --release --locked
engine/.venv/bin/python -m pytest engine/tests -q
FIRE_CLI="$PWD/physics/surface_v1/target/release/fire-cli" engine/.venv/bin/python -m pytest physics/surface_v1/adapter/tests -q
cargo test --manifest-path physics/surface_v1/Cargo.toml --workspace --release --locked
engine/.venv/bin/python -m emberline.hazard.compatibility
engine/.venv/bin/python -m emberline.hazard.demo --out /tmp/emberline-combined-demo --members 16
node tools/check_hazard_frontend.mjs /tmp/emberline-combined-demo
node --test tools/check_hazard_frontend.test.mjs
```

In `web/`: `npm ci --ignore-scripts`, `npm run lint -- --deny-warnings`, `npm test`,
`npm run build`. In `stdb/`: `npm ci --ignore-scripts`, `npm exec -- tsc --noEmit`.
The Python package must be installed (the existing editable locked environment does
this); merely invoking an arbitrary Python from the repository root is insufficient.

## Gates that remain open

Prior unchanged evidence: N=128 synthetic assimilation improved outline Sørensen
from 0.7259 to 0.9132, but posterior wind was 5.53° against 20° truth (8° tolerance)
and ROS multiplier 0.978 against 1.6 (25% tolerance). The low-trust report produced
29.55 m mean boundary change against a 300 m gate. These are failing gates.
The prior cold/contended 128-member, 340×340, six-hour benchmark took 244.25 s
against a 0.5 s target. The small integration fixture is not a replacement benchmark.

Next: (1) Cody forcing/runtime/publisher assembly, (2) Linda FileSource and forecast
rendering using the existing decoder contract, (3) live hub acceptance, (4) calibration
and independent historical evaluation, (5) profile native propagation before optimizing.
Detailed implementation and acceptance criteria: [next tasks](RUST_HAZARD_NEXT_TASKS.md).
