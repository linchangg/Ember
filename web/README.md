# Emberline web

```bash
npm install
npm run dev
```

- `/` — historical-event globe with labeled simulated Eaton spread (`/?focus=eaton`)
- Press **Take the guided tour** on the globe for a hands-off walkthrough of the Eaton replay.
- `/dashboard` — earlier Sierra Vista command dashboard mock
- `/compare` — globe stack bake-off (Cesium · MapLibre+Three · Hybrid)

Real elevation: Esri WorldElevation3D (Cesium) and AWS Terrarium DEM (MapLibre). Fire overlays are synthetic.

The dashboard is not yet a consumer of the Rust HazardModel's schema-v2 bundles.
The full Rust fixture and integration instructions are in
[`docs/RUST_HAZARD_BACKEND.md`](../docs/RUST_HAZARD_BACKEND.md).

Run `npm test`, `npm run lint -- --deny-warnings` and `npm run build` before merging
UI changes. Comparison map engines load only when their route is opened.

Phase B provides the typed `src/data/` FileSource/store/hooks and validated assets.
Run `npm run typecheck` and `npm test` (Node 22.18+ or Node 24). Tests use the canonical
fixture by default; `EMBERLINE_FIXTURE_DIR` plus `EMBERLINE_SCENARIO_ID` select a
separately generated Rust fixture. See [Phase B details](../docs/DASHBOARD_PHASE_B.md).
The environment selection factory is prepared, but existing map/panel mock values
are not connected yet; Phase C requires direct owner confirmation.

## Hosting on GitHub Pages

`.github/workflows/pages.yml` builds the site and deploys it on every push to `main` (or by hand from the Actions tab).
One-time setup: Settings → Pages → Source: **GitHub Actions**. The site lives at `https://<owner>.github.io/<repo>/`.

- `npm run build:pages` runs `vite build`, then `scripts/pages_assets.mjs` copies the scenario files into `dist/static/<id>/` and
  `dist/cache/<id>/main/` (the synthetic `fixture` always; `eaton` too once `data/scenarios/eaton/web` and `cache/eaton/main` are committed).
- The workflow sets `VITE_BASE=/<repo>/`. The default scenario is `fixture`; run the workflow by hand with `scenario: eaton` to open the real replay,
  or add `?scn=eaton` to the URL.
- Try it locally: `VITE_BASE=/Legendary-Fire-Project/ npm run build:pages && VITE_BASE=/Legendary-Fire-Project/ npx vite preview`.
