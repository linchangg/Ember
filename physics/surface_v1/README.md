# Surface physics research engine

This is an additive Rust research workspace imported from `cursor/surface-v1-physics-engine` (`99b7f45`) onto Ember's existing history. It implements extended Rothermel local surface behavior and a terrain-projected elliptical level-set solver. It is separately runnable; it does not replace the Python `HazardModel`, MTT ensemble, contracts, fixture pipeline, data collectors, or UI.

Read [MODEL_AUDIT.md](docs/MODEL_AUDIT.md) for model coverage, evidence, defects fixed, and remaining gates. The root `SPEC.md`, `AGENTS.md`, `spec/00_contracts.md`, and `spec/02_physics.md` continue to govern the demo. The root `ENGINE_BUILD_PLAN.md` specifies this research engine; `docs/BUILD_PLAN.md` here is the original spike's short provenance note.

## Build and verify

Install Rust with rustup. The local `rust-toolchain.toml` pins Rust 1.99.1, rustfmt, and clippy. From the repository root:

```sh
cd physics/surface_v1
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo test --workspace --release --locked
```

The checks need only Rust and the locked Cargo dependencies. They use committed independent oracle fixtures; they do not download training data or depend on live feeds. GitHub CI repeats them on Linux and macOS. Running locally here verifies macOS only; see the PR checks for CI results.

## Run a synthetic forecast

Use a new destination for each command. Completed results and checkpoints are immutable; existing output or staging directories are rejected.

```sh
cargo run --release -p fire-cli -- make-fixture --kind fm1-circle --out /tmp/surface-example-input
cargo run --release -p fire-cli -- validate --input /tmp/surface-example-input/manifest.json
cargo run --release -p fire-cli -- simulate --input /tmp/surface-example-input/manifest.json --out /tmp/surface-example-run
cargo run --release -p fire-cli -- checkpoint --input /tmp/surface-example-input/manifest.json --until-s 600 --out /tmp/surface-example-checkpoint
cargo run --release -p fire-cli -- resume --input /tmp/surface-example-input/manifest.json --checkpoint /tmp/surface-example-checkpoint --until-s 3600 --out /tmp/surface-example-resumed
```

Input arrays are little-endian row-major, row zero south, with a lower-left metric origin; time is f64 seconds since `start_utc`. Moisture is a fraction and wind is a midflame velocity **toward** east/north in m/s. These conventions differ from Ember's north-first 90 m arrays and float32 minutes. Geographic lon/lat grids are rejected. Normalized UTC timestamps currently require whole-second `YYYY-MM-DDTHH:MM:SSZ`; normalize offsets/fractions upstream. Weather must have been available at the forecast origin, including forecast weather valid later.

A run publishes `run.json`, `diagnostics.json`, arrival times/status, and integer-second `horizon_000300/` directories with phi/burned/front arrays. Run metadata schema is 2 and includes its grid. Checkpoints use schema 2, verify input and state hashes, and reject old spike checkpoints. Resume exports snapshots captured after restoration; it does not recreate earlier snapshots absent from the checkpoint. Partial runs report their actual completion time and captured horizons.

### Ensembles

Save this as `/tmp/surface-ensemble.json`:

```json
{
  "schema_version": 1,
  "preset": "demo_global_bias_v1",
  "members": 8,
  "master_seed": 42,
  "workers": 2,
  "memory_budget_bytes": 268435456,
  "wind_bias_std_m_s": 0.5,
  "dead_moisture_log_std": 0.1,
  "live_moisture_log_std": 0.1,
  "front_bias_std_m": 0.0,
  "allow_partial": false
}
```

```sh
cargo run --release -p fire-cli -- ensemble --input /tmp/surface-example-input/manifest.json --config /tmp/surface-ensemble.json --out /tmp/surface-example-ensemble
```

`ensemble.json` lists hashes, array paths, member seeds, perturbations, successful-member count, and semantics. Binary outputs contain per-horizon scenario frequencies, no-arrival fractions, conditional nearest-rank arrival q10/q50/q90 in seconds, validity and member coverage. Invalid coverage is NaN with `valid=0`, not zero risk. An empty modeled-arrival sample also produces NaN quantiles. Initial burned cells contribute to burned frequency but have unknown historical arrival time. Successful-member frequencies are uncalibrated; a partial ensemble is labeled. The memory budget uses a conservative array reservation and bounds active input clones; it is not an OS-enforced process-memory limit.

### Corrections

Use `fire-cli correct --parent-input PARENT_MANIFEST --checkpoint CHECKPOINT --update UPDATE_JSON --out NEW_DIRECTORY`. `UPDATE_JSON.grid_manifest` and `corrected_phi` resolve relative to the update file. Other fields follow `CorrectionManifest` in `fire-types/src/input.rs`; the executable tests in `crates/fire-cli/tests/workflow.rs` contain a complete example.

The corrected bundle must preserve the parent grid and time origin. The CLI resumes the corrected state, retains correction metadata, hashes the observation phi, and records the parent checkpoint metadata hash. Existing arrivals may be preserved or the observed interior reset to unknown. This version supports immediate observations only: observed/available timestamps must equal the correction time. Delayed-observation replay, changing the time origin, particle filtering and posterior inference need a separate implementation. A replacement perimeter is not proof of a scientifically validated assimilation method.

### Evaluation

`fire-cli evaluate --predictions RUNS_JSON --truth TRUTH_JSON --protocol PROTOCOL_JSON --out REPORT_JSON` supports exploratory binary-mask scoring. Full fixture formats are exercised in `crates/fire-cli/tests/workflow.rs`. Use absolute manifest/run references in the outer lists. Truth mask and grid-manifest paths resolve relative to their truth manifest. Prediction and truth must share grid and time origin. The protocol must use `baseline="none"`, `mask_rule="intersection_valid"`, `bootstrap_incident_replicates=0`, and `release_claim="exploratory_only"`. Other requested methods fail explicitly; there is no historical benchmark, bootstrap interval, baseline evaluation, or calibration claim here.

## Regenerate the independent reference

Only maintainers changing local behavior need this. CMake and a C++11 compiler are required. The source is the USFS Fire Sciences Laboratory's public-domain Behave library, pinned to a specific commit:

```sh
git clone https://github.com/firelab/behave.git /tmp/surface-behave-oracle
git -C /tmp/surface-behave-oracle checkout 7e3d7a44b7e7eed271ec3029ec34f0bd66466532
python3 tools/oracle/regenerate.py --behave-source /tmp/surface-behave-oracle
cargo test -p fire-behavior --test behave_oracle --locked
```

The script refuses changed or unpinned source, builds and runs the upstream tests, compiles our independent C++ reference driver against upstream objects, then records 42 cases and provenance. Of these, 36 compare matching conventions strictly; six record the known GR1 curing difference. Upstream defaults to no wind cap, so matching Rust comparisons explicitly select `WindLimitPolicy::None`; another test checks the project's enabled cap.

## Integration boundary

Do not route these binary files directly to the UI or `arrival_q.bin`. A future approved `HazardModel` adapter must reverse row order, reconcile the 90 m UTM grid, convert seconds to minutes with a fixed `t_ref`, and construct unconditional arrival quantiles using no-arrival mass. It also needs model-version selection, Python save/load parity, observation cutoff rules, and end-to-end fixture tests. Conditional research quantiles cannot be copied into the demo output contract. AllConvNet, PhysFire-WM, learned residuals, and other deep-learning models are not implemented.

## Research adapter

`adapter/` is a research compatibility layer: row-order and time conversion, unconditional quantiles from member outcomes, and a subprocess bridge to a release `fire-cli`. The field map and the decisions still open are in [docs/ADAPTER_SPEC.md](docs/ADAPTER_SPEC.md). Workload timing is [docs/BENCHMARK_PROTOCOL.md](docs/BENCHMARK_PROTOCOL.md). This layer does not implement `HazardModel` and does not select the application backend.
