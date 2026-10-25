# surface_v1 workload benchmark protocol

This protocol times the existing release `fire-cli`. It does not change solver algorithms, and it does not decide whether Rust is an application backend.

## Profiles

| Profile | Grid | Members | Forecast horizon | How to select |
|---|---|---|---|---|
| Smoke | 64 × 64 | 1 | 1 hour | default |
| Single application grid | 340 × 340 | 1 | 6 hours | `--profile single_grid` |
| Small ensemble | 340 × 340 | 8 | 6 hours | `--profile small_ensemble` |
| Target workload | 340 × 340 | 128 | 6 hours | `--profile target` |

Application-grid profiles use 90 m square cells and `EPSG:32611`. The origin is the specified Eaton upper-left outer edge, `(381480, 3800280)`. Smoke uses that cell size, CRS, and origin on a 64 × 64 window. It is not the full 340 × 340 domain.

Cells are FM1 (`fuel_id` 1) from the committed catalog. Elevation is flat. One weather frame at `time_s = 0` holds a 1 m/s east, 0 m/s north midflame wind and fixed moisture fractions. The ignition is a research disk. These inputs are synthetic.

Smoke and the single-grid profile call `simulate` once per repetition. Ensemble profiles call `ensemble` once per repetition with `master_seed` 7, `allow_partial` false, and `workers = min(cpu_count, members)`. The executable is built once with `cargo build --release -p fire-cli --locked` and reused. Members are not separate builds.

Horizons are the integer seconds `3600` or `21600`. Solver settings match the committed fixture defaults: CFL 0.4, max step 30 s, wind limit `effective_0p9_ir`, `ellipse_max_lb` 8, `reinitialize` false.

## What is recorded

Each JSON result includes:

- Git commit, model `surface_v1`, solver `level_set_lf_euler_v1`.
- `rustc` version, Python version, OS, architecture, CPU count.
- Physical memory and available memory when the platform reports them. Unavailable stays null.
- Grid, fuel, weather-frame count, horizon, members, workers, and seed.
- Build time, separate from execution. The command is the release build above. It is incremental if `target/` is already warm; the JSON says so. It is not a clean rebuild unless the binary was absent.
- Per run: input preparation, solver wall time, aggregation, output accounting, and end-to-end wall time.
- Peak memory and the measurement method.
- Requested and completed members, output size, numerical failures, timeouts, and quality flags.
- Raw per-run records, plus the median, minimum, and maximum of the repeated end-to-end times. The warm-up run is stored separately and is not in that median.

Aggregation for a one-member smoke run is the research unconditional quantile utility and is timed on its own. Ensemble profiles aggregate inside `fire-cli`. That time stays inside the solver measurement. `aggregation_s` is null there, with a note. Conditional ensemble quantiles are not reported as application quantiles.

Memory uses `/usr/bin/time` maximum resident set size of the `fire-cli` process (bytes on macOS, kilobytes converted to bytes on GNU time). Worker threads share that process. If `time` is missing or its output cannot be parsed, peak memory is null and the method is `unavailable`. An array-size estimate is not substituted.

The warm-up is the cold process start. Repeated runs are the steady measurements. A parent `--max-wall-s` limit (default 600 s per run) terminates a run through the bridge timeout. That is not solver-level resumable cancellation. An interrupted profile is `completed: false` and is not a passed gate.

`member_128_gate_passed` is true only when the `target` profile finishes with 128 completed members. Selecting the profile is not a pass. A failed or skipped target stays false.

## Five-minute updates

A sequence of five-minute forecast updates is a different benchmark. It is blocked.

Rust corrections accept only an immediate observation: observed time, available time, and correction time must be equal. The application cycle also depends on the unlanded `HazardModel` step, particle filter or reinit, and checkpointed Python state. This harness does not invent a workload that only looks like that cycle.

`--profile five_minute_updates` writes a blocked record and does not launch a substitute run.

## How to run

From `physics/surface_v1`:

```sh
cargo build --release -p fire-cli --locked
python3.12 tools/benchmark_workloads.py --out docs/benchmark_smoke.json
```

Larger profiles:

```sh
python3.12 tools/benchmark_workloads.py --profile single_grid --out /tmp/single_grid.json --max-wall-s 3600
python3.12 tools/benchmark_workloads.py --profile small_ensemble --out /tmp/small_ensemble.json --max-wall-s 3600
python3.12 tools/benchmark_workloads.py --profile target --out /tmp/target.json --max-wall-s 7200
```

Repeats default to 3 after 1 warm-up. `--repeats` and `--warmup` change that. Raster directories are temporary. The JSON is the preserved measurement record.

CI runs the conversion tests and the small bridge smoke test. It does not run this matrix. The 340 × 340 profiles are manual.
