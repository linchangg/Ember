# Release implementation review results

2026-10-25. Plan: `RELEASE_COMPLETION_PLAN.md`. Branch: `release/all-phases`.
Integrated main through `1d56f83`, including the current globe, guided tour, Pages
hosting and presentation changes. No frozen table/type/reducer signature changed.
The existing `rewind` reducer now performs its specified ancestor-seek behavior.

## Implemented and verified

- Explicit runtime cache/bundle roots, bounded precompute pilots, selected-scenario
  asset hosting and truthful partial-cache coverage.
- Raw member/weight export through the causal orchestrator with complete consumed
  observation history and per-issue forcing audits. Independent truth import
  preserves holes, projected coordinates, valid time and unknown coverage.
- Public perimeter-source snapshots with pagination, identity checks and hashes;
  positive future-detection consistency diagnostics separated from independent skill.
- Configurable calibration seeds and repeated target-size timing/RSS framework.
  Failed timings now retain partial measurements and return a failure status.
- Durable report evidence, checkpoint-based child branches, next-cycle availability,
  lost-ack reconciliation and explicit rejection publication. Backward seeks across
  nested forks select the correct ancestor. Parent cycles remain unchanged.
- Signed service sessions, tap controls, bounded optional voice classification,
  authenticated callback tools and conservative persistent call-dispatch reservation.
  Internal checkpoints/evidence/journals are not public cache assets.

The actual published-hub/native integration passed: a report command reached the
Rust PF backend, published accepted/assimilated child evidence, preserved parent
cycles and exported a matching raw two-member forecast. This is synthetic integration
evidence, not historical skill. CI now runs this check in a separate fresh database.

The local browser joined `/p`, connected to the hub and stored a smoke report with
providers disabled. It correctly displayed "awaiting engine review" because that
particular Eaton display database had no live worker. A separate worker integration
test established assimilation; the two pieces of evidence are not conflated.

## Validation ledger

| Check | Result |
|---|---|
| Full engine suite before integrating newly updated main | 353 passed, 18 skipped |
| Full engine suite after integration/review | 357 passed, 19 skipped; later-added pilot/timeout cases also passed in focused checks |
| Actual published hub contracts, including nested-fork rewind | 15 passed |
| Actual report command → native PF → child publication/raw export | 1 passed |
| Repaired report/replay/private-asset checks | 15 passed |
| Acquisition/import/positive-event diagnostic checks | 18 passed |
| Benchmark harness, including retained timeout evidence | 7 passed |
| Release inventory audit regressions | 9 passed |
| Dashboard unit checks | 48 passed, 1 configured skip |
| Report/callback/voice-stub/audio checks | 19 passed |
| TypeScript checks, lint, production build, hub type check | Passed locally |
| Fresh complete 16-member Rust fixture + report branch | Running; completion and independent decoder pending |
| Remote CI on this implementation | Pending publication |

Configured hub tests are skipped in the ordinary suite; they are run separately
against a disposable published database. Provider tests use stubs. No actual call,
provider request, deployment or message to a teammate was made.

## Release gates still open

1. **Full Eaton replay:** the committed main cache remains 80/157 issues. New PF,
   open-loop and reinit development runs use a separate root. The last accessible
   reinit index had 10/157; other new indexes were cloud-offloaded at review time.
   Do not infer completion from cycle directory counts. These runs do not establish
   N=128 coverage. Copy/continue runs in storage that will remain locally available,
   then run `release_audit.py` on every intended branch and record hashes/coverage.
2. **Independent historical accuracy:** no usable overnight Eaton burned-perimeter
   sequence with explicit coverage was acquired. The downloaded NIFC polygon is
   later context. Obtain original FireGuard GIS/coverage/lineage or select another
   incident with verified IR snapshots; follow `PERIMETER_SOURCE_REVIEW.md` and
   freeze independent matched cases before scoring.
3. **Scientific calibration:** the completed N=32 twin/report experiment did not
   pass the full gate. Outline improvement/ESS did not establish recovery of true
   wind/ROS parameters; the report-shift target also failed. Preserve all seeds and
   failures. Do not change default parameters. First fit/compare observation-model
   candidates on independent calibration cases, then evaluate held-out incidents.
4. **Latency:** the earlier isolated cold target-size forecast was 94.649 s versus
   0.5 s. The repeated N=128/340² run was contended and timed out at 900 s before
   completing ten measured samples. `evidence/release-contended-benchmark.json`
   retains the successful steps and failure disposition; it is not an accepted warm
   p95. Profile native replay/state reconstruction, implement numerically equivalent
   incremental state and reuse forcing/landscape work, then run isolated repeated
   measurements with convergence/parity checks. No such solver optimization is
   included here and no latency gate is passed.
5. **Phone/delivery:** assigned-location reporting is implemented; spoken landmark
   geocoding, phone danger-map/alert playback, automatic reconnect, <=5-second branch
   response and authorized provider rehearsal remain open. Static GitHub Pages
   cannot run `web/api`; see `RELEASE_SERVICE_SETUP.md` for the hosting boundary.

These limitations belong in the PR description. Publish a draft while integration
evidence is incomplete; do not represent this as a calibrated production release.
