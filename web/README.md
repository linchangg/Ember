# Emberline web

Linda's globe (`/`) is the connected scenario view. `/dashboard` remains the original
Sierra Vista mock; `/compare` and its engine demos remain available.

```sh
npm ci --ignore-scripts
npm run dev
```

Open the shown URL and select **Open scenario**. Default `fixture` is a seeded
50-member analytic synthetic ensemble, served by Vite's development middleware.
Scenario replay works even if NASA EONET is unavailable. The view opens paused at
ignition+90 minutes; Restart returns to pre-ignition. Scrubbing changes only view
time. Follow clock, speeds, quantile display and +30/+120/+360 controls use Linda's UI.

For the distinct generated Rust fixture, from repository root:

```sh
engine/.venv/bin/python -m emberline.hazard.fixture_server \
  --root /private/tmp/emberline-dashboard-phase-a --port 8791 \
  --allow-origin http://127.0.0.1:5175
```

In another terminal:

```sh
VITE_DATA_SOURCE=file VITE_SCENARIO_ID=rust-fixture \
VITE_ASSET_BASE=http://127.0.0.1:8791/ \
npm --prefix web run dev -- --host 127.0.0.1 --port 5175
```

Open `http://127.0.0.1:5175/`. Restart Vite after environment changes. File mode
is the supported forecast transport; a configured hub clock is labeled separately
from file forecasts. `VITE_DATA_SOURCE=stdb` is not implemented until Phase E.
Vite's scenario middleware serves development only: production must host/proxy assets.

Use Node 22.18+ or Node 24:

```sh
npm run typecheck
npm run lint -- --deny-warnings
npm test
npm run build
```

Repeat tests from repository root with `EMBERLINE_FIXTURE_DIR` and
`EMBERLINE_SCENARIO_ID` for a generated Rust fixture. `tools/check_dashboard_source.mts`
now checks Linda's loader over actual HTTP, including all quantiles and representative
leads. Shared wire contracts are unchanged; scientific skill/latency remain unestablished.

See [revised A–E plan](../docs/DASHBOARD_ENGINE_CONNECTION_PLAN.md),
[Phase B API](../docs/DASHBOARD_PHASE_B.md), and
[Phase C rendering/visual checks](../docs/DASHBOARD_PHASE_C.md).
D/E implementation requires separate direct confirmation for each phase.
