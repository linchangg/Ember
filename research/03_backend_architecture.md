# 03 — Ember backend / data-system architecture

Research agent 3 of 6. Researched Sat Oct 24 2026, about 4:30–5:00 AM PT. Every claim about a vendor was checked against that vendor's docs or package, unless it is marked (unverified). Payload sizes come from a local experiment (`scratchpad/sdkprobe/payload_test.py`) that used a synthetic 64-member ensemble.

## 0. TL;DR

- **Recommendation: Option A. One Python "engine" process sends outbound calls only. Convex is the single hub for live state, files, and public webhooks. The web app is Vite/React on Vercel.** The engine holds all the domain logic: clock, adapters, assimilation loop, risk, alerts, LLM, and notifications. Convex holds state and fans it out. It also stores live raster and audio files at public URLs and receives Twilio webhooks at `*.convex.site`. The laptop never has to accept inbound connections, so we need no tunnel.
- **Strong alternative: Option B, with SpacetimeDB as the hub.** Its prize criteria describe our product almost word for word. The cost is roughly 3–4 extra hours of Cody's time. Python has no maintained SDK, so we would call reducers over HTTP and poll SQL. There is no file storage, so files go in blob rows or behind a tunnel. HTTP handlers for webhooks are still beta. The architecture is otherwise identical, and two thin adapter files isolate the vendor (`StateSink` in Python, `useLive*` hooks in React). **Decide in the first hour. Do not wait for the 4 PM workshops.**
- **Replay-as-live.** The clock is a single anchor row that clients extrapolate locally, not a ticking counter. Each adapter answers "everything with `available_at` in (a, b]". The mainline timeline is precomputed by the same loop running on a fast clock, then content-addressed into a cache. Live injections fork a branch and recompute live. If the inputs exactly match a scripted injection, the cache is hit legitimately, because the computation is deterministic.
- **Rasters.** For each lead step we send one pre-coloured RGBA PNG with 16 probability levels, plus `[w,s,e,n]` bounds in EPSG:4326, plus contour GeoJSON. At 400×400 that measured about 20 KB per step and about 0.25 MB per snapshot of 12 steps. Phones get vectors only.
- **Biggest risks:** (1) latency of the live re-forecast; (2) Twilio's trial and A2P rules make SMS to a judge's phone close to impossible today, so the in-app alert on the phone page has to be the path that always works; (3) the Convex free-plan caps (1 GB/month each for database I/O and file egress), which means narrow, indexed queries and baking the replay rasters into the static build.

## 1. Components and boundaries

| Component | Runs in | Owner | Consumes → Produces |
|---|---|---|---|
| **Clock** | engine (authoritative), Convex `clock` doc (shared) | Cody | play/pause/seek/speed/live commands → `ClockState` anchor; `sim_now()` |
| **Feed adapters** (GOES, VIIRS, camera, ground/SMS report, thermal-footprint interface) | engine | Cody | archive files or live polls / Convex rows → `Observation[]` with `available_at` |
| **Forcing provider** (HRRR wind/RH, terrain, fuels) | engine | Cody (load) / Abby (use) | gridded files → `Forcing(t)`. **Not an Observation**: it is model input, not a likelihood. |
| **Assimilation loop** | engine | Cody (loop) / Abby (model + filter) | obs, forcing, prior ensemble → posterior ensemble + `AssimilationCycle` |
| **HazardModel** (fire now, flood later) | engine, process pool | Abby | Python Protocol (section 7.3) |
| **Forecast summarizer** | engine | Cody | ensemble → `EnsembleSummary`, PNG per lead step, contours, arrival quantiles |
| **Snapshot cache** | engine disk `cache/` | Cody | content-addressed cycle bundles + ensemble checkpoints |
| **Risk engine** | engine | Cody | member arrival grids × zones × exposure → `RiskAssessment[]` (rank, act-by) |
| **Alert engine** | engine (rules) + Convex (state-machine guard) | Cody | risk deltas → `Alert` proposals; approvals → send |
| **Briefing generator** | engine (Claude) | Cody | ranked table → `Briefing` (validated, with template fallback) |
| **Notification service** | engine | Cody | approved alert → SMS / voice / in-app channels → `NotificationLog` |
| **Camera intake** | browser `/cam` page → Convex storage → engine (Claude vision) | Cody | JPEG frame → detection → bearing `Observation` |
| **Victim/report intake** | `/p` phone page (Convex mutations) + Twilio → Convex HTTP action | Cody / Linda | `Person`, `Report` → ground-report `Observation` |
| **Activity feed** | every component calls `emit()` → Convex `events` | all | `ActivityEvent` stream rendered in the UI |
| **State hub** | Convex | Cody | tables, reactive queries, files, HTTP actions |
| **Web app** | Vite/React/deck.gl on Vercel. Routes: `/` dashboard, `/p` phone, `/cam` camera console | Linda | `useLive*` hooks over `ConvexSource` or `FileSource` |

**Boundary rule.** All computation lives in Python. Convex functions only validate input, enforce state-machine transitions, and store or fan out data. They never compute fire, risk, or alert logic. That keeps the hub swappable (A↔B) and lets offline mode run the exact same engine.

```mermaid
flowchart LR
  subgraph Sources
    GA[GOES/VIIRS archive or live poll]
    HR[HRRR + 3DEP + LANDFIRE files]
    CAMB["/cam browser (webcam)"]
    PH["/p phone page (judge)"]
    TW[Twilio inbound SMS]
  end
  subgraph Engine["Python engine (1 process, asyncio; outbound only)"]
    CL[Clock] --> AD[Adapters → Observation]
    AD --> AL[Assimilation loop]
    HR --> FP[Forcing provider] --> AL
    AL <--> HM[["HazardModel + Filter (Abby, ProcessPool)"]]
    AL --> SUM[Summarizer: PNG/contours/quantiles]
    AL --> RK[Risk engine] --> ALR[Alert rules]
    RK --> BR[Briefing (Claude Sonnet)]
    VIS[Vision (Claude Haiku) → bearing] --> AD
    NT[Notify: in-app / voice / SMS]
    CA[(cache/: bundles, PNG, npz checkpoints)]
    SUM --> CA
    SINK[StateSink]
  end
  subgraph Hub["Convex (cloud; local backend offline)"]
    T[(tables: clock, observations, cycles, snapshots, layers, risk, alerts, briefings, people, reports, frames, events, notifications)]
    FS[(file storage: live PNGs, frames, audio)]
    HA[HTTP actions: /twilio/sms, /twilio/status]
  end
  GA --> AD
  CAMB -- upload frame --> FS
  CAMB -- frames.create --> T
  PH -- reports.submit / people.join --> T
  TW --> HA --> T
  T -- Python subscribe: clock, approvals, frames, reports --> Engine
  SINK -- mutations: publishCycleBundle, events --> T
  SUM -- storeFile --> FS
  NT --> TWO[Twilio voice/SMS] & TTS[TTS provider]
  T -- useQuery --> WEB["Dashboard (Vercel/local)"] & PH
  CA -. FileSource offline .-> WEB
```

## 2. Architecture options and scoring

**A. Engine + Convex hub (recommended).** The Python engine writes with the official `convex` client (0.8.1, released Sep 2026, requires Python ≥ 3.10). I installed it and confirmed `query`, `mutation`, `action`, `subscribe` (sync iteration and `async for`), `watch_all`, and `set_admin_auth`. The engine reacts to approvals, frames, reports, and clock changes through `subscribe`. Live rasters, camera frames, and TTS audio go to Convex file storage. `getUrl` returns a bearer URL that anyone holding it can fetch, which is exactly what Twilio `<Play>` and phones need. Twilio webhooks hit HTTP actions at `https://<deployment>.convex.site`. For offline use, `npx convex dev` runs an anonymous local backend on port 3210 with no account.

**B. Engine + SpacetimeDB hub.** The module is TypeScript; SpacetimeDB 2.0 (Feb 20 2026) made TS modules first-class, and the current release is 2.13.0 (Oct 21 2026). The engine calls `POST /v1/database/:db/call/:reducer` with a JSON array of arguments, and reads through `POST /v1/database/:db/sql`. The PyPI `spacetimedb-sdk` stopped at 0.7.0 in Dec 2023, so it is dead. React uses `useTable(tables.x.where(...))`, `useReducer`, `useProcedure`, and `SpacetimeDBProvider` from `spacetimedb/react`; I confirmed these in the npm package. Webhooks would go through `httpHandler` routes at `/v1/database/:db/route/*`. Those are beta, but they can open transactions with `ctx.withTx`. Procedures can make outbound HTTP calls. For binary data the options are `t.byteArray()` columns, or rasters and audio served from the engine through a tunnel. Offline use is `spacetime start` or Docker on port 3000.

**C. FastAPI-only.** The engine also serves REST plus WebSocket, with SQLite, or with Postgres on Tiger Data for the MLH prize. Phones and Twilio would reach the laptop through a cloudflared quick tunnel. Quick tunnels need no account, but they get a random URL, allow at most 200 in-flight requests, and **do not support SSE**. WebSocket support through quick tunnels is (unverified). The alternative is a Vultr or DigitalOcean VM. This option is the most familiar to Cody, but we would hand-build reconnection, hydration, and fan-out, and win no realtime-DB prize.

**D. Hybrid: A or B plus Tiger Data for the time-series history.** I do not recommend it. It would put the same truth in two stores, which violates principle 3. The only role for Tiger that would not overlap is the replay *feed archive*: windowed time queries over GOES and VIIRS detections. A Parquet file does that job in about 10 lines.

Scores run 1–5, higher is better.

| Criterion | A Convex | B SpacetimeDB | C FastAPI(+Tiger) | D Hybrid |
|---|---|---|---|---|
| Python worker integration | 5 (official client, push subscriptions) | 3 (HTTP reducers + SQL polling; JSON encodings of SATS types are quirky) | 5 | 3 |
| Frontend DX | 5 (`useQuery`/`useMutation`) | 4 (good hooks, needs a codegen step) | 3 | 3 |
| Binary/raster/audio | 4 (file storage, public URLs; free tier caps egress at 1 GB/month) | 2 (no file store) | 4 (static files, but a public URL needs a tunnel) | 3 |
| Setup time | 5 (~15 min) | 3 (~1 h: CLI, publish, generate, login) | 4 | 2 |
| Offline demo | 4 (local anonymous backend) | 4 (`spacetime start`) | 5 | 2 |
| Multi-client sync, phones over cellular | 5 | 5 | 2 | 4 |
| Public webhooks with no tunnel | 5 (HTTP actions) | 3 (beta HTTP handlers) | 1 | 4 |
| Coding-agent familiarity | 5 (stable API since 2022) | 3 (2.x TS API is 8 months old, 13 minor releases) | 5 | 3 |
| Prize value | 4 ($5,000 per Hive, generic criteria, probably crowded (unverified)) | 5 (criteria match "shared ops dashboard / shared simulations / AI agent coordination"; 3 placements) | 1 | 3 (violates principle 3) |
| **Total** | **42** | **32** | **30** | **27** |

**Why A over B.** Convex does four jobs we would otherwise build ourselves: the database, realtime push, public file hosting, and a public webhook endpoint. It also gives Python a first-class client. The extra criteria fit of B is real, but that is a prize-strategy call, not an engineering one. If the team chooses B, change only the following:
- reducers called from Python take only primitives plus `*_json: string` payloads, which sidesteps the SATS-JSON encoding of options and sums;
- engine-only reducers check a shared `engine_key` argument against a private `config` table;
- the engine polls `SELECT * FROM command WHERE status='pending'` every 0.5–1 s;
- live PNGs and audio are stored in a `blob` table (`t.byteArray()`) and served by an `httpHandler`, or by the engine through a cloudflared tunnel;
- pin CLI and npm to 2.13.0;
- energy use on the free maincloud tier under SQL polling is unknown (2,500 TeV/month), so measure it in hour 1 (unverified).

## 3. Data model

**Wire conventions.** These are the most important decisions to freeze in hour 1.
- Times are integer epoch **milliseconds UTC**, with `_ms` names on TS/Convex fields.
- Coordinates are WGS84 `[lon, lat]`, in GeoJSON order. Distances are in metres. Bearings are degrees true, measured clockwise. Probabilities are 0–1. Durations are seconds (`_s`) or minutes (`_min`).
- IDs are prefixed strings. Cached artifacts get **deterministic** IDs: `snp_{scenario}_{branch}_{cycleMs}`.
- **`knowledge_ms`** (the data cutoff of a cycle) is a different thing from **`valid_ms`** (the time a forecast layer describes). Any field whose meaning is ambiguous gets a suffix.
- Every record carries `scenario_id` and `branch_id` (default `"main"`).
- Every source-derived record carries `provenance: "real" | "simulated" | "derived"`, which lets the UI label it honestly.

### 3.1 Pydantic (engine source of truth: `engine/ember/core/models.py`)

```python
Ms = int; Prov = Literal["real", "simulated", "derived"]

class GridSpec(BaseModel):
    bounds: tuple[float, float, float, float]   # W,S,E,N EPSG:4326 (delivery grid)
    width: int; height: int
    native_crs: str = "EPSG:32611"; cell_m: float = 75.0   # model grid (UTM 11N for LA)

class Scenario(BaseModel):
    id: str; name: str; description: str
    t_ignition_ms: Ms; t_start_ms: Ms; t_end_ms: Ms
    cycle_interval_s: int = 300; horizon_min: int = 360; lead_step_min: int = 30
    grid: GridSpec; seed: int; n_members: int = 64
    provenance: dict[str, Prov]          # {"goes":"real","hrrr":"real","cameras":"simulated",...}
    thresholds_ref: str                   # git sha of alert rules frozen before replay

class ClockState(BaseModel):
    scenario_id: str; mode: Literal["replay", "live"]
    playing: bool; speed: float            # sim s per wall s (live: 1.0)
    anchor_sim_ms: Ms; anchor_wall_ms: Ms  # sim_now = anchor_sim + playing*(wall-anchor_wall)*speed
    active_branch_id: str = "main"
    status: Literal["ok", "computing", "catching_up"] = "ok"; revision: int

class SensorSpec(BaseModel):
    id: str; kind: Literal["goes_pixel", "viirs_pixel", "camera_bearing", "ground_report", "thermal_footprint"]
    name: str; platform: str               # "GOES-18 ABI", "NOAA-20 VIIRS", "webcam:lookout-1"
    lon: float | None = None; lat: float | None = None
    heading_deg: float | None = None; hfov_deg: float | None = None; max_range_m: float | None = None
    pixel_m: float | None = None; typical_latency_s: int = 0; clock_offset_s: float = 0.0
    likelihood: dict[str, float]           # p_detect, p_false, sigma_deg, trust ...
    provenance: Prov; enabled: bool = True

class Footprint(BaseModel):   type: Literal["footprint"]; ring: list[tuple[float, float]]
class PointRadius(BaseModel): type: Literal["point_radius"]; lon: float; lat: float; radius_m: float
class BearingRay(BaseModel):
    type: Literal["bearing_ray"]; lon: float; lat: float; bearing_deg: float; sigma_deg: float; max_range_m: float
Geometry = Annotated[Footprint | PointRadius | BearingRay, Field(discriminator="type")]

class Observation(BaseModel):
    id: str; scenario_id: str; branch_id: str; sensor_id: str; kind: str
    observed_ms: Ms; available_ms: Ms; ingested_wall_ms: Ms
    geometry: Geometry
    hot: bool                              # negative evidence (clear, not hot) is first-class
    confidence: float = 1.0; frp_mw: float | None = None; trust: float = 1.0
    provenance: Prov; source_ref: str | None = None   # archive row / frame id / report id
    status: Literal["pending", "assimilated", "rejected"] = "pending"; cycle_id: str | None = None

class EnsembleSummary(BaseModel):
    n_members: int; ess: float; resampled: bool
    burned_km2_p50: float; head_bearing_deg_p50: float; ros_mult_mean: float; ros_mult_sd: float

class AssimilationCycle(BaseModel):
    id: str; scenario_id: str; branch_id: str; knowledge_ms: Ms
    obs_ids: list[str]; n_obs_by_kind: dict[str, int]
    ess_before: float; ess_after: float; loglik: float
    source: Literal["cache", "live"]; duration_ms: int; status: Literal["running", "done", "failed"]

class ForecastLayer(BaseModel):
    lead_min: int; valid_ms: Ms; png_url: str; contours: dict   # FeatureCollection, p∈{0.1,0.5,0.9}

class ForecastSnapshot(BaseModel):
    id: str; scenario_id: str; branch_id: str; cycle_id: str; knowledge_ms: Ms
    grid: GridSpec; layers: list[ForecastLayer]
    arrival_quantiles_png_url: str | None  # R,G,B = p10,p50,p90 arrival, 2-min units
    summary: EnsembleSummary; model_version: str; cache_key: str

class Zone(BaseModel):
    id: str; name: str; polygon: list[list[tuple[float, float]]]
    population: int; buildings: int; evac_min: float             # editable assumption
    facilities: list[dict]                 # {id,type: hospital|nursing_home|school|substation|water, name}
    egress_roads: int; chokepoint: bool; priority_tier: Literal[1, 2, 3]

class RiskAssessment(BaseModel):
    snapshot_id: str; zone_id: str; rank: int
    p_by_lead: dict[int, float]            # {60: .12, 120: .41, ...} P(arrival ≤ knowledge+lead)
    arrival_p10_ms: Ms | None; arrival_p50_ms: Ms | None
    act_by_ms: Ms | None                   # arrival_q(alpha) - evac_min; alpha in scenario config
    score: float; tier: int; drivers: list[str]; delta_p_120: float

class Alert(BaseModel):
    id: str; scenario_id: str; branch_id: str; zone_id: str
    level: Literal["advisory", "warning", "order_recommended"]
    state: Literal["proposed", "approved", "declined", "superseded", "sending", "sent", "failed"]
    rule_id: str; dedupe_key: str; snapshot_id: str
    proposed_sim_ms: Ms; act_by_ms: Ms | None
    sms_text: str; voice_script: str; briefing_id: str | None
    decided_by: str | None = None; decided_wall_ms: Ms | None = None; sent_wall_ms: Ms | None = None

class Briefing(BaseModel):
    id: str; snapshot_id: str; model: str; prompt_version: str; input_hash: str
    headline: str; bullets: list[str]; sms_text: str; voice_script: str
    grounded: bool; unmatched_tokens: list[str]; fallback_used: bool; latency_ms: int

class NotificationLog(BaseModel):
    id: str; alert_id: str | None; channel: Literal["inapp", "voice", "sms"]
    to_masked: str; provider: str; provider_id: str | None; audio_url: str | None
    status: Literal["queued", "sent", "delivered", "failed"]; error: str | None; wall_ms: Ms

class Person(BaseModel):
    id: str; scenario_id: str; display_name: str | None
    role: Literal["resident", "responder", "official", "judge"]
    sim_lon: float; sim_lat: float; zone_id: str      # assigned scenario location, labelled simulated
    status: Literal["unknown", "safe", "evacuating", "needs_help"]; last_seen_wall_ms: Ms

class Report(BaseModel):
    id: str; scenario_id: str; person_id: str | None; channel: Literal["web", "sms"]
    kind: Literal["fire", "smoke", "blocked_road", "needs_help"]
    lon: float; lat: float; radius_m: float; text: str; created_sim_ms: Ms; observation_id: str | None

class ActivityEvent(BaseModel):
    id: str; scenario_id: str; branch_id: str; wall_ms: Ms; sim_ms: Ms
    kind: Literal["obs_ingested", "cycle_started", "cycle_done", "snapshot_published", "risk_ranked",
                  "alert_proposed", "alert_decided", "alert_sent", "briefing_ready", "report_received",
                  "camera_detection", "clock_changed", "branch_created", "error"]
    actor: str                             # "engine.assimilation", "claude.briefing", "operator:Ana", "phone:p_8f2"
    summary: str                           # one human sentence; UI renders verbatim
    ref_type: str | None; ref_id: str | None; severity: Literal["info", "warn", "crit"] = "info"
```

### 3.2 TypeScript (web: `web/src/contracts.ts`, generated from the JSON Schema exported by Pydantic, with key types shown)

```ts
export type Ms = number;
export type Geometry =
  | { type: "footprint"; ring: [number, number][] }
  | { type: "point_radius"; lon: number; lat: number; radius_m: number }
  | { type: "bearing_ray"; lon: number; lat: number; bearing_deg: number; sigma_deg: number; max_range_m: number };
export interface ClockState { scenario_id: string; mode: "replay" | "live"; playing: boolean; speed: number;
  anchor_sim_ms: Ms; anchor_wall_ms: Ms; active_branch_id: string; status: "ok" | "computing" | "catching_up"; revision: number }
export const simNow = (c: ClockState, wall = Date.now()) =>
  c.anchor_sim_ms + (c.playing ? (wall - c.anchor_wall_ms) * c.speed : 0);
export interface ForecastLayer { lead_min: number; valid_ms: Ms; png_url: string; contours: GeoJSON.FeatureCollection }
export interface ForecastSnapshot { id: string; branch_id: string; knowledge_ms: Ms;
  grid: { bounds: [number, number, number, number]; width: number; height: number };
  layers: ForecastLayer[]; summary: { n_members: number; ess: number; burned_km2_p50: number } }
export interface RiskAssessment { zone_id: string; rank: number; p_by_lead: Record<number, number>;
  arrival_p50_ms: Ms | null; act_by_ms: Ms | null; score: number; tier: 1 | 2 | 3; drivers: string[]; delta_p_120: number }
export type AlertState = "proposed" | "approved" | "declined" | "superseded" | "sending" | "sent" | "failed";
export interface Alert { _id: string; zone_id: string; level: "advisory" | "warning" | "order_recommended";
  state: AlertState; act_by_ms: Ms | null; sms_text: string; voice_script: string; decided_by?: string }
export interface ActivityEvent { wall_ms: Ms; sim_ms: Ms; kind: string; actor: string; summary: string;
  ref_type?: string; ref_id?: string; severity: "info" | "warn" | "crit" }
```

**Convex tables and indexes.** Use narrow indexes to stay inside the free-plan I/O cap.
- `clock`: one document per scenario.
- `cycles`, `snapshots`, `risk`, `alerts`, `observations`, `events`: index `by_branch_time` on `[scenario_id, branch_id, knowledge_ms | sim_ms]`.
- `layers`: index `by_snapshot`.
- `zones`: static fields only. Polygons also ship as a static file.
- `people`, `reports`, `frames`, `notifications`, `briefings`.
- Contours sit inside `layers` documents, about 28 KB each, well under the 1 MiB document limit.

The **atomic cycle bundle** is a single `engine.publishCycleBundle` mutation that writes the cycle, snapshot, layers, risk rows, alert proposals, and events together. A client never sees half a cycle. The 16 MiB write limit per mutation is plenty.

## 4. Raster delivery

Measured on a 64-member synthetic ensemble with 12 lead steps from +30 to +360 min. Real fields with terrain texture are probably 1.5–2× larger (unverified).

| Encoding | 256² per step | 400² per step (≈30 km at 75 m) | 400², 12 steps |
|---|---|---|---|
| RGBA PNG, continuous ramp | 26 KB | 47 KB (max 79) | 536 KB |
| **RGBA PNG, 16 probability levels (recommended)** | 11 KB | **20 KB (max 34)** | **234 KB** |
| Lossless WebP | 13 KB | 23 KB | 273 KB |
| Grayscale "data" PNG, coloured on the client | 13 KB | 24 KB | 266 KB |
| Contour GeoJSON (p = 0.1/0.5/0.9, simplified) | 12 KB | 28 KB | 297 KB |
| One arrival-quantile PNG per snapshot (RGB = p10/p50/p90) | 63 KB total | 154 KB total | — |
| Raw float32 member arrival grids (never ship) | 16.8 MB | 41 MB | — |

**Primary path.**
- The engine reprojects the native UTM grid once to an EPSG:4326 delivery grid (`rasterio.warp.reproject`).
- For each lead step it writes a 16-level, pre-coloured, transparent-background PNG at `cache/{scn}/{branch}/{cycleMs}/p_{lead}.png`. Contours for 0.1/0.5/0.9 come from `skimage.measure.find_contours` plus a shapely `simplify`.
- On the client: deck.gl `BitmapLayer({image: png_url, bounds: [w,s,e,n]})`, or a MapLibre `image` source using the 4 corners, or Cesium `SingleTileImageryProvider`. All three accept PNG plus bounds, so Linda's choice of map library does not change the contract.
- `BitmapLayer` draws flat. Draping it on 3D terrain needs `_TerrainExtension`, which is experimental; that is the frontend agent's call.

**Where the files live.**
- Mainline replay rasters are about 17 MB for 72 cycles. Copy them into `web/public/cache/` at build time so Vercel's CDN serves them and offline mode reads them locally.
- Live-branch rasters go to Convex file storage through an engine action that stores bytes, and the URL is written into `layers`.
- Phones subscribe only to `layers.contours` for the current snapshot, a few KB at p = 0.5.
- Rasters are immutable and content-addressed, so they cache forever.

**Optional upgrade.** The arrival-quantile PNG (154 KB) lets the slider render any lead time client-side with no extra fetches. Use it only if per-step fetches feel slow.

## 5. Replay/live parity

**Clock.**
- `ClockState` is an anchor. Every client computes `simNow()` locally, so nothing is written per tick.
- Play, pause, seek, and speed changes are Convex mutations that rewrite the anchor and bump `revision`.
- The engine subscribes to `clock:get` and wakes on changes.
- Live mode sets `anchor_sim = anchor_wall = now` and `speed = 1`.
- Demo speed is about 240× (1 sim hour ≈ 15 s), so a 5-minute cycle passes every ≈1.25 s of wall time. That forces precompute for the mainline.

**Adapters.**

```python
class Adapter(Protocol):
    sensor_ids: list[str]
    async def collect(self, a_ms: Ms, b_ms: Ms) -> list[Observation]   # available_ms in (a, b]
class ArchiveSource:  # replay: Parquet of past detections with observed_ms + modelled latency -> available_ms
class LiveSource:     # live: polls the feed, stamps available_ms = clock.wall_to_sim(now)
class InboxSource:    # camera/report/injected: drains Convex rows; observed_ms = clock.wall_to_sim(wall_ms) + offset
```

- GOES and VIIRS adapters emit **negative evidence** too: one `hot=false` footprint for each clear pixel in the domain. At 2 km over 30 km that is about 225 per scan.
- Clouds become `rejected` or are simply absent.
- HRRR goes through `ForcingProvider.at(t)`, not through `Observation`.

**Cycle loop.** This is the same code for precompute, replay, and live.

```python
async def cycle_loop():
    while True:
        k_ms = last_ms + interval
        await clock.wait_until_sim(k_ms)               # returns immediately on FastClock (precompute)
        obs = await adapters.collect(last_ms, k_ms)
        key = sha(scn, branch, k_ms, sorted(o.id for o in obs), model_version, seed)
        bundle = cache.get(key) or await run_cycle(branch, k_ms, obs)  # ProcessPool: advance+update+forecast+summarize+risk
        alerts = alert_rules.evaluate(bundle.risk, prev_risk, open_alerts)   # dedupe/supersede
        await sink.publish_cycle_bundle(bundle, alerts)  # one atomic mutation + events
        last_ms = k_ms
```

**Precompute.** `python -m ember.precompute --scenario eaton` runs this loop on a `FastClock` with a `CacheSink`. It writes a bundle JSON, PNGs, and an ensemble checkpoint (`npz`: float16 arrival grids, member parameters, weights; about 10–20 MB per cycle) for every cycle. Because the same code computes and replays, the replay is honest.

**Determinism.** Each cycle uses `np.random.default_rng([seed, branch_hash, cycle_index])`. Cycle *k* is reproducible without replaying the RNG history, and identical inputs produce identical bundles. That is what makes cache hits legitimate.

**Seek.**
- **Forward**: the engine publishes the cached bundles for every cycle ≤ t in batches of about 10 per mutation. The clock status shows `catching_up`.
- **Backward**: `engine.rewind(scn, branch, t)` deletes rows with sim time > t, so the database is exactly what the system knew at `sim_now`.
- Within a snapshot, scrubbing the forecast lead is client-side only.

**Branching on live injection.** A judge's camera detection or ground report arrives at `sim_now = t_i`.
1. The engine creates branch `b{n}` with parent `main`, forked at the last cycle at or before t_i, and sets `active_branch_id`.
2. The `branch_created` event makes the fork visible in the UI.
3. The engine loads that cycle's checkpoint and runs a live cycle with the injected observation. The clock is set to `status="computing"`, with an auto-pause, while that cycle runs.
4. When the cycle finishes, it publishes, and the UI shows a before/after contour diff.
5. Subsequent branch cycles are computed live. The clock stalls whenever compute lags behind it.
6. "Reset" returns to `main`.

The **latency budget for the moment-2 re-forecast is ≤ 10–15 s**. If it runs over, use a 32-member ensemble on the branch with 60-minute lead steps. A scripted injection that is identical to a precomputed one is a deterministic cache hit, and it must be labelled as cached in the event log.

## 6. API contracts

**Convex public functions (web and phones).**
- Queries:
  - `clock.get(scn)`
  - `snapshots.current(scn, branch, simNowMs)`: latest with `knowledge_ms ≤ simNow`
  - `layers.bySnapshot(id)`
  - `risk.bySnapshot(id)`
  - `alerts.list(scn, branch, states?)`
  - `observations.recent(scn, branch, sinceMs, limit≤200)`
  - `events.recent(scn, limit=50)`
  - `briefings.latest(scn, branch)`
  - `people.list(scn)`
  - `people.me(personId)`
  - `zones.list(scn)`
  - `sensors.list(scn)`
- Mutations:
  - Clock: `clock.play | pause | seek(simMs) | setSpeed(x) | goLive`
  - Alerts: `alerts.approve(id, operator, smsTextEdited?)`, `alerts.decline(id, operator, reason)`. Transitions are enforced server-side, and only `proposed → approved/declined` is client-callable.
  - People: `people.join(personId, name?, role)`, where the person ID is a UUID generated by the client and kept in localStorage; `people.setStatus`
  - Reports: `reports.submit(personId, kind, lon, lat, radius_m, text)`
  - Frames: `frames.generateUploadUrl()`, then `frames.create(cameraId, storageId, capturedWallMs)`
  - Branches: `branches.reset(scn)`
  - Stretch: `questions.ask(text)`
- **Engine-only internal functions** (Python uses `set_admin_auth`, from the deploy key in `.env`):
  - `engine.publishCycleBundle(bundle)`, `engine.rewind`, `engine.upsertAlert`, `engine.setClockStatus`, `engine.logEvents`, `engine.markFrame`, `engine.recordNotification`
  - Action `engine.storeFile(bytes, contentType) → {storageId, url}`. Bytes map to an `ArrayBuffer`. Function arguments can be up to 16 MiB; Node actions are limited to 5 MiB.
- **HTTP actions:**
  - `POST /twilio/sms`: form-encoded `From` and `Body` become a `Report`; validate `X-Twilio-Signature`.
  - `POST /twilio/status`: delivery callbacks go to `notifications`.
  - `GET /healthz`.

**Engine subscriptions**, using Python `subscribe` in a thread that feeds an `asyncio.Queue`:
- `clock.get`
- `alerts.approvedUnsent`
- `frames.pending`
- `reports.pending`
- `questions.pending`

**Engine HTTP** (FastAPI on localhost, dev and offline only):
- `GET /healthz`
- `POST /dev/load-scenario`
- `POST /dev/precompute`
- `GET /cache/*` (static, used by offline mode)

**StateSink interface.** This is the seam between A and B.

```python
class StateSink(Protocol):
    async def publish_cycle_bundle(self, b: CycleBundle, alerts: list[Alert]) -> None
    async def rewind(self, scn: str, branch: str, sim_ms: Ms) -> None
    async def emit(self, ev: ActivityEvent) -> None
    async def put_file(self, data: bytes, content_type: str) -> str   # returns public URL
    def commands(self) -> AsyncIterator[Command]                       # clock/approval/frame/report changes
# impls: ConvexSink, CacheSink (precompute), StdbSink (Option B)
```

**Web data layer.** Linda's components call only the following hooks: `useClock()`, `useSimNow()`, `useCurrentSnapshot()`, `useRisk()`, `useAlerts()`, `useEvents()`, `usePeople()`, `useControls()`. Each hook has two implementations, `ConvexSource` and `FileSource`. `FileSource` reads `cache/{scn}/manifest.json` plus the bundle JSONs and runs a local clock. It is Linda's fixture source from hour 1, and it is the last-resort offline demo that needs no backend at all.

## 7. LLM layer (Claude)

Model IDs below are as listed on platform.claude.com on Oct 24 2026.

1. **Cycle and alert briefing**, with `claude-sonnet-5-5` ("best speed + intelligence").
   - Input: the top-N `RiskAssessment` rows, deltas since the previous cycle, observation counts by sensor, provenance, and the alert being drafted. The model never sees raw ensembles.
   - Output: structured JSON through a tool schema — `headline`, `bullets[]`, `sms_text` (≤ 300 characters), and `voice_script` (≤ 45 s).
   - **Grounding validator.** Every number, time, and zone ID in the output must appear in the input table. Probability words come from a fixed mapping (p ≥ 0.9 → "very likely", and so on). Banned phrases include "will reach", "guaranteed", and "would have saved". On failure, use a deterministic template.
   - Alerts never wait for the LLM. The template text is proposed first, and the LLM version replaces it if it arrives before approval.
   - Mainline briefings are precomputed into the cache; branch briefings are generated live.
   - Budget: ≤ 8 s, async, with a "Claude drafting briefing…" event (unverified latency).
2. **Camera vision**, with `claude-haiku-5-5` ("fastest; classification, extraction").
   - One JPEG every 5 s from `/cam` goes to the engine.
   - The prompt returns `{smoke, flame, confidence, plume_base_x∈[0,1]}`.
   - **The bearing is computed in Python:** `heading + (x − 0.5)·hfov`, with sigma from the box width plus 2°. The result is a `BearingRay` observation with the camera's `trust`.
   - A detection must repeat in 2 consecutive frames before it counts.
   - Clock offset: `observed_ms = wall_to_sim(captured) + clock_offset_s`.
   - Budget: about 1–3 s (unverified). Vision never moves the forecast directly; only the filter does.
3. **"Ask the operating picture" agent** (stretch). Claude with tool use and read-only tools: `get_risk_table`, `get_zone`, `get_recent_observations`, `diff_snapshots`. It has one write tool, `draft_alert(zone_id)`, which creates only `proposed` alerts. Every tool call is logged as an `ActivityEvent` with actor `claude.agent`, which also makes the steps visible for the Microsoft and Factory-style criteria.

**Guardrails.**
- The LLM never computes probabilities or times.
- Approval is enforced in a Convex mutation, not in the UI.
- The notifier sends only when `state == "approved"` and `decided_by` is set.
- Every LLM output is stored with its `input_hash` and `prompt_version`.

## 8. Process topology, hosting, offline

- **Demo laptop.**
  - The `engine` is one Python 3.11+ process: an asyncio loop for clock, adapters, cycles, alerts, notifications, and LLM calls, plus a `ProcessPoolExecutor` for physics so the loop never blocks.
  - The dashboard runs from Vite on localhost or from Vercel.
  - The engine makes **outbound connections only**: Convex, Claude, Twilio, TTS. There are no tunnels.
  - Use a phone hotspot as backup uplink.
- **Convex cloud dev deployment** is the shared hub for all three laptops during integration. Each developer can also run an anonymous local backend (`npx convex dev`, port 3210) so iteration does not burn quota.
- **Vercel** hosts the static build: `/` dashboard, `/p` phone page (behind the QR code), and `/cam` camera console. Phones depend only on Vercel and Convex, and work over cellular.
- **Heavy compute.** Run the mainline precompute on the strongest laptop. If one 6-hour precompute of 72 cycles takes more than about 30 minutes, run it on a Vultr VM (an MLH prize). Live branch cycles run on the demo laptop.
- **Offline profile** (`EMBER_PROFILE=offline`):
  - The local Convex backend is seeded from `cache/` by `scripts/seed_offline.py`.
  - Rasters come from `web/public/cache`.
  - Briefings are cached; vision falls back to a cached detection.
  - Notifications are in-app only, with prerecorded audio.
  - The last resort is the `FileSource` mode with no backend at all.

**Notification channels.**
- **In-app** is the channel that always works: the `/p` page subscribes to alerts for its zone and plays TTS audio from a storage URL. Audio is unlocked by the tap on "Join".
- **Twilio voice and SMS** are best effort. Twilio's own docs say a trial account can only reach up to 5 *verified* numbers and must use Twilio-provided templates (no custom bodies). US 10DLC SMS requires A2P registration on a paid account. Toll-free numbers need verification.
- **So texting an arbitrary judge's phone today is unlikely.** Plan on in-app plus a voice call to a pre-verified team phone. Voice from a paid account to any number without registration is (unverified).

## 9. Repo layout and parallel work

```
ember/
  README.md  .env.example  justfile          # dev, precompute, seed-offline, demo
  engine/                                    # uv/pyproject; Cody + Abby
    ember/core/{models,clock,events,config,ids}.py
    ember/adapters/{base,goes,viirs,camera,inbox,forcing}.py
    ember/physics/{protocol,fake_ellipse,rothermel,ensemble,filter,likelihoods}.py   # Abby
    ember/loop/{cycle,cache,branch}.py
    ember/forecast/{summarize,rasterize,contours}.py
    ember/risk/{exposure,risk}.py   ember/alerts/{rules,dedupe}.py
    ember/llm/{briefing,vision,agent,validate}.py  prompts/
    ember/notify/{base,inapp,twilio_voice,sms,tts}.py
    ember/sync/{sink,convex_sink,cache_sink}.py   ember/api/app.py
    scripts/{precompute,seed_offline,export_schema}.py   tests/ (golden-bundle tests)
  web/                                       # Vite+React+deck.gl; Linda
    convex/{schema,clock,snapshots,risk,alerts,people,reports,frames,events,engine,http}.ts
    src/live/{ConvexSource,FileSource,hooks}.ts  src/contracts.ts
    src/{dashboard,phone,cam,map}/   public/cache/  public/scenarios/
  data/scenarios/eaton/{scenario.yaml,zones.geojson,assets.geojson,cameras.yaml,
                        archive/{goes,viirs}.parquet, forcing/*.nc, terrain.tif, fuels.tif}
  cache/                                     # git-ignored, plus a small committed fixture/ bundle
```

**HazardModel contract (Abby).** The fake ellipse model ships in hour 1 so everything downstream runs immediately.

```python
class HazardModel(Protocol):
    def init_ensemble(self, scn: Scenario, forcing: Forcing, rng) -> EnsembleState
    def advance(self, s: EnsembleState, t0: Ms, t1: Ms, forcing: Forcing) -> EnsembleState
    def forecast_arrival(self, s: EnsembleState, t0: Ms, horizon_min: int, forcing: Forcing) -> np.ndarray  # (M,H,W) minutes, inf = never
    def predict_obs(self, s: EnsembleState, obs: Observation) -> np.ndarray  # per-member predicted quantity
class Likelihood(Protocol):  # registry keyed by Observation.kind; a new sensor never touches the filter
    def loglik(self, predicted: np.ndarray, obs: Observation, spec: SensorSpec) -> np.ndarray  # (M,)
class Assimilator(Protocol):
    def update(self, s: EnsembleState, obs: list[Observation], specs, rng) -> tuple[EnsembleState, dict]
```

The risk engine uses `forecast_arrival`. Zone arrival per member is the minimum over the zone's cells. Then `P(≤t)` is the weighted fraction of members, `act_by = arrival_q(α) − evac_min`, and the rank is ordered by tier first, then score.

**Plan.**

| Time | Cody | Linda | Abby |
|---|---|---|---|
| H0–1 | Pick the hub. Freeze `models.py` and `contracts.ts`. Write `fake_ellipse` and the fixture generator (the payload script is a starting point). Commit `cache/fixture/`. | — | — |
| H1–6 | Convex schema and functions, `ConvexSink`, clock, archive adapters, cycle loop running end to end on the fake model | UI built against `FileSource` fixtures | Rothermel ensemble and filter, behind the Protocol with golden tests |
| H6 | E2E milestone on Convex | | |
| H12 | Real-physics mainline precompute | | |
| H16 | Live branch injection, camera, phone page | | |
| H20 | Feature freeze; record video | | |
| H22 | Offline rehearsal | | |

Each coding-agent task gets the interface, a fixture, and an acceptance test.

## 10. Risks and open questions

1. **Live re-forecast latency**, owned by physics. Benchmark one cycle of 64 members at 400² and 6 h by H8.
2. **Twilio compliance** (section 8). Confirm early whether the team has a paid Twilio account. Voice-provider choice (Grok / Cartesia / ElevenLabs) is a separate agent's scope.
3. **Convex free caps** (1 GB/month database I/O, 1 GB/month file egress, hard caps). Use indexed `take(n)` queries, avoid whole-table subscriptions, serve replay rasters from Vercel, and develop on local backends. Hackathon credits are (unverified).
4. **Prize strategy A vs B.** If the team values the SpacetimeDB criteria fit, switch in hour 1 using the Option B adaptations in section 2. Never run both (principle 3).
5. **Reprojection and 3D draping** of rasters (section 4).
6. **Time semantics** (`knowledge_ms` vs `valid_ms` vs `wall_ms`): enforce them through the contracts and through suffix naming.

## Sources

- SpacetimeDB HTTP database API (call reducer, SQL, routes), docs v2.0.0: https://spacetimedb.com/docs/http/database
- SpacetimeDB HTTP handlers (beta): https://spacetimedb.com/docs/functions/http-handlers
- SpacetimeDB FAQ (module languages, self-host, maincloud tiers, procedures): https://spacetimedb.com/docs/intro/faq
- SpacetimeDB Next.js quickstart (useTable/useReducer, spacetime dev): https://spacetimedb.com/docs/quickstarts/nextjs
- SpacetimeDB SATS-JSON: https://spacetimedb.com/docs/sats-json ; WebSocket protocol (1.0 RC3 page): https://spacetimedb.com/docs/ws
- SpacetimeDB v2.10.0 release notes: https://newreleases.io/project/github/clockworklabs/SpacetimeDB/release/v2.10.0
- npm `spacetimedb` 2.13.0 package source (inspected locally: react hooks, server http_handlers withTx, t.byteArray); PyPI `spacetimedb-sdk` (last release 0.7.0, 2023-12-09): https://pypi.org/project/spacetimedb-sdk/
- Convex Python client 0.8.1: https://pypi.org/project/convex/ ; https://github.com/get-convex/convex-py
- Convex limits: https://docs.convex.dev/production/state/limits
- Convex local deployments: https://docs.convex.dev/cli/local-deployments ; anonymous dev: https://stack.convex.dev/anonymous-development
- Convex HTTP actions: https://docs.convex.dev/functions/http-actions ; file storage store/serve: https://docs.convex.dev/file-storage/store-files , https://docs.convex.dev/file-storage/serve-files
- deck.gl BitmapLayer: https://deck.gl/docs/api-reference/layers/bitmap-layer ; TerrainExtension: https://deck.gl/docs/api-reference/extensions/terrain-extension
- Cloudflare Quick Tunnels: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/
- Twilio trial account restrictions: https://www.twilio.com/docs/messaging/guides/how-to-use-your-free-trial-account ; A2P 10DLC blocking: https://www.twilio.com/en-us/changelog/-u-s--a2p-10dlc--full-blocking-of-traffic-sent-from-unregistered
- Claude models overview: https://platform.claude.com/docs/en/models/overview
- Tiger Data (free-plan details not confirmed): https://www.tigerdata.com/docs
