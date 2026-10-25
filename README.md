# Legendary Fire Project · Emberline

Wildfire command dashboard for Cal Hacks, with satellite-data tooling and an experimental Rust fire-spread backend. The current globe and dashboard use labeled prototype overlays; they are not yet connected to the Rust forecasts.

## Web UI

The Emberline command dashboard lives in [`web/`](./web).

```bash
cd web
npm install
npm run dev
```

Open the printed local URL (default `http://localhost:5173`).

### Routes

| Path | What |
|------|------|
| `/` | Globe home page: terrain, labels, simulated Eaton spread + forecast (`/?focus=eaton`) |
| `/dashboard` | Earlier command dashboard (mock Sierra Vista scene) |
| `/compare` | Globe pattern bake-off hub |
| `/compare/cesium` | **Pattern 1** — Cesium only + Esri WorldElevation3D |
| `/compare/maplibre-three` | **Pattern 2** — MapLibre + AWS Terrarium DEM + Three.js fire layer |
| `/compare/hybrid` | **Pattern 3** — MapLibre world → handoff to existing incident canvas |

Fire footprints are synthetic. Elevation/imagery are real (Esri / AWS Terrarium). Use the on-screen FPS meter while orbiting/zooming to compare smoothness.

Design reference (exported mockup) is in [`design/command-dashboard-mockup/`](./design/command-dashboard-mockup).

## Roadmap

- Choose a globe stack from the bake-off and wire live satellite / FireSat + physics-ML forecasts
- Zoomed-out interactive **world topographic globe**, drill-down into regional terrain
- Live satellite / FireSat feeds and real physics-ML forecasts

## Engine build plan

See [Engine Build Plan](ENGINE_BUILD_PLAN.md) for the proposed Rust physics engine, equations, input/output contracts, implementation milestones, and runnable reference fixtures. It is a research proposal alongside the current Emberline demo specifications.

## Rust HazardModel and research tools

The Rust engine lives in [`physics/surface_v1/`](physics/surface_v1/README.md).
The operational Python adapter is [`engine/emberline/hazard/`](engine/emberline/hazard/),
including ensemble inference, delayed observations, reanchoring and checkpoints.
The separate [`physics/surface_v1/adapter/`](physics/surface_v1/adapter/README.md)
preserves the research CLI conversions and smoke tests; it does not select a backend.

See the [build and integration guide](docs/RUST_HAZARD_BACKEND.md),
[measured acceptance gaps](docs/evidence/rust-hazard/README.md), and
[ordered next tasks](docs/RUST_HAZARD_NEXT_TASKS.md). The owner-approved solver
decision preserves the shared HazardModel/wire contracts. Historical accuracy,
parameter recovery and the 0.5-second latency target remain unfinished.

From the repository root, with Python 3.12, uv and the pinned Rust toolchain:

```sh
uv sync --project engine --locked
cargo build --manifest-path physics/surface_v1/Cargo.toml -p fire-bridge --release --locked
uv run --project engine python -m emberline.hazard.demo --out /tmp/emberline-rust-demo --members 16
node tools/check_hazard_frontend.mjs /tmp/emberline-rust-demo
python3 -m http.server 8787 --bind 127.0.0.1 --directory /tmp/emberline-rust-demo
```

Open `http://127.0.0.1:8787/demo.html` for the complete offline Rust fixture. This
verification viewer is separate from the existing dashboard design. Choose a new
output directory on subsequent runs.

## Deep learning research framework

See [Deep Learning Framework](DEEP_LEARNING_FRAMEWORK.md) for candidate tradeoffs, the selected compact physics-assisted U-Net, Python training and Rust inference, and the gated implementation backlog. This is a research plan; no learned model is active and no forecast-skill or latency improvement has been demonstrated.

Scientific follow-up tools: [calibration equations and experiments](docs/SCIENTIFIC_CALIBRATION.md),
[frozen-artifact historical evaluation](docs/HISTORICAL_VALIDATION.md), and
[Phase A fixture hosting / approval boundary](docs/DASHBOARD_PHASE_A.md).
The example scorer uses synthetic data; it does not establish historical forecast skill.
