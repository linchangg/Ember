# T5: engine runtime core: clock, physics cache, summarizer

**Goal.** Three engine modules that the wave-2 loop and publisher (T10) are built on:
- `clock.py` tells the engine what sim time it is;
- `cache.py` stores and loads per-cycle physics outputs with a chained cache key;
- `summarize.py` turns forecast arrays into the exact files the dashboard draws (`arrival_q.bin`, perimeter, isochrones, danger polygons, stats).

**Follow `docs/briefs/README.md` §2 for all general rules.** Branch: `c-t5-runtime-core`.

## Read
- `spec/03_engine.md`: §1 (module list), §2 (clock), §4 (cache key, hit/miss), §5 "Per cycle / Also written", §6 (summarizer)
- `spec/00_contracts.md`: §1, §3.1 (`Cycle`, `CycleAssets`, `ClockState`), §3.2 (the clock formula), §4 (files: `index.json`, §4.2 `arrival_q.bin`, §4.3 perimeter/isochrones/danger), §6 (`ForecastArrays` fields; **read only, don't import**)
- `engine/emberline/scripts/make_fixture.py`: `encode_arrival`, `boundary_rings`, `vectorize_mask` and the code that writes the fixture's assets. Your summarizer must produce the **same formats**.

## You own
- `engine/emberline/runtime/__init__.py` (empty, 0 bytes)
- `engine/emberline/runtime/clock.py`, `engine/emberline/runtime/cache.py`, `engine/emberline/runtime/summarize.py`
- `engine/tests/test_clock.py`, `engine/tests/test_cache.py`, `engine/tests/test_summarize.py`

**Don't import `emberline.hazard`** (Abby's package, which may not exist yet). The summarizer takes any object with the `ForecastArrays` attribute names (duck typing). Tests use `types.SimpleNamespace`.

## Steps

1. `uv add numpy shapely pyproj rasterio`
2. **`runtime/clock.py`:**
   ```python
   def sim_now_ms(c: ClockState, wall_ms: int) -> int    # 00 §3.2: clamp(anchor_sim + (playing ? (wall − anchor_wall)·speed : 0), min, max); int()
   class SimClock:
       def __init__(self, state: ClockState | None = None): ...
       def update(self, state: ClockState) -> None       # called by the poller; if epoch/playing/speed/active branch changed -> self.wake()
       def sim_now(self) -> int                          # uses time.time()*1000
       def wake(self) -> None                            # sets an asyncio.Event
       async def wait_until_sim(self, t_ms: int) -> bool # returns True when sim_now >= t_ms; False if woken early
       # loop: while sim_now < t: if not playing -> wait for event (max 1 s); else sleep min(0.25, (t - now)/speed/1000); return False if event fired
   class FastClock:                                     # precompute: everything is "now"
       def sim_now(self) -> int: return 2**53
       async def wait_until_sim(self, t_ms: int) -> bool: return True
   ```
   `speed` is sim seconds per wall second, so sim-ms per wall-ms is also `speed`.
3. **`runtime/cache.py`.** Root defaults to `REPO / "cache"`; every function takes `root: Path` so tests use `tmp_path`.
   ```python
   @dataclass
   class PhysicsOutput:
       cycle: dict                 # Cycle fields except "source" (set at publish)
       zone_arr: np.ndarray        # (N, Z) float32
       area_arr: np.ndarray        # (N, 1) float32
       weights: np.ndarray         # (N,) float64
       obs_status: list[dict]      # [{obs_id, status: "assimilated"|"rejected", influence}]
       scans: list[dict]           # ScanEvent dicts
       events: list[dict]          # ActivityEvent dicts
       key: str
       cached: bool = False
   def cycle_key(prev_key: str | None, new_obs_ids: list[str], model_version: str,
                 config_hash: str | None, seed: int, filter_mode: str) -> str
       # sha256 hex of json.dumps([prev_key, sorted(new_obs_ids), model_version, config_hash, seed, filter_mode], separators=(",",":"))
   def cycle_dir(root, scn, branch, k_ms) -> Path       # root/scn/branch/k
   def save_cycle(root, scn, branch, k_ms, out: PhysicsOutput) -> None
       # physics.json = {cycle, obs_status, scans, events, key}; zone_arrivals.npz = zone_arr, area_arr, weights (np.savez_compressed)
   def load_cycle(root, scn, branch, k_ms, expect_key: str) -> PhysicsOutput | None
       # None if missing OR physics.json["key"] != expect_key  (a stale cache is a miss)
   def save_checkpoint(root, scn, branch, k_ms, blob: bytes) -> None   # root/scn/branch/checkpoints/{k}.npz (raw bytes from model.save_state)
   def latest_checkpoint(root, scn, branch, at_or_before_ms: int) -> tuple[int, bytes] | None
   def write_index(root, scn, branch) -> Path
       # 00 §4 index.json: {"scenario_id", "branch_id", "cycles": [{"knowledge_sim_ms": k, "bundle": f"cache/{scn}/{branch}/{k}/bundle.json"}]}
       # one entry per numeric k directory that contains bundle.json, sorted by k
   def briefing_path(root, scn, input_hash) -> Path     # root/scn/briefings/{hash}.json
   def save_briefing(root, scn, input_hash, briefing: dict) -> None
   def load_briefing(root, scn, input_hash) -> dict | None
   ```
   Write files atomically: write to `name.tmp`, then `os.replace`.
4. **`runtime/summarize.py`.** Every function takes `g`, the `grid` dict from `scenario.yaml` (`cell_m`, `nx`, `ny`, `x0`, `y0`, `crs`). For UTM↔lon/lat, use a module-level `pyproj.Transformer` (`always_xy=True`); don't import T0's `grid.py`, which may not be merged yet.
   ```python
   QUANTILES = (0.1, 0.3, 0.5, 0.7, 0.9)
   def encode_arrival(t_q: np.ndarray, horizon_min: int = 360) -> np.ndarray
       # 00 §4.2, matching make_fixture.encode_arrival exactly: uint8 filled with 255;
       # finite t <= 0 -> 0 (burned); 0 < t <= horizon -> clip(ceil(t/2), 1, 254); NaN, inf or t > horizon -> 255
   def mask_to_multipolygon(mask: np.ndarray, g, simplify_m: float) -> dict | None
       # rasterio.features.shapes(mask.astype(uint8), mask=mask, transform=Affine(cell, 0, x0, 0, -cell, y0)) -> shapely union
       # -> simplify(simplify_m) in UTM -> transform to lon/lat -> GeoJSON MultiPolygon dict (wrap a Polygon). None if empty.
   def front_lines(burned_mpoly_utm, burnable_unburned_mask, g) -> dict
       # MultiLineString: the burned boundary segments that touch burnable unburned cells.
       # Simple version: burned.boundary ∩ buffer(cell_m) of the unburned-burnable region; MultiLineString (possibly empty coords).
   def write_assets(arrays, out_dir: Path, rel_prefix: str, g, burnable: np.ndarray | None = None,
                    prev_burned_ha: float | None = None, cycle_min: int = 5) -> tuple[dict, dict, dict]
       # returns (assets: CycleAssets dict, danger: dict, stats: dict)
   ```
   **`write_assets`** follows `03` §6 exactly:
   - **`arrival_q.bin`:** `encode_arrival(arrays.t_q)` (shape `(5, ny, nx)`, C order, row 0 = north) written as raw bytes. **Assert `len == 5·ny·nx`.**
   - **`perimeter.geojson`:** a FeatureCollection with exactly two features:
     - `{"type": "Feature", "id": "burned", "properties": {"kind": "burned"}, "geometry": MultiPolygon}` from `p_burned_now ≥ 0.5`, simplified 30 m;
     - `{"id": "front", "properties": {"kind": "front"}, "geometry": MultiLineString}`.

     If nothing is burned, both geometries have empty `coordinates: []`.
   - **`isochrones.geojson`:** for `lead in 60, 120, …, 360`, take the polygon where `t_q[2] ≤ lead` (q = 0.5; NaN = not reached), and write its **exterior rings as LineStrings**: one Feature per lead, geometry MultiLineString, `properties {"lead_min": lead, "q": 0.5}`. Skip empty leads.
   - **`danger`:**
     - `{"lead60_p30": MultiPolygon of t_q[1] ≤ 60, "lead120_p30": MultiPolygon of t_q[1] ≤ 120}` (q = 0.3);
     - start at a 30 m simplify tolerance and double it until `len(json.dumps(each)) ≤ 10_000`;
     - an empty polygon is `{"type": "MultiPolygon", "coordinates": []}`.
   - **Stats:**
     - `burned_ha = Σ p_burned_now · cell_m²/1e4` (0.81 ha per cell at 90 m);
     - `front_km` = the length of `front` in UTM / 1000;
     - `growth_ha_per_h = (burned_ha − prev_burned_ha) · 60/cycle_min`, or 0.0 if prev is None;
     - `head_bearing_deg` is the bearing (degrees clockwise from north) from the centroid of the burned cells to the farthest cell with `t_q[2] ≤ 60`, or None if there is none or nothing is burned.
   - **`assets`** = `{"arrival_q": f"{rel_prefix}/arrival_q.bin", "perimeter": f"{rel_prefix}/perimeter.geojson", "isochrones": f"{rel_prefix}/isochrones.geojson"}`, where `rel_prefix` is `cache/{scn}/{branch}/{k}`.
   - Target: ≤ 0.3 s on a 340×340 grid. Measure it in a test and print the time; don't assert it.
5. **`engine/tests/test_clock.py`:**
   - `sim_now_ms`: paused → anchor; playing at 240× after 1000 wall-ms → anchor + 240000; clamped at min and max.
   - `wait_until_sim` with a fake playing clock at 900× returns True quickly for a target 1 sim-minute ahead.
   - `wake()` during a wait returns False.
6. **`engine/tests/test_cache.py`:**
   - `cycle_key` is deterministic and order-insensitive in obs IDs, and changes when any input changes; the chain differs if an earlier key differs.
   - `save_cycle`/`load_cycle` round-trip arrays bit-exactly (`np.array_equal`, same dtypes); a different `expect_key` gives None.
   - `latest_checkpoint` picks the newest ≤ t.
   - `write_index` lists only the dirs that have `bundle.json`, sorted.
   - Briefing save/load round-trips.
7. **`engine/tests/test_summarize.py`.** Use a synthetic 340×340 grid at 90 m (Eaton numbers) with an analytic ellipse: `t_q[q] = max(0, dist_m/ros_q − T)`, NaN beyond 360, and `p_burned_now = 1` where `dist < r0`.
   - `encode_arrival` agrees with `emberline.scripts.make_fixture.encode_arrival` on a random array that includes 0, negatives, NaN, inf, 0.1, 360 and 361. Importing the fixture function **in a test** is fine.
   - The `arrival_q.bin` length is 5·340·340, and it decodes back to within 2 min of `t_q`.
   - `perimeter.geojson` has exactly the ids `burned` and `front` with the right geometry types; the `burned` area in ha is within 5% of `burned_ha`.
   - `isochrones` leads are increasing, and each feature's `q == 0.5`.
   - `danger`: both keys exist, each ≤ 10 KB, and `lead60` ⊆ `lead120` (shapely `covers`, after `buffer(1e-9)`).
   - Nothing burned → empty geometries, `head_bearing_deg is None`, `burned_ha == 0`.
   - `head_bearing_deg` for an ellipse pushed toward 240° is within ±15° of 240.
8. `uv run pytest -q` passes. Paste the output and the measured summarize time.

## Done when
- All three test files pass.
- Formats match `00` §4 and the fixture byte-for-byte for `arrival_q`.
- The PR notes the summarize timing.

## Don't
- Write the loop, model host, publisher or sinks (wave-2 T10).
- Import `emberline.hazard`.
- Ship UTM coordinates in any GeoJSON. Everything you write is lon/lat.
