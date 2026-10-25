# Legendary Fire Project · Emberline

Wildfire command dashboard for Cal Hacks: satellite fire observations + physics-based ML spread forecasts, surfaced for fire-control operators.

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

## Rust surface physics research engine

The separately runnable Rust engine lives in [`physics/surface_v1/`](physics/surface_v1/README.md).
Its [model audit and verification report](physics/surface_v1/docs/MODEL_AUDIT.md) explains the tested physics, independent Behave comparisons, and remaining integration gates. It does not activate a new backend in Emberline's Python demo.
