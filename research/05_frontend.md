# 05 — Frontend research (map stack, rendering, layout, data contract)

Scope: light research for Linda's React/TS frontend. **The most important part is §7, the data contract the backend must produce.** Versions were checked on npm on 2026-10-24. API facts marked "verified" were read from the installed package types or from official example source.

## TL;DR
- **Stack:** **deck.gl 9.4 standalone** (`@deck.gl/react`, no basemap library). Use `TerrainLayer` for terrain, `_TerrainExtension` to drape the forecast, zones, cones and routes onto it, and plain 3D layers for satellites and beams. An optional `Tile3DLayer` adds Google Photorealistic 3D Tiles for a recorded fly-in only. Terrain and imagery for the AOI are cached locally, so the demo still works if Wi-Fi fails.
- **Fallback:** MapLibre GL JS via `react-map-gl/maplibre`. It has native `raster-dem` terrain, and an `image`/`canvas` source drapes the forecast natively. It consumes the same data contract. Last resort: a flat 2D deck.gl `MapView`.
- **Forecast encoding:** each run gets **one arrival-time quantile grid** (uint16 minutes at q = 0.1/0.3/0.5/0.7/0.9). The client colors it on the CPU every frame, so scrubbing is continuous without shaders. Per-step PNG frames and isochrone GeoJSON are secondary assets.
- **Clock:** the server publishes an anchor of the form `{anchor_sim_ms, anchor_wall_ms, speed, paused}` and clients extrapolate from it. The scrub position is client-only UI state.
- **Event log:** everything the UI shows can be rebuilt from an **append-only typed event log plus static assets**. Recording that log gives the offline replay for free.

## 1. Map stack comparison

| Option | Wow | Effort | Risk | Offline | Laptop perf | Notes |
|---|---|---|---|---|---|---|
| **deck.gl 9.4 standalone + TerrainLayer + _TerrainExtension** | 4 | Low–med | Low–med | **5** | 4 | One engine and declarative layers, which suits coding agents. The official examples `terrain-extension` (GeoJSON/Icon/Text draped on Terrarium terrain) and `google-3d-tiles` (GeoJSON draped on Google tiles via `operation:'terrain+draw'`) are near-templates (verified from 9.4-release source). The extension is still exported as `_TerrainExtension`, i.e. experimental (verified). |
| MapLibre GL JS 6.x / 5.24 (+ deck overlay) | 4 | Med | Med | 5 | 5 | Best basemap: vector labels and street names. v6.0.0 (2026-07-22) is ESM-only, requires WebGL2 and removed `map.transform`. `@deck.gl/mapbox` 9.4 still reads `map.transform`, which is a likely runtime break with v6 (code inspected, not run). Use `@deck.gl/maplibre` 9.4 instead (peer `maplibre-gl ^4.5.1‖^5‖^6`). Caveat from its docs: "deck.gl layers are not draped over MapLibre terrain", so draped things must be native MapLibre layers. That means two rendering models. |
| CesiumJS 1.146 / Resium 1.27 | 5 | High | High | 3 | 3 | Native `Clock`/`Timeline`, CZML and `SampledPositionProperty` orbits are ideal for satellites. But the default terrain/imagery need a Cesium ion token (an account). Terrarium terrain needs a custom provider, a time-varying raster drape is awkward (swapping imagery layers flickers), the bundle is heavy and needs Vite plugin setup. Too much for ~20 h. |
| Google Photorealistic 3D Tiles (as a layer) | 5 in neighborhoods | +2 h | Med–high | **1** | 3 (bandwidth-heavy) | Needs a billing-enabled Maps API key. Policy: "must not pre-fetch, index, store, or cache any Content"; "Offline uses" are prohibited. You must show the Google logo plus the aggregated tile attributions. **Altadena's tiles may now show post-fire destruction (unverified, check first)**, which would confuse a replay. Use it only for a pre-recorded fly-in segment of the Devpost video. |

**Recommendation:** deck.gl standalone, `MapView` with `TerrainController` and `maxPitch` 85 or more.
- **Terrain, simplest offline path:** TerrainLayer in **single-image mode** (`elevationData` URL plus `bounds`; verified in types). The backend exports the AOI DEM it already has (USGS 3DEP) as one Terrarium-encoded PNG in EPSG:4326 (about 2048² for 25 km). The imagery texture is one JPEG (about 4096²) cut from **USGS Imagery Only / NAIP**: 1 m, public domain, dated 2017–21, so it is pre-fire.
- **Night grade:** darken and desaturate the imagery offline so the fire glows.
- **Optional context:** tiled terrain from Mapterhorn (`tiles.mapterhorn.com/{z}/{x}/{y}.webp`, Terrarium, US 10 m) for areas outside the AOI. VersaTiles satellite is only Sentinel-2 (about 10 m) in the US, which is too blurry for neighborhoods.

## 2. Satellites

**Altitude cannot be to scale.** At a local zoom, an 830 km LEO or a 35,786 km GEO satellite falls outside the camera frustum. The honest stylization: true direction, compressed distance, labeled "altitude not to scale".
- **GOES-West = GOES-18 at 137.2°W** (operational since 2023-01-04). For a Jan 2025 fire, GOES-East was GOES-16; GOES-19 replaced it on 2025-04-07.
  - Compute look angles from the AOI with `satellite.js` `ecfToLookAngles` and place a fixed icon about 40 km out along that azimuth/elevation.
  - On each scan, draw a beam from the icon to the AOI.
  - CIMSS reports that the earliest LA-fire detections came from the **GOES-18 1-minute mesoscale sector**. The backend may therefore have 1-min scans for some periods, so the UI must not assume a 5-min cadence.
- **VIIRS orbiters:** S-NPP 37849, NOAA-20 43013, NOAA-21 54234 (NORAD IDs unverified).
  - Get TLEs from `celestrak.org/NORAD/elements/gp.php?CATNR=<id>&FORMAT=TLE` (unverified URL form) and bake them into a static JSON. satellite.js 7.1 is ESM-only, with `twoline2satrec`, `propagate(satrec, date)` returning a nullable value, `gstime`, `eciToGeodetic`, and a WASM bulk API (verified).
  - Draw the sub-satellite point on a "sky shell" about 25 km up, with a ground-track `PathLayer` for ±10 min.
- **Past dates:** a current TLE propagated about 21 months back is useless for timing, since error grows daily from 1–3 km at epoch. Space-Track `gp_history` needs an account. **Approximation:** the backend emits real scan times (VIIRS `acq_time` from FIRMS, GOES file start times). The frontend propagates the current TLE, finds the closest approach to the AOI, and **time-shifts the track so the pass coincides with the real scan time**. Label it "orbit illustrative; scan time real". The 16-day repeat and the roughly 01:30/13:30 local crossing keep the geometry plausible.
- **"Satellite just looked" pulse:** when the sim clock crosses `ScanEvent.t_start`, run a 1.5 s animation:
  - the beam brightens;
  - a sweep line crosses the AOI (N→S for GOES, along-track for VIIRS);
  - the pixel footprints of that scan flash, hot pixels in yellow and "not hot" pixels as faint outlines (negative evidence matters to the filter);
  - an expanding ring appears on the hottest pixel.

  Implementation: `ScatterplotLayer`/`PolygonLayer` driven by `(simNow - t_start)/dur` in `updateTriggers`. The slider also shows a tick for each scan.

## 3. Forecast rendering

- **Primary asset: an arrival-time quantile grid per run.** It is uint16 minutes since `issued_sim_ms`, shape [5, ny, nx], for q ∈ {0.1, 0.3, 0.5, 0.7, 0.9}, with 65535 meaning "not reached within the horizon".
  - Because `P(arrival ≤ t) ≥ q ⇔ T_q ≤ t`, the class for any t is the count of levels with `T_q ≤ t`. This is exact, continuous in t, about 2 ms of JS per frame for 300×300 cells, and needs no shader.
  - Linear interpolation between levels gives a smooth ramp if wanted.
  - Write the result into an `ImageData` and pass it to `BitmapLayer.image` (ImageData is accepted; verified), draped via `_TerrainExtension`.
- **Size:** 300×300×5×2 B = 900 KB raw, roughly 200–300 KB gzipped (unverified). Per-step PNG frames (15-min steps, about 25 × 20–60 KB) remain as a fallback, crossfading two `BitmapLayer`s by opacity.
- **Isochrones:** P50 arrival contours at +1…+6 h are white 1 px lines labeled "+2h". Optional dashed P10/P90 contours give the "possible / very likely" envelope. The backend computes them (skimage/rasterio) as GeoJSON, which keeps the frontend dumb.
- **Past vs future:**
  - For `viewTime ≤ simNow`, show the analysis perimeter from the latest run with `issued ≤ viewTime`, the burned area (charcoal), the active front (hot yellow line with a glow), and the observations up to `viewTime`.
  - For `viewTime > simNow`, show the latest run's P.
  - "Hindsight mode" overlays later detections on an older run's forecast. That is the visual for the honest lead-time number.
- **Color, discrete 5 bins on dark imagery:**

  | P | Hex | Alpha |
  |---|---|---|
  | ≥0.1 | `#FFE0B2` | .30 |
  | ≥0.3 | `#FFB74D` | .45 |
  | ≥0.5 | `#FF8A3D` | .60 |
  | ≥0.7 | `#F4511E` | .72 |
  | ≥0.9 | `#D50000` | .85 |

  Below 0.1 is transparent. The ramp is monotone in lightness, so it is CVD-safe; never pair it with green.
- **Legend wording:** "Chance fire reaches here by 23:40 PST". Show the bins with numeric labels, plus "Ensemble of N runs, reweighted to satellite data through 23:05; not calibrated against past fires."

## 4. Ops console layout (1920×1080 projector; must also work at 1440×900)
- **Top bar:** scenario and **REAL / SIMULATED** badges per layer, a big mono sim clock with "REPLAY 60×" or "LIVE", and source-health dots.
- **Center:** the 3D map, about 70% of the width. Layer toggles sit in a small floating dock.
- **Right panel (400 px), top to bottom:**
  1. **Recommendation card:** "Evacuate Zone 4 by 23:40", a countdown, P, ETA P50, the evacuation-time assumption (editable), and a **Review alert** button.
  2. **Ranked zones table:** rank, zone, P by act-by, ETA, act-by, status chip, P sparkline.
  3. **Fire stats:** area, growth per hour, front length, ESS.
  4. **Data sources:** last scan or ingest, labeled real or simulated.
- **Bottom time slider:** spans ignition → now → +6 h. A NOW marker; the future segment is hatched. Tick lanes for GOES, VIIRS, alerts and injected observations. Play/pause, speed (1×/30×/60×/300×) and **LIVE** (snap to now).
- **Activity feed (left drawer, 320 px):** grouped per cycle, e.g. "GOES-18 23:05 → 3 hot px → filter ESS 412/1000 → run 42 (2.1 s) → Zone 4 crossed 0.3 → alert drafted → awaiting approval". Each line links to the referenced row.
- **Alert approval (human in the loop):** a takeover card showing:
  - the triggering rule (fixed before the replay);
  - an SMS preview of 160 characters or fewer;
  - the voice script with a ▶ preview;
  - masked recipients;
  - **Approve & send** (hold for 1 s), **Edit** and **Dismiss**.

  After sending it shows delivery status and "Approved by … at …". The LLM text is labeled "draft".
- **Demo controls:** a separate `/control` route on a second device, plus hidden hotkeys. Controls: seek presets, speed, inject a ground report (click the map), simulate a camera bearing, drop a source (outage), reset.

## 5. Realtime and state
- **Adapter:** wrap the backend behind one adapter, `DataSource` (`subscribe(table, filter)`, `call(action, args)`), with three implementations: SpacetimeDB, Convex and **OfflineReplay**. OfflineReplay reads `events.ndjson` and assets from `/public/cache` and replays them against a local clock.
- **SpacetimeDB** (`spacetimedb@2.13.0`; React hooks verified in package types):
  - Build the connection with `DbConnection.builder().withUri(...).withDatabaseName('emberline').withToken(saved).onConnect(...)`, defined at module scope, and pass it to `<SpacetimeDBProvider connectionBuilder={…}>`.
  - `const [rows, ready] = useTable(tables.zone_risk.where(r => r.run_id.eq(id)))` and `const approve = useReducer(reducers.approveAlert)` (calls queue until connected).
  - Bindings come from `spacetime generate --lang typescript`.
  - Keep rasters out of rows; store URLs. Prune `activity_event` on the server to the last ~300 rows, since ORDER/LIMIT in subscriptions is unverified.
- **Convex** (`convex@1.48.0`): `useQuery(api.x.y, args)` returns `undefined` while loading, plus `useMutation`/`useAction`.
  - Documents are limited to 1 MiB and arrays to 8192 elements, so rasters and GeoJSON go to file storage (`storageId` → `getUrl`).
  - `useQuery(api.activity.recent, {limit:200})` with an index is natural.
- **Clock and scrub state** (zustand store, outside React render):
  - `simNow()` is computed per frame from the `ClockState` anchor (paused, or anchor + (wallNow − anchor_wall) × speed). The server only writes on play/pause/seek/speed. `epoch` increments on seek/reset so the client drops stale caches.
  - `viewMode: 'live' | 'scrub'`. In live mode, `viewTime = simNow()`. Dragging the slider enters scrub mode; LIVE exits it.
  - deck layers read `viewTime` every frame. Panels subscribe to a `viewMinute` selector (rounded) to avoid 60 fps React renders.
  - Prefetch the current run's quantile grid and the previous run's grid (`fetch → arrayBuffer → Uint16Array`, little-endian).

## 6. Mobile "victim" page
- **Join flow:** the QR code (`qrcode.react`) encodes `https://<public-host>/m?j=<joinCode>`. The page calls `joinAsPerson(joinCode)`. The server assigns a **demo spawn location** in an at-risk zone, because the judge is physically in SF; real GPS is used only in live mode.
  - Geolocation needs HTTPS.
  - **Cross-cutting constraint:** a phone on cellular cannot reach a laptop's localhost. The phone page and the backend must be publicly reachable over https/wss (Vercel plus SpacetimeDB Maincloud or Convex cloud, or a tunnel). The phone loop therefore needs internet, while the dashboard's offline mode does not.
- **UI:** MapLibre 2D with a light vector basemap (street names matter; e.g. OpenFreeMap, style URL unverified). It shows:
  - the danger polygon (P≥0.3 by +1 h from the isochrones; no raster on phones);
  - the route line;
  - "You are here (demo location)";
  - a big status with the act-by countdown;
  - a **Report fire here** button, which sends a ground report with `accuracy_m` as the radius;
  - an **SMS fallback** link: `sms:<num>?&body=EMBERLINE FIRE 34.1897,-118.1311 r50` (cross-platform `?&body=` form unverified). The backend parses the inbound message.
- **Routing: precompute, don't call public routers live.**
  - The OSRM demo server is best-effort ("Excessive use is not allowed") and has no polygon avoidance. GraphHopper needs a key. Valhalla supports `exclude_polygons` ("Roads intersecting these rings will be avoided"), but public server reliability is unknown.
  - **Plan:** offline, compute 2–3 alternatives from each of 8–12 spawn points to 2–3 assembly points and store them as GeoJSON.
  - On each run, the backend scores each route as the max over vertices of P(arrival ≤ now + travel time to that vertex). It picks the minimum-risk route and emits `Route{reason:'reroute'}` when the choice changes.
  - Live Valhalla with `exclude_polygons` is a stretch goal.

## 7. Data contract (frontend ⇄ backend)
Conventions:
- **Times:** integer epoch ms UTC with explicit suffixes `_sim_ms` / `_wall_ms`. Display uses the scenario timezone.
- **Coordinates:** WGS84 `[lon, lat]`.
- **IDs:** strings.
- **Geometry:** inline GeoJSON when ≤ ~100 KB, otherwise a URL.
- **Provenance:** every row carries `provenance: 'real_archive' | 'simulated' | 'injected_live'` (the honesty principle).
- **Raster corners:** given *named*, because deck `BitmapLayer` uses [BL, TL, TR, BR] (verified in source) while a MapLibre image source uses [TL, TR, BR, BL].

```ts
// Static, loaded once (~10 KB) — /cache/<scenario>/manifest.json
ScenarioManifest { scenario_id; title; tz: 'America/Los_Angeles';
  honesty: { real: string[]; simulated: string[] };
  aoi_bbox: [w,s,e,n]; initial_view: {longitude,latitude,zoom,pitch,bearing};
  t_ignition_sim_ms; replay_start_sim_ms; replay_end_sim_ms; horizon_min: 360;
  terrain: { elevation_url; encoding:'terrarium'; bounds:[w,s,e,n]; texture_url; attribution: string[] };
  grid: { nx; ny; cell_m; crs:'EPSG:32611'; corners:{tl,tr,br,bl}: [lon,lat] }; // row 0 = north
  static: { zones_url; assets_url; cameras_url; satellites_url; spawn_points_url; routes_url } }

ClockState { id:'main'; mode:'replay'|'live'; anchor_sim_ms; anchor_wall_ms; speed; paused; epoch; min_sim_ms; max_sim_ms } // ~150 B, on change only

ForecastRun { run_id; issued_sim_ms; obs_cutoff_sim_ms; created_wall_ms; status:'running'|'ready'|'failed';
  model:{name,version}; filter:{kind:'particle'|'enkf'; n_members; ess; resampled:boolean};
  horizon_min; step_min;
  assets:{ arrival_q_url /* uint16 LE [5,ny,nx], 65535=never */; q_levels:[.1,.3,.5,.7,.9];
           frames?: {t_offset_min:number; url:string}[]; isochrones_url; perimeter_url /* burned MultiPolygon + front LineString */ };
  stats:{ burned_area_ha; front_km; p50_area_ha_at_h:number[]; max_ros_m_min };
  n_obs_used; supersedes_run_id? } // ~1 KB row + ~0.3–1.5 MB assets; one per assimilation cycle

Zone (static GeoJSON, 20–60 features, 50–300 KB) { zone_id; name; geometry; population; households; buildings; facility_ids:string[]; evac_time_min_default }
ZoneRisk { run_id; zone_id; p_by_step:number[] /* aligned to step_min */; eta_p10_sim_ms|null; eta_p50_sim_ms|null; eta_p90_sim_ms|null;
  evac_time_min; act_by_sim_ms|null; act_by_basis:string /* e.g. "t(P≥0.3) − 45 min" */; p_at_act_by;
  exposure:{population; critical:number}; risk_score; rank; status:'clear'|'watch'|'warning'|'order'; trend:'up'|'flat'|'down' } // ~400 B × zones per run
Asset (static) { asset_id; type:'hospital'|'nursing_home'|'school'|'substation'|'water_plant'|'fire_station'|'chokepoint'; name; lon; lat; zone_id }

Recommendation { rec_id; run_id; zone_ids; action:'evac_warning'|'evac_order'|'shelter'|'preposition'; act_by_sim_ms;
  priority:'life_safety'|'stabilization'|'property'; rationale:{p; eta_p50_sim_ms; evac_time_min; population; facilities:string[]};
  briefing_text; briefing_model; status:'proposed'|'superseded'|'accepted'|'rejected'; created_sim_ms }
Alert { alert_id; rec_id; rule:{metric; threshold; fixed_before_replay:true}; triggered_sim_ms; channels:('sms'|'voice')[];
  recipients:{label; masked}[]; sms_text; voice_script; voice_audio_url?;
  status:'pending_approval'|'approved'|'sending'|'sent'|'failed'|'dismissed'|'expired';
  approved_by?; approved_wall_ms?; sent_wall_ms?; delivery?:{sms?:string; voice?:string} } // rare

Observation { obs_id; type:'goes_pixel'|'viirs_pixel'|'camera_bearing'|'ground_report'|'thermal_footprint'; sensor_id;
  t_obs_sim_ms; t_ingest_wall_ms; provenance; status:'pending'|'assimilated'|'rejected'; assimilated_run_id?; influence?:number;
  goes_pixel?/viirs_pixel?: { scan_id; footprint:[lon,lat][4]; hot:boolean; frp_mw?; confidence? };   // send all AOI pixels for GOES (~100/scan), hot-only for VIIRS
  camera_bearing?: { camera_id; bearing_deg; sigma_deg; confidence; frame_url?; clock_offset_s };
  ground_report?: { lon; lat; radius_m; trust; kind:'fire'|'smoke'|'embers'|'all_clear'; person_id?; channel:'app'|'sms'; note? };
  thermal_footprint?: { polygon; platform } } // ~150–300 B; 10–150 per scan
Satellite (static) { sat_id; name; norad_id; orbit:'geo'|'leo'; geo_lon_deg?; tle?:[string,string]; sensor:'ABI'|'VIIRS'; pixel_m }
ScanEvent { scan_id; sat_id; mode:'CONUS'|'MESO'|'FD'|'pass'; t_start_sim_ms; t_end_sim_ms; footprint_bbox; n_pixels_aoi; n_hot; source_file? } // drives pulse
Camera { camera_id; name; lon; lat; elev_m; heading_deg; hfov_deg; range_m; clock_offset_s; status:'online'|'offline'|'detecting'; provenance; last_frame_url? }
Triangulation { tri_id; camera_ids:[string,string]; lon; lat; cov_m:[number,number,number]; t_sim_ms } // client draws cones/rays from Camera + Observation
Person { person_id; kind:'resident'|'responder'|'judge'; label; lon; lat; accuracy_m; location_source:'assigned'|'gps'|'sms';
  zone_id?; risk:{p_by_60; eta_p50_sim_ms|null; status}; route_id?; alerted; acknowledged; last_seen_wall_ms } // ≤20 rows, ~5–10 s updates
Route { route_id; person_id; run_id; geometry:LineString /* ≤500 pts */; dest_name; distance_m; duration_s; risk_max_p; reason:'initial'|'reroute'; created_sim_ms }
ActivityEvent { event_id; seq; t_sim_ms; t_wall_ms; stage:'ingest'|'observation'|'filter'|'forecast'|'risk'|'recommendation'|'alert'|'briefing'|'human'|'agent'|'system';
  actor; level:'info'|'notice'|'warn'|'error'; message /* ≤140 chars */; ref?:{kind; id}; duration_ms?; detail? /* ≤2 KB */ } // 5–20 per cycle
SourceStatus { source_id; label; provenance; last_data_sim_ms; last_ingest_wall_ms; latency_s; status:'ok'|'stale'|'down'; note? } // ~10 rows
Metric { key:'lead_time_min'|...; label; value; definition; computed_sim_ms }
```

**Actions (reducers/mutations):**
- `setClock{mode,speed,paused,seek_sim_ms}`, `resetScenario`
- `injectObservation(obs)`, `reportFire{lon,lat,radius_m,kind}`
- `joinAsPerson(joinCode)→person_id`, `updatePersonLocation`
- `approveAlert(alert_id)`, `editAlert(alert_id, sms_text)`, `dismissAlert(alert_id, reason)`
- `setEvacTime(zone_id, min)`, `dropSource(source_id)`

**Update cadence at 60× replay:**
- With 5-min GOES scans, there is one ScanEvent, ~100 Observations, one ForecastRun, 20–60 ZoneRisk rows and ~10 ActivityEvents every **5 s wall**.
- With 1-min meso scans, the backend should coalesce to ≤1 run per ~5 s wall.
- Bandwidth: about 0.3–1.5 MB of assets per run. That is fine on localhost; on venue Wi-Fi, fetch lazily and drop superseded runs.

**Offline bundle:** the backend records an append-only `events.ndjson` (`{t_sim_ms, table, op:'upsert'|'delete', row}`) plus the run assets and manifest under `/cache/<scenario>/`. That is enough for the frontend to replay the whole demo with no network.

## 8. Visual direction
- **Dark ops console:**
  - Surfaces: bg `#0B0E13`, panel `#11161D`, border `#222B36`, text `#E6EDF3`, muted `#8B98A5` (about 7:1 on panel; check with a contrast tool).
  - **Cool cyan `#4CC9F0` is reserved for sensors and satellites** so it never competes with the warm fire ramp.
  - Pending/approval amber is `#FFB020`, sent/OK is `#3DDC97`, critical red is reserved for "order" status.
- **Typography:** Inter or IBM Plex Sans for UI. **JetBrains Mono or IBM Plex Mono with `tabular-nums`** for every clock, P value and act-by time. Act-by times use 28–32 px on the recommendation card.
- **Fire layers:** burned area in charcoal `#1A1210` at α .7. The active front is a 2-layer line: 8 px `#FF9F1C` at α .35 for glow, under 2 px `#FFE08A`. The forecast uses the 5-bin ramp in §3.
- **Accessibility:** never rely on color alone. Status chips carry text, zones are labeled "Z4 62% · act by 23:40", and people markers carry risk-ring icons. Respect `prefers-reduced-motion` (pulses become static ticks).
- **Honesty affordances:** REAL/SIM badges on every layer and source. Show "altitude not to scale" on satellites and "demo location" on phones. Show the forecast "issued at / obs through" stamp at all times.

## Sources
- npm registry (2026-10-24): deck.gl 9.4.0, @deck.gl/maplibre 9.4.0, maplibre-gl 6.15.0 (5.24.0 last v5), cesium 1.146.0, resium 1.27.0, satellite.js 7.1.0, spacetimedb 2.13.0, convex 1.48.0; package types inspected locally.
- [deck.gl MapLibre integration](https://deck.gl/docs/api-reference/maplibre/overview)
- [deck.gl google-3d-tiles example](https://deck.gl/examples/google-3d-tiles) and [source](https://raw.githubusercontent.com/visgl/deck.gl/9.4-release/examples/website/google-3d-tiles/app.jsx)
- [deck.gl terrain-extension example source](https://raw.githubusercontent.com/visgl/deck.gl/9.4-release/examples/website/terrain-extension/app.tsx)
- [MapLibre GL JS CHANGELOG](https://github.com/maplibre/maplibre-gl-js/blob/main/CHANGELOG.md)
- [Google Map Tiles API policies](https://developers.google.com/maps/documentation/tile/policies)
- [USGS Imagery Only item info](https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/info/iteminfo)
- [Mapterhorn](https://mapterhorn.com/)
- [VersaTiles sources](https://versatiles.org/sources/)
- [Valhalla route API](https://valhalla.github.io/valhalla/api/route/api-reference/)
- [OSRM API usage policy](https://github.com/Project-OSRM/osrm-backend/wiki/API%20Usage%20Policy)
- [SpacetimeDB TypeScript client](https://spacetimedb.com/docs/clients/typescript)
- [Convex limits](https://docs.convex.dev/production/state/limits)
- [satellite.js docs](https://shashwatak.github.io/satellite-js/)
- [GOES-19 (Wikipedia)](https://en.wikipedia.org/wiki/GOES-19)
- [NOAA OSPO: GOES-18 declared GOES-West](https://ospo.noaa.gov/data/messages/2022/12/MSG_20221205_1730.html)
- [CIMSS: NGFS detections of LA fires](https://cimss.ssec.wisc.edu/satellite-blog/archives/62428)
- [ExoAtlas: TLE accuracy](https://www.exoatlas.com/documentation/orbit-visualizer/accuracy-and-validation/)
