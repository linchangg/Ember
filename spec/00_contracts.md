# 00: Shared contracts (v1.1; frozen at M0, Sat 08:00)

Every component codes against this file. To change anything after M0:
1. Post in the team channel.
2. Bump `SCHEMA_VERSION`.
3. Update both the Python and the TypeScript types in the same commit.

- **Python is the source of truth:** `engine/emberline/contracts.py` (Pydantic v2).
- **TypeScript:** `web/src/contracts.ts`. Write it by hand from §3.3 at M0.
- **Hub tables:** `04_hub.md` §2. Reducer signatures there are canonical; §5 here is only an index.

## 1. Conventions

- **Time on the wire and in the hub:** integer epoch **milliseconds, UTC**.
  - In SpacetimeDB they are stored as `f64` (exact below 2^53, and no `bigint` in TS).
  - The suffix says which clock: `*_sim_ms` (scenario timeline) or `*_wall_ms` (real time).
  - Display is `America/Los_Angeles` (PST = UTC−8 in January).
- **Time inside physics:** `float32` minutes since `t_ref` (`scenario.yaml`). The engine converts at the boundary.
- **Time words** (each means one thing everywhere):

  | Word | Meaning |
  |---|---|
  | `knowledge_sim_ms` | The data cutoff and issue time of a cycle |
  | `observed_sim_ms` | When the sensor saw it |
  | `available_sim_ms` | When a live system could first have received it |
  | `data_through_sim_ms` | The newest `observed_sim_ms` among observations a cycle used |

- **Coordinates:**
  - Wire: WGS84 `[lon, lat]`.
  - Model grid: `EPSG:32611`, row 0 = north.
  - Never ship UTM to the web.
- **Units:** metres; degrees true, clockwise; probabilities 0–1; durations `_s` or `_min`.
- **IDs** (prefixed strings, deterministic):

  | Object | ID format |
  |---|---|
  | Satellite pixel observation | `obs_{sensor_id}_{scan_id}_{px_x}_{px_y}` |
  | VIIRS observation | `obs_{sensor_id}_{t_obs_ms}_{row}` |
  | Report observation | `obs_rep_{report_id}` |
  | Camera observation (P2) | `obs_cam_{camera_id}_{t_ms}` |
  | Cycle | `cyc_{scenario}_{branch}_{knowledge_sim_ms}` |
  | Scan | `{sensor_short}_{scan_start:%Y%m%dT%H%M%S}` |
  | Alert | `al_{branch}_{top_zone}_{rule}_{triggered_sim_ms}` |
  | Briefing | `brf_{alert_id}` |
  | Activity event | `ev_{cycle_or_ctx}_{i}` |
  | Person, report | `per_{uuid8}`, `rep_{uuid8}` |

  Zones use the county zone code (e.g. `ALD-FARNSWORTH`). Commands get IDs assigned by the hub.
- **Scenario and branch:** every row carries `scenario_id` and `branch_id`. The default branch is `"main"`; live branches are `"b1"`, `"b2"`, …, numbered max+1 and never reused within a run.
- **Provenance:** `"real" | "simulated" | "injected" | "derived"`. Kept in the data; not rendered on screen.
  - `real`: archived GOES/VIIRS/HRRR, documented responder reports.
  - `simulated`: our synthetic residents, cameras, officials, and everything in the fixture.
  - `injected`: live input during the demo.
  - `derived`: forecasts and risk.
- **Hub storage:**
  - Nested or array fields are stored as `*_json: string`.
  - Nullable scalars use `option` types.
  - Counts use `u32`/`i32`; **never 64-bit integers**.
  - The web data layer adapts rows to these contracts (`04` §4): camelCase → snake_case, `undefined` → `null`, `JSON.parse` on `*_json`.

## 2. Scenario config (`data/scenarios/<id>/scenario.yaml`; everything here is covered by the config hash)

```yaml
scenario_id: eaton
title: "Eaton Fire replay — Jan 7–8, 2025"
tz: America/Los_Angeles
t_ref_utc: "2025-01-08T00:00:00Z"        # physics minute 0
t_ignition_utc: "2025-01-08T02:15:00Z"   # 18:15 PST
replay_start_utc: "2025-01-08T02:00:00Z"
replay_end_utc:   "2025-01-08T15:00:00Z"
ignition: { lon: -118.088, lat: 34.205, radius_m: 300 }
grid: { crs: "EPSG:32611", cell_m: 90, nx: 340, ny: 340, x0: 381480, y0: 3800280 }  # upper-left corner
cycle_min: 5
horizon_min: 360
step_min: 30                       # LEADS = 30..360
quantiles: [0.1, 0.3, 0.5, 0.7, 0.9]
seed: 7
n_members: 128
filter: pf                         # pf | reinit | none   (none = open-loop baseline)
assimilate_sensors: [goes18_fdcc, viirs_n20, viirs_snpp, viirs_n21]   # goes16_fdcc only if enabled (P2)
viirs_latency: product_created     # NOAA EFIRE file creation time (41-93 min after the pass); urt | nrt are assumptions
use_documented_reports: false      # 01 §9; the honest number is computed with this false
area_x:                            # honest-number region; verify on a map, then freeze (01 §6)
  lon_min: -118.165
  lon_max: -118.1316               # Lake Ave
  lat_min: 34.190
  lat_max: 34.235
act_by_quantile: 0.3
honesty:
  real: [GOES-18 FDC, VIIRS 375m, HRRR winds, USGS 3DEP, LANDFIRE LF2024, Census 2020, LA County zone names, official timeline]
  simulated: [residents and phones, duty officer, fixed cameras]
config_hash: null                  # written by scripts/freeze.py; shown in UI
```

## 3. Core types

### 3.1 Python (`engine/emberline/contracts.py`)

```python
from typing import Literal, Annotated
from pydantic import BaseModel, Field

SCHEMA_VERSION = 2
Ms = int
Prov = Literal["real", "simulated", "injected", "derived"]
QUANTILES = (0.1, 0.3, 0.5, 0.7, 0.9)
LEADS_MIN = tuple(range(30, 361, 30))                # 12 values; index i -> lead 30*(i+1)
def lead_idx(lead_min: int) -> int: return lead_min // 30 - 1   # p_at(60) = p_by_lead[1]
GOES_FIRE = frozenset({10, 11, 12, 13, 14, 30, 31, 32, 33, 34})   # "hot"
GOES_STRONG = frozenset({10, 11, 12, 13, 30, 31, 32, 33})          # used for re-anchoring + honest number
REPORT_HOT = {"fire": (True, 1.0), "embers": (True, 1.0), "smoke": (True, 0.5), "all_clear": (False, 1.0)}  # (hot, trust factor)

class Footprint(BaseModel):   type: Literal["footprint"] = "footprint"; ring: list[tuple[float, float]]   # closed lon/lat ring
class PointRadius(BaseModel): type: Literal["point_radius"] = "point_radius"; lon: float; lat: float; radius_m: float
class BearingRay(BaseModel):
    type: Literal["bearing_ray"] = "bearing_ray"; lon: float; lat: float
    bearing_deg: float; sigma_deg: float; max_range_m: float
Geometry = Annotated[Footprint | PointRadius | BearingRay, Field(discriminator="type")]
ObsKind = Literal["goes_pixel", "viirs_pixel", "camera_bearing", "ground_report", "thermal_footprint"]

class ObservationRecord(BaseModel):
    obs_id: str; scenario_id: str; branch_id: str = "main"
    sensor_id: str; kind: ObsKind
    observed_sim_ms: Ms; available_sim_ms: Ms
    geometry: Geometry
    hot: bool                                 # False = negative evidence (clear pixel / all_clear report)
    footprint_key: str | None = None          # stable cache key for geometry, e.g. "goes18:{px_x}:{px_y}"
    fdc_class: int | None = None              # GOES Mask code when kind=goes_pixel
    report_kind: Literal["fire", "smoke", "embers", "all_clear"] | None = None
    confidence: float = 1.0                   # display only (VIIRS l/n/h -> .3/.6/.9)
    frp_mw: float | None = None
    trust: float = 1.0                        # r in L = (1-r) + r*L_sensor  (01 §3/§4 tables; reports 05 §2)
    provenance: Prov
    source_ref: str | None = None
    status: Literal["pending", "assimilated", "rejected"] = "pending"
    cycle_id: str | None = None
    influence: float | None = None

class ScanEvent(BaseModel):
    scan_id: str; scenario_id: str; branch_id: str = "main"; sensor_id: str
    mode: Literal["CONUS", "MESO", "FD", "pass"]
    t_start_sim_ms: Ms; t_end_sim_ms: Ms; available_sim_ms: Ms
    n_pixels_aoi: int; n_hot: int; n_clear: int
    assimilated: bool = True                  # False = display-only (e.g. 1-min meso, P2)
    pixels_path: str | None = None            # static/{scn}/scans/{scan_id}.json (P2 flash)
    provenance: Prov = "real"

class SensorInfo(BaseModel):
    sensor_id: str; scenario_id: str; kind: ObsKind; name: str; platform: str
    provenance: Prov; enabled: bool = True
    status: Literal["ok", "stale", "down"] = "ok"   # GOES: stale if no scan in 15 sim-min; down if disabled
    last_data_sim_ms: Ms | None = None; latency_s: float | None = None
    lon: float | None = None; lat: float | None = None
    heading_deg: float | None = None; hfov_deg: float | None = None; pixel_m: float | None = None

class CycleAssets(BaseModel):                 # ASSET_BASE-relative (§4)
    arrival_q: str                            # cache/{scn}/{branch}/{k}/arrival_q.bin
    perimeter: str                            # cache/{scn}/{branch}/{k}/perimeter.geojson
    isochrones: str                           # cache/{scn}/{branch}/{k}/isochrones.geojson

class Cycle(BaseModel):
    cycle_id: str; scenario_id: str; branch_id: str
    knowledge_sim_ms: Ms; data_through_sim_ms: Ms; created_wall_ms: Ms
    source: Literal["cache", "live"]          # set at PUBLISH time
    filter_mode: Literal["pf", "reinit", "none"]
    n_members: int; ess: float; tempering_alpha: float; resampled: bool
    n_obs_by_kind: dict[str, int]
    horizon_min: int = 360; step_min: int = 30
    assets: CycleAssets
    danger: dict                              # {"lead60_p30": GeoJSON geometry (MultiPolygon), "lead120_p30": ...}; <= 20 KB
    burned_ha: float; front_km: float; growth_ha_per_h: float; head_bearing_deg: float | None
    params_summary: dict                      # {"ros_mult": [mean, sd], ...}
    model_version: str; config_hash: str | None
    duration_ms: int

class ZoneRisk(BaseModel):
    cycle_id: str; scenario_id: str; branch_id: str; zone_id: str; knowledge_sim_ms: Ms
    p_by_lead: list[float]                    # 12 values aligned to LEADS_MIN
    eta_q10_sim_ms: Ms | None; eta_q50_sim_ms: Ms | None; eta_q90_sim_ms: Ms | None
    act_by_sim_ms: Ms | None                  # eta_q{act_by_quantile} - evac_min
    act_by_basis: str                         # "q30 arrival − 45 min evac"
    evac_min: float
    population: int; housing: int; n_critical: int
    tier: Literal[1, 2, 3]
    risk_score: float; rank: int
    status: Literal["clear", "watch", "warning", "order"]
    trend: Literal["up", "flat", "down"]
    drivers: list[str]

AlertState = Literal["proposed", "approved", "declined", "superseded", "calling", "called", "acknowledged", "failed"]
class Alert(BaseModel):
    alert_id: str; scenario_id: str; branch_id: str
    zone_ids: list[str]                       # ranked; alert_id uses zone_ids[0]
    level: Literal["advisory", "warning", "order_recommended"]
    rule_id: str; rule_text: str
    cycle_id: str; triggered_sim_ms: Ms; act_by_sim_ms: Ms | None
    state: AlertState = "proposed"
    text: str                                 # <= 300 chars
    voice_script: str                         # <= 110 words
    draft_source: Literal["template", "claude"] = "template"
    briefing_id: str | None = None
    decided_by: str | None = None; decided_wall_ms: Ms | None = None
    call_id: str | None = None; call_status: str | None = None
    acked_via: Literal["phone", "dashboard"] | None = None
    acked_wall_ms: Ms | None = None; acked_sim_ms: Ms | None = None   # UI shows "acknowledged 23:14 (replay time)"

class Briefing(BaseModel):
    briefing_id: str; scenario_id: str; branch_id: str; cycle_id: str; alert_id: str
    model: str; prompt_version: str; input_hash: str
    headline: str; bullets: list[str]; text: str; voice_script: str
    grounded: bool; unmatched_tokens: list[str]; fallback_used: bool
    latency_ms: int; created_wall_ms: Ms

class Person(BaseModel):
    person_id: str; scenario_id: str
    kind: Literal["resident", "official", "judge"]; label: str
    lon: float; lat: float; location_source: Literal["assigned", "gps"]
    zone_id: str | None
    status: Literal["unknown", "safe", "evacuating", "needs_help"] = "unknown"
    p_60: float | None = None; eta_q50_sim_ms: Ms | None = None; act_by_sim_ms: Ms | None = None   # = their ZONE's values
    alerted_alert_id: str | None = None; acknowledged: bool = False
    last_seen_wall_ms: Ms; provenance: Prov

class Report(BaseModel):                      # public row; transcript lives in a private table (04 §2.1)
    report_id: str; scenario_id: str; branch_id: str; person_id: str | None
    channel: Literal["voice", "tap", "dashboard"]
    kind: Literal["fire", "smoke", "embers", "all_clear"]
    summary: str                              # the readback text, no raw transcript
    lon: float; lat: float; radius_m: float; trust: float = 0.5
    observed_sim_ms: Ms; created_wall_ms: Ms
    status: Literal["pending", "accepted", "rejected"] = "pending"; reject_reason: str | None = None
    observation_id: str | None = None; provenance: Prov = "injected"

class ReportStatus(BaseModel):                # engine -> hub status update, carried in a bundle
    report_id: str; status: Literal["accepted", "rejected"]; reject_reason: str | None; observation_id: str | None

class ActivityEvent(BaseModel):
    event_id: str; scenario_id: str; branch_id: str
    sim_ms: Ms; wall_ms: Ms                   # engine re-stamps wall_ms at publish; hub assigns seq
    stage: Literal["ingest", "observation", "filter", "forecast", "risk", "alert", "briefing", "call", "human", "report", "system"]
    actor: str
    level: Literal["info", "notice", "warn", "error"] = "info"
    message: str                              # <= 140 chars, rendered verbatim
    ref_kind: str | None = None; ref_id: str | None = None; duration_ms: int | None = None

class Metric(BaseModel):
    key: str; scenario_id: str; branch_id: str
    label: str; value: float | None; unit: str; definition: str
    computed_sim_ms: Ms | None                # None = post-hoc evaluation (not gated on sim time)
    detail: dict = {}

class CycleBundle(BaseModel):                 # THE unit of publication (engine -> hub; cache -> FileSource)
    schema_version: int = SCHEMA_VERSION
    bundle_id: str                            # bnd_{scenario}_{branch}_{knowledge_sim_ms}
    scenario_id: str; branch_id: str; knowledge_sim_ms: Ms
    cycle: Cycle | None                       # None = pre-ignition bundle (scans/observations/sensors/events only)
    zone_risk: list[ZoneRisk]
    observations: list[ObservationRecord]     # newly available hot satellite pixels + non-satellite obs (never clear pixels)
    scan_events: list[ScanEvent]
    sensors: list[SensorInfo]                 # status deltas
    alerts: list[Alert]                       # new proposals + superseded transitions (engine-owned states only)
    briefings: list[Briefing]                 # zero or one per cycle (highest-level new alert)
    people: list[Person]                      # risk fields for joined people
    reports: list[ReportStatus]               # report acceptance results
    metrics: list[Metric]
    events: list[ActivityEvent]
```

### 3.2 Clock (hub-owned)

```python
class ClockState(BaseModel):
    scenario_id: str
    mode: Literal["replay", "live"]
    playing: bool
    speed: float                    # sim seconds per wall second (default 240)
    anchor_sim_ms: Ms; anchor_wall_ms: Ms
    active_branch_id: str = "main"
    status: Literal["ok", "computing", "catching_up"] = "ok"
    resume_playing: bool = False    # value of `playing` saved when status went to computing
    pause_on_proposal: bool = True  # hub pauses the clock when a bundle inserts a NEW proposed alert (demo)
    epoch: int                      # +1 on seek/reset/branch switch -> clients drop derived caches
    min_sim_ms: Ms; max_sim_ms: Ms
# sim_now = clamp(anchor_sim + (playing ? (wall_now - anchor_wall) * speed : 0), min, max)
# EVERY change to playing/speed/status re-anchors first: anchor_sim = sim_now, anchor_wall = now.
```

### 3.3 TypeScript (`web/src/contracts.ts`), full set

```ts
export type Ms = number;
export type Prov = "real" | "simulated" | "injected" | "derived";
export const QUANTILES = [0.1, 0.3, 0.5, 0.7, 0.9] as const;
export const LEADS_MIN = [30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360] as const;
export const pAt = (p_by_lead: number[], leadMin: number) => p_by_lead[leadMin / 30 - 1];

export interface ClockState { scenario_id: string; mode: "replay" | "live"; playing: boolean; speed: number;
  anchor_sim_ms: Ms; anchor_wall_ms: Ms; active_branch_id: string; status: "ok" | "computing" | "catching_up";
  resume_playing: boolean; pause_on_proposal: boolean; epoch: number; min_sim_ms: Ms; max_sim_ms: Ms }
export const simNow = (c: ClockState, wall = Date.now()) =>
  Math.min(c.max_sim_ms, Math.max(c.min_sim_ms, c.anchor_sim_ms + (c.playing ? (wall - c.anchor_wall_ms) * c.speed : 0)));

export interface Branch { branch_id: string; scenario_id: string; parent_id: string | null; fork_sim_ms: Ms | null;
  reason: string; created_wall_ms: Ms }
export interface Cycle { cycle_id: string; scenario_id: string; branch_id: string; knowledge_sim_ms: Ms; data_through_sim_ms: Ms;
  source: "cache" | "live"; filter_mode: "pf" | "reinit" | "none"; n_members: number; ess: number; tempering_alpha: number;
  resampled: boolean; n_obs_by_kind: Record<string, number>; horizon_min: number; step_min: number;
  assets: { arrival_q: string; perimeter: string; isochrones: string };
  danger: { lead60_p30?: GeoJSON.MultiPolygon; lead120_p30?: GeoJSON.MultiPolygon };
  burned_ha: number; front_km: number; growth_ha_per_h: number; head_bearing_deg: number | null;
  params_summary: Record<string, [number, number]>; model_version: string; config_hash: string | null; duration_ms: number }
export interface CycleHead { scenario_id: string; branch_id: string; cycle_id: string; knowledge_sim_ms: Ms;
  danger: Cycle["danger"]; burned_ha: number }
export interface ZoneFeatureProps { zone_id: string; name: string; population: number; housing: number; n_critical: number;
  tier: 1 | 2 | 3; evac_min_default: number; egress_roads: number; approximate: boolean }
export type ZoneFeature = GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.MultiPolygon, ZoneFeatureProps>;
export interface Zone extends ZoneFeatureProps { evac_min: number }            // hub row (editable evac_min)
export interface ZoneRisk { cycle_id: string; scenario_id: string; branch_id: string; zone_id: string; knowledge_sim_ms: Ms;
  p_by_lead: number[]; eta_q10_sim_ms: Ms | null; eta_q50_sim_ms: Ms | null; eta_q90_sim_ms: Ms | null;
  act_by_sim_ms: Ms | null; act_by_basis: string; evac_min: number; population: number; housing: number;
  n_critical: number; tier: 1 | 2 | 3; risk_score: number; rank: number;
  status: "clear" | "watch" | "warning" | "order"; trend: "up" | "flat" | "down"; drivers: string[] }
export type AlertState = "proposed" | "approved" | "declined" | "superseded" | "calling" | "called" | "acknowledged" | "failed";
export interface Alert { alert_id: string; scenario_id: string; branch_id: string; zone_ids: string[];
  level: "advisory" | "warning" | "order_recommended"; rule_id: string; rule_text: string; cycle_id: string;
  triggered_sim_ms: Ms; act_by_sim_ms: Ms | null; state: AlertState; text: string; voice_script: string;
  draft_source: "template" | "claude"; briefing_id: string | null; decided_by: string | null; decided_wall_ms: Ms | null;
  call_id: string | null; call_status: string | null; acked_via: "phone" | "dashboard" | null;
  acked_wall_ms: Ms | null; acked_sim_ms: Ms | null }
export interface Briefing { briefing_id: string; branch_id: string; cycle_id: string; alert_id: string; model: string;
  prompt_version: string; headline: string; bullets: string[]; text: string; voice_script: string;
  grounded: boolean; unmatched_tokens: string[]; fallback_used: boolean; latency_ms: number }
export type Geometry =
  | { type: "footprint"; ring: [number, number][] }
  | { type: "point_radius"; lon: number; lat: number; radius_m: number }
  | { type: "bearing_ray"; lon: number; lat: number; bearing_deg: number; sigma_deg: number; max_range_m: number };
export interface Observation { obs_id: string; scenario_id: string; branch_id: string; sensor_id: string;
  kind: "goes_pixel" | "viirs_pixel" | "camera_bearing" | "ground_report" | "thermal_footprint";
  observed_sim_ms: Ms; available_sim_ms: Ms; geometry: Geometry; hot: boolean; fdc_class: number | null;
  report_kind: "fire" | "smoke" | "embers" | "all_clear" | null; confidence: number; frp_mw: number | null; trust: number;
  provenance: Prov; status: "pending" | "assimilated" | "rejected"; cycle_id: string | null; influence: number | null }
export interface ScanEvent { scan_id: string; scenario_id: string; branch_id: string; sensor_id: string;
  mode: "CONUS" | "MESO" | "FD" | "pass"; t_start_sim_ms: Ms; t_end_sim_ms: Ms; available_sim_ms: Ms;
  n_pixels_aoi: number; n_hot: number; n_clear: number; assimilated: boolean; pixels_path: string | null; provenance: Prov }
export interface SensorInfo { sensor_id: string; kind: Observation["kind"]; name: string; platform: string; provenance: Prov;
  enabled: boolean; status: "ok" | "stale" | "down"; last_data_sim_ms: Ms | null; latency_s: number | null;
  lon: number | null; lat: number | null; heading_deg: number | null; hfov_deg: number | null; pixel_m: number | null }
export interface Person { person_id: string; kind: "resident" | "official" | "judge"; label: string; lon: number; lat: number;
  location_source: "assigned" | "gps"; zone_id: string | null; status: "unknown" | "safe" | "evacuating" | "needs_help";
  p_60: number | null; eta_q50_sim_ms: Ms | null; act_by_sim_ms: Ms | null; alerted_alert_id: string | null;
  acknowledged: boolean; last_seen_wall_ms: Ms; provenance: Prov }
export interface Report { report_id: string; branch_id: string; person_id: string | null; channel: "voice" | "tap" | "dashboard";
  kind: "fire" | "smoke" | "embers" | "all_clear"; summary: string; lon: number; lat: number; radius_m: number; trust: number;
  observed_sim_ms: Ms; created_wall_ms: Ms; status: "pending" | "accepted" | "rejected"; reject_reason: string | null;
  observation_id: string | null; provenance: Prov }
export interface ActivityEvent { event_id: string; seq: number; branch_id: string; sim_ms: Ms; wall_ms: Ms;
  stage: "ingest" | "observation" | "filter" | "forecast" | "risk" | "alert" | "briefing" | "call" | "human" | "report" | "system";
  actor: string; level: "info" | "notice" | "warn" | "error"; message: string; ref_kind: string | null; ref_id: string | null;
  duration_ms: number | null }
export interface Metric { key: string; branch_id: string; label: string; value: number | null; unit: string;
  definition: string; computed_sim_ms: Ms | null; detail: Record<string, unknown> }
export interface CycleBundle { schema_version: number; bundle_id: string; scenario_id: string; branch_id: string;
  knowledge_sim_ms: Ms; cycle: Cycle | null; zone_risk: ZoneRisk[]; observations: Observation[]; scan_events: ScanEvent[];
  sensors: SensorInfo[]; alerts: Alert[]; briefings: Briefing[]; people: Person[];
  reports: { report_id: string; status: "accepted" | "rejected"; reject_reason: string | null; observation_id: string | null }[];
  metrics: Metric[]; events: Omit<ActivityEvent, "seq">[] }
```

**Lineage** (which rows are visible for the active branch): a row is visible if `row.branch_id == active`, or if `row.branch_id` is an ancestor of `active` and `row_time ≤ fork_sim_ms` of the child on the path.

| Table | `row_time` |
|---|---|
| `cycle`, `zone_risk` | `knowledge_sim_ms` |
| `observation` | `available_sim_ms` |
| `scan_event` | `t_start_sim_ms` |
| `alert` | `triggered_sim_ms` |
| `metric` | `computed_sim_ms` (null = always visible) |
| `report` | `observed_sim_ms` |
| `activity_event` | `sim_ms` |

## 4. Files, URLs and assets

`ASSET_BASE` is where the web app fetches files.
- **Demo laptop:** `http://localhost:8787/` (the engine's local API).
- **Vercel build:** `/` (mainline copied into `web/public/`).
- **File mode:** `/` (Vite `publicDir` = `fixtures/` or a copied cache).
- **Phones never fetch rasters.**

| URL (relative to ASSET_BASE) | File on disk |
|---|---|
| `static/{scn}/{file}` | `data/scenarios/{scn}/web/{file}` (fixture: `fixtures/static/{scn}/{file}`) |
| `cache/{scn}/{branch}/{k}/{file}` | `cache/{scn}/{branch}/{k}/{file}` (fixture: `fixtures/cache/{scn}/main/{k}/{file}`) |
| `cache/{scn}/{branch}/index.json` | `{"scenario_id", "branch_id", "cycles": [{"knowledge_sim_ms": k, "bundle": "cache/{scn}/{branch}/{k}/bundle.json"}]}`. `bundle` is ASSET_BASE-relative. Pre-ignition bundles are listed too. |

### 4.1 Scenario manifest (`static/{scn}/manifest.json`; loaded once from ASSET_BASE; the hub copy is informational)

```ts
interface ScenarioManifest {
  scenario_id: string; title: string; tz: string; config_hash: string | null; is_fixture: boolean;
  honesty: { real: string[]; simulated: string[] };
  aoi_bbox: [number, number, number, number];
  initial_view: { longitude: number; latitude: number; zoom: number; pitch: number; bearing: number };
  t_ignition_sim_ms: Ms; replay_start_sim_ms: Ms; replay_end_sim_ms: Ms;
  horizon_min: number; step_min: number; quantiles: number[];
  grid: { nx: number; ny: number; cell_m: number; crs: "EPSG:32611";
          corners: { tl: [number, number]; tr: [number, number]; br: [number, number]; bl: [number, number] } };  // OUTER corners, row 0 = north
  terrain: { elevation_url: string; encoding: "terrarium"; bounds: [number, number, number, number];
             texture_url: string; texture_date: string; attribution: string[] };
  static: { zones_url: string; assets_url: string; sensors_url: string; satellites_url: string;
            timeline_url: string; area_x_url: string; spawn_points_url: string; scans_dir: string | null };
  lake_ave: [number, number][];
}
```

Every `*_url` value is ASSET_BASE-relative, e.g. `static/eaton/zones.geojson`.

### 4.2 `arrival_q.bin` (one per cycle)

- **Layout:** `uint8`, shape `[5, ny, nx]`, C order, row 0 = north. The quantile index follows `QUANTILES`. **Assert `byteLength == 5*ny*nx` against the manifest grid.**
- **Values:**
  - `v` in 1..254: arrival of that quantile within `(issue + 2(v−1), issue + 2v]` minutes. Clients use `issue + 2v`.
  - `v = 0`: already burned at issue.
  - `v = 255`: not reached within the horizon.
- **Encoding:** `v = 0 if t_q == 0 else min(254, ceil(t_q / 2))`; NaN → 255.
- **Size:** 340×340×5 = 578 KB raw. Served uncompressed from disk with HTTP gzip, ~100–200 KB on the wire.
- **Client rule:**
  - At view time `t`: `k = #{ i : v_i != 255 and issue + 2·v_i·60000 ≤ t }`, a class from 0 to 5.
  - Class c means P ≥ `QUANTILES[c−1]`.
  - Cells with all five `v == 0` are drawn as burned, not as forecast.
- **Drawing:** use the 4 named corners. deck.gl `BitmapLayer` takes `[bl, tl, tr, br]`; MapLibre image sources take `[tl, tr, br, bl]`.

### 4.3 Vector and static assets (lon/lat GeoJSON)

| File | Content |
|---|---|
| `perimeter.geojson` | FeatureCollection of 2 features: `{id: "burned", properties: {kind: "burned"}, geometry: MultiPolygon}` and `{id: "front", properties: {kind: "front"}, geometry: MultiLineString}` |
| `isochrones.geojson` | q50 arrival contour lines at leads 60..360 every 60 min, `properties: {lead_min, q: 0.5}` |
| `Cycle.danger` | GeoJSON **geometries** (not Features): `lead60_p30` and `lead120_p30` MultiPolygons |
| `static/{scn}/zones.geojson` | Features with `ZoneFeatureProps` (§3.3) |
| `static/{scn}/assets.geojson` | Points `{asset_id, type: hospital\|nursing_home\|school\|fire_station\|substation\|water_plant, name, zone_id}`. **The UI and briefings use `type`, never `name`.** |
| `static/{scn}/timeline.json` | `[{t_sim_ms, label, source, kind: "official"\|"report"\|"satellite", as_observation?: bool, lon?, lat?}]`. Display only, unless `use_documented_reports`. |
| `static/{scn}/sensors.json` | `SensorInfo[]` |
| `static/{scn}/satellites.json` | `[{sat_id, name, orbit: "geo"\|"leo", geo_lon_deg?, norad_id?, tle?: [l1, l2], sensor, pixel_m}]` |
| `static/{scn}/area_x.geojson` | One Feature, Polygon, `properties: {name: "west Altadena foothills (area X)"}` |
| `static/{scn}/spawn_points.json` | `{ "<join_code>": [{spawn_id, lon, lat, zone_id, street}] }`. Also loaded into the hub's `spawn_point` table by `load_scenario`. |
| `static/{scn}/scans/{scan_id}.json` | `{pixels: [{ring, hot, cls}]}`. P2 flash only; `scans_dir: null` if absent. |
| `static/{scn}/terrain_elev.png`, `terrain_tex.jpg` | §01-7 |

## 5. Hub interface index (canonical signatures in `04_hub.md` §2.2)

- **Public tables:**
  - `scenario`, `clock`, `branch`, `sensor`, `scan_event`, `observation`
  - `cycle`, `cycle_head`, `zone`, `zone_risk`
  - `alert`, `briefing`, `person`, `spawn_point`, `report`
  - `activity_event`, `metric`, `command`
- **Private tables:** `config`, `report_text` (transcripts).
- **Engine reducers** (authorized by the engine identity):
  - `load_scenario`, `publish_bundle`, `publish_bundles`, `rewind`
  - `set_clock_status`, `create_branch`, `upsert_alert_draft`, `update_alert_call`
  - `append_events`, `mark_command`, `submit_report_engine`
- **Service reducers** (authorized by the service identity): `submit_report`, `ack_alert`.
- **Operator reducers** (need `operator_pin`):
  - `clock_play`, `clock_pause`, `clock_seek`, `clock_set_speed`, `set_pause_on_proposal`
  - `reset_scenario`, `approve_alert`, `decline_alert`, `set_evac_min`, `drop_sensor`
- **Public reducers** (phones): `join_as_person`, `set_person_status`.

## 6. Physics boundary: HazardModel (`engine/emberline/hazard/api.py`)

Physics returns arrays. The engine owns files, contours, risk and publication.

```python
from dataclasses import dataclass, field
from typing import Protocol, Literal
import numpy as np

@dataclass(frozen=True)
class Grid:
    crs: str; x0: float; y0: float; cell_m: float; H: int; W: int   # row 0 = north; x0,y0 = upper-left
    t_ref_s: int                                                    # model time = float32 minutes since t_ref

@dataclass
class Landscape:                     # built by data.load_landscape(scn) (01 §6); Abby may call it directly
    grid: Grid
    fuel: np.ndarray                 # (H,W) int16 FBFM40 code (91 = NB1 urban; 92/93/98/99 non-burnable)
    slope: np.ndarray                # (H,W) float32 RISE/RUN (= tan of slope angle). phiS uses slope**2.
    aspect: np.ndarray               # (H,W) float32 deg cw from N, DOWNSLOPE direction
    elev: np.ndarray                 # (H,W) float32 m
    canopy_cover: np.ndarray | None = None
    canopy_height: np.ndarray | None = None
    building_frac: np.ndarray | None = None

@dataclass
class Weather:                       # from ForcingProvider.weather_as_of() (03 §3); earth-relative winds
    t_min: np.ndarray                # (B,) float32 band start, minutes since t_ref, hourly, ascending
    u10: np.ndarray                  # (B,H,W) float32 m/s eastward
    v10: np.ndarray                  # (B,H,W) float32 m/s northward
    temp_c: np.ndarray               # (B,)
    rh: np.ndarray                   # (B,) 0–1
    # Times after the last band use the last band; times before the first use the first.

@dataclass
class Ignition:
    t_min: float; points_xy: np.ndarray; radius_m: float = 300.0   # grid-CRS metres, (K,2)

@dataclass
class ModelConfig:
    n_members: int = 128; horizon_min: int = 360; out_step_min: int = 30; cycle_min: int = 5
    filter: Literal["pf", "reinit", "none"] = "pf"
    ess_target: float = 0.5; jitter_a: float = 0.97; reseed_frac: float = 0.25
    spotting: bool = True; urban: bool = True; seed: int = 7
    history_min: int = 720           # physics keeps its own observation history this long (in state)
    quantiles: tuple[float, ...] = (0.1, 0.3, 0.5, 0.7, 0.9)

@dataclass
class ForecastArrays:
    t_issue_min: float
    t_q: np.ndarray                  # (5,H,W) float32 minutes AFTER issue; NaN = beyond horizon; 0 = burned at issue
    p_burned_now: np.ndarray         # (H,W) float32
    weights: np.ndarray              # (N,) float64 normalized
    ess: float; tempering_alpha: float; resampled: bool; filter_mode: str
    params_summary: dict
    obs_used: list[dict] = field(default_factory=list)   # [{obs_id, used: bool, ll_spread: float}] -> status/influence
    duration_ms: int = 0

class HazardModel(Protocol):
    def init(self, land: Landscape, weather: Weather, cfg: ModelConfig, ign: Ignition) -> None: ...
    def update_weather(self, weather: Weather) -> None: ...
    def step(self, t_now_min: float, new_obs: list["ObservationRecord"]) -> ForecastArrays: ...
        # new_obs = ONLY observations that became available since the previous step; physics keeps history.
        # RNG for this step = np.random.default_rng([cfg.seed, cycle_index]), cycle_index = round(t_now_min / cycle_min).
        # Branch-independent on purpose: a branch differs from its parent ONLY by the injected observations.
    def region_arrivals(self, index: np.ndarray, n_regions: int) -> np.ndarray: ...
        # index (H,W) int32 region id or -1 -> (N, n_regions) float32 earliest arrival minutes since t_ref (inf = never)
    def save_state(self) -> bytes: ...   # float32 arrays, np.savez_compressed; bit-identical round trip
    def load_state(self, blob: bytes) -> None: ...
```

**Rules**
- **Observation records cross the boundary unchanged.** Physics owns the kind → likelihood registry (`hazard/likelihoods.py`) and caches geometry by `footprint_key` (or a hash of the geometry).
- **Ground reports:** the engine sets `hot`/`trust` via `REPORT_HOT` (`trust = report.trust × factor`); physics uses `hot`.
- **Risk** uses `region_arrivals` + `weights` (`03` §7).

## 7. Frozen alert rules (`config/alert_rules.yaml`, hashed by `scripts/freeze.py`)

```yaml
version: 1
act_by_quantile: 0.3          # act_by = eta_q30 - evac_min
evac_min_tier1: 75
evac_min_default: 45
tiers:                        # computed by the data pipeline (01 §6)
  tier1: "hospital or nursing/assisted-living facility in zone, OR <= 1 egress road (any drivable road crossing the zone boundary)"
  tier2: "population > 500 OR school in zone"
  tier3: "otherwise"
rules:
  - id: R1_WARNING
    level: warning
    text: "P(fire reaches zone within 2 h) ≥ 30%"
    lead_min: 120
    p_min: 0.30
  - id: R2_ORDER
    level: order_recommended
    text: "P(fire reaches zone within 1 h) ≥ 50%, or act-by time within 15 min"
    lead_min: 60
    p_min: 0.50
    act_by_within_min: 15     # (act_by_sim_ms - knowledge_sim_ms) <= 15 min, including negative
min_population: 1
cooldown_min: 30              # per zone+level, counted from the previous triggered_sim_ms, any state
supersede: true               # an order_recommended supersedes a PROPOSED warning for the same zone
merge_min_zones: 3            # >= 3 zones crossing the same rule in one cycle -> one alert
```

## 8. Activity feed vocabulary (≤ 140 chars, rendered verbatim)

- `GOES-18 22:05 → 3 hot px, 141 clear`
- `Filter: ESS 41/128 (α 0.62), resampled`
- `Forecast run 0.6 s → 12 leads`
- `ALD-FARNSWORTH crossed R1 (P 34% by +2h)`
- `Claude drafted briefing (validated ✓)` / `Claude draft rejected → template`
- `Clock paused for operator review`
- `Operator approved alert`
- `Cartesia calling duty officer…`
- `Acknowledged by phone`
- `Voice report per_3f2a → ground obs (trust 0.25)`
- `Branch b1 forked at 23:10 → live re-forecast 1.4 s`
- `Cached rehearsal branch` (whenever the `demo_b1` cache is served)

## 9. Repo layout and environment

```
Legendary-Fire-Project/           # repo root
  AGENTS.md  README.md  SPEC.md  spec/  research/  docs/{process,build_process}.md  .env.example  .gitignore  .gitattributes  .cursor/rules/
  design/command-dashboard-mockup/ # Linda's design reference (look)
  config/alert_rules.yaml
  engine/                          # Python 3.12, uv; package "emberline"
    pyproject.toml
    emberline/contracts.py
    emberline/hazard/              # Abby
    emberline/data/                # Cody (Abby runs statics.py herself; 01 §6)
    emberline/runtime/             # Cody
    emberline/risk/                # Cody: risk.py, alerts.py; Abby: metrics.py (after 18:00)
    emberline/sync/                # Cody
    emberline/ai/                  # Cody
    emberline/notify/              # Cody
    emberline/api/local.py         # Cody
    emberline/scripts/             # make_fixture.py, export_schema.py, precompute.py, freeze.py, check_goes.py
    emberline/run.py
    tests/
  stdb/                            # SpacetimeDB TypeScript module (Cody)
  web/                             # Vite 8 + React 19 (Linda); TypeScript after Cody's conversion (C1)
    api/                           # Vercel functions (Linda; 05 §2–4)
    src/contracts.ts  src/data/  src/dashboard/  src/map/  src/phone/  src/control/
  data/scenarios/eaton/            # built by 01; raw/ git-ignored; small vectors + scenario.yaml committed
  cache/                           # git-ignored
  fixtures/{static,cache}/fixture/ # committed analytic fixture (§10)
```

Scripts run as `python -m emberline.scripts.<name>`.

`.env.example`. The engine reads `engine/.env`; Vercel uses its env vars.

```
SCENARIO_ID=eaton
STDB_URI=https://maincloud.spacetimedb.com        # http://localhost:3000 offline / for dev
STDB_DB=emberline
STDB_TOKEN=                                       # engine identity token (04 §3)
OPERATOR_PIN=                                     # typed once into dashboard and /control
ASSET_PORT=8787
ANTHROPIC_API_KEY=
CARTESIA_API_KEY=
CARTESIA_AGENT_ID=
CARTESIA_FROM_NUMBER_ID=
CARTESIA_VERSION=                                 # value of the required Cartesia-Version header (see docs)
DUTY_OFFICER_NUMBERS=                             # E.164, comma-separated; local only, never in the hub
FIRMS_MAP_KEY=
LANDFIRE_EMAIL=
EMBERLINE_PROFILE=online                          # online | offline
# Vercel only: XAI_API_KEY, ANTHROPIC_API_KEY, STDB_URI, STDB_DB, STDB_SERVICE_TOKEN, CARTESIA_WEBHOOK_SECRET, GEMINI_API_KEY (P2)
```

## 10. Fixture (Cody, by M0 = Sat 08:00): analytic, no dependency on physics or the summarizer

`python -m emberline.scripts.make_fixture` writes `fixtures/` using numpy + shapely + Pillow only. Everything is labeled: `scenario_id = "fixture"`, `is_fixture: true`, all `provenance = "simulated"`, zone IDs `FIX-Z1..FIX-Z6`, title "FIXTURE (synthetic)".

| Item | Content |
|---|---|
| Grid | Eaton extent (§2 corners), **120×120, cell_m = 255**. The manifest declares 120×120. |
| Timing | `t_ignition` = 2025-01-08T02:15Z. `replay_start` = 02:00Z. 3 pre-ignition bundles (02:00, 02:05, 02:10; `cycle: null`, scan events only) then **36 cycles** every 5 min from 02:20Z. |
| Fire | Analytic ellipse from (−118.088, 34.205), heading 240° (toward the SW), head ROS 1.2 km/h, L:W 3. 50 seeded synthetic members (ROS ~±18%, heading ±8° with a 20% wind-shift tail to ±25°, ignition ±250 m) give quantiles directly. Write `arrival_q.bin`, `perimeter.geojson` (both features), `isochrones.geojson`, and `danger` exactly per §4. |
| Zones | 6 rectangles (~1.5 km) placed down the SW axis, so `FIX-Z2` crosses R1 at cycle 20 and `FIX-Z3` crosses R2 at cycle 30. `zone_risk` comes from the same member arrivals. |
| Alerts | Cycle 20 bundle: a `proposed` R1 alert for FIX-Z2 with template text. Cycle 30: R2 for FIX-Z3. |
| Static files | `manifest.json`, `zones.geojson`, `assets.geojson` (3 points), `timeline.json` (2 fake events), `sensors.json` (goes18_fdcc + viirs_n20), `satellites.json` (GOES-18 geo, NOAA-20 with a placeholder TLE), `area_x.geojson`, `spawn_points.json` (`{"demo": [6 points]}`). Terrain: flat Terrarium PNG (RGB 128,0,0 = 0 m) 256² + a gray 512² JPEG; `texture_date: "fixture"`. `scans_dir: null`. |
| Observations | Each cycle: 1 `ScanEvent` (goes18_fdcc) and 2–4 hot `goes_pixel` observations along the perimeter (2.3×2.8 km footprints). |
| Events | 3–5 per cycle (§8 wording, prefixed "FIXTURE"). |
| Index | `fixtures/cache/fixture/main/index.json` (§4). |

Test `engine/tests/test_fixture.py`:
- every bundle validates as `CycleBundle`
- `byteLength == 5·120·120`
- the alerts appear at cycles 20 and 30

Keep the fixture < 10 MB.
