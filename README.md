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
| `/` | Command dashboard (mock Sierra Vista scene) |
| `/compare` | Globe pattern bake-off hub |
| `/compare/cesium` | **Pattern 1** — Cesium only + Esri WorldElevation3D |
| `/compare/maplibre-three` | **Pattern 2** — MapLibre + AWS Terrarium DEM + Three.js fire layer |
| `/compare/hybrid` | **Pattern 3** — MapLibre world → handoff to existing incident canvas |

Fire footprints are synthetic. Elevation/imagery are real (Esri / AWS Terrarium). Use the on-screen FPS meter while orbiting/zooming to compare smoothness.

Design reference (exported mockup) is in [`design/command-dashboard-mockup/`](./design/command-dashboard-mockup).

## Roadmap

- Choose a globe stack from the bake-off and wire live satellite / FireSat + physics-ML forecasts
