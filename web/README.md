# Emberline web

```bash
npm install
npm run dev
```

- `/` — scenario-backed globe and replay clock (`/?focus=eaton`); press **Take the guided tour** for a hands-off walkthrough of the Eaton replay
- `/dashboard` — Sierra Vista mock by default; experimental Rust forecast map in explicit file mode
- `/compare` — globe stack bake-off (Cesium · MapLibre+Three · Hybrid)

Comparison routes use Esri WorldElevation3D (Cesium) and AWS Terrarium DEM (MapLibre).
Forecast source labels identify synthetic fixtures; the Phase C map uses a flat surface.

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
Phase D connects published zone risk, statistics, provenance, observation/activity history, local alert review and matched parent/child assimilation comparison. See [Phase D](../docs/DASHBOARD_PHASE_D.md) for its paired native fixture and commands. Phase E remains confirmation-gated.
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

Phase E explicit hub mode, runtime publication rules and local published-hub acceptance: [DASHBOARD_PHASE_E.md](../docs/DASHBOARD_PHASE_E.md). File mode remains available.

## Hosting on GitHub Pages

`.github/workflows/pages.yml` builds the site and deploys it on every push to `main` (or by hand from the Actions tab).
One-time setup: Settings → Pages → Source: **GitHub Actions**. The site lives at `https://<owner>.github.io/<repo>/`.

- `npm run build:pages` runs `vite build`, then `scripts/pages_assets.mjs` copies the scenario files into `dist/static/<id>/` and
  `dist/cache/<id>/main/` (the synthetic `fixture`, and the real `eaton` run from `data/scenarios/eaton/web` and `cache/eaton/main`).
- The workflow sets `VITE_BASE=/<repo>/`. The site opens the real `eaton` backtest by default; run the workflow by hand with `scenario: fixture`
  for the synthetic one, or add `?scn=fixture` to the URL.
- Try it locally: `VITE_BASE=/Legendary-Fire-Project/ npm run build:pages && VITE_BASE=/Legendary-Fire-Project/ npx vite preview`.
