# 04: Shared-state hub: SpacetimeDB module (owner: Cody)

**Role.** SpacetimeDB holds the **shared operating picture** and fans it out live to the dashboard, the `/control` device and judges' phones. It contains:
- the clock and the published cycles
- the risk ranking
- alerts and their approval state machine
- people, reports and sensors
- the activity feed

The Python engine and the Vercel functions coordinate through it as agents. Science data never goes in the database: rasters, pixel tables, checkpoints. Why SpacetimeDB: `SPEC.md` D5.

**Versions.** Pin `spacetimedb@2.13.0` (npm) and the matching CLI. **Have Cursor index https://spacetimedb.com/docs** and follow the **TypeScript module** and **TypeScript client** docs for exact syntax. Ignore older Rust or C# examples.

**Development.** Develop against a local `spacetime start` (`STDB_URI=http://localhost:3000`). Use Maincloud only for phone tests and the demo; this saves its free energy budget.

## 1. Spike: G1-b gate (Sat 08:00–09:30, ≤ 90 min)

Pass criteria (all four):
1. The `stdb/` TypeScript module is published as `emberline` (local, then Maincloud). It has `config`, `clock`, `cycle` and `command`, plus reducers `set_identities`, `clock_play` and `publish_bundle`.
2. **Python → reducer:** `engine/emberline/sync/stdb_http.py` calls `publish_bundle(bundle_json)` over HTTP with a fixture bundle, using the engine identity's token, and the row appears.
3. **Python reads:** the same client runs `SELECT * FROM command WHERE status = 'pending'` over HTTP and parses the result.
4. **React subscribes:** a bare React page with the generated bindings shows `cycle` rows appearing live. A button calling `clock_play` inserts a `command` that step 3 sees within 1 s.

**If any of the four fails at 09:30, switch to Convex (§7).** Don't debug past the gate. Afterwards, note the Maincloud energy usage. If it looks tight, plan a self-hosted instance on a Vultr VM (`spacetime start` + Caddy TLS) by M2.

## 2. Module (`stdb/src/index.ts`)

**Conventions**
- All times are `f64` epoch ms; IDs are `string`.
- Nullable fields use `t.option(...)`. Counts are `u32`/`i32`; never 64-bit ints.
- Nested data is stored as `*_json: string`.
- Add an index on every column used in a subscription `WHERE`.

### 2.1 Tables

| Table | PK | Columns (beyond PK) | Notes |
|---|---|---|---|
| `config` **(private)** | `id` = "singleton" | `engine_identity`, `service_identity` (Identity), `operator_pin` | Set once by `set_identities` |
| `scenario` | `scenario_id` | `title`, `tz`, `manifest_json`, `config_hash` (option), `replay_start_sim_ms`, `replay_end_sim_ms` | |
| `clock` | `scenario_id` | `mode`, `playing`, `speed`, `anchor_sim_ms`, `anchor_wall_ms`, `active_branch_id`, `status`, `resume_playing`, `pause_on_proposal`, `epoch: u32`, `min_sim_ms`, `max_sim_ms` | `00` §3.2 |
| `branch` | `branch_id` | `scenario_id`, `parent_id` (option), `fork_sim_ms` (option), `reason`, `created_wall_ms` | `main` row created on load |
| `sensor` | `sensor_id` | `SensorInfo` fields | |
| `scan_event` | `scan_id` | `ScanEvent` fields | index (`scenario_id`) |
| `observation` | `obs_id` | `ObservationRecord` fields; `geometry_json` | Hot satellite pixels + non-satellite only. Index (`scenario_id`) |
| `cycle` | `cycle_id` | `Cycle` fields; `n_obs_by_kind_json`, `assets_json`, `danger_json`, `params_summary_json` | index (`scenario_id`) |
| `cycle_head` | `id` = `{scenario}:{branch}` | `scenario_id`, `branch_id`, `cycle_id`, `knowledge_sim_ms`, `danger_json`, `burned_ha` | One row per branch. Advance only if the new `knowledge_sim_ms` ≥ the current one (`rewind` recomputes it). **Phones subscribe here.** |
| `zone` | `zone_id` | `ZoneFeatureProps` fields + `evac_min` (editable) | Polygons stay in static GeoJSON |
| `zone_risk` | `id` = `{cycle_id}:{zone_id}` | `ZoneRisk` fields; `p_by_lead_json`, `drivers_json` | index (`scenario_id`) |
| `alert` | `alert_id` | `Alert` fields; `zone_ids_json` | index (`scenario_id`) |
| `briefing` | `briefing_id` | `Briefing` fields; `bullets_json`, `unmatched_tokens_json` | |
| `person` | `person_id` | `Person` fields | Assigned demo location only; no PII |
| `spawn_point` | `spawn_id` | `scenario_id`, `join_code`, `lon`, `lat`, `zone_id`, `street`, `taken_by` (option) | Seeded by `load_scenario` |
| `report` | `report_id` | `Report` fields (public: `summary`, no transcript) | |
| `report_text` **(private)** | `report_id` | `transcript` | Deleted at reset |
| `activity_event` | `event_id` | `ActivityEvent` fields; `seq: u32` assigned by the hub | Pruned to the newest 300 per scenario |
| `metric` | `id` = `{branch}:{key}` | `Metric` fields; `detail_json` | |
| `command` | `command_id` | `scenario_id`, `kind`, `args_json`, `status` (`pending`/`done`/`failed`), `created_wall_ms`, `actor`, `result_json` (option) | Engine queue; no PII. ID = `cmd_{ctx timestamp µs}_{counter}` |

### 2.2 Reducers (canonical signatures)

**Authorization**
- Engine reducers require `ctx.sender == config.engine_identity`.
- Service reducers require `ctx.sender ∈ {service_identity, engine_identity}`.
- Operator reducers require an `operator_pin` argument equal to `config.operator_pin`.
- Use the reducer context timestamp for every wall time; never trust a client-supplied time.

**Bootstrap**
- `set_identities(engine_identity: Identity, service_identity: Identity, operator_pin: string)`: succeeds only while `config` is empty. Call it once from the CLI right after the first publish.
  - The engine and Vercel each create their identity with `POST /v1/identity` (§3) and print it. Paste both into the CLI call.
  - **If Identity comparison is awkward in the TS module, fall back to string keys passed as the first argument.** It's the same logic.

**Engine reducers**

| Reducer | Behaviour |
|---|---|
| `load_scenario(scenario_json)` | Upsert `scenario`, `zone`, `sensor`, `spawn_point`, `branch(main)`. Create `clock` **only if absent**: paused at `replay_start`, speed 240, `pause_on_proposal = true`. An engine restart doesn't reset the demo. Payload: `{scenario_id, title, tz, config_hash, manifest, replay_start_sim_ms, replay_end_sim_ms, zones: [ZoneFeatureProps + evac_min], sensors: [SensorInfo], spawn_points: [{spawn_id, join_code, lon, lat, zone_id, street}]}` |
| `publish_bundle(bundle_json)` | One transaction. Parse the `CycleBundle` (`00` §3.1), then: (1) if `cycle` is not null, upsert `cycle` + advance `cycle_head`; (2) upsert all `zone_risk`, `observation` (insert-or-ignore by `obs_id`), `scan_event`, `sensor` deltas and `metric`; (3) upsert `alert`s: insert new ones; for existing rows **only** apply `proposed → superseded`; never touch any other state; (4) insert `briefings` (ignore if they exist); (5) update `person` risk fields (`p_60`, `eta_q50_sim_ms`, `act_by_sim_ms`); skip unknown people; (6) apply `reports` statuses (`pending → accepted/rejected`, set `observation_id`); (7) insert `events` **skipping existing `event_id`s**, assign `seq = max+1` per scenario, then prune to 300; (8) **if any alert was newly inserted as `proposed` and `clock.pause_on_proposal`:** re-anchor, `playing = false`, emit event `Clock paused for operator review`. |
| `publish_bundles(bundles_json)` | Array version for catch-up (≤ 10 per call). The pause rule (8) applies only to the last bundle. |
| `rewind(scenario_id, branch_id, sim_ms)` | Delete that branch's rows with time > `sim_ms`: `cycle`/`zone_risk` by `knowledge_sim_ms`; `observation` by `available_sim_ms`; `scan_event` by `t_start_sim_ms`; `alert` by `triggered_sim_ms` **(any state)** and their `briefing`s; `metric` by `computed_sim_ms` (non-null only). Recompute `cycle_head`; `epoch++`. |
| `set_clock_status(scenario_id, status)` | Always re-anchor first. `computing`: `resume_playing = playing; playing = false`. `ok` from `computing`: `playing = resume_playing`. `catching_up`: no play change. |
| `create_branch(branch_json)` | Insert `branch`; set `clock.active_branch_id`; `epoch++` |
| `upsert_alert_draft(alert_id, text, voice_script, draft_source, briefing_json)` | Always insert the briefing. Replace the alert's `text`/`voice_script`/`draft_source`/`briefing_id` **only if** it is still `proposed`. |
| `update_alert_call(alert_id, state, call_id, call_status)` | Allowed: `approved → calling`, `calling → called`, `approved\|calling → failed`. If the alert is already `acknowledged`, update only `call_id`/`call_status`, never `state`, and never error. |
| `append_events(events_json)` | Same insertion rule as `publish_bundle` (7) |
| `mark_command(command_id, status, result_json)` | |
| `submit_report_engine(report_json, transcript)` | Same as `submit_report`, for the engine's offline `/dev/report` path |

**Service reducers** (Vercel functions):

| Reducer | Behaviour |
|---|---|
| `submit_report(report_json, transcript)` | Insert `report` (`pending`) + `report_text` + `command {kind: "report", args: {report_id}}` |
| `ack_alert(alert_id, via)` | From `approved\|calling\|called` → `acknowledged`. Set `acked_via`, `acked_wall_ms` = now, and `acked_sim_ms` = the clock's `sim_now`. Emit event `Acknowledged by phone`. |

**Operator reducers** (dashboard and `/control`; `operator_pin` as the **last** argument). Each makes its domain change and inserts a `command` row:

| Reducer | Domain change | command.kind | args_json |
|---|---|---|---|
| `clock_play(scenario_id, pin)` | Re-anchor; `playing = true`. **Rejected while `status == computing`.** | `clock_play` | `{}` |
| `clock_pause(scenario_id, pin)` | Re-anchor; `playing = false` | `clock_pause` | `{}` |
| `clock_seek(scenario_id, sim_ms, pin)` | Re-anchor at `sim_ms` (clamped); `epoch++` | `clock_seek` | `{sim_ms, from_sim_ms}` |
| `clock_set_speed(scenario_id, speed, pin)` | Re-anchor; set `speed` (30–900) | `clock_speed` | `{speed}` |
| `set_pause_on_proposal(scenario_id, on, pin)` | | — | — |
| `reset_scenario(scenario_id, pin)` | **Hard reset.** Delete all `alert`, `briefing`, `report`, `report_text`, `person`, `command` (except this one), non-main `branch`, and every row with `branch_id != "main"`. Delete main rows with time > `replay_start` (same columns as `rewind`). Clear `spawn_point.taken_by`. Clock: paused at `replay_start`, `active_branch_id = main`, `status = ok`, `epoch++`. | `reset` | `{}` |
| `approve_alert(alert_id, operator, edited_text, pin)` | Only if `proposed` → `approved`. `decided_by = operator` (the name typed in the dashboard, default "Operator"), `decided_wall_ms`. A non-empty `edited_text` replaces `text`. | `alert_approved` | `{alert_id, operator}` |
| `decline_alert(alert_id, operator, reason, pin)` | Only if `proposed` → `declined` | `alert_declined` | `{alert_id, operator, reason}` |
| `set_evac_min(zone_id, minutes, pin)` | Clamp to 10–240 | `evac_min` | `{zone_id, minutes}` |
| `drop_sensor(sensor_id, enabled, pin)` | P2 | `drop_sensor` | `{sensor_id, enabled}` |

**Public reducers** (phones):

| Reducer | Behaviour |
|---|---|
| `join_as_person(scenario_id, person_id, join_code, label)` | Idempotent per `person_id`. Assign the first `spawn_point` with that `join_code` and `taken_by` null; set `taken_by`. If none is free, reuse the least recently taken. Create the person: `kind = "judge"`, `location_source = "assigned"`, `provenance = "simulated"`, `zone_id` from the spawn point. Insert command `person_joined {person_id}`. |
| `set_person_status(person_id, status)` | |

## 3. Engine HTTP client (`engine/emberline/sync/stdb_http.py`)

These endpoints were checked against https://spacetimedb.com/docs/http/database by review A. Re-check them in the spike.

| Operation | Request |
|---|---|
| Identity | `POST {STDB_URI}/v1/identity` returns `{identity, token}`. Run once and store `STDB_TOKEN`. Vercel gets its own identity (`STDB_SERVICE_TOKEN`). |
| Reducer call | `POST {STDB_URI}/v1/database/{STDB_DB}/call/{reducer}` with header `Authorization: Bearer {STDB_TOKEN}`. The body is a **JSON array of positional args**, e.g. `["<bundle json string>"]`. |
| SQL | `POST {STDB_URI}/v1/database/{STDB_DB}/sql`. The body is SQL text; statements are separated by `;`. The response is a list of result sets (schema + rows); map column names to rows. **No `ORDER BY` in the grammar: sort client-side.** |

**Client rules**
- `httpx.AsyncClient`, 10 s timeout.
- **Retry** network errors and 5xx up to 3 times with backoff. **Don't retry** a reducer rejection; log it as an `error` event.
- Keep bundles ≤ 200 KB (typically ~50 KB). If large string arguments are rejected, split `observations` into a separate `publish_observations` call.

**Poll loop**
- Cadence: every **500 ms while playing**, every **1 s while paused**.
- One SQL request:
  ```sql
  SELECT * FROM command WHERE status = 'pending'; SELECT * FROM clock WHERE scenario_id = '<SCENARIO_ID>'; SELECT * FROM zone; SELECT * FROM person
  ```
  If multi-statement fails in the spike, send 4 requests.
- Sort commands by `created_wall_ms` and dispatch in order. Call `mark_command` after handling. On engine restart, handle only `pending`.

## 4. Web client (`web/src/data/SpacetimeSource.ts`)

**Bindings and connection**
- Generate bindings: `spacetime generate --lang typescript --out-dir web/src/module_bindings --project-path stdb`. Re-run after every module change and commit the result.
- Create the connection once at module scope and wrap the app in the provider (form verified against the package types in research 05):
  ```
  DbConnection.builder().withUri(VITE_STDB_URI).withDatabaseName('emberline').withToken(saved).onConnect(save)
  ```

**Row adapter (required).** `adapt<T>(row)` does four things:
1. Converts generated camelCase accessors to the snake_case contract types.
2. Maps `undefined` options to `null`.
3. `JSON.parse`s every `*_json` field into its contract field, e.g. `p_by_lead_json → p_by_lead`.
4. Converts `Identity` and timestamp types to strings and numbers.

Components only ever see `00` §3.3 types.

**Subscriptions**

| Surface | Subscribe to |
|---|---|
| Dashboard | `scenario`, `clock`, `branch`, `sensor`, `cycle`, `cycle_head`, `zone`, `zone_risk`, `alert`, `briefing`, `metric`, `person`, `report`, `activity_event`, `scan_event`, `observation` (all `WHERE scenario_id = ...`) |
| `/control` | `clock`, `branch`, `alert`, `activity_event`, `spawn_point` |
| Phone `/p` | `clock`, `cycle_head`, its own `person`, `alert` (scenario; filtered client-side to its zone), its own `report` |

Apply lineage with `inLineage(row, branches, clock)` (`00` §3.3). Expose everything to components through the hooks in `06` §2.

## 5. Offline

1. `spacetime start` (or Docker).
2. `spacetime publish emberline --server local`.
3. Call `set_identities`.
4. `python -m emberline.run --profile offline`, which calls `load_scenario`.

The web app uses `VITE_STDB_URI=http://localhost:3000`. Rehearse at M5 with Wi-Fi off.

## 6. Acceptance tests (local instance; Python integration test `engine/tests/test_hub.py`)

1. Publishing the same fixture bundle twice is idempotent: same row counts, no error.
2. `approve_alert` on a `superseded` alert is rejected. `publish_bundle` never downgrades `approved` → `proposed`.
3. **Approve → ack → `reset_scenario` → republish the cycle-20 bundle**, and the same alert appears as `proposed`. Persons, reports and branches are gone.
4. `rewind` leaves exactly the rows ≤ `sim_ms` and bumps `epoch`.
5. `set_clock_status(computing)` then `(ok)`: `sim_now` is continuous (no jump back), and `playing` is restored.
6. A bundle with a new proposal and `pause_on_proposal` pauses the clock.
7. `activity_event` stays ≤ 300 after 1,000 inserts, and `seq` is strictly increasing.
8. Engine, service and operator reducers reject wrong identities or PINs.
9. The phone subscription payload stays < 100 KB after a full replay.

## 7. Convex fallback (design note only; build only if G1-b fails)

The contracts and the seams (`StateSink`, `DataSource` hooks) are unchanged.
- **`convex/schema.ts`:** the same tables (nested objects are allowed). Mutations take the same names in camelCase.
- **Engine and service auth:** `internalMutation`s behind a Convex HTTP action that checks a shared-secret header.
- **Engine:** `sync/convex_sink.py` uses the official `convex` Python client 0.8.1 (`mutation`, `query`, `subscribe`).
- **Web:** `ConvexSource.ts` uses `useQuery`/`useMutation` behind the same hooks.
- **Free-plan caps:** 1 GB/month DB I/O and 1 GB file egress. Use indexed queries; never serve rasters from Convex storage.
- **Prize:** drop the SpacetimeDB entry and enter Convex instead. Never run both.
