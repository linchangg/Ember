# Phase D — panels, provenance and assimilation review

Owner approval: “ok approve for d.” Implemented on `feat/dashboard-phase-d`, based
on restored original plan `ff44fcd`. Phase E remains unapproved and unimplemented.
The connected route remains `/dashboard` in explicit file mode. No frozen shared
contracts or scientific model defaults changed.

## What is connected

`FileDashboard` supplies one FileSource/clock to the map, typed incident panels and
branch selector. `usePanelSnapshot` updates at cycle/status/minute boundaries, and
`panelModel.ts` selects published values rather than estimating risk in the browser.
The sidebar preserves the existing section/typography/layout styles and scrolls
within the desktop viewport.

- Zone rows are sorted by published rank and joined to static names by zone_id.
  `p_by_lead[lead/30-1]` is used only for exact published 30-minute leads. Lead0 or
  an intermediate replay minute is explicitly unavailable, not interpolated or 0%.
- ETA q10/q50/q90, act-by time, evacuation assumption, basis, trend and drivers are
  the published row values. Null and zero remain distinct; null timestamps never
  become January 1970. No unsent evacuation-time edit changes published numbers.
- Burned hectares, front kilometres, growth ha/h and true heading come from Cycle.
  Status includes branch, view, issue, data-through, model/config identity, members,
  ESS, tempering alpha, filter and resampling. A missing config hash is unavailable.
- Recommendations/briefings are matched by cycle/alert/briefing IDs. If the selected
  cycle has no published recommendation, the panel says so. Earlier alert records
  remain in a separately labelled history with their original issue/branch/IDs.
- Observations display observed and available times, hot/clear, sensor/kind, report
  kind, trust, provenance, source reference if present, assimilation status/influence
  and original assimilation cycle. History is causal and lineage-filtered by FileSource.
  Influence is a likelihood-spread diagnostic, not a percentage or causal attribution.
- Report acceptance summaries, eligible activity events, source declarations and
  sensor status use bundle data. Empty collections are shown explicitly. Synthetic
  replay/experimental warnings remain visible; no historical skill claim is made.
- Loading/error/beyond-horizon clear forecast numbers. During loading the map hides
  its forecast layers and the issue label explicitly refers to the last committed
  issue. Failed data never falls back to the mock dashboard.

## Local operator actions

The review card labels approval/decline “local” and states that no messages/calls are
sent. It uses the existing local FileSource transitions; approval survives seeks.
Decline requires a reason. Review state is local to the source instance and resets on a page reload; no server persistence is implied. Only active-branch proposals can be reviewed. Inherited
parent records remain visible but require switching to their branch before review.
This prevents a child review from changing the parent's decision through an inherited
card. No report submission, notification endpoint or hub reducer is connected.

A lineage fix conservatively inherits the last pre-fork **alert state**: a post-fork
parent bundle may repeat an earlier trigger ID with a later supersession. Trigger time
alone cannot date that transition, so the later parent state is not imported into a
child. Local operator decisions remain separate from physics and survive replay.

The source owner also replaces a retired FileSource after development hot reload. Late subscriptions read its terminal snapshot rather than throwing; replacement creates a fresh explicit file replay. This does not reconnect a hub or enable live transport.

## Matched parent/child comparison

Configure complete frozen Branch descriptors through optional `VITE_BRANCHES_JSON`.
It is internal file-source configuration, not a manifest/schema change. Descriptors
are validated, including parent IDs/fork times/cycles. Unknown/missing branches fail.

Selecting a child uses the existing tested lineage loader. `compareBranch(parent,
issue,view,signal)` reads exactly the parent issue, validates its bundle and assets,
and returns no match when that exact issue/horizon is absent. It never publishes a
new selected snapshot or changes operator state. Aborts, generations/epoch checks and
keyed React results prevent old comparisons appearing on a newer branch/view.

The map shows the parent’s published q50 contours blue dashed and the child's brown
dashed, through the same selected lead. Side-by-side zone probabilities and burned
area retain their own model output, at identical issue/view times. No boundary-movement
threshold is assumed. A child still inheriting a pre-fork parent cycle shows unavailable
comparison until a child forecast actually exists.

## Generate a genuine Rust branch fixture

From repository root, Python 3.12 and the locked environment:

```sh
uv sync --project engine --locked
cargo build --manifest-path physics/surface_v1/Cargo.toml -p fire-bridge --release --locked
uv run --project engine python -m emberline.hazard.demo \
  --out /private/tmp/fire-phase-d-fixture --members 16 --report-branch
```

Output must not already exist. The new flag requires at least 24 forecast cycles.
Default invocation without the flag retains the original single-branch demo behavior.
With the flag, main is a control **without** the synthetic delayed report. At cycle24,
a new Rust model loads the checkpoint after cycle23; only the child receives that report.
Both then receive the same subsequent ordinary observations. Their RNG seed/cycle
semantics stay branch-independent. Publication accepts an internal `branch` argument
(default main), retaining current risk/alert/summarizer methods and frozen wire types.

This creates 36 main forecasts + 3 pre-ignition entries and 13 report-demo forecasts.
Main uses 110 observations; the child lineage adds the simulated report. The descriptor
fork is the last parent issue before treatment. Branches, model evidence, checksums and
verification JSON are written alongside the assets. The generator asserts parent t_q
and weights are unchanged while computing each child forecast, and checks checkpoint
next-step parity. These are synthetic integration checks, not historical validation.

## Run and check

```sh
uv run --project engine python -m emberline.hazard.fixture_server \
  --root /private/tmp/fire-phase-d-fixture --port 8794 \
  --allow-origin http://127.0.0.1:5178
```

In another terminal:

```sh
VITE_DATA_SOURCE=file VITE_SCENARIO_ID=rust-fixture \
VITE_ASSET_BASE=http://127.0.0.1:8794/ \
VITE_BRANCHES_JSON="$(cat /private/tmp/fire-phase-d-fixture/branches.json)" \
npm --prefix web run dev -- --host 127.0.0.1 --port 5178
```

Open `http://127.0.0.1:5178/dashboard`, Latest forecast, +120 min, then report-demo.
The sidebar has independent scrolling. Expand Observations to inspect the delayed
report: observed 20:05 PST, available 20:15 PST, trust .25, simulated provenance.
The parent comparison at 21:15/+120 shows published parent/child values; results may
change with a deliberately regenerated fixture/version and are not tuning targets.

```sh
npm --prefix web ci --ignore-scripts
npm --prefix web run typecheck
npm --prefix web run lint -- --deny-warnings
npm --prefix web test
EMBERLINE_FIXTURE_DIR=/private/tmp/fire-phase-d-fixture \
EMBERLINE_SCENARIO_ID=rust-fixture npm --prefix web test
npm --prefix web run build
node tools/check_hazard_frontend.mjs /private/tmp/fire-phase-d-fixture
node tools/check_dashboard_panels.mts http://127.0.0.1:8794/ rust-fixture
uv run --project engine pytest engine/tests
```

Browser acceptance lives in `web/e2e/dashboard.spec.ts`, with pinned
`@playwright/test` and its own controlled fixture interception. It serves actual file
bytes, including generated Rust output when selected; it does not fabricate forecasts.
It checks all 39 entries, all masks/leads against independent byte counts, panel IDs/
probabilities, missing assets/retry, route exit, native branch/report review, local
approval/no POSTs, page errors and screenshots. To run in a normal development/CI environment:

```sh
cd web
npm exec -- playwright install --with-deps chromium
EMBERLINE_FIXTURE_DIR=/private/tmp/fire-phase-d-fixture \
EMBERLINE_SCENARIO_ID=rust-fixture npm run test:browser
```

The runner launches a dedicated Vite on 5189; it does not reuse an unrelated preview.
The [official web-server](https://playwright.dev/docs/test-webserver) and
[CI documentation](https://playwright.dev/docs/ci-intro) informed this setup. Canonical
web CI runs browser tests; Linux native CI also runs them against the paired Rust
fixture, and both native platforms run the web unit/HTTP panel checks. Remote CI status
must be observed separately from local manual Chrome and component test results.

Replay controls serialize each pause/seek/scrub operation. Branch, lead, display and
issue controls are disabled until it commits, so an old cycle cannot rebase a new
lead selection. Browser acceptance follows actual index timestamps, including the
analytic fixture’s missing ignition slot, and waits for the selected cycle before
checking its raster.

## Evidence and remaining boundary

See `docs/evidence/dashboard-phase-d.json` for actual local results and limitations.
The native replay completed in 143.108 s for the paired development workload; this is
not the target N=128/340×340 per-cycle benchmark. Browser CI collection/configuration
is validated locally; execution status is recorded separately. Scientific calibration,
held-out historical skill and performance targets remain open. No continuously updating
hub transport, live report API, notification delivery or deployment is included.

Stop here. Phase E requires a new direct confirmation.
