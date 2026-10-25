# Integration and remaining release gates

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

## Real-data and scientific gate

Both accessible checkouts lack Eaton's `forcing/` directory. The repository contains
real terrain/fuels and GOES/VIIRS inputs, but independent timestamped burned-perimeter
truth is absent. `zone_orders.json` is evacuation-order context, not fire perimeter
truth; active-fire observations used for assimilation cannot also establish independent
accuracy. No substitute data or scientific score is generated.

Required handoff: the frozen HRRR files and checksums, then independently sourced
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
