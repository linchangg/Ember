# 02: Physics: spread ensemble and filter (owner: Abby)

**Goal.** Implement `HazardModel` (`00_contracts` §6) in `engine/emberline/hazard/`. Inputs:
- a landscape
- hourly winds
- an ignition
- a stream of new `ObservationRecord`s

Every 5 sim-minutes it assimilates them into a 128-member ensemble and returns arrival-time quantiles for the next 6 h. It returns numpy arrays only; the engine owns files, contours, risk and publication.

**Reference:** `research/02_fire_model_filter.md`. It has the measured benchmarks, the verified `pyretechnics` API and the likelihood derivations.

**Also yours:**
- Pulling the DEM/LANDFIRE statics for the bbox (`01` §6, you're the consumer).
- `risk/metrics.py` after the 18:00 gate (`03` §9).

## 0. Setup

```
uv venv --python 3.12 && uv pip install pyretechnics numba numpy scipy rasterio shapely pyproj py3dep rioxarray pytest
```

- `pyretechnics` is sdist only, pins numpy 1.26, and needs a C compiler.
- **Use Python 3.12.** 3.13 tries to compile numpy.
- If the install blocks for more than 30 min, vendor the SB40 table (public-domain GTR-153 values) from its `fuel_models.py` or from FireBench.

```
hazard/api.py          # copy 00 §6 verbatim; MODEL_VERSION = "mtt-pf-1"
hazard/fake.py         # FakeHazardModel
hazard/fuel.py         # SB40 tables, moisture scenarios
hazard/mtt.py          # numba Dijkstra arrival-time solver
hazard/spotting.py     # P1, cuttable
hazard/ensemble.py     # priors, sampling, Liu-West jitter
hazard/likelihoods.py  # kind -> likelihood registry
hazard/filter.py       # pf / reinit / none
hazard/cdf.py          # weighted CDF -> quantiles, p_burned_now, region_arrivals
hazard/model.py        # EmberlineHazardModel
tests/test_hazard_*.py
```

**Loaders.** `emberline.data.load_landscape(scn) -> Landscape` (Cody, `01` §6; you can write it if you get there first) and `ForcingProvider.weather_as_of()` (`03` §3).

## 1. FakeHazardModel (by Sat 08:00)

- **Shape:** analytic ellipse from the ignition point.
  - Head direction: downwind of the current band's mean wind.
  - Head ROS 1.2 km/h, L:W 3.
- **Members:** **16 fixed**, ignoring `cfg.n_members`, with ±15% ROS and ±10° heading.
- **Returns** real `t_q`, uniform `weights`, `ess = 16`, and `filter_mode = "fake"`.
- **Implements** `region_arrivals` and `save_state`/`load_state` (`np.savez`).
- **Observations:** marks all of them `used`.
- **Purpose:** the engine can run end to end immediately. The fixture does **not** depend on it.

## 2. Fuel table (`fuel.py`)

- For SB40 codes 101–204, call pyretechnics once each:
  - `fm.moisturize(fm.get_fuel_model(code), moist)`
  - `sf.calc_surface_fire_behavior_no_wind_no_slope(...)`
  - Store `base_spread_rate` (m/min), `base_fireline_intensity`, `_phiS_G`, `_phiW_scalr`, `_phiW_expnt`, `_ws_scalr`, `_ws_expnt`, `max_effective_wind_speed`.
- **5 moisture scenarios:** 1-h dead fuel moisture ∈ {2, 3, 4, 6, 9}%.
  - 10h = 1h + 1 pp; 100h = 1h + 2 pp.
  - Live herbaceous 0.30; live woody 0.60.
  - Interpolate linearly in `dfm_1h` per member.
- Result: `FUEL[k_code, k_moist, field]` float32, plus `code_to_k`. NB92/93/98/99 have ROS 0. NB91 → §5.
- **Unit test:** the numba ROS combination matches `sf.calc_surface_fire_behavior_max(..., "behave")` within 2% for 3 models × 2 winds × 2 slopes.

## 3. Spread solver (`mtt.py`): minimum travel time, 16-neighbour stencil

Per member, a time-dependent Dijkstra. `T[r,c]` is float32 minutes since `t_ref`; `inf` = unburned.

**Main loop**
1. Pop the earliest unsettled cell `i`.
2. Band `b` = the hourly band containing `T[i]`. Clamp to the first or last band outside the range (`00` §6).
3. **Fire behavior at `i`**, computed lazily and cached per member per band:
   - Midflame wind `U = WAF·|wind|` in **m/min**. `WAF` = 0.4, or 0.2 if `canopy_cover > 0.5`.
   - Member wind: `|wind| = wind_speed_mult·|(u10, v10)|`; direction rotated by `wind_dir_bias_deg` (**clockwise positive**).
   - `φW = scalr·U^expnt` with U in m/min (pyretechnics convention).
   - `φS = phiS_G·slope²`. **`slope` is already rise/run (tan θ).**
   - Vector-add wind (toward downwind) and slope (toward upslope = aspect + 180°) to get `φE`.
   - `R_head = ros_mult·R0·(1 + |φE|)`; `β_head` = the direction of `φE`.
   - Effective wind `U_eff = ws_scalr·|φE|^ws_expnt`, capped at `max_effective_wind_speed`.
   - `L:W = min(8, 0.936e^{0.1147U} + 0.461e^{−0.0692U} − 0.397)`, with **U = U_eff converted to mph**.
   - `e = √(L²−1)/L`.
4. **Relax neighbours** `j` at offsets (0,±1), (±1,0), (±1,±1), (±1,±2), (±2,±1). For each, with edge length `d` and azimuth `β`:
   - `R(β) = R_head·(1−e)/(1−e·cos(β−β_head))`
   - Travel time `= d·½(1/R_i(β) + 1/R_j(β))`
   - **Knight-move edges** (±1,±2)/(±2,±1) also require both cells they cross to be burnable (R > 0). Firebreaks one cell thick still block.
   - Any `R` = 0 blocks the edge.
   - `T[j] = min(T[j], T[i] + tt)`
5. Stop when the popped `T > t_to`.

**Re-forecast from `t_now`.** Keep `T ≤ t_now`, set `T > t_now` to inf, seed the heap with burned cells that have an unburned neighbour, and clamp new arrivals to ≥ `t_now`.

**Ensemble.** `numba.prange` over members; `T` is `(N,H,W)` float32 (59 MB at 128×340²). Use `nogil=True` everywhere so the engine can call `step` from a thread.

**Performance target:** ≤ 0.5 s per cycle for N=128 at 340² on the demo laptop. Log the actual time in `ForecastArrays.duration_ms`.

**Tests**
- Uniform GR2, 10 m/s wind, flat: an ellipse with the expected L:W within 15%, head within 10°.
- A **1-cell-thick** NB98 water wall blocks spread.
- Upslope spreads faster than downslope.

## 4. Spotting (`spotting.py`): P1, first thing to cut

Inside the Dijkstra loop:
- When a cell pops with intensity `I > 2000 kW/m`, draw `n ~ Poisson(spot_rate·I/1000)` brands.
- For each brand:
  - distance `~ LogNormal(mean 400 m·(I/10⁴)^0.3·(U10/15)^0.7, σ_ln 0.8)`, capped at 3 km
  - heading = downwind + N(0, 10°)
  - if the landing cell is burnable or urban and unburned, ignite with probability `P_I·exp(−d/1000)` (Schroeder P_I from pyretechnics) at `T + 8 min`
- RNG: a counter-based hash `splitmix64(seed, member, cycle_index, cell, k)`.
- Label it "heuristic".

## 5. Urban cells (NB91): **P0, by Sat 15:00**

Area X and most Altadena zones are NB91. Without this, no fire reaches them.
- Treat NB91 as burnable with `ROS = urban_ros` (a per-member parameter).
- Elliptical with the local wind, L:W from the wind formula capped at 3.
- Intensity fixed at 5000 kW/m (for spotting).
- Optional, P1: multiply by `0.5 + building_frac`.
- The filter learns `urban_ros` from detections. It is labeled a "heuristic" for structure-to-structure spread.

## 6. Ensemble (`ensemble.py`)

| Parameter | Prior | Notes |
|---|---|---|
| `ros_mult` | LogN(0, 0.35) | model error |
| `wind_speed_mult` | LogN(0, 0.25) | HRRR bias |
| `wind_dir_bias_deg` | N(0, 15) | clockwise positive |
| `dfm_1h` | 4% + N(0, 1.5 pp), clipped to [2, 12]% | |
| `urban_ros` (m/min) | LogN(ln 1.0, 0.7) | NB91 |
| `spot_rate` | LogN(ln 0.02, 0.7) | P1 |
| ignition | uniform burnable cell within `radius_m` of the ignition point | |

- **Sizes:** N = 128 by default; 64 during development.
- **Liu-West jitter:** `a = 0.97`, in transformed (log) space.
- **RNG (branch-independent, by design):** `np.random.default_rng([cfg.seed, cycle_index])`, with `cycle_index = round(t_now_min / cycle_min)`. A branch then differs from its parent **only** by the injected observation, so the before/after diff is attributable.

## 7. Likelihoods (`likelihoods.py`)

**Registry.** `LIKELIHOODS: dict[ObsKind, type]`.
- Geometry → grid indices/weights are cached by `footprint_key`, or by a hash of the geometry JSON.
- Times: `t_obs = (observed_sim_ms/1000 − t_ref_s)/60`.

**Every likelihood**
- Computes `loglik(T (N,H,W), tau (H,W)) -> (N,)`.
- A cell is **active** at t if `0 ≤ t − T ≤ τ(fuel)`. τ: GR 15 min; GS/SH 45; TU/TL/SB 90; urban 180.
- Uses the trust mixture `L = (1−r) + r·L_sensor` with r = `rec.trust`.

| kind | Geometry → cells | `L_sensor` | Defaults |
|---|---|---|---|
| `goes_pixel` | Rasterize the corrected footprint ring; Gaussian blur σ 1 km → `S_p[c]` | `A = Σ S_p·active·cell_area`; `p_d = p_max(1−e^{−A/A0})`. Hot: `p_fa + (1−p_fa)p_d`. Not hot: `1 − that`. | p_max 0.85, A0 1 ha, p_fa 0.002 |
| `viirs_pixel` | Same; σ 200 m | Same (hot only) | p_max 0.95, A0 0.05 ha, p_fa 0.001 |
| `ground_report` | Point + radius r | `q = max_{\|c−x\|<2r} exp(−\|c−x\|²/2r²)·1[T(c) ≤ t_obs+10]`. `hot=True`: `ε+(1−ε)q`. `hot=False`: `1−q`. | ε 0.05; trust from the record (the engine applies `REPORT_HOT`) |
| `camera_bearing` (P2) | Per-cell bearing and range, FOV mask, range < 20 km | `q = max_{active, in FOV} exp(−½(Δβ/σ_β)²)`; `ε+(1−ε)q` | σ_β from the record |
| `thermal_footprint` | Polygon mask; σ 30 m | as VIIRS | interface only |

Cloud, DQF 2–5 and missing data never become observations (the data pipeline drops them).

## 8. Filter (`filter.py`): `cfg.filter ∈ {pf, reinit, none}`

```text
step(t_k, new_obs):
  history.add(new_obs); history.prune(older than t_k - history_min)
  ll = Σ_{o in new_obs} loglik_o(T, tau)                           # late obs evaluated at their own t_obs
  if pf:
      α = largest in (0,1] with ESS(normalize(logw + α·ll)) ≥ ess_target·N    # 20-step bisection; report α
      logw += α·ll ; normalize
      for o in strong(new_obs, history):
          if support(o) < 0.1:                                     # Σ_m w_m·1[member has fire within o's footprint/2r by t_obs+10]
              frac = 1.0 if o.trust >= 0.9 or o.kind != "ground_report" else o.trust
              re-anchor: in a random `frac` share of members (chosen with this cycle's rng), ignite the burnable
                         cell of o's footprint nearest that member's front at o.t_obs
      if best member mean per-obs ll < log(0.05): replace worst reseed_frac with reinit members (θ from posterior)
      if ESS < ess_target·N: systematic resample; Liu-West jitter; logw = 0
  elif reinit: re-seed every member from the observed burned mask M_t (below); θ from prior (or the elite 25% by last-hour ll)
  elif none: no update (open-loop baseline)
  T[T > t_k] = inf ; T = mtt_ensemble(T, θ, t_k, t_k + horizon)
  return cdf.summarize(T, w, t_k)
```

**`strong(o)`:**
- VIIRS with n/h confidence;
- GOES with `fdc_class ∈ GOES_STRONG` hot in this scan **and** the previous assimilated scan (`00` §3.1; classes 30–33 are the persistent codes); or
- any `ground_report` with `hot=True`. A citizen report re-anchors only a trust-weighted share, so one report can visibly move the forecast without taking it over.

**`reinit` mask `M_t`** (monotone):
- VIIRS footprints of the last 12 h
- GOES `GOES_STRONG` pixels hot in ≥ 2 of the last 3 scans, ∩ burnable/urban, ∩ within 2 km of the previous `M`
- trusted reports

Per member: dilate or erode by 0–1 cells; interior `T = t_now − 60`; boundary `T = t_now`. **If `M_t` is empty, fall back to the ignition prior.**

**Sat 18:00 gate (choose `pf` vs `reinit`).** Criteria, using **no data from after 20:15 PST** (`08` §2):
1. The twin experiment (§11.4) passes for `pf`.
2. On the Eaton replay **18:15–20:15 PST only:** Brier / hit rate of P at +1 h against the GOES `GOES_STRONG` hot pixels of the next hour, `pf` vs `reinit`.
3. Stability: no NaNs; ESS ≥ 0.2N on ≥ 80% of cycles.

Pick the better one; ties go to `reinit` (simpler). Tune priors on the same 2 h only. Then Cody runs the freeze (`03` §5). **Area X results are never used to choose or tune.**

## 9. Outputs (`cdf.py`)

- **Weighted CDF (numba):** per cell, histogram `T − t_issue` into 2-min bins over (0, 360] with weights `w`, then take the cumulative sum.
  - Quantile `q` = the **upper edge** `2·(b+1)` of the first bin where CDF ≥ q; NaN if never reached within the horizon.
  - Burned before issue → **0**.
- **`ForecastArrays`:**
  - `t_q (5,H,W)`
  - `p_burned_now = Σ w·1[T ≤ t_issue]`
  - `weights`, `ess`, `tempering_alpha`, `resampled`, `filter_mode`
  - `params_summary` (weighted mean/sd)
  - `obs_used`: one entry per new observation, `{obs_id, used, ll_spread = max(ll) − min(ll)}`
  - `duration_ms`
- **`region_arrivals(index, R)`:** for each member, the min of `T` over the cells of each region. Precompute the cell lists per index once.
- **Budget:** CDF + regions ≤ 0.2 s.

## 10. State and determinism

- `save_state()`: `np.savez_compressed` of θ, **`T` as float32**, `logw`, the observation history, cycle index and `MODEL_VERSION`. ~10–30 MB compressed.
- `load_state()` restores exactly. **Test:** `step` after a save→load round trip is bit-identical to `step` without it.
- The engine checkpoints every 30 sim-min and branches or recovers from those checkpoints (`03` §4, §10.2).

## 11. Tests

1. Fuel ROS vs pyretechnics within 2%.
2. Ellipse geometry, 1-cell water wall, upslope.
3. Determinism and save/load bit-identity. Same `(seed, cycle_index, inputs)` gives identical outputs regardless of the branch name.
4. **Twin experiment:**
   - Hidden truth: `wind_dir_bias +20°`, `ros_mult 1.6`, `urban_ros 2.0`.
   - Synthesize GOES-like observations (2.3×2.8 km pixels, 5-min cadence, classes 10 then 30 on repeats) and 2 VIIRS passes.
   - **Pass:** posterior `wind_dir_bias` within 8°, `ros_mult` within 25% after 2 h; ESS ≥ 0.2N on ≥ 80% of cycles; `pf` beats `none` on Sørensen at +2 h.
5. **Report effect:** a single hot report (trust 0.25) placed 1 km ahead of the ensemble's q50 front moves the q50 +2 h isochrone by ≥ 300 m on average. Measure it and report the number.
6. `region_arrivals` equals the brute-force min on a toy grid.

## 12. Build plan (hour 0 ≈ Sat 06:30)

| By | Deliverable |
|---|---|
| 08:00 | `FakeHazardModel` merged; `api.py` matches the contracts |
| 09:30 | DEM + LANDFIRE pulled and on the grid (`01` §6), with the NB91 fraction printed |
| 11:00 | Fuel table + single-member MTT; tests 1–2 green |
| 13:00 | Ensemble + CDF + `reinit` on real Eaton arrays; one sane forecast from ignition |
| **15:00** | **Urban NB91 spread (P0)**; GOES + VIIRS likelihoods; PF with tempering, resampling, jitter |
| 17:00 | Twin experiment passing; ground-report likelihood + re-anchoring; save/load |
| **18:00** | **Gate** (§8): pick `pf` or `reinit`; tune priors on 18:15–20:15 PST only; hand to Cody for the freeze (target 20:00) |
| 21:00 | `risk/metrics.py` (`03` §9) on the v1 caches; report-effect test 5 |
| 23:00 | Spotting (P1) only if everything else is green. Otherwise sleep. |

## 13. Limitations (README / Q&A)

- Surface-fire Rothermel under-predicts wind-driven WUI spread. Urban spread and spotting are heuristics learned from detections, and labeled.
- HRRR at 3 km misses canyon and downslope winds (WindNinja is roadmap).
- GOES pixels are ~2.3×2.8 km and only approximately parallax-corrected. The value is cadence, not resolution.
- Correlated GOES errors can make the PF over-confident. Tempering is the mitigation; we show ESS and α.
