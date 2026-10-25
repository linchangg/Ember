# Integration and remaining release gates

Implementation instructions, phase approvals and executable replay audit:
[Release implementation framework](RELEASE_IMPLEMENTATION_FRAMEWORK.md).

Merged via [PR #11](https://github.com/linchangg/Legendary-Fire-Project/pull/11)
on 2026-10-25, main `0308ba8`. All five PR checks passed. Local engine suite:
311 passed / 18 skipped; full synthetic fixture, report branch and decoder passed.

The integration preserves main's globe UI, Eaton statics, VIIRS ingestion, documented
report inputs, official evacuation-time comparison and precomputed duty-officer cards.
Connected forecast panels and explicit file/hub sources remain at `/dashboard`.
No shared wire schema changed. Evidence: `docs/evidence/main-integration/`.

## Measured performance

A single cold synthetic six-hour forecast at N=128, 340×340 cells, 90 m, two Rust
workers took **94.649 s**, versus a **0.5 s** target. Target not met. macOS 15.7.9,
arm64, 10 logical CPUs. Python peak RSS 635,387,904 bytes and child peak RSS
685,637,632 bytes are separate process peaks, not a simultaneous process-tree total.
This run has no observations/reanchoring and is not a p95 or a historical accuracy test.
Reproduce with `python -m ember.hazard.benchmark --out NEW_PATH --members 128
--size 340 --workers 2`. Next optimization must preserve native numerical parity:
profile member propagation, move authoritative state incrementally, cache forcing and
landscape transforms, then consider bounded active regions. Measure isolated repeated
warm runs and process-tree memory before changing the performance claim.

## Replay coverage

Main now keeps the Eaton cache out of Git (`96ea89b`); its earlier tracked subset
contained 80/157 issues. Cody's main commit `b408fe2` fetches the public
`demo-data-eaton-v1` release for Pages. The release was downloaded with its declared
SHA256 verified: main, satellites-only and openloop each have **157/157** issues,
with 462 referenced forecast assets checked per branch and no inventory errors.

The archive omits 471 immutable bundle copies referenced by its indexes. The new
Pages packaging step restores them only after canonical bytes match the indexed
SHA256; indexes and predictions are unchanged. It rejects mismatches and excludes
private checkpoints/evidence/dispatch journals from the site. Standalone preparation:

```sh
node web/scripts/restore_bundle_versions.mjs /path/to/extracted-release eaton
```

See `docs/evidence/published-eaton-release.json` for source IDs, checksum and audits.
This closes the development demo's published replay coverage, **not** target-profile
or scientific validation. All three branches are N=16; a matched reinit baseline,
N=128 runs and raw-member historical exports remain separate work. Model perimeters
in this archive are forecasts, not independent observed-perimeter truth.

## Real-data and scientific gate

Cody's main commits `0e7ce57` and `655b775` supply Eaton's HRRR forcing, precompute
cache and raw sources. These are included in this integration. Real terrain/fuels
and GOES/VIIRS inputs are present; independent timestamped burned-perimeter truth
is still absent. `zone_orders.json` is evacuation-order context, not fire perimeter
truth; active-fire observations used for assimilation cannot also establish independent
accuracy. No substitute data or scientific score is generated.

Required handoff: independently sourced
perimeters with valid times, source/publication times and coverage masks. Run none,
reinit and PF on identical causal inputs, retain raw member arrivals, and feed the
existing `hazard.historical` framework at 30/60/120/360 minutes. Calibrate only on
training/calibration incidents and reserve held-out test incidents. Keep default/frozen
parameters unchanged until that evidence exists. See `HISTORICAL_VALIDATION.md` and
`SCIENTIFIC_CALIBRATION.md` for schemas, equations and reproducible scoring commands.

## Report and notification gate

The release implementation adds server-side signed report sessions, bounded tap/voice
handlers, authenticated Cartesia callback tools, a persistent dispatch reservation
journal and `/p` submission controls. Local browser join/tap submission, service tests
and an actual published-hub/Rust report-to-child integration are verified. Rejected
reports are published explicitly; backward seeks can restore an ancestor branch.
Private branch state, dispatch journals and checkpoints are excluded from local assets.

This remains a partial phone implementation: assigned locations only, no spoken
landmark geocoding, no phone danger-map/alert playback, no automatic transport reconnect,
and no proven <=5-second report-to-forecast budget. An accepted report becomes part of
the following configured cycle; a paused clock must advance before that cycle appears.
GitHub Pages is static and cannot host these API handlers. Use Vercel or the documented
local service adapter for `/p`; no service credential belongs in browser configuration.
External delivery rehearsal still needs provider configuration and an explicitly
selected test recipient. Voice is disabled by default. No external call or message
was sent; callback tests and precomputed call cards are not delivery evidence.

See `RELEASE_COMPLETION_PLAN.md`, `RELEASE_COMPLETION_RESULTS.md` and
`RELEASE_SERVICE_SETUP.md` for implementation scope, results and reproduction.

Recheck the committed data with `PYTHONPATH=engine python scripts/validation/eaton_data_smoke.py`.
This verifies HRRR checksums and release-time selection across the replay, then runs
a two-member Rust PF forecast on actual Eaton statics and delivered observations.
It does not modify the 128-member default or establish historical skill.
