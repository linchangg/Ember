# T6: HRRR wind forcing (`data/hrrr.py`)

**Goal.** Download the archived HRRR surface forecasts for the Eaton night. Rotate the winds to earth-relative, regrid them onto the 90 m model grid, and write one small NetCDF per model run (init), each stamped with when it became available. The engine later uses only the newest init that was available at each sim time.

**Follow `docs/briefs/README.md` §2 for all general rules.** Branch: `c-t6-hrrr`. **Start after T0 is merged** (you import `ember.data.grid` and `ember.data.config`). Time box: if Herbie/cfgrib fights you for more than **45 min**, switch to the Open-Meteo fallback (step 7). That's in the spec, not a failure.

## Read
- `spec/01_data_pipeline.md` §0 (layout: `forcing/`), §1 (grid, bilinear for winds), §5 (**all of it**: your spec), §12 test 5
- `research/01_data_sources.md` §4 "Wind" (Herbie notes; the snippets are unverified)
- `engine/ember/data/grid.py` (from T0): `template()`, `bbox_lonlat()`, `cell_centers()`

## You own
- `engine/ember/data/hrrr.py`
- `engine/tests/test_hrrr.py`

Outputs go in `data/scenarios/eaton/forcing/hrrr_{YYYYMMDDHH}.nc` (about 0.5 MB each, ~22 files). **Ask Cody before committing them**; the default is not to commit (they can be rebuilt). Raw GRIB goes under `data/scenarios/eaton/raw/hrrr/` (git-ignored).

## Output format (the contract with T7's ForcingProvider; don't deviate)
One NetCDF per init, written with xarray:
- `u10`, `v10`: `float32`, dims `("fxx", "y", "x")`, **earth-relative** m/s on the model grid (`y` row 0 = north, coords = cell centres from `grid.cell_centers`).
- `temp_c`, `rh`: `float32`, dims `("fxx",)`. These are domain means; `rh` is in **0–1** (HRRR gives %, so divide by 100).
- Coord `fxx` = `0..9` (int).
- Attributes:
  - `init_ms` (int)
  - `available_ms_by_fxx` (a list of 10 ints)
  - `source = "HRRR sfc via Herbie"`, or the fallback label
  - `rotation = "earth-relative"`
  - `crs = "EPSG:32611"`

## Steps

1. Dependencies: `uv add herbie-data xarray cfgrib netcdf4 scipy`. cfgrib needs eccodes. On Windows, try `uv add eccodes ecmwflibs` if cfgrib can't find the library. Report what worked.
2. **Constants and pure helpers** (unit-tested, no network):
   ```python
   INITS = [datetime(2025,1,7,18,tzinfo=UTC) + timedelta(hours=h) for h in range(22)]   # 18Z Jan 7 .. 15Z Jan 8
   FXX = range(10)
   SEARCH = r":(?:UGRD|VGRD):10 m above ground|:TMP:2 m above ground|:RH:2 m above ground"
   def rotation_deg(lon: np.ndarray) -> np.ndarray      # alpha = sin(38.5°) * (lon + 97.5)   (degrees)
   def to_earth_relative(u, v, lon) -> tuple[u_e, v_e]  # u_e = u cosα + v sinα ; v_e = −u sinα + v cosα
   def wind_from_deg(u, v) -> float                     # meteorological "from" direction of the mean vector, 0..360
   def available_ms(init_ms: int, fxx: int, s3_last_modified_ms: int | None) -> int
       # S3 LastModified if it exists and is within 3 h of init; else init + 60 min + 2 min·fxx
   ```
3. **Fetch one init:** `fetch_init(init: datetime) -> list[xr.Dataset]`, one per `fxx`.
   - Use `Herbie(init, model="hrrr", product="sfc", fxx=f)` and `.xarray(SEARCH)`. It may return a list of Datasets; merge them with `xr.merge(..., compat="override")`.
   - Get the S3 LastModified of each GRIB object. Herbie exposes the URL; do an anonymous `HEAD` on the `noaa-hrrr-bdp-pds` S3 HTTPS URL with `httpx` and read `Last-Modified`. If that fails, use `None` (which triggers the fallback rule).
   - Save the merged per-init raw subset as `raw/hrrr/{init}.nc` so reruns skip the download. The step is idempotent: skip if it exists, unless `--force`.
4. **Regrid:**
   1. Crop the HRRR 2-D `latitude`/`longitude` fields to `bbox_lonlat(g)` + 15 km.
   2. Rotate u/v with `to_earth_relative` using each HRRR cell's own longitude (HRRR longitudes may be 0–360; convert to −180..180 first).
   3. Interpolate onto the model cell centres converted to lon/lat (`grid.xy_to_lonlat` of the meshgrid of cell centres). Use `scipy.interpolate.griddata(..., method="linear")` from the cropped HRRR points; fill any NaN with `"nearest"`.
   4. `temp_c` = mean of `t2m − 273.15` over the crop; `rh` = mean of `r2/100`.
5. **Write** `forcing/hrrr_{YYYYMMDDHH}.nc` in the format above. Add a CLI:
   ```bash
   uv run python -m ember.data.hrrr --scenario eaton [--force] [--only 2025010806]
   ```
   It prints, per init, the `available_ms` of fxx 0 and 9 (PST), the domain-mean wind speed and direction at fxx 0, and the file size.
6. **Sign check (`01` §12 test 5).** For the init that is available by 06Z Jan 8 (the newest init with `available_ms_by_fxx[0] ≤ 06Z`), take the band valid at 06Z. The domain-mean wind must blow **from the NE quadrant (0°–90°)**. Santa Ana flow: an SW result means the rotation sign is wrong. **Print the result in the PR.**
7. **Fallback (only if step 3 fails for more than 45 min).** Use the Open-Meteo historical-forecast API at a 3×3 lattice of points over the bbox. Request hourly `wind_speed_10m`, `wind_direction_10m`, `temperature_2m` and `relative_humidity_2m` for Jan 7–8 2025, and convert speed/direction to u/v (these are already earth-relative). Interpolate bilinearly to the grid. Write the same files with `source = "HRRR via Open-Meteo, not as-of"`, and `available_ms_by_fxx` = init + 60 min + 2 min·fxx. **Say clearly in the PR that the fallback was used**; the UI must label it (`01` §5).
8. **`engine/tests/test_hrrr.py`:**
   - **Offline:**
     - `rotation_deg(-118.1) ≈ -12.8` (±0.1).
     - `to_earth_relative` with α = 0 is the identity; it preserves vector magnitude for any α.
     - `wind_from_deg(u=-1, v=-1) == 45` (wind blowing toward the SW comes from the NE).
     - `available_ms` uses LastModified when it's within 3 h, and the fallback otherwise.
     - Write a tiny fake output file with the format above, then reopen it and check the dims, dtypes and attributes. This pins the contract for T7.
   - **Network** (skip unless `EMBER_NETWORK_TESTS`): build one init (`2025010806`), and the sign check from step 6 passes.
9. Run the full build for all 22 inits (in the background; it may take a while). Paste the CLI summary and the sign-check line into the PR.

## Done when
- The offline tests pass.
- All inits are written (or the fallback is used and labelled).
- The Santa Ana sign check prints a NE direction.
- The PR lists file count and total size, and whether the files were committed.

## Don't
- Write `ForcingProvider` (that's T7) or any engine code.
- Use an analysis valid at time T that wasn't yet available at T. Availability is per `fxx` and goes in the attributes.
- Commit raw GRIB.
