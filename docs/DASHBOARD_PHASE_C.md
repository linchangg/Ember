Subsequent update: the owner approved Phase D; see [DASHBOARD_PHASE_D.md](DASHBOARD_PHASE_D.md). The remainder records the original Phase C handoff.

# Dashboard connection — Phase C

Owner confirmed this phase with “yes.” Implementation is on `feat/dashboard-phase-c`,
based on Phase B `3cd12f2`, with updated main `2a62206` integrated before publication.
This branch does not change main.
Phase D needs a separate direct confirmation.

## Delivered behavior

`VITE_DATA_SOURCE=file` connects the existing `/dashboard` route to FileSource.
The default mock, homepage and comparison routes remain available. The connected view
uses the existing header/main/sidebar layout and an explicitly flat georeferenced map.
Its sidebar discloses that risk/recommendation/alert panels await Phase D, avoiding
invented numbers next to engine output. Scientific readiness remains experimental.

- `forecastRaster.ts` decodes quantile-major, north-first bytes. For cell i and
  quantile q the offset is `q*nx*ny+i`. Arrival is `issue+120000*b`; 255 never
  arrives, and all five zero bytes receive the modeled-burned style.
- At view t, `k=sum_q 1[b_q!=255 and b_q<=floor((t-issue)/120000)]` selects the
  lower-bound probability class [10,30,50,70,90]% for k=1..5. These classes do
  not estimate probabilities between the published quantiles. Each single-quantile
  view uses only its corresponding reached mask.
- `reprojection.ts` uses pinned `proj4@2.22.0` for the EPSG:32611 grid and EPSG:3857
  map. It derives the projected northwest outer corner, verifies the other corners
  within 1 m of the declared cell dimensions, and inverse-projects output pixel
  centers into source row/column indices. Outside cells remain transparent.
- Output is twice the source dimensions, capped at 2048 in each dimension. A
  conservative output-pixel diagonal must be smaller than one source cell or
  rendering fails visibly. Nearest sampling preserves categorical classes;
  MapLibre raster interpolation is also nearest. This avoids stretching the UTM
  grid through a four-corner geographic approximation.
- `raster.worker.ts` caches one reprojection lookup and transfers the completed
  image. Source/cache buffers are copied before transfer. Generation checks reject
  obsolete worker results. Textures update on two-minute bins, cycle or display
  changes, rather than every replay animation frame.
- `ForecastMap.tsx` renders the published burned/front GeoJSON and q50 isochrones
  through the selected lead. Fit forecast uses published vector extents only.
  Load failures hide forecast layers and display an error; pre-ignition exposes
  no forecast. Map/worker/source resources are disposed on route exit.
- Display, lead, replay issue, play/pause and latest forecast use the shared source.
  Issued and view times are distinct and formatted in the manifest time zone.

No frozen contracts, physics solver, likelihood defaults or alert delivery behavior
changed. This is a renderer of existing forecasts, not a new model or live hub client.

The latest main merge preserves Linda's scenario-backed homepage, replay clock,
street basemap, size-aware globe fitting, and refreshed 50-member analytic fixture.
Package scripts/dependencies combine her basemap command and SpacetimeDB dependency
with the Phase C tests, strict checking and proj4. Removed one unused `maskCv` canvas
declaration from the updated globe because it failed warning-free lint; no drawing
behavior used it. Main's fixture-spec update is inherited, not a new wire change.

## Reproduce and verify

Use Node 22.18+ or Node 24. Follow `web/README.md` for the two-server run commands;
follow Phase A for generating a new Rust fixture. From the repository root:

```sh
npm --prefix web ci --ignore-scripts
npm --prefix web run typecheck
npm --prefix web run lint -- --deny-warnings
npm --prefix web test
EMBERLINE_FIXTURE_DIR=/private/tmp/emberline-dashboard-phase-a \
  EMBERLINE_SCENARIO_ID=rust-fixture npm --prefix web test
VITE_DATA_SOURCE=file VITE_SCENARIO_ID=rust-fixture \
  VITE_ASSET_BASE=http://127.0.0.1:8791/ npm --prefix web run build
node tools/check_dashboard_source.mts http://127.0.0.1:8791/ rust-fixture
node tools/check_hazard_frontend.mjs /private/tmp/emberline-dashboard-phase-a
```

Local evidence (2026-10-24): strict typecheck and warning-free lint passed; **34/34
web tests passed on each fixture**; production file-mode build passed, including its
separate raster worker. Existing large map-library chunk warnings remain. The HTTP
source and independent decoder accepted all 36 forecasts and 3 pre-ignition bundles.
CI runs canonical web checks and now also runs the web suite on the generated native
fixture in both Linux/macOS jobs. These CI changes have not yet been observed remotely.

The eight new tests cover all 36 forecast entries × five quantiles × 30/120/360-minute
leads, independent probability-class counts, 0/255 and exact two-minute boundaries,
invalid data/horizon rejection, projection and orientation, and published-vector framing.
Independent pyproj/PROJ reference cell centers cover 120×120 at 255 m and 340×340
at 90 m; JavaScript transforms agree within the asserted 10 cm tolerance. Nearest
source-center error stays within `cell_m/sqrt(2)`. Northwest/southeast markers remain
correctly oriented. These are numerical placement checks, not scientific skill tests.

Regenerate the independent golden artifact only deliberately:

```sh
engine/.venv/bin/python tools/make_georeference_reference.py \
  --manifest /private/tmp/emberline-dashboard-phase-a/static/rust-fixture/manifest.json \
  --out web/src/forecast/georeference.reference.json
```

The generator refuses a different domain extent. The committed golden file includes
the manifest checksum and is not regenerated by routine tests.

## Visual verification still to close

Chrome showed the generated Rust probability map, published vectors, issue/view
labels, and a successful switch to q50 with its updated legend. The in-app browser
was unresponsive; native Chrome control subsequently timed out as well. No screenshot
artifact was recovered. Therefore final visual acceptance is **partial**, particularly
for the final fit/accessibility changes; do not treat this as a full browser-test pass.

Before merging, finish this checklist in the running preview:

1. At replay start, confirm “No forecast yet” and no forecast fill.
2. Select Latest forecast, then all five quantiles and +30/+120/+360; inspect
   coherent raster/vector positions, north-first orientation, and both time labels.
3. Use Fit forecast, pan/zoom and replay backward/forward; verify no stale layer is
   relabeled with a new issue. Play must return lead to Replay now.
4. Use a disposable fixture copy to remove one referenced binary. Seek away/back;
   confirm an explicit load error and no mock fallback. Restore it, retry and verify recovery.
5. Navigate to `/compare` during loading, return and reload; check for resource and
   console errors. Capture a clean viewport screenshot with issue/lead/legend visible.
6. Automate these DOM/map checks in browser CI before claiming the plan's complete
   browser acceptance. Current CI tests the decoder, source and projection, not WebGL.

## Next gated phase

Phase D would connect bundle zone risk, published recommendations/alerts, observations,
provenance, ensemble diagnostics and matched-issue branch assimilation review. It must
not launch notification delivery or fabricate absent values. Phase E separately adds
continuous runtime/hub updates. Calibration, held-out historical validation, target-size
latency and browser render latency acceptance remain open.
