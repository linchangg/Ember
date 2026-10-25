# Emberline web

```bash
npm install
npm run dev
```

- `/` — historical-event globe with labeled simulated Eaton spread (`/?focus=eaton`)
- `/dashboard` — earlier Sierra Vista command dashboard mock
- `/compare` — globe stack bake-off (Cesium · MapLibre+Three · Hybrid)

Real elevation: Esri WorldElevation3D (Cesium) and AWS Terrarium DEM (MapLibre). Fire overlays are synthetic.

The dashboard is not yet a consumer of the Rust HazardModel's schema-v2 bundles.
The full Rust fixture and integration instructions are in
[`docs/RUST_HAZARD_BACKEND.md`](../docs/RUST_HAZARD_BACKEND.md).

Run `npm test`, `npm run lint -- --deny-warnings` and `npm run build` before merging
UI changes. Comparison map engines load only when their route is opened.
