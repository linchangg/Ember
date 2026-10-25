# T7: engine data sources (`runtime/sources.py`)

**Goal.** The engine's inputs, released at their real availability times:
- `ReplaySource` turns the scenario's pre-built tables into `ObservationRecord`s and `ScanEvent`s for a time window.
- `InboxSource` holds accepted reports until the next cycle.
- `SyntheticSource` scripts fake scans for tests.
- `ForcingProvider` serves the newest HRRR run that was available at time *t*.

The core rule is **no future data**, enforced in code.

**Follow `docs/briefs/README.md` §2 for all general rules.** Branch: `c-t7-sources`.

## Read
- `spec/03_engine.md` §3 (all), §4 (the first lines: `weather_as_of` and the assertion), §14 test 3
- `spec/01_data_pipeline.md` §3 (the GOES pixel table columns and rules), §4 (VIIRS fields), §5 "Output" + "Honest replay rule", §8 (the replay manifest columns)
- `spec/00_contracts.md` §1 (the ID formats in the table), §3.1 (`ObservationRecord`, `ScanEvent`, `Footprint`, `PointRadius`), §6 (`Weather`)
- `docs/briefs/T6_hrrr.md`, the "Output format" section: the NetCDF format `ForcingProvider` reads

## You own
- `engine/ember/runtime/sources.py`
- `engine/ember/runtime/__init__.py` (empty, 0 bytes; T5 may create the same empty file)
- `engine/tests/test_sources.py`

The real input tables don't exist yet (wave-2 T8 and T13 write them). **Your tests build tiny tables in `tmp_path`** using exactly the column names below. The column lists below are the contract those tasks must follow, so put them in a module docstring at the top of `sources.py`.

## Input formats (from `01` §3, §4, §8; the docstring contract)
- **`replay_manifest.parquet`** (`01` §8): `entry_id, source, kind ("observation"|"forcing"), scan_start_ms, scan_end_ms, available_ms, latency_basis, local_path, rows, n_hot, provenance`. For observations, `local_path` points at the pixel table, relative to the scenario dir, and `entry_id` = the `scan_id` (GOES) or the pass id (VIIRS).
- **`obs/goes18_fdcc.parquet`** (`01` §3 table): `scan_id, sensor_id, scan_start_ms, scan_end_ms, created_ms, s3_last_modified_ms, available_ms, px_x, px_y, lon, lat, ring (list of [lon, lat]), mask, dqf, hot, clear, frp_mw, trust`.
- **`obs/scan_events.parquet`:** the `ScanEvent` field names minus `scenario_id`/`branch_id`.
- **`obs/viirs.parquet`:** `pass_id, sensor_id, row, t_obs_ms, available_ms, lon, lat, ring, trust, confidence, frp_mw`.

## Steps

1. `uv add pandas pyarrow xarray netcdf4`
2. **`ReplaySource`:**
   ```python
   class ReplaySource:
       def __init__(self, scn_dir: Path, scenario_id: str, assimilate_sensors: list[str]): ...
           # loads the manifest + pixel tables + scan_events once (pandas); sorts by available_ms
       def collect(self, a_ms: int, b_ms: int) -> tuple[list[ObservationRecord], list[ScanEvent]]
   ```
   - **Window:** observation manifest rows with `a_ms < available_ms ≤ b_ms` (half-open; `a` excluded, `b` included).
   - **GOES:** for each scan in the window, one `ObservationRecord` per pixel row where `hot or clear`; rows that are neither are dropped.
     - `obs_id = f"obs_{sensor_id}_{scan_id}_{px_x}_{px_y}"`
     - `kind = "goes_pixel"`
     - `observed_sim_ms = scan_start_ms`, `available_sim_ms = available_ms`
     - `geometry = Footprint(ring=...)`, `hot = hot`
     - `footprint_key = f"goes18:{px_x}:{px_y}"`
     - `fdc_class = mask`, `frp_mw`, `trust` from the table
     - `provenance = "real"`, `source_ref = scan_id`
   - **ScanEvent:** one per scan, from `scan_events.parquet`, with `assimilated = sensor_id in assimilate_sensors`. Scans from sensors **not** in `assimilate_sensors` still produce their ScanEvent (display-only) but **no** observations.
   - **VIIRS** (only if `viirs.parquet` exists):
     - one record per row: `obs_id = f"obs_{sensor_id}_{t_obs_ms}_{row}"`, `kind = "viirs_pixel"`, `hot = True`, `confidence` and `trust` from the table;
     - one `ScanEvent(mode="pass")` per `pass_id`: `n_hot` = the row count, `n_clear = 0`, `n_pixels_aoi` = the row count.
   - **Assertion (`03` §3):** every returned record and scan has `available_sim_ms ≤ b_ms`. Raise `AssertionError(f"future data: {obs_id} available {x} > {b}")`. It can never fire for correct tables; it exists to catch a doctored manifest (test below).
   - Helper: `def hot_only(obs) -> list[ObservationRecord]`. Clear pixels go to physics but are **never published**, so the publisher calls this.
3. **`InboxSource`:**
   ```python
   class InboxSource:
       def add(self, rec: ObservationRecord) -> None
       def drain(self, k_ms: int) -> list[ObservationRecord]   # returns + removes records with available_sim_ms <= k_ms (assert none are later)
       def clear(self) -> None                                  # used on reset
   ```
4. **`SyntheticSource`** (tests only; same `collect` signature):
   - Its constructor takes a list of `(scan_start_ms, [(lon, lat, hot), ...])`.
   - Each scan becomes `available = scan_start + 3 min`, with square ~2 km footprints around each point.
   - IDs follow the GOES formats, with `sensor_id = "synthetic_goes"` and `provenance = "simulated"`.
5. **`sensor_status(scans_so_far: list[ScanEvent], k_ms: int, enabled: dict[str, bool]) -> list[SensorInfo-delta dicts]`** (`03` §3 "Sensor status"):
   - GOES is `stale` if no GOES scan has `available_sim_ms` within 15 sim-min before `k`, and `down` if disabled;
   - VIIRS is always `ok`, with `last_data_sim_ms` = the last pass.

   Return only `sensor_id`, `status` and `last_data_sim_ms`.
6. **`ForcingProvider`.** **Write this last, only once `engine/ember/hazard/api.py` is on `main`** (Abby's A0 creates it). Check with `git fetch && git ls-tree origin/main engine/ember/hazard/api.py`. If it isn't there when you reach this step, ship everything else, mark this step "blocked on A0" in the PR, and stop. **Don't create `hazard/api.py` yourself.**
   ```python
   @dataclass
   class WeatherAsOf:
       weather: Weather; init_ms: int; available_ms: int
   class ForcingProvider:
       def __init__(self, forcing_dir: Path, t_ref_ms: int, horizon_min: int = 360): ...   # index files by attrs init_ms, available_ms_by_fxx[0]
       def weather_as_of(self, t_ms: int) -> WeatherAsOf
   ```
   - **Choose** the newest init with `available_ms_by_fxx[0] ≤ t_ms`. Use only the `fxx` bands whose own `available_ms_by_fxx[f] ≤ t_ms`. Never return a band that wasn't available.
   - **Build `Weather`:**
     - `t_min` = `(init_ms + 3600000·fxx − t_ref_ms)/60000` as float32, ascending;
     - `u10`, `v10` as `(B, H, W)` float32;
     - `temp_c` and `rh` as `(B,)`.
   - **Assert** that `available_ms ≤ t_ms`.
   - **Horizon:** if the last band's `t_min` is less than `t_now_min + horizon_min`, log a warning once per init (`01` §5: "hold the last band"). Physics already holds the last band, so no padding is needed.
   - Cache the loaded arrays per init.
7. **`engine/tests/test_sources.py`.** Build tiny tables in `tmp_path` with the exact columns above: 3 GOES scans, 4 pixels each (2 hot, 1 clear, 1 neither), plus one display-only `goes18_fdcm` scan.
   - `collect(a, b)` returns only the scans with `a < available ≤ b`, including exactly-at-`b` and excluding exactly-at-`a`.
   - The neither-hot-nor-clear pixel is dropped; clear pixels are returned with `hot=False`; `hot_only` filters them.
   - The `obs_id` and `footprint_key` formats are exact.
   - The FDCM scan → a ScanEvent with `assimilated=False` and no observations.
   - **`03` §14 test 3:** doctor the pixel table so one row's `available_ms` is later than the manifest's. `collect` raises `AssertionError` mentioning "future data".
   - **InboxSource:** `drain` returns only due records, and a second `drain` returns nothing.
   - **SyntheticSource:** produces valid `ObservationRecord`s (`model_validate` round-trip).
   - **`sensor_status`:** stale after 15 sim-min with no scan, ok otherwise, down when disabled.
   - **ForcingProvider** (only if `hazard/api.py` exists; otherwise `pytest.importorskip("ember.hazard.api")`): write 2 fake NetCDF inits in T6's format with staggered `available_ms_by_fxx`, then check:
     - at a time between their availabilities, the older init is chosen and only its available bands appear;
     - later, the newer init is chosen;
     - before either, it raises `LookupError`.
8. `uv run pytest -q` passes.

## Done when
- The tests pass.
- The column contract is in the `sources.py` docstring.
- The PR says whether `ForcingProvider` is done or blocked on A0.

## Don't
- Read anything outside `scn_dir` / `forcing_dir`, or touch the network.
- Publish clear pixels anywhere.
- Change contracts to fit a table. If a field doesn't fit, stop and report.
