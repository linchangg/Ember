# Phase A — fixtures and hosting for Linda's globe (revised)

This revision supersedes the earlier route/configuration instructions. Linda's
`GlobeNetwork.jsx`, `scenarioData.js` and `replayClock.js` are the application path.
The Rust fixture and scientific validation work remain reusable; no separate UI is needed.

## Two deliberately different fixtures

| Fixture | Source | Use |
|---|---|---|
| `fixture`, checked into `fixtures/` | Linda/main's seeded analytic 50-member ensemble, 120×120 at 255 m | Fast deterministic UI regression; not Rust propagation |
| `rust-fixture`, generated outside `fixtures/` | Rust HazardModel, 16-member development profile, 120×120 at 255 m | End-to-end adapter/assimilation/encoding regression; not historical skill |

Both have 36 forecasts and 3 pre-ignition bundles. Keep names, model version,
configuration identity and provenance distinct. Do not overwrite the canonical fixture
with Rust output or describe either as a held-out historical evaluation.

## Run from repository root

Use the locked Python environment and Rust toolchain documented in
`RUST_HAZARD_BACKEND.md`. If dependencies are absent, prepare them as directed there.

```sh
cargo build --manifest-path physics/surface_v1/Cargo.toml -p fire-bridge --release --locked
engine/.venv/bin/python -m emberline.hazard.demo --out /private/tmp/emberline-dashboard-phase-a --members 16
node tools/check_hazard_frontend.mjs /private/tmp/emberline-dashboard-phase-a
engine/.venv/bin/python -m emberline.hazard.fixture_server \
  --root /private/tmp/emberline-dashboard-phase-a --port 8791 \
  --allow-origin http://127.0.0.1:5175
```

In a second terminal (Node 22.18+ or Node 24):

```sh
npm --prefix web ci --ignore-scripts
VITE_DATA_SOURCE=file VITE_SCENARIO_ID=rust-fixture \
  VITE_ASSET_BASE=http://127.0.0.1:8791/ \
  npm --prefix web run dev -- --host 127.0.0.1 --port 5175
node tools/check_dashboard_source.mts http://127.0.0.1:8791/ rust-fixture
```

Open `http://127.0.0.1:5175/` and **Open scenario**. The NASA event feed is optional
for this action. Do not use `/dashboard` for the integration; it retains the original
mock. For the analytic fixture, omit the environment variables and start Vite; Linda's
scenario middleware serves the checked-in files. Production hosting must explicitly
publish/proxy `/static` and `/cache`: Vite's development middleware is not a production server.

## Acceptance and evidence

The independent wire decoder and Linda-path HTTP checker must accept all 39 entries.
Startup/missing/index/binary failures must be visible, not replaced with mock forecasts.
Allow only the exact preview origin when using the Rust fixture host. No hub, secrets,
notification delivery or new service is required for offline file replay.

Original Phase A generation evidence remains in `docs/evidence/phase-a/`: 114.130 s
for the complete synthetic run, 111 observations, checkpoint next-step parity. This
revision reuses that generated artifact rather than claiming a new native solver run.
Target-size latency, historical skill and calibration gates remain separate.
