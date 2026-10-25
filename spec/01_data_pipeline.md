# 01: Data pipeline (owner: Cody; statics §6 reassigned from Abby to Cody, Sat Oct 24 evening)

> **v1.1 changes after review:**
> - one shared `area_x_satellite_hit()` function for G1-a and metrics
> - HRRR fxx 0–9
> - P2 items: GOES-16, 1-min meso and the scan flash
> - VIIRS trust mapping
> - tier rule
> - pre-fire texture check
> - spawn-point format
> - Abby owns the statics

**Goal.** One command builds a self-contained, offline scenario package. Every downstream component (physics, engine, frontend) reads only this package. Nothing in the demo touches NOAA, NASA or USGS at runtime.

```
python -m ember.data.build_scenario --scenario eaton [--only grid,statics,goes,viirs,hrrr,vectors,terrain,truth,manifest]
```

Each step is idempotent: it skips work if its output exists, and `--force` overrides that. Raw downloads go to `data/scenarios/eaton/raw/`, which is git-ignored.

**Reference:** `research/01_data_sources.md`. It has the URL templates, the mask/DQF tables, the latency rules and the pitfalls. Every snippet there is unverified, so test on a laptop.

## 0. Output layout

```
data/scenarios/eaton/
  scenario.yaml                         # 00_contracts §2 (committed)
  grid.json                             # transform, crs, nx, ny, corners lon/lat (committed)
  static/
    dem.tif slope.tif aspect.tif        # float32, 90 m, model grid (COG)
    fbfm40.tif                          # int16 LANDFIRE LF2024 FBFM40
    cc.tif ch.tif                       # canopy cover 0-1, canopy height m (optional)
    building_frac.tif                   # P2
    zone_index.tif                      # int16 zone index (-1 none), model grid
    area_x_index.tif                    # int8 (1 = in area X)
  forcing/
    hrrr_{YYYYMMDDHH}.nc                # per init: u10,v10 [fxx,y,x] earth-relative on model grid; temp_c, rh [fxx]; attrs init_ms, available_ms
  obs/
    goes18_fdcc.parquet goes18_fdcm.parquet goes16_fdcc.parquet viirs.parquet   # pixel tables (§3, §4)
  replay_manifest.parquet               # every observation batch + forcing file with available_sim_ms (§8)
  vectors/
    zones.geojson assets.geojson area_x.geojson lake_ave.geojson
  web/                                  # served as static/eaton/* (00 §4); copied to web/public/static/eaton/ for Vercel
    manifest.json terrain_elev.png terrain_tex.jpg zones.geojson assets.geojson
    timeline.json satellites.json sensors.json area_x.geojson spawn_points.json [scans/{scan_id}.json  (P2)]
  truth/
    timeline.json feds_perimeters.geojson (P1) dins_zone_summary.json (P2)
```

## 1. Grid (`data/grid.py`)

- **Projection:** `EPSG:32611`, `cell_m = 90`, `nx = ny = 340`, upper-left `(381480, 3800280)`. The transform is `Affine(90, 0, 381480, 0, -90, 3800280)`.
- **Cell centres:** `x = x0 + 45 + 90·col`, `y = y0 − 45 − 90·row`.
- **Corners:** compute the lon/lat of the four outer corners with pyproj and write them to `grid.json` and to the web manifest.
- **Expected coverage:** about lon −118.288 to −117.957, lat 34.061 to 34.337. Assert that the ignition point (−118.088, 34.205) and Lake Ave (−118.1316) both fall inside.
- **Shared template:** `template()` returns an `xarray.DataArray` with this grid and CRS. Every raster is aligned with `rio.reproject_match(template)`.
- **Resampling per layer:**

  | Layer | Resampling |
  |---|---|
  | Fuels | mode (or nearest) |
  | DEM | average |
  | Canopy | average |
  | Winds | bilinear |

- **Palisades backup:** same function, different bbox. Snap the origin to 90 m in the UTM zone of the centroid (11N).

## 2. G1-a go/no-go check (FIRST, by Sat 09:30): `scripts/check_goes.py`

Run this before building anything else. It decides Eaton vs Palisades.

1. List `s3://noaa-goes18/ABI-L2-FDCC/2025/008/{02..15}/` (anonymous `s3fs`). Also list `ABI-L2-FDCM` for the same hours and find which mesoscale sector (M1/M2) covers LA. Check each file's `geospatial_lat_lon_extent`.
2. For each file:
   - crop to bbox + 5 km
   - convert the fixed grid to lon/lat (pyproj `geos`, see research 01 §2)
   - record pixels with `Mask ∈ {10,11,12,13,14,30,31,32,33,34}` and `DQF`
3. **Parallax-correct each pixel centre.** Shift it toward the sub-satellite point by `h · tan(VZA)`, where `h` is the DEM elevation at the apparent position. For GOES-18 at Altadena that is about 0.99 km per 1000 m. Iterate twice.
4. Produce `check_goes_report.html`, a matplotlib grid of frames every 15 min plus a summary table, containing:
   - (a) first detection time after 02:15Z
   - (b) **`t_sat_goes` computed with `ember.data.area_x.area_x_satellite_hit()`**. This is the *same* function `risk/metrics.py` uses (`03` §9). A scan is a hit if a corrected `GOES_STRONG` pixel has ≥ 50% of its area inside area X and lies wholly west of Lake Ave; `t_sat_goes` needs 2 of 3 consecutive scans to be hits.
   - (c) persistence and bleed behaviour
   - (d) whether FDCM shows the run more clearly (informational only; FDCM ingest is P2)
5. **Area X.** Before running (b), check the area-X box from `scenario.yaml` on a map against the foothills west of Lake Ave. Adjust it **now if needed**; it is frozen with the config later (`03` §5).
6. **Decision rule.**
   - If (b) gives a time before 11:25Z (03:25 PST): **Eaton is GO**.
   - Otherwise, check VIIRS (§4) for an n/h detection inside area X before 11:25Z. If one exists, Eaton is GO with VIIRS as the honest-number sensor. If not, **switch to Palisades** (`research/06` §3) and define its area X the same way.

Time box: 75 min. If S3 listing is slow, use `goes2go`, which can list and download FDC files.

## 3. GOES ingest (`data/goes.py`): primary sensor

**Products:**
1. **GOES-18 `ABI-L2-FDCC`, every 5 min.** This is the only assimilated GOES stream (`assimilate_sensors`), giving exactly one scan per 5-min window.
2. *(P2, display only)* GOES-18 `ABI-L2-FDCM` 1-min mesoscale scans: `ScanEvent.assimilated = false`, for the flash only. They must never be double-counted in the filter.
3. *(P2)* GOES-16 `ABI-L2-FDCC`: oblique, ~3.4 km pixels. Off unless added to `assimilate_sensors` before the freeze.

**Pixel table** (`obs/goes18_fdcc.parquet`). One row per pixel per scan, for every pixel whose footprint intersects the AOI:

| column | type | notes |
|---|---|---|
| scan_id | str | `g18c_{scan_start:%Y%m%dT%H%M%S}` |
| sensor_id | str | `goes18_fdcc`, `goes18_fdcm`, `goes16_fdcc` |
| scan_start_ms, scan_end_ms | int64 | parsed from filename `s`, `e` |
| created_ms | int64 | filename `c` |
| s3_last_modified_ms | int64 or null | |
| available_ms | int64 | §8 rule |
| px_x, px_y | int32 | fixed-grid indices (stable across scans → physics can precompute pixel→cell weights) |
| lon, lat | float64 | **parallax-corrected** centre |
| ring | list[[lon,lat]] | corrected footprint, 4 corners at ±28 µrad scan angle |
| mask | int16 | FDC Mask code |
| dqf | int8 | |
| hot | bool | mask ∈ fire classes |
| clear | bool | `dqf == 1` (good fire-free land). Only these are negative evidence |
| frp_mw | float32 or null | `Power` |
| trust | float32 | class reliability: 10/11/30/31 → 0.95; 12/13/32/33 → 0.85; 14/34 → 0.6; 15/35 → 0.3 (physics may override) |

**Rules**
- Pixels with DQF 2–5 are **dropped entirely**. They are no information, never "not hot".
- Mask 15/35 (low probability) is stored with `hot=True` and trust 0.3. Physics decides whether to use it.
- Volume: about 13×11 GOES-18 pixels over the AOI × 156 scans = ~22k rows. Tiny.
- *(P2)* Write `web/scans/{scan_id}.json` with `{pixels: [{ring, hot, cls: mask}]}` for the frontend flash. Otherwise set `scans_dir: null` in the manifest.
- Emit one `ScanEvent` per scan (`00_contracts` §3.1) into `obs/scan_events.parquet`.

## 4. VIIRS ingest (`data/viirs.py`)

- **Request:** FIRMS area API, `…/api/area/csv/{MAP_KEY}/{SOURCE}/{W,S,E,N}/{days}/{date}`.
  - Sources: `VIIRS_SNPP_SP|NRT`, `VIIRS_NOAA20_SP|NRT`, `VIIRS_NOAA21_SP|NRT`.
  - Dates: 2025-01-07 and 2025-01-08 (UTC).
  - Try `_SP` first; fall back to `_NRT`.
- **Observation time:** `t_obs = acq_date + acq_time` (UTC; zero-pad `acq_time` to 4 digits).
- **Footprint:** a rectangle of `scan × track` km centred on the point. Orient it along-track (approximately N–S for these orbits).
- **Fields:** the FIRMS `confidence` label maps to:
  - **`trust`** (used by physics): l → 0.5, n → 0.8, h → 0.95
  - **`confidence`** (display only): l → 0.3, n → 0.6, h → 0.9
- **Detections only.** Swath non-detections (negative evidence) are cut (P2).
- **Latency profile:** `config.viirs_latency = "urt"`, giving `available = t_obs + 2 min`. This assumes a live system subscribed to FIRMS URT for CONUS (~1 min). The UI states the assumption. `"nrt"` = `t_obs + 3 h` is the conservative alternative; compute the honest number under both (`03_engine` §9).
- **Expected passes:** first VIIRS detection of Eaton around 01:30 PST (09:30Z). Expect 2–3 night passes 08:30–11:30Z (unverified).

## 5. HRRR forcing (`data/hrrr.py`)

- **Cycles:** inits 2025-01-07 18Z through 2025-01-08 15Z, `sfc` product, **`fxx` 0–9**. With ~1–2 h availability latency, the newest available init must still cover `t + 6 h`. Assert in `ForcingProvider` that band coverage reaches `t_now + horizon`; if it doesn't, hold the last band and log a `warn` event. Use Herbie byte-range subsetting with the regex `:(?:UGRD|VGRD):10 m above ground|:TMP:2 m above ground|:RH:2 m above ground`. Herbie may return a list of Datasets; merge them.
- **Rotate to earth-relative.** HRRR u/v are relative to the Lambert grid. Herbie does not rotate them.
  - Rotation angle: `α = sin(38.5°)·(λ + 97.5°)` in degrees, where λ is longitude. About −12.8° at Altadena.
  - Apply `u_e = u·cos α + v·sin α` and `v_e = −u·sin α + v·cos α`.
  - **Verify the sign** against one RAWS/RTMA reading (Santa Ana flow should come out of the NE).
- **Regrid:** crop to bbox + 15 km, then bilinear onto the model grid. `temp_c` and `rh` are domain means per `fxx`.
- **Availability:** `available_ms = S3 LastModified` of the `fxx` object when it is within 3 h of init. Otherwise fall back to `init + 60 min + 2 min·fxx`.
- **Honest replay rule:** at sim time T, the engine uses the newest init with `available_ms ≤ T` and takes its forecast hours as hourly `Weather` bands. It never uses an analysis valid at T that wasn't available yet.
- **Output:** one NetCDF per init, `forcing/hrrr_{init}.nc` (`u10`, `v10` with dims `[fxx, y, x]`; `temp_c`, `rh` with dims `[fxx]`; attrs `init_ms`, `available_ms_by_fxx`). About 0.5 MB each.
- **Fallback if Herbie fights you for more than 45 min:** use the Open-Meteo historical-forecast API at a 3×3 lattice of points, bilinear-interpolated. Label it "HRRR via Open-Meteo, not as-of" in the UI and README.

## 6. Statics and vectors (`data/statics.py`, `data/vectors.py`, `data/exposure.py`)

### DEM, slope, aspect
- `py3dep.get_dem(bbox_lonlat, 10)` (returns EPSG:5070), then `reproject_match` to the template with `average` resampling.
- Slope as rise/run; aspect in degrees clockwise from north, pointing **downslope**. Use `numpy.gradient` with the 90 m spacing.

### Fuels
- LANDFIRE LFPS v2 job (research 01 §6). Layers `LF2024_FBFM40;LF2024_CC;LF2024_CH`, `Output_Projection=32611`, `Resample_Resolution=30`.
- Then `reproject_match` with mode resampling.
- Needs an email address.
- **Print the code histogram.** Physics needs the fraction of NB91 (urban) cells inside Altadena.
- Fallback if LFPS is down: the LANDFIRE CONUS GeoTIFF download for FBFM40 (large; crop with rasterio windowed read).

### Zones (`vectors/zones.geojson`). Source order; stop at the first that works

1. **Cal OES "California Evacuation Aggregation Layer"** (data.ca.gov, Zonehaven/Genasys schema; GeoJSON or ArcGIS REST). Filter to LA County zones intersecting the bbox. Keep `zone_id` = the county zone code (e.g. `ALD-FARNSWORTH`).
2. **The LA County / Genasys public ArcGIS layer**, if one is findable (search "Los Angeles County evacuation zones FeatureServer").
3. **Fallback:** build approximate zones by dissolving Census 2020 block groups. Name the 13 west-Altadena ones after the zones in `research/06` §2:
   - ALD-ARROYOSECO, CANON, CASITAS, CHANEY, FARNSWORTH, GARDEN, LAUREL, MEADOWS, MILLARD, PALM, WAPELLO, WHITEPARK, LCF-JPL
   - plus the east-Altadena zones
   - Set `approximate: true`. The UI then shows "zone boundaries approximate".

Target 20–60 zones in the AOI. **Note:** current polygons are post-fire edits at worst. Zone geometry rarely changes; say so in the README.

### Exposure (`data/exposure.py`)
- **Population and housing:** TIGER 2020 `tl_2020_06_tabblock20` (`POP20`, `HOUSING20`), clipped to the AOI. Area-weight blocks into zones (`population`, `housing`).
- **Assets:** OSM via osmnx `features_from_bbox`:
  - `amenity=hospital|clinic|school|kindergarten|fire_station`
  - `social_facility=nursing_home|assisted_living` or `amenity=nursing_home`
  - `power=substation`
  - `man_made=water_works|wastewater_plant`

  Write `vectors/assets.geojson` with `zone_id` from a point-in-polygon join. `n_critical` = hospitals + nursing homes + schools in the zone.
- **Egress:** use `ox.graph_from_bbox(network_type="drive")`. For each zone, `egress_roads` = the number of distinct roads (any drivable class, including residential) crossing the zone boundary.
- **Tiers** (`00` §7):
  - `tier = 1` if the zone has a hospital or a nursing/assisted-living facility, or `egress_roads ≤ 1`
  - `tier = 2` if population > 500 or the zone has a school
  - `tier = 3` otherwise
  - `n_critical` = hospitals + nursing/assisted living + schools
  - `evac_min_default`: 75 for tier 1, 45 otherwise
  - **Print the tier histogram.** If more than half the zones are tier 1, recheck egress.
- **Rasterize** zones into `static/zone_index.tif` (int16, −1 = none) on the model grid with `rasterio.features.rasterize`.

### Story geometry
- `lake_ave.geojson`: the Lake Ave centerline from OSM (`name="North Lake Avenue"`), clipped to the AOI. Verify that it sits around lon −118.1316.
- **`area_x.geojson`** (honest-number region): the lon/lat box from `scenario.yaml` `area_x` (default: −118.165 to −118.1316, 34.190–34.235 N), i.e. the west-Altadena foothills and edge (research 06 §2). **All cells inside count, any fuel.** Write `static/area_x_index.tif` and `ember/data/area_x.py` with:
  - `area_x_polygon()`
  - `area_x_satellite_hit(pixels)`: the shared function (§2)
- **`spawn_points.json`:** `{ "demo": [{spawn_id, lon, lat, zone_id, street}] × 6 }`. These are judges' phones' assigned locations: on streets inside at-risk west-Altadena zones, not in canyons. Hand-pick them on a map. `load_scenario` seeds them into the hub (`04` §2.2).
- **`load_landscape(scn) -> Landscape`** (`00` §6): reads `static/*.tif`; slope as rise/run, aspect as the downslope azimuth.

## 7. Terrain and imagery for the web (`data/terrain.py`)

- **Elevation:** reproject the 10 m DEM (not the 90 m one) to EPSG:4326 over `aoi_bbox` at about 2048×2048. Encode as **Terrarium PNG** (`R·256 + G + B/256 − 32768`): `v = elev + 32768`, `R = floor(v/256)`, `G = floor(v) % 256`, `B = floor((v − floor(v))·256)`. Write `web/terrain_elev.png`.
- **Texture (must be pre-fire):** **first check the acquisition date** with the service's `identify` / item info. If it shows the 2025 burn scar, use pre-2025 NAIP from Microsoft Planetary Computer (STAC `naip`, year ≤ 2024) or a Dec 2024 Sentinel-2 L2A composite. Record `texture_date` in the manifest. Default source: export from `https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export?bbox=W,S,E,N&bboxSR=4326&size=4096,4096&imageSR=4326&format=jpg&f=image` (NAIP, public domain). If the 4096 request is refused, use 2048.
  - **Night grade:** brightness ×0.35, saturation ×0.4, slight blue tint.
  - Write `web/terrain_tex.jpg`.
  - Attribution "USGS The National Map: Imagery; USGS 3DEP".
- Bounds = `aoi_bbox`. Both files go in the manifest's `terrain` block.

## 8. Replay manifest (`data/manifest.py`)

`replay_manifest.parquet` has one row per observation batch (one GOES scan, one VIIRS pass) and one row per forcing file:

```
entry_id str | source str (goes18_fdcc|goes18_fdcm|goes16_fdcc|viirs_n20|viirs_snpp|viirs_n21|hrrr_sfc)
kind enum(observation|forcing) | scan_start_ms | scan_end_ms (forcing: init / valid) | available_ms
latency_basis enum(s3_last_modified|product_created|assumed_urt|assumed_nrt) | local_path | rows (n pixels) | n_hot | provenance
```

**Availability rules** (research 01 §12):

| Source | Rule |
|---|---|
| GOES | `available = max(created, s3_last_modified)` if `s3_last_modified − scan_end < 2 h`. Otherwise `created + 3 min` (assumed). |
| HRRR | §5 |
| VIIRS | §4 |

Log the GOES latency distribution (median, p90) to `truth/latency.json`. It is a citable number for Q&A ("GOES fire product reached the public bucket a median of X min after scan").

## 9. Truth and timeline (`data/truth.py`)

`web/timeline.json` (display) and `truth/timeline.json` (same content). Times are PST; store `t_sim_ms` in UTC. Sources: Citygate (May 2026), LA County AAR, FSRI via LA Times (research 06 §2).

| PST | Event | kind |
|---|---|---|
| Jan 7 18:15 | Ignition, Eaton Canyon | official |
| 18:45 | Aircraft grounded by wind | official |
| 18:48 | Advisory alerts to east Altadena zones | official |
| 19:26 | First evacuation orders (east Altadena) | official |
| 22:50 | 911 call: fire visible from E. Calaveras St | report |
| 23:49 | First structure fire west of Lake (Glenrose Ave and W. Loma Alta Dr) | report, `as_observation: true` |
| Jan 8 ~01:30 | First VIIRS detection | satellite |
| 02:18 | Radio: fire moving west in foothills north of Farnsworth Park | report, `as_observation: true` |
| 03:25 | Evacuation orders for 13 west-Altadena zones | official |
| 05:13 | FireGuard: front ~1 block west of Lake Ave | report |

**Notes**
- **`as_observation: true` rows** become `ground_report` observations (`provenance="real"`, trust 0.9, radius 400 m) **only if** `scenario.yaml` sets `use_documented_reports: true`. Default false: the honest number is computed **without** them. Geocode the intersections by hand on a map and note it.
- **P1 validation:** `truth/feds_perimeters.geojson`, NASA FEDS 12-hourly VIIRS perimeters for the Eaton fire (research 01 §10), used for the Sørensen metric.
- **P2:** `dins_zone_summary.json`, CAL FIRE DINS damage counts **aggregated per zone only**, never per address.

## 10. Web manifest and static files (`data/webpack.py`)

Write `web/manifest.json` (`00_contracts` §4.1) and copy the vectors. Also write:
- **`sensors.json`:** one row per sensor in `00_contracts` §3.1 `SensorInfo` form.
- **`satellites.json`:**
  - GOES-18 at 137.2°W and GOES-16 at 75.2°W (geo)
  - S-NPP 37849, NOAA-20 43013, NOAA-21 54234 with current TLEs from CelesTrak (verify the NORAD IDs)
  - The frontend time-shifts the LEO tracks to match the real scan times; label them "orbit illustrative; scan time real".

The web build copies `data/scenarios/eaton/web/` to `web/public/static/eaton/`.

## 11. Live mode (roadmap; P2 at most)

`ember/data/live.py` implements the same source interface as replay: `poll(now) -> new manifest rows`. Live versions of each source:

| Source | Live behaviour |
|---|---|
| GOES | `fs.ls` the current and previous hour prefix every 30 s |
| FIRMS | area API with `DAY_RANGE=1` every 2 min |
| HRRR | `herbie.latest` every 5 min |

In live mode, `available_ms = first_seen_wall_ms`. Not needed for the demo. Mention it as "same code path" in the pitch, and only claim what works.

## 12. Acceptance tests (`engine/tests/test_data.py`)

1. `grid.json` corners round-trip: lon/lat → UTM → the same corner within 1 m.
2. All static rasters have shape (340, 340), CRS 32611, and identical transforms.
3. FBFM40 codes ⊂ the valid SB40 + NB set. Print the histogram.
4. GOES pixel table: no DQF 2–5 rows; `hot` matches mask classes; `available_ms ≥ scan_end_ms`.
5. HRRR: at 06Z on Jan 8 the domain-mean wind direction comes **from** the NE quadrant (Santa Ana). A sign error shows up as SW.
6. Zones: ≥ 13 west-Altadena zones are present (or approximate fallback flagged); population sums are within 20% of the Census total for the clipped blocks.
7. `replay_manifest.parquet` is sorted by `available_ms` and covers 02:00–15:00Z.
8. `web/manifest.json` validates against the TS interface (load it in a tiny vitest).

## 13. Time budget (Cody + coding agents)

| Step | Owner | Est. | Needed by |
|---|---|---|---|
| G1-a GOES check (coding agent, Cody reviews) | Cody | 1.25 h | 09:30 |
| Grid + DEM + LANDFIRE + slope/aspect + `load_landscape` | **Cody** (was Abby) | 1.5 h | herself at ~10:00 |
| HRRR fxx 0–9 + `ForcingProvider` | Cody | 1.5 h | Abby at ~12:00 |
| GOES-18 FDCC pixel table + scan events + manifest | Cody | 1.25 h | engine M1 13:00 |
| Zones + exposure + assets + area X + spawn points | Cody (agent) | 2 h | risk at M1 |
| Terrain/texture export (pre-fire check) | Cody (agent) | 0.75 h | Linda by ~14:00 (placeholder until then) |
| VIIRS detections | Cody | 0.5 h | P1 |
| Timeline / FEDS | Cody (agent) | 0.75 h | P1 |
| *Cut to P2:* GOES-16, FDCM ingest, scan flash files, VIIRS negatives, `building_frac`, live pollers | — | — | — |
