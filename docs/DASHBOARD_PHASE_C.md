# Phase C — forecast layers on Linda's globe (revised)

The separate white MapLibre `FileDashboard` and worker renderer have been removed.
The connected incident view is Linda's homepage globe (`/`), opened with **Open scenario**
or an Eaton event. `/dashboard` retains the earlier mock. This revision supersedes the
previous Phase C route/renderer instructions and its screenshot checklist.

## Implementation

- `forecast/forecastRaster.ts` remains the pure decoder and mask reference.
  `offset=q*nx*ny+row*nx+col`; row0=north, quantiles=[.1,.3,.5,.7,.9].
- Byte b=255 is never reached; otherwise arrival=`issue+120000*b`. At view t,
  `k=sum_q 1[b_q!=255 and b_q<=floor((t-issue)/120000)]`. k=1..5 are lower-bound
  chance classes ≥10/30/50/70/90%, not exact intermediate probabilities. All-five-zero
  cells receive modeled-burned styling. Single-quantile modes use only their own mask.
- `reprojection.ts::projectGrid` transforms the UTM origin plus exact metre offsets
  to Float64 WGS84 cell centers and a shared outer-vertex grid. It replaces Linda's
  bilinear geographic interpolation. Rounded manifest corners are checked against
  projected dimensions within 1 m. Independent PROJ centers agree within 10 cm.
- Linda's geographic globe projection transforms each cell's four actual corners.
  Terrain height sampling uses the existing terrain projection. Cells crossing or
  behind the limb are omitted; no screen-aligned forecast image is pasted over the globe.
- Forecast class fill uses exact cell polygons, replacing blurred probability sprites
  that could expand class support. Linda's published burned/front glow, terrain contours,
  basemap, interaction and framing remain. Terrain/glow are presentation effects;
  no scientific boundary accuracy or terrain draping fidelity is claimed from a screenshot.
- `ScenarioFiles.raster` memoizes one mask by entry, two-minute bin and selected mode.
  The canvas still reprojects visible cells each animation frame as the globe moves.
  The old worker is no longer active; target-size frame-time acceptance remains open.
- Published burned/front geometries are consumed unchanged. Only published q50
  isochrones through the selected lead are shown. No decorative forecast ellipses are generated.
- The existing time strip adds five quantile modes and +30/+120/+360 buttons.
  Its slider can view through the current forecast horizon; it does not advance
  the replay clock. Follow clock returns to now. Restart reveals valid pre-ignition state.
- Issued/view/data-through times stay distinct. Failures, loading and no forecast are
  distinct; the old `lastEntry` fallback is removed. Unknown zone risk is neutral rather
  than a fabricated clear status. Null act-by values render unavailable, not Jan 1970.
- **Open scenario** derives its location/title from the manifest, allowing the fixture
  replay even when NASA EONET is unavailable. Synthetic/experimental labels stay visible.

## Verification

See `web/README.md` and Phase A for run commands; use `/`, not `/dashboard`.
Existing decoder tests exhaust all 36 forecasts × five quantiles × 30/120/360-minute
leads, zero/255 boundaries, invalid encodings, golden coordinates and NW/SE markers.
New tests exercise the active Linda loader and exact cell vertex/center grid. Run the
suite against both the checked-in 50-member analytic and generated 16-member Rust fixture.

The standalone Mercator helper/tests remain reusable numerical reference code; they
are not the active globe renderer and no worker performance claim carries forward.
All new files are typechecked; the existing large JavaScript globe retains lint/build
checks. Full conversion to strict TypeScript remains Cody/Linda's separate work.

Browser acceptance still requires: open scenario; restart before ignition; replay
back/forward; all quantiles and three leads; explicit missing asset and Retry; navigate
away while loading; verify no console/lifecycle errors; save screenshot with issue/view/
legend visible; compare known NW/SE positions under globe rotation and limb clipping.
The browser controller has been unavailable in this session, so automated software
checks are not a substitute for final visual acceptance. Do not claim browser CI passed.

No Phase D panel/report/alert command implementation or Phase E live transport was
started. Those plans now extend Linda's implementation; see the revised connection plan.
