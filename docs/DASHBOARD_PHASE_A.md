# Dashboard connection: Phase A record

Subsequent update: the owner directly authorized Phase B, which is now implemented;
see `DASHBOARD_PHASE_B.md`. The Phase A boundary below is the original handoff.

The owner explicitly requires direct confirmation before each subsequent phase.
This record covers Phase A only. No FileSource, dashboard store/hooks, forecast-map
wiring or later-phase UI changes were implemented. The calibration candidate and
historical scorer are separate authorized scientific framework work.

## Completed

- Built the release Rust bridge from merged main `c5e2599`.
- Regenerated the complete N=16 fixture: 36 forecast + 3 pre-ignition cycles,
  111 observations, 7 alert records and exact checkpoint next-step parity.
- Total local replay time: 114.130 s. This is not the target-size per-cycle benchmark.
- Verified all 39 entries and 72,000-byte rasters using the existing Node consumer,
  both from files and through HTTP.
- Added a loopback-only read-only fixture server with explicitly allowed development
  origins. HEAD/GET/OPTIONS work, POST is rejected, missing assets return 404, and
  unapproved origins, hidden files, traversal and escaped symlinks are rejected.
- Prepared the existing plan as `docs/DASHBOARD_ENGINE_CONNECTION_PLAN.md`.
- Independent calibration research code remains opt-in; model defaults are unchanged.

## Local output and restart

Generated assets: `/private/tmp/ember-dashboard-phase-a/`.
The server started for this session at `http://127.0.0.1:8790/`; it lasts only while
that local process and machine remain available. Restart from the repository root:

```sh
engine/.venv/bin/python -m ember.hazard.fixture_server \
  --root /private/tmp/ember-dashboard-phase-a --port 8790 \
  --allow-origin http://localhost:5173 --allow-origin http://127.0.0.1:5173
```

In another terminal with Node 22+:

```sh
node tools/check_hazard_frontend.mjs http://127.0.0.1:8790/
node tools/check_fixture_host.mjs http://127.0.0.1:8790/
```

If generated output has been removed, regenerate with the Phase A commands in the
connection plan. Keep canonical fixtures unchanged.

## Configuration prepared for Phase B, not yet wired

```dotenv
VITE_DATA_SOURCE=file
VITE_SCENARIO_ID=rust-fixture
VITE_ASSET_BASE=http://127.0.0.1:8790/
```

The current application does not implement FileSource; setting these alone does not
connect it. Port 5173 origins are permitted by the fixture server; explicitly add
another exact origin if a different dashboard development port is chosen.

## Approval boundary

Stop here. Phase B would add the shared FileSource, clock/store/hooks, validated
asset loader and request cancellation. It needs a new direct owner confirmation.
Phase B completion must itself stop for confirmation before Phase C rendering work.
The same rule applies to all remaining phases, including live runtime/hub assembly.
