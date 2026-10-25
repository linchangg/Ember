# Build process

## Rust backend update (Oct 24)

The project owner approved Rust as the `HazardModel` backend. The public API and
schema remain frozen; the implementation decision, exact build commands, equations,
tests and current limitations are in [RUST_HAZARD_BACKEND.md](RUST_HAZARD_BACKEND.md).
The ordered follow-up work for Abby, Cody and Linda is in
[RUST_HAZARD_NEXT_TASKS.md](RUST_HAZARD_NEXT_TASKS.md). This update supersedes the
Python/MTT implementation choice for this backend; it does not claim that forecast
skill, latency, the full runtime or Linda's dashboard integration is complete.

How the three of us and our coding agents (Cursor) get from the spec to the demo. This repo now holds the canonical spec. The `Emberline` folder on Cody's laptop is an older copy; don't edit it.

## Where we are (Sat 12:40)

- **`web/`:** Linda's mock (React 19, Vite 8, JavaScript). It sets the UI's look and direction.
- **In the repo:** spec, contracts plan, agent rules (`SPEC.md`, `AGENTS.md`, `.cursor/rules/`, `spec/`, `research/`).
- **Not started:** contracts, fixture, engine, hub, physics.
- **M0 (planned Sat 08:00) hasn't landed.** Re-baseline the milestones (below) when C0 merges.

## Setup (once)

- [ ] Cody commits this setup from his machine on a branch and opens a PR; Linda merges.
- [ ] Add Cody and Abby as collaborators on `linchangg/Legendary-Fire-Project`.
- [ ] Everyone opens the repo in Cursor. Agents pick up `AGENTS.md` and `.cursor/rules/emberline.mdc`.
- [ ] Locally: copy `.env.example` to `engine/.env` (git-ignored). Vercel env vars per `spec/05` §7.
- [ ] Before the repo goes public for Devpost: decide whether `research/04_sponsors.md` (prize strategy) stays in.

## Ground rules

1. **One task = one agent run = one branch = one PR.** A task is a spec section, never a whole spec file.
2. **Contracts change only through Cody** (`contracts.py`, `contracts.ts`, hub tables and reducers).
3. **The agent runs the tests named in its task and pastes the output.** A human reads the diff and merges.
4. **After C0, `main` always runs the fixture demo.** Don't merge anything that breaks it.
5. **Linda's UI:** agents don't change its look or direction unless Linda asks.
6. **One line in `docs/process.md` per merged task** (Cursor evidence for SpaceXAI and MLH A-Team).
7. **C1 touches all of `web/src`.** Agree a 30-minute window with Linda so her next PR doesn't collide with it.

## Prompt template

```
Read AGENTS.md, SPEC.md and spec/00_contracts.md, then <spec file> §<n>.
Task <ID>: <one sentence>.
Constraints: <from the task>. Don't edit contracts unless the task is C0.
Done when: <tests>. Paste the test output and end with the 5-line summary from AGENTS.md.
```

## Queue

C0 unblocks everything that reads data. C1 is independent of C0 but should land before Linda's next `web/` PR.

### Cody

| ID | Task | Spec | Needs | Done when |
|---|---|---|---|---|
| **C0** | Contracts + analytic fixture: `engine/` skeleton (Python 3.12, uv), `contracts.py`, `web/src/contracts.ts`, `make_fixture.py` | `00` §1–4, §9–10 | — | `test_fixture.py` passes; fixture < 10 MB; Cody has read every line of both contract files |
| **C1** | Convert `web/` to TypeScript (strict). No change to look or behaviour | `06` §0 | A window agreed with Linda | `npm run build` and `npm run lint` pass; the dashboard looks and behaves the same; Linda approves |
| **C2** | G1 gates, if not already run: `grid.py` + `check_goes.py` (agent); SpacetimeDB spike (agent + Cody); Cartesia test call (Cody, by hand) | `01` §1–2, `04` §1, `05` §4 setup | — | Each go/no-go written in `docs/process.md` |
| C3 | HRRR forcing, GOES pixel table + manifest, zones + exposure + area X + spawn points, terrain export | `01` §3, §5, §6 (zones, exposure), §7–8, §10 | C0, C2 | `01` §12 tests |
| C4 | Engine on the fake model: clock, sources, physics loop, cache, summarizer, publisher + risk, alerts | `03` §2–8 | C0, A0 | `03` §14 tests on the fixture |
| C5 | Hub module + engine HTTP client + generated web bindings | `04` §2–3, §6 | C2 spike passed | `04` §6 tests |
| C6 | Claude briefing + validator; Cartesia call placement | `05` §1, §4 | C4 | `05` §8 tests |
| C7 | Commands, branching, reset; local API | `03` §10–11 | C4, C5 | `03` §14 tests |

### Abby

Order and target times follow `02` §12. Cursor scaffolds and writes tests; Abby drives the numerics.

**`grid.py` (`01` §1)** is needed by both C2 and A1. Whichever starts first writes it and tells the other; the other reuses it.

| ID | Task | Spec | Needs | Done when |
|---|---|---|---|---|
| **A0** | `FakeHazardModel` (16 members) behind the HazardModel protocol | `00` §6, `02` §1 | C0 | The engine can step it on the fixture grid; save/load round-trips |
| A1 | DEM, slope, aspect, LANDFIRE fuels on the grid | `01` §1, §6 (DEM, fuels) | `grid.py` (see note) | `01` §12 tests 2–3 |
| A2 | Fuel table + single-member MTT solver | `02` §2–3 | A1 | `02` §11 tests 1–2; one solve timed on 340×340 |
| A3 | Ensemble + CDF + `reinit`, then urban spread (P0) | `02` §5–6, §9 | A2 | One sane forecast from ignition on real Eaton arrays |
| A4 | Likelihoods + particle filter; state and determinism | `02` §7–8, §10 | A3 | `02` §11 tests 3–4 (twin experiment) |
| A5 | 18:00-style gate (`pf` vs `reinit`), then `metrics.py` | `02` §8, `03` §9 | A4 | Gate result in `docs/process.md` |

### Linda

Linda sequences her own work from `spec/06`, `spec/07` and `spec/05` §2–4 (`web/api/*`). What she needs from the others:

- **C0:** `web/src/contracts.ts` and `fixtures/` so the dashboard can read fixture data.
- **C1:** her current code converted to TypeScript (she reviews it).
- **C5:** generated SpacetimeDB bindings in `web/src/module_bindings`.
- API keys for the Vercel functions (`spec/05` §7).

## First prompts (copy into Cursor)

**C0: contracts + fixture**

```
Read AGENTS.md, SPEC.md and all of spec/00_contracts.md.
Task C0: contracts and the analytic fixture.
- Create engine/ per 00 §9: Python 3.12, uv, package "emberline". Implement every type in 00 §3 as
  Pydantic v2 models in engine/emberline/contracts.py, with SCHEMA_VERSION = 2.
- Create web/src/contracts.ts with the TypeScript types from 00 §3, same field names. web/ is still
  JavaScript; don't convert or touch any other web/ file.
- Implement engine/emberline/scripts/make_fixture.py exactly per 00 §10 (numpy, shapely, Pillow only)
  and write fixtures/{static,cache}/fixture/.
- Add engine/tests/test_fixture.py per 00 §10.
Done when: `uv run pytest` passes, the fixture is under 10 MB, and the proposed alerts appear at
cycles 20 and 30. Paste the test output and end with the 5-line summary from AGENTS.md.
```

**C1: TypeScript conversion**

```
Read AGENTS.md and .cursor/rules/emberline.mdc.
Task C1: convert web/ from JavaScript to TypeScript (strict). This is a mechanical port.
- Rename web/src *.jsx → *.tsx and *.js → *.ts; vite.config.js → vite.config.ts; update the script src in
  index.html. Add typescript and a strict tsconfig (Vite react-ts defaults with "strict": true).
- Keep React 19, Vite 8, oxlint, every dependency version, and the Vite server settings
  (host, port, strictPort, allowedHosts).
- Add types for component props and for the functions in lib/. No `any` unless unavoidable; no @ts-ignore.
- Don't change markup, CSS, class names, copy, colours, numbers, the terrain renderer or the mock data.
  No new features and no refactors beyond what typing needs. Leave design/ untouched.
Done when: `npm run build` and `npm run lint` pass, and `npm run dev` renders the same dashboard
(header, draggable terrain, slider, sidebar values at the default time). Paste the build and lint
output and end with the 5-line summary from AGENTS.md.
```

**C2: GOES go/no-go**

```
Read AGENTS.md, SPEC.md, spec/00_contracts.md §1–2 and spec/01_data_pipeline.md §1–2.
Task C2: implement the shared area_x_satellite_hit() in engine/emberline/data/area_x.py and
engine/emberline/scripts/check_goes.py exactly as 01 §2 describes, including parallax correction and
the decision rule. Use engine/emberline/data/grid.py if it exists; otherwise write it per 01 §1 first.
For the parallax elevation, use the DEM from task A1 if it exists; otherwise fetch the 3DEP DEM for
the bbox with py3dep. Run it for Eaton.
Done when: the script runs end to end against s3://noaa-goes18, writes check_goes_report.html, and
prints the go/no-go with t_sat_goes; 01 §12 test 1 passes. Paste the output and end with the
5-line summary from AGENTS.md.
```

**A0: fake hazard model**

```
Read AGENTS.md, spec/00_contracts.md §6 and spec/02_physics.md §0–1.
Task A0: FakeHazardModel in engine/emberline/hazard/, implementing the HazardModel protocol from 00 §6.
16 fixed members, analytic ellipse, uniform weights, ess = 16, filter_mode = "fake",
region_arrivals and save_state/load_state as 02 §1 says.
Done when: a test steps it on the fixture grid for 3 cycles and save/load round-trips exactly.
Paste the test output and end with the 5-line summary from AGENTS.md.
```

## Milestones (re-baseline when C0 merges)

The end of the schedule can't move, so a late start means cutting from the bottom of `SPEC.md` §4, not moving M5 or the submission.

| Milestone | Planned | New |
|---|---|---|
| M0: contracts, fixture, fake model, dashboard reading the fixture | Sat 08:00 | |
| M1: end to end on fake physics | Sat 13:00 | |
| M2 + filter gate | Sat 18:00 | |
| Freeze (v1 reported run) | Sat 20:00 | |
| M3: the two moments | Sat 22:00 | |
| Extension code freeze | Sun 04:00 | Sun 04:00 (fixed) |
| M5: rehearsals | Sun 06:00 | Sun 06:00 (fixed) |
| Submit | Sun 08:30 | Sun 08:30 (fixed) |
