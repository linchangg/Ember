# surface_v1 research adapter

This directory is a research compatibility layer between documented Ember array conventions and the existing `fire-cli` executable. It does not select a production backend, replace the planned Python MTT model, or implement `HazardModel`.

Read [ADAPTER_SPEC.md](../docs/ADAPTER_SPEC.md) before using the converters. Unresolved decisions in that file are intentional. The utilities refuse those mappings instead of picking a silent default.

## What is here

- `conversions.py` — row order, metric edges, time origins, fuel and moisture checks, Rust bundle writer.
- `arrivals.py` — unconditional quantiles from individual member outcomes.
- `bridge.py` — subprocess runner for a prebuilt `fire-cli`. One build is reused.
- `tests/` — conversion tests and a small synthetic Rust path.

The ensemble CLI does not export per-member arrivals. Unconditional application quantiles need those outcomes. The synthetic test repeats deterministic `simulate` runs and does not change the ensemble schema.

## Run the tests

From `physics/surface_v1`, with Python 3.12:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install 'numpy>=2' 'pytest>=8'
cargo build --release -p fire-cli --locked
FIRE_CLI=target/release/fire-cli .venv/bin/python -m pytest adapter/tests
```

`numpy` and `pytest` match the floors in `engine/pyproject.toml`. This package does not import the engine. No service or binding framework is added.

The benchmark harness is `tools/benchmark_workloads.py`. Its protocol is [BENCHMARK_PROTOCOL.md](../docs/BENCHMARK_PROTOCOL.md). Smoke is the default. Larger profiles and the blocked five-minute update profile are explicit.
