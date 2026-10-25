# Engine Build Plan

> **Repository status:** This is a proposed Rust research-engine build plan. The current Emberline demo remains governed by [`SPEC.md`](SPEC.md), [`spec/00_contracts.md`](spec/00_contracts.md), and [`spec/02_physics.md`](spec/02_physics.md), which specify Python/Numba MTT and different time, grid, RNG, and output contracts. This proposal does not replace those decisions or change the shared contracts. Adopting it requires a separate team decision and integration design.

Version 3 • October 24, 2026 • Scope: physics engine and its integration contracts.

## How to use this document

Implement sections 1–16 in order. Sections 17–20 define the task backlog, delivery gates, and references. All coordinates, units, equations, boundary rules, defaults, and file formats below are normative for `surface_v1`. Where a rule is an engineering approximation rather than a published physical law, it is labeled **project convention**. Do not describe this implementation as a complete FARSITE reproduction.

This is an implementable research engine specification, not evidence that the resulting model is operationally validated. The included arithmetic fixtures verify this document's equations. Independent reference comparisons and historical validation remain required deliverables, with exact procedures below.

Companions:

- `docs/spec/fuel_models.csv`: initial supported static fuel catalog, plus a dynamic-model test entry.
- `docs/spec/reference_math.py`: executable arithmetic/analytic reference for selected equations; Python standard library only.
- `fixtures/spec/reference_values.json`: generated intermediate values and analytic cases.
- `docs/BUILD_PLAN_PREVIOUS.md`: preceding high-level physics plan.
- `SYSTEM_REFERENCE_PLAN.md`: broader architecture, outside this team's implementation scope.

## 1. Decisions and ownership

### 1.1 Selected model

Build a **semi-empirical surface-fire propagation engine**:

1. Behave-style extended Rothermel local surface-fire behavior using dead/live particle classes and the Albini reaction-velocity parameterization.
2. Wind/slope vector combination on the local terrain plane.
3. An elliptical local spread shape using effective wind speed.
4. A two-dimensional projected **level-set equation**, solved with first-order local Lax–Friedrichs spatial flux and explicit Euler time integration.
5. Adaptive time steps constrained by a documented CFL bound and environmental/output event times.
6. Independent scenario ensembles, parallelized across scenarios in Rust.

This selects one complete first solver. Do not start with a neural model, Bézier front solver, full atmospheric CFD, or browser WASM. Higher-order/narrow-band methods can replace the numerical baseline only after matching its tests and showing an advantage.

### 1.2 Ownership

| Team | Supplies/owns |
|---|---|
| Data team | Normalized, aligned landscape; fuel IDs; midflame wind vectors; moisture; UTC validity/availability; interpreted initial/updated fire geometry; historical validation observations |
| Physics team | Input validation; fuel calculations; propagation; restart/correction; uncertainty; scientific/runtime evaluation; result schema and fixtures |
| Visualization/service team | Hosting, map layers, user interaction, subscriptions, asset exposure unless separately assigned |

The physics team must export a library and CLI. Download connectors, HTTP servers, geospatial file reprojection, asset indexes, and frontend rendering are not required engine work.

### 1.3 Applicability

`surface_v1` assumes an established, quasi-steady surface fire. It does not simulate crown fire, spotting, convection/plume feedback, combustion chemistry, suppression, structural burning, point-ignition acceleration, or extinguishment. Its zero contour is a **modeled propagation boundary**, not a measurement of flames or fuel residence time.

For a perimeter initialization, v1 treats the full external perimeter as capable of propagation. If only certain segments are known to be active, the data team must mark the run `partial_active_front_unsupported`; do not claim that v1 reproduces those segment dynamics. Raw satellite detections cannot be substituted for a perimeter without a separate state-estimation method.

## 2. Workspace, dependencies, and interfaces

Create:

```text
Cargo.toml
Cargo.lock
rust-toolchain.toml
crates/
  fire-types/src/{lib,grid,units,input,output,error}.rs
  fire-behavior/src/{lib,fuel,weights,reaction,wind,ellipse}.rs
  fire-propagation/src/{lib,level_set,flux,timestep,arrival}.rs
  fire-state/src/{lib,checkpoint,correction}.rs
  fire-ensemble/src/{lib,sampling,run,aggregate}.rs
  fire-io/src/{lib,manifest,arrays,export}.rs
  fire-eval/src/{lib,analytic,replay,metrics}.rs
  fire-cli/src/main.rs
fixtures/
benchmarks/
docs/
```

Use Edition 2021 and workspace resolver `2` for continuity with the project reference. On project initialization, select a current supported stable toolchain, record its exact version in `rust-toolchain.toml`, and commit `Cargo.lock`. The edition does not require using an old compiler.

Dependencies by role:

- `serde` + `serde_json`: manifests and metadata.
- `thiserror`: explicit errors.
- `clap`: CLI arguments.
- `rayon`: bounded ensemble workers.
- `sha2`: artifact hashes.
- `geo`: optional output geometry operations; not required for raster propagation.
- `criterion`: benchmark development dependency.

Resolve compatible dependency versions once, commit them, and record compiler/target metadata in every run. The numerical core uses `f64`, standard slices/Vec, and no unsafe code initially. Avoid a tensor/ML framework.

Workspace dependency direction:

```text
fire-types <- fire-behavior <- fire-propagation <- fire-state
      ^                ^              ^                ^
      +----------------+------ fire-ensemble ---------+
fire-io -> fire-types
fire-eval -> numerical crates + fire-io
fire-cli -> all required public APIs
```

No crate may depend back on the CLI. `fire-state` wraps solver state; propagation must not import checkpoint I/O.

Public APIs, with concrete domain structs rather than untyped dictionaries:

```rust
fn validate_inputs(input: &RunInput) -> Result<ValidationReport, EngineError>;
fn prepare(input: &RunInput) -> Result<PreparedLandscape, EngineError>;
fn behavior(fuel: &FuelModel, moisture: Moisture, env: LocalEnvironment)
    -> Result<LocalBehavior, EngineError>;
fn initialize(prepared: &PreparedLandscape, initial: &InitialState)
    -> Result<SimulationState, EngineError>;
fn advance(state: &mut SimulationState, prepared: &PreparedLandscape,
    environment: &EnvironmentSeries, until_s: f64)
    -> Result<AdvanceReport, EngineError>;
fn run(input: &RunInput) -> Result<RunOutput, EngineError>;
fn run_ensemble(input: &RunInput, config: &EnsembleConfig)
    -> Result<EnsembleOutput, EngineError>;
```

Store shared immutable prepared arrays in `Arc<PreparedLandscape>`. Each scenario owns its evolving arrays. Result types expose quality flags rather than silently substituting defaults.

## 3. Coordinate, unit, and numeric conventions

### 3.1 Grid

All engine geometry uses a local projected metric CRS supplied by the data team. Require square cells in v1: `dx = dy = cell_size_m > 0`. Reject latitude/longitude degree grids, rotated grids, and mismatched layers.

- x = easting, y = northing, z = elevation, all meters.
- Origin `(x0,y0)` = southwest corner of the entire grid.
- Rows increase northward; columns increase eastward.
- Cell center: `x=x0+(col+0.5)*dx`, `y=y0+(row+0.5)*dy`.
- Row-major index: `i=row*width+col`.
- Total array length must equal checked integer multiplication `width*height`.

Many image/GeoTIFF arrays run north-to-south. The data adapter, not the solver, must reorder those arrays.

### 3.2 Units

External and propagation units are SI. The empirical behavior kernel below deliberately uses its published US customary coefficient units. Convert once at the kernel boundary; never insert SI values into those coefficient formulas.

| Quantity | Behavior-kernel units | Conversion |
|---|---|---|
| Depth | ft | `ft = m / 0.3048` |
| Dry load | lb/ft² | `lb/ft² = kg/m² / 4.88242763638305` |
| Particle density | lb/ft³ | `lb/ft³ = kg/m³ / 16.0184633739601` |
| SAV ratio | ft⁻¹ | `ft⁻¹ = m⁻¹ * 0.3048` |
| Heat content | Btu/lb (IT Btu) | `J/kg = Btu/lb * 2326.00032491728` |
| Wind | ft/min | `ft/min = m/s * 60 / 0.3048` |
| Spread rate | ft/min → m/s | multiply by `0.3048/60` |
| Reaction intensity | Btu/(ft² min) → kW/m² | multiply by `1055.05585262/(0.09290304*60*1000)` |
| Moisture | kg water/kg oven-dry fuel | fraction; `0.05 = 5%`, live moisture may exceed `1.0` |

Angles in mathematical functions are radians. Internal 2D vectors use `(east,north)`; prefer vectors over azimuths. A meteorological wind-from bearing `theta` degrees clockwise from north converts to a wind-to vector:

`u=-U*sin(theta*pi/180)`, `v=-U*cos(theta*pi/180)`.

Inputs must already represent **midflame** wind; a 10 m/20 ft wind is rejected unless the data team supplies an explicit, versioned wind adjustment. The core does not guess canopy sheltering or equate gust speed to mean wind.

### 3.3 Numeric rules

- Reject NaN/Infinity in required valid input values.
- Use `f64` for calculations and dynamic state. Use explicit masks for missing/unreached output.
- Evaluate powers only after checking positive bases where required.
- Fail a run on overflow/nonfinite behavior rather than clipping arbitrary spread rates.
- Relative comparison: `abs(a-b) <= atol + rtol*max(abs(a),abs(b))`.
- Equation fixture tolerance: `atol=1e-10`, `rtol=1e-8`, unless a source fixture has rounded published values.
- Cross-platform simulation reproducibility is tolerance-based, not assumed bitwise identical.

## 4. Input/output files: exact v1 interchange

### 4.1 Run bundle

```text
input/
  manifest.json
  fuel_models.csv
  elevation.f64le
  fuel_id.u16le
  valid.u8
  burnable.u8
  initial_phi.f64le
  weather/
    000000/{wind_east,wind_north,m1,m10,m100,mlh,mlw}.f64le
    000600/{wind_east,wind_north,m1,m10,m100,mlh,mlw}.f64le
```

Array files have no header; little-endian, row-major, exact declared length. `valid` and `burnable` contain only 0 or 1. Geometry initialization is represented by a signed-distance field supplied by the data team or generated by the fixture command. This avoids an unstated polygon rasterizer requirement.

Normative manifest example (a 64×64, flat, FM1 fixture):

```json
{
  "schema_version": 1,
  "incident_id": "synthetic-fm1",
  "start_utc": "2026-01-01T00:00:00Z",
  "grid": {
    "width": 64, "height": 64,
    "cell_size_m": 10.0,
    "x0_m": 0.0, "y0_m": 0.0,
    "crs": "LOCAL_METRIC_TEST"
  },
  "landscape": {
    "elevation": "elevation.f64le", "fuel_id": "fuel_id.u16le",
    "valid": "valid.u8", "burnable": "burnable.u8",
    "fuel_catalog": "fuel_models.csv"
  },
  "initial": {
    "phi": "initial_phi.f64le",
    "perimeter_activity": "assume_entire_boundary_active"
  },
  "environment": {
    "wind_reference": "midflame_horizontal_heading_surface_speed",
    "interpolation": "hold_right_continuous",
    "frames": [
      {
        "time_s": 0.0,
        "observed_or_valid_utc": "2026-01-01T00:00:00Z",
        "available_utc": "2026-01-01T00:00:00Z",
        "directory": "weather/000000"
      },
      {
        "time_s": 600.0,
        "observed_or_valid_utc": "2026-01-01T00:10:00Z",
        "available_utc": "2026-01-01T00:00:00Z",
        "directory": "weather/000600"
      }
    ],
    "coverage_end_s": 3600.0
  },
  "simulation": {
    "horizons_s": [300.0, 600.0, 1800.0, 3600.0],
    "cfl": 0.4, "max_step_s": 30.0,
    "wind_limit": "effective_0p9_ir",
    "ellipse_max_lb": 8.0,
    "reinitialize": false,
    "solver": "level_set_lf_euler_v1"
  }
}
```

A frame's timestamp is when its values apply, not necessarily when they were acquired. Forecast frames can validly be available before their valid times. The kernel requires full environmental coverage through the last requested horizon; do not hold beyond `coverage_end_s`. Sorted unique frame times begin at 0. Within `[t_k,t_(k+1))`, use frame k. All events use UTC plus elapsed seconds; no daylight-saving arithmetic.

### 4.2 Validation

Before allocating simulation state:

1. Check schema and exact byte lengths, grid compatibility, checksums, monotonic frame times, and required frame coverage.
2. Require at least 3×3 cells. Impose configurable memory limit before reading large bundles.
3. Check nonnegative moisture, finite wind, valid fuel IDs on burnable cells, positive fuel depth/density/SAV for nonzero load.
4. Allow moisture above extinction; it produces zero damping, not an input error.
5. Require at least one negative and one positive `phi` value on valid burnable cells and no exact zero initial samples in v1 fixtures. A zero-contour must be resolved by neighboring centers. Reject an isolated negative cell as an unresolved point ignition; use a disk radius of at least 3 cells for scientific fixtures.
6. `phi<0` means previously burned, `phi>0` means unburned. Values should be distances in meters near the contour. Require `initial_phi` positive in blocked cells.
7. A nonburnable or invalid cell has zero propagation and is an impermeable computational boundary. Invalid cells additionally mark uncertainty/coverage; they must not be displayed as known firebreaks.
8. Warn if slope exceeds 45° or cell size exceeds input information resolution. These are project review flags, not claims of a universal physical cutoff.

### 4.3 Output bundle

```text
output/
  run.json
  manifest.sha256
  arrival_time_s.f64le
  arrival_status.u8
  horizon_000300/{phi.f64le,burned.u8,front_cells.u8}
  horizon_000600/...
  diagnostics.json
```

`arrival_status`: 0=invalid/outside model domain, 1=valid but not reached within forecast, 2=initially burned with unknown historical arrival, 3=modeled arrival. `arrival_time_s` is meaningful only for status 3; write 0 in other slots and rely on the mask. Do not represent unknown historical arrival as a measured time zero.

`front_cells=1` for a burnable center that has a sign-changing valid/burnable von Neumann neighbor. This is a geometric contour band, not flaming residence. `burned=1` for initial burned or newly arrived centers.

Results include all input hashes, source revisions supplied by data team, compiler/build ID, exact configuration, step count, min/max step, wind-limit counts, LB-clamp counts, boundary/invalid-interface flags, memory and timing. For each horizon output use the elapsed time in the directory name; integer-second horizons only in v1.

## 5. Fuel representation and load transfer

### 5.1 Particle table

Each model has bed depth `delta`, dead extinction moisture `Mx_d`, and five original components: dead 1h/10h/100h, live herbaceous, live woody. Add a sixth computational component for cured herbaceous fuel. Fuel moisture is a dry-mass fraction.

The supplied catalog uses the published coefficient units, explicit column names, and standard assumptions:

- particle density `rho_p=32 lb/ft³`;
- total mineral fraction `S_T=0.0555`;
- effective mineral fraction `S_e=0.01`;
- 10h SAV `109 ft⁻¹`, 100h SAV `30 ft⁻¹`;
- dead/live heat `8000 Btu/lb` for the supplied entries.

Support FM1–FM13 and GR1 (101) initially. Unknown IDs are errors. Do not claim support for the other Scott–Burgan models until their explicit catalog entries are added and tested.

### 5.2 Dynamic curing

For dynamic models only, start from original catalog loads at every environmental frame. Define cured fraction from live herb moisture `M_lh`:

`f_c = clamp((1.20-M_lh)/0.90, 0, 1)`.

Transfer:

`w_cured=f_c*w_lh_original`, `w_lh=(1-f_c)*w_lh_original`.

Cured herbaceous fuel becomes a separate dead particle with the live herb SAV and dead 1h moisture. Do not merge it into 1h load if SAV differs. Woody load is unchanged. Total oven-dry load must be conserved within `1e-12` relative tolerance.

**Project convention:** use this exact endpoint-preserving interpolation rather than rounded `1.333-1.11*M_lh`. Record this variant in model metadata; source implementations using rounded coefficients can differ slightly. Static FM1–FM13 do not use curing.

## 6. Local Rothermel/Behave-style equations

All quantities in this section use the behavior-kernel units from section 3.2. Life state `l` is dead or live; particle `j` runs over present particles in that life state. Skip zero-load particles; absent groups contribute zero. No shortcuts may collapse dead/live weights into one unweighted average.

### 6.1 Surface-area and size-bin weights

Particle surface area per unit ground area:

`A_lj = w_lj * sigma_lj / rho_p_lj`.

`A_l=sum_j A_lj`, `A_total=sum_l A_l`.

`f_lj=A_lj/A_l` when `A_l>0`, otherwise 0.

`F_l=A_l/A_total` when `A_total>0`.

Characteristic SAV:

`sigma=sum_l F_l * sum_j f_lj*sigma_lj`.

For weighted net reaction load, group SAV into bins in ft⁻¹:

```text
bin 0: sigma >= 1200
bin 1: 192 <= sigma < 1200
bin 2: 96 <= sigma < 192
bin 3: 48 <= sigma < 96
bin 4: 16 <= sigma < 48
below 16: no reaction-load bin contribution
```

`g_l,b=sum_{j in bin b} f_lj`.

`Wn_l=sum_j g_l,bin(j) * w_lj*(1-S_T_lj)`.

This is intentionally **size-bin weighted**, not simply `sum f*w`. Particles in the same SAV bin use the same group's summed weight. Compute moisture, heat and effective mineral with ordinary within-life surface weights:

`Mbar_l=sum_j f_lj*M_lj`, `hbar_l=sum_j f_lj*h_lj`, `Sebar_l=sum_j f_lj*S_e_lj`.

### 6.2 Density and packing

`rho_b=(sum_lj w_lj)/delta` [lb/ft³].

`beta=sum_lj w_lj/(delta*rho_p_lj)` [dimensionless].

`beta_op=3.348*sigma^(-0.8189)`.

`r_beta=beta/beta_op`.

All-zero load returns a zero-spread `LocalBehavior` before any division. For a nonzero-load fuel, zero/negative depth, SAV, or packing is an error.

### 6.3 Live extinction moisture

Fine dead and fine live loads:

`D=sum_dead w_j*exp(-138/sigma_j)`.

`L=sum_live w_j*exp(-500/sigma_j)`.

`Mfd=(sum_dead w_j*exp(-138/sigma_j)*M_j)/D` if D>0; otherwise 0.

When live fuel exists and L>0:

`Mx_l=max(Mx_d, 2.9*(D/L)*(1-Mfd/Mx_d)-0.226)`.

If L=0, live reaction is zero. If no dead fuel, the formula reduces to the dead extinction lower bound; keep the declared convention and flag unusual live-only custom beds. Standard pilot catalog entries contain dead fuel. `Mx_d` must be positive.

### 6.4 Damping

For each present life state, `r_M=Mbar_l/Mx_l`:

```text
eta_M = 0                                    if r_M >= 1
eta_M = 1 - 2.59*r_M + 5.11*r_M² - 3.52*r_M³ otherwise
```

Clamp roundoff to [0,1], not negative physical input. For the supplied positive effective mineral fractions:

`eta_S=min(1, 0.174*Sebar_l^(-0.19))`.

Reject a nonzero-load custom particle with `S_e<=0` in v1; do not divide by zero.

### 6.5 Reaction velocity and intensity

Use the **Albini/Behave variant** consistently:

`a_gamma=133*sigma^(-0.7913)`.

`Gamma_max=sigma^(1.5)/(495+0.0594*sigma^(1.5))` [min⁻¹].

For stability, compute:

`Gamma=Gamma_max*exp(a_gamma*(ln(r_beta)+1-r_beta))` [min⁻¹].

`IR_l=Gamma*Wn_l*hbar_l*eta_M_l*eta_S_l`.

`IR=IR_dead+IR_live` [Btu/(ft² min)].

Do not mix the original 1972 A-parameter equation with this selected variant and still expect identical reference values.

### 6.6 Heat sink, flux, and no-wind/no-slope spread

For each particle:

`epsilon_lj=exp(-138/sigma_lj)`.

`Qig_lj=250+1116*M_lj` [Btu/lb].

`H_sink=rho_b*sum_l F_l*sum_j f_lj*epsilon_lj*Qig_lj` [Btu/ft³].

`xi=exp((0.792+0.681*sqrt(sigma))*(beta+0.1))/(192+0.2595*sigma)`.

`R0=IR*xi/H_sink` [ft/min]. Convert R0 to m/s for the geometry layer.

If IR=0, return zero spread. For positive IR, nonpositive heat sink is an input/implementation error. Export intermediate quantities in `inspect-behavior` so disagreements are traceable.

### 6.7 Wind/slope factors

`C=7.47*exp(-0.133*sigma^0.55)`.

`B=0.02526*sigma^0.54`.

`E=0.715*exp(-0.000359*sigma)`.

For midflame U in **ft/min**:

`phi_w=C*U^B*r_beta^(-E)`; define phi_w=0 at U=0.

For slope angle alpha:

`phi_s=5.275*beta^(-0.3)*tan(alpha)^2`.

The vector geometry and wind limitation applied to these factors are specified next.

The underlying surface-fire equation and selected implementation variants are documented in [Rothermel 1972](https://www.fs.usda.gov/rm/pubs_int/int_rp115.pdf), the [Behave fuelbed calculation](https://github.com/firelab/behave/blob/master/src/behave/surfaceFuelbedIntermediates.cpp), and [reaction-intensity calculation](https://github.com/firelab/behave/blob/master/src/behave/surfaceFireReactionIntensity.cpp). These government source files identify themselves as public domain. Preserve attribution and record the exact upstream revision used for independent comparisons.

## 7. Terrain plane, wind/slope vector, and ellipse

### 7.1 Elevation gradients

At cell `(r,c)`, use centered elevation differences when both required neighbors are valid:

`gx=(z[r,c+1]-z[r,c-1])/(2*dx)` and `gy=(z[r+1,c]-z[r-1,c])/(2*dy)`.

At a domain/invalid boundary, use a one-sided difference if one valid neighbor exists; otherwise use gradient component 0 and set `terrain_gradient_incomplete`. Do not use a nonburnable mask to remove otherwise valid elevation neighbors.

Let `s=sqrt(gx²+gy²)=tan(alpha)` and `q=sqrt(1+s²)`.

For s>1e-12 define an orthonormal basis on the local terrain plane:

`e_up=(gx/(s*q), gy/(s*q), s/q)`.

`e_cross=(-gy/s, gx/s, 0)`.

For flat terrain use `e_up=(1,0,0)`, `e_cross=(0,1,0)`.

The 3D tangent lift of a horizontal unit heading `d=(dxh,dyh)` is:

`lift(d)=normalize((dxh,dyh,gx*dxh+gy*dyh))`.

**Project wind convention:** the input vector's norm U is midflame speed along the local surface, while its two components specify horizontal heading. Lift the heading but preserve U; do not increase wind magnitude by the terrain lift. This is a declared local-plane convention, not an atmospheric terrain-flow model. Data-team wind adapters must identify this convention.

### 7.2 Combine enhancement vectors

Compute `phi_s=5.275*beta^(-0.3)*s²`.

Let `w_hat=lift(wind_to/|wind_to|)` when U>0.

`v_phi = phi_w*w_hat + phi_s*e_up` [a 3D vector tangent to terrain].

`phi_eff_raw=|v_phi|`.

If `phi_eff_raw>1e-12`, heading `h=v_phi/phi_eff_raw`. Otherwise use `h=e_up`; with no enhancement the ellipse will be circular, so arbitrary heading has no effect.

A side direction tangent to the plane is obtained without ambiguous angle conventions:

`n=normalize((-gx,-gy,1))`, `k=n cross h`.

Check `|h|≈|k|≈1` and `h dot k≈0`.

### 7.3 Effective-wind inversion and limit

For nonzero enhancement, invert the wind-factor equation:

`U_eff_raw = (phi_eff_raw/(C*r_beta^(-E)))^(1/B)` [ft/min].

For zero enhancement use 0. Apply selected historical empirical limit:

`U_limit = 0.9*IR` [numerical relation in the original coefficient units].

This is an empirical unit-specific rule; multiplying a kW/m² value by 0.9 is wrong. In `effective_0p9_ir` mode:

`U_eff=min(U_eff_raw,U_limit)`.

`phi_eff=C*U_eff^B*r_beta^(-E)` for U_eff>0, otherwise 0.

`R_head=R0*(1+phi_eff)` [ft/min, then convert to m/s].

Keep the original heading h after limiting magnitude. This is the selected project limit policy applied to the combined equivalent wind, including slope. Export a flag when limiting occurs. A development-only `wind_limit=none` mode is permitted for source comparisons; publish the selected mode with every result.

Exact equality with another program requires matching that program's wind-limit options and wind/slope conventions. Do not silently add further clamps to R_head.

### 7.4 Ellipse shape

Convert effective wind to m/s: `Ue=U_eff*0.3048/60`.

`LB_raw=0.936*exp(0.2566*Ue)+0.461*exp(-0.1548*Ue)-0.397`.

`LB=clamp(LB_raw,1,8)`.

Evaluate with overflow checks; the Ue argument is m/s, not mph. The equivalent coefficients often used with mph are approximately 0.1147 and -0.0692; do not mix the two versions.

Eccentricity `e=sqrt(max(0,1-1/LB²))`.

`R_back=R_head*(1-e)/(1+e)`.

`a=(R_head+R_back)/2` [semimajor growth rate, m/s].

`b=a/LB` [semiminor growth rate, m/s].

`c=(R_head-R_back)/2` [center drift speed, m/s].

Checks: `a+c=R_head`, `a-c=R_back`, `a/b=LB`, `c/a=e` when a>0.

**Important geometry distinction:** b is maximum half-width growth; it is not the radial rate perpendicular to the ignition-to-head axis. The radial rate at relative angle theta on flat terrain is:

`R_radial(theta)=R_head*(1-e)/(1-e*cos(theta))`.

The normal rate used by a level-set solver is a different quantity, derived below. Do not substitute the radial formula for the normal speed.

The ellipse and wind/slope construction is informed by the [FARSITE model-development report](https://www.fs.usda.gov/rm/pubs/rmrs_rp004.pdf) and [official ellipse technical reference](https://owfflammaphelp62.firenet.gov/LegacyFARSITE/Tech_Elliptical_Dimensions.htm). The terrain-vector implementation and limit policy above are explicitly specified project choices.

## 8. Governing propagation equation

### 8.1 Level-set field

Let `phi(x,y,t)<0` inside modeled burned area, `phi>0` outside, and `phi=0` at the propagation boundary. Define horizontal gradient `p=(phi_x,phi_y)`.

The local terrain-plane growth ellipse is the velocity set:

`v(theta)=c*h + a*cos(theta)*h + b*sin(theta)*k`.

Only its east/north components move projected coordinates. Define:

`d=(c*h_x,c*h_y)`, `A=(a*h_x,a*h_y)`, `Bv=(b*k_x,b*k_y)`.

The support function of this projected velocity ellipse is:

`H(x,y,t,p)=d dot p + sqrt((A dot p)²+(Bv dot p)²)`.

Solve:

`phi_t + H(x,y,t,grad(phi)) = 0`.

This formula automatically accounts for projection from slope-surface distances into map distances. Do not apply an additional cosine slope correction afterward. On flat terrain, zero wind/slope gives `H=R0*|p|`.

For `p!=0`, the front-normal map speed is `H(p)/|p|`. For p=0 define H=0. The square-root argument is a sum of squares and must be nonnegative; clamp negative roundoff only if using an algebraically expanded equivalent expression.

The Hamiltonian is homogeneous: `H(lambda*p)=lambda*H(p)` for lambda>=0. The spread ellipse contains zero velocity because c<a for finite LB, so H>=0 analytically. This models outward growth without extinguishment. The use of an Eulerian level-set representation follows the framework described in [NIST TN 1611](https://www.nist.gov/publications/fire-front-propagation-using-level-set-method); the specific projected-ellipse Hamiltonian is derived here from the stated ellipse.

### 8.2 Initial field

The engine accepts `initial_phi` as its canonical state input. For fixture circles centered at `(xc,yc)` with radius r0:

`phi0(x,y)=sqrt((x-xc)²+(y-yc)²)-r0`.

For an arbitrary polygon supplied by another team, they compute Euclidean map distance to the polygon boundary, negative for covered interior and positive outside. Holes remain positive. The engine validates signs and near-interface scale; it does not infer fire activity from color or imagery.

For input distance validation, calculate central-gradient norms away from medial axes; emit a warning if more than 10% of samples within 3 cells of the front have norm outside [0.5,1.5]. This is a numerical quality heuristic, not a proof of valid distance.

## 9. Numerical method: exact update rules

### 9.1 One-sided derivatives

At an interior valid/burnable cell i:

```text
Dx_minus = (phi[i] - phi[west]) / dx
Dx_plus  = (phi[east] - phi[i]) / dx
Dy_minus = (phi[i] - phi[south]) / dy
Dy_plus  = (phi[north] - phi[i]) / dy
px = (Dx_minus + Dx_plus)/2
py = (Dy_minus + Dy_plus)/2
```

When a neighbor is out of the grid, invalid, or nonburnable, replace that neighbor's phi by phi[i] for this derivative only. This is a zero-normal-gradient ghost rule. Do not read across the obstacle to the next valid cell. Never update blocked cells themselves; retain their initial positive phi.

This rule prevents first-order stencil transfer through a masked one-cell wall. It is an engineering obstacle treatment and must pass the wall/disconnected-domain tests. An invalid region acts as an unknown computational boundary; results near it are flagged, not interpreted as evidence fire cannot cross the real region.

### 9.2 Dissipation bounds

For each cell's projected vectors:

`alpha_x=abs(d_x)+sqrt(A_x²+Bv_x²)`.

`alpha_y=abs(d_y)+sqrt(A_y²+Bv_y²)`.

These bound the absolute partial derivatives of H with respect to px/py. Use a **global bound per environmental frame** for the baseline:

`ax=max_valid_burnable alpha_x`, `ay=max_valid_burnable alpha_y`.

Although this is more diffusive than a carefully implemented local optimization, it makes the first implementation's monotonicity and timestep bound straightforward. If all rates are zero, set ax=ay=0.

### 9.3 Numerical Hamiltonian and update

`H_LF=H(px,py) - 0.5*ax*(Dx_plus-Dx_minus) - 0.5*ay*(Dy_plus-Dy_minus)`.

`phi_candidate=phi_old-dt*H_LF`.

**Project irreversibility rule:** `phi_new=min(phi_old,phi_candidate)` on burnable/valid cells. This enforces the model's no-recession assumption and prevents numerical sign reversals from unburning centers. Record how many near-front updates were constrained. It does not imply simulation of extinction or actual flame duration.

Important zero-spread special case: if a cell has zero reaction/spread, set `phi_new=phi_old` at that cell. Otherwise global LF dissipation could numerically propagate through a wet non-spreading fuel cell. Treat such zero-speed cells as frozen for this frame. Positive but tiny rates are not rounded to zero.

Use separate old/new arrays. Never update phi in place inside the spatial loop. Boundary behavior, masks, and the zero-speed override are part of the numerical specification and must be included in convergence tests.

### 9.4 Time step and events

`dt_CFL=cfl/(ax/dx+ay/dy)` when the denominator is positive, otherwise infinity.

`dt=min(dt_CFL, max_step_s, next_environment_time-t, next_output_time-t, requested_end-t)`.

Defaults: CFL factor 0.4, maximum step 30 s. Reject CFL<=0 or CFL>0.5 in v1. An output/checkpoint/correction time is an event; never step past it. At an environmental knot t_k, apply the new frame before starting the next step. Recompute behavior/ellipse/bounds when a frame changes.

Use `event_eps=1e-9*max(1,abs(t))` only to snap to an event already within that tolerance. Process coincident events once. If the selected positive step is below `1e-6 s`, report `TimeStepTooSmall` rather than looping indefinitely. Do not silently increase an unstable time step.

This piecewise-constant environment convention is deliberately simple and fully reproducible. Linear interpolation can be a later, separately tested mode; do not implement it implicitly.

### 9.5 Arrival time

For an initially unburned cell crossing from `phi_old>0` to `phi_new<=0`, estimate:

`T=t+dt*phi_old/(phi_old-phi_new)`.

Set status 3 once; do not overwrite with later values. Initially burned cells retain status 2. This is a first-order temporal interpolation of the numerical level set, not an exact physical arrival measurement. Output arrival error must be evaluated against time-resolved truth.

### 9.6 Reinitialization and numerical order

Do not reinitialize phi in v1 (`reinitialize=false`). Use a full-domain first-order baseline with no narrow-band truncation. This avoids introducing an unspecified signed-distance reconstruction that can move the front.

Inspect near-front gradient norms each output and report their distribution. The supported maximum horizon is only the range for which the numerical tests and historical evaluation pass. If gradient degeneration or diffusion fails the declared gates, do not claim that reducing dt alone fixes first-order spatial diffusion: refine the grid, shorten the supported horizon, or implement a separately documented higher-order/reinitialization method with its own zero-contour-preservation tests.

### 9.7 Complete deterministic loop

```text
validate all input contracts
prepare terrain gradients and catalog
load phi0; initialize arrival status/time and t=0
for each environment epoch:
    compute local behavior + projected ellipse for every modeled cell
    compute ax, ay in deterministic index order
    while t is before the next environment epoch or final time:
        process any output/checkpoint events exactly at t
        choose dt from CFL and next event times
        for every modeled cell using phi_old only:
            apply masks and zero-spread freeze
            form derivatives, H_LF, irreversible phi_new
            check finite values
            assign first numerical arrival if sign crossed
        swap phi buffers; t += dt
        check cancellation; record diagnostics
process final events; atomically export complete run
```

Save results only after a complete step. Cancellation returns a declared partial state and last reached model time; it must not label an unfinished requested horizon as complete.

## 10. Optional intensity diagnostic

Enable only after validating against independent source cases. This is modeled head-fire intensity potential under local inputs, not structural damage or a cell's ongoing heat release.

Using behavior-kernel sigma [ft⁻¹]:

`residence_time_min=384/sigma`.

`heat_per_area=IR*residence_time_min` [Btu/ft²].

`head_fireline_intensity=heat_per_area*(R_head_ft_min/60)` [Btu/(ft s)].

`head_fireline_intensity_kw_m = head_fireline_intensity * 1055.05585262/(0.3048*1000)`.

Label this output `head_intensity_potential_kw_m`, with frame valid time. Do not call it realized front-normal intensity everywhere on the perimeter. Directional or observed intensity requires additional definitions. Keep disabled by default until reference checks are available. No flame-length or damage model is required for v1.

## 11. Restart and interpreted observation corrections

### 11.1 Checkpoint contents

Checkpoint only at completed time steps. Save:

- Schema/model/solver version and elapsed time t.
- Original run manifest hash and every input artifact checksum.
- Current phi, arrival status/time arrays, and initial-burned mask.
- Current environmental frame index, next output event index, diagnostic counters.
- Numerical configuration and any scenario perturbations.
- Completed output references and seed/PRNG state where applicable.

Ax/ay and behavior arrays can be recomputed from the same versioned inputs. A scratch `phi_new` need not be stored. Write to a temporary directory, verify byte counts/checksums, then rename to a completed checkpoint directory. A resume must reject mismatched input hashes, solver/model versions, shape, or configuration.

Test exact bytes for repeated same-build sequential runs; use the declared tolerance for cross-platform runs. Resume must produce the same requested final state as uninterrupted execution.

### 11.2 Corrections

The data team supplies a corrected signed-distance state at acquisition time t_obs, with provenance. Baseline correction policy is `replace_state_at_observation`, not an unspecified Kalman filter.

1. Restore a checkpoint at or before t_obs and advance exactly to t_obs with the original pre-update environmental sequence.
2. Replace phi with the interpreted corrected field on the same grid.
3. Rebuild arrival statuses: corrected interior cells with independently known earlier modeled arrivals retain those times only if the correction policy explicitly preserves history; baseline resets all corrected interior to status 2 (arrival before correction unknown). Corrected exterior becomes status 1 and loses previously modeled future arrivals.
4. Set the corrected state as a new run origin at t_obs, reset elapsed forecast times, and retain parent-run provenance.
5. Use the forecast information available at the new issue time for future propagation.

Replacing a perimeter can revise previously estimated burned area. That is an epistemic correction, not physically reversing combustion. Preserve old results as superseded outputs. Do not mix pre-correction and post-correction arrival claims without explicit provenance.

If no checkpoint is available, re-run from the original input to t_obs; this is correct though potentially slower. Support for raw hotspot likelihoods and partial front activity is outside this milestone.

## 12. Ensembles: specified first framework

### 12.1 Experimental perturbations

An ensemble quantifies uncertainty under its specified scenario distribution. It is not calibrated merely because it contains many runs.

For a reproducible development preset, use N=32, with **one coherent bias per scenario across the full domain and all environmental frames**:

- Wind east/north biases: independent N(0,1²) m/s added to the supplied midflame components.
- Dead moisture multiplier: `exp(0.15*Z_d-0.5*0.15²)` with Z_d~N(0,1), shared by m1/m10/m100.
- Live moisture multiplier: same formula with an independent Z_l, shared by mlh/mlw.
- Initial-front distance bias: 0 by default. Optional explicit signed-distance perturbation `phi0'=phi0-delta`, delta~N(0,sigma_front²), only for a distance-valid initial field.
- No arbitrary spread-rate multiplier in the initial preset.

These standard deviations are **engineering demonstration values**, not empirical uncertainty estimates for a particular sensor or weather model. Label outputs `raw_scenario_frequency`, preset `demo_global_bias_v1`, and prohibit an operational probability claim until the pilot calibration is complete. The baseline deterministic run is separate and not included as a privileged ensemble member.

Revalidate each perturbed input. Do not cap live moisture at 100%. Positive moisture multipliers preserve nonnegativity. A perturbed invalid initial contour fails that scenario; report it, do not silently substitute the unperturbed member.

### 12.2 PRNG specification

To avoid unspecified library RNG behavior, implement SplitMix64 with wrapping u64 arithmetic:

```text
state += 0x9E3779B97F4A7C15
z = state
z = (z xor (z >> 30)) * 0xBF58476D1CE4E5B9
z = (z xor (z >> 27)) * 0x94D049BB133111EB
return z xor (z >> 31)
```

Seed scenario k by setting a seed-generator state to `master_seed + k*0x9E3779B97F4A7C15` modulo 2^64 and taking one SplitMix64 output; use this output as that scenario's generator state.

Uniform open-interval samples: `u=((next_u64 >> 11)+0.5)/2^53`.

Box–Muller: `z1=sqrt(-2*ln(u1))*cos(2*pi*u2)`, `z2=sqrt(-2*ln(u1))*sin(2*pi*u2)`.

Draw in fixed order: wind-east, wind-north, dead-moisture, live-moisture, optional front bias. Scenario draws occur before Rayon scheduling; worker order must not affect scenarios. Store every realized perturbation in metadata.

### 12.3 Aggregation

For valid cell i and horizon h:

`p_raw(i,h)=(1/N_complete)*sum_k 1[cell i initially burned or T_k(i)<=h]`.

Compute only on a common valid domain; otherwise export a member-coverage count and a missing output. Default publication requires all requested members completed. A deliberately allowed partial result is labeled partial with N_requested/N_complete, never silently normalized as complete.

Arrival quantiles are conditional on arrival by the maximum horizon and must be accompanied by `p_no_arrival`. Use nearest-rank quantile: sort finite arrivals, choose index `max(0,ceil(q*n)-1)` for q=0.1,0.5,0.9. If no finite modeled arrivals, arrival quantiles are missing. Initially burned cells have historical arrival unknown, not a distribution of zeros.

Convergence experiment: run N=16,32,64,128 with nested member seeds and report changes in probability maps and decision thresholds. A rough binomial sampling diagnostic is `sqrt(p*(1-p)/N)`; it measures scenario sampling noise, not all model uncertainty.

## 13. Verification: exact tests and expected outcomes

All test names below become Rust integration tests or documented evaluation commands. The equations checker is an arithmetic reference, not an independently reproduced Behave/FARSITE result.

### 13.1 Scalar and transformation tests

| Test | Inputs | Required result |
|---|---|---|
| Units | 1 ft/min | 0.00508 m/s |
| Wind bearing | U=2 m/s, from 0° | east≈0, north=-2 |
| Curing | GR1, M_lh=0.30/0.75/1.20 | cured fraction=1/0.5/0; total load conserved |
| Fuel extinction | FM1, M1=0.12 and 0.20 | IR=R0=0 |
| FM1 arithmetic | M1=0.05; no wind/slope | match companion intermediate values |
| Mixed fuel | FM2 with (0.05,0.07,0.09,0.60,0.90) | match weighting/reaction reference |
| Live woody | FM4 with same moisture tuple | live/dead extinction and reaction match reference |
| Terrain basis | gx=0.3, gy=0.4 | slope tangent=0.5; orthonormal basis |
| Terrain projection | plane gx=0.5, gy=0, isotropic surface R=0.1 | east support=0.1/sqrt(1.25), north support=0.1 |
| Zero ellipse | a=b=c=0 | H=0, ax=ay=0 |
| Ellipse identities | a=0.2,b=0.1,c=sqrt(0.2²-0.1²) | positive head/back and homogeneous H |
| Normal vs radial | same ellipse; north direction | normal support=b=0.1, radial north=b²/a=0.05 |
| Homogeneity | any p, lambda=2 | H(2p)=2H(p) |

For all scalar tests use section 3 tolerances. Keep exact input tuples in committed JSON. A failed intermediate test must be fixed before tuning spread parameters.

### 13.2 Numerical fixtures with analytic geometry

Provide a `test-speed` backend that directly supplies d/A/Bv or constant isotropic R. It is test-only and bypasses the behavior equations so propagation defects can be isolated.

**A. Stationary front**

- 2000 m square domain, dx=10 m, initial disk radius 150 m at center, R=0.
- Run 3600 s.
- Phi and burned mask unchanged exactly; no modeled arrivals.

**B. Isotropic disk**

- Same domain and radius; R=0.1 m/s; run 1200 s.
- Exact radius is 270 m.
- Extract subcell edge-crossing points from phi as described below.
- Max radial boundary error <=2*dx; relative burned-area error <=8% at dx=10 m.
- Repeat dx=20,10,5 m with the same physical initial geometry. Fine-grid mean absolute boundary error must be smaller than coarse-grid error. Report actual errors rather than assuming a convergence order from the code name.

**C. Affine planar front**

- Flat homogeneous grid and phi0=x-x_front, with comparison cells far from outer boundaries.
- Exact solution `phi=x-x_front-H((1,0))*t`.
- Central differences eliminate LF diffusion for the affine field. Interior errors <=1e-9 m over a short run whose stencil influence cannot reach the comparison area. This checks flux signs and time integration.

**D. Homothetic ellipse**

- Flat 4000 m square domain, a=0.2,b=0.1,c=sqrt(a²-b²), east heading; t0=1500 s.
- Initial ellipse: center `(2000+c*t0,2000)`, semiaxes a*t0,b*t0.
- Initialize implicit phi as `b*(sqrt(((x-2000-c*t0)/a)^2+((y-2000)/b)^2)-t0)`. This has meter units and correct signs, but is not exact signed distance; permit the distance warning in this test-only backend.
- After 600 s exact center is `(2000+c*(t0+600),2000)` and semiaxes a*(t0+600),b*(t0+600).
- Compare support at 72 evenly spaced normal directions against the analytic ellipse; maximum error <=2*dx at dx=10 m. Repeat at 5 m and require lower mean support error.

**E. Changing conditions**

- Initial disk r0=150 m, flat domain.
- First ellipse a=0.2,b=0.1,c=sqrt(a²-b²), east heading for 300 s.
- Rotate heading and side vectors 90° north for another 300 s.
- Exact support in unit normal n is `r0 + 300*H_east(n) + 300*H_north(n)`, relative to the original disk center.
- Compare 72 support directions, max error <=3*dx at dx=10 m; improve mean error at 5 m.
- Must differ from a run holding east wind throughout. Apply the switch exactly at 300 s.

**F. Obstacles/masks**

- Two regions separated by a domain-height one-cell nonburnable wall, ignition only on west side.
- No east-side center may burn over the test horizon.
- Repeat with an invalid-data wall: same computational blocking, plus unknown-coverage flag.
- Repeat with a burnable but zero-speed wall: no leakage from global dissipation.
- Leave an opening at least 4 cells wide: the model must propagate through it when geometry/time permit.

**G. Restart/events**

- Compare continuous 600 s simulation to checkpoint at 300 s and resume.
- Include a weather change and output at the same 300 s event.
- Same-build results match exactly if reduction/order remains sequential; otherwise require documented tolerance and identical masks/statuses.
- Duplicate horizons and missing environmental coverage fail validation.

These geometric error thresholds are proposed engineering acceptance gates for the stated fixtures, not universal real-fire accuracy promises. If the selected first-order scheme fails, improve numerics or revise supported resolution through a documented change; do not silently relax a failing assertion.

### 13.3 Boundary-point extraction for evaluation

For each east/north pair of adjacent valid/burnable centers with opposite signs, compute crossing fraction `lambda=phi_i/(phi_i-phi_j)` and point `x_i+lambda*(x_j-x_i)`. Include zero endpoint once if it occurs during evolution. Deduplicate shared endpoint coordinates within 1e-9 m.

For a unit normal n, numerical support is `max_points dot(point-center,n)`. Use these subcell crossings instead of burned cell centers for support/error tests. Writing these as `front_points.csv` is sufficient for debugging; ordered closed polygons or marching-squares topology can be added by the visualization team.

### 13.4 Independent behavior reference gate

The numerical companion is derived from this specification, so it cannot establish independent correctness. Before publishing a physics release:

1. Obtain the official Behave repository linked in section 20 and pin a commit SHA in `docs/reference_revision.json`.
2. Build its own tests according to its README before using it as an oracle.
3. Generate values for FM1, FM2, FM4, FM10 at dead moistures (0.05,0.07,0.09), live herb/wood (0.60,0.90), midflame winds 0/1/3 m/s, slopes 0/15/30°, with aligned wind/slope and exact matching options.
4. Record expected intermediate values and final R0/head rates, units, upstream SHA, and oracle command/configuration as JSON.
5. Compare Rust against source results using 1e-6 relative tolerance for equivalent unrounded formulas, or a justified published rounding tolerance. Separate known convention differences: curing interpolation, wind limit, direction transform, ellipse coefficient rounding.
6. Treat unexplained differences as defects. Do not calibrate around arithmetic errors.

An external oracle build requires a working compiler/source download environment; that access cannot be replaced by inventing expected numbers. The source retrieval and exact procedure are specified, but the independent comparison has not been executed as part of writing this plan.

## 14. Historical validation and model acceptance

### 14.1 Data-team handoff

For each incident require a normalized bundle at forecast issue time, reference perimeter progression with acquisition times and uncertainty, forecast weather vintages available at that issue time, and any corrected state updates with availability times. If only eventual burn scars exist, report final-footprint comparison only; do not claim validated 30-minute arrival predictions.

Use whole-incident splits. A suggested initial pilot needs at least 12 independent progression-rich incidents: 6 development, 3 calibration, 3 untouched test. This is a minimum workflow demonstration, not statistical evidence of broad geographic reliability. If fewer exist, report exploratory case studies and retain all results.

Freeze split IDs and metrics before tuning. Never split adjacent pixels of one incident across development and test. Replay exposes an observation only when `available_utc <= forecast_issue_utc`; future valid weather is allowed only from forecast products available at issue time.

### 14.2 Metrics

For predicted binary burn B and observed burn O on common valid cells, compute areas by multiplying counts by dx*dy:

- `IoU=|B intersection O|/|B union O|`.
- `Dice=2*|B intersection O|/(|B|+|O|)`.
- `missed_fraction=|O minus B|/|O|`.
- `false_burn_area=|B minus O|*dx*dy`.
- Arrival MAE: mean `abs(T_pred-T_obs)` only where reference arrival is known; separately report missed arrivals and interval-censored truth.
- Brier score: `(1/n)*sum_i (p_i-y_i)^2` for calibrated/raw probability evaluated separately.

Empty-set rules: if B and O are both empty, IoU=Dice=1; if O empty, missed_fraction is missing. If one set alone is empty, IoU=Dice=0. Never turn an undefined arrival comparison into zero error.

Reliability diagram: bins [0,.1),...,[.9,1], report member count, mean p, and observed burn fraction; show sample counts and avoid treating correlated pixels as independent incidents. Report per-incident/per-horizon and incident-bootstrap uncertainty, not just pooled favorable pixels.

### 14.3 Required comparisons and gates

Compare persistence, labeled heuristic envelope, deterministic physics, changing-weather physics, correction-enabled runs, and raw/calibrated ensembles. Keep spatial/temporal resolutions matched or explicitly stratified.

Before final test, write `evaluation_protocol.json` with intended decision task and thresholds. Default research advancement rule: physics improves median incident IoU and reduces median missed area versus persistence at at least two requested horizons on development incidents, without hiding incident-level failures. Final operational thresholds require the intended user's acceptable missed/false warning tradeoff and domain review; this cannot be settled by a generic equation.

No automated criterion in this document converts a small pilot into a validated emergency-warning service. A completed build is a tested research engine; deployment claims must match the evidence.

## 15. Performance and memory framework

### 15.1 Baseline layout

Use structure-of-arrays for terrain gradients, six projected ellipse coefficients (d_x,d_y,A_x,A_y,Bv_x,Bv_y), spread-active flag, two phi buffers, arrivals, and masks. Cache fuel-specific static terms where moisture/curing does not change them. Dynamic curing requires reweighting at each environmental frame.

Full-domain baseline cost is approximately O(N_cells*N_steps) per scenario. N_steps scales inversely with dx under CFL and spatial refinement increases N_cells; halving dx can approach an 8× runtime increase in 2D before other effects. Measure instead of assuming a million-cell grid is cheap.

Core arrays consume roughly 110–160 bytes/cell depending on retained input/diagnostic arrays, excluding environment frames and Rust allocator overhead. Calculate exact allocations from array counts before running; a 1024² grid can exceed 150 MB per scenario once inputs/output buffers are included. Avoid caching every frame's full behavior when unnecessary.

### 15.2 Concurrency

Use one bounded Rayon pool for independent scenarios. Begin with each scenario's spatial loop sequential for deterministic reference behavior. Worker limit:

`workers=min(requested_workers, available_cpus, floor(memory_budget/per_scenario_peak_estimate))`.

If workers<1, fail before allocation. Shared landscape bytes are subtracted once from budget; variable scenario bytes separately. Add cancellation checks at step boundaries and in long preparation loops.

Do not spawn an independent Rayon pool inside each scenario. Later cell-parallel loops require benchmarked coordination to avoid oversubscription.

### 15.3 Benchmark matrix

Record CPU/OS/toolchain, peak resident memory, step count, min dt, wall time, preparation/solve/export times, and output bytes for:

- 256²/512²/1024² cells at 30 m.
- 1 h and 6 h horizons.
- N=1/16/32 scenarios.
- Homogeneous and heterogeneous fuels, changing weather, and a masked domain.

Set a measured pilot responsiveness budget after the first matrix. Do not carry over the reference architecture's sub-millisecond spatial-query promise into regional simulation. Scientific correctness tests run before each performance comparison. Any optimized implementation must match baseline outputs within declared tolerance and retain the same masks/arrival semantics.

## 16. Commands, deliverables, and errors

Implement these commands with `--help` documenting units and paths:

```text
fire-cli make-fixture --kind fm1-circle --out fixtures/generated/fm1
fire-cli validate --input fixtures/generated/fm1/manifest.json
fire-cli inspect-behavior --fuel FM1 --m1 0.05 --m10 0.07 --m100 0.09 \
  --mlh 0.60 --mlw 0.90 --wind-east-m-s 1 --wind-north-m-s 0 --gx 0 --gy 0
fire-cli simulate --input INPUT/manifest.json --out OUTPUT
fire-cli resume --checkpoint CHECKPOINT --until-s 3600 --out OUTPUT
fire-cli correct --checkpoint CHECKPOINT --update UPDATE.json --out OUTPUT
fire-cli ensemble --input INPUT/manifest.json --config ensemble.json --out OUTPUT
fire-cli evaluate --predictions RUNS.json --truth TRUTH.json --protocol PROTOCOL.json --out REPORT
```

`make-fixture fm1-circle` creates the exact 64² bundle from section 4, flat elevation, all masks=1, fuel=1, center (320,320), radius 45 m, no wind, moisture (0.05,0.07,0.09,0.60,0.90) at both frames, and copies the supplied catalog. Radius is deliberately not an exact integer multiple of cell-center distance. The purpose is wiring, not a real-fire benchmark.

Errors are structured `{code,message,artifact,cell_index?,time_s?}`. Minimum codes: `SchemaMismatch`, `InvalidArrayLength`, `UnsupportedFuel`, `InvalidUnits`, `InvalidWindReference`, `MissingCoverage`, `NonFiniteInput`, `InvalidFuelParameter`, `InvalidInitialFront`, `TimeStepTooSmall`, `NumericalOverflow`, `CheckpointMismatch`, `MemoryBudgetExceeded`, `Cancelled`.

CLI exit codes: 0=complete, 2=input/configuration error, 3=numerical failure, 4=resource limit, 130=cancelled. A completed directory contains `run.json` with `status=complete`; incomplete directories cannot be mistaken for completed results.

CI commands after creating the Rust workspace:

```text
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo test --workspace --release
cargo bench -p fire-propagation
```

The benchmark command assumes the implementation adds Criterion targets; it is an instruction for the build, not a claim that those crates already exist.

## 17. Ordered implementation backlog

| ID | Implement | Done when |
|---|---|---|
| P01 | Workspace/types/typed units/error schema | Builds; unit conversions and index-overflow tests pass |
| P02 | File bundle reader and validation | Exact fixture accepted; corrupt lengths/units/masks rejected |
| P03 | Catalog parsing and dynamic transfer | All supplied IDs load; endpoint/mass-conservation tests pass |
| P04 | Surface and size-bin weights | FM1/FM2/FM4 intermediate values match fixtures |
| P05 | Reaction, heat sink, R0 | Extinction and no-wind reference tests pass |
| P06 | Terrain frame and wind/slope ellipse | Orthonormality/projection/ellipse identities pass |
| P07 | Test-speed Hamiltonian and LF step | Affine-front and zero-speed tests pass |
| P08 | Adaptive events and arrival output | Weather-switch and arrival interpolation tests pass |
| P09 | Full deterministic CLI | Fixture run exports readable versioned arrays |
| P10 | Analytic geometry suite | Circle/ellipse/switch/wall/refinement gates pass |
| P11 | Independent behavior oracle | Pinned source comparisons pass or differences documented |
| P12 | Checkpoint/resume/correction | Continuation agrees; corrected state creates separate provenance |
| P13 | Scenario RNG, execution, aggregation | Same seeds independent of worker order; no-arrival masks correct |
| P14 | Historical replay and evaluation | Split protocol enforced; complete per-incident report produced |
| P15 | Profiling and bounded memory | Benchmark matrix published; resource failures graceful |
| P16 | Team handoff | Library/CLI docs, schemas, fixtures, and limitations accepted |

No step depends on waiting for every live data feed. P01–P13 can use synthetic fixtures and normalized archived bundles. Begin real-incident integration as soon as P09 is available.

## 18. Provisional milestones and effort

For one experienced engineer with periodic fire-domain review and timely input-team support:

- Weeks 1–2: P01–P03; freeze contracts and ship an integration fixture.
- Weeks 3–4: P04–P06; verified behavior kernel and source comparison preparation.
- Weeks 5–7: P07–P10; deterministic numerical engine and analytic suite.
- Weeks 8–9: P11–P12; independent comparisons, restart/correction.
- Weeks 10–12: P13; ensembles and preliminary historical runs.
- Weeks 13–16+: P14–P16; revisions, profiling, validation, handoff.

These are planning estimates, not guaranteed delivery dates. Data gaps or solver failures can extend the schedule. If analytical/numerical gates fail, fixing the engine takes priority over adding research models or UI integration.

## 19. Completion checklist

A research-engine release is complete when:

- All equations have an implementation location and scalar tests.
- All supplied fuel entries and model variants appear in run provenance.
- Circle/ellipse/changing-condition/mask/restart tests pass at stated resolutions.
- Independent behavior checks pass for matching configurations.
- Historical results include failed cases and limits, without future information leakage.
- Ensemble outputs identify raw frequency versus calibrated probability.
- Other teams can run the supplied bundle and read outputs without interpreting hidden conventions.
- Memory/runtime measurements correspond to actual tested workloads.
- The model is labeled surface-only with no implied crown/spotting/extinction capability.

## 20. References and implementation attribution

1. Rothermel, 1972, INT-RP-115: [surface spread formulation](https://www.fs.usda.gov/rm/pubs_int/int_rp115.pdf). Governing semi-empirical surface-fire framework, not a guarantee of validity for extreme regimes.
2. USDA Fire Sciences Laboratory: [Behave repository](https://github.com/firelab/behave), [fuelbed intermediates](https://github.com/firelab/behave/blob/master/src/behave/surfaceFuelbedIntermediates.cpp), [reaction intensity](https://github.com/firelab/behave/blob/master/src/behave/surfaceFireReactionIntensity.cpp), [fuel catalog](https://github.com/firelab/behave/blob/master/src/behave/fuelModels.cpp), [ellipse implementation](https://github.com/firelab/behave/blob/master/src/behave/fireSize.cpp). Source-informed calculation variants and catalog values; pin revision when establishing the independent oracle.
3. Scott & Burgan, 2005, RMRS-GTR-153: [standard fuel model framework](https://research.fs.usda.gov/treesearch/9521). Additional catalog expansion reference.
4. Finney, RMRS-RP-4: [FARSITE development/evaluation](https://www.fs.usda.gov/rm/pubs/rmrs_rp004.pdf); [ellipse technical notes](https://owfflammaphelp62.firenet.gov/LegacyFARSITE/Tech_Elliptical_Dimensions.htm). Ellipse geometry and propagation context.
5. Rehm & McDermott, NIST TN 1611: [level-set fire-front framework](https://www.nist.gov/publications/fire-front-propagation-using-level-set-method).
6. Rust [workspace documentation](https://doc.rust-lang.org/cargo/reference/workspaces.html) and Rayon [bounded thread pool documentation](https://docs.rs/rayon/latest/rayon/struct.ThreadPoolBuilder.html).

This document specifies its own numerical discretization, masked-boundary policy, state replacement, interchange format, and demonstration ensemble preset. Those choices require the listed tests; citing a source does not validate the assembled engine.

## Appendix A — Concrete scalar expectations

These values are generated by the companion from this specification, not by an independent oracle. Moisture tuple is (0.05,0.07,0.09,0.60,0.90). No wind and flat terrain unless noted.

| Quantity | FM1 | FM2 | FM4 |
|---|---:|---:|---:|
| Characteristic SAV, ft⁻¹ | 3500 | 2784.016729706139 | 1739.2378306290284 |
| Packing beta | 0.0010625 | 0.00575 | 0.003833333333333333 |
| Reaction velocity, min⁻¹ | 14.201359906473266 | 15.897033163076461 | 14.155579218905377 |
| Reaction intensity, Btu/(ft² min) | 842.6651820057328 | 3625.4191568692754 | 12362.7718237753 |
| Heat sink, Btu/ft³ | 9.995229885125415 | 64.01525474217043 | 78.22245472881315 |
| R0, m/s | 0.024733996158492002 | 0.015289511177370011 | 0.025859989270422667 |

FM1 with 1 m/s midflame wind, flat: head spread 0.12490103558085179 m/s, LB 1.207692585557591 under the declared limit policy.

Reproduce from the project root:

```text
python3 docs/spec/reference_math.py
```

The full JSON includes weights, damping, packing, extinction, reaction, flux, and ellipse terms. The script includes assertions distinguishing radial and normal speed, terrain projection, curing, extinction, units, and CFL arithmetic.

## Appendix B — Auxiliary JSON contracts

All JSON rejects unknown enum values and wrong types. Unrecognized additional fields are rejected in schema version 1 (`serde(deny_unknown_fields)`); changes require a version increment. File paths are relative to their containing manifest; imports must remain within the bundle root. Catalog values use explicit customary-unit column names, while all raster arrays use the SI units specified above.

### B.1 Ensemble configuration

```json
{
  "schema_version": 1,
  "preset": "demo_global_bias_v1",
  "members": 32,
  "master_seed": 12345,
  "workers": 4,
  "memory_budget_bytes": 2147483648,
  "wind_bias_std_m_s": 1.0,
  "dead_moisture_log_std": 0.15,
  "live_moisture_log_std": 0.15,
  "front_bias_std_m": 0.0,
  "allow_partial": false
}
```

All standard deviations must be nonnegative; members/workers/memory must be positive. `preset` identifies uncalibrated demonstration semantics. Custom values remain uncalibrated until evaluated; do not retain the preset name after changing its declared defaults without recording an override map.

### B.2 Correction manifest

```json
{
  "schema_version": 1,
  "observation_id": "reviewed-perimeter-002",
  "observed_utc": "2026-01-01T00:05:00Z",
  "available_utc": "2026-01-01T00:06:00Z",
  "time_s_in_parent": 300.0,
  "grid_manifest": "corrected_input/manifest.json",
  "corrected_phi": "corrected_input/initial_phi.f64le",
  "policy": "replace_state_at_observation",
  "arrival_history": "reset_corrected_interior_to_unknown",
  "provenance": "reviewed perimeter, source revision 2"
}
```

`grid_manifest` supplies the complete new normalized bundle including future environmental inputs and forecast horizons; its grid must match the parent. Parent t_obs maps exactly to the new bundle's start UTC. If only a corrected phi is available without future weather coverage, fail with `MissingCoverage`.

### B.3 Truth manifest

```json
{
  "schema_version": 1,
  "incident_id": "incident-001",
  "grid_manifest": "input/manifest.json",
  "snapshots": [
    {
      "time_s": 1800.0,
      "burned": "truth/burned_001800.u8",
      "valid": "truth/valid_001800.u8",
      "acquisition_uncertainty_s": 60.0,
      "boundary_uncertainty_m": 30.0
    }
  ],
  "arrival": null
}
```

Binary truth is evaluated only at supplied matched times and on intersected valid masks. Optional `arrival` is an object with `time_s` (f64le array path) and `valid` (u8 path). For interval-valued truth use `lower_s`/`upper_s` and a validity mask instead of `time_s`; score distance from the predicted time to the interval, zero when inside, and label it interval error. Do not invent fine-time truth by interpolating distant perimeters without documenting that assumption.

### B.4 Replay and evaluation inputs

`RUNS.json`:

```json
{
  "schema_version": 1,
  "runs": [
    {"incident_id": "incident-001", "issue_utc": "2026-01-01T00:00:00Z", "directory": "runs/001"}
  ]
}
```

`TRUTH.json`:

```json
{
  "schema_version": 1,
  "incidents": [
    {"incident_id": "incident-001", "manifest": "truth/incident-001.json"}
  ]
}
```

`PROTOCOL.json`:

```json
{
  "schema_version": 1,
  "split": "development",
  "incident_ids": ["incident-001"],
  "horizons_s": [1800.0,3600.0],
  "baseline": "persistence",
  "mask_rule": "common_valid",
  "bootstrap_incident_replicates": 1000,
  "bootstrap_seed": 12345,
  "release_claim": "research_only"
}
```

Persistence uses the initial burned mask unchanged at every horizon. A missing matched truth horizon yields a missing metric with a reason, not a nearest-time substitution. Bootstrap samples whole incidents with replacement using the specified SplitMix generator, computes the same incident-aggregated statistic, and reports nearest-rank 2.5/97.5 percentiles. Do not use this small-sample interval to claim broad external validity.

Historical replay runner: for each issue time, the data team supplies or identifies a complete bundle containing only information available at that issue. The runner checks availability times before calling `run`, writes the issue-specific output, and links it in RUNS.json. If source availability metadata are absent, mark the run retrospective/unverified for leakage rather than calling it an operational replay.

### B.5 Completed run metadata

`run.json` must include:

```json
{
  "schema_version": 1,
  "status": "complete",
  "model": "surface_v1",
  "solver": "level_set_lf_euler_v1",
  "incident_id": "synthetic-fm1",
  "start_utc": "2026-01-01T00:00:00Z",
  "completed_until_s": 3600.0,
  "horizons_s": [300,600,1800,3600],
  "input_manifest_sha256": "<computed hash>",
  "input_artifacts": [],
  "build": {"git_commit": "<revision>", "rustc": "<version>", "target": "<target triple>"},
  "configuration": {},
  "quality_flags": [],
  "parent_run": null
}
```

Angle-bracket strings above describe runtime-generated values, not literal accepted hashes. `input_artifacts` entries are `{path,sha256,bytes}` for every referenced input artifact including catalog and frames. Hash original bytes, not an unspecified JSON reserialization. `configuration` stores the full simulation settings and model conventions. A run identifier is SHA-256 of the ordered list of input path/hash pairs plus the exact configuration/build manifest bytes; order paths lexicographically.

Boundary flags:

- `front_at_outer_boundary`: a sign-changing front cell is on the outer row/column, or a burned cell reaches that boundary.
- `front_near_invalid`: a front/burned cell has an invalid von Neumann neighbor.
- `front_near_blocked`: same for known nonburnable neighbor, distinct from invalid.
- `unsupported_surface_regime`: data team/domain review identifies crown/spotting or another excluded process; the core cannot infer all such regimes by itself.

## Appendix C — Implementation notes that prevent common errors

1. The letter phi is used in two contexts: `phi_w/phi_s` are dimensionless behavior factors; plain `phi` is the evolving level-set field in meters. Use distinct Rust names such as `wind_factor` and `level_set`.
2. In sections 6 and 7, R0/head rates start in ft/min. The ellipse and propagation equations require m/s. Put the conversion in a typed constructor and test it.
3. CSV model names FM1 and GR1 are different catalogs/IDs, not interchangeable vegetation labels.
4. Environmental frames change at exact times; reread fuel moisture and redo curing from original loads. Never repeatedly transfer already-transferred load.
5. Recomputing terrain gradients every simulation step is unnecessary; they change only when the landscape changes.
6. A field of high risk and a field of conditional burn probability are different products. This engine emits conditional spread results for an existing incident.
7. A head-fire intensity diagnostic is not an observed heat-release field and cannot by itself predict building loss.
8. Keeping the mesh resolution fixed while reducing dt does not remove spatial discretization error.
9. A complete input fixture plus the arithmetic companion makes engine development independent of live-feed availability. Real predictive validation still requires independent observations.

## Appendix D — Runnable specification aids and verification scope

The following helpers are supplied with this plan; none is the production Rust engine.

```text
python3 docs/spec/reference_math.py
python3 tools/make_spec_fixture.py --out fixtures/generated/fm1
python3 docs/spec/check_numerics.py --quick
python3 docs/spec/check_numerics.py --full
```

- The first command checks scalar/analytic arithmetic and writes 17 behavior cases.
- The second generates a complete normalized bundle and checksum inventory that the Rust importer can consume.
- The numerical checker implements a small, deliberately slow, standard-library Python version of the stated LF/Euler scheme for circle, ellipse, and changing-condition smoke tests. It isolates numerical design from the future Rust implementation. It is not an independent scientific oracle.
- Quick mode checks circle grids at 20/10 m and ellipse/changing-condition grids at 20 m. Full mode adds the 5 m circle and uses 10 m ellipse/changing-condition grids. It writes `fixtures/spec/numerical_smoke_results.json` with the mode and measured errors.

At plan preparation, the scalar checker, JSON examples, catalog uniqueness, and generated-bundle checksum checks passed. Quick numerical checks passed: circle maximum boundary error was approximately 6.59 m at 20 m cells and 3.10 m at 10 m cells; ellipse and changing-condition support errors at 20 m cells were approximately 7.44 m and 8.83 m. These confirm selected synthetic properties of the proposed discretization, not real-fire predictive accuracy.

Outstanding implementation verification remains: the Rust crates, complete masked-boundary and restart tests, independent Behave comparisons, historical evaluation, and performance/resource measurements. Do not describe these as already completed.


Full-resolution smoke checks also passed at plan preparation:

| Synthetic case | Cell size | Maximum geometry error |
|---|---:|---:|
| Circle | 20 m | 6.5911 m |
| Circle | 10 m | 3.0964 m |
| Circle | 5 m | 1.5080 m |
| Homothetic ellipse | 10 m | 3.5390 m |
| Changing conditions | 10 m | 4.2008 m |

These numbers are measurements from the included Python specification checker. They must be reproduced independently by the Rust implementation's corresponding tests.
