# Phase B verification

Base: Phase A `cce64dc` plus main `2181e1b`, combined in `19f1b50`.
Local Node 24.22.1, TypeScript 5.6.3.

- Web suite: 26 passed on current canonical fixture.
- Same suite: 26 passed using the independently generated N=16 Rust fixture.
- Strict data-layer type checking: passed.
- Web lint with warnings denied: passed, zero warnings.
- Production web build: passed. Existing large lazy map-engine chunk warning remains.
- Independent Node contract checks: three passed.
- Actual FileSource over HTTP: 36 forecast + 3 pre-ignition entries, 111 eligible
  observations, 5 unique alert IDs (the fixture contains 7 alert records/transitions).
  Selected cycle, raster and risk rows remain consistent; backward seek clears
  pre-ignition forecasts; future view uses the latest currently known issue.
- Branch fixtures verify parent-cycle cutoff, independent child assets and return
  to parent; malformed and out-of-order requests are covered without mock fallback.
- React hooks tested via server rendering; lifecycle leases tested for StrictMode-style
  setup/cleanup/setup and source replacement. No map is mounted or changed in Phase B.

Evidence files: canonical-tests.txt, rust-fixture-tests.txt and http-source.json.
CI now includes strict web type checks and actual FileSource HTTP checks in the
Linux/macOS native-fixture jobs. These local results do not claim that future CI
runs have passed, or that live hub/engine/dashboard integration is complete.
Scientific calibration, historical skill and target latency remain separate open gates.
Stop before Phase C until direct owner confirmation.
