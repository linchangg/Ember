# Adapter verification

Historical research-branch report (baseline `8cea5fc`). On 2026-10-24 the research
adapter was combined with the separate Rust-backed HazardModel implementation.
`engine/ember/hazard/` now exists; the statements below about its absence
describe this report’s original baseline. See `docs/MAIN_INTEGRATION_REVIEW.md`
at the repository root for the combined verification and remaining gates.

Checked on macOS arm64, Rust 1.99.1, Python 3.12.11, numpy 2.5.4. Commands were run from `physics/surface_v1` unless noted. `CARGO_TARGET_DIR` was unset so build artifacts stayed in `physics/surface_v1/target`.

## Rust

```text
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo test --workspace --release --locked
```

The four commands were chained. The chain exited 0. Formatter and clippy produced no errors. Debug and release test binaries reported passing results, including the geometry, workflow, checkpoint, and ensemble regressions. No solver equation was changed.

## Python

```text
python3.12 -m pip install 'numpy>=2' 'pytest>=8'
FIRE_CLI=target/release/fire-cli python -m pytest adapter/tests -q
```

Outcome: `38 passed` (numpy 2.5.4). That includes grid, time, and arrival unit tests, subprocess failure classes, and the synthetic Rust path. The synthetic path is not a `HazardModel` acceptance test. `engine/ember/hazard/` is still absent.

## Smoke benchmark

```text
python tools/benchmark_workloads.py --out docs/benchmark_smoke.json --repeats 3 --warmup 1 --max-wall-s 600
```

Profile `smoke` completed at commit `97c1870d0fbf9eb574d4b7ce50c732d069647a97`. One warm-up and three repeats, 64×64, 90 m, one member, 3600 s horizon, release `fire-cli`.

| | Warm-up | Repeat median (min–max) |
|---|---|---|
| End-to-end wall | 0.318 s | 0.0479 s (0.0467–0.0507 s) |
| Solver | 0.304 s | 0.038–0.043 s |
| Peak RSS | 3.8 MB | 3.4–3.9 MB via `/usr/bin/time` |

Build time on this already-warm tree was 16.5 s and is recorded separately. Requested and completed members were 1. No numerical failures or timeouts. Quality flag `terrain_gradient_incomplete` was recorded. `member_128_gate_passed` is false. The 340×340 profiles were not run. The five-minute update profile is blocked in the protocol and was not simulated.

These times are a uniform FM1 window, not a 128-member Eaton forecast.

## Limitations

- No `HazardModel` implementation exists to accept.
- Ensemble CLI output still has no per-member arrival arrays. The synthetic ensemble uses repeated `simulate` runs.
- Parent timeouts are not resumable solver cancellation.
- Hosted CI for this commit was not observed from this machine.
