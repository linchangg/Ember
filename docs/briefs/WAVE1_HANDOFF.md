# Wave 1: handoff for review and commit

Claude ran these briefs on Sat Oct 24 in one checkout, on local branch `wave1` (cut from `origin/c0/contracts-fixture`, so it includes PR #4). **Nothing is committed.**

Full engine suite: `cd engine && PYTHONUTF8=1 uv run pytest -q` → **106 passed, 3 skipped**. The skipped tests are the network, hub and live-Claude ones.

## Results

| Brief | Status | Key result |
|---|---|---|
| T0 grid | done | 12 tests; `grid.json` generated |
| T1 GOES gate | done | **G1-a: GO (Eaton), `t_sat_goes` = 02:11 PST** (10:11Z); parallax uses 3DEP 90 m; FDCM M2 covers the bbox |
| T2 hub | **phase 1 only** | **G1-b: PASS** on local `spacetime start`, all four criteria; phase 2 is waiting for Cody's "go" |
| T3 risk + alerts | done | 23 tests; found a fixture bug (below) |
| T4 briefing + validator | done | 25 tests; **live Claude call not run** (no `ANTHROPIC_API_KEY`) |
| T5 clock/cache/summarizer | done | 18 tests; `write_assets` 0.076 s on 340×340 |
| T6 HRRR | done | 22 inits via Herbie; Santa Ana check PASS (06Z: 11.6 m/s from 26°) |
| T7 sources | done except `ForcingProvider` | `ForcingProvider` blocked on Abby's A0 (`hazard/api.py`) |
| C1 TypeScript | not started | gated on PR #3 and Linda's window |

**`process.md` lines:**
- `Sat 15:05 · Cody · G1-a GOES gate: GO (Eaton), t_sat_goes 02:11 PST · Claude`
- `Sat 15:20 · Cody · G1-b SpacetimeDB spike PASS (local) · Claude`
- `Sat 15:30 · Cody · T0/T3/T4/T5/T6/T7 engine wave 1 (106 tests green) · Claude`

## Suggested commits (one per brief keeps review easy)

| Brief | Paths |
|---|---|
| deps | `engine/pyproject.toml`, `engine/uv.lock`, `.gitignore` |
| T0 | `engine/emberline/data/{__init__,config,grid}.py`, `data/scenarios/eaton/{scenario.yaml,grid.json}`, `engine/tests/test_grid.py` |
| T1 | `engine/emberline/data/{area_x,goes}.py`, `engine/emberline/scripts/check_goes.py`, `engine/tests/test_{area_x,goes_geo}.py` |
| T2 | `stdb/{package.json,package-lock.json,tsconfig.json,.gitignore,src/,spike/}`, `engine/emberline/sync/`, `engine/emberline/scripts/hub_spike.py` |
| T3 | `config/alert_rules.yaml`, `engine/emberline/risk/`, `engine/tests/test_{risk,alerts}.py` |
| T4 | `engine/emberline/ai/`, `engine/tests/test_{validate,briefing}.py`, `engine/tests/data/briefing_inputs/` |
| T5 | `engine/emberline/runtime/{__init__,clock,cache,summarize}.py`, `engine/tests/test_{clock,cache,summarize}.py` |
| T6 | `engine/emberline/data/hrrr.py`, `engine/tests/test_hrrr.py` |
| T7 | `engine/emberline/runtime/sources.py`, `engine/tests/test_sources.py` |

**Don't commit any of these:**
- `engine/.env`: holds the STDB tokens and the PIN; it's git-ignored.
- `.agents/` and `skills-lock.json`: not created by this run.
- `fixtures/**`: it shows as modified only because `test_fixture` regenerates it. There's no content diff; run `git checkout -- fixtures` first.

The new `.gitignore` lines keep these out of the commit:
- `data/scenarios/*/forcing/` (30 MB)
- `check_goes_report.html`
- `engine/cache/` (py3dep's HTTP cache)
- `stdb/dist/`

## Needs a decision

1. **Fixture R2 timing bug (PR #4).** `make_fixture.py` evaluates "act-by within 15 min" on float minutes. The bundles store act-by in whole ms, and on those values the act-by is exactly 15.0 min out at cycle 29 (FIX-Z3) and cycle 31 (FIX-Z2). The spec's "≤ 15" therefore fires one cycle earlier than the fixture's alerts at 30 and 32.
   - `test_alerts.py` currently documents the 1-cycle shift.
   - Fix: run the R2 test on the integer-ms act-by in `make_fixture.py` and regenerate. Then simplify the cross-check and update T3's expected list and T4's saved inputs (`c30.json`).
2. **`critical_types`.** Fixture assets include `fire_station` and `water_plant`, which would then appear in drivers and alert text. The publisher (T10) should pass only critical types (hospital, nursing home, assisted living, school), in readable form.
3. **Area X box.** T1 says it is tight (3.1 × 5.0 km against 2.3 × 2.8 km pixels), and its northern half is front-range terrain. It suggests possibly trimming `lat_max` to ~34.215; that's untested. Re-check `t_sat_goes` once T9 writes the real Lake Ave centreline.
4. **SpacetimeDB 2.13 vs the docs.** Fix the docs, then start T2 phase 2:
   - `spacetime generate` needs `--module-path`, not `--project-path` (in `AGENTS.md`, `spec/04` §4 and the T2 brief).
   - Multi-statement SQL is unsupported, so the poll costs 4 requests.
   - Reducer rejections return HTTP **530**.
   - Calls need `Content-Type: application/json`.
5. **h5netcdf.** It can't open GOES files without `h5py`, so T1 reads them with `netCDF4` instead. Either add `h5py` or leave it as is.
6. **HRRR forcing files** (22 files, 30 MB): ignored for now. Commit or host them deliberately if the demo laptop shouldn't rebuild them (about 25 min).
7. **Duplicate `p_words`.** It exists in both `risk/alerts.py` and `ai/validate.py`; unify them after merge.

Still open:
- T4's live Claude check: needs `ANTHROPIC_API_KEY` in `engine/.env`.
- `ForcingProvider`: after A0.
- Publishing the hub to Maincloud.
- C1.
