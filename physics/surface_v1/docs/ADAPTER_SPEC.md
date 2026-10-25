# surface_v1 adapter specification

Integration note: the inventory below records the research branch's baseline at
`8cea5fc`, before the operational adapter landed. `engine/ember/hazard/` now
implements the owner-approved Rust HazardModel, weighted inference, SB40/urban
mapping and delayed observations. See `docs/RUST_HAZARD_BACKEND.md` at the repository
root for those decisions. This research utility intentionally retains its separate
unweighted CLI API and conservative conversion policies; it is not a second backend.

Research compatibility map between the documented Ember grid and the Rust `surface_v1` bundle. This file does not activate a backend and does not change frozen contracts.

## Inventory

Inspected again at `8cea5fc4edf5d8d9d1f7bbf628633fce342c0785`, which is current `main`. PR #7 is in that history (`976441ac554ffda2b3b7040174cbcb4e8bd6a134`). `integration/surface-v1-verified` (`8d6002be9bb1d2dab9284b5709eb6410332fc36a`) is an ancestor. Later main commits do not change `physics/surface_v1`.

1. **Application interface available today.** `engine/ember/contracts.py` has the landed wire models and `QUANTILES = (0.1, 0.3, 0.5, 0.7, 0.9)`. `engine/ember/data/grid.py` has the landed metric grid: upper-left outer edge, row 0 north, `Affine(cell_m, 0, x0, 0, -cell_m, y0)`, and cell centers `x0 + cell/2 + cell·col`, `y0 - cell/2 - cell·row`. `engine/ember/hazard/` does not exist. No `Grid`, `Landscape`, `Weather`, `ForecastArrays`, or `HazardModel` class is in the tree. Physics behavior in `spec/00_contracts.md` §6 and `spec/02_physics.md` is still specified text. This adapter does not invent that protocol. It does use the landed grid formulas.

2. **Rust entry points and outputs available today.** `physics/surface_v1` `fire-cli`: `validate`, `simulate`, `checkpoint`, `resume`, `correct`, `ensemble`, `evaluate`, `make-fixture`, `inspect-behavior`. A simulate directory publishes `run.json` (schema 2), `diagnostics.json`, `manifest.sha256`, `arrival_time_s.f64le`, `arrival_status.u8`, and integer-second `horizon_*` arrays. Arrays are little-endian, row-major, row 0 south. Ensemble output is `ensemble.json` plus scenario-frequency and conditional q10/q50/q90 arrays. It does not publish per-member arrivals. Checkpoints are schema 2 and keep the parent `start_utc`.

3. **Missing contracts and unresolved semantics.** There is no landed encoder for `arrival_q.bin`, no weighted CDF implementation, no FBFM40-to-catalog table, no agreed meaning for `ARRIVAL_INITIAL_UNKNOWN`, no 10 m to midflame reduction, no humidity-to-moisture function, and no production rule for partial ensembles. Rust timestamps are whole-second `YYYY-MM-DDTHH:MM:SSZ`. Rust weather must be available at `start_utc`. Corrections require the observed, available, and correction times to be equal. Slope and aspect are application fields; Rust derives a gradient from elevation.

4. **What can proceed on its own.** Row reversal and the outer-edge origin formulas, because both sides are stated in source or spec text. Integer-millisecond time arithmetic. Unconditional quantiles from caller-supplied member outcomes. A subprocess bridge to the existing executable. A smoke benchmark of that executable. Production selection, contract edits, and schema changes cannot proceed here.

## Status words

| Status | Meaning |
|---|---|
| Confirmed | Both representations are in current source, or one side is source and the other is the cited spec sentence, and the formula does not choose an open policy. |
| Specified, not landed | The application side exists only as spec text. The field is not read from a Python object. |
| Awaiting decision | A production choice is still open. The utility rejects it or requires an explicit research flag. |

## Field mapping

Application arrays are C-order, row 0 north, unless a row says otherwise. Rust arrays are little-endian, C-order (row-major), row 0 south. The row map for every spatial field is `A_rust[r, c] = A_app[H-1-r, c]`. Reprojection is not part of that map. Wind components are not negated when rows are reversed.

| Field | Application representation | Rust representation | Units and dtype | Shape and memory order | Conversion | Validation | Missing data | Status |
|---|---|---|---|---|---|---|---|---|
| CRS | Landed `grid.py` reads `g["crs"]`. Eaton spec and scenario text use `EPSG:32611`. | `GridSpec.crs` string. Geographic names containing `4326`, `WGS84`, or `LATLON` are rejected. | CRS id, text | scalar | Pass the same string. No reprojection. `grid.py` can reproject lon/lat separately; this layer does not call it. | Default accepts only `EPSG:32611`. Any other geographic name is an error. `allow_research_metric_crs` may pass an identical non-geographic label such as `LOCAL_METRIC_TEST`. | Absent CRS is an error. | Confirmed. Reprojection stays in `grid.py`. |
| Width and height | Spec `nx`, `ny` or `Grid.W`, `Grid.H`. | `GridSpec.width`, `height` (`u32`). | cell counts | scalar | `width = nx`, `height = ny`. | Both axes `>= 3`. Array shape must equal `(height, width)`. No reshape. | Missing extent is an error. | Rust minimum is confirmed. Application 340×340 is the specified demo extent, not a limit of this utility. |
| Origin | Landed `grid.py` `transform`: `Affine(cell_m, 0, x0, 0, -cell_m, y0)`. `corners_lonlat` uses outer corners. `x0`, `y0` are the upper-left outer edge. | `x0_m`, `y0_m` are the lower-left outer edge. `center()` is `(x0+(c+1/2)dx, y0+(r+1/2)dy)`. | metres, f64 | scalar | `x0_rust = x0_app`, `y0_rust = y0_app - H * cell_m`. That `y0_rust` is the landed `bl` northing. | `origin` must be `upper_left_outer_edge`. A cell-center origin is rejected. Non-finite origins are rejected. | Missing origin is an error. | Confirmed in `grid.py` and `grid.rs`. |
| Resolution | Spec `cell_m` (90 m for Eaton). | One `cell_size_m` used for both axes. | metres, f64 | scalar | Copy when the cell is square. | Positive and finite. A Rust grid whose cell size, origin, or shape differs is `grid_misaligned`. | No silent resample. | Confirmed. Non-square cells are unsupported because Rust has one cell size. |
| Cell centers | Landed `grid.py` `cell_centers`: `x = x0 + cell/2 + cell·col`, `y = y0 - cell/2 - cell·row`, row 0 north. | `x = x0 + (c+1/2) cell`, `y = y0 + (r+1/2) cell`, row 0 south. | metres, f64 | `x` length `W`, `y` length `H` | After the origin conversion, app row `r` matches Rust row `H-1-r`. | Compared exactly for values produced by these formulas. | Not stored as an array in either bundle. | Confirmed in `grid.py` and `grid.rs`. |
| Row order | Row 0 north (`00` §1). | Row 0 south (`README`, ensemble metadata `south_to_north`). | n/a | last two axes are row, column; C-contiguous after conversion | `flip` on the row axis only, then `ascontiguousarray`. Dtype and values are unchanged by the flip. | Rank `< 2` is rejected. A flipped view with negative strides is copied to C order. | n/a | Confirmed. |
| Elevation | Spec `Landscape.elev` `(H, W)` float32 metres. Not landed. | `elevation.f64le` | metres; app f32, Rust f64 | `(H, W)` row-major | Flip, then cast each f32 value to f64. The f64 value is the exact f32 value. | Finite. Shape must match the grid. | Non-finite elevation is an error. Rust has no nodata code. | Specified, not landed. Widening cast is confirmed as representation, not resampling. |
| Fuel identifiers | Spec int16 FBFM40. `91` is urban NB91 and burnable in `02` §5. `92/93/98/99` are non-burnable. | `fuel_id.u16le`. Ids must exist in `docs/spec/fuel_models.csv` when `valid=1` and `burnable=1`. Anderson 1–13 and Scott-Burgan ids from that file. No NB91 model. | code, integer | `(H, W)` | `passthrough_catalog_id` only: flip, check burnable+valid ids against the catalog. | Unknown burnable ids error. Policy `fbfm40_to_catalog` errors. | Invalid and non-burnable cells are not looked up, matching `validate_inputs`. | Awaiting decision for FBFM40 and NB91. Passthrough of catalog ids is confirmed. |
| Validity | No separate landed mask. Spec fuel and data pipeline imply cells outside the prepared grid are absent. | `valid.u8`, 0 or 1. `valid!=1` becomes `ARRIVAL_INVALID` and skips fuel lookup. | 0/1, u8 | `(H, W)` | Flip. Bool is stored as 0/1. | Values other than 0 or 1 error. | A missing mask is an error. It is not filled with 1. | Rust side confirmed. Application mask is not a landed field; callers pass it explicitly. |
| Burnability | Spec: NB92/93/98/99 have ROS 0; NB91 is burnable urban. | `burnable.u8`, 0 or 1. Non-burnable modeled-off cells get `ARRIVAL_NOT_REACHED` and must have positive phi. | 0/1, u8 | `(H, W)` | Flip. Fuel code does not set this mask. | Values other than 0 or 1 error. | Missing mask is an error. NB91 is not marked burnable or non-burnable by this layer. | Awaiting decision for urban cells. Rust mask handling is confirmed. |
| Slope | Spec `(H, W)` float32 rise/run. | Not an input. `prepare` differences elevation into `gx`, `gy`. | rise/run vs 1/m | app `(H, W)` | Not converted. The bundle writer has no slope argument. | n/a | Dropping a supplied slope would hide a disagreement with the derived gradient, so the writer cannot accept it. | Awaiting decision. |
| Aspect | Spec degrees clockwise from north, downslope. | Not an input. Gradient sign carries the downhill direction. | degrees vs 1/m | app `(H, W)` | Not converted. | n/a | Not accepted by the writer. | Awaiting decision. |
| Wind east | Spec `u10` `(B, H, W)` float32 m/s eastward, earth-relative, 10 m (`00` §6). | `wind_east.f64le` m/s toward east. Reference enum is only `midflame_horizontal_heading_surface_speed`. | m/s; app f32, Rust f64 | `(H, W)` per frame | Flip the field. Do not negate. | Finite. `components_are_midflame=True` is required before a bundle is written. | 10 m values are rejected rather than reduced or relabeled. | Component axis is confirmed. Reference height is awaiting decision. |
| Wind north | Spec `v10` m/s northward. | `wind_north.f64le` m/s toward north. | m/s, f64 | `(H, W)` per frame | Flip the field. Do not negate. | Same as east. | Same as east. | Confirmed axis. Reference height awaiting decision. |
| Wind direction | Spec degrees true, clockwise (`00` §1). `02` §3 rotates toward-direction clockwise. | Stored as components, not a direction. | degrees → m/s | same shape as the inputs | If speed is already midflame: `east = speed * sin(direction)`, `north = speed * cos(direction)`. Row flip is a separate later step. | `speed_is_midflame` required. Negative speed errors. | Direction without a midflame speed is rejected. | Decomposition formula is the toward-vector from the spec's clockwise-from-north wording. Applying it to 10 m speed is awaiting decision. |
| Wind reference height | 10 m earth-relative (`00` §6). `02` §3 uses `WAF` 0.4 or 0.2. | Midflame only. | m/s | scalar metadata | No WAF is applied. | Bundle wind reference is the Rust enum, and only after the midflame flag. | n/a | Awaiting decision. |
| Fuel moisture | Spec scenarios are 1-h dead fuel moisture percent, with fixed 10-h/100-h offsets and live fractions (`02` §2). `Weather` carries `rh`, not a spatial moisture field. | `m1`, `m10`, `m100`, `mlh`, `mlw` fractions, f64, per cell. | fraction, f64 | `(H, W)` per frame | Flip explicit fractions. Scalars broadcast, then flip. | Finite and `>= 0`. | RH is rejected by `reject_humidity_as_moisture`. Missing fractions are an error. | Awaiting decision for RH and for the percent scenarios. Explicit fractions are confirmed Rust inputs. |
| Temperature | Spec `Weather.temp_c` `(B,)` . | No input field. | °C | `(B,)` | Not written. | n/a | Not accepted. | Awaiting decision. There is nowhere to put it. |
| Forecast issue time | Spec `knowledge_sim_ms` / cycle issue. `t_q` is minutes after issue (`00` §6). | Not a Rust field. Availability uses `start_utc`. | epoch ms, int | scalar | Kept on `TimeOrigins.t_issue_ms`. Compared with `t_available_ms`. | Integer milliseconds. | Missing issue time is an error when checking availability. | Specified, not landed. Not assumed equal to `t_start`. |
| Application reference time | Spec `t_ref` / `Grid.t_ref_s`. Physics minutes are float32 minutes since `t_ref`. | Not stored. Checkpoints store `start_utc` only. | epoch ms, int | scalar | `minutes = (t_start_ms - t_ref_ms) / 60000 + duration_s / 60`, computed in integer milliseconds and f64, then cast to f32. | Absolute result `<= 1_000_000` minutes. Non-finite durations error. | The adapter records `t_ref` beside checkpoint metadata. It does not rewrite the checkpoint. | Formula confirmed for this research layer. `t_ref` itself is specified, not landed. |
| Rust simulation origin | n/a | `start_utc`, whole-second Zulu. Arrival seconds are since this instant. | text timestamp; arrivals f64 seconds | scalar | `format_rust_utc` only when `epoch_ms % 1000 == 0`. | Fractional milliseconds error and are not truncated. Years before 1970 error. | Missing origin is an error. | Confirmed. Fractional UTC is a documented Rust incompatibility. |
| Observation acquisition time | Spec `observed_sim_ms`. | Frame `observed_or_valid_utc`. Correction `observed_utc`. | epoch ms → Zulu text | scalar per frame | Stored as the valid/observed timestamp. It is not the availability test. | Whole seconds when written to Rust. | Acquisition alone does not make weather usable. | Confirmed distinction. Delayed correction (`observed != available`) is rejected by Rust and is not bypassed. |
| Data availability time | Spec `available_sim_ms`. Cutoff for a forecast at issue `t` is `available <= t`. | `available_utc <= start_utc` (`fire-propagation` `validate_inputs`). | epoch ms → Zulu text | scalar per frame | Check `t_available_ms <= t_issue_ms`. Also require `t_available_ms <= t_start_ms` before writing, because Rust enforces that. | Failure names which clock failed. Timestamps are not clamped. | A future valid time is allowed when availability is at or before the issue. | Application cutoff is specified. The extra Rust `<= start_utc` rule is confirmed and can be stricter. Awaiting a decision when issue and start differ. |
| Weather frame time | Spec hourly bands, `t_min` minutes since `t_ref` (`00` §6). | `time_s` seconds after `start_utc`. First frame must be `0`. Hold-right-continuous. | seconds, f64 | one record per frame | Caller supplies `time_s`. This layer does not infer it from `t_min`. | Strictly increasing, first value `0`, coverage end `>=` last horizon. | No frame is an error. | Rust schedule is confirmed. Mapping hourly bands onto `time_s` is awaiting decision, so it is not automatic. |
| Initial fire state | Spec MTT `T`: float32 minutes since `t_ref`, `inf` unburned. Ignition is a point plus radius (`00` §6). `t_q = 0` means burned at issue. | Signed distance `phi`. Negative is inside. Exact `0` is rejected. Isolated negatives are rejected. Initially negative cells become `ARRIVAL_INITIAL_UNKNOWN` (time stored as 0, history unknown). | metres, f64 | `(H, W)` | Research disk: `hypot(center) - radius`, then flip. Exact zeros become `+1e-3` m. | At least one negative and one positive modeled cell. Blocked cells must be positive. 8-connected burned neighborhood. | Point ignitions that are a single cell are rejected. `phi` is not an arrival time. | Rust constraints confirmed. Interpreting unknown history as burned-at-issue (`t_q = 0`) is awaiting decision. |
| Member arrival time | Spec per-member `T` minutes since `t_ref`, `inf` = never in `region_arrivals`. | `arrival_time_s.f64le`. Meaningful when status is `ARRIVAL_MODELED` (3). | seconds since `start_utc`, f64 | `(H, W)` Rust order; the quantile utility takes the caller's order | Flip to application order, then `minutes_since_t_ref`. | Modeled times finite and inside `[0, horizon]`. | Not-reached times are ignored. They are not zeros and not `inf`. | Rust encoding confirmed. `inf = never` in the spec is a different claim from horizon censoring. Awaiting decision before those are treated as the same. |
| Member arrival status | Not a landed per-cell code. `ForecastArrays` uses 0, NaN, and finite `t_q`. | `0` invalid, `1` not reached, `2` initial unknown, `3` modeled. | u8 | `(H, W)` | Status is flipped with the same row rule. Codes are not remapped. | Unknown codes error. | Failed members are records with `failed=True` and no arrays. | Rust codes confirmed. Application encoding is specified, not landed. |
| Quantiles | Spec levels `(0.1, 0.3, 0.5, 0.7, 0.9)`. `t_q` is `(5, H, W)` float32 minutes after issue. NaN means beyond the horizon. Weighted CDF in `02` §9. | Ensemble q10/q50/q90 are nearest-rank **conditional on modeled arrival**. | level in `(0, 1]`; research times are float32 minutes since `t_ref` | `(Q, …)` matching the member shape | `F(t) = count(T_k <= t) / N_usable` over usable members. `Q(q) = inf {t : F(t) >= q}`. Censored members never satisfy `T_k <= t`. Rank is `ceil(q * N)` using multiples of `1e-6`. | Levels required, in `(0, 1]`, unique, multiples of `1e-6`. Shapes must match. | Empty usable sample errors. Conditional Rust quantiles are not inverted to recover members. | Research formula confirmed for unweighted member outcomes. Weighted application CDF and the uint8 `arrival_q.bin` sentinel are specified, not landed, and are not emitted. |
| Censored arrivals | Spec NaN in `t_q`, and `255` in `arrival_q.bin` (`00` §4.2). `region_arrivals` uses `inf` = never. | `ARRIVAL_NOT_REACHED` through the modeled horizon. Ensemble `p_no_arrival` is the fraction of completed members in that state. | research status `Q_CENSORED = 1`; time NaN | same as quantiles | Right-censored at `horizon_s`. The result does not say the cell never burns. | Horizon must be finite and positive. | Not encoded as 255. That sentinel belongs to the unlanded file contract. | Awaiting decision before 255 or `inf` is written. Censoring semantics are confirmed for the research utility. |
| Partial ensembles | Spec `n_members` is the ensemble size. No landed partial-member rule. | `allow_partial`. Frequencies use completed members. `partial` is true when `n_complete != n_requested`. Invalid cells stay missing, not zero. | counts, int | scalar plus per-cell coverage | Caller passes `reject_incomplete` or `aggregate_usable`. | `reject_incomplete` errors if any requested member is missing or failed. `aggregate_usable` still errors on zero usable members. | Coverage is `usable / requested`. It is not replaced with 0. Per-cell invalid coverage yields `Q_INVALID`, not a quantile from the other members. | Awaiting the production policy. Both research policies are explicit arguments with no default. |
| Weights | Spec `ForecastArrays.weights` float64, normalized. CDF is weighted (`02` §9). | Ensemble members are equally counted scenario frequencies. `operational_probability_claim` is false. | n/a | `(N,)` if the caller has them | Not applied. | Passing `weights` raises `unsupported_mapping`. | n/a | Awaiting decision. This utility is unweighted scenario frequency, not a calibrated probability. |
| Checkpoint metadata | Spec `save_state` is `np.savez_compressed` of `T`, weights, and `MODEL_VERSION` (`02` §10). Not landed. | `meta.json` schema 2: model, solver, `t`, manifest hash, `run_manifest.start_utc`, state hashes, input hashes. | mixed | checkpoint directory | `bind_checkpoint_reference` records `t_ref_ms` outside the file. `assert_reference_preserved` checks `start_utc`, model, schema, and `t_ref`. | Schema 2 and model `surface_v1` required. | The checkpoint file is not edited. A changed origin is an error. | Rust checkpoint contents confirmed. Python `savez` parity is awaiting decision. |
| Model version | Spec `Cycle.model_version` and `MODEL_VERSION = "mtt-pf-1"` in `02` §0. Not landed. | `surface_v1` / `level_set_lf_euler_v1`. Run schema 2. Input schema 1. | text | scalar | Compared, not translated. | A published run with another model or solver is `malformed_output`. | n/a | Awaiting decision before either name is treated as the application's `model_version`. |
| Canopy and building fraction | Optional `Landscape` fields in the spec. | No inputs. | n/a | n/a | Not written. | n/a | Not accepted. | Awaiting decision. |
| Ensemble frequencies | Spec probabilities are weighted member fractions. | `probability_*.f64le` and `p_no_arrival.f64le`, labeled `raw_scenario_frequency`. | fraction, f64 | `(H, W)` Rust order | Not converted into `t_q`. | n/a | NaN where `valid.u8 = 0`. | Confirmed Rust meaning. Not an application probability. |

## Time arithmetic

For an arrival `s` seconds after the Rust origin:

```text
t_since_t_ref_min = float32( (t_start_ms - t_ref_ms) / 60000 + s / 60 )
```

The subtraction uses Python integers. Epoch milliseconds are never cast to float32. The only rounding is the final cast.

`minutes_after_issue` subtracts `(t_issue_ms - t_ref_ms) / 60000` in float64 before that cast. It is not the default. `t_issue`, `t_ref`, and `t_start` are stored separately.

Float32 round-trip tolerance is 2 ulps of the stored minute value (`FLOAT32_ROUNDTRIP_ULPS`). A value that has already been stored as float32 converts back to the same float32. Results with absolute value above `1_000_000` minutes are rejected.

Availability:

```text
t_available_ms <= t_issue_ms
```

`t_valid_ms` and `t_observed_ms` are not substitutes for `t_available_ms`. Future-valid weather is usable when it was available at or before the issue. Rust will still reject the bundle when `available_utc > start_utc`. This layer reports that incompatibility and does not rewrite the timestamp.

Rust correction remains immediate-only: observed time, available time, and correction time must match. That check stays inside Rust.

## Arrival distribution

Usable members are completed outcomes. Failed and omitted members reduce coverage. They do not arrive at time zero.

Not-reached members are inside `N_usable` and add no mass at any finite time. With four usable outcomes `60 s`, `120 s`, not reached, not reached:

- `Q(0.25) = 60 s`
- `Q(0.50) = 120 s`
- `Q(0.75)` is censored at the horizon

Those quantiles are not computed from the two finite arrivals alone.

Initially burned cells (`ARRIVAL_INITIAL_UNKNOWN`) reject the cell's quantiles until a production interpretation exists. Invalid cells, including mixed invalid coverage, stay missing. Research status codes are `finite`, `censored`, `invalid`, and `initial_unknown`. Times for the last three are NaN.

The result label is `unweighted_scenario_frequency_uncalibrated`. `calibrated_probability` is false.

`arrival_q.bin` values `0`, `1..254`, and `255` are specified in `00` §4.2 and are not written. The landed encoder does not exist, so this layer does not invent the file.

## Subprocess bridge

`bridge.simulate` takes an executable path and an argument list. `shell` is false. Each call uses a new scratch directory.

Before launch it checks that the manifest exists, the output path is free, the grid is at least 3×3, and the CRS is not geographic. Rust still performs full validation.

Exit classes:

| Class | How it is recognized |
|---|---|
| `validation_failure` | Rust codes such as `SchemaMismatch`, `InvalidInitialFront`, `MissingCoverage`, or exit 2 |
| `numerical_failure` | `TimeStepTooSmall`, `NumericalOverflow`, or exit 3 |
| `memory_budget` | `MemoryBudgetExceeded` or exit 4 |
| `cancellation` | `Cancelled` or exit 130 |
| `timeout` | the parent timer, after SIGTERM and, if needed, SIGKILL |
| `malformed_output` | exit 0 with a bad schema, or a non-zero exit that is not a Rust error object |

A verified return checks run schema 2, model `surface_v1`, solver `level_set_lf_euler_v1`, grid shape, arrival lengths, `manifest.sha256`, and every `input_artifacts` hash and byte length.

Retention: the published directory is kept only after those checks. On failure the published path is removed if this call created it, and staging or unverified bytes are moved into the scratch directory, which is kept. On success the scratch directory is deleted. Rust's own rule still applies: an existing output or staging path is refused.

A parent timeout does not write a resumable checkpoint. Solver cancellation and a wall-clock kill are different events.

Build `fire-cli` once in release mode and pass that path. Do not rebuild per member.

## Synthetic ensemble

The compatibility test builds several deterministic bundles and runs `simulate` on each. That is the member-level path. `ensemble` is invoked only to confirm that its public directory still has conditional quantiles and does not contain `arrival_time_s` or `arrival_status`. Those conditional quantiles are not fed back into the unconditional utility.

Production use needs a member-arrival export (time and status per completed member, plus failed-member identity). Adding that export is a schema change and is out of scope.

## Unresolved decisions

1. Whether Rust becomes an application backend.
2. Whether it replaces the planned Python MTT model or runs beside it.
3. The production backend selector and its default.
4. Any edit to frozen application contracts, `arrival_q.bin`, or hub tables.
5. The authoritative state format: planned `np.savez_compressed` physics state, or Rust checkpoint schema 2.
6. FBFM40 identifiers, including whether NB91 is burnable urban fuel. The Rust catalog has no urban model.
7. The 10 m wind to midflame reduction, wind-direction bias, and whether `WAF` is in the adapter or the solver.
8. Fuel moisture from relative humidity or from the planned 1-h percent scenarios, versus explicit fractions.
9. Whether `ARRIVAL_INITIAL_UNKNOWN` is burned-at-issue (`t_q = 0`), excluded, or something else.
10. Weighted CDF versus unweighted scenario frequency, and any calibration.
11. The production partial-member policy. Research callers must pass one.
12. Writing censored or missing quantiles as NaN, `inf`, or the uint8 sentinel `255`.
13. Whether "not reached by the horizon" may be reported as "never".
14. How an application issue time that is not the Rust `start_utc` should satisfy `available_utc <= start_utc`.
15. Slope and aspect supplied by the application versus the gradient Rust derives from elevation.
16. A per-member arrival export from `ensemble`.
17. Five-minute assimilating cycles, delayed observations, particle filtering, and Python `save_state` / `load_state` parity.
18. Fractional-second forecast clocks. Rust v1 cannot store them, and this layer will not truncate them.

Independent utilities in this directory do not choose these policies. Flags such as `components_are_midflame` and `passthrough_catalog_id` only describe the arrays the caller already has.
