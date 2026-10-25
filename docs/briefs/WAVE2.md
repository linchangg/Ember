# Wave 2: shared rules, interface contracts and task briefs

Written Sat Oct 24 2026, evening, after wave 1 landed on `main` (`8cea5fc`). One checkout, branch `main`. Agents edit **only** the paths their task owns and **never commit**. The orchestrator reviews, runs the suite and commits.

## 0. Rules for every wave-2 agent

1. **Read these, in order:**
   - `AGENTS.md`;
   - `SPEC.md` §2;
   - `spec/00_contracts.md` §1, then the sections your task names;
   - this file's §1 (contracts) and your task section;
   - the existing code your task names.
2. **Edit only "You own" paths.** Never `git checkout`, `stash`, `reset`, `commit`, `push` or branch. Other agents are writing other files in this same tree. If something outside your paths must change, stop and report it.
3. **Dependencies are pre-installed.** That includes `fastapi`, `uvicorn`, `osmnx`, `geopandas`, `pandas`, `pyarrow`, `rasterio`, `rioxarray`, `py3dep`, `httpx`, `anthropic`, `herbie-data`, `s3fs` and `netcdf4`. **Never run `uv add`, `uv lock` or `uv sync`.** If a package is missing, stop and report it.
4. **Commands:**
   - Run from `engine/` with `PYTHONUTF8=1`.
   - Run **only your own test files**: `uv run --no-sync pytest -q tests/test_<yours>.py`. The orchestrator runs the full suite; concurrent full runs race on the fixture files.
5. **Network.** Network tests are skipped unless `EMBERLINE_NETWORK_TESTS` is set. Real data downloads go under `data/scenarios/eaton/raw/`, which is git-ignored.
6. **Hub.**
   - The local hub (`spacetime start`, :3000) is shared. Don't republish the `stdb/` module.
   - Hub tests use your own `scenario_id`, never `fixture`, and are skipped unless `EMBERLINE_HUB_TESTS=1`.
7. **Honesty.**
   - Never fabricate data, coordinates, dates or citations.
   - Anything hand-picked or approximate is labelled as such in the data (`approximate: true`, `"note"`) and in your report.
   - No future data: anything used at sim time `k` has `available ≤ k`, and that's asserted in code.
8. **Secrets.**
   - Read them only from env or `engine/.env`.
   - Never print a key, token, PIN or phone number; mask all but the last 4 characters.
9. **Style.** Python 3.12, typed, small functions; Pydantic contract types from `emberline.contracts`. Match `engine/emberline/scripts/make_fixture.py` and the wave-1 modules.
10. **Final reply:**
    - the 5-line summary from `AGENTS.md`;
    - files created or changed;
    - pasted test output;
    - every assumption or deviation;
    - one `process.md line:`.

## 1. Interface contracts between wave-2 tasks (binding; don't change them)

### 1.1 Cartesia call: T11 implements, T10 calls (`engine/emberline/notify/cartesia.py`)
```python
@dataclass
class CallResult:
    ok: bool
    call_id: str | None
    call_status: str          # "placed" | "unconfigured" | "error" | "dry_run" | "offline"
    error: str | None = None
def configured() -> bool                          # CARTESIA_API_KEY, CARTESIA_AGENT_ID, CARTESIA_FROM_NUMBER_ID, CARTESIA_VERSION all set
def mask(number: str) -> str                      # "•••• 4821"
def dynamic_variables(alert: Alert, tz_name: str = "America/Los_Angeles") -> dict[str, str]   # alert_id, voice_script, zones_spoken, act_by_spoken, p_words, issued_spoken
def build_request(alert: Alert, to_number: str) -> dict   # exact POST body, 05 §4 "Placing the call"
async def place_call(alert: Alert, to_number: str, *, dry_run: bool = False,
                     client: httpx.AsyncClient | None = None) -> CallResult   # never raises
```

### 1.2 Local API: T12 implements, T10's `run.py` mounts it (`engine/emberline/api/local.py`)
```python
@dataclass
class LocalState:
    scenario_id: str
    profile: str                                  # "online" | "offline"
    fixtures: bool                                # serve fixtures/{static,cache} instead of data/ + cache/
    duty_officer_number: str | None = None        # set by POST /dev/duty-officer; memory only
    seek_presets: dict[str, int] = field(default_factory=dict)   # name -> sim_ms
    status: dict = field(default_factory=dict)    # engine writes: last_cycle_sim_ms, physics_ms, hub_ok, cache_coverage
def create_app(state: LocalState, hub: "StdbHttp | None") -> fastapi.FastAPI
```

### 1.3 Hazard stand-in (T10, tests only)
`engine/emberline/hazard/` doesn't exist, because Abby's A0 hasn't landed. **Nobody creates it.**
- T10's engine code imports `from emberline.hazard.api import ...` lazily, inside functions or under `TYPE_CHECKING`, so the package still imports without it.
- T10's tests put a stand-in into `sys.modules["emberline.hazard"]` and `sys.modules["emberline.hazard.api"]`, but only if the real import fails. The stand-in lives in `engine/tests/hazard_standin.py` and holds the `spec/00` §6 dataclasses verbatim plus a `FakeHazardModel`.
- When A0 lands, the real module wins automatically.

### 1.4 Replay inputs: T8 writes them, T7's `ReplaySource` reads them
Column names and paths are **exactly** the docstring at the top of `engine/emberline/runtime/sources.py`. Don't edit `sources.py`.

### 1.5 Event IDs
Engine-made activity events use `ev_{cycle_id}_{i}`. `cycle_id` already carries the branch, so branch events never collide with main's. Pre-ignition bundles use `ev_{bundle_id}_{i}`.

## 2. Task briefs

### F0: fix the fixture's R2 boundary bug (runs first, alone)
- **Owns:**
  - `engine/emberline/scripts/make_fixture.py`
  - `fixtures/**` (regenerated)
  - `engine/tests/test_fixture.py`
  - the fixture cross-check in `engine/tests/test_alerts.py`
  - `engine/tests/data/briefing_inputs/*.json` (regenerate if they change)
  - any fixture cycle numbers hard-coded in `engine/tests/test_hub.py` and `engine/tests/test_briefing.py`
- **Bug.**
  - `solve_along` bisects until the zone sits exactly on the R2 boundary.
  - `rule_holds` then evaluates "act-by within 15 min" on float minutes (`q30 = 205.00000000000045`).
  - The committed `zone_risk` rows store act-by in whole ms, so the act-by is exactly 15.0 min one cycle earlier.
  - `risk/alerts.py` (spec "≤ 15, including") fires R2 at cycles 29 and 31 for FIX-Z3 and FIX-Z2. The fixture puts them at 30 and 32.
- **Fix:**
  1. Make `rule_holds` (and everything that decides fixture alerts) use the **same integer-ms act-by** as `zone_risk_row`.
  2. After bisection, move each zone a safe margin off the boundary (e.g. tens of metres inward) so float noise can't flip a cycle.
  3. Keep `00` §10's pins: **FIX-Z2 crosses R1 at cycle 20 and FIX-Z3 crosses R2 at cycle 30.**
  4. Regenerate the fixture with `uv run --no-sync python -m emberline.scripts.make_fixture`.
  5. The fixture must stay deterministic (run it twice and diff) and under 10 MB.
- **Then:**
  - Simplify the `test_alerts.py` cross-check to `ours == committed`, with the exact list updated to whatever the fixed fixture produces.
  - Update `test_fixture.py`'s pinned expectations to match. Keep the cycle-20 and cycle-30 pins.
  - Regenerate T4's saved briefing inputs if their source cycles changed. The 18/20/30 alerts must still exist; if one moved, use its new cycle and rename the file.
- **Done when:** these all pass:
  ```bash
  uv run --no-sync pytest -q tests/test_fixture.py tests/test_alerts.py tests/test_risk.py tests/test_briefing.py tests/test_validate.py
  ```
  Then also run `EMBERLINE_HUB_TESTS=1 uv run --no-sync pytest -q tests/test_hub.py` (the local hub is running). Report the fixed fixture's full alert list.

### T4L: live Claude via OpenRouter, plus de-duplicate `p_words`
- **Owns:**
  - `engine/emberline/ai/**`
  - `engine/tests/test_briefing.py`, `engine/tests/test_validate.py`
  - `.env.example`: add variable **names** only, never values.
- **Context.**
  - `engine/.env` holds `OPENROUTER_API_KEY`, `ANTHROPIC_BASE_URL=https://openrouter.ai/api` and `EMBERLINE_BRIEFING_MODEL=anthropic/claude-sonnet-5.5`.
  - Verified by the orchestrator: the Anthropic Python SDK with `base_url=ANTHROPIC_BASE_URL` and `auth_token=<OpenRouter key>` accepts the exact briefing request shape. That's `output_config.format` json_schema plus `thinking={"type":"between_tools"}`; the response is one `text` block of JSON.
  - The key has **$5** of credit.
- **Do:**
  1. **Config.** Make the client and model configurable, in this order:
     - `EMBERLINE_BRIEFING_MODEL`, defaulting to `claude-sonnet-5-5`.
     - The client: `anthropic.AsyncAnthropic(base_url=os.environ.get("ANTHROPIC_BASE_URL"), auth_token=OPENROUTER_API_KEY if set, else api_key=ANTHROPIC_API_KEY)`.
     - `Briefing.model` records the model actually used.
     - Keep the `.env` loader non-overriding.
  2. **De-duplicate.** `ai/validate.py` imports `P_WORDS`/`p_words` from `emberline.risk.alerts` instead of keeping its own copy. Don't edit `risk/alerts.py`.
  3. **Live runs.** Run the live smoke test (`EMBERLINE_LIVE_TESTS=1`, 3 inputs) and the CLI (`python -m emberline.ai.briefing --fixture-cycle 20`).
     - **Make no more than 8 live calls in total.** Print `usage` tokens for each.
     - Report grounded/fallback per input and the validator failures verbatim.
     - If fewer than 2 of 3 validate, tighten `prompts/briefing_v1.md` (bump `PROMPT_VERSION` to `briefing_v2` if the prompt changes) and re-run, within the 8-call cap.
  4. **Never let Claude produce numbers.** The validator still gates everything.
- **Done when:**
  - The offline tests pass.
  - The live smoke test has ≥ 2 of 3 grounded, with the output pasted.
  - The PR notes include the total tokens and estimated cost.

### T8: GOES-18 pixel table, scan events, replay manifest
- **Owns:**
  - `engine/emberline/data/goes.py`: append only; don't change the T1 functions' behaviour.
  - `engine/emberline/data/manifest.py`
  - `engine/tests/test_goes_table.py`, `engine/tests/test_manifest.py`
- **Outputs:**
  - `data/scenarios/eaton/obs/goes18_fdcc.parquet`
  - `data/scenarios/eaton/obs/scan_events.parquet`
  - `data/scenarios/eaton/replay_manifest.parquet`
  - `data/scenarios/eaton/truth/latency.json`
- **Read:** `spec/01` §3 and §8 (columns, rules, availability), `runtime/sources.py`'s docstring (**the column contract**), and `data/goes.py` plus `scripts/check_goes.py` from T1.
- **Reuse.** The 168 cropped scans are already cached as `data/scenarios/eaton/raw/goes/*.npz`. Reuse T1's reader, parallax correction and footprint code, and the DEM at `raw/dem_parallax.tif`. Fetch S3 `LastModified` for each key (anonymous `s3fs` `info()`) for the §8 availability rule.
- **Pixel rows.**
  - Write every pixel whose footprint intersects the AOI.
  - Apply the §3 rules: drop DQF 2–5; `hot` = mask in `GOES_FIRE`, plus 15/35 at trust 0.3; `clear` = DQF 1; trust comes from the §3 class table.
  - Rings and centres are parallax-corrected.
- **Scan IDs.** `scan_id = g18c_{scan_start:%Y%m%dT%H%M%S}`. `ScanEvent` fields come per the docstring, with `mode = "CONUS"` and `n_pixels_aoi`, `n_hot` and `n_clear` from the rows.
- **Manifest.**
  - One observation row per scan.
  - HRRR forcing rows from `data/scenarios/eaton/forcing/*.nc` attributes (`init_ms`, `available_ms_by_fxx`), if present. This is one row per init, with `available_ms` = fxx-0's; say so.
  - Sorted by `available_ms`; must cover 02:00–15:00Z.
- **`truth/latency.json`:** median and p90 of `available − scan_end`, in minutes.
- **Tests:** `01` §12 test 4 (no DQF 2–5; `hot` matches mask classes; `available_ms ≥ scan_end_ms`) and test 7 (sorted; coverage).
- **Round-trip test:** `ReplaySource(scn_dir, "eaton", ["goes18_fdcc"]).collect(a, b)` on the real outputs returns records whose `obs_id` and `footprint_key` formats match the contract, with no future data.

### T9a: zones, exposure, assets, egress, tiers, zone raster, spawn points
- **Owns:**
  - `engine/emberline/data/vectors.py`, `engine/emberline/data/exposure.py`
  - `engine/tests/test_zones.py`
- **Outputs:**
  - `data/scenarios/eaton/vectors/{zones,assets}.geojson`
  - `data/scenarios/eaton/static/zone_index.tif`
  - `data/scenarios/eaton/web/{zones,assets}.geojson`
  - `data/scenarios/eaton/web/spawn_points.json`
- **Read:** `spec/01` §6 "Zones", "Exposure" and the spawn-points bullet, `spec/00` §4.3 and §7 (tiers), `research/06` §2 (zone names), `research/01` §8–9.
- **Zones.**
  - Use the §6 source order and stop at the first that works.
  - If you fall back to Census block groups, set `approximate: true` on every zone and name the 13 west-Altadena zones exactly as listed in `01` §6.
  - Target 20–60 zones in the AOI.
- **Population and housing.**
  - Prefer the Census TIGERweb REST 2020 blocks layer queried by bbox (it has POP100 and HU100) over downloading the statewide `tl_2020_06_tabblock20` zip. Say which you used.
  - Area-weight blocks into zones.
- **Assets.** Use OSM through `osmnx` with the §6 tags. `n_critical` = hospitals + nursing/assisted living + schools. Join `zone_id` by point-in-polygon.
- **Egress.** `egress_roads` per §6 (drivable roads crossing the zone boundary).
- **Tiers.** Apply the §6/`00` §7 tier rules and `evac_min_default`, and **print the tier histogram**.
- **Zone raster.** `zone_index.tif` is int16 (−1 = none), on the model grid, built with `emberline.data.grid.template`.
- **Spawn points.** Write 6 `demo` points `{spawn_id, lon, lat, zone_id, street}`:
  - on real OSM residential street segments;
  - inside west-Altadena at-risk zones, not in canyons;
  - with the street name taken from OSM.
  - **Flag them in your report for Cody to eyeball on a map.**
- **Feature properties** must match `ZoneFeatureProps` (`00` §3.3) exactly.
- **Tests** (offline, against the written files):
  - `01` §12 test 6: ≥ 13 west-Altadena zones, or the approximate flag set; population within 20% of the clipped block total;
  - every zone has a valid tier and `evac_min_default`;
  - `zone_index.tif` matches the template's shape and CRS;
  - spawn points fall inside their zones.

### T9b: story geometry, terrain, web manifest, sensors, satellites, timeline
- **Owns:**
  - `engine/emberline/data/story.py`, `engine/emberline/data/terrain.py`, `engine/emberline/data/webpack.py`, `engine/emberline/data/truth.py`
  - `engine/tests/test_webpack.py`
- **Outputs:**
  - `data/scenarios/eaton/vectors/{lake_ave,area_x}.geojson`
  - `data/scenarios/eaton/static/area_x_index.tif`
  - `data/scenarios/eaton/web/{manifest.json,terrain_elev.png,terrain_tex.jpg,sensors.json,satellites.json,timeline.json,area_x.geojson}`
  - `data/scenarios/eaton/truth/timeline.json`
- **Read:**
  - `spec/01` §6 "Story geometry", §7, §9 and §10
  - `spec/00` §4.1 (the `ScenarioManifest`; **every field**) and §4.3
  - `research/06` §2
- **Lake Ave.** The OSM `North Lake Avenue` centreline, clipped to the AOI. Assert that it sits near lon −118.1316.
- **Area X.** `area_x.geojson` from `scenario.yaml`, plus `area_x_index.tif` (int8) on the template grid. Reuse `emberline.data.area_x.area_x_polygon`.
- **Terrain.**
  - 10 m DEM via `py3dep`, reprojected to EPSG:4326 over `aoi_bbox` at about 2048².
  - Encode it as Terrarium PNG exactly per §7.
  - The texture comes from the USGS imagery export URL in §7, with the night grade.
  - **Pre-fire check:** try to establish the acquisition date. If you can't prove it's ≤ 2024, set `texture_date` to `"unverified (check pre-fire)"` and say so. Never claim it's pre-fire without evidence.
- **Satellites.** `satellites.json`: GOES-18 at 137.2°W and GOES-16 at 75.2°W. LEO TLEs come from CelesTrak for NORAD 37849, 43013 and 54234; verify that the IDs and names match.
- **Sensors.** `sensors.json` holds `SensorInfo` rows for the `assimilate_sensors` in `scenario.yaml`.
- **Timeline.**
  - `timeline.json`: the `01` §9 table, with PST converted to UTC ms.
  - `as_observation` rows get lon/lat from OSM street intersections, with `"approximate": true` and a `note`. If an intersection can't be found, leave out lon/lat.
- **Manifest.**
  - `manifest.json` must have every `00` §4.1 field: grid corners from `grid.json`, `lake_ave` coordinates, `is_fixture: false`, `config_hash: null`, honesty from `scenario.yaml`, and `scans_dir: null`.
  - `static.*_url` paths look like `static/eaton/zones.geojson`. T9a writes the zones, assets and spawn files; just reference them.
- **Tests:**
  - `manifest.json` has every key and type in `00` §4.1, checked by comparing against the `ScenarioManifest` field list copied from the spec;
  - the Terrarium decode of a known pixel matches the DEM within 0.5 m;
  - Lake Ave lon ≈ −118.1316 ± 0.003;
  - `area_x_index.tif` has the template's shape and a non-zero count.

### T10: engine M1, fake physics to cache, publisher and hub
- **Owns:**
  - `engine/emberline/runtime/{model_host,loop,publisher}.py`
  - `engine/emberline/sync/{sink,stdb_sink,cache_sink,poller}.py`
  - `engine/emberline/scripts/precompute.py`, `engine/emberline/run.py`
  - `engine/tests/hazard_standin.py`, `engine/tests/test_engine.py`
- **Read:**
  - `spec/03` §1–8, §10 table, §12–14
  - `spec/00` §3–4, §6 and §8
  - `spec/04` §3
  - the existing modules: `runtime/{clock,cache,summarize,sources}.py`, `risk/{risk,alerts}.py`, `ai/briefing.py`, `sync/stdb_http.py`
  - §1 contracts above
- **Build:**
  - **`model_host.py`.** Owns the model: `init` from an `Ignition`; checkpoints every 30 sim-min; `ensure_state` = restore the newest checkpoint ≤ k−5 and roll forward deterministically.
  - **`loop.py`.**
    - `k = replay_start + n·5 min`.
    - Pre-ignition bundles have `cycle = None`.
    - `physics_cycle` per `03` §4, with `assert weather.available_ms ≤ k` and no-future-data asserts on observations.
    - The chained cache key comes from `runtime/cache.py`. A hit loads from the cache; a miss calls `ensure_state` and then steps.
  - **`publisher.py`.** `build_bundle(phys, hub_snapshot, ...) -> CycleBundle`, per `03` §7:
    - `risk.zone_risks` → `alerts.evaluate` → `apply_open_orders` → `people`;
    - briefing-cache lookup by `ai.briefing.input_hash`. No Claude call in the publish path; online live briefings run as a background task that calls `upsert_alert_draft`;
    - `hot_only` observations, `sensor_status`, event IDs per §1.5;
    - critical types limited to hospital, nursing home, assisted living and school, in readable form.
  - **`sync/sink.py`.** The `StateSink` Protocol (`03` §12) and a `HubSnapshot` dataclass.
  - **`stdb_sink.py`.** Wraps `StdbHttp`. A final failure becomes an `error` event, and **the loop never crashes**.
  - **`cache_sink.py`.** Writes `cache/{scn}/{branch}/{k}/bundle.json` plus `index.json` (the format the fixture uses), so file mode works.
  - **`poller.py`.**
    - Polls at 500 ms while playing and 1 s while paused.
    - Dispatches the `03` §10 table: clock commands; seek forward = catch-up batches of ≤ 10; seek back = rewind; reset; `alert_approved` → `notify.cartesia.place_call` (§1.1), or `failed`/`offline` when offline or unconfigured; `alert_declined`; `evac_min` = republish the current cycle; `person_joined`.
    - It marks each command done. A `report` command is marked `failed` with `{"reason": "report branching lands in wave 3"}`; say so.
  - **`scripts/precompute.py`.** `--scenario --branch --filter`, using `FastClock` and `CacheSink`.
  - **`run.py`.**
    - `--scenario --profile online|offline [--fixtures]`.
    - Loads `engine/.env` and builds the `load_scenario` payload from static files (same shape as `tests/test_hub.py`'s `load_payload`).
    - Runs the poller, loop and publisher, plus the T12 app through uvicorn on :8787 (§1.2).
- **Real Eaton physics** waits on A0. `run.py` and `precompute.py` must fail with a clear message if `emberline.hazard.api` is missing outside tests.
- **Tests:**
  - **`hazard_standin.py`.** The `00` §6 dataclasses verbatim, plus `FakeHazardModel`: 16 members, analytic ellipse, uniform weights, `filter_mode` `"none"`, bit-exact `save_state`/`load_state`, and `region_arrivals`.
  - **`test_engine.py`.** `03` §14 tests 1–5, using `SyntheticSource`, the stand-in, an in-memory fake sink and a flat synthetic scenario in `tmp_path`:
    1. Precompute 40 cycles; replay publishes each cycle once; seek +3 h then −2 h leaves exactly the rows ≤ +1 h. This one's against the fake sink, plus a hub-gated variant on scenario `synth`.
    2. Determinism and cache miss after hits: identical keys and byte-identical `arrival_q.bin`; delete cycle 25 and it's restored from a checkpoint byte-for-byte.
    3. No future data: the assertion fires on a doctored source.
    4. Alerts: covered by T3; add one publisher-level check that a new proposal appears once and the supersede flows through.
    5. Operator edits survive replay: `evac_min` changes act-by in the next 3 cached cycles.

### T11: Cartesia outbound call, with dry run
- **Owns:**
  - `engine/emberline/notify/__init__.py` (empty), `engine/emberline/notify/cartesia.py`
  - `engine/tests/test_cartesia.py`
- **Read:** `spec/05` §4 (all of it) and §7, and §1.1 above.
- **Verify the request shape** against `docs.cartesia.ai` (outbound calls: endpoint, body, the `Cartesia-Version` header, per-call errors in a 200 response). Quote the doc URL in a code comment. If the docs differ from `05` §4, follow the docs and report the difference.
- **No real calls.** There are no Cartesia keys on this machine.
- **CLI:**
  ```bash
  python -m emberline.notify.cartesia --dry-run --fixture-alert 20 --to +15555550100
  ```
  It prints the exact body, with the number masked in the printout only.
- **Tests:**
  - `build_request` shape;
  - `dynamic_variables` uses spoken PST times and `p_words` from `risk.alerts`;
  - a 200 response with per-call errors gives `ok=False`;
  - unconfigured env gives `unconfigured`;
  - `httpx.MockTransport` for success, 4xx and network errors;
  - `place_call` never raises;
  - masking.

### T12: local API on :8787
- **Owns:**
  - `engine/emberline/api/__init__.py` (empty), `engine/emberline/api/local.py`
  - `engine/tests/test_local_api.py`
- **Read:** `spec/03` §11, `spec/00` §4 (ASSET_BASE layout) and §1.2 above.
- **Endpoints:**
  - **`GET /cache/{path}`:** from `cache/`, or `fixtures/cache/` with `fixtures=True`. Gzip, `Cache-Control: immutable`. Reject `..` path traversal.
  - **`GET /static/{scn}/{path}`:** from `data/scenarios/{scn}/web/`, or `fixtures/static/{scn}/` with `fixtures=True`.
  - **`GET /healthz`:** profile, `hub_ok`, cache coverage, last cycle and physics ms from `state.status`.
  - **`POST /dev/duty-officer {number}`:** E.164 check; stored in memory only; returns the masked number.
  - **`POST /dev/report {lon, lat, kind, observed_sim_ms}`:** `hub.submit_report_engine` with `channel` `dashboard`, `trust` 0.5, `provenance` `injected`, `report_id` `rep_{uuid8}`. A 503 if there's no hub.
  - **`POST /dev/seek-preset {name}`:** `clock_seek` with `OPERATOR_PIN` from env.
- **CORS:** `http://localhost:*` only. Bind `127.0.0.1` by default.
- **Tests** (FastAPI `TestClient`, a fake hub object, fixture mode):
  - `index.json` and a `bundle.json` are served;
  - `arrival_q.bin` comes back with the right bytes;
  - traversal is blocked;
  - the duty-officer number never appears in any response or log;
  - `/dev/report` calls the hub with the right payload.

## 3. Results (Sat evening; committed to main one commit per task)

The workflow ran 34 agents: 8 builders, 16 reviewers, 8 fixers, 1 integrator and 1 critic. None failed. The orchestrator then re-verified everything and applied 4 fixes.

**Tests:**
- full suite: **188 passed, 18 skipped**;
- hub plus engine suites: **26 of 26** (`EMBERLINE_HUB_TESTS=1`), stable across repeated runs.

**OpenRouter spend:** **$0.05** of $5.

| Task | Result |
|---|---|
| F0 | The fixture judges R2 on the integer-ms act-by, and alert zones sit about 52 m off the firing boundary. Alerts are unchanged (18, 20, 30, 32) and the cross-check is exact (`ours == committed`). Deterministic, 4.5 MB. |
| T4L | Model, URL and key come from env (OpenRouter). Prompt is `briefing_v2`. `p_words` is imported from `risk.alerts`. **Live: c18, c20 and c30 all validated** (c30 and the CLI were re-run by the orchestrator). |
| T8 | `goes18_fdcc.parquet` has 21,026 pixel rows (1,453 hot). There are 168 scan events, and the replay manifest has 190 rows (168 GOES plus 22 HRRR). `ReplaySource` round-trips with no future data. |
| T9a | 131 real county zones (LA County "Evacuation Polygon" layer), 237 OSM assets, egress, tiers 13/83/35, `zone_index.tif`, and 6 spawn points. |
| T9b | Lake Ave, the area X raster, Terrarium terrain plus texture, `manifest.json` (every §4.1 field), sensors, satellites (verified TLEs) and the timeline. |
| T10 | Model host, loop, publisher, sinks, poller, `precompute.py` and `run.py`. All of `03` §14 tests 1–5 pass against the hazard stand-in. Real Eaton physics waits on A0. |
| T11 | Cartesia request builder, dry-run CLI and `place_call` (never raises). No real call made; there are no keys. |
| T12 | The `:8787` FastAPI app (`/cache`, `/static`, `/healthz`, `/dev/*`), with traversal blocked and the number never echoed. |

**Orchestrator fixes after the workflow:**
1. **`test_engine::test_1_hub_variant` race (failed about half the time).** The counting sink recorded a bundle before the hub call returned.
2. **`test_hub::test_5` cross-process clock skew (failed about 1 in 12).** It compared Python's clock with the hub's at a 1 sim-ms tolerance; it now uses a hub-only check plus 50 ms of skew.
3. **Honesty:** `manifest.json` claims a source as real, and `sensors.json` shows a sensor live, only when its data is in the package. VIIRS and LANDFIRE are now dropped from `real`, and the VIIRS sensors show `down`.
4. **Live checks:** c30 (order level) and the CLI were run once each; both validated.

**Security note.** This Claude Code shell exports `ANTHROPIC_BASE_URL=https://api.anthropic.com`. In T4L's first live run that overrode `engine/.env`, so the OpenRouter key was sent once to `api.anthropic.com` and rejected (401). `make_client()` now forces OpenRouter's URL whenever `OPENROUTER_API_KEY` is set. **Rotate the key after the hackathon** (it was also pasted in chat).

**Open decisions:**
1. **Zone set (honesty). Disclosed:** the manifest's zone honesty badge now states the snapshot selection, so decision (a) is in place. It is still open whether to switch to (b) or (c). The only working real source is LA County's Jan 2025 Eaton/Palisades *snapshot*, which contains only zones that were warned or ordered. Status and time fields are dropped, so no outcome data leaks into the replay, but **which zones exist reflects hindsight**. That's 131 zones against the 20–60 target. Options:
   - (a) keep the set and disclose it in the manifest (done; the README still to do);
   - (b) use the outcome-free Census block-group fallback (`approximate: true`);
   - (c) trim by geography (e.g. within N km of the ignition).
2. **Spawn points** need eyeballing on a map. ALD-FARNSWORTH's point is about 150 m west of Lake Ave.
3. **Zone names** are title-cased from the county codes (e.g. "Altadena Eatoncanyon"), yet the honesty block says "LA County zone names".
4. **Commit size.** `data/scenarios/eaton/web/` is 11 MB (`terrain_elev.png` 7.6 MB, `terrain_tex.jpg` 2 MB), and `zones.geojson` is 1.3 MB stored twice. Everything else is small.
5. **Texture date** is `"unverified (check pre-fire)"`. The service metadata says NAIP 2017–2021, but no pixel was checked.
6. **M1 blockers:**
   - A0 (`hazard/api.py` + `FakeHazardModel`), or Abby's OK to promote `tests/hazard_standin.py`;
   - the dashboard's data path (C1 + Linda);
   - a first full Eaton run through the hub.
