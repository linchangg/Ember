# 01 · Data sources, ingestion recipes, replay layer

Scope: every external feed Ember needs, how to get each one, how late it really arrives, and a replay and cache design that runs fully offline. Tags used below: **(verified)** means confirmed from a primary source or from package source code I inspected. **(unverified)** means from memory, secondary sources, or not testable from this shell; check these on a laptop. No file was downloaded, because NOAA, AWS and NASA hosts are blocked from this shell.

## 0. TL;DR

1. **Satellite fire data: use GOES-18 (West) ABI L2 FDC from `s3://noaa-goes18`, read with xarray and pyproj. Do not depend on NGFS or FIRMS for GOES data.** There are three products: FDCC (CONUS, 5 min), FDCF (full disk, 10 min) and FDCM (mesoscale, 1 min, when a sector covers the fire). FDCM is listed in goes2go's product table (verified). GOES-18 sat over the LA fires with a 1-min mesoscale sector from 18:10 UTC on Jan 7, 2025 (verified, CIMSS). NGFS is experimental and has no documented public bulk API. FIRMS shows GOES data on its map only, as a provisional layer, and the area API does not list a GOES source (verified).
2. **Release replay data at its real availability time, read from the data itself.** Every ABI filename carries a product creation time `c…`, and every S3 object has a `LastModified`. Use `available_time = max(c_time, LastModified)` when LastModified falls within about 2 h of the scan; otherwise fall back to an assumed latency. Do the same for HRRR. I found no published latency figure for FDC on AWS. Run a 1-hour live logger on Saturday to measure it; that gives us an honest number we can cite.
3. **Grid: EPSG:32611 (UTM 11N), 60 m cells, 512×512 (30.72 km).** For Eaton, the upper-left corner is (381480, 3800280), which spans about lon -118.288 to -117.951 and lat 34.063 to 34.337. Align every raster to this grid with `rioxarray.reproject_match`. Use nearest resampling for categorical layers and bilinear for continuous ones. Rotate HRRR winds from grid-relative to earth-relative first, a correction of about 13° at LA (unverified sign).
4. **Static layers:** py3dep for the DEM; LANDFIRE LFPS v2 (`LF2024_FBFM40` and canopy layers; needs an email address, no account); 2020 TIGER `TABBLOCK20`, which already carries `POP20`/`HOUSING20` (verified); OSM via osmnx for facilities and roads. For the 3D frontend, use AWS Terrarium terrain tiles.
5. **Biggest data traps:**
   - HIFLD Open is gone (shut down Aug 26 and Sep 16, 2025; verified).
   - Overture keeps only the last ~60 days of releases (verified in the CLI source), so any building layer we can download now is post-fire for Altadena.
   - GOES fire pixels are not terrain-corrected: about 1 km of shift per 1000 m of elevation for GOES-18 at LA (computed).
   - RRFS replaces HRRR operationally on **Nov 3, 2026** (verified). This does not affect the demo. It does matter for anything we call "production path".

## 1. Feed summary

| # | Feed | Access | Format | Resolution | 30×30 km, 12 h volume | Real latency (obs → public) | Archive |
|---|---|---|---|---|---|---|---|
| 1a | GOES-18 ABI L2 FDCC | `s3://noaa-goes18/ABI-L2-FDCC/YYYY/DDD/HH/` (anonymous), `s3fs`/`goes2go` | netCDF4 | ~2 km nominal; **2.3×2.8 km at Altadena** (computed) | 144 files, ~0.3–1 MB each, so ~50–150 MB raw → <1 MB after bbox crop (unverified sizes) | Not published. Measure it with `c`-time and LastModified (unverified; expect minutes) | GOES-18 from 2022. GEE lists from 2022-10-13 (verified) |
| 1b | GOES-18 FDCF | `ABI-L2-FDCF` | netCDF4 | same | 72 files, a few MB each (unverified) | same | same |
| 1c | GOES-18 FDCM | `ABI-L2-FDCM` (M1/M2 in filename) | netCDF4 | same, 1 min | ≤720 small files per sector | same | Only when a sector covered the area |
| 1d | GOES-East FDC | `noaa-goes16` (Jan 2025), `noaa-goes19` (after GOES-19 became East in 2025; date unverified) | netCDF4 | **3.4×3.4 km at LA, VZA 60°** (computed) | same | same | same |
| 2 | VIIRS 375 m (SNPP, N20, N21) | FIRMS area API `…/api/area/csv/{KEY}/{SOURCE}/{W,S,E,N}/{1-5}/{YYYY-MM-DD}` | CSV | 375 m | 10s–100s of rows, <100 KB | URT about 50 s (CONUS); RT <60 min; NRT about 3 h (unverified) | SP (standard) and NRT sources (verified) |
| 3a | HRRR sfc and subh | `noaa-hrrr-bdp-pds` via Herbie byte-range | GRIB2 | 3 km, hourly and 15 min | ~5 MB/file after idx subset; 12 cycles × f00–f06 ≈ 400 MB download, <10 MB after crop (unverified) | Use LastModified (unverified: roughly 45–90 min after init) | AWS and GCP from 2014-07-30 (verified) |
| 3b | RTMA-RU (15-min analysis) | `noaa-rtma-pds/rtma2p5_ru.YYYYMMDD/rtma2p5_ru.tHHMMz.2dvaranl_ndfd.grb2` (verified template) | GRIB2 | 2.5 km, 15 min | small | about 30 min (unverified) | AWS (verified registry link) |
| 4 | 3DEP DEM | `py3dep.get_dem(bbox, 10)` returns EPSG:5070 (verified) | GeoTIFF/xarray | 10 m (1 m in places) | 3000² float32 ≈ 36 MB | static | — |
| 4b | Terrain tiles | `s3://elevation-tiles-prod` (us-east-1, verified); `…/terrarium/{z}/{x}/{y}.png` (unverified path) | PNG Terrarium | z≤15 (unverified) | — | static | — |
| 5 | LANDFIRE fuels | LFPS v2 `https://lfps.usgs.gov/api/job/submit` (verified from rlandfire source) | zip → multiband GeoTIFF | 30 m | ~1000² × 6 bands ≈ 12 MB | static (LF2024 = v2.5.0, includes 2024 disturbances; verified) | LF2016–LF2024 |
| 6 | Buildings | MS GlobalMLBuildingFootprints quadkey CSV.gz; Overture GeoParquet | GeoJSONL / GeoParquet | footprint | ~100–150k buildings ≈ 10–20 MB parquet (unverified) | static | **Overture: last 2 releases only** (verified) |
| 7 | Population | TIGER 2020 `tl_2020_06_tabblock20.zip` with POP20 and HOUSING20 (verified fields) | Shapefile | census block | a few thousand blocks | static | — |
| 8 | Facilities and roads | Overpass/osmnx | GeoJSON / graph | vector | a few MB | static (but see the snapshot-date issue below) | `[date:]` Overpass queries |
| 9 | Validation | FIRIS public view FeatureServer, NIROPS IR (ftp.wildfire.gov), NASA FEDS (12-hourly), LA County AAR timeline | vector + PDF | — | small | — | yes |

## 2. GOES ABI L2 Fire/Hot Spot Characterization (FDC)

**Files.** `OR_ABI-L2-FDCC-M6_G18_s20250080200203_e20250080202576_c20250080203162.nc`. The `s`, `e` and `c` fields are `YYYYJJJHHMMSSt`: scan start, scan end and product creation time (UTC; the last digit is tenths of a second). goes2go parses `start` and `creation` with exactly these formats (verified in its source). Bucket path: `noaa-goes18/ABI-L2-FDCC/2025/008/02/`. Mesoscale products appear as `ABI-L2-FDCM` with M1 or M2 in the filename (unverified naming). Check each file's `geospatial_lat_lon_extent` to confirm the sector covered the bbox.

**Variables** (verified, GEE catalog and the NOAA ReadMe):
- `Mask`: fire category codes.
- `Power`: FRP in MW.
- `Area`: sub-pixel fire area in m², filled only for "processed" pixels.
- `Temp`: fire temperature in K, also processed pixels only.
- `DQF`: data quality flag.
- `goes_imager_projection`: projection parameters.
- `x`, `y`: scan angles in radians.

**Mask codes** (verified):

| Code | Meaning | Use in the filter |
|---|---|---|
| 10 / 30 | Processed fire (30 = temporally filtered, i.e. seen in the same pixel within 12 h) | **Hot, high confidence** |
| 11 / 31 | Saturated fire; FRP is a low-end estimate | **Hot, high confidence** |
| 12 / 32 | Cloud-contaminated fire | Hot, medium |
| 13 / 33 | High-probability fire (water-cloud false alarms are common) | Hot, medium |
| 14 / 34 | Medium probability | Hot, low |
| 15 / 35 | Low probability; many false alarms | Hot, very low or ignore |
| 170 | No background could be computed; large clusters at night over warm moist air | No observation |
| Other non-fire codes | Space, block-out zones, water, cloud, bad input | Read `Mask.flag_values` / `flag_meanings` from the file. I could not verify the numeric table. |

**DQF codes** (verified):

| DQF | Meaning |
|---|---|
| 0 | Good fire |
| **1** | **Good fire-free land** |
| 2 | Opaque cloud |
| 3 | Surface type, sunglint, LZA limit, off-earth or missing input |
| 4 | Bad input |
| 5 | Algorithm failure |

**Rule for the likelihood:** count "not hot" only where DQF == 1. Treat DQF 2–5 as "no information" (likelihood = 1). NOAA recommends codes {10, 11, 30, 31} for operational, false-alarm-averse use (verified). Known false-alarm sources include solar farms in the SW US on GOES-18, urban and coastal heterogeneity, and missing rectangular blocks (verified).

**Geometry at Altadena (computed with pyproj geos, 56 µrad IR pixel):**

| Satellite | Footprint (E-W × N-S) | View zenith angle | Shift per 1000 m elevation |
|---|---|---|---|
| GOES-18 | 2.27 × 2.80 km | 44.6° | 0.99 km |
| GOES-16/19 East | 3.44 × 3.43 km | 60.3° | 1.75 km |

Pixel locations are **not terrain-corrected** (verified, ReadMe). An elevated fire appears displaced *away from the sub-satellite point*, which for GOES-18 over LA means toward the NE, into the San Gabriels. Correct each detection by moving it toward the satellite by `h_DEM · tan(VZA)`. The real point-spread function extends beyond the nominal footprint, so the likelihood should use a soft footprint. With a 30 km domain, GOES-18 gives roughly 13×11 pixels. The value of GOES is cadence, not resolution.

**Fixed grid → lat/lon** (PUG formula; pyproj is the easy path):

```python
# unverified (no data downloadable here)
import s3fs, xarray as xr, numpy as np, pyproj, re, datetime as dt
fs = s3fs.S3FileSystem(anon=True)

def parse_t(tok):  # 's20250080200203' -> datetime
    return dt.datetime.strptime(tok[1:14], "%Y%j%H%M%S").replace(tzinfo=dt.timezone.utc)

def list_fdc(bucket="noaa-goes18", product="ABI-L2-FDCC", hour=dt.datetime(2025,1,8,2)):
    out = []
    for d in fs.ls(f"{bucket}/{product}/{hour:%Y/%j/%H}/", detail=True):
        name = d["name"].rsplit("/", 1)[1]
        s, e, c = re.findall(r"_([sec]\d{14})", name)
        out.append(dict(uri=f"s3://{d['name']}", scan_start=parse_t(s), scan_end=parse_t(e),
                        created=parse_t(c), s3_last_modified=d.get("LastModified")))
    return out

def fdc_pixels(path, bbox_lonlat):
    ds = xr.open_dataset(path)  # netcdf4/h5netcdf; x,y decoded to radians
    p = ds.goes_imager_projection.attrs
    h = p["perspective_point_height"]
    geos = pyproj.Proj(proj="geos", h=h, lon_0=p["longitude_of_projection_origin"],
                       sweep=p["sweep_angle_axis"], a=p["semi_major_axis"], b=p["semi_minor_axis"])
    W, S, E, N = bbox_lonlat
    xs, ys = geos([W, E, W, E], [S, S, N, N])           # metres = angle * h
    sub = ds.sel(x=slice(min(xs)/h - 1e-4, max(xs)/h + 1e-4),
                 y=slice(max(ys)/h + 1e-4, min(ys)/h - 1e-4))  # y is descending
    X, Y = np.meshgrid(sub.x.values * h, sub.y.values * h)
    lon, lat = geos(X, Y, inverse=True)
    m, q = sub.Mask.values, sub.DQF.values
    hot = np.isin(m, [10, 11, 12, 13, 14, 30, 31, 32, 33, 34])
    clear = (q == 1)
    return dict(lon=lon, lat=lat, mask=m, dqf=q, frp=sub.Power.values, hot=hot, clear=clear,
                x=sub.x.values, y=sub.y.values)  # keep x,y to build footprint polygons (±28 µrad)
```

**NGFS.** Its outputs (CSV, GeoJSON or KML; terrain-corrected; GOES pixel and feature files) are documented in the NGFS README (verified). The README calls them "non-operational and experimental" and gives no download URLs. NOAA's portal (`fire.data.nesdis.noaa.gov`) targets forecasters and responders, and the CIMSS pages offer dashboards, imagery (RealEarth) and an alert archive (e.g. `cimss.ssec.wisc.edu/ngfs/alert/report/<id>`). **Recommendation:** use NGFS only as a cited comparison ("NGFS first flagged X at T") if we can read an Eaton alert report in a browser. Do not ingest it.

**GOES in FIRMS.** GOES-16/18, Himawari and Meteosat appear as a filtered "Geostationary (provisional)" map layer at a 10-min cadence with about 30 min or less of latency (verified). The area API lists no GOES source (verified), so FIRMS is not an ingestion path for GOES.

## 3. VIIRS 375 m via FIRMS

- **Endpoint (verified):** `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{W,S,E,N}/{DAY_RANGE 1–5}/{YYYY-MM-DD}`.
- **Sources:** `VIIRS_SNPP_NRT|SP`, `VIIRS_NOAA20_NRT|SP`, `VIIRS_NOAA21_NRT|SP` (plus MODIS and LANDSAT_NRT). Check `/api/data_availability/csv/{KEY}/ALL` for the date ranges of each source.
- **MAP_KEY:** free and requested with an email address. A human on the team must request it; I did not. Limit: 5,000 transactions per 10 min (verified).
- **CSV fields (verified):**
  - `latitude`, `longitude` (pixel centre)
  - `bright_ti4`, `bright_ti5` (K)
  - `scan`, `track` (pixel size in km, which grows off-nadir)
  - `acq_date`, `acq_time` (UTC, HHMM; zero-pad it)
  - `satellite` (N, N20, N21)
  - `confidence` (l, n, h; h = saturated)
  - `version` (suffix "NRT" or none for SP)
  - `frp` (MW)
  - `daynight`
- **Latency:**
  - URT: about 50 s after observation for VIIRS over CONUS, via SSEC direct broadcast (verified).
  - RT/URT rows are dropped after 6 h or once NRT arrives (verified), so **the archive never shows URT timing**.
  - NRT target is about 3 h (unverified).
- **Overpasses:** each satellite crosses at about 01:30 and 13:30 local (unverified). The three are staggered, so expect about 3–5 looks per night over LA. Take the real times from `acq_time`.
- **Night of Jan 7–8, 2025:** the useful passes fall around 08:30–10:30 UTC (00:30–02:30 PST) (unverified).

```python
# unverified
import pandas as pd
def firms(key, source, bbox, day, ndays=2):
    W,S,E,N = bbox
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{W},{S},{E},{N}/{ndays}/{day}"
    df = pd.read_csv(url)
    df["t_obs"] = pd.to_datetime(df.acq_date + " " + df.acq_time.astype(str).str.zfill(4),
                                 format="%Y-%m-%d %H%M", utc=True)
    return df
# for 2025-01-08 try *_SP first, fall back to *_NRT if empty
```

## 4. Wind

**HRRR via Herbie (template verified from the herbie-data 2026.9.2 source):**
- Path: `https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{YYYYMMDD}/conus/hrrr.t{HH}z.wrf{sfc|subh}f{FF}.grib2`, with `.idx` byte-range subsetting.
- Fallback sources: Google, Azure, Pando.
- Forecast range: f00–f18 hourly, f00–f48 at 00/06/12/18Z.
- Archive: from 2014-07-30 (verified).
- Fields: `UGRD/VGRD:10 m above ground`, `GUST:surface`, and the hourly max `WIND:10 m` (unverified field name).
- `subh` gives 15-min steps. Herbie notes that it can return multiple hypercubes (verified, core.py #73).

**Gotcha: winds are grid-relative.** HRRR u/v are relative to the Lambert grid (unverified; check the GRIB `resolutionAndComponentFlags`). The rotation is α = sin(38.5°)·(λ + 97.5°), about **−12.8° at Altadena**. Herbie's `with_wind()` does **not** rotate (verified, it only computes speed and direction). Rotate before use, and sanity-check against RTMA or a RAWS station.

**Honest replay rule:** at clock time T, use the newest cycle whose `available_time ≤ T`, i.e. its forecast hours. Never use the analysis valid at T. For "what actually happened" diagnostics, use RTMA-RU (15 min, 2.5 km) or HRRR f00. Label which is which.

**Alternative:** Open-Meteo's historical-forecast API (`historical-forecast-api.open-meteo.com/v1/forecast`) offers HRRR since 2018, 15-min resolution in North America, and `wind_speed_10m`, `wind_direction_10m`, `wind_gusts_10m` (verified). It stitches together the early hours of successive runs, so it is **not** an as-of-T forecast. Use it only as a fallback and label it.

**RRFS:** goes operational Nov 3, 2026 (verified, SCN26-48). The demo can use HRRR now. List "swap to RRFS (incl. 1.5 km fire-weather nest)" on the roadmap.

```python
# unverified
from herbie import Herbie
H = Herbie("2025-01-08 02:00", model="hrrr", product="sfc", fxx=2)
dsl = H.xarray(r":(?:UGRD|VGRD):10 m above ground|:GUST:surface")  # may be a list of Datasets
# crop by 2-D latitude/longitude to bbox + 10 km margin, rotate u/v by alpha(lon), then regrid
```

## 5. Terrain

- **Backend:** `py3dep.get_dem((W,S,E,N), resolution=10)` returns xarray in EPSG:5070 (verified). It uses the fast static 3DEP VRTs at 10, 30 and 60 m. Reproject to the domain grid (60 m, bilinear, or `average` when downsampling), then derive slope and aspect on the 60 m grid. Alternatives: OpenTopography (needs an API key) and the AWS terrain tiles below.
- **Frontend:**
  - Location: `elevation-tiles-prod` (us-east-1, anonymous, verified).
  - Terrarium decode: `(R·256 + G + B/256) − 32768` (verified, joerd docs).
  - URL pattern: `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png` (unverified).
  - The US source is mainly 3DEP/NED (unverified).
  - deck.gl `TerrainLayer` decoder: `{rScaler:256, gScaler:1, bScaler:1/256, offset:-32768}`. Mapbox and MapLibre also accept `encoding: "terrarium"`.
  - Pre-fetch z10–z14 tiles for the bbox into `public/tiles/` for offline use (≈ a few hundred tiles, unverified).

## 6. Fuels: LANDFIRE

- **API (verified from the rlandfire source):** `GET https://lfps.usgs.gov/api/job/submit` with parameters `Email`, `Layer_List` (`;`-separated), `Area_of_Interest` (`"W S E N"` in lon/lat), `Output_Projection` (EPSG code, e.g. 32611), `Resample_Resolution` (m), and optional `Edit_Rule`/`Priority_Code`. The response is JSON with a `jobId`. Poll `GET /api/job/status?JobId=…` until `status` contains "Succeeded", then download `outputFile` (a zip holding one multiband GeoTIFF plus a .dbf for categorical layers).
- **Health check:** `/api/healthCheck`.
- **Layer names:** follow `LF2024_FBFM40` (the CONUS file is `LF2024_FBFM40_CONUS`, LF 2.5.0; verified). By analogy, the canopy layers should be `LF2024_CC`, `LF2024_CH`, `LF2024_CBH`, `LF2024_CBD` (unverified). Confirm at `lfps.usgs.gov/products`. LF2024 includes 2024 disturbances, so it is **pre-Eaton**. Good.
- **Urban fuel (FBFM40 codes):**
  - **91 = NB1** (urban/developed), 92 snow/ice, 93 agriculture, 98 water, 99 barren (unverified codes; standard Scott and Burgan).
  - The Rothermel model treats NB as **non-burnable**. Altadena's interior will therefore read as unburnable, while the Eaton losses were house-to-house and ember-driven.
  - Mitigation, visibly labeled: in NB1 cells with building density above a threshold, either (a) substitute a surrogate fuel model (e.g. GR2/TU1, or a structure-spread rate), or (b) model the WUI with an ember-spotting probability.
  - This is a physics decision for Abby. The data layer should provide a building-density raster on the same grid.

```python
# unverified; needs the team's email
import requests, time
p = dict(Email=EMAIL, Layer_List="LF2024_FBFM40;LF2024_CC;LF2024_CH;LF2024_CBH;LF2024_CBD",
         Area_of_Interest="-118.30 34.05 -117.94 34.35", Output_Projection=32611, Resample_Resolution=30)
job = requests.get("https://lfps.usgs.gov/api/job/submit", params=p).json()["jobId"]
while "Succeeded" not in (s := requests.get("https://lfps.usgs.gov/api/job/status", params={"JobId": job}).json())["status"]:
    assert "Failed" not in s["status"], s; time.sleep(5)
open("lf.zip","wb").write(requests.get(s["outputFile"]).content)
```

## 7. Buildings

- **Overture** (`overturemaps download --bbox=W,S,E,N -f geoparquet -t building -o b.parquet`):
  - Only the last two monthly releases (~60 days) exist (verified, CLI source). For Eaton this means post-fire data: destroyed Altadena homes may already have been removed from OSM-sourced features.
  - DuckDB alternative: `read_parquet('s3://overturemaps-us-west-2/release/<REL>/theme=buildings/type=building/*', hive_partitioning=1)` filtered on `bbox.xmin/xmax/ymin/ymax` (path pattern verified; bbox struct unverified).
- **Microsoft GlobalMLBuildingFootprints:**
  - Index: `dataset-links.csv` at `https://bfppub.blob.core.windows.net/$web/2026-08-13/dataset-links.csv` (verified). Download only the zoom-9 quadkey tiles that intersect the bbox.
  - US tiles were refreshed in 2026 from 2020–2025 imagery (verified), so post-fire edits are possible.
  - **Recommendation for Eaton:** use the legacy static `microsoft/USBuildingFootprints` California file (pre-2025 imagery; large, unverified size) or the LA County LARIAC building outlines (unverified availability), then clip.
  - Validate against **CAL FIRE DINS 2025 Eaton** (damage per structure; data.gov "DINS 2025 Eaton Public View"), which is also a great ground-truth layer.

## 8. Population

**Simplest route:** `https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_06_tabblock20.zip` (URL unverified). The 2020 TABBLOCK20 layout includes **POP20 and HOUSING20** (verified). Counts carry disclosure-avoidance noise, so aggregate to zones rather than showing single blocks. This avoids the Census API, where block-level queries need `in=state:06+county:037+tract:*`.

**Downscaling:** spread block population over residential buildings (area-weighted) to get a people-per-building raster on the grid. Use a GHSL or WorldPop 100 m raster only as a fallback.

## 9. Critical facilities, roads, evacuation zones

- **OSM via osmnx** (`ox.features_from_bbox`), with these tags:
  - `amenity=hospital|clinic|school|kindergarten|fire_station|police`
  - `social_facility=nursing_home|assisted_living` (or `amenity=nursing_home`)
  - `power=substation`
  - `man_made=water_works|wastewater_plant`
  - `ox.graph_from_bbox(network_type="drive")` for the road network: its chokepoints and evacuation times.
- **Overpass `[date:"2025-01-07T00:00:00Z"]`** returns pre-fire OSM state (unverified that public Overpass instances serve attic data for this query).
- **HIFLD Open was discontinued on Aug 26, 2025, and the portal went offline on Sep 16, 2025** (verified, NAPSG). Archived copies exist on source.coop (`seerai/hifld`) and Data Lumos (unverified completeness). Cite them only if we use them.
- **Evacuation zones:**
  - Cal OES publishes an experimental "California Evacuation Aggregation Layer" on data.ca.gov. It uses the Zonehaven/Genasys schema, refreshes every 10 min, is downloadable as GeoJSON or Shapefile or via ArcGIS REST (verified), and has **no status history** (verified absence).
  - Whether LA County is included is unconfirmed.
  - Fallback: make our own zones (e.g. block groups or road-bounded polygons) and label them "illustrative zones".

## 10. Validation and ground truth (Eaton, if chosen)

**Official timeline** (LA County AAR synopsis, verified; times PST):

| Date | Time | Event |
|---|---|---|
| Jan 7 | 18:18 | Ignition (time per news, unverified vs. the AAR) |
| Jan 7 | 18:48 | Advisory to 12 zones |
| Jan 7 | 19:26 | First evacuation orders |
| Jan 7 | 22:50 | 911 reports of fire west of Lake Ave |
| Jan 8 | 00:55 | First validated structure fire west of Lake (DINS) |
| Jan 8 | ~02:18 | Radio reports fire moving west |
| Jan 8 | **03:25** | **Evacuation messages west of Lake Ave** |
| Jan 8 | after ~05:00 | Main front crosses Lake (FireGuard imagery) |

The May 2026 independent investigation report (`file.lacounty.gov/.../1208708_...pdf`) has more detail.

**Honest metric:** "the forecast gave P > 0.5 for west Altadena cells at T_f, the first GOES or VIIRS hot pixel there came at T_s, and the order went out at 03:25".

**Perimeters:**
- **FIRIS:** `services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/CA_Perimeters_NIFC_FIRIS_public_view/FeatureServer/0` (verified URL). Whether it retains per-flight history is unverified, so query `where=…Eaton…`.
- **NIROPS IR:** `ftp.wildfire.gov/public/incident_specific_data/calif_s/2025_…/IR/YYYYMMDD/` (pattern verified for other fires; the Eaton path is unverified).
- **NASA FEDS:** 12-hourly VIIRS perimeters for Jan 4–19, 2025, available within about 4 h of FIRMS data (verified). The MapServer is `maps.disasters.nasa.gov/ags03/rest/services/california_wildfires_202501/FEDS`, with an OGC API at `gis.earthdata.nasa.gov/maphost/rest/services/EIS_Fire/OGCFeatureServer`.
- **WFIGS:** gives final perimeters.

## 11. Live mode

Each source implements `poll(now) -> list[ManifestEntry]`:
- **GOES:** every 30 s, `fs.ls` the current and previous hour prefix for FDCC, FDCF and FDCM; diff against seen keys. Optionally subscribe to the NODD SNS topic for `noaa-goes18` (topic exists per the AWS registry; ARN unverified). HRRR's topic is `arn:aws:sns:us-east-1:123901341784:NewHRRRObject` (verified).
- **FIRMS:** every 2–5 min, area API with `DAY_RANGE=1`, de-duplicated on (lat, lon, t, satellite). URT rows arrive within about a minute in CONUS.
- **HRRR:** every 5 min, `Herbie(...).grib` existence check for the newest cycle (or `herbie.latest`).

In live mode, `available_time = first_seen_time`. That is the same field replay uses, so the filter code path is identical.

## 12. Replay layer design

**Manifest:** one Parquet or JSONL row per file or observation batch.

```text
entry_id        str     sha1(source|uri)
source          str     goes18_fdcc | goes18_fdcm1 | goes16_fdcc | viirs_n20 | hrrr_sfc | hrrr_subh | rtma_ru | camera:<id> | ground:<id> | static:<layer> | truth:<layer>
kind            enum    observation | forcing | static | validation
scan_start      ts UTC  (obs) or init_time (forcing)
scan_end        ts UTC  (obs) or valid_time (forcing; one row per fxx)
available_time  ts UTC  when a live system could first have used it
latency_basis   enum    s3_last_modified | product_created | assumed:<profile> | live_first_seen
uri             str     original s3://, https:// or API URL
local_path      str     data/scenarios/<name>/raw|crop/...
sha256          str
bbox            [W,S,E,N]
summary         json    e.g. {n_hot:3, n_clear:140, max_frp:812}
provenance      enum    real | simulated   (shown in the UI)
```

**Latency rules:**

| Source | Rule |
|---|---|
| GOES | `max(created, s3_last_modified)` if `s3_last_modified − scan_end < 2 h`; otherwise `created + L_goes`. Log the measured distribution and report the median. |
| HRRR | S3 LastModified of each `fxx` object, with the same plausibility guard; fallback `init + 60 min + 2 min·fxx` (unverified). |
| VIIRS | Two named profiles. `urt` = `t_obs + 2 min`, matching FIRMS' ~50 s URT claim for CONUS. `nrt` = `t_obs + 3 h`. The default for the honest metric is `nrt` unless we state the URT assumption on screen. |
| Validation | Never released to the filter, only to the scoring panel. |

**Clock:** `ReplayClock(t_start, speed, t_now_override)`. A `Broker` holds the manifest sorted by `available_time`. `broker.advance(now)` yields the newly available entries to the *same* `Source.load(entry) -> list[Observation]` handlers that live mode uses. Forcing queries are as-of: `wind_at(valid_t, as_of=now)` picks the newest cycle with `available_time ≤ now`. Injected camera and ground reports are appended to the manifest with `available_time = wall-clock now` and `provenance = simulated`.

**Precompute and cache (offline demo):** `scripts/build_scenario.py --name eaton --bbox ... --t0 2025-01-08T02:00Z --t1 14:00Z` does the following:
1. Lists and downloads raw files.
2. Crops each one to the bbox plus margin (GOES: keep a pixel table with footprint polygons; HRRR: crop, rotate and regrid to NetCDF on the domain grid).
3. Writes static rasters as COGs aligned to the grid.
4. Writes `manifest.parquet`.
5. Copies terrain tiles.

The demo then reads only `data/scenarios/eaton/` (≈ <200 MB, unverified). Commit the cropped products (small) to the repo or a release asset. Never commit raw GOES or HRRR files.

**Grid:**
- `EPSG:32611`, 60 m, 512×512, transform `Affine(60,0,381480,0,-60,3800280)` (Eaton). For any other fire, pick the UTM zone of its centroid and snap the origin to 60 m.
- Use `rio.reproject_match(template)` to align layers:
  - Fuels: `Resampling.mode`, or `nearest` when the source is already 30 m.
  - DEM: `average` for 10→60 m.
  - Canopy: `average`.
  - Buildings: rasterize counts and areas with `rasterio.features.rasterize` at 60 m.
  - HRRR: rotated u/v → `bilinear`.
  - GOES and VIIRS: keep as vector footprints and let the likelihood intersect them with cells.
- For the frontend, export the forecast as a 4326 PNG or COG overlay plus GeoJSON contours.

```python
# unverified
import rioxarray, xarray as xr, numpy as np
from affine import Affine
from rasterio.enums import Resampling
T = Affine(60, 0, 381480, 0, -60, 3800280)
tmpl = xr.DataArray(np.zeros((512, 512), "float32"),
        coords={"y": 3800280 - 30 - 60*np.arange(512), "x": 381480 + 30 + 60*np.arange(512)},
        dims=("y", "x")).rio.write_crs(32611).rio.write_transform(T)
dem60  = dem.rio.reproject_match(tmpl, resampling=Resampling.average)
fbfm60 = lf.sel(band=1).rio.reproject_match(tmpl, resampling=Resampling.mode)
```

## 13. Risks

1. **GOES and HRRR latency are unmeasured.** Mitigation: the Saturday logger, and LastModified in the manifest.
2. **NB1 urban fuel makes Rothermel blind inside Altadena.** Decide a surrogate early.
3. **Pre-fire buildings and OSM** are hard to get because of Overture retention and post-fire edits.
4. **FIRMS MAP_KEY, LFPS email, and possibly the Census download** need a human with a laptop.
5. **GOES-East identity in Jan 2025** (G16 vs. G19) is unverified. List both buckets.
6. **The GOES terrain offset (~1 km) is comparable to the fire-front scale**, so apply the correction.
7. **Smoke and cloud at night:** DQF 2 pixels must be "no info", not "no fire".
8. **Evacuation zones:** we may have to make our own, labeled illustrative.

## Sources
- GEE GOES-18 FDCC catalog (mask, DQF, bands): https://developers.google.com/earth-engine/datasets/catalog/NOAA_GOES_18_FDCC
- NOAA G18 ABI FDC ReadMe: https://star.nesdis.noaa.gov/atmospheric-composition-training/documents/G18_ABI_FDC_ReadMe.pdf
- CIMSS LA fires blog (G18 meso 1-min from 1810 UTC Jan 7): https://cimss.ssec.wisc.edu/satellite-blog/archives/62559
- NGFS README (Feb 2025): https://bin.ssec.wisc.edu/pub/volcat/ngfs_docs/NGFS_README_Feb2025.pdf
- CIMSS NGFS: https://cimss.ssec.wisc.edu/ngfs ; NOAA Wildland Fire Data Portal: https://www.nesdis.noaa.gov/data-products-research-services/wildland-fire-data-portal
- FIRMS geostationary blog: https://www.earthdata.nasa.gov/news/blog/geostationary-active-fire-detection-data-firms
- FIRMS area API: https://firms.modaps.eosdis.nasa.gov/api/area/ ; data availability: https://firms.modaps.eosdis.nasa.gov/api/data_availability/
- FIRMS URT: https://www.earthdata.nasa.gov/learn/articles/firms-urt-data
- FIRMS attributes: https://earthdata.nasa.gov/data/tools/firms/active-fire-data-attributes-modis-viirs
- Herbie HRRR docs: https://herbie.readthedocs.io/en/stable/user_guide/background/model-info/hrrr.html (plus herbie-data 2026.9.2 source)
- goes2go 2025.10.0 source (product table incl. ABI-L2-FDCM, filename parsing)
- RRFS implementation SCN26-48: https://www.weather.gov/media/notification/pdf_2026/scn26-048_Updated_RRFS_and_REFS_Implementation.aaf.pdf
- NWS NODD AWS notes (SNS topics): https://www.weather.gov/media/wrn/NWS_NODD_AWS_Office_Hours_Notes.pdf
- Open-Meteo historical forecast API: https://open-meteo.com/en/docs/historical-forecast-api
- py3dep 0.19.0 source; AWS terrain tiles: https://registry.opendata.aws/terrain-tiles/ ; Terrarium formula: https://github.com/tilezen/joerd/blob/master/docs/formats.md
- rlandfire source (LFPS v2 endpoints): https://github.com/bcknr/rlandfire ; LF2024 release: https://landfire.gov/node/6080 ; LFPS products: https://lfps.usgs.gov/products
- Overture cloud sources: https://docs.overturemaps.org/getting-data/cloud-sources/ ; overturemaps CLI source (60-day retention)
- Microsoft building footprints: https://github.com/microsoft/GlobalMLBuildingFootprints
- TIGER 2020 record layouts (POP20/HOUSING20): https://www.census.gov/programs-surveys/geography/technical-documentation/complete-technical-documentation/tiger-geo-line.2020.html
- HIFLD Open discontinuation: https://www.napsgfoundation.org/hifld_open/ ; archive: https://source.coop/seerai/hifld
- CA evacuation aggregation layer: https://lab.data.ca.gov/dataset/california-evacuation-aggregation-layer
- FIRIS perimeters: https://catalog.data.gov/dataset/ca-perimeters-cal-fire-nifc-firis-public-view
- NASA FEDS (Jan 2025): https://maps.disasters.nasa.gov/ags03/rest/services/california_wildfires_202501/FEDS/MapServer/info/iteminfo ; SVS: https://svs.gsfc.nasa.gov/5558/
- LA County Eaton AAR timeline: https://file.lacounty.gov/SDSInter/lac/1192777_AAR_EatonFireSynopsisofFindingsandTimeline.pdf ; investigation report: https://file.lacounty.gov/SDSInter/lac/1208708_InvestigationReportoftheEatonFireEvacuationAlerts_05-18-26_.pdf
- Fire Engineering summary of FSRI timeline: https://www.fireengineering.com/firefighting/wildland-firefighting/report-eaton-fire-marched-toward-west-altadena-hours-before-official-accounts
- DINS 2025 Eaton: https://catalog.data.gov/dataset/dins-2025-eaton-public-view
