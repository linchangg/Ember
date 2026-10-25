# 02: Fire spread model and data-assimilation filter (Abby's component)

Scope: spread engine, ensemble, particle filter, sensor likelihoods, compute budget, interface, build plan. Numbers marked **measured** were run in this sandbox (2 vCPU Xeon 2.1 GHz, Python 3.12, numba 0.68, numpy 1.26.4). Bench scripts: `scratchpad/bench/mtt_bench.py`, `pyre_bench.py`, `fueltab.py`.

## TL;DR
1. **Engine: a custom numba minimum-travel-time (Dijkstra) solver on a 16-neighbour stencil with elliptical (Anderson/Behave L:W) Rothermel spread.** It outputs a per-cell arrival-time raster directly. **Measured:** N=128 members, 333x333 @ 90 m, 6 h horizon: **0.32 s** on 2 vCPU. N=200, 500x500 @ 60 m, large fire: 2.1 s. Expect roughly 4x faster on an 8-core laptop.
2. **Physics source: `pyretechnics` (Spatial Informatics Group, EPL-2.0).** Use it for the Scott & Burgan 40 fuel table and the Rothermel coefficients (called from plain Python, verified), and as a test oracle. Do **not** use its level-set as the ensemble engine: 0.03–1.0 s per member, single thread (measured).
3. **Filter: a SIR particle filter.** State is the parameter vector plus each member's own arrival-time field. Add adaptive likelihood tempering (keeps ESS ≥ N/2), Liu-West parameter jitter, observation re-anchoring, and replacement of the worst 25% of members with members seeded from detections. Reject EnKF: binary hot/not-hot data does not fit Gaussian updates.
4. **Every sensor is `loglik(T[N,H,W]) -> (N,)`.** The arrival-time history is the only state a sensor reads. Late observations are therefore just evaluated at `t_obs`, with no rewinding.
5. **Fallback (decide Sat 6 PM): `filter="reinit"`.** It re-seeds every member from recent detections each cycle and keeps the same interface and outputs.
6. **Precompute the full replay offline and cache it.** Run live only for judge-injected observations: load the nearest checkpoint, then step forward at under 1 s per cycle.

## 1. Building blocks evaluated

| Candidate | Lang / license | Maintained | Computes | Install | Speed | Verdict |
|---|---|---|---|---|---|---|
| **pyretechnics** 2026.10.20 | Python+Cython, EPL-2.0 | Yes. 8 releases 2025–26, latest Oct 20 2026 | 58 fuel models (Anderson 13, SB40, NB91–99). Rothermel surface fire, crown fire, spotting (lognormal jump, Schroeder ignition probability, Perryman survival), ELMFIRE-style level set returning `time_of_arrival`, suppression | **sdist only.** Pins `numpy~=1.26.2`, `scipy~=1.12`, `rasterio~=1.4.3`, Cython. Built in 1.6 min on **Py 3.12 (verified)**. On Py 3.13 pip starts compiling numpy 1.26 from source (observed, killed). **Use Python 3.11/3.12** and a C compiler (Xcode CLT on Mac) | **Measured** level set, 1 member, 6 h: 0.03 s (GR2, 30 km/h) to 0.38 s (SH5, 50 km/h) at 333² @ 90 m; 0.99 s at 500² @ 60 m | **Use for fuel table, Rothermel coefficients, test oracle, and synthetic-truth runs** |
| ELMFIRE | Fortran, EPL-2.0 | Active; 138 commits in the year to Jun 2025 | Level set, crown, spotting. Runs the operational Pyrecast forecasts | Fortran/Docker, namelist + GeoTIFF I/O | Fast, but runs as a subprocess with file I/O | Skip: integration cost |
| FARSITE (mbedward/farsite port) | C++, license not shown | 13 commits; tested on Ubuntu 16/18 | Huygens perimeters, spotting | `make`, LCP inputs | n/a | Skip |
| FlamMap MTT | USFS desktop | — | MTT arrival time with constant weather | Windows | — | Skip. Confirms MTT is the operational analogue ("in theory identical to wave-front expansion used in FARSITE") |
| ForeFire 2.5.0 | C++ + `pyforefire`, **GPL-3.0** | Active (pushed Oct 21 2026) | Lagrangian front, pluggable ROS models (Rothermel availability unverified) | `pip install forefire` wheels for Linux/macOS-arm64, py3.9–3.14 | unverified | Skip: GPL, learning curve |
| Cell2Fire | C++ core + Python, GPL-3.0 | ~1 yr since last push | Cell-based elliptical spread, Canadian FBP | g++, Boost, Eigen | Fast | Skip: FBP fuels, not SB40 |
| JaxWildfire | JAX, license "aim to release" | Preprint Dec 2025 | Probabilistic CA (Alexandridis); not Rothermel; arrival-time output not mentioned | — | GPU vmap | Skip: not physically calibrated |
| FireBench | Python | — | `import_scott_burgan_40_fuel_model()` table with Pint units | pip | — | Backup SB40 table source |
| Python "firelib" ports | — | No maintained package found (limited search, unverified) | — | — | — | — |

**Verified API usage** (pyretechnics, plain Python):
```python
import pyretechnics.fuel_models as fm, pyretechnics.surface_fire as sf
mf  = fm.moisturize(fm.get_fuel_model(145), [0.04,0.05,0.07,0.04,0.6,0.8])  # 1h,10h,100h,dead-herb,live-herb,live-woody
mn  = sf.calc_surface_fire_behavior_no_wind_no_slope(mf)
# -> {'base_spread_rate': 0.866 m/min, 'base_fireline_intensity': 281 kW/m, 'max_effective_wind_speed': 1533 m/min,
#     '_phiS_G': 33.75, '_phiW_scalr': 0.075, '_phiW_expnt': 1.189, '_ws_scalr': 8.83, '_ws_expnt': 0.841}
mx  = sf.calc_surface_fire_behavior_max(mn, midflame_m_per_min, upwind_deg, slope, aspect, True, "behave")
# -> max_spread_rate, max_spread_direction (3-vector), max_fireline_intensity, length_to_width_ratio, eccentricity
```
These are per-fuel-model scalars. Build a lookup table `FUEL[fm_code, moisture_scenario]` once, then re-implement the vector combination in numba:
- φW = scalr·U_mid^expnt
- φS = phiS_G·tan²(slope)
- Add the wind and slope vectors; R = R0(1+|φE|)
- U_eff = ws_scalr·|φE|^ws_expnt, capped at max_effective_wind_speed
- L:W = min(8, 0.936e^{0.1147U} + 0.461e^{−0.0692U} − 0.397), with U the effective wind speed in mph (Behave form, from pyretechnics source)
- e = √(L²−1)/L

Validate the numba kernel against `calc_surface_fire_behavior_max` in a unit test.

## 2. Spread algorithm

| Option | Arrival raster | Ensemble speed | Anisotropy / accuracy | Effort |
|---|---|---|---|---|
| Cellular automaton (stochastic) | Only via time stepping | Fast on GPU | Poor calibration; dt-quantised | Low |
| **MTT / Dijkstra + ellipse** | **Native** | **ms per member** | Good with 16 neighbours; small angular bias | **Low–medium (~150 lines)** |
| Fast marching (`scikit-fmm`) | Native | Fast | Isotropic speed only, so the ellipse needs an anisotropic solver | Medium–high |
| Level set (pyretechnics/ELMFIRE) | Native | 0.03–1 s per member (measured) | Best | Low to call, slow for N≥100 |

**Recommendation: time-dependent Dijkstra.** Pop the cell with the smallest arrival time, then relax its 16 neighbours. The travel time over an edge of length d in azimuth β is
`d·½(1/R_i(β)+1/R_j(β))`, where `R(β) = R_head·(1−e)/(1−e·cos(β−β_head))` (polar ellipse from the rear focus). R_head, β_head and e come from the weather band at the popped cell's arrival time (hourly HRRR bands; approximately FIFO).

Evaluate Rothermel **lazily**, only for cells the fire reaches. Non-burnable cells (R=0) block spread. On re-forecast, seed with the front cells of the member's burned set at t_now and clamp new arrivals to ≥ t_now. **Measured:** 1 ms per ~1k burned cells per member.

## 3. Ember spotting and urban cells

**Spotting** (1–2 h, inside the Dijkstra loop; this keeps arrival-time ordering correct):
- When a cell c pops with intensity I_c > 2000 kW/m: `n ~ Poisson(spot_rate·I_c/1000)`.
- For each brand:
  - distance `d ~ LogNormal` with mean `400 m·(I/10⁴)^0.3·(U10/15 m/s)^0.7`, σ_ln = 0.8, capped at 3 km
  - heading = downwind + N(0, 10°)
  - landing cell must be burnable or urban and still unburned
  - ignite with probability `P_I·exp(−d/decay)`: Schroeder P_I from temperature and 1-h moisture, decay 1 km (pyretechnics has both formulas)
- On ignition, push `(T_c + 8 min, landing)` onto the heap.
- Use a counter-hash RNG (splitmix64 of seed, member, cell) so prange runs are reproducible.
- All coefficients are tunable and unverified for Eaton. The pyretechnics `spot_config` uses the same lognormal structure (mean ∝ I^a·U^b, variance/mean ratio).

**Urban (LANDFIRE NB91):**
- Treat NB91 as burnable, with ROS = `urban_ros` (ensemble parameter, LogN(median 1.0 m/min, σ_ln 0.7); unverified prior). It spreads elliptically with the local wind; intensity is fixed at 5000 kW/m for ember production; active window τ = 180 min.
- Optionally scale by building fraction (Microsoft footprints) so dense blocks spread faster than parks.
- The filter learns `urban_ros` from detections. Pitch line: "the satellite tells us how fast it's moving house to house."
- NB92/93/98/99 (snow, agriculture, water, barren) stay non-burnable.
- Embers may land in urban cells.
- Literature (NIST, UMD ELMFIRE-WUI thesis) models structure-to-structure spread and ember ignition explicitly. That is out of scope here; say so.

## 4. Ensemble design (defaults)

| Parameter (per member) | Prior | Notes |
|---|---|---|
| `ros_mult` | LogN(0, 0.35) | Global Rothermel model error |
| `wind_speed_mult` | LogN(0, 0.25) | HRRR bias |
| `wind_dir_bias` | N(0, 15°) | Santa Ana/terrain channelling; unverified size |
| `dfm_1h` | 4% + N(0, 1.5 pp), clipped to 2–12% | Interpolated over 5 precomputed moisture scenarios. 10h/100h = 1h + 1/3 pp; live herb 0.3 (cured), woody 0.6 |
| `urban_ros` | LogN(ln 1.0 m/min, 0.7) | NB91 cells |
| `spot_rate` | LogN(ln 0.02, 0.7) | Brands per cell per 1000 kW/m |
| Ignition | Uniform burnable cell inside first GOES pixel, or reported point + N(0, 300 m) | |

Defaults: **N=128** (64 for development, 256 if time allows). Sample in log space. Liu-West jitter with a=0.97 (shrink toward the mean plus N(0, (1−a²)·Cov)). Wind error is held constant per member and evolves only by jitter. That is enough for 6 h.

## 5. Filter

| | Particle filter (recommended) | EnKF on arrival time | Re-init from observed front |
|---|---|---|---|
| Binary or bearing data | Native, any likelihood | Must convert to Gaussian arrival-time observations | Heuristic mask only |
| Learns wind/ROS bias | Yes (parameters in the state) | Yes, if augmented | No |
| Physical consistency | Each member is a real simulation | Updates give non-physical fields (needs smoothing) | Yes |
| Failure mode | Collapse (fixed by tempering, jitter, re-seeding) | Localization/inflation tuning | No learning |
| Effort | ~1 day, with simple code | Highest | ~2 h |

**Recommendation:** SIR particle filter with adaptive tempering, plus re-anchoring and re-seeding. The re-init path is both the re-seeding mechanism and the fallback.

**Common observation model.** A cell is *active* at time t if `0 ≤ t − T(c) ≤ τ(fuel)`. Defaults for τ:
- GR: 15 min
- GS/SH: 45 min
- TU/TL/TL: 90 min
- urban: 180 min

All likelihoods use a trust mixture, `L = (1−r) + r·L_sensor`, where r is the per-class or per-reporter reliability. This replaces outlier handling.

**Likelihood per sensor:**
- **GOES pixel** (~2 km; precompute a sparse matrix S from pixels to cells. The fixed ABI grid means S is constant for the whole fire).
  - Active area `A_m = S_p · active_m · cell_area`, with a Gaussian footprint blur of σ ≈ 1 km.
  - ABI navigation is not terrain-corrected and can shift fires by up to several km. Better: map each cell to its *apparent* pixel using elevation and view geometry (unverified geometry; verify).
  - `p_d(A) = p_max(1−e^{−A/A0})`, with p_max 0.85 and A0 = 1 ha.
  - Hot: `L = p_fa + (1−p_fa)p_d`. Not-hot (clear): `1 − that`. p_fa = 0.002.
  - Cloud or unprocessed pixel: no term at all. **Never treat cloud as negative evidence.**
  - FDC class reliability r: 10/11/30/31 → 0.95, 12/13/32/33 → 0.85, 14/34 → 0.6, 15/35 → 0.3. Hall et al. found low-probability classes were 56–95% false alarms.
  - Correlated-error caveat: GOES re-observes the same pixels every 5 min. Without tempering this collapses the ensemble.
- **VIIRS 375 m.** Same model with a per-detection footprint (383x360 m at nadir, up to 795x784 m at scan edge), σ = 200 m, p_max 0.95, A0 = 0.05 ha, p_fa 0.001.
  - FIRMS CSV gives detections only.
  - For non-detections inside the overpass swath, use `L = 0.3 + 0.7·(1−p_d)`: cloud state is unknown, so negative evidence is weak.
- **Camera bearing.** Precompute per-cell bearing, range and in-FOV mask (optional DEM viewshed).
  - `q_m = max_{c active, visible, range<20 km} exp(−½(Δβ_c/σ_β)²)`, with σ_β = 2° for flame and 5° for smoke.
  - Detection: `L = ε + (1−ε)q_m`, ε = 0.05.
  - "No fire in FOV": `L = 1 − 0.8·max_c(visible active)`.
  - `t_obs = t_cam + clock_offset[cam]`.
  - Two cameras triangulate automatically because their likelihoods multiply.
- **Ground report** (point x, radius r, trust w).
  - `q_m = max_{c: |c−x|<2r} exp(−|c−x|²/2r²)·1[T_m(c) ≤ t_obs+10 min]`
  - `L = (1−w) + w(ε+(1−ε)q_m)`
  - "No fire here" reports use `1−q_m`.
  - Default w: citizen 0.5, responder 0.9.
- **Drone/heli footprint.** Polygon converted to a cell mask; same as VIIRS with σ = 30 m. Interface only.

**Time handling.**
- Every observation carries `t_obs` (sensor time) and `t_avail` (latency).
- The replay clock releases an observation only when `now ≥ t_avail`. FIRMS latency is realistic replay-as-live behaviour.
- Members keep their full arrival history, so an observation that arrives late is still evaluated at its own `t_obs`.
- Store all times as **float32 minutes since `t_ref`**. Float32 epoch seconds only resolve to about 2 min.

**Tempering.** Choose the largest α ∈ (0,1] such that ESS(w·L^α) ≥ 0.5N (20-step bisection). Report α as an honesty and diagnostic signal.

**Re-anchoring and re-seeding.**
- A strong detection (VIIRS, GOES class 10–13 seen in 2 consecutive scans, or a trusted report) whose weighted support Σw_m·1[p_d,m > 0.2] is below 0.1 counts as "unexplained" (spot fire or model miss). Ignite the burnable cell in its footprint nearest each member's front, at `t_obs`, in every member.
- If the best member's per-observation mean log-likelihood falls below log(0.05), replace the worst 25% with members re-initialised from the detection mask, drawing parameters from the current posterior.

## 6. Compute budget

**Measured, this sandbox (2 vCPU):**

| Config | Time |
|---|---|
| Numba MTT, 1 member, 333² @ 90 m, 6 h | 1–8 ms |
| N=128, 333² @ 90 m | 0.32 s |
| N=200, 333² @ 90 m (~9.8k burned cells per member) | 0.83 s |
| N=200, 500² @ 60 m (~26k burned cells per member) | 2.1 s |
| Probability rasters, numpy, 12 thresholds | 0.2–0.6 s (do this in numba instead, below) |
| Pyretechnics level set, 1 member | 0.03–1.0 s |

**Estimates (unverified):**
- 8–10-core laptop: 3–5x faster, so about 0.1–0.5 s per cycle at the defaults, plus about 0.2 s to summarise and contour.
- Memory: (128, 333, 333) float32 = 57 MB.
- **GPU is not useful.** Dijkstra is serial per member. Members parallelise across CPU cores, so a Vultr *CPU-optimised* 16–32 vCPU instance scales roughly linearly.
- A GPU port would mean an iterative Bellman-Ford/fast-sweeping rewrite in JAX. Not worth it in 20 h.
- If you want the Vultr prize, run the batch replay precompute there.

**Defaults:**
- Domain 30x30 km at **90 m (333x333)**
- N = **128**
- Horizon **360 min**
- Cycle **5 sim-min** (GOES cadence)
- Outputs every 30 min; internal CDF bins every 10 min
- Upgrade to 60 m only after everything works.

**Demo clock** (1 h of fire per 10–20 s): a 5-min cycle gets 0.8–1.7 s of real time. A laptop can keep up at the defaults, but do not depend on it.
- **Precompute:**
  - landscape and fuel table
  - GOES S-matrix and parallax map
  - camera geometry
  - HRRR bands
  - **the whole baseline replay** (all cycles → cached snapshots, plus full particle checkpoints every 30 sim-min and just before each planned injection time)
- **Live:** judge injection. Load the checkpoint, add the observation, step forward 1–6 cycles at under 1 s each, and serve a "live branch".

## 7. Outputs and validation

- **Weighted CDF kernel (numba).** Histogram each member's arrival into 10-min bins with its weight, then cumulative-sum. This gives `P(T ≤ t_issue+lead)` for every lead and any quantile (q10/q50/q90) by interpolation in one pass.
- **Act-by times.** Give the risk layer **q10 (early) arrival** as the conservative act-by input, plus q50.
- **Contours.** Use `rasterio.features.shapes` on P ≥ {0.1, 0.5, 0.9} masks per lead → GeoJSON in the grid CRS.
- **Spread display.** Show ensemble spread as q90−q10 arrival (minutes), plus 8 "spaghetti" member perimeters.

**Metrics** (fix thresholds before replay):
- **Lead time for area X** = t(first satellite detection in X) − min{t_k : forecast issued at t_k has P(arrival in X within horizon) > 0.5}. Forecasts at t_k use only observations available by t_k, which the replay clock enforces.
- **Sørensen** = 2|A∩B|/(|A|+|B|) and **Jaccard** = |A∩B|/|A∪B|. Compare the P ≥ 0.5 region at t with official/IR perimeters (NIFC/FIRIS; Cody's ingest).
- **Brier score** of P at +1 h and +3 h against later detections.
- **Baselines:**
  - persistence (the fire stays where it is)
  - an unfiltered ensemble

  Showing that the filter beats both is the honest headline.

## 8. Fallback mode (switch if the PF isn't healthy by Sat 6 PM)

`cfg.filter = "reinit"`:
1. Each cycle, build the observed burned mask M_t from:
   - VIIRS footprints over the last 12 h
   - GOES pixels hot in at least 2 of the last 3 scans, intersected with burnable cells within 2 km of the previous M
   - trusted ground reports

   M is monotone: once burned, always burned.
2. For each member, dilate or erode M by 0–1 cells at random, set T = t_now on its boundary, and draw parameters from the prior. Optionally use "elite" parameters: the top 25% by last-hour log-likelihood, which is cheap calibration.
3. Run the MTT forecast.

Same interface and snapshot. The UI shows `filter_mode="reinit"`. It is still "updates every time a satellite looks".

## 9. Cycle pseudocode

```text
init(land, weather, cfg, ignition):
  θ[N,P] ~ prior;  T[N,H,W] = +inf;  logw[N] = 0
  for m: T[m, sample_ignition(ignition)] = t_ign
  T = mtt_ensemble(T, θ, t_from=t0, t_to=t0+H)          # predict

step(t_k, new_obs):                                    # called every cycle_min of sim time
  obs = [o for o in new_obs if o.t_avail <= t_k and o.t_obs > t_k - H]
  ll  = Σ_o o.loglik(T)                                 # (N,) vectorised over members
  α   = max α∈(0,1] s.t. ESS(logw + α·ll) ≥ 0.5N       # tempering
  logw += α·ll ; normalise
  for o in strong(obs): if support(o, T, w) < 0.1: ignite(T, o.footprint, o.t_obs)   # re-anchor
  if mean_best_ll < log(0.05): replace worst 25% with reinit_members(detections, θ posterior)
  if ESS(w) < 0.5N: idx = systematic_resample(w); θ, T = θ[idx], T[idx]; logw[:] = 0
  θ = liu_west_jitter(θ, a=0.97)
  T[T > t_k] = +inf                                     # keep history only
  T = mtt_ensemble(T, θ, t_from=t_k, t_to=t_k+H)        # forecast (numba prange over members)
  return summarise(T, w, t_k)                           # ForecastSnapshot
```

## 10. Interface (backend calls this; `ember/hazard/api.py`)

```python
from dataclasses import dataclass, field
from typing import Protocol, Literal
import numpy as np

@dataclass(frozen=True)
class Grid:
    crs: str                 # projected metres, e.g. "EPSG:32611"; row 0 = north
    x0: float; y0: float     # upper-left corner (m)
    cell_m: float            # 90.0
    H: int; W: int
    t_ref: int               # epoch s; ALL model times = float32 minutes since t_ref

@dataclass
class Landscape:
    grid: Grid
    fuel: np.ndarray         # (H,W) int16 LANDFIRE FBFM40 code (91 urban, 98 water, ...)
    slope: np.ndarray        # (H,W) float32 rise/run
    aspect: np.ndarray       # (H,W) float32 deg cw from N (downslope)
    elev: np.ndarray         # (H,W) float32 m
    canopy_cover: np.ndarray | None = None    # (H,W) float32 0-1
    canopy_height: np.ndarray | None = None   # (H,W) float32 m
    building_frac: np.ndarray | None = None   # (H,W) float32 0-1

@dataclass
class Weather:
    t_min: np.ndarray        # (B,) float32 band start (min since t_ref), hourly
    u10: np.ndarray          # (B,H,W) float32 m/s east;  v10 likewise north
    v10: np.ndarray
    temp_c: np.ndarray       # (B,) float32
    rh: np.ndarray           # (B,) float32 0-1

@dataclass
class Ignition:
    t_min: float
    points_xy: np.ndarray    # (K,2) float64 grid-CRS metres
    radius_m: float = 300.0

class Observation(Protocol):
    obs_id: str
    sensor: Literal["goes", "viirs", "camera", "ground", "footprint"]
    t_obs_min: float         # clock-offset corrected sensor time
    t_avail_min: float       # earliest time the system may see it
    trust: float             # r in L = (1-r) + r*L_sensor
    def prepare(self, land: Landscape) -> None: ...                 # cache footprint idx/weights
    def loglik(self, T: np.ndarray, tau: np.ndarray) -> np.ndarray: ...
        # T (N,H,W) float32 arrival min (inf = unburned); tau (H,W) float32 active window -> (N,) float64

@dataclass
class ModelConfig:
    n_members: int = 128
    horizon_min: int = 360
    out_step_min: int = 30
    cycle_min: int = 5
    filter: Literal["pf", "reinit", "none"] = "pf"
    ess_target: float = 0.5
    jitter_a: float = 0.97
    reseed_frac: float = 0.25
    spotting: bool = True
    seed: int = 7

@dataclass
class ForecastSnapshot:
    t_issue_min: float
    lead_min: np.ndarray     # (S,) int16 [30..360]
    p_arrival: np.ndarray    # (S,H,W) uint8 = round(255*P(T <= t_issue+lead))
    p_burned_now: np.ndarray # (H,W) uint8
    t_q10: np.ndarray        # (H,W) float32 min after t_issue, NaN beyond horizon
    t_q50: np.ndarray
    t_q90: np.ndarray
    contours: dict           # {"p10"|"p50"|"p90": {lead: GeoJSON MultiPolygon}}
    ess: float
    tempering_alpha: float
    filter_mode: str
    params_summary: dict     # {"ros_mult": (mean, sd), "wind_dir_bias_deg": ..., ...}
    obs_influence: list = field(default_factory=list)   # [{obs_id, sensor, ll_spread, used}]

class HazardModel(Protocol):
    def init(self, land: Landscape, weather: Weather, cfg: ModelConfig, ign: Ignition) -> None: ...
    def update_weather(self, weather: Weather) -> None: ...
    def step(self, t_now_min: float, obs: list[Observation]) -> ForecastSnapshot: ...  # assimilate+forecast
    def save_state(self) -> bytes: ...       # θ, T, logw, rng counters (np.savez, ~60 MB)
    def load_state(self, blob: bytes) -> None: ...
```

## 11. Staged build plan for Abby (~20 h; hour 0 ≈ Sat 5 AM)

- **Hour 0–1:** `FakeHazardModel` returning an analytic growing ellipse in the exact `ForecastSnapshot` format. This unblocks Cody and Linda.
- **By hour 4:**
  - fuel table from pyretechnics (Py 3.12 venv)
  - numba elliptical MTT, one member, time-varying wind bands, synthetic landscape
  - unit tests: (a) uniform fuel and wind give an ellipse with the expected L:W, (b) ROS matches `calc_surface_fire_behavior_max`
  - real Eaton-area arrays from Cody plugged into one deterministic forecast
  - P rasters and contours (N=1)
- **By hour 10:**
  - ensemble with prange and priors
  - CDF/quantile kernel
  - **reinit fallback** (it is also the initializer)
  - GOES and VIIRS likelihoods
  - PF loop: tempering, resampling, jitter
  - **twin experiment:** pyretechnics or MTT "truth" run with a hidden wind bias → synthetic GOES/VIIRS → check the filter recovers the bias and ESS stays healthy
- **By hour 16:**
  - camera and ground likelihoods
  - re-anchoring and re-seeding
  - spotting and urban
  - save/load state
  - full Eaton replay precompute plus metrics (lead time, Sørensen, Brier, baselines)
  - **Sat 6 PM gate:** if the PF doesn't beat reinit on lead time or Sørensen, ship `filter="reinit"`
- **Hour 16–20:**
  - tune priors on the *first hours only* (avoid leakage)
  - freeze alert thresholds
  - build demo injection checkpoints
  - check performance at 60 m (optional)
  - write README/limitations

## 12. Biggest risks
1. **Rothermel/SB40 badly under-predicts wind-driven WUI spread (Eaton).** The forecast is wrong until the filter learns `ros_mult` and `urban_ros`. Mitigations: wide priors, re-anchoring, and saying so on stage.
2. **GOES 2 km pixels plus parallax (several km) cannot resolve the front.** VIIRS passes are sparse. Keep footprint blur and tempering conservative, and show the uncertainty.
3. **PF collapse or over-confidence.** Mitigations: tempering, jitter, re-seeding, and the fallback mode.
4. **HRRR 3 km winds miss canyon and downslope wind effects.** WindNinja is roadmap.
5. **Install friction.** Pyretechnics needs Python ≤3.12 and a compiler. If that blocks, vendor the SB40 table (public-domain GTR values) from its `fuel_models.py` or from FireBench.

## Sources
- pyretechnics on PyPI and its source (fuel_models.py, surface_fire.py, spot_fire.py, eulerian_level_set.py inspected): https://pypi.org/project/pyretechnics ; docs https://pyregence.github.io/pyretechnics/ ; repo https://github.com/pyregence/pyretechnics/
- ELMFIRE: https://ost.ecosyste.ms/projects/175594 ; https://elmfire.io ; https://github.com/lautenberger/elmfire
- ForeFire: https://pypi.org/project/forefire/ ; https://ost.ecosyste.ms/projects/175588
- Cell2Fire: https://science.ecosyste.ms/projects/75955 ; https://github.com/cell2fire/cell2fire
- FARSITE port: https://github.com/mbedward/farsite
- FlamMap MTT: https://owfflammaphelp62.firenet.gov/Tech_Topics/Tech_MTT.htm ; Finney 2002 (unverified link) https://research.fs.usda.gov/treesearch/25940
- JaxWildfire: https://arxiv.org/html/2512.06102v1
- FireBench SB40: https://firebench.readthedocs.io/en/stable/fire_models_info/21_fuel_models/03_SB40.html
- Satellite active-fire assimilation (logistic detection likelihood, geolocation σ, coverage gaps): https://arxiv.org/pdf/2204.00686 ; WRF-SFIRE detection assimilation https://arxiv.org/pdf/1410.6948
- GOES ABI FDC validation (class false-alarm rates, ~0.1% pixel-area detectability, no terrain correction): https://bsosdev1.umd.edu/sites/geog.umd.edu/files/pubs/Geostationary%20active%20fire%20products%20validation%20GOES%2017%20ABI%20GOES%2016%20ABI%20and%20Himawari%20AHI.pdf ; https://www.frames.gov/catalog/58459
- NGFS: https://bin.ssec.wisc.edu/pub/volcat/ngfs_docs/NGFS_README_Feb2025.pdf ; https://support.watchduty.org/hc/en-us/articles/47896549294093-Next-Generation-Fire-System-NGFS-Heat-Detections
- VIIRS 375 m (pixel sizes, commission): https://directreadout.sci.gsfc.nasa.gov/publications_documents/Schroeder_et_al_2014b_RSE.pdf
- Eaton elliptical-frame study (no measured ROS published): https://arxiv.org/pdf/2512.10189
- WUI spread: NIST IR 8436 https://nvlpubs.nist.gov/nistpubs/ir/2022/NIST.IR.8436.pdf ; ELMFIRE-WUI thesis https://drum.lib.umd.edu/items/b9c8055d-84a2-4545-83ca-c1b16c0dee78
- Unverified (own judgement or standard practice, not checked against a primary source): all prior widths, τ windows, A0/p_max/p_fa values, urban ROS, spotting coefficients, GOES parallax mapping geometry, laptop and Vultr speed-ups.
