# 03: Engine: clock, replay loop, publisher, risk, alerts (owner: Cody; `metrics.py` by Abby)

**Goal.** One Python 3.12 process on the demo laptop: `python -m ember.run --scenario eaton --profile online|offline`.

**What it does**
- Releases data at its real availability time.
- Runs the cycle loop: observations → HazardModel → assets.
- Publishes a `CycleBundle` per cycle with risk, alerts and people derived from current hub state.
- Reacts to commands.
- Serves files on `localhost:8787`.

**Connections are outbound only:** hub, Claude, Cartesia.

**Inputs** come from the scenario package built by `01_data_pipeline.md`. Read **01 §0 (layout), §3 (GOES table), §4 (VIIRS), §5 (HRRR files), §8 (replay manifest)** for column names.

## 1. Modules

```
runtime/clock.py      SimClock (hub anchor), FastClock (precompute)
runtime/sources.py    ReplaySource, InboxSource, SyntheticSource (tests), ForcingProvider
runtime/model_host.py owns the HazardModel instance: init, checkpoint, restore-and-roll-forward
runtime/loop.py       physics cycle loop (writes the PhysicsCache)
runtime/publisher.py  turns cached physics outputs + live hub state into CycleBundles and publishes them
runtime/cache.py      PhysicsCache (per-cycle physics outputs, checkpoints, briefing cache)
runtime/branch.py     fork / reset
runtime/summarize.py  ForecastArrays -> arrival_q.bin, perimeter/isochrones/danger GeoJSON, stats
risk/risk.py          zone + people risk from region arrivals (Cody)
risk/alerts.py        frozen rules -> proposals, templates (Cody)
risk/metrics.py       honest number + evaluation (Abby, after the 18:00 gate)
sync/sink.py          StateSink Protocol; stdb_sink.py; cache_sink.py; poller.py
api/local.py          FastAPI :8787
run.py                entrypoint
```

**Key split.** Physics outputs are cached. **Risk, alerts and people are re-derived at publish time** from the cached `zone_arrivals` plus the hub's current `evac_min` values, people and alert states. Operator edits and joined phones therefore survive cached replay.

## 2. Clock

- **`SimClock`.** The poller reads the hub `clock` row (500 ms playing, 1 s paused) and computes `sim_now()` with the anchor formula (`00` §3.2).
  - `wait_until_sim(t)` sleeps `(t − sim_now)/speed` in ≤ 250 ms slices.
  - It wakes early on a poller signal: seek, reset, branch or play.
- **`FastClock`** (precompute): `wait_until_sim` returns immediately.
- **The hub owns the clock.** The engine only calls `set_clock_status`.
- **Auto-pause** on new proposals happens in the hub (`04` §2.2 rule 8). The engine doesn't pause on its own.

## 3. Sources

```python
class ReplaySource:
    def collect(self, a_ms, b_ms) -> tuple[list[ObservationRecord], list[ScanEvent]]:
        # manifest entries with available_ms in (a, b] AND source in scenario.assimilate_sensors (or display-only scans)
        # GOES: one ObservationRecord per pixel row (hot AND clear); obs_id per 00 §1; footprint_key "goes18:{px_x}:{px_y}";
        #       trust from the table; ScanEvent per scan (assimilated = source in assimilate_sensors)
        # VIIRS: one record per detection row; ScanEvent(mode="pass") per overpass
class InboxSource:      # accepted reports (and P2 camera obs) waiting for the next cycle
class SyntheticSource:  # tests only: a scripted list of scans/obs over a flat landscape (no files needed)

@dataclass
class WeatherAsOf: weather: Weather; init_ms: int; available_ms: int
class ForcingProvider:
    def weather_as_of(self, t_ms) -> WeatherAsOf   # newest HRRR init with available_ms <= t_ms; fxx 0-9 as hourly bands
```

**Rules**
- **No-future-data assertion (in code):** every record passed at cycle k has `available_sim_ms ≤ k`, and `weather.available_ms ≤ k`.
- **Clear pixels** go to physics but are never published. The hub sees `ScanEvent.n_clear`.
- **Sensor status:**
  - GOES is `stale` if no scan has arrived in 15 sim-min, and `down` if disabled.
  - VIIRS is always `ok` and shows its last pass.

## 4. Physics loop (`runtime/loop.py` + `model_host.py`)

**Cycle times:** `k = replay_start + n·5 min`.
- Before `t_ignition`: no physics. Produce **pre-ignition bundles** (`cycle = None`) carrying scan events, hot observations, sensors and events.
- The first cycle ≥ `t_ignition` calls `model.init(...)` with the `Ignition` from `scenario.yaml`.

```python
def physics_cycle(host, branch, k, new_obs, scans) -> PhysicsOutput:
    w = forcing.weather_as_of(k); assert w.available_ms <= k
    if w.init_ms != host.weather_init_ms: host.model.update_weather(w.weather)
    arrays = host.model.step(ms_to_min(k), new_obs)                 # new obs only; physics keeps history
    assets = summarize.write_assets(arrays, branch, k)              # §6
    zone_arr = host.model.region_arrivals(zone_index, n_zones)      # (N,Z)
    area_arr = host.model.region_arrivals(area_x_index, 1)          # (N,1)
    out = PhysicsOutput(cycle=..., assets=..., zone_arr=zone_arr, area_arr=area_arr, weights=arrays.weights,
                        obs_status=map_obs_used(arrays.obs_used), scans=scans, events=[...])
    if (k - t_ignition) % 30min == 0: cache.save_checkpoint(branch, k, host.model.save_state())
    return out
```

**Physics cache key (chained):** `key_k = sha256(key_{k−5min}, sorted(new obs ids), model_version, config_hash, seed, filter_mode)`. Outputs depend on all earlier observations, not only this window's.

**Cache hit vs miss:**
- On a hit, load `PhysicsOutput` from `cache/{scn}/{branch}/{k}/` (`physics.json` + `zone_arrivals.npz` + assets) and do **not** step the model.
- On a miss, `model_host.ensure_state(branch, k − 5 min)` first:
  1. If the in-memory model isn't at `k − 5`, `load_state` the newest checkpoint ≤ `k − 5`.
  2. Roll forward with the cached observations (deterministic; ≤ 6 cycles).
  3. Then run `physics_cycle`.

**obs_used → observation status.** `used=True` → `assimilated`, `influence = ll_spread`; `used=False` → `rejected`. Only published observations need this.

## 5. Precompute and cache (`python -m ember.scripts.precompute`)

```
python -m ember.scripts.precompute --scenario eaton --branch main     --filter pf
python -m ember.scripts.precompute --scenario eaton --branch openloop --filter none
python -m ember.scripts.precompute --scenario eaton --branch demo_b1 --script config/demo_report.yaml   # §10.3
```

**Per cycle**, it writes to `cache/{scn}/{branch}/{k}/`:
- `physics.json`: cycle fields except `source`, plus obs status, scans and events
- `zone_arrivals.npz`: `zone_arr`, `area_arr`, `weights`
- `arrival_q.bin`, `perimeter.geojson`, `isochrones.geojson`

**Also written:**
- `index.json` (`00` §4)
- `checkpoints/{k}.npz` every 30 sim-min
- **briefings** for the alerts that the default-evac risk would propose, in `cache/{scn}/briefings/{input_hash}.json`. Precompute **awaits** Claude here, one briefing per cycle for its highest-level new alert.

**Cost:** 156 cycles × (~0.5 s physics + ~0.3 s summarize) ≈ 2–3 min, plus ~10–20 Claude calls.

**Freeze protocol** (one protocol; `08` §2):
1. Choose `pf` vs `reinit` and tune priors on the twin experiment + **the first 2 h of the Eaton fire only** (18:15–20:15 PST).
2. Run `python -m ember.scripts.freeze` **once**. It hashes `scenario.yaml` + `config/alert_rules.yaml` + `hazard.MODEL_VERSION` and writes `config_hash` into `scenario.yaml` and `web/manifest.json`.
3. The **first full `main` + `openloop` precompute after the freeze is the reported run (v1).**
4. Any later change produces v2. It is published alongside v1 and disclosed. Target: freeze by **Sat 20:00**.

**Web copy:** copy `cache/{scn}/main` (minus checkpoints and npz) plus `data/scenarios/{scn}/web` into `web/public/` for the Vercel build.

## 6. Summarizer (`runtime/summarize.py`, ≤ 0.3 s)

1. **`arrival_q.bin`** exactly per `00` §4.2 (`v = 0` if burned, `ceil(t_q/2)` capped at 254, NaN → 255).
2. **`perimeter.geojson`** with exactly two features (`00` §4.3):
   - `burned`: `rasterio.features.shapes` of `p_burned_now ≥ 0.5` in the grid CRS → union → lon/lat → simplify 30 m.
   - `front`: the boundary of `burned` against burnable unburned cells.
3. **`isochrones.geojson`:** for each lead 60…360 (every 60 min), the polygon `t_q[q=0.5] ≤ lead`, written as its exterior lines.
4. **`danger`:** `lead60_p30` = polygon of `t_q[q=0.3] ≤ 60`; `lead120_p30` = polygon of `t_q[q=0.3] ≤ 120`. Simplify until each is ≤ 10 KB.
5. **Stats:**
   - `burned_ha = Σ p_burned_now · 0.81`
   - `front_km`
   - `growth_ha_per_h` (vs the previous cycle)
   - `head_bearing_deg`: from the burned centroid to the farthest `q50 ≤ 60` cell
   - `data_through_sim_ms` = max `observed_sim_ms` of the assimilated observations so far

## 7. Publisher, risk and people (`runtime/publisher.py`, `risk/risk.py`)

For each cycle the clock reaches (`k ≤ sim_now`), on the active branch:

```python
def build_bundle(phys: PhysicsOutput, hub: HubSnapshot) -> CycleBundle:
    evac = {z: hub.zone[z].evac_min for z in zones}           # operator edits always apply
    risk = risk_engine.zones(phys.zone_arr, phys.weights, k, evac, prev_risk, hub.open_alerts)
    alerts = alert_engine.evaluate(risk, hub.alerts, k)        # §8; open_alerts mirrored from hub
    risk = risk_engine.patch_status(risk, alerts)              # status 'order' if an R2 alert is now open
    briefings = briefing_cache.lookup(alerts.new)              # same input_hash as precompute -> reuse; else []
    people = risk_engine.people(hub.people, risk)              # person risk = their ZONE's values
    reports = inbox.status_updates()
    return CycleBundle(cycle=phys.cycle | {source: "cache" if phys.cached else "live"}, zone_risk=risk, ...)
```

- **Async briefings.** New proposals without a cached briefing are published with template text. In the online profile, `ai/briefing.py` then runs in the background (`05` §1) and calls `upsert_alert_draft`.
- **`open_alerts`** is in-memory, mirrored from the hub `alert` table. It is rebuilt from SQL at startup and after every `reset`/seek.

**Zone risk.** Inputs: `A = zone_arr (N,Z)` in minutes since `t_ref`; `w` = weights; `k` = issue time.

- `P_z(t) = Σ_m w_m·1[A_mz ≤ t]`
- `p_by_lead[i] = P_z(k + LEADS[i])`. Use `pAt(lead)` / `lead_idx` (`00` §3.1); **never index by minutes.**
- **ETA quantiles:** sort members by `A_mz` and walk the cumulative weights. `eta_q` = the first A with cumulative weight ≥ q, if ≤ `k + horizon`; otherwise null.
- `act_by = eta_q30 − evac_min`. `act_by_basis = "q30 arrival − {evac} min evac"`.
- **Status:**

  | status | Condition |
  |---|---|
  | order | `p_at(60) ≥ 0.5`, or an R2 alert for the zone is open |
  | warning | `p_at(120) ≥ 0.3` |
  | watch | `p_at(360) ≥ 0.1` |
  | clear | otherwise |

- `risk_score = p_at(120)·(1 + log10(1 + population))·{1: 2.0, 2: 1.3, 3: 1.0}[tier]`
- **Rank:** status severity desc, then `act_by` asc (nulls last), then `risk_score` desc.
- **Trend:** `p_at(120)` vs the previous cycle on this lineage; ±0.05 threshold; `flat` on the first cycle.
- **Drivers:** up to 3, e.g. `"P 62% by +2h"`, `"nursing home"`, `"1 egress road"`.

**People.** `p_60`, `eta_q50_sim_ms` and `act_by_sim_ms` are **copied from their zone's `ZoneRisk`**, so the phone and the dashboard always agree. Include every joined person whose values changed.

**Seek forward** across cycles:
- Publish bundles for all cycles ≤ the target in batches of ≤ 10 (`publish_bundles`), status `catching_up`.
- Cycles missing from the cache are computed (§4 miss path).
- Then set status `ok`.

## 8. Alerts (`risk/alerts.py`)

**Evaluating the frozen rules** (`00` §7):
- **Cooldown:** per zone and level, counted from the previous `triggered_sim_ms`, whatever that alert's state.
- **Supersede:** a new `order_recommended` moves a **`proposed`** warning for the same zone to `superseded`. Approved alerts are never touched.
- **Merge:** if ≥ 3 zones cross the same rule in one cycle, propose one alert. `zone_ids` are ranked, `alert_id` uses `zone_ids[0]`, and the cooldown applies to all of them.
- **`act_by_within_min`:** `(act_by − k) ≤ 15 min`, including negative values.

**Templates** (always available). They use facility **types**, never names, and always carry the demo framing:

```
text:  "{LEVEL} recommended — {zones}: fire {p_words} within {lead_h} h (P {p:.0%}). Act by {act_by:%H:%M} PST.
        ~{pop:,} residents{, critical types}. Forecast {k:%H:%M}, satellite data through {data_through:%H:%M}."
voice: "This is Ember. Briefing for the duty officer. Recommend {level_words} for
        {zones_spoken}. Fire is {p_words} to reach the zone within {lead_words}. Act by {act_by_spoken}. About {pop}
        residents{, critical_spoken}. This is decision support; you make the call. Say 'acknowledged' to confirm."
```

`p_words` mapping:

| P | Words |
|---|---|
| ≥ 0.9 | very likely |
| ≥ 0.7 | likely |
| ≥ 0.5 | more likely than not |
| ≥ 0.3 | possible |
| < 0.3 | unlikely |

**State ownership** (`04` §2.2):
- **Engine:** `proposed`, `superseded` (via bundles); `calling`, `called`, `failed` (via `update_alert_call`).
- **Humans:** `approved`, `declined`.
- **Cartesia webhook:** `acknowledged`.
- The engine sends nothing unless `state == approved` and `decided_by` is set.

**On an `alert_approved` command:**
- Phones in the zone react on their own: they subscribe to `alert`.
- Online, with the Cartesia gate passed: `notify/cartesia.py` (`05` §4) places the call → `calling` → `called` / `failed`.
- Offline: `failed` with `call_status = "offline"`. The dashboard offers "Play briefing here".

## 9. Metrics: the honest number (`risk/metrics.py`; Abby after the 18:00 gate)

Computed **after** precompute from the `main` and `openloop` caches. Published as **post-hoc** `Metric` rows (`computed_sim_ms = null`) in the first bundle after load. The dashboard shows them in a separate **"Evaluation (computed after the full replay)"** card, toggled from `/control` (`06` §5.6), so they are never gated on replay time.

**Satellite hit function.** `area_x_satellite_hit(scan_pixels)` is shared **verbatim** with `scripts/check_goes.py` (`01` §2), so the gate and the metric use one definition. A scan is a hit if any parallax-corrected pixel has:
- `fdc_class ∈ GOES_STRONG`,
- ≥ 50% of its footprint area inside area X, and
- a footprint lying wholly west of Lake Ave.

| key | definition |
|---|---|
| `t_sat_goes` | First assimilated GOES-18 scan that is a hit, where 2 of the 3 consecutive scans starting at it are hits |
| `t_sat_viirs` | First VIIRS detection (n/h confidence) whose footprint centre is inside area X |
| `t_sat` | `t_sat_goes` if not null, else `t_sat_viirs` (state which) |
| `t_fc` | Earliest cycle `k < t_sat` with **P(area-X arrival ≤ t_sat) ≥ 0.5**, from `area_arr` at k. This rewards predicting the right time, not just "within 6 h". |
| `lead_time_min` | `t_sat − t_fc` for `main`; `lead_time_min_openloop` for `openloop`. **Reported even if negative or null.** |
| `eta_interval_at_t_fc` | q10–q90 area-X ETA at `t_fc`, and whether `t_sat` falls inside it (`detail`) |
| `sorensen_feds` | Sørensen of the P(burned) ≥ 0.5 region vs NASA FEDS perimeters at their timestamps (P1) |
| `false_alarm_zone_hours` | Zone-hours with `p_at(120) ≥ 0.5` for zones with no destroyed structures per CAL FIRE damage inspection (DINS) data (P2, per-zone aggregate only) |
| `goes_latency_median_min` | From `truth/latency.json` |

Context, not a score: the 03:25 order time from `timeline.json`, with the disclaimer in `08` §2.

## 10. Commands, branching, reset

**Poller** (`sync/poller.py`): `asyncio` task.
- Reads commands + clock + zone + person (`04` §3).
- Sorts commands by `created_wall_ms` and dispatches them.
- Updates the `HubSnapshot` used by the publisher.

**Loop and poller.** The physics/publish loop is one task. On seek, reset or fork, the poller sets `loop.retarget(next_k, branch)` and wakes it. The loop finishes its current cycle and then continues from the new target.

| command.kind | Engine action |
|---|---|
| `clock_play` / `clock_pause` / `clock_speed` | Wake the loop |
| `clock_seek {sim_ms, from_sim_ms}` | **Forward:** catch up (§7). **Backward:** `rewind(active, sim_ms)`. If the active branch is not `main` and `sim_ms < fork_sim_ms`, also rewind the branch to its fork, switch `active_branch_id` to the parent, and rewind that. Rebuild `open_alerts`. |
| `reset` | The hub has already hard-reset. Reload state, clear the inbox, set the branch counter to 1, retarget to `replay_start`. |
| `alert_approved {alert_id, operator}` | §8 |
| `alert_declined` | Log an event |
| `evac_min {zone_id, minutes}` | Republish the current cycle's bundle (risk recomputed from cached `zone_arrivals`) |
| `person_joined {person_id}` | Include the person's zone risk in the next bundle; or publish immediately with the current cycle's values via `publish_bundle` (same cycle, people only) |
| `report {report_id}` | §10.1, then §10.2 |
| `drop_sensor` | P2 |

### 10.1 Report acceptance
1. Read the `report` row and its trust. Convert the report to an observation with `REPORT_HOT` (`00` §3.1): `hot`, and `trust = report.trust × factor`.
2. **Reject** (with a reason) if any of these holds:
   - the point is outside the AOI;
   - the same person reported < 60 wall-s ago;
   - the kind is `all_clear` inside a cell where `arrival_q[q=0.9] == 0` (certainly burned).
3. **Accept:** create `ObservationRecord(obs_id = "obs_rep_{report_id}", kind = "ground_report", geometry = PointRadius, report_kind = …, provenance = report.provenance, observed_sim_ms = report.observed_sim_ms, available_sim_ms = sim_now)`. Put it in `InboxSource`, and put a `ReportStatus` in the next bundle.

### 10.2 Branch on injection (Moment 2). Budget ≤ 10 s, target ≤ 5 s
1. `t_i = sim_now`. Call `set_clock_status(computing)`; the hub pauses and saves `resume_playing`. Emit `Branch b{n} forking at HH:MM`.
2. `model_host` loads the newest checkpoint ≤ `t_i` on the active lineage and rolls forward to the last cycle ≤ `t_i`. These re-run cycles are **not published**: they reproduce the parent exactly, because the RNG is branch-independent (`00` §6).
3. `create_branch({branch_id: b{n}, parent: active, fork_sim_ms: last cycle ≤ t_i})`.
4. Run the next cycle (`k = next 5-min mark`) with the parent's observations **plus the report**. Publish it under `b{n}`; this bundle carries only the **new** observations (the report) and new cycles.
5. Call `set_clock_status(ok)`; the hub restores play. Later cycles on `b{n}` compute live (≤ 1 s each). If compute lags, the clock waits in `computing`.
6. The UI diffs the parent's cycle at the fork vs the branch's new cycle (`06` §4).

### 10.3 Scripted rehearsal branch (fallback)
`config/demo_report.yaml` fixes the demo report: person spawn, point, kind, trust and the sim time to inject it. Precompute it as branch `demo_b1`.
- Also **measure** the q50 +2 h isochrone shift it causes and the change in the area-X P. Write both to `cache/{scn}/demo_b1/effect.json` and into `08`. If the shift is invisible, pick a better report point **before** the freeze.
- If live branching fails in rehearsal, a report matching the script is served from `demo_b1`. **The dashboard shows "Cached rehearsal branch" whenever it is active.**

## 11. Local API (`api/local.py`, FastAPI, `0.0.0.0:8787`)

CORS allows `http://localhost:*`. Bind to the LAN only if `/control` needs it; that is offline-only, never exposed publicly.

| Endpoint | Purpose |
|---|---|
| `GET /cache/{path}` | Files from `cache/`, gzip, `Cache-Control: immutable` |
| `GET /static/{scn}/{path}` | From `data/scenarios/{scn}/web/{path}`. `--fixtures` serves `fixtures/static/...` and `fixtures/cache/...` instead. |
| `GET /healthz` | Profile, hub connectivity, cache coverage, last cycle, demo laptop timing (physics ms) |
| `POST /dev/duty-officer {number}` | Called from the **dashboard** (same laptop). Holds the number **in memory only** for the next Cartesia call; returns it masked. |
| `POST /dev/report {lon, lat, kind, observed_sim_ms}` | Offline / dashboard injection path. Calls `submit_report_engine` with `channel = "dashboard"`, `trust = 0.5`, `provenance = "injected"`. |
| `POST /dev/seek-preset {name}` | Rehearsal convenience |

## 12. Sinks

```python
class StateSink(Protocol):
    async def load_scenario(self, payload: dict) -> None
    async def publish(self, b: CycleBundle) -> None
    async def publish_many(self, bs: list[CycleBundle]) -> None
    async def rewind(self, scn: str, branch: str, sim_ms: int) -> None
    async def set_clock_status(self, scn: str, status: str) -> None
    async def create_branch(self, branch: dict) -> None
    async def upsert_alert_draft(self, alert_id: str, text: str, voice: str, source: str, briefing: Briefing) -> None
    async def update_alert_call(self, alert_id: str, state: str, call_id: str | None, call_status: str | None) -> None
    async def append_events(self, events: list[ActivityEvent]) -> None
    async def submit_report_engine(self, report: Report, transcript: str | None) -> None
    async def poll(self) -> HubSnapshot          # commands + clock + zones + people
    async def mark_command(self, command_id: str, status: str, result: dict) -> None
```

- `StdbSink` uses the HTTP client (`04` §3).
- `CacheSink` (precompute) writes files only.
- Sink errors are retried per `04` §3. A final failure emits an `error` event; the loop never crashes.

## 13. Profiles

| | `online` | `offline` (Wi-Fi died) |
|---|---|---|
| Hub | Maincloud | local `spacetime start` |
| Briefings | cached + live Claude | cached only |
| Call | Cartesia | none: "Play briefing here" with browser `speechSynthesis` |
| Phones | yes | **no** (say so) |
| Moment 2 | phone voice report | dashboard click-to-report → `/dev/report` |

## 14. Acceptance tests

1. **`SyntheticSource` + `FakeHazardModel`:** precompute 40 cycles, then replay at 900× into a local hub. Every cycle appears once. Seek forward +3 h then backward to +1 h leaves exactly the rows ≤ +1 h.
2. **Determinism:** two precomputes give identical cache keys and byte-identical `arrival_q.bin`. **Cache miss after hits:** delete cycle 25's cache; the replay restores checkpoint 24 and produces a byte-identical cycle 25.
3. **No future data:** the assertions in §3/§4 fire on a doctored manifest.
4. **Alerts:** a synthetic risk sequence gives R1 → cooldown → R2 superseding the proposed R1. An approved R1 is never superseded.
5. **Operator edits survive replay:** `set_evac_min` on a zone → the next 3 cached cycles publish with the new `evac_min` and act-by.
6. **Branch:** inject the demo report. The branch cycle publishes in ≤ 10 s, `sim_now` is continuous across `computing`, the q50 +2 h isochrone moves by the amount in `effect.json`, and reset returns to `main`.
7. **Metrics:** `area_x_satellite_hit` gives the same scans in `check_goes.py` and `metrics.py`, and both branch metrics are written with definitions.

## 15. Build order

| By | Owner | Deliverable |
|---|---|---|
| 08:00 | Cody | `contracts.py` + analytic fixture (`00` §10) |
| 09:30 | Cody | Hub spike (`04` §1) |
| 11:00 | Cody | `SyntheticSource`, `ReplaySource` on the real manifest, `ForcingProvider`; physics loop with the fake model + `CacheSink` |
| 13:00 | Cody | **M1:** publisher + `StdbSink` + poller + risk + alerts + summarizer; replay into the dashboard |
| 17:00 | Cody | Real model wired in; checkpoints; cache-miss path; local API |
| 18:00 | Abby / Cody | **M2:** gate; freeze by 20:00; v1 precompute main + openloop; briefings cached |
| 21:00 | Cody | Branching on report; `demo_b1` + `effect.json` |
| 21:00 | Abby | `metrics.py` + Evaluation numbers |
| 22:00 | | **M3** |
