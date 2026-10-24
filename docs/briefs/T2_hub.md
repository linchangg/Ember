# T2: SpacetimeDB hub: G1-b spike, then the full module + engine HTTP client

**Goal.** The shared-state hub. It has two phases with a **hard stop between them**:
- **Phase 1** proves SpacetimeDB works end to end (the G1-b gate).
- **Phase 2** builds every table and reducer in `spec/04` §2, the Python HTTP client, the integration tests, and the generated web bindings.

**Follow `docs/briefs/README.md` §2 for all general rules.** Branch: `c-t2-hub`.

**Prerequisites (Cody did these by hand):**
- the `spacetime` CLI is installed (2.13.x);
- `spacetime start` is running locally (`http://localhost:3000`);
- PR #4 (contracts + fixture) is on `main`.

Check them with `spacetime --version` and `curl -s http://localhost:3000/v1/ping` (or whatever health endpoint the docs give). If they fail, stop and tell Cody.

## Read
- `spec/04_hub.md`, **all of it**. It is your spec.
- `spec/00_contracts.md`: §1 (hub storage conventions), §3.1–3.3 (types; `CycleBundle` is what `publish_bundle` parses), §5
- **SpacetimeDB 2.x TypeScript docs: https://spacetimedb.com/docs.** Read the TypeScript **module** quickstart/reference (tables, reducers, `ctx.sender`, `ctx.timestamp`, indexes, `t.option`) and the TypeScript **client** reference (`DbConnection.builder()`, subscriptions, calling reducers), plus the HTTP API page (`/v1/identity`, `/v1/database/{db}/call/{reducer}`, `/v1/database/{db}/sql`).
  - **Don't use Rust or C# examples, and don't use pre-2.0 TypeScript syntax.**
  - When the docs and your memory disagree, the docs win. When the docs are unclear, read the type definitions in `stdb/node_modules/spacetimedb/` after installing.
- `engine/emberline/contracts.py`, for field names.

## You own
- `stdb/**`: the TypeScript module (`package.json`, `tsconfig.json`, `src/index.ts`, plus `spike/` scripts)
- `engine/emberline/sync/__init__.py` (empty), `engine/emberline/sync/stdb_http.py`
- `engine/emberline/scripts/hub_spike.py`
- `engine/tests/test_hub.py`
- `web/src/module_bindings/**`: generated code only, in **phase 2 only**. Never hand-edit it.

## Phase 1: spike (time box 90 min; G1-b)

1. **Module scaffold.** `cd stdb && npm init -y && npm i spacetimedb@2.13.0 --save-exact`, plus `typescript` as a dev dependency. Use the project layout the SpacetimeDB TS module docs prescribe; if `spacetime init --lang typescript` exists in this CLI, prefer it, then pin `spacetimedb@2.13.0` exactly.
2. **Spike tables and reducers in `stdb/src/index.ts`** (only these four tables):
   - **`config`** (private): PK `id` = `"singleton"`; `engine_identity`, `service_identity`, `operator_pin`.
   - **`clock`:** columns from `04` §2.1. All times are `f64`; `epoch` is `u32`.
   - **`cycle`:** PK `cycle_id`; for the spike, just `scenario_id`, `branch_id`, `knowledge_sim_ms: f64`, `burned_ha: f64`, and an index on `scenario_id`.
   - **`command`:** columns from `04` §2.1, with an index on `status`.
   - **`set_identities(engine_identity, service_identity, operator_pin)`:** succeeds only while `config` is empty. If comparing `Identity` values is awkward, store them as hex strings and compare `ctx.sender.toHexString()` (or the docs' equivalent). Spec `04` §2.2 explicitly allows the string fallback.
   - **`clock_play(scenario_id, pin)`:**
     - checks the PIN;
     - creates the clock row if missing (paused at 0, speed 240);
     - sets `playing = true`;
     - inserts a `command` with `kind: "clock_play"`, `args_json: "{}"`, `status: "pending"`, `created_wall_ms` = the context timestamp in ms, and `command_id = cmd_{µs}_{counter}`.
   - **`publish_bundle(bundle_json)`:** checks `ctx.sender == engine identity`, `JSON.parse`s the string, and upserts the `cycle` row when `bundle.cycle` is not null.
3. **Publish** with `spacetime publish emberline --server local` (run from `stdb/`, or pass `--project-path stdb`).
4. **Python client `engine/emberline/sync/stdb_http.py`** (`uv add httpx`):
   ```python
   class StdbHttp:
       def __init__(self, uri: str, db: str, token: str | None): ...   # httpx.AsyncClient(timeout=10)
       async def new_identity(self) -> tuple[str, str]                  # POST {uri}/v1/identity -> (identity, token)
       async def call(self, reducer: str, *args) -> None                 # POST {uri}/v1/database/{db}/call/{reducer}, body = json.dumps(list(args)), Bearer token
       async def sql(self, query: str) -> list[list[dict]]               # POST .../sql, body = query text; map schema column names onto rows -> list of result sets of row dicts
   ```
   - **Retry:** network errors and HTTP 5xx, up to 3 times with backoff (0.5, 1, 2 s). **Don't** retry 4xx (a reducer rejection); raise `ReducerRejected(reducer, status, body)`.
   - **Read the SQL response shape** from the docs or a real response, and write the column-name mapping against what you actually see. Print one raw response while developing.
5. **`engine/emberline/scripts/hub_spike.py`** (`uv run python -m emberline.scripts.hub_spike`):
   1. If `STDB_TOKEN` isn't already set in `engine/.env`:
      - create an engine identity and a service identity;
      - append `STDB_TOKEN=...` and `STDB_SERVICE_TOKEN=...` to `engine/.env` (git-ignored);
      - print both identities, but print **only the last 4 characters of each token**.

      Otherwise reuse the existing token.
   2. Print the exact `spacetime call emberline set_identities ...` CLI line for Cody to run, **or** call it over HTTP if the docs allow an anonymous caller to run that reducer.
   3. Call `publish_bundle` with the fixture's cycle-20 bundle (`fixtures/cache/fixture/main/<20th cycle k>/bundle.json`; find it through `index.json`).
   4. Run `SELECT * FROM cycle` and print the row.
   5. Run `SELECT * FROM command WHERE status = 'pending'` and print what it parses to.
6. **React-side check.** Write `stdb/spike/subscribe.ts`, run with `npx tsx`, using the generated **TypeScript client** bindings (`spacetime generate --lang typescript --out-dir stdb/spike/bindings --project-path stdb`). It connects, subscribes to `SELECT * FROM cycle`, prints rows as they arrive, then calls `clock_play` with the PIN.
   - While it runs, re-run `hub_spike.py` and check that (a) the new cycle row prints live, and (b) the `command` row from `clock_play` shows up in the Python SQL poll within 1 s.
   - This uses the same client SDK a React page would; say in the summary that the check used a Node script, not a React page.

### STOP: report the G1-b result
Push the branch and open a **draft** PR titled `T2 (spike): SpacetimeDB G1-b`. In it and in your reply, report each of the four pass criteria in `04` §1 as PASS/FAIL with evidence (pasted output). Note anything that differs from `04` §3, such as the SQL response shape, or whether multi-statement SQL works. **Then stop and wait for Cody.**
- If any criterion fails, Cody decides whether to switch to Convex (`04` §7); don't build Convex yourself.
- Continue to phase 2 only when Cody says "go".

## Phase 2: the full module (after Cody says go)

Work in this order. Commit after each numbered step, and run the relevant tests from step 8 as you go.

1. **Every table in `04` §2.1**, following the conventions in `04` §2:
   - `f64` times;
   - `t.option` for nullables;
   - `u32`/`i32` counts;
   - nested data as `*_json: string`;
   - an index on `scenario_id` wherever the table has it;
   - `config` and `report_text` **private**.

   Replace the spike's slim `cycle` with all `Cycle` fields (`n_obs_by_kind_json`, `assets_json`, `danger_json`, `params_summary_json`).
2. **Helpers (inside `index.ts`):**
   - `nowMs(ctx)`;
   - `simNow(clock, nowMs)` = `clamp(anchor_sim + (playing ? (now − anchor_wall) × speed : 0), min, max)`;
   - `reanchor(clock, nowMs)`, which sets `anchor_sim = simNow`, `anchor_wall = now`;
   - `requireEngine(ctx)`, `requireService(ctx)` (service **or** engine), `requirePin(pin)`;
   - `insertCommand(ctx, scenario_id, kind, args)`;
   - `insertEvents(ctx, events)`: skips existing `event_id`s, sets `seq = max(seq for that scenario) + 1` per insert, then deletes the lowest-`seq` rows until ≤ 300 per scenario.

   Every wall time comes from the context timestamp; never from an argument.
3. **Engine reducers** (`04` §2.2 table, behaviour exactly as written):
   - `load_scenario`: creates `clock` **only if absent**; `branch main`.
   - `publish_bundle`: steps (1)–(8). (3) alerts: insert new; on existing rows **only** `proposed → superseded`. (8) auto-pause only when an alert was **newly inserted** as `proposed`.
   - `publish_bundles` (pause rule only on the last bundle)
   - `rewind`: deletes by the columns listed; recomputes `cycle_head`; `epoch++`.
   - `set_clock_status`: always re-anchors first; `computing` saves `resume_playing`; `ok` from `computing` restores it.
   - `create_branch`
   - `upsert_alert_draft`: replaces text only while `proposed`.
   - `update_alert_call`: only the allowed transitions; on `acknowledged`, updates only `call_id`/`call_status`, **never errors**.
   - `append_events`, `mark_command`, `submit_report_engine`
4. **Service reducers:**
   - `submit_report`: inserts `report` + `report_text` + `command {kind: "report"}`.
   - `ack_alert`: `approved|calling|called → acknowledged`; sets `acked_sim_ms = simNow(clock)`; emits event `Acknowledged by phone`.
5. **Operator reducers.** The PIN is the **last** argument. Each makes its domain change **and** inserts its `command` row:
   - `clock_play` (rejected while `status == computing`), `clock_pause`
   - `clock_seek` (clamps; `epoch++`; args `{sim_ms, from_sim_ms}`)
   - `clock_set_speed` (30–900), `set_pause_on_proposal`
   - `reset_scenario`: the hard reset. Follow the delete list exactly, and keep this reset's own `command` row.
   - `approve_alert` (only from `proposed`), `decline_alert`
   - `set_evac_min` (clamped 10–240)
   - `drop_sensor`
6. **Public reducers:**
   - `join_as_person`: idempotent; takes the first free spawn with that `join_code`, else reuses the least recently taken one; the person is `kind judge`, `assigned`, `simulated`; inserts command `person_joined`.
   - `set_person_status`
7. **Python client additions:**
   - `poll(scenario_id) -> dict`: one multi-statement SQL (`04` §3 Poll loop) returning `{"commands": [...sorted by created_wall_ms], "clock": {...} | None, "zones": [...], "people": [...]}`. Fall back to 4 requests if multi-statement fails.
   - Thin typed wrappers for each engine and service reducer: `publish_bundle(b: CycleBundle)` sends `b.model_dump_json()`, and so on.
   - Warn when a bundle is over 200 KB.

   The `StateSink` class itself is wave-2 T10. Don't write it.
8. **`engine/tests/test_hub.py`:** the 9 tests in `04` §6.
   - Skip the module unless `EMBERLINE_HUB_TESTS=1`.
   - Use local `STDB_URI`, `STDB_TOKEN` and `OPERATOR_PIN` from env.
   - Each test starts with `reset_scenario` + `load_scenario` (the fixture scenario: build the `load_scenario` payload from `fixtures/static/fixture/*.json`/`.geojson`).
   - For identity rejection (test 8), call with a fresh identity from `new_identity()`.
   - Test 9: publish all fixture bundles, then measure `len(json)` of the phone subscription tables (`clock`, `cycle_head`, one `person`, `alert`) via SQL as a proxy, and assert < 100 KB.
9. **Generate the web bindings:** `spacetime generate --lang typescript --out-dir web/src/module_bindings --project-path stdb`. Commit them. Don't write `SpacetimeSource.ts`; that's Linda's data layer.
10. Republish locally, run `EMBERLINE_HUB_TESTS=1 uv run pytest -q tests/test_hub.py`, then the full `uv run pytest -q`. Mark the PR ready for review.

## Done when
- The phase-1 report is posted and Cody said go.
- All 9 hub tests pass against local `spacetime start` (paste the output).
- The bindings are generated.
- The PR description lists every reducer with a one-line note on how it was verified.

## Don't
- Put science data (rasters, pixel tables) in the hub.
- Use 64-bit integer columns. Times are `f64`; counts are `u32`/`i32`.
- Use `ORDER BY` in SQL (not in the grammar); sort client-side.
- Run against Maincloud. It's local only; Cody publishes to Maincloud later.
- Commit any token, or the PIN.
