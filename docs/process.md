# Process log

One line per merged agent task or team decision (`AGENTS.md` asks for this). This log is also our evidence of how we used Cursor and Grok Bot (SpaceXAI track, MLH A-Team).

Format: `Sat 13:05 · Cody · C0 contracts + fixture merged (PR #3) · Cursor agent`

- Sat 12:40 · Cody · Spec, agent rules and build plan added to the repo (`SPEC.md`, `AGENTS.md`, `spec/`, `research/`, `docs/build_process.md`) · Claude

- Sat Oct 24 · Abby · Engine build plan and reproducible reference aids added for review; Rust proposal does not replace the canonical Python demo contracts · Codex

- Sat Oct 24 · Abby · Verified surface_v1 research import prepared on integration/surface-v1-verified from main; added independent Behave oracle, numerical/workflow regressions, correction/checkpoint/ensemble/evaluation fixes and isolated CI; Python HazardModel replacement remains gated · Codex

- Sat Oct 24 · Abby · surface_v1 research adapter: grid/time/arrival conversions, subprocess bridge, synthetic compatibility test, and smoke benchmark; Rust backend not activated and HazardModel contracts unchanged · Cursor

- Sat 13:13 · Cody · C0 contracts + analytic fixture (PR #4 merged into a stale base; landed on main with wave 1) · Cursor agent
- Sat 15:05 · Cody · G1-a GOES gate: GO (Eaton), t_sat_goes 02:11 PST · Claude
- Sat 15:20 · Cody · G1-b SpacetimeDB spike PASS (local) · Claude
- Sat 15:30 · Cody · T0/T3/T4/T5/T6/T7 engine wave 1 (106 tests green) · Claude
- Sat 16:40 · Cody · T2 phase 2: full SpacetimeDB hub (20 tables, all 04 §2 reducers) + Python poll + web bindings; 9/9 acceptance + 5 extra hub tests pass locally after review fixes · Claude
- Sat Oct 24 · Abby · Project owner approved Rust as the HazardModel backend; added versioned Python/Rust adapter, observation assimilation, checkpoint/contract tests and full synthetic fixture verification against current main; decision and remaining scientific/performance gates in docs/RUST_HAZARD_BACKEND.md and docs/RUST_HAZARD_NEXT_TASKS.md · Codex

2026-10-24 — Reviewed main 9e00a67 and Cursor/research branches; combined research adapter 8e4055b with Rust HazardModel, fixed globe empty-feed/lifecycle errors, split lazy comparison bundles, refreshed hub dev dependency and added web/hub CI. Combined local evidence: 148 engine + 38 research + 50 Rust release tests; 39-cycle synthetic fixture with checkpoint parity. See docs/MAIN_INTEGRATION_REVIEW.md; live UI/runtime assembly, scientific and performance gates remain open.
- Sat 18:30 · Cody · Wave 2 F0: fixture judges R2 on integer-ms act-by; alerts unchanged (18/20/30/32), cross-check exact · Claude workflow
- Sat 18:30 · Cody · Wave 2 T4L: briefings via OpenRouter, prompt v2; live c18/c20/c30 validated ($0.05) · Claude workflow
- Sat 18:30 · Cody · Wave 2 T8: GOES-18 pixel table (21,026 rows), scan events, replay manifest · Claude workflow
- Sat 18:30 · Cody · Wave 2 T9a/T9b: 131 LA County zones (event snapshot, disclosed), exposure, assets, spawn points; terrain, manifest, sensors, timeline · Claude workflow
- Sat 18:30 · Cody · Wave 2 T10/T11/T12: engine loop, publisher, sinks, poller, run.py; Cartesia dry run; local API :8787; 03 §14 tests 1-5 on a hazard stand-in · Claude workflow
- Sat 19:45 · Cody · T10 on Abby's Rust HazardModel: factory-only model, dev (N=16, labelled)/target profiles, weather_from_hrrr forcing with per-cycle audit, evidence/identity/catalog/forcing cache keys, lineage-bound checkpoints, live loop survives physics errors; native tests run in CI only · Claude workflow

- Sat Oct 24 · Abby · Prepared deep learning research framework for review: compared candidate architectures, selected bounded U-Net spread corrections through the Rust solver, specified Python training/ONNX-to-Rust inference, contract compatibility, human-impact evaluation and latency gates; documentation only, no learned model activated · Codex

- Sat Oct 24 · Abby · E0 local experiment contracts, atomic run directories, and prediction snapshots; 8 focused tests passed and wire schema stayed 2; no forecast-skill or performance claim · Cursor
