# Emberline web

```bash
npm install
npm run dev
```

- `/` — historical-event globe with labeled simulated Eaton spread (`/?focus=eaton`)
- `/dashboard` — Sierra Vista mock by default; experimental Rust forecast map in explicit file mode
- `/compare` — globe stack bake-off (Cesium · MapLibre+Three · Hybrid)

Real elevation: Esri WorldElevation3D (Cesium) and AWS Terrarium DEM (MapLibre). Fire overlays are synthetic.

The dashboard consumes Rust HazardModel schema-v2 bundles in explicit file mode.
The full Rust fixture and integration instructions are in
[`docs/RUST_HAZARD_BACKEND.md`](../docs/RUST_HAZARD_BACKEND.md).

Run `npm test`, `npm run lint -- --deny-warnings` and `npm run build` before merging
UI changes. Comparison map engines load only when their route is opened.

Phase B provides the typed `src/data/` FileSource/store/hooks and validated assets.
Run `npm run typecheck` and `npm test` (Node 22.18+ or Node 24). Tests use the canonical
fixture by default; `EMBERLINE_FIXTURE_DIR` plus `EMBERLINE_SCENARIO_ID` select a
separately generated Rust fixture. See [Phase B details](../docs/DASHBOARD_PHASE_B.md).
Phase C connects `/dashboard` to the validated forecast raster and published vectors.
Risk, recommendation, observation and alert panels await Phase D confirmation.
The file-backed map is flat and georeferenced; it does not use the mock terrain.

From the repository root, with a generated Phase A fixture available:

```sh
engine/.venv/bin/python -m emberline.hazard.fixture_server \
  --root /private/tmp/emberline-dashboard-phase-a --port 8791 \
  --allow-origin http://127.0.0.1:5175
```

In a second terminal:

```sh
VITE_DATA_SOURCE=file VITE_SCENARIO_ID=rust-fixture \
VITE_ASSET_BASE=http://127.0.0.1:8791/ \
npm --prefix web run dev -- --host 127.0.0.1 --port 5175
```

Open `http://127.0.0.1:5175/dashboard`, then select **Latest forecast**.
The initial replay position is pre-ignition and correctly has no forecast.
Use Display for probability classes or q10/q30/q50/q70/q90, Lead for the future
view, and Replay issue time for causal backward/forward selection. Play returns
to the replay clock with lead zero. Fit forecast frames the published vectors.
Vite environment variables are selected at startup/build time; restart after changing
them. No environment variables retains the existing mock. Unsupported explicit modes
show a configuration error. See [Phase C evidence and remaining visual checks](../docs/DASHBOARD_PHASE_C.md).
