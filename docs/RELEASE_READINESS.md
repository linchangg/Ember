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
Reproduce with `python -m emberline.hazard.benchmark --out NEW_PATH --members 128
--size 340 --workers 2`. Next optimization must preserve native numerical parity:
profile member propagation, move authoritative state incrementally, cache forcing and
landscape transforms, then consider bounded active regions. Measure isolated repeated
warm runs and process-tree memory before changing the performance claim.

## Replay coverage

The committed Eaton main cache contains 80 of 157 replay issues, ending at
2025-01-08 08:35 UTC. All 80 bundles pass schema and observation-availability checks.
Complete and verify the intended branch caches before claiming a complete Eaton demo.
Use a separate cache root for regenerated outputs and retain their actual model,
configuration and environment identity.

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

The engine already has local report ingestion, runtime report commands, the outbound
Cartesia request builder and file-mode call cards. The Vercel report/voice and callback
service functions are not present. Complete those against `spec/05_ai_voice.md`, test
service-only auth and report availability through a real worker/branch, then add the
phone submission controls. External delivery rehearsal needs provider configuration
and an explicitly selected test recipient. No call or message was sent during this
integration, and a precomputed call card is not delivery evidence.

Recheck the committed data with `PYTHONPATH=engine python scripts/validation/eaton_data_smoke.py`.
This verifies HRRR checksums and release-time selection across the replay, then runs
a two-member Rust PF forecast on actual Eaton statics and delivered observations.
It does not modify the 128-member default or establish historical skill.
