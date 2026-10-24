# Model audit and integration verification

Audit date: 2026-10-24. Imported source: `cursor/surface-v1-physics-engine` at `99b7f459bfbb1968d19d479152adcf80b33e8bfe`. Integration base: `main` at `2277dfd6fd069d0b9bc0493f6f918fb80c2ad988`. This report covers the implementation and executable tests, not operational wildfire forecast skill.

## What is actually implemented

| Previously discussed method | Rust coverage | Verification / disposition |
|---|---|---|
| Extended Rothermel surface rate of spread, Behave-style weighting/reaction | Implemented in `fire-behavior` | Independent USFS Behave cases for FM1/FM2/FM4/FM10 and GR1; explicit convention differences below |
| Dynamic herbaceous curing | Implemented for catalog GR1 | Mass conservation and exact endpoints; comparison to upstream distinguishes rounded curing coefficients |
| Terrain-plane wind and slope, projected elliptical spread | Implemented | Aligned-slope independent local rates, rotation covariance, projected Hamiltonian homogeneity and CFL derivative bounds |
| LF/Euler level-set fire-front propagation | Implemented in `fire-propagation` | Analytic circle and ellipse at 20/10/5 m, stationary front, wind/schedule events, barriers, planar arrival interpolation and irreversibility |
| Independent scenario uncertainty ensemble | Implemented, uncalibrated | Deterministic member seeds, worker-count parity, missing coverage and conditional quantile semantics, real binary exports, conservative memory reservations |
| Perimeter state replacement and restart | Implemented with limited semantics | CLI and library regression tests; immutable checkpoints with verified input/state hashes; fixed parent time origin |
| Python numba MTT, pyretechnics, particle filter, Liu–West jitter, likelihood registry | Not implemented by this Rust workspace | Remain the canonical demo plan in `spec/02_physics.md`; this import does not claim equivalence |
| RGB-to-NDVI/NDRE Pix2Pix study | Not implemented | Vegetation observation research; no image translation model, weights, paired training set or validated fuel conversion exists here |
| ANN/SVM wildfire occurrence study | Not implemented | Occurrence classification is a different output from arrival-time propagation |
| MODIS post-fire phenology study | Not implemented | Recovery/phenology research does not supply a minute-scale spread solver |
| Deep-learning risk review and ML/DL spread review | Literature references only | Reviews describe multiple methods; they are not individual architectures implemented by this engine |
| AllConvNet burn prediction (Bergado et al., *Predicting wildfire burns from big geodata using deep learning*) | Not implemented | No convolutional architecture, training pipeline, checkpoint, learned predictor or held-out daily burn evaluation |
| Composite Bézier fire-front representation | Not implemented | Rust evolves a raster level set, rather than the paper's differentiable parametric boundary |
| PhysFire-WM | Not implemented | No learned world model, thermal state, training loss, dataset or inference artifact; a front-only Rothermel solver is not that model |
| Sonoma heterogeneous-landscape MIP/planning study | Not implemented | No mixed-integer solver or intervention optimization |
| Crown fire, spotting, plume/atmospheric coupling, suppression and structural burning | Outside surface_v1 | Must not be inferred from passing surface tests |

The nine supplied papers were rechecked against their extracted text when making this coverage table. Learned models need separate requirements, data, training, validation and versioned weights before they can be tested as implemented models. Adding an empty inference wrapper would not satisfy that requirement. The existing spatial-intersection/Aegis proposal also remains separate from this physics component.

## Scientific checks and conventions

The local spread equation is

\[
R_0=I_R\xi/(\rho_b\epsilon Q_{ig}),\qquad
R_h=R_0(1+\phi_{eff}).
\]

The implementation computes the mixed-bed heat sink directly with life/particle surface-area weighting. Internal empirical equations use their prescribed ft/min and Btu units, then convert spread to m/s. The original model is described in [Rothermel (1972), USDA INT-115](https://research.fs.usda.gov/treesearch/32533); subsequent weighting, reaction-velocity and curing conventions need the [USDA comprehensive explanation](https://research.fs.usda.gov/treesearch/55928). Passing Rothermel tests alone does not validate a neural model or the full fire-growth solution.

The independent executable reference uses [USFS Firelab Behave](https://github.com/firelab/behave/tree/7e3d7a44b7e7eed271ec3029ec34f0bd66466532), pinned commit `7e3d7a44b7e7eed271ec3029ec34f0bd66466532`. `tools/oracle/behave_reference.cpp` invokes its public API; `regenerate.py` builds upstream and runs its tests before generating fixtures. No Rust values are used as expected values. See `tools/oracle/provenance.json` for compiler and upstream test summary.

The matrix records **42 cases**: FM1, FM2, FM4, FM10 at 0.60 live-herb moisture and GR1 at 0.30, 1.20, 1.21; each at 0, 1, 3 m/s midflame wind and flat/30% aligned slope. In **36 matching-convention cases**, Rust head rate, reaction intensity, heat sink and packing ratio agree within `1e-9 × max(|reference|, 1e-6)`.

Two differences are deliberate and separately tested:

1. **Wind cap.** This pinned Behave implementation disables its wind cap by default. `surface_v1` defaults to `U_eff ≤ 0.9 I_R` in empirical units. Matching oracle comparisons use Rust's explicit `WindLimitPolicy::None`. A separate test verifies the capped GR1 result, lower head rate, and preserved direction. Changing only a tolerance would conceal a modeling decision.
2. **Curing.** Upstream uses `1.333 − 1.11 M_lh` over its transition interval, leaving 0.001 cured fraction at exactly 1.20. The project uses `clamp((1.20 − M_lh)/0.90, 0, 1)`, giving exactly zero. Six GR1/1.20 cases explicitly verify a roughly 0.37% lower Rust head rate; they are excluded from strict equality and retained as a documented source-variant test. At 1.21, both use zero transferred herbaceous load and the strict comparisons pass. Intermediate curing values likewise should not be advertised as bit-for-bit Behave reproduction.

For the projected ellipse,

\[
H(p)=d\cdot p+\sqrt{(A\cdot p)^2+(B\cdot p)^2},\quad
\phi^{n+1}=\min(\phi^n,\phi^n-\Delta t\widehat H_{LF}),
\]

with global derivative bounds

\[
\alpha_x=|d_x|+\sqrt{A_x^2+B_x^2},\quad
\alpha_y=|d_y|+\sqrt{A_y^2+B_y^2},\quad
\Delta t\le\frac{CFL}{\alpha_x/\Delta x+\alpha_y/\Delta y}.
\]

These are project numerical conventions, not claims to reproduce all of FARSITE. Arrival is interpolated at a positive-to-nonpositive phi crossing:

\[
t_a=t_n+\Delta t\frac{\phi^n}{\phi^n-\phi^{n+1}}.
\]

An affine front has zero LF diffusion, giving an exact arrival check. A separate test uses independent Behave speeds on either side of a real weather-frame event and checks the integrated front displacement.

## Numerical and workflow evidence

Local checks: **47 Rust tests pass in debug and release**, strict clippy and formatting pass; the independent upstream Behave suite passes **215/215**. Local release geometry results on Apple Silicon/macOS with Rust 1.99.1:

| Resolution | Circle max radial error | Circle relative burned-area error | Ellipse max support error |
|---|---:|---:|---:|
| 20 m | 6.591123 m | 5.68596% | 7.436499 m |
| 10 m | 3.096366 m | 2.01819% | 3.539029 m |
| 5 m | 1.507950 m | 1.14491% | 1.725594 m |

Each finer grid improves the measured boundary error. These homogeneous analytic cases measure discretization error; they do not measure skill on observed incidents. Circle initialization is a 150 m disk with 0.1 m/s isotropic growth for 1200 s. The ellipse test starts with a homothetic ellipse, then evolves it another 600 s and compares support functions. Barrier tests include invalid cells, nonburnable cells, a traversable gap, and an **actual extinct-fuel wall**; the old test named zero-speed wall only exercised invalid cells.

Additional executable regressions cover:

- whole-second UTC validation, unavailable weather rejection, finite configuration and local inputs, environment metadata/order/coverage consistency, and backward/nonfinite advance targets;
- checkpoint at zero elapsed time, continuation parity, changed input hashes, corrupted checkpoint arrays, and refusing to overwrite completed checkpoints;
- correction changing the forecast rather than restarting the original input, keeping parent time origin, rejecting nonfinite corrected phi, and exporting observation provenance;
- real ensemble arrays, recorded artifact hashes, invalid/mixed member coverage, conditional quantile denominators, partial labeling, tiny-budget rejection, and worker-count equality;
- metric cell area, mismatched/empty validity masks, grid/time alignment, Brier/arrival array shapes, and rejection of requested but unimplemented evaluation methods;
- immutable completed run publication.

A short Criterion kernel benchmark measured the 128×128 LF step at 137.97–140.05 microseconds (10 samples, 1 s measurement). It uses a uniform phi field and is only a kernel measurement, not a complete-forecast or operational latency result.

The validation command set is in the README. CI repeats formatting, strict linting, debug and release tests on Linux/macOS. Local checks do not imply those hosted CI jobs have already succeeded. The committed JSON verification summary records the executed commands and test counts.

## Defects fixed before import

The original `correct` command ignored `apply_correction`'s returned state and simply reran the unchanged bundle. It now loads an explicit parent input, resolves updated paths relative to the update file, continues the corrected state and preserves its time origin. Correction provenance includes the observation manifest, corrected-phi hash and parent checkpoint metadata hash.

Checkpoint loading previously ignored recorded input artifact hashes. Schema 2 verifies those hashes plus all four stored state arrays and validates time/event indices and basic array values. Zero-step diagnostics now serialize with a finite zero minimum step. Existing output/checkpoint directories are rejected instead of deleted.

Ensemble CLI output previously contained metadata only. It now publishes frequencies, coverage, no-arrival and conditional quantile arrays with hashes. Invalid cells previously acquired numeric zero risk; they now remain missing. Member inputs are cloned inside bounded workers rather than cloning every member upfront; memory reservations include retained arrival results and aggregate arrays. This is a conservative estimate, not a proven hard RSS cap or a full production-scale performance gate.

Evaluation previously assumed 1 m cells and could index a short validity mask. It now reads grid metadata, checks lengths and alignment, intersects prediction validity, uses metric cell area and rejects unsupported baseline/bootstrap/release requests instead of claiming they were evaluated. Build metadata collection moved from the CLI build script to the crate that actually embeds those values.

## Integration decision and remaining gates

The Rust source is relocated under `physics/surface_v1/` on a normal descendant of `main`; no unrelated-history merge is used. The root ignore rules are extended, not replaced. Shared root fixture files, frozen specifications, the web UI and existing code remain intact. There is no backend selector enabled by this import. `main` currently has the demo specifications and UI but no `engine/` implementation; the pending C0 branch is not silently included in this task. Consequently a Python fixture-demo acceptance run is not yet available on this base.

Before Rust can become a demo backend, obtain the documented runtime/physics decision and implement an adapter against the **landed** `HazardModel` API. Required work includes north/south row reversal; exact UTM/90 m alignment; seconds-to-float32-minutes conversion with a fixed `t_ref`; unconditional q05/q25/q50/q75/q95 arrival CDF including no-arrival mass; defined initial-unknown semantics; RNG/model-version differences; serialization parity; observation cutoffs and late-data replay; and full fixture-demo acceptance tests. Research q10/q50/q90 conditional quantiles cannot fill that contract.

Also outstanding: calibrated priors/posteriors and particle filtering, independent observed-incident replay with baselines and incident-level uncertainty intervals, held-out historical spread skill, large-grid/128-member memory and runtime gates, cancellation/deadline handling, and end-to-end data freshness/visualization integration. Learned components require their own training and evaluation evidence. None of those capabilities is implied by the current tests. The scientific scope remains a quasi-steady surface-fire research engine.
