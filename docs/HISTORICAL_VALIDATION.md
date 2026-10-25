# Historical validation framework v1

This is an implemented offline, frozen-artifact scorer. It does not fetch real data,
run the real-data orchestrator, fit parameters or certify forecast skill.
A synthetic example exercises the scoring/audit code only. The implemented methods
are in `engine/emberline/hazard/historical.py`; the manifest JSON schema is
`docs/schemas/historical-v1.schema.json`.

## Run the included framework example

From the repository root with the locked engine environment installed:

```sh
engine/.venv/bin/python -m emberline.hazard.historical \
  --make-example /tmp/emberline-validation-example \
  --out /tmp/emberline-validation-example-report.json
engine/.venv/bin/python -m pytest engine/tests/test_historical.py engine/tests/test_hazard_calibration.py engine/tests/test_fixture_server.py -q
```

Use an empty example directory. The result MUST say `synthetic_harness_only` and
`scientific_acceptance: not_established_by_this_report`. The analytical example does
not invoke Rust and must never be presented as historical validation.

## Build a real historical experiment

1. **Freeze the question before running test forecasts.** Choose incidents and assign
   each once to train, calibration or test. Use Eaton as a case study if data are ready;
   one incident cannot establish cross-incident generalization. Declare 30/60/120/360
   minute leads, threshold, seeds, domain and baselines. Do not change thresholds after
   viewing test results. Record all runs, not only successful ones.
2. **Acquire independent truth.** Obtain timestamped IR or independently mapped
   perimeters with source/version/publication information. Record observation-valid
   time separately from publication/availability. Do not use a final fire perimeter as
   truth for earlier times. A satellite active-fire point is not a complete burned mask.
3. **Prepare one projected evaluation grid.** All arrays use EPSG:32611, north-first,
   square equal-area-in-grid cells with exact upper-left x0,y0, nx,ny and cell_m.
   Reproject vector inputs explicitly and rasterize on this grid using a fixed rule
   (recommended center-in-polygon / `all_touched=False`). Record that choice in a
   checksummed configuration input. Persist a separate coverage/validity mask; outside
   the mapped/quality-controlled footprint is unknown, not unburned. Reject mismatched
   grids instead of silently resizing or flipping rasters.
4. **Replay each mode causally.** Run `none`, `reinit` and `pf` through the operational
   Rust-backed HazardModel using identical initial conditions, seed and forcing policy.
   Only release inputs available at/before each issue. Do not initialize from a later
   perimeter. Baseline `persistence` freezes the burned probability at issue.
   Record every consumed weather/static/observation/config input; all must have a local
   path, SHA256, role, source ID and available_sim_ms. Configs and prior/calibration
   choices are inputs too. The scorer audits the declared list; it cannot prove that
   an upstream producer omitted no inputs. Review the replay audit independently.
5. **Archive raw ensemble arrivals.** `write_prediction()` takes arrays shaped
   (N,ny,nx), normalized weights and grid/time metadata. Arrivals are absolute minutes
   since integer `t_ref_sim_ms`, not epoch floats or leads. Keep unreached as +inf.
   Use the post-step member histories and corresponding weights; in the present
   implementation these live in the model's internal `_arrivals` and returned
   `ForecastArrays.weights`. Isolate that internal access in your evaluation runner;
   it is not a new public HazardModel method. Record native physics/source identity,
   config hash, seed and measured runtime in each Prediction record. Five encoded
   dashboard quantiles are insufficient to reconstruct exact probability scores.
6. **Register matched cases.** Each case declares incident, issue, lead, one independent
   truth source and exactly four Prediction artifacts named persistence/none/reinit/pf.
   Truth NPZs contain `burned` (0/1 array), `valid` (bool array), `valid_sim_ms` (scalar),
   and `grid_sha256` (scalar string from `grid_digest(Grid(...))`). The valid time must
   exactly equal issue+lead; v1 does not interpolate nonmatching perimeter times.
   Treat inaccurate perimeter timestamps explicitly in a separate sensitivity study.
7. **Freeze and score.** Copy the generated example manifest as a structural guide,
   replace every synthetic input/incident/artifact and set dataset_kind=historical.
   Set frozen=true, compute SHA256s and make the experiment immutable under version
   control or content-addressed storage. Run:

   ```sh
   engine/.venv/bin/python -m emberline.hazard.historical \
     --manifest /path/to/frozen-experiment/experiment.json \
     --out /path/to/reports/historical-report.json
   ```

   Paths are relative to and confined under the manifest directory; symlink escapes,
   missing files and checksum changes fail. The report stores manifest/evaluator hashes.
   Keep the report outside source artifacts and retain the original manifest.

### Minimal array export recipe

```python
from pathlib import Path
import numpy as np
from emberline.hazard.historical import Grid, grid_digest, write_prediction, digest

grid = Grid(nx=340, ny=340, cell_m=90, x0=YOUR_X0, y0=YOUR_Y0)
# forecast = model.step(issue_min, newly_available_observations)
write_prediction(
    Path("pf.npz"), model._arrivals.copy(), forecast.weights,
    grid=grid, issue_sim_ms=ISSUE_MS, t_ref_sim_ms=REFERENCE_MS, horizon_min=360,
)
np.savez_compressed(
    "truth.npz", burned=independent_burned_mask.astype(np.uint8),
    valid=independent_coverage_mask.astype(bool), valid_sim_ms=TRUTH_VALID_MS,
    grid_sha256=grid_digest(grid),
)
print(digest("pf.npz"), digest("truth.npz"))
```

Uppercase names and independent masks must come from the frozen incident inputs;
this recipe intentionally does not invent historical data. Ensure Grid values match
the model's grid and convert `model.grid.t_ref_s * 1000` to the reference milliseconds.
For persistence, replace future arrival entries with +inf, retaining arrivals at/before
issue and the matching issue weights. Truth files cannot appear in any mode's input
list, even under another ID with the same content hash.

## Scores and their limits

For target time t, raw ensemble probability is `p_c=sum_m w_m*1[T_mc<=t]`, including
unreached-member mass. Score only independently valid cells V:

- Brier = mean_V((p-y)^2).
- IoU = intersection/union; Sørensen = 2*intersection/(predicted+observed).
- Thresholded area bias in hectares, predicted and observed area, area ratio.
- Symmetric interior raster-boundary mean and p95 distance in metres.
- Ten-bin reliability counts, mean probability and observed frequency.
- Per-case Brier skill relative to persistence: `1-BS_model/BS_persistence`;
  undefined if persistence Brier is zero.

Both-empty IoU/Sørensen are one and explicitly flagged; do not hide empty-case counts.
Undefined area ratios/boundaries are JSON null. Boundary metrics exclude domain and
unknown-coverage edges. They describe cell boundaries, not exact vector Hausdorff
geometry, and require a later vector-level sensitivity check.

Summary Brier averages cycles within each incident, then gives incidents equal
weight, separately by split, mode and lead. Keep per-case failures available. Bootstrap
whole incidents for confidence intervals only after enough independent incidents are
available; the present scorer does not fabricate an interval from correlated pixels.
Arrival-time/interval error is not inferred from a single perimeter; add independent
arrival brackets and a censored scoring protocol before claiming that metric.

Runtime is supplied metadata, not a benchmark result or proof of <=0.5 s. Use the
existing dedicated benchmark for performance acceptance. Historical scoring reports
`historical_artifacts_scored`, not scientific acceptance. Publication of a validation
claim additionally requires an independent provenance review, frozen selection rules,
held-out improvement with uncertainty, physics-oracle checks and documented failures.
