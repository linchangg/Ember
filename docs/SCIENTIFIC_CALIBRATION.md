# Scientific calibration: evidence, equations and controlled experiments

Reviewed 2026-10-24 against the original `research/02_fire_model_filter.md` sources
and the merged `rust-levelset-pf-1` code. The research notes contain proposals and
unverified priors; they are not all established measurements for the Rust engine.

## What should improve first

Distinguish numerical error, forcing bias, sensor likelihood error and unidentifiable
parameters. A better fit to an assimilated outline alone does not demonstrate
forecast accuracy or recovery of true wind/moisture/ROS. Current synthetic wind/ROS
recovery fails. Treat additional equations as testable hypotheses, not accuracy promises.

### 1. Heat decay plus sensor location uncertainty — candidate implemented

Haley (2022), §2.3.3, equations 2.3.8–2.3.16, combines heat decay, logistic detection
and Gaussian location uncertainty. Using arrival time T and sensor time t:

```
h(t,T) = 1[t >= T] exp(-(t-T)/tau)
p(d=1 | T, x) = integral K_Sigma(x-y) sigmoid(a*h(t,T(y))-b) dy
sigmoid(z) = 1/(1+exp(-z))
p(d=0 | T, x) = 1-p(d=1 | T,x)
```

Our implementation discretizes the integral as normalized spatial weights; allowing
anisotropic covariance is an explicit extension. The `calibration.py` functions are
isolated candidates, not the default filter. Parameters a, b, tau and Sigma require
sensor-specific fitting; missing/cloudy pixels are not negative evidence. The current
model instead uses hard active-fire windows and an active-area response. Compare the
alternatives with the same inputs before choosing. Do not transfer MODIS parameter
values directly into GOES/VIIRS. [Original thesis](https://arxiv.org/html/2204.00686v1).

Fit using calibration incidents only: minimize held-out Bernoulli negative log
likelihood `-sum[y log(p)+(1-y)log(1-p)]`, with blocked overpass/time folds. Include
coverage-verified clear pixels and positive detections; detections-only CSVs cannot
identify false-negative rates. Estimate precision/recall, reliability and Brier score
on separate incidents. Renormalized kernels near domain edges condition on the modeled
domain: flag/clamp that experiment to interior footprints or account for missing mass.

### 2. Replace the coarse wind adjustment — next controlled physics experiment

`hazard/bridge.py` currently multiplies 10 m wind by 0.2 or 0.4 based on canopy
cover. It does not use canopy height or fuel-bed depth. Pyretechnics documents
`U20 = U10/1.15`, then `Umid = U20*WAF`, with sheltered/unsheltered WAF depending
on canopy and fuel depth. For unsheltered fuel depth d in feet:

`WAF = 1.83 / log((20 + 0.36*d)/(0.13*d))`.

Port the complete source branch conditions and unit conversion, including canopy
height and zero-depth/nonburnable cases; do not combine this formula with metre-valued
d. Compare independently against the pinned oracle, then run held-out forecasts.
This is proposed, not activated. [Source equations and implementation](https://pyregence.github.io/pyretechnics/#midflame-wind-speed).

### 3. Reduce level-set numerical diffusion — proposed, not activated

Current Rust uses LF flux and forward Euler. A candidate SSP-RK2 step is
`phi1=phi+dt*L(phi)`; `phi_next=.5*phi+.5*(phi1+dt*L(phi1))`.
Test flux-limited spatial derivatives with this time integrator; RK2 alone does not
remove first-order spatial diffusion. Recompute speed/CFL bounds correctly and retain
barriers, arrival interpolation, irreversibility and weather transitions. Compare
convergence, not just outline appearance. [Pyretechnics numerical method](https://pyregence.github.io/pyretechnics/#numerical-solution-of-the-phi-field-equation).

### 4. Smooth arrival correction — alternative research assimilation

Mandel et al. (2014), §2.2, minimizes a regularized arrival-time objective. In compact
notation:

`J(T) = lambda/2 * <T-Tf, A(T-Tf)> - log p(data|T)`, `A=(-Laplacian)^p`.

The ignition time is constrained. This penalizes rough corrections instead of
moving detections directly. A future prototype must preserve barriers, use a valid
finite computational domain and regenerate physically consistent forecasts. It is
an alternative to compare with PF/reanchoring, not a correction to stack blindly
on every update. [Original paper, equation 2](https://arxiv.org/pdf/1410.6948).

### 5. Fix statistical identifiability before adding more free parameters

The following are implementation experiments, not equations attributed to those papers:

- Run likelihood-only, reanchor-only and combined ablations using identical seeds.
- Verify prior support, parameter correlations and sensitivity. Begin with ROS/wind
  as free variables while nuisance moisture/urban behavior is fixed to independently
  supported inputs; then release parameters only when evidence identifies them.
- The current one-stage tempering uses `w' proportional w*L^alpha`. If alpha<1,
  some likelihood strength is deliberately unused. Prototype progressive stages
  `delta_beta` totaling one, each followed by valid resample/move/reforecast steps.
  Simply applying the remaining power to duplicated particles is not rejuvenation.
- Keep proposals in log space for positive parameters and use circular wind statistics.
  Recompute member trajectories after a changed parameter vector. Retaining arrivals
  from the old parameters would invalidate the posterior model interpretation.
- Correct correlated GOES scan evidence with a predeclared observation error/block
  model, selected on calibration data. ESS alone is not a calibration score.
- Freeze predeclared seeds and scoring definitions before evaluating test incidents.

## Experiment order and promotion criteria

1. Reproduce baseline twin failures and record all seeds, inputs and model identity.
2. Independently verify numerical/physics units and wind conversion.
3. Compare current and candidate sensor likelihoods on calibration observations.
4. Compare filter ablations; measure outline skill, probability reliability, ESS,
   parameter recovery and runtime together. Log tempering and reanchor contributions.
5. Run the historical framework on frozen independent truth; report test incidents
   separately from calibration. Use incident-level uncertainty intervals once enough
   independent incidents exist; pixels are not independent replicates.
6. Promote a change only after oracle, convergence, causality, checkpoint, multi-seed
   calibration and historical gates pass. Change model identity/checkpoint version
   deliberately. No default physics or likelihood behavior changed in this work.

Existing twin targets: wind error <=8 degrees, ROS error <=25%, ESS>=0.2N on >=80%
of cycles and improved +2 h outline skill over open loop. Do not tune the low-trust
report toward a dramatic q50 shift; separately measure probability and boundary effect.
Crown/spotting and structure-to-structure spread require separate validated models.
A learned residual can be considered only after a calibrated physical baseline exists.
