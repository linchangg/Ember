# T1: G1-a GOES go/no-go check (`check_goes.py`) + shared `area_x_satellite_hit()`

**Goal.** Decide, from real GOES-18 data, whether Eaton stays the demo fire. Two outputs:
- `scripts/check_goes.py`, which reads the archived GOES-18 fire product for the Eaton night, parallax-corrects the pixels, and prints a GO/NO-GO with `t_sat_goes`.
- One shared hit function, `area_x_satellite_hit()`. The engine's metrics will import it later, so the gate and the reported number use **one** definition.

**Follow `docs/briefs/README.md` §2 for all general rules.** Branch: `c-t1-goes-gate`. **Start only after T0 is merged** (you import `ember.data.grid` and `ember.data.config`). Time box: **90 minutes**. If S3 listing or reading fights you for more than 20 min, switch to `goes2go` (step 3).

## Read
- `spec/01_data_pipeline.md` §2 (the whole gate) and §6 "Story geometry" (the `area_x.py` part only)
- `spec/03_engine.md` §9: the hit definition table and the `t_sat_goes` row
- `research/01_data_sources.md` §2, lines 36–123. This has the file naming, the `geos` projection recipe and the pixel geometry at Altadena. **Snippets there are unverified; test them.**
- `spec/00_contracts.md` §3.1: `GOES_FIRE` and `GOES_STRONG` (import them from `ember.contracts`; don't redefine them)

## You own
- `engine/ember/data/area_x.py`
- `engine/ember/data/goes.py`: only the read/geometry helpers below. Wave-2 task T8 adds the pixel-table writer to this same file later.
- `engine/ember/scripts/check_goes.py`
- `engine/tests/test_area_x.py`, `engine/tests/test_goes_geo.py`

Outputs go under `data/scenarios/eaton/raw/` (git-ignored) and `data/scenarios/eaton/check_goes_report.html` (don't commit the HTML; Cody decides).

## Steps

### 1. Dependencies
```bash
uv add s3fs h5netcdf xarray shapely pyproj matplotlib py3dep
```
If `py3dep` won't install on Windows within 10 minutes, drop it and use the fallback DEM in step 4.

### 2. `engine/ember/data/area_x.py` (pure functions, no I/O except reading `scenario.yaml`)
```python
def area_x_box(cfg: dict) -> tuple[float, float, float, float]   # (lon_min, lat_min, lon_max, lat_max) from cfg["area_x"]
def area_x_polygon(cfg: dict) -> shapely.Polygon                  # lon/lat box
def lake_ave_lon(cfg: dict) -> float                              # cfg["area_x"]["lon_max"]
    # ponytail: Lake Ave as a constant meridian; T9 writes the real centreline (lake_ave.geojson)
def pixel_is_hit(px: dict, cfg: dict) -> bool
def area_x_satellite_hit(pixels: list[dict], cfg: dict) -> bool   # any(pixel_is_hit(...))
def t_sat_goes(scans: list[tuple[int, bool]]) -> int | None       # [(scan_start_ms, is_hit)] sorted by time
```
- **A pixel dict** has at least `mask: int` and `ring: list[[lon, lat]]` (the parallax-corrected footprint, closed or open).
- **`pixel_is_hit` is True only if all three hold:**
  - (a) `mask in GOES_STRONG`;
  - (b) the area of `ring ∩ area_x_polygon` divided by the area of `ring` is ≥ 0.5, **computed in EPSG:32611**: project both polygons with `pyproj` + `shapely.ops.transform` before taking areas;
  - (c) every ring vertex has `lon < lake_ave_lon(cfg)`, so the footprint lies wholly west of Lake Ave.
- **`t_sat_goes`:** the first scan `i` that is a hit where **at least 2 of scans `i, i+1, i+2`** are hits. Return that scan's start ms, or `None`. If `i` is near the end of the list, count only the scans that exist (2 of what's there).

### 3. `engine/ember/data/goes.py`: read + geometry helpers
- `GOES18_LON = -137.2` (sub-satellite longitude).
- `list_fdc(product: str, day_of_year: int, hours: range) -> list[str]`: anonymous `s3fs.S3FileSystem(anon=True)`, listing `noaa-goes18/{product}/2025/{doy:03d}/{hh:02d}/`. Return the keys sorted.
- `parse_times(key) -> dict`: from the filename fields `s`, `e`, `c` (format `YYYYJJJHHMMSSt`; the last digit is tenths of a second) to `scan_start_ms`, `scan_end_ms`, `created_ms`. Unit-test it with the example filename in research 01 line 38.
- `read_crop(key, bbox_lonlat, margin_km=5) -> dict`. Open with `xr.open_dataset(fs.open(key), engine="h5netcdf")` (reads only the chunks needed). Build the `geos` projection from `goes_imager_projection` exactly as research 01 lines 104–119 show (x/y are radians; metres = radians × `perspective_point_height`). Find the x/y index window covering the bbox + margin, then load **only** that window of `Mask`, `DQF` and `Power`. Return:
  - `x`, `y` (radians, 1-D)
  - `mask`, `dqf`, `power` (2-D)
  - `lon`, `lat` (2-D apparent centres)
  - the `geos` proj object
  - the times from `parse_times`

  Cache the cropped arrays as `.npz` under `data/scenarios/eaton/raw/goes/{basename}.npz`, and reuse the cache on rerun.
- `footprint_ring(x_rad, y_rad, geos, h) -> list[[lon, lat]]`: the 4 corners at `x ± 28e-6`, `y ± 28e-6` radians (half of the 56 µrad pixel), converted with `geos(X*h, Y*h, inverse=True)`.
- `parallax_shift(lon, lat, elev_m) -> (lon_c, lat_c)`: move the point **toward the sub-satellite point (lat 0, lon −137.2)** by `d = elev_m · tan(VZA)` metres.
  - **VZA:** convert the ground point (WGS84, height `elev_m`) and the satellite (lat 0, lon −137.2, height 35,786,023 m above the equator) to ECEF with `pyproj.Transformer.from_crs("EPSG:4979", "EPSG:4978")`. Let `v = sat − ground`. The local up vector at the ground point is `(cos φ cos λ, cos φ sin λ, sin φ)`. Then `VZA = arccos(v·up / |v|)`.
  - **Direction:** take the azimuth with `pyproj.Geod(ellps="WGS84").inv(lon, lat, -137.2, 0.0)`, then `Geod.fwd(lon, lat, az, d)`.
- `correct_pixel(lon, lat, ring, dem_lookup) -> (lon_c, lat_c, ring_c)`, iterating twice:
  1. Look up `h = dem_lookup(lon, lat)` (≥ 0).
  2. Shift the centre.
  3. Look up `h` again at the shifted point and shift the **original** apparent centre with the new `h`.

  Then apply the same Δlon/Δlat to every ring vertex.

### 4. DEM for parallax (`dem_lookup`)
- **Preferred:** if `data/scenarios/eaton/static/dem.tif` exists (Abby's A1), sample it.
- **Else:** `py3dep.get_dem(bbox, resolution=90)` once, saved to `data/scenarios/eaton/raw/dem_parallax.tif`. Check its signature first: `uv run python -c "import py3dep, inspect; print(inspect.signature(py3dep.get_dem))"`.
- **Last-resort fallback** (only if both fail): a constant 600 m, with a loud `WARNING: constant parallax elevation`. Write it in the report too.

Return a function `(lon, lat) -> metres` that uses nearest-cell lookup.

### 5. `engine/ember/scripts/check_goes.py`
CLI: `uv run python -m ember.scripts.check_goes --scenario eaton [--hours 2-15] [--no-fdcm]`.
1. Load `cfg = load_scenario(scn)` and `bbox = bbox_lonlat(cfg["grid"])` (from T0).
2. **Area-X sanity (spec step 5).** Make a small PNG of the area-X box, the Lake Ave meridian and the ignition point over the DEM hillshade, and embed it at the top of the report. **Don't change `scenario.yaml`.** If the box looks wrong, say so in the summary for Cody to decide.
3. For every FDCC key for day 008, hours 02–15 UTC:
   - Read the crop.
   - Keep pixels with `mask ∈ GOES_FIRE` **or** `dqf == 1` (needed for counts).
   - Drop `dqf ∈ {2, 3, 4, 5}` (spec `01` §3 rule).
   - Parallax-correct the **fire** pixels.
   - Build `pixels = [{"mask", "dqf", "lon", "lat", "ring", "frp_mw"}]`.
   - Record `(scan_start_ms, area_x_satellite_hit(fire_pixels, cfg), n_fire, n_strong)`.
4. **FDCM (informational, (d)):** list `ABI-L2-FDCM` for the same hours. For each distinct sector (M1/M2), open one file and print its `geospatial_lat_lon_extent` and whether it covers the bbox. Don't ingest FDCM.
5. **Report** `data/scenarios/eaton/check_goes_report.html`, one self-contained file with PNGs inlined as base64 (`io.BytesIO` + `fig.savefig(..., dpi=60)`):
   - **Frames every 15 min:** corrected fire pixel rings coloured by mask class, the area-X box, the Lake Ave meridian, and the scan time in PST.
   - **Summary table:**
     - (a) first fire detection after 02:15Z
     - (b) `t_sat_goes` (UTC and PST)
     - (c) a per-scan list of hit / n_fire / n_strong, to show persistence and bleed
     - (d) FDCM coverage
     - the DEM source used for parallax
6. **Decision rule (`01` §2 step 6).** Print one final line:
   - `G1-a: GO (Eaton), t_sat_goes = HH:MM PST` if `t_sat_goes` is before **11:25Z**;
   - otherwise `G1-a: GOES NO-GO; run the VIIRS check (01 §4) before switching to Palisades`.

   VIIRS is not in this task.

### 6. Tests (offline, default)
- **`test_area_x.py`:**
  - Synthetic square ring fully inside area X, mask 10 → hit.
  - The same with mask 14 (fire but not strong) → not hit.
  - A ring 40% inside → not hit; 60% inside → hit.
  - A ring inside the box but with one vertex east of Lake Ave → not hit.
  - `t_sat_goes`: `[F, T, F, F, T, T, F]` → the 5th scan's time; `[T, F, F, T, F, F]` → None; `[F, T, T]` → the 2nd.
- **`test_goes_geo.py`:**
  - `parse_times` on the research example filename.
  - `parallax_shift` at Altadena (−118.12, 34.20) with 1000 m moves the point **0.9–1.1 km** (spec: ~0.99 km per 1000 m), toward the south-west (both lon and lat decrease).
  - `elev 0` → no move.
- **Network test** (skipped unless `EMBER_NETWORK_TESTS`): `list_fdc("ABI-L2-FDCC", 8, range(2, 3))` returns 12 keys. Then `read_crop` on the first one returns arrays with matching shapes.

### 7. Run the check
`uv run python -m ember.scripts.check_goes --scenario eaton`. Paste the full console output, including the final G1-a line, into the PR body.

## Done when
- The script runs end to end against `s3://noaa-goes18` and writes the HTML report.
- It prints the G1-a line with `t_sat_goes`.
- `uv run pytest -q` passes.
- The PR body contains the G1-a result and the DEM source used. Cody copies the go/no-go into `docs/process.md`.

## Don't
- Write the GOES pixel parquet table, the replay manifest or the VIIRS ingest. Those are wave-2 T8/T13.
- Edit `scenario.yaml`. Report box problems instead.
- Put a second copy of the hit logic anywhere. Wave-2 metrics imports `area_x_satellite_hit` and `t_sat_goes` from `ember.data.area_x`.
