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

### What you get (current mock)

- Rotatable 3D synthetic terrain for the **Sierra Vista Fire** scenario
- Time scrubber across observed burn + ensemble forecast
- Ops sidebar: recommendation, town risk probabilities, fire size, data sources

Design reference (exported mockup) is in [`design/command-dashboard-mockup/`](./design/command-dashboard-mockup).

## Roadmap

- Zoomed-out interactive **world topographic globe**, drill-down into regional terrain
- Live satellite / FireSat feeds and real physics-ML forecasts

## Engine build plan

See [Engine Build Plan](ENGINE_BUILD_PLAN.md) for the proposed Rust physics engine, equations, input/output contracts, implementation milestones, and runnable reference fixtures. It is a research proposal alongside the current Emberline demo specifications.
