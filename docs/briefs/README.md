# Cody's next wave: agent briefs

Written Sat Oct 24 2026, afternoon. Status at this point:

- C0 (contracts + fixture) is in PR #4 and has been reviewed.
- Nothing else in Cody's queue has started.
- G1 gates (due 09:30) and M1 (due 13:00) are both overdue.
- Abby has not pushed `hazard/` yet.

Each `T*.md` file here is a self-contained brief for one coding agent: **one brief = one agent = one branch = one PR.** Paste the whole brief file into the agent's first message.

## 0. Before any agent starts (Cody, by hand, ~20 min)

1. **Land PR #4.** Its base branch is already merged, so rebase the PR onto `main`:
   ```bash
   git rebase --onto origin/main 8d45d46 c0/contracts-fixture
   git push --force-with-lease
   gh pr edit 4 --base main
   ```
   Get Linda or Abby to approve (GitHub won't let you approve your own PR), then merge. **Every brief assumes `contracts.py` and `fixtures/` are on `main`.**
2. **Install the SpacetimeDB CLI** for T2 (it isn't on this machine). Follow https://spacetimedb.com/docs, check that `spacetime --version` matches the `2.13.x` line, then run `spacetime start` in a terminal and leave it running.
3. **Do the G1-c Cartesia test call by hand** (`spec/05` §4 Setup steps 1–3). Write the go/no-go result in `docs/process.md`. No agent can do this one.
4. **Tell Abby** that T0 writes `engine/emberline/data/grid.py`, so she reuses it for A1 rather than writing her own. Ask for an ETA on A0 (`hazard/api.py` + `FakeHazardModel`): T7's `ForcingProvider` and the wave-2 engine loop need it.
5. Create `engine/.env` from `.env.example` and fill in `ANTHROPIC_API_KEY`. T4's live test needs it; the others don't.

## 1. Wave 1: what runs in parallel

```
T0 grid + scenario.yaml (≈20 min) ──► T1 GOES gate
                                  └─► T6 HRRR forcing
T2 hub (spike → STOP → full module)        ┐
T3 risk + alerts                           │ start immediately,
T4 Claude briefing + validator             │ all in parallel
T5 clock + cache + summarizer              │
T7 sources (ForcingProvider last, after A0)┘
C1 TypeScript conversion: gated (see C1 brief)
```

**Priority if you can't run them all at once.** These are on the M1 path: T3, T5, T2, T7. T1 is the overdue GOES gate. T4 and T6 come next. C1 runs whenever its gate opens.

| Brief | Branch | Owns these paths (nobody else may edit them) | Starts when |
|---|---|---|---|
| [T0](T0_grid.md) | `c-t0-grid` | `engine/emberline/data/{__init__,config,grid}.py`, `data/scenarios/eaton/{scenario.yaml,grid.json}`, `engine/tests/test_grid.py` | PR #4 merged |
| [T1](T1_goes_gate.md) | `c-t1-goes-gate` | `engine/emberline/data/{area_x,goes}.py`, `engine/emberline/scripts/check_goes.py`, `engine/tests/test_area_x.py`, `engine/tests/test_goes_geo.py` | T0 merged |
| [T2](T2_hub.md) | `c-t2-hub` | `stdb/**`, `engine/emberline/sync/{__init__,stdb_http}.py`, `engine/emberline/scripts/hub_spike.py`, `engine/tests/test_hub.py`, `web/src/module_bindings/**` | PR #4 merged + CLI running |
| [T3](T3_risk_alerts.md) | `c-t3-risk-alerts` | `engine/emberline/risk/{__init__,risk,alerts}.py`, `config/alert_rules.yaml`, `engine/tests/test_risk.py`, `engine/tests/test_alerts.py` | PR #4 merged |
| [T4](T4_briefing.md) | `c-t4-briefing` | `engine/emberline/ai/**`, `engine/tests/test_validate.py`, `engine/tests/test_briefing.py` | PR #4 merged |
| [T5](T5_runtime_core.md) | `c-t5-runtime-core` | `engine/emberline/runtime/{__init__,clock,cache,summarize}.py`, `engine/tests/test_{clock,cache,summarize}.py` | PR #4 merged |
| [T6](T6_hrrr.md) | `c-t6-hrrr` | `engine/emberline/data/hrrr.py`, `engine/tests/test_hrrr.py` | T0 merged |
| [T7](T7_sources.md) | `c-t7-sources` | `engine/emberline/runtime/sources.py`, `engine/tests/test_sources.py` | PR #4 merged |
| [C1](C1_typescript.md) | `c-c1-typescript` | everything in `web/` except `web/src/module_bindings/` and `web/src/contracts.ts` | PR #3 resolved + Linda agrees a window |

**Shared files.**
- **`engine/pyproject.toml` and `engine/uv.lock`.** Every Python agent adds dependencies with `uv add`, so these files will conflict. Resolve with the rule below; never hand-edit the lock file.
- **Empty package files.** `engine/emberline/data/__init__.py` (T0 creates it; T1 and T6 reuse it), `engine/emberline/runtime/__init__.py` (T5 and T7 may both create it) and `engine/emberline/risk/__init__.py` must be **empty (0 bytes)**. Git merges two identical new files cleanly.
- **`docs/process.md`.** Agents don't edit it. Each PR description ends with a `process.md line:` that Cody pastes in after merging. This keeps every PR from conflicting on the same file.

## 2. Rules for every agent (each brief repeats "follow README §2")

1. **Read first, in this order:**
   - `AGENTS.md`
   - `SPEC.md` §2 (decisions)
   - `spec/00_contracts.md` §1, then the sections your brief names
   - your brief's spec sections
   - the existing code your brief names

   Don't read other component specs.
2. **Start the branch:** `git fetch origin && git switch -c <branch> origin/main`.
3. **Edit only the paths your brief says you own,** plus `engine/pyproject.toml` / `engine/uv.lock` through `uv add`. If you think another file must change, **stop and say so** in your summary. This applies above all to `contracts.py`, `contracts.ts`, `make_fixture.py` and anything under `hazard/` or `web/src/` (other than what your brief allows).
4. **Python:**
   - Run commands from `engine/`.
   - Add dependencies with `uv add <pkg>` (no version pins unless the brief gives one).
   - Run tests with `uv run pytest -q`; the **whole** suite must pass, not just your tests.
   - Code style: Python 3.12, type hints, Pydantic v2 contract types from `emberline.contracts`, and small, boring functions. Match the style of `engine/emberline/scripts/make_fixture.py`.
5. **No network in default tests.** A test that needs the internet, a hub or an API key is skipped unless an env var is set:
   ```python
   needs_net = pytest.mark.skipif(not os.environ.get("EMBERLINE_NETWORK_TESTS"), reason="network")
   ```
   Hub tests use `EMBERLINE_HUB_TESTS`. Live Claude tests use `EMBERLINE_LIVE_TESTS`.
6. **Times:**
   - Integer epoch **ms UTC** everywhere outside physics.
   - Physics minutes are `(ms − t_ref_ms) / 60000`, with `t_ref` = `2025-01-08T00:00:00Z` for Eaton. The fixture uses ignition time as its `t_ref`; read it from the fixture rather than assuming.
   - Display is PST (UTC−8): `datetime.fromtimestamp(ms/1000, timezone(timedelta(hours=-8)))`.
7. **Coordinates:** `[lon, lat]` on the wire. EPSG:32611 metres inside the engine. Never send UTM to the web.
8. **No future data.** Anything used at sim time `k` must have `available_sim_ms <= k`; assert it in code with a clear message.
9. **Never use Python's `hash()`** for anything persisted. Use `hashlib.sha256` over `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.
10. **Secrets** come only from env (`os.environ`) and are never printed in full; mask all but the last 4 characters. Never commit `.env`.
11. **Rebasing when `main` moves:** `git fetch origin && git rebase origin/main`. If the only conflicts are in `engine/uv.lock` and/or `engine/pyproject.toml`, take main's copies and add your dependencies again:
    ```bash
    git checkout origin/main -- engine/uv.lock engine/pyproject.toml
    cd engine && uv add <your deps> && cd ..
    git add engine/uv.lock engine/pyproject.toml && git rebase --continue
    ```
12. **When done:**
    - Push and open a PR into `main` with `gh pr create`.
    - Title: `T<n>: <what it does>`.
    - Body:
      - what changed
      - the exact test command and its pasted output
      - anything skipped or deferred
      - open questions
      - a final line `process.md line: Sat HH:MM · Cody · T<n> <summary> (PR #<n>) · <agent name>`
    - Then reply with the 5-line summary from `AGENTS.md`.
13. **If you're stuck for more than 20 minutes on one thing, stop and report** what you tried. Don't invent data, endpoints, or SDK functions. When a library API is uncertain, read its installed source or docs (`uv run python -c "import x, inspect; print(inspect.signature(x.f))"`) before writing code.

## 3. Wave 2 (brief these once wave 1 lands)

| ID | Task | Needs |
|---|---|---|
| T8 | GOES-18 FDCC pixel table + `scan_events.parquet` + `replay_manifest.parquet` + `truth/latency.json` (`01` §3, §8). Reuses `data/goes.py` from T1. **Column names must match the contract in T7's `sources.py` docstring** | T1, T7 |
| T9 | Zones + exposure + assets + area X raster + spawn points + `lake_ave.geojson` + web manifest/sensors/satellites + terrain export (`01` §6 zones/exposure/story geometry, §7, §10) | T0, Abby's DEM |
| T10 | Engine M1: `model_host.py`, `loop.py`, `publisher.py`, `sync/sink.py` (`StdbSink`, `CacheSink`), `sync/poller.py`, `scripts/precompute.py`, `run.py`; `03` §14 tests 1–5 on the fixture with `FakeHazardModel` | A0, T2, T3, T5, T7 |
| T11 | `notify/cartesia.py` with `--dry-run` (`05` §4 "Placing the call") | G1-c passed |
| T12 | `api/local.py` FastAPI :8787 (`03` §11) | T5 |
| T13 | VIIRS detections (`01` §4); `obs/viirs.parquet` columns per T7's docstring | T0, T7, FIRMS key |

Merge order that keeps `main` runnable: T0 → (T3, T5, T7, T4 in any order) → T2 phase 2 → T1/T6 → wave 2.

## 4. Launching an agent in Cursor

Start one Cursor agent (a background agent, or a new Agent chat on a fresh checkout) **per brief**. Paste this prompt, replacing `<BRIEF>` with the brief's file name (for example `T3_risk_alerts.md`). Cursor loads `AGENTS.md` and `.cursor/rules/` by itself.

```
You are one of several coding agents working in parallel on the Emberline repo.
Your task brief is docs/briefs/<BRIEF>.

1. Read docs/briefs/README.md in full (§2 holds the rules every agent follows),
   then read docs/briefs/<BRIEF> in full, then everything in its "Read" section.
2. Check the prerequisites before writing any code: run `git fetch origin`, then the
   check for your brief from the table in docs/briefs/README.md §4. If a check fails,
   don't start; reply with which prerequisite is missing and stop.
3. Do the steps in the brief in order. Edit only the paths listed under "You own"
   (plus engine/pyproject.toml and engine/uv.lock through `uv add`). If you think
   anything else must change, stop and say so; don't edit it.
4. If the brief has a STOP point, stop there, report, and wait for my reply.
5. Don't invent APIs, endpoints, data or numbers. When a library API is uncertain,
   read its docs or installed source first. If you're stuck for 20 minutes on one
   thing, stop and report what you tried.
6. Finish exactly as README §2 rule 12 says: run the full test command, open the PR
   with the pasted output and the `process.md line:`, then give the 5-line summary.
```

**Prerequisite checks** (each prints a path or a version when the prerequisite is met):

| Brief | Check |
|---|---|
| T0, T3, T4, T5, T7 | `git ls-tree --name-only origin/main engine/emberline/contracts.py` |
| T1, T6 | `git ls-tree --name-only origin/main engine/emberline/data/grid.py` |
| T2 | the contracts check above, plus `spacetime --version` (2.13.x) and a local `spacetime start` answering on `http://localhost:3000` |
| T7 step 6 only | `git ls-tree --name-only origin/main engine/emberline/hazard/api.py` (without it, finish the rest and mark step 6 blocked) |
| C1 | `gh pr view 3 --json state -q .state` prints `MERGED` or `CLOSED`, **and** Cody confirms Linda's window in the prompt |

**Launch order.**
1. Once PR #4 is merged, start T0, T2, T3, T4, T5 and T7 together.
2. Merge T0 as soon as it's green, then start T1 and T6.
3. Answer T2's STOP report before it continues to phase 2.
