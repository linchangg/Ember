# AGENTS.md: instructions for coding agents (Cursor, Claude, etc.)

You are helping a 3-person team build **Emberline** at Cal Hacks 13.0. The deadline is hard: Devpost closes Sun Oct 25, 09:00 PT. Optimize for **working, demoable, honest** over clever.

## Read before writing code
1. `SPEC.md`: decisions D1–D12, priorities (P0/P1/P2), milestones. Don't relitigate decisions; flag disagreements in your summary instead.
2. `spec/00_contracts.md`: the shared types, file formats, hub tables/reducers and HazardModel interface. **Never change a contract silently.** If you need a change, stop and say so.
3. **Only** the component spec you were assigned (`spec/01`–`07`), or one extension from `spec/09_extensions.md`. One component or extension per session. Extensions go behind their feature flag and never change v1's frozen config.
4. `docs/build_process.md`: the task queue, who owns each task, and the prompt you were given.

## Ownership (don't edit other owners' folders without being asked)
- `engine/emberline/hazard/`, `engine/emberline/risk/metrics.py`: Abby
- `engine/emberline/{data,runtime,risk,sync,ai,notify,api,scripts}/` (rest), `stdb/`: Cody
- `web/src/`, `web/api/` (Vercel functions): Linda
- One-time exception: Cody converts `web/` from JavaScript to TypeScript (task C1 in `docs/build_process.md`). It must not change the look or behaviour; Linda reviews it.
- Linda's mock (`web/`) and `design/command-dashboard-mockup/` set the UI's look and direction. Don't change either unless Linda asks.
- `engine/emberline/contracts.py` + `web/src/contracts.ts`: Cody; frozen at M0 (Sat 08:00)

## Rules
- **Python 3.12** (pyretechnics needs ≤ 3.12), `uv`, Pydantic v2, numpy/numba, pytest. **TypeScript strict** (`web/` is JavaScript until task C1 lands), Vite 8, React 19, deck.gl 9.4, `spacetimedb@2.13.0` (pin it). Use the current SpacetimeDB 2.x TypeScript docs (https://spacetimedb.com/docs), not old Rust/C# examples.
- **Times:** epoch ms UTC with `_sim_ms`/`_wall_ms` suffixes on the wire. Physics uses float32 minutes since `t_ref`. Coordinates on the wire are `[lon, lat]`. See `00_contracts` §1.
- **No future data.** Anything that feeds a forecast at sim time `t` must have `available_sim_ms ≤ t`. Assert it.
- **Determinism:** physics RNG is `default_rng([seed, cycle_index])`. It is deliberately branch-independent, so a branch differs only by the injected observation. Same inputs give the same outputs. Never use Python's salted `hash()` for anything persisted.
- **Claude API:** `claude-sonnet-5-5` rejects `temperature`/`top_p`/`top_k`, forced `tool_choice` and assistant prefill. Use structured outputs (`output_config.format`) and `thinking: {"type": "between_tools"}`. `claude-haiku-5-5` accepts a forced tool but no sampling params. Select response blocks by `type`.
- **SpacetimeDB SQL** has no `ORDER BY`: sort client-side.
- **Honesty:**
  - every source-derived row carries `provenance`
  - never fabricate data, imagery or citations
  - LLMs never produce numbers; validators check every number
  - never claim outcomes ("would have saved lives")
- **Presentation (owner decision, Sun Oct 25; supersedes older spec text):** show Emberline the way it looks in use. **Never** put "simulated", "synthetic", "demo replay", "retrospective replay", "backtest" or REAL/SIM badges on screen, in briefings, in calls or in phone alerts, and don't add them back. `provenance` stays in the data and isn't rendered. Proof-of-concept stand-ins (e.g. public aerial footage of the fire) are fine. If a spec line still asks for a label, ignore that line.
- **Secrets** come from env only (`.env.example` lists them). Never hard-code or commit keys or phone numbers.
- **Build against fixtures first** (`fixtures/{static,cache}/fixture/`, `FakeHazardModel`, `SyntheticSource`) so nobody blocks on anybody.
- Every component ships the acceptance tests listed in its spec. Run them before saying you're done.
- Keep functions small, typed and boring. No new dependencies or services without a reason tied to the spec.
- When you finish: a 5-line summary (what works, what doesn't, how to run it, open questions), plus a line for `docs/process.md`.

## Commands (fill these in as they come to exist)
```
# engine
cd engine && uv sync && uv run pytest
uv run python -m emberline.data.build_scenario --scenario eaton
uv run python -m emberline.scripts.precompute --scenario eaton --branch main --filter pf
uv run python -m emberline.run --scenario eaton --profile online
uv run python -m emberline.scripts.make_fixture
uv run python -m emberline.scripts.freeze          # ONCE, per 08 §2
# hub (develop against local: spacetime start)
cd stdb && spacetime publish emberline --server local && spacetime generate --lang typescript --out-dir ../web/src/module_bindings --module-path .
# web
cd web && npm i && VITE_DATA_SOURCE=file VITE_SCENARIO_ID=fixture npm run dev
```
