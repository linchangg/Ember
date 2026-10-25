# Rust HazardModel backend: implementation and integration guide

## Decision and scope

On 2026-10-24 the project owner explicitly approved using Rust as the `HazardModel`
backend and requested a Python adapter, observation assimilation, and fixture verification.
This document records that decision. It supersedes the **implementation choice** of
Python/numba MTT in D3 and `spec/02_physics.md`, for this backend only. It preserves
the public API in `spec/00_contracts.md` §6, the schema-v2 wire contract, quantiles,
time conventions, and ownership of Cody's runtime and Linda's frontend.

The implemented model is `rust-levelset-pf-1`: Rothermel surface behavior, an elliptical
Hamiltonian propagated with the Rust LF/Euler level-set solver, and Python posterior
inference. It is not an MTT solver. NB91 spread is an explicit heuristic, not a validated
structure-fire model. There is no deep-learning model, trained weight file, crown-fire
model, or spotting model in this implementation. Use `spotting=False`; the backend
rejects a configuration requesting an unsupported feature instead of silently ignoring it.

Scientific acceptance and integration acceptance are separate. Passing serialization,
geometry, deterministic replay, and fixture tests does not establish historical skill.
The synthetic parameter-recovery, report-effect and latency gates have executable
evaluations; failures are recorded rather than hidden. See `RUST_HAZARD_NEXT_TASKS.md`.

## Build from a clean checkout

Run these commands at the repository root, using Python 3.12, Node 22+, Rust 1.99.1,
and `uv` 0.13.5. The first dependency installation requires network access. Forecasts
and the fixture need no external services, account, credentials, or downloaded fire data.

```sh
python3.12 -m pip install uv==0.13.5
rustup toolchain install 1.99.1 --profile minimal --component rustfmt --component clippy
uv sync --project engine --locked
cargo build --manifest-path physics/surface_v1/Cargo.toml -p fire-bridge --release --locked
uv run --project engine python -m emberline.hazard.compatibility
uv run --project engine pytest engine/tests -q
cargo test --manifest-path physics/surface_v1/Cargo.toml --workspace --locked
cargo test --manifest-path physics/surface_v1/Cargo.toml --workspace --release --locked
cargo clippy --manifest-path physics/surface_v1/Cargo.toml --workspace --all-targets --locked -- -D warnings
```

No model method builds Rust or downloads anything. Set `EMBERLINE_RUST_BRIDGE` to an
absolute executable path to override the default nested release build. `BackendOptions`
controls process timeout, worker count and the native memory budget; these are deployment
limits, not new fields in the frozen `ModelConfig` contract. The source checkout is
currently required: source fingerprints and fuel catalogs are read from it. A standalone
Python wheel without the Rust workspace is not a supported deployment artifact yet.

## Python entry point and exact boundary

```python
from emberline.hazard.api import ModelConfig
from emberline.hazard.factory import create_hazard_model
from emberline.hazard.bridge import BackendOptions

model = create_hazard_model(options=BackendOptions(workers=2, timeout_s=120))
cfg = ModelConfig(n_members=16, spotting=False, filter="pf")
model.init(land, weather, cfg, ign)  # dataclasses defined in hazard/api.py
forecast = model.step(t_now_min=140.0, new_obs=due_observations)
zone_arrivals = model.region_arrivals(zone_index, number_of_zones)
checkpoint_bytes = model.save_state()
restored = create_hazard_model()
restored.load_state(checkpoint_bytes)  # no separate init is necessary
```

Use actual `Landscape`, `Weather`, and `Ignition` objects from `hazard/api.py`.
`zone_index` is `(H,W)` integer region IDs, with `-1` excluded. Regions must be
pre-rasterized on exactly the same grid. Empty regions return `+inf` for every member.
Before ignition the runtime must publish its established pre-ignition state; do not
call `step()` before ignition or twice at the same issue time. The fixture demonstrates
three pre-ignition cycles and 36 strictly increasing forecast cycles.

| Quantity | Python boundary | Rust bridge | Frontend files |
|---|---|---|---|
| Coordinates | Projected conformal metric grid, usually EPSG:32611 | Metric east/north | WGS84 `[lon,lat]` |
| Row order | Row 0 north; origin upper left | Row 0 south; origin lower left | Row 0 north |
| Time | Minutes since `t_ref_s` | Seconds since same origin | Epoch milliseconds UTC |
| Arrivals | Member `T`: float32 absolute minutes, `+inf` never | f64 little-endian seconds | Quantile planes of uint8 |
| Forecast quantiles | `.1,.3,.5,.7,.9`, **relative lead minutes** | Python derives posterior CDF | Planes in that exact order |
| Unreached | `NaN` in `t_q`, retained mass in member CDF | `+inf` | 255 |
| Already burned | Quantile lead 0 | Arrival at/before issue | 0 |
| Positive lead | Rounded upward to 2 minutes | Continuous interpolated arrival | `ceil(lead_min/2)`, 1–254 |

North/south conversion is an explicit contiguous copy on axis `-2`, not a transpose.
Slopes are rise/run. Aspect is the **downslope** compass bearing from true north;
the adapter constructs the upslope terrain gradient. Earth-relative 10 m wind is
converted to the local projected true-east/north basis, then multiplied by WAF 0.4,
or 0.2 where canopy cover exceeds 0.5. Native particle wind bias rotates clockwise.
Wire coordinate transforms always use longitude first. Tests use asymmetric arrays
and off-central-meridian grids to catch flips and UTM convergence mistakes.

## Native execution

`fire-bridge` is a separate safe-Rust process with a versioned JSON request and binary
arrays in a private temporary directory. This keeps compiler/ABI details out of the
Python API and avoids requiring a Python extension build on every contributor's machine.
It does incur I/O, copies, and process startup; it is not a zero-copy binding.

1. Initialization performs `fire-bridge --describe`, checking protocol 1, model version,
   and an SHA-256 fingerprint of propagation-related Rust sources and the Cargo lock.
2. Python validates shapes, units, fuel IDs, clock times, and finite inputs. It writes
   north-to-south landscape, projected wind, terrain gradients and particle parameters.
3. Rust loads the bundle once, constructs a bounded Rayon pool and solves members in
   deterministic input order. The memory guard accounts for the arrival matrix and
   worker scratch, including weather fields. It is a conservative native allocation
   estimate, not an OS-enforced process-tree RSS limit.
4. Rust returns dimensions, arrival-file SHA-256, model/build identity, solver steps
   and quality flags. Python verifies size, checksum and values before committing state.
5. Failure, timeout, malformed response or checksum mismatch restores the prior posterior,
   pending weather, anchors, evidence ledger, and issue time. Caller can retry safely.

All forty Scott/Burgan models are exported from the pinned USFS Behave catalog;
the original thirteen-model catalog remains intact. Eighty independent upstream SB40
reference cases supplement the existing 42-case oracle. Reproduction instructions
and the pinned commit are in `physics/surface_v1/tools/catalog/README.md`.

The current implementation replays each member from ignition or its reinit seed to
`issue + horizon` on every cycle. Late anchors are inserted at their **observation time**
and propagation is replayed forward, rather than being stamped at their arrival time.
This is deliberately simple and testable, but the full-grid replay is too costly for
the original 0.5-second target. The exact per-frame behavior cache reuses identical
fuel/moisture/wind/slope calculations; it does not approximate their values.

## Equations and assimilation sequence

For each cell, surface behavior uses Rothermel's formulation in the tested Rust module:

`R = I_R * xi * (1 + phi_w + phi_s) / (rho_b * epsilon * Q_ig)`.

Catalog calculations retain the published imperial-unit convention internally and
convert spread to SI before propagation. Runtime ROS scaling multiplies the ellipse's
spread vectors, not elapsed time. For ellipse drift `d` and axes `a,b`, the Hamiltonian is

`H(p) = d dot p + sqrt((a dot p)^2 + (b dot p)^2)`;
`d(phi)/dt + H(grad(phi)) = 0`.

The LF spatial flux with explicit Euler obeys
`dt <= 0.4 / (alpha_x/dx + alpha_y/dy)`, capped at 30 seconds and split exactly at
weather changes, observation anchors and the requested end time. Arrival times use
the solver's interpolated sign crossing. Domain edges and nonburnable cells follow
the existing tested propagation policy; reaching an edge does not model fire outside it.

Particle parameters are `[log(ROS multiplier), log(wind multiplier), wind angle rad,
log(dead 1h moisture), log(urban ROS m/min)]`. Priors are explicit in `_draw_prior()`.
The initial ignition disk is a resolved numerical seed of radius `1.25 * cell_m`;
`Ignition.radius_m` defines member-center uncertainty, not a known observed burned area.
NB91 uses a wind-aligned heuristic ellipse with sampled head rate; it does not use
Rothermel fuel loads or claim building-to-building physical validation.

At each issue, sorted new observations undergo the following steps:

1. Reject future availability or observation-after-availability. Never double-count an
   ID; reject a changed payload reusing an ID. Record unused reasons for zero trust,
   observations before ignition/outside retained history/outside burnable domain,
   and disabled assimilation. Clear satellite pixels enter physics even though only
   hot detections are published for display.
2. Predict member arrivals, then evaluate evidence at `observed_sim_ms`, including
   delayed evidence. Active burning means `0 <= t_obs - T <= tau_fuel`; residence is
   15 min GR, 45 min GS/SH, 90 min TU/TL/SB and 180 min NB91.
3. Blur satellite footprint weights with sigma 1000 m GOES, 200 m VIIRS and 30 m
   thermal. For active weighted area `A` in hectares:
   `p_detect = p_max * (1 - exp(-A/A0))`, with GOES `(p_max,A0)=(.85,1)` and
   VIIRS/thermal `(.95,.05)`. Add a .002 false-detection probability. Clear evidence
   uses the complementary probability. Ground reports use maximum spatial support
   `q=max(exp(-distance²/(2 radius²)) * 1[T <= t_obs+10])` within `2*radius`.
   Hot-report likelihood is `.05+.95*q`, cold-report likelihood `1-q`.
   Cameras use geodesic bearing uncertainty and active burning within their field of view.
4. Every likelihood uses the trust mixture `L=(1-r)+r*L_sensor`. In log space,
   `w'_j proportional w_j * exp(alpha * sum(log L_ij))` and
   `ESS=1/sum(w_j²)`. Twenty bisections choose the largest alpha preserving the ESS floor.
5. Strong hot evidence with posterior support below .1 can reanchor: ground reports,
   VIIRS confidence >=.6, or GOES with an actual preceding strong hot scan of that pixel.
   Low-trust ground reports anchor a sampled fraction `r` of members. They do not
   become certain observations. A persistent class without a preceding scan is insufficient.
6. Reinitialize the worst configured fraction when all members poorly explain the
   evidence. `filter="reinit"` supplies the explicit baseline: monotone observed mask,
   one-cell erosion/dilation, signed-distance seed, interior arrivals `max(ignition,now-60)`
   and boundary arrivals `now`. A saturated domain raises an explicit enlarge-grid error.
7. Systematic resampling and Liu–West jitter update particle diversity when ESS falls
   below the floor **or tempering is partial**. This explicitly corrects the strict-ESS
   comparison in the old pseudocode: bisection leaves ESS just above the floor, so
   using only `< floor` caused alpha to stall near zero forever. Circular angles are
   unwrapped locally; dead moisture remains clipped to .02–.12. Resampling is diagnosed.
8. Reforecast if anchors, seeds or particle parameters changed. Return the unconditional
   weighted CDF: `F_cell(t)=sum_j w_j * 1[T_j <= t]`. Never renormalize away members
   with no arrival. `p_burned_now=F_cell(issue)`. Region arrivals are per-member cell minima.

RNG is `default_rng([seed,cycle_index])`, independent of branch labels. New forcing is
activated at the next step without rewriting earlier forcing. Because `Weather` has
no availability field, external forcing selection must enforce archived release times.
`hazard/integration.py:weather_from_hrrr()` does this for Cody's current NetCDF format,
checks row/CRS/wind conventions and returns a provenance audit. It holds the nearest
selected weather band outside its valid range; it does not invent unavailable bands.

## Checkpoints and future updates

NPZ checkpoints are self-contained, compressed, pickle-free and array-checksummed.
They contain landscape, all applied/pending weather, member parameters and weights,
float32 arrival fields, observation history and evidence hashes, anchors/reinit states,
configuration, ignition and last issue. A decompressed-size check bounds ingestion.
Loading creates a fresh validated model. The next forecast must be bit-identical.

Checkpoints bind to model/protocol version, physics-source fingerprint, Python model
fingerprint, fuel catalog and platform/dependency versions. An unrelated Cody documentation
or UI commit does not invalidate them. A changed numerical implementation does: rebuild
and restart or write an explicit migration. Cross-platform bit-identical resumes are
not promised. Binary-file hash is recorded for provenance; numerical source identity
is the compatibility gate, allowing equivalent rebuilds after unrelated Git commits.

`hazard.compatibility` compares the public dataclass/protocol AST with the current
shared spec, rather than relying on a copied informal interface. CI watches the
shared spec and TypeScript contracts as well as both engines. Coordinate, enum, field,
quantile, time or binary-layout changes require matched tests and an explicit migration.

## Complete offline fixture and inspection

```sh
uv run --project engine python -m emberline.scripts.make_fixture
uv run --project engine python -m emberline.hazard.demo --out /tmp/emberline-rust-demo --members 16
node tools/check_hazard_frontend.mjs /tmp/emberline-rust-demo
python3 -m http.server 8787 --bind 127.0.0.1 --directory /tmp/emberline-rust-demo
```

Open `http://127.0.0.1:8787/demo.html`. In another terminal:

```sh
node tools/check_hazard_frontend.mjs http://127.0.0.1:8787/
uv run --project engine python -m emberline.hazard.evaluate_assimilation --out /tmp/assimilation.json --members 32
uv run --project engine python -m emberline.hazard.benchmark --out /tmp/latency.json
```

Choose fresh output paths; the tools refuse accidental overwrites. The viewer is a
small contract-verification tool, not Linda's dashboard. Cycle/lead controls decode
the actual generated binary and show posterior diagnostics. The full fixture uses
120×120 cells at 255 m, 16 members and six-hour forecasts. It is a development profile,
not evidence that the 340×340/90 m/128-member latency target is met.

The demo generates all 36 forecast cycles and retains the canonical three pre-ignition
cycles. Synthetic fuels, winds, detections, reports, residents and terrain are labeled.
It adds two synthetic VIIRS passes and a delayed ground report, checks a fresh-object
checkpoint at cycle 12 against the next forecast, and writes schema-v2 bundles, binary
rasters, GeoJSON, diagnostics, `verification.json` and artifact hashes. It calls Cody's
current `runtime.summarize.write_assets`, `risk.zone_risks` and `risk.alerts.evaluate`;
it does not maintain a second competing alert policy. No notification is sent.

The Node consumer checks every cycle, no-future observation timestamps, all five
quantile planes, monotonic encodings, geometry IDs, probabilities, ranks and alert
length/replay labeling through both filesystem and HTTP access. Additional Python
integration tests pass Rust output through Cody's real cache, risk, alert and asset
functions. These checks prepare Linda's data connection; they do not claim her
interactive dashboard or a deployed SpacetimeDB replay has been tested end to end.

## Evidence and limits

Machine-readable results belong in `docs/evidence/rust-hazard/`. Exact measured
results, commands and remaining acceptance gaps are recorded in the verification
report there. The synthetic evaluator emits gate booleans even on failure; it must
not be interpreted as a historical test. Live hub/API/Claude and historical network
tests remain opt-in because they require services or source data.

Numerical references: [pinned USFS Behave](https://github.com/firelab/behave/tree/7e3d7a44b7e7eed271ec3029ec34f0bd66466532),
[SciPy exact Euclidean distance transform](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.distance_transform_edt.html),
[SciPy Gaussian filtering](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.gaussian_filter.html),
[pyproj transformations](https://pyproj4.github.io/pyproj/stable/api/transformer.html),
and [pyproj geodesics](https://pyproj4.github.io/pyproj/stable/api/geod.html).
