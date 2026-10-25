# Rust HazardModel verification — 2026-10-24

The adapter and observation update paths work in the complete synthetic fixture.
They are ready for engineering review. **The backend does not yet pass scientific
parameter-recovery, low-trust report-effect, or real-time latency acceptance.**
These failures are explicit follow-up work, not tests silently waived as successful.

Integrated against `origin/main` at `8cea5fc` (Cody's data/risk/cache/hub wave).
Python 3.12.14 with the repository's exact `uv.lock`; macOS 15.7.9 arm64.
The shared Python/TypeScript wire contracts and canonical fixture files are unchanged.

| Check | Result |
|---|---|
| Complete combined Python suite | **148 passed, 17 skipped**, 12.32 s |
| Rust workspace debug | **50 passed** |
| Rust workspace release | **50 passed** |
| Rust formatting and strict all-target Clippy | Passed |
| New Python formatting and undefined/unused-name lint | Passed |
| Frozen HazardModel API/schema compatibility probe | Passed |
| Independent oracle regeneration | **215/215 upstream Behave tests passed**; regenerated references unchanged |
| Full integrated fixture | **36 Rust forecast + 3 pre-ignition cycles**, N=16, 120×120/255m, 6h horizon |
| Assimilation in fixture | **111 synthetic observations used**; GOES + two added VIIRS passes + delayed ground report |
| Alert publication | **7 proposals/transitions**, using Cody's actual alert evaluator; no messages/calls sent |
| Checkpoint | Fresh-object restore reproduced the next cycle's quantiles and weights exactly |
| Frontend consumer | Passed all 39 entries via filesystem and HTTP; **72,000 bytes** per forecast raster |
| Frontend compatibility guards | **3 Node tests passed**, including rejection of changed TypeScript quantiles and lead order |
| Browser | First/last cycle and lead 0/360 controls verified; **zero browser warning/error logs** |

The fixture took **253.97 seconds** while the larger assimilation evaluation was also
running. It is an integration run, not a latency benchmark. The fixture has no natural
reanchor events; separate native integration tests explicitly exercise late ground
reanchoring, repeated strong GOES, resampling and the reinit baseline.

The 17 skips belong to existing optional network/live-Claude/SpacetimeDB tests.
Those external services were not started or claimed tested. One existing rioxarray
pending-deprecation warning remains. A current-main grid freshness test compared
transformed doubles for exact equality; macOS differed by 1e-14 degrees. Its assertion
now keeps all grid metadata exact and allows 1e-9 degree (sub-millimeter) coordinate
variation. The grid definition and stored grid file were not changed.

## Synthetic skill gates: 128 members

See `assimilation-128.json` and the reproducible evaluator in
`engine/emberline/hazard/evaluate_assimilation.py`. Hidden truth: ROS multiplier 1.6,
wind bias +20°, urban head rate 2 m/min. Observations have GOES-like 2.3×2.8km
footprints at five-minute cadence and two idealized VIIRS positive-detection passes.
This is not an independent historical-data evaluation.

| Metric at two hours | Measured | Gate | Result |
|---|---:|---:|---|
| Posterior mean wind bias | 5.53° | within 8° of 20° | **Fail** |
| Posterior mean ROS multiplier | 0.9784 | within 25% of 1.6 | **Fail** |
| Cycles with ESS ≥.2N | 100% | ≥80% | Pass |
| PF Sørensen, +2h forecast | 0.9132 | beat open-loop 0.7259 | Pass |
| Trust-.25 report mean boundary shift | 29.55m | ≥300m | **Fail** |

The report experiment uses seeds 7/8/9 on a 90m grid. East-head displacement was
90m in all three; symmetric mean boundary shifts were 20.80, 33.86 and 33.97m.
The exact measurement definition is preserved in the JSON and next-task document.
The N=32 development run also improved Sørensen but failed parameter/report gates.

A filter defect found during this evaluation was corrected: an ESS-floor bisection
combined with a strict `< floor` resampling condition stalled subsequent likelihood
updates at alpha near zero. Partial tempering now also triggers resampling/jitter;
a dedicated test protects that behavior. This fixes the numerical stall, not the
remaining statistical identifiability problem. Do not infer parameter accuracy from
the improved perimeter overlap.

## Target-size performance

See `benchmark.json`: N=128, 340×340 cells at 90m, 360-minute horizon, two native
workers, no new observations, 93,440 total member solver steps.

- Forecast duration: **244,251ms (244.25 seconds)** versus the proposed **500ms** target.
- Python process peak RSS: **548,667,392 bytes**.
- Native child process peak RSS: **625,639,424 bytes**.
- These are separate process peaks, not simultaneous process-tree RSS.

Other checks were running concurrently. This is a single cold-run measurement,
not an isolated p95 study; the large target miss is nevertheless unambiguous.
The current solver replays the full domain from ignition to issue+horizon each cycle.
Incremental authoritative state, forecast snapshots, late-data replay and narrow-band
propagation (or a separately validated Rust arrival-time backend) are the next work.
The benchmark overrides timeout to 600 seconds. Use the explicit N=16 development
profile for the fixture; the default 120-second deployment timeout can reject the
target workload until performance improves.

## Reproduce and continue

Exact commands, equations, file layouts, checkpoint/version rules, and current-owner
integration calls are in [the implementation guide](../../RUST_HAZARD_BACKEND.md).
[The ordered task list](../../RUST_HAZARD_NEXT_TASKS.md) specifies owners, implementation
steps and acceptance criteria for runtime, frontend, science and performance work.

`fixture.json`, `frontend.json`, `assimilation-*.json`, `benchmark.json`, environment
versions, and test/oracle logs accompany this report. Generated rasters and viewer
are reproducible outputs, not committed bulk fixtures. The local handoff includes
the complete generated directory and a browser screenshot. Linda's real dashboard,
the full live orchestrator and historical Eaton skill remain separate acceptance steps.
