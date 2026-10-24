# 06: Frontend: dashboard, map, time slider, control page (owner: Linda)

**Goal.** An ops console projected for judges, in the look of Linda's mockup (`web/`, `design/command-dashboard-mockup/`).
- **Centre:** a rotatable 3D terrain map of Altadena with the fire, the forecast glowing ahead of it, and satellites "looking".
- **Bottom:** a time slider from ignition through now to +6 h.
- **Right:** the recommendation, ranked zones and the alert approval card.
- **Left drawer:** an activity feed showing every system step.

**Precedence:** where Linda's mockup differs from the layout here, **the mockup wins on look**. `00_contracts` wins on data.

**Reference:** `research/05_frontend.md`. It covers stack comparisons, verified deck.gl APIs and the satellite approach.

> **v1.1 changes after review:**
> - Fixture paths, scenario `fixture`
> - Row adapter
> - Lineage types
> - Duty-officer field moved to the dashboard
> - Post-hoc Evaluation card
> - Auto-pause indicator
> - Operator PIN
> - P2 cuts: LEO tracks, scan-pixel flash
> - Linda also owns `web/api/*` (`05` §2–4) and the 09:00 booth run (`SPEC.md` §6)

## 0. Stack and setup

```
# web/ already exists (Vite 8, React 19). Cody converts it to TypeScript strict first (docs/build_process.md C1).
npm i deck.gl@9.4.0 @deck.gl/react@9.4.0 @deck.gl/core@9.4.0 @deck.gl/layers@9.4.0 @deck.gl/geo-layers@9.4.0 @deck.gl/extensions@9.4.0 \
      zustand satellite.js@7.1.0 qrcode.react spacetimedb@2.13.0 maplibre-gl@5.24.0 react-map-gl
```

- **deck.gl standalone** (no basemap library):
  - `TerrainLayer` in **single-image mode**: `elevationData` = `manifest.terrain.elevation_url`, `texture` = `texture_url`, `bounds`, with the Terrarium decoder `{rScaler: 256, gScaler: 1, bScaler: 1/256, offset: -32768}`.
  - `_TerrainExtension` drapes 2D layers (forecast bitmap, zones, perimeter, routes) on the terrain.
  - Start from the official `terrain-extension` example in deck.gl 9.4.
- **Fallback if draping fights you for more than 90 min:** render flat. Use `MapView` pitch 0–45, with `TerrainLayer` shown but the overlays not draped. Then try MapLibre `raster-dem` terrain plus a native `image` source for the forecast. **Do not** use `@deck.gl/mapbox` with MapLibre v6.
- **MapLibre** is used only on the phone page (`07`).
- Routes: `/` dashboard, `/control` demo control (second device), `/p` phone (`07`), `/cam` (P2).

## 1. Build order

| By | Deliverable |
|---|---|
| 08:30 | App shell + `FileSource` on the fixture (`VITE_DATA_SOURCE=file`, `VITE_SCENARIO_ID=fixture`, Vite `publicDir: "../fixtures"`, `VITE_ASSET_BASE=/`): clock store, slider, a flat `BitmapLayer` forecast from `arrival_q.bin`, zones panel |
| 11:00 | `TerrainLayer` with a placeholder DEM; forecast draped; perimeter and front; activity feed |
| 13:00 | **M1:** `SpacetimeSource` live from the hub; alert approval card; real terrain and texture once Cody exports them |
| 16:00 | `web/api/report-tap`, `tts`, `cartesia/status`, `cartesia/ack` (`05`) |
| 18:00 | **M2:** GOES satellite + scan beam pulse; recommendation card with act-by countdown; sources panel; REAL/SIM badges |
| 22:00 | **M3:** branch before/after diff; Evaluation card; `/control`; phone page (`07`) + `web/api/report-voice` |
| 02:00 | Polish: legend, transitions, reduced-motion, projector check at 1920×1080 and 1440×900 |

## 2. Data layer (`web/src/data/`)

**Components never touch the bindings or `fetch` directly.** They use these hooks:

```ts
useManifest(): ScenarioManifest                         // static, loaded once
useClock(): ClockState ; useSimNow(): number             // simNow per animation frame (via store)
useView(): { viewTime: number; mode: 'live'|'scrub'; setScrub(t:number): void; goLive(): void }
useCycles(): Cycle[]                                     // lineage-filtered (04 §4), sorted by knowledge_sim_ms
useCycleAt(t: number): Cycle | undefined                 // latest with knowledge_sim_ms <= t
useArrivalGrid(cycle?: Cycle): Uint8Array | undefined    // fetch ASSET_BASE + assets.arrival_q -> Uint8Array, LRU cache 4
useZones(): ZoneFeature[] ; useZoneRisk(cycleId?: string): ZoneRisk[]
useAlerts(): Alert[] ; useBriefing(id?: string): Briefing | undefined
useObservations(sinceMs?: number): Observation[] ; useScanEvents(): ScanEvent[]
useSensors(): SensorInfo[] ; usePeople(): Person[] ; useReports(): Report[]
useEvents(limit = 80): ActivityEvent[] ; useMetrics(): Metric[] ; useBranches(): Branch[]
useCycleHead(branchId?: string): CycleHead | undefined
useActions(): { play(); pause(); seek(t); setSpeed(x); reset(); setPauseOnProposal(on);
                approve(id, text?); decline(id, reason); setEvacMin(zone, m);
                injectTapReport(lon, lat, kind) }   // online: POST /api/report-tap; offline: POST {ENGINE}/dev/report
```

- **Operator actions** pass the `operator_pin` (`04` §2.2). It is typed once into the dashboard and `/control` and kept in localStorage.
- The operator name (default "Operator") is typed once and passed to `approve`/`decline`.
- **`SpacetimeSource` must run every row through the adapter** (`04` §4): camelCase → snake_case, `undefined` → `null`, `*_json` → parsed. **Lineage** follows `00` §3.3, including its time-column table.

**Implementations** behind one `DataSource` interface:

| Implementation | Source |
|---|---|
| `SpacetimeSource` | The hub (`04` §4) |
| `FileSource` | Reads `{ASSET_BASE}static/{scn}/manifest.json` and `{ASSET_BASE}cache/{scn}/main/index.json`, then each listed `bundle` (`00` §4). It runs a **local clock**: paused at `replay_start`, speed 240, min/max from the manifest, same anchor math. It exposes bundles with `knowledge_sim_ms ≤ simNow` and applies the hub's rules locally: insert-or-ignore, never downgrade `approved`, only `proposed → superseded`, and pause on new proposals. Actions mutate local state only. It is the fixture source from hour 0 **and** the last-resort offline demo. |
| `ConvexSource` | Only if the hub falls back |

`VITE_DATA_SOURCE=stdb|file`, `VITE_ASSET_BASE=http://localhost:8787/`.

## 3. Time model (zustand store, outside React render)

- `simNow()` is computed every frame from the `ClockState` anchor. `epoch` changes clear caches.
- `mode: 'live' | 'scrub'`:
  - **live:** `viewTime = simNow()`.
  - **Dragging the slider:** enter `scrub` and set `viewTime`.
  - **LIVE button:** back to live.
  - Scrubbing never changes the shared clock; only `/control` or the dashboard's play/seek buttons do.
- **Which cycle:**
  - Past (`viewTime ≤ simNow`): `cycle = useCycleAt(viewTime)`, which shows what the system knew then.
  - Future (`viewTime > simNow`): the latest cycle, so the forecast is shown out to +6 h.
- **Re-render discipline:**
  - deck layers read `viewTime` via `updateTriggers` every frame.
  - Panels subscribe to `viewMinute = floor(viewTime/60000)`, which avoids 60 fps React renders.
- **Prefetch** the arrival grid for the current and the next cycle.

## 4. Map layers (bottom → top)

| Layer | deck.gl | Data | Notes |
|---|---|---|---|
| Terrain | `TerrainLayer` | manifest terrain PNG + night-graded texture | `TerrainController`, `maxPitch` 85; initial view from the manifest |
| Forecast probability | `BitmapLayer` + `_TerrainExtension` | `arrival_q.bin` decoded per frame (below) | Bounds = 4 named corners → `[bl, tl, tr, br]` |
| Burned area | `GeoJsonLayer` (draped) | `perimeter.geojson#burned` | Charcoal `#1A1210` α .7 |
| Active front | `PathLayer` ×2 | `perimeter.geojson#front` | Glow 8 px `#FF9F1C` α .35 under 2 px `#FFE08A` |
| Isochrones | `PathLayer` | `isochrones.geojson` | White 1 px, labels "+2h" (`TextLayer`) |
| Zones | `GeoJsonLayer` (draped) | `zones.geojson` + `ZoneRisk` | Outline colour by status; fill only for `order`/`warning` α .12; label "Z4 62% · act by 23:40" |
| Lake Ave | `PathLayer` | manifest `lake_ave` | Dashed cyan, label "Lake Ave". This is the story line. |
| Assets | `IconLayer` | `assets.geojson` | Hospital / nursing home / school glyphs; tier-1 badge |
| Observations | `PolygonLayer` / `ScatterplotLayer` | hot satellite pixels (footprints), ground reports (circle radius), camera rays (P2) | Fade by age (60 sim-min); provenance badge colours |
| Scan pulse | `LineLayer` + `ScatterplotLayer` | `ScanEvent` + hot `Observation` footprints | Beam + sweep + flash of **hot** footprints when `simNow` crosses `t_start`. The clear-pixel outline flash from `static/{scn}/scans/` is P2. |
| Satellites | `ScatterplotLayer` + `LineLayer` + `TextLayer` | `satellites.json` + scan events | §4.2 |
| People | `ScatterplotLayer` + rings | `Person` | Risk ring colour; label "demo location" |
| Branch diff | `PathLayer` dashed | Parent cycle's q50 +2h isochrone vs current | Only when the active branch ≠ `main`, for 30 s after the fork |

### 4.1 Forecast decode (the core visual)

```ts
// grid: Uint8Array length 5*ny*nx; issue = cycle.knowledge_sim_ms
const t = (viewTime - issue) / 120000;            // view time in 2-minute units after issue
for (let i = 0; i < ny*nx; i++) {
  let k = 0;
  for (let q = 0; q < 5; q++) { const v = grid[q*ny*nx + i]; if (v !== 255 && v <= t) k++; }
  rgba.set(PALETTE[k], i*4);                      // PALETTE[0] = transparent
}
// write into an ImageData; pass to BitmapLayer.image (ImageData accepted); only when viewMinute or cycle changes
```

**Colour bins** (discrete; monotone in lightness, so colour-vision safe; never pair with green):

| class | P | hex | α |
|---|---|---|---|
| 1 | ≥ 0.1 | `#FFE0B2` | .30 |
| 2 | ≥ 0.3 | `#FFB74D` | .45 |
| 3 | ≥ 0.5 | `#FF8A3D` | .60 |
| 4 | ≥ 0.7 | `#F4511E` | .72 |
| 5 | ≥ 0.9 | `#D50000` | .85 |

**Legend:** "Chance fire reaches here by {viewTime PST}". Footer: "Ensemble of {n_members}, re-weighted to satellite data through {knowledge PST} · filter {filter_mode} · ESS {ess}".

- For past view times, cells with class 5 at `knowledge_sim_ms` are drawn as burned (they already are).
- Urban (NB91) cells: add a diagonal hatch overlay labeled "urban spread: heuristic". Cody provides a `static/urban_mask.png` P1 asset; skip it if absent.

### 4.2 Satellites (honest stylization)

- **GOES-18** (137.2°W) and **GOES-16** (75.2°W):
  - Compute the azimuth/elevation from the AOI centre with `satellite.js` (`ecfToLookAngles`). Place an icon ~40 km out along that look direction, at a compressed altitude.
  - On each `ScanEvent` (`t_start_sim_ms` crossed): the beam brightens, a sweep line crosses the AOI N→S, the scan's pixel footprints flash (hot yellow, clear faint outline), and a ring expands on the hottest pixel.
  - With 1-min mesoscale scans the pulses come fast. Throttle to at most 1 per 400 ms wall.
- **VIIRS** (P2: until then, show VIIRS passes only as slider ticks and a "VIIRS pass" toast) (S-NPP, NOAA-20, NOAA-21):
  - Propagate the current TLE, find the closest pass to the AOI, and **time-shift the track** so it coincides with the real scan time from the scan event.
  - Draw the sub-satellite point on a sky shell ~25 km up with a ±10 min ground track.
  - Label: "orbit illustrative · scan time real · altitude not to scale".

## 5. Panels

### 5.1 Top bar
- Scenario title.
- **REPLAY 240×** / **LIVE** badge; big mono sim clock (PST); "data through HH:MM".
- Source-health dots per sensor (ok/stale/down).
- `config_hash` (short) with tooltip "thresholds frozen before replay".

### 5.2 Right panel (400 px), top → bottom

**1. Recommendation card**
- The top-ranked zone with status `order` or `warning`: "Recommend evacuation order: Farnsworth".
- **Act by 23:40** (28–32 px mono) with a countdown in sim time.
- P by +1 h / +2 h; ETA q50; the evacuation-time assumption, editable inline via `setEvacMin`.
- Button **Review alert** opens §5.4.

**2. Ranked zones table**
- Columns: rank, zone, status chip (with text), P +2h, ETA, act-by, trend arrow, P sparkline (from `zone_risk` history).
- Top 8, with an expander.
- Click → fly to the zone and highlight it.

**3. Fire stats:** burned ha, growth ha/h, front km, ESS, filter mode, model run time.

**4. Data sources:** each sensor with last data time, latency and a REAL/SIM badge. The voice/call channel status shows here too.

### 5.3 Activity feed (left drawer, 320 px, collapsible)
- `useEvents(80)`, grouped per cycle.
- Icons by `stage`; level colours.
- Click an event with `ref_kind/ref_id` to focus it on the map or in a panel.
- This is our "show the agent's work" surface: Microsoft, Anthropic and SpacetimeDB judges look here.

### 5.4 Alert approval card (modal takeover; human in the loop)
- **Content:**
  - level, zones, triggering rule text (from the frozen config, with the hash)
  - `text` preview (≤ 300 characters) with a `draft_source` tag: "Claude draft (validated)" or "template"
  - voice script with ▶ preview (browser `speechSynthesis`)
  - recipient "Duty officer •••• 4821"
- **Buttons:**
  - **Approve & call**: hold for 1 s; calls `approve(id, editedText?)`
  - **Edit**
  - **Decline** (with reason)
- **Duty officer line:** "Calling: teammate phone •••• 4821 (default)" with a **Change** button. The button opens a field for a judge's number, which is POSTed to `{ENGINE}/dev/duty-officer`. The number is held in engine memory only and the reply is masked. The card is on the laptop, so this request is same-machine.
- **After approving:** live state chips: `approved → calling → called → acknowledged (by phone, 23:14 replay time)`. Use `acked_sim_ms`, never wall time. Show "call channel unavailable" if `failed`, with a **Play briefing here** fallback.
- **Auto-pause:** when the hub pauses for a new proposal, the top bar shows "⏸ Paused for operator review". The operator resumes with Play; the script resumes at 60×.
- Badge on the top bar while any alert is `proposed`.

### 5.5 Time slider (bottom, full width)
- Spans `replay_start → simNow → simNow + 6 h`. The future part is hatched.
- Tick lanes: GOES scans (thin cyan), VIIRS passes (thicker), alerts (amber), reports/injections (white), documented timeline events (grey diamonds from `timeline.json`, with tooltips).
- **The 03:25 orders marker is shown with the caption "official west-Altadena orders (context)".**
- Controls: play/pause, speed (60/240/600×), LIVE. Seek presets: 21:00, 23:00, 01:00, 03:00.

### 5.6 Evaluation card (post-hoc; toggled from `/control`; never gated on replay time)

These metrics have `computed_sim_ms = null` and are computed after the full replay (`03` §9). The card is titled **"Evaluation (computed after the full replay)"**:

```
Area X: west-Altadena foothills (outline on map)
  Forecast first gave ≥50% for fire by the time it was actually seen: {t_fc}
  Satellite first saw fire there ({GOES-18 | VIIRS}): {t_sat}
  Lead time: {lead} min   · without assimilation: {lead_openloop} min
  ETA range at {t_fc}: {q10}–{q90} ({inside | outside} the actual time)
  Context: west-Altadena evacuation orders 03:25. Retrospective replay — officials did not have this tool; not an outcomes claim.
  Config {hash} frozen before the reported run.
```

Render real `Metric` values, **including negative or missing ones** (show "—" with the definition).

When `active_branch_id == "demo_b1"`, show a persistent **"Cached rehearsal branch"** chip.

## 6. `/control` page (second device: phone or tablet, used by a teammate during the pitch)

- **PIN:** asked once and stored in localStorage.
- **Clock:** play/pause, speed (60/240/600×), seek presets, **auto-pause on proposals** toggle, **Reset (hard)** with a confirm.
- **Inject report** (fallback for Moment 2 if no judge phone): tap the map → kind buttons → `injectTapReport` via `/api/report-tap`.
- **Evaluation card** show/hide (§5.6).
- **Status:** active branch (with the "Cached rehearsal branch" chip), clock status (`computing` spinner), last 10 events.
- **QR code** for `/p?j=demo` (`qrcode.react`), shown big so a judge can scan it.
- The duty-officer number is entered **on the dashboard**, not here (§5.4).

## 7. Visual tokens

Colours, type and spacing come from Linda's mockup: the tokens in `web/src/App.css` (from `design/command-dashboard-mockup/`). Don't add or change tokens without Linda. Any extra semantic colours the panels need (sensors, REAL/SIM/INJECTED, pending/ok) are her call.

- Never rely on colour alone: chips carry text, zones carry labels.
- Respect `prefers-reduced-motion`: pulses become ticks.
- Contrast ≥ 4.5:1 on panels.
- **Honesty affordances visible at all times:**
  - REAL / SIM / INJECTED badges per layer and source
  - "Retrospective replay" in the top bar
  - "issued at / data through" stamp on the forecast
  - "heuristic" hatch on urban spread

## 8. Performance and offline

- Target 60 fps panning, ≥ 30 fps while playing at 240× on the demo laptop.
- Decode the forecast only when `viewMinute` or the cycle changes.
- Cap observations rendered to the last 2 sim-hours (older ones fade out).
- Activity feed virtualized.
- **Offline:**
  - `ASSET_BASE` points to the local engine.
  - Terrain, texture, fonts and icons are all local; no CDN at runtime. Bundle fonts.
  - Verify with Wi-Fi off at M5.

## 9. Acceptance tests (vitest + a manual checklist)

1. **Decode:** for a synthetic grid with known quantile minutes, `classAt(t)` matches hand-computed classes at 6 view times.
2. **Corners:** forecast bitmap aligns with the zones layer within 1 cell visually. Check at Lake Ave.
3. `FileSource` replays the fixture at 600× with no console errors. Scrubbing back shows the older cycle's forecast.
4. `SpacetimeSource`: approving in one browser updates a second browser within 1 s.
5. Branch diff appears after an injected report and clears on reset.
6. **Projector check:** readable from 3 m at 1920×1080. The act-by time is the largest number on screen.
