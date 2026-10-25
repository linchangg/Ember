# Phase A / scientific framework verification — 2026-10-24

Base: main `c5e2599`; branch `feat/historical-validation-phase-a`.

- Full Python suite: 185 passed, 17 optional service/network checks skipped.
- New scientific/host tests: 37 passed within that suite.
- Ruff formatting and undefined/unused-name checks passed on new Python files.
- Frozen HazardModel compatibility probe passed.
- Rust bridge release build passed; no default native solver or filter change.
- Complete synthetic fixture: 36 forecast + 3 pre-ignition cycles, 111 observations,
  7 alerts, checkpoint next-step parity. Full local replay 114.130 s.
- Existing Node decoder passed all entries from filesystem and HTTP; 72,000 bytes/raster.
- Three existing Node contract regression tests passed.
- Host smoke passed allowed-origin CORS, HEAD, OPTIONS, rejected POST/foreign origin,
  missing asset 404 and hidden-file rejection. Traversal/symlink checks have unit tests.
- Historical harness example scored, then rescored its frozen manifest successfully.
  Its status is synthetic_harness_only; scientific acceptance remains not established.

The small analytical inputs are committed in `docs/examples/historical/`. Reproduce:

```sh
engine/.venv/bin/python -m emberline.hazard.historical \
  --manifest docs/examples/historical/experiment.json --out /tmp/example-report.json
```

`fixture-verification.json` records the native-backed integration run.
`historical-harness-smoke.json` records the separate analytical scorer smoke run.
Neither establishes historical skill. Phase B is not started and requires direct
owner confirmation. No actual dashboard UI change is part of Phase A.
