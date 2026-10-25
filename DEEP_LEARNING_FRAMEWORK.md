# Deep learning framework for fire prediction

Proposed research framework • October 24, 2026

## 1. Objective and scope

Given the current estimated fire state and information available at forecast time, estimate where an existing fire could spread over 30 minutes, 1 hour, 3 hours, and 6 hours. Produce burn probabilities, arrival distributions, and scenario trajectories usable by the exposure subsystem.

These horizons are experimental targets, not validated capabilities. This proposal extends the research tracks in `SYSTEM_REFERENCE_PLAN.md`; it does not change the physics-first release scope or authorize replacement of the existing engine. Repository contracts were checked against main `c5e2599` on October 24, 2026. The owner-approved Rust backend remains behind the existing Python HazardModel API; the learned component is a proposed research extension.

**Recommended sequence:** establish historical replay → train a compact spatial baseline → test a physics-assisted correction model → add temporal learning if observation sequences support it → investigate larger architectures only when a measured limitation warrants them.

New-ignition forecasting is a separate task: predict occurrence within a region and time window using occurrence/nonoccurrence labels, lightning, human activity, fuel, and weather. Do not mix its probabilities with spread conditional on an already established fire.

## 2. Shared system architecture

```text
Terrain + fuels + weather forecasts + available fire observations
                         ↓
Alignment, availability filtering, quality masks, state estimation
                         ↓
Immutable forecast snapshot and shared feature builder
                         ↓
             ┌───────────┴─────────────┐
       Physics baseline         Learned challenger
             └───────────┬─────────────┘
                         ↓
Calibration, scenario aggregation, coverage/applicability checks
                         ↓
Burn probability + arrival distribution + scenario trajectories
                         ↓
Existing asset exposure service and map
```

Keep models interchangeable behind one input/output adapter. Initially run learned models in historical replay and shadow forecasts. Keep forecast generation separate from observation/state estimation so a better reconstruction is not mistaken for better prediction.

## 3. Candidate implementations

| Candidate | Implementation | Main value | Data needed | Main risk / priority |
|---|---|---|---|---|
| Spatial U-Net | Encode current fire geometry, terrain, fuel, and weather; decode interval arrival hazards | Practical first learned baseline; local spatial context | Matched initial states and future progression labels | Limited temporal context; **first experiment** |
| Physics-assisted U-Net | Add physics forecast layers; learn corrected interval hazards or bounded spread-parameter adjustments | Uses existing engine and targets systematic errors | Paired physics replays and observed outcomes | Learns baseline weaknesses or fails when inputs shift; **preferred integration candidate** |
| U-Net + ConvLSTM | Encode a sequence of observed states/weather; recurrent spatial state; decode future hazards | Learns evolving fronts and changing conditions | Multiple timestamped observations per incident with age/missingness | Sparse sequences, rollout drift; **second-stage experiment** |
| Spatiotemporal transformer | Attend over spatial patches, weather tokens, and time | Tests longer-range spatial and temporal relationships | Broad, diverse independent incidents and substantial compute | Overfitting and cost; **later challenger** |
| Neural operator / simulator surrogate | Learn the physics simulator's input-to-trajectory mapping | Faster scenario ensembles | Diverse simulator runs plus independent real incidents for evaluation | Simulator fidelity limits transfer; **runtime research track** |
| Graph neural network | Represent irregular terrain cells/front segments as nodes with directional edges | Irregular meshes or terrain connectivity | Consistent graph construction and progression labels | Changing topology and harder integration; **only if raster limitations matter** |
| Physics-informed parameter learner | Infer uncertain spread parameters using observed progression and solver consistency | Parameter estimation and interpretable corrections | Identifiable parameters and timestamped observations | Nonunique solutions, optimizer instability, imperfect physics; **focused research** |

These are candidate designs, not claims that one architecture will outperform the others. ConvLSTM's original evidence concerns precipitation nowcasting, so its use here is an architectural transfer hypothesis, not wildfire validation [1]. Wildfire-specific work demonstrates physics-informed parameter-learning research, which motivates testing that track without establishing local performance [2].

## 4. Data and label contract

One training example is an **incident, forecast issue time, spatial window, and future horizon set**.

| Group | Suggested inputs | Required treatment |
|---|---|---|
| Static landscape | Elevation, slope, aspect represented by sine/cosine, fuel class, canopy/vegetation when available, burnability | Common projected grid; categorical encoding; provenance |
| Dynamic environment | East/north wind, humidity, temperature, precipitation, dead/live moisture where defensible | Use weather forecasts issued by the historical issue time; retain forecast lead and uncertainty |
| Current fire state | Estimated burned extent, perimeter distance field, observation age/confidence; prior states when available | Keep observed evidence separate from inferred state; hotspots are footprint observations, not exact perimeters |
| Quality | Valid domain, missing-channel masks, source age, source resolution | Missing data must not become zero hazard |
| Optional physics features | Baseline spread, arrival/CDF features, local spread rates, ensemble dispersion | Generate using only issue-time inputs; record engine/configuration versions |
| Labels | Matched future burned extent or arrival interval, observation confidence and coverage | Unknown is unlabeled; final scars alone cannot label short-horizon progression |

Store acquisition time and system availability time separately. No future perimeter, revised weather analysis, final burn scar, or normalization fitted on test incidents may enter historical forecast inputs. Retrospective reanalysis experiments must be explicitly distinguished from operationally realistic replay.

Start at a resolution justified by source data and the existing engine; do not imply fine detail from coarse observations. Keep spatial buffers/overlap around tiles and score stitched incident forecasts to expose seam effects. Rotate wind vectors and aspect consistently if applying spatial augmentation.

## 5. First implementation: compact U-Net with optional physics channels

**Inputs:** static landscape tensor `[B, Cs, H, W]`, current fire-state tensor, future weather features for each target interval, and explicit quality masks. A temporal extension accepts history `[B, T, Cd, H, W]` plus observation ages and time gaps. Standardize continuous inputs using training incidents only.

**Network:** a small multiscale encoder/decoder with skip connections and horizon-conditioned output heads. Select width/depth from development learning curves and available hardware. Compare an otherwise identical model with and without physics channels to isolate their contribution.

**Output parameterization:** for cells estimated unburned at issue time, predict a conditional arrival hazard `h_k` for each interval. Convert it to cumulative arrival probability:

```text
F_k = 1 - product over j=1..k of (1 - h_j)
P(arrival in interval k) = F_k - F_(k-1)
P(no arrival by final horizon) = 1 - F_K
```

This enforces increasing cumulative probability over time within a fixed forecast. Already-burned cells have a separate initial-state status. Invalid cells remain masked. Observation corrections can revise the estimated history between forecast versions; this is separate from within-forecast monotonicity.

Start with a masked proper probabilistic objective: interval-censored arrival likelihood where supported, or horizon-specific binary cross-entropy with suitable observation masks. Add a small boundary/overlap term only if development results justify it. Class weighting, focal loss, or oversampling can distort probability calibration; evaluate and recalibrate on a separate calibration partition if used.

For a parameter-correction variant, bound adjustments to defensible ranges and propagate them through the solver. A standard Rust solver is not automatically differentiable: use an explicit differentiable implementation, supervised parameter targets, or a documented optimization method. Compare this added complexity against direct output correction.

## 6. Uncertainty and forecast products

- Separate uncertain weather/state inputs from variation across independently trained models. Neither captures missing processes automatically.
- Preserve valid-domain and coverage masks, no-arrival mass, forecast issue time, source age, model/data versions, and applicability flags.
- Calibrate on held-out development incidents. Apply calibration in a way that preserves a coherent monotone CDF; do not independently adjust horizons without checking consistency.
- Compute an arrival quantile only if cumulative arrival probability reaches the requested level. Label any conditional-on-arrival summaries separately. Finite horizons leave tail timing unresolved.
- Marginal cell probabilities do not define coherent fire trajectories. Asset/region arrival calculations that depend on joint behavior require validated joint scenarios; do not sample each pixel independently or take the minimum of cellwise quantiles.

## 7. Evaluation framework

Split whole incidents into training, tuning, calibration, and locked final-test groups. Prevent overlapping incidents/near-duplicate observations from crossing partitions. Add later-season and geographically separated holdouts where data permits. Report independent incident counts, not just tile counts.

Compare persistence, a simple spread envelope, the deterministic physics engine, its ensemble, a simple statistical baseline, spatial U-Net, and physics-assisted U-Net using identical information and horizons.

| Question | Evaluation |
|---|---|
| Does the predicted footprint improve? | IoU/Dice, boundary distance, missed and overpredicted area by horizon |
| Are probabilities useful? | Brier score, reliability diagrams, precision-recall, skill relative to baselines |
| Is timing accurate? | Interval-aware arrival scoring; timing error on observed arrivals, paired with missed-arrival/no-arrival evaluation |
| Does exposure improve? | Missed threatened assets, false alerts, lead time, and region-arrival accuracy using joint scenarios |
| Does it generalize? | Per-incident results, fuel/region/wind strata, unseen geography/seasons, incident-level uncertainty intervals |
| Can it run reliably? | End-to-end latency, memory, missing/stale inputs, incomplete coverage, reproducibility |

Predeclare numeric acceptance thresholds with the intended users before locked testing. Require improvement in agreed primary measures, acceptable missed-exposure burden, calibration, and runtime on documented hardware. Choose thresholds from the application and development evidence, not from final-test results.

Required ablations: physics channels on/off; history on/off; dynamic versus fixed weather; quality masks/source age; synthetic pretraining versus observed-only training; performance by observation delay. Stress-test rapidly changing winds, sparse observations, tile boundaries, unseen fuels, and conditions outside surface-fire applicability.

## 8. Delivery stages and decision gates

1. **Data audit and replay:** inventory independent incidents, observation cadence, forecast archives, coverage, and label quality. Deliver immutable examples, split manifest, leakage audit, and reproducible baseline scores. If only final scars exist, narrow the task before training.
2. **Spatial baseline:** train compact U-Net and statistical baseline; establish calibration, error maps, and learning curves. Deliver model card and reproducible held-out development report.
3. **Physics-assisted challenger:** add versioned engine outputs and compare against both physics alone and U-Net alone. Proceed only if added features improve measured outcomes.
4. **Temporal challenger:** test ConvLSTM only if sequential observations are sufficient; measure multi-step drift and irregular-input robustness. Larger models require evidence of a specific unresolved limitation.
5. **Shadow integration:** connect the shared forecast adapter, compare complete live/replayed jobs, exercise failure handling, and retain physics fallback. Publish learned outputs only after the predeclared gates pass.

Suggested research modules: `data` (alignment, availability, labels, splits), `features`, `models`, `training`, `calibration`, `evaluation`, and `inference`. Use a separate training environment and a versioned adapter to the Rust/service layer; select export/runtime tooling after profiling compatibility. No new neural dependency is required in the physics core.

Every experiment should record its hypothesis, data/split versions, inputs, architecture, objective, seed, compute budget, calibration method, per-incident metrics, and promotion decision. Estimate schedule and hardware after the data audit and a representative training/inference benchmark.

## 9. Research references

1. Shi et al., *Convolutional LSTM Network: A Machine Learning Approach for Precipitation Nowcasting* (2015). Original spatial recurrent architecture; transfer to wildfire requires testing. https://arxiv.org/abs/1506.04214
2. *Physics-informed neural networks for parameter learning of wildfire spreading* (2024). Wildfire-specific parameter-learning research. https://doi.org/10.1016/j.cma.2024.117545

Local design context: `SYSTEM_REFERENCE_PLAN.md`, `ENGINE_BUILD_PLAN.md`, and `SURFACE_ENGINE_INTEGRATION_REVIEW.md`. Existing fixture and numerical-test results do not establish historical forecast accuracy or provide sufficient real training data.


## 10. Implementation decision: compact physics-assisted U-Net

**Selected first integration candidate:** a compact U-Net that predicts bounded spatial corrections to local surface spread rates, with corrected rates propagated by the existing Rust solver. Train in Python/PyTorch; export to ONNX; execute the network inside the Rust backend through a version-pinned compatible binding/runtime. Keep the existing Python HazardModel facade, observation assimilation, forecast publication, and wire schema.

This is the best initial engineering choice for the combined goal of useful prediction, affordable calculation, and trustworthy exposure estimates. It is a hypothesis to validate, not an established accuracy winner. No local learned-model benchmark or historical improvement has been demonstrated.

### Human-impact priorities

Prioritize fewer missed threatened assets at a declared false-alert burden, calibrated early-arrival probabilities, useful warning lead time, and timely complete forecasts. Aggregate overlap accuracy alone cannot establish those benefits. Examine hospitals, care facilities, and limited-egress areas separately using the existing asset inventory and frozen risk rules; do not change alert thresholds or claim lives saved.

Choose the model on a constrained accuracy/latency frontier: candidates must satisfy calibration, missing-data, applicability, and runtime gates before comparing missed exposures and lead time. Avoid an arbitrary weighted score that allows faster computation to conceal substantially worse missed exposure.

### Candidate tradeoffs and ordering

| Candidate | Prediction advantages | Calculation/deployment advantages | Costs and failure modes | Decision |
|---|---|---|---|---|
| Compact physics-assisted U-Net, bounded spread correction | Spatial context; retains solver barriers, weather handling, and coherent per-member arrivals; can target systematic local errors | Feed-forward network; evaluate corrections per forcing interval and reuse within solver steps where validated; ordinary convolution operators simplify export | Still pays solver cost; physics bias persists; parameter identifiability; cannot add missing crown/spotting physics | **First integration candidate** |
| Standalone compact U-Net, arrival-hazard head | Directly optimizes observed spread/arrival outcomes with fewer physics assumptions | One multihorizon forward pass; credible low-latency challenger | Marginal maps alone cannot implement joint region arrivals; can learn implausible spread and fail outside training coverage | **Essential baseline; alternate if it wins and joint scenarios are solved** |
| U-Net + ConvLSTM | Uses recent evolution when observations are frequent | Reuses compact spatial encoder | More state, sequential work, irregular observations, checkpoint/export complexity; rollout drift | Add only after temporal-data audit |
| Spatiotemporal transformer | Broad spatial/temporal context | Parallel attention implementations can help suitable workloads | Higher data/compute burden and tuning risk; patch resolution can lose front detail | Defer until compact models show a context limitation |
| Neural operator / simulator surrogate | Can approximate many simulator trajectories | Potentially removes repeated solver work; strongest direct speed-research motivation | Simulator errors transfer; domain/forcing changes and real-fire calibration need independent checks | Second research track if profiling shows solver dominates |
| Graph network | Irregular terrain/front connectivity | Potentially efficient for truly sparse, stable graphs | Graph construction, topology changes, export/integration effort | Defer for current fixed raster |
| PINN / inverse parameter optimization | Physical constraints and parameter interpretation | May work with sparse observations in identifiable cases | Forecast-time optimization can be costly; imperfect equations and nonunique parameters | Offline parameter research, not first forecast path |

These speed comparisons are architectural expectations, not measured rankings. The smallest standalone model could be faster than the selected hybrid. A hybrid earns adoption only if its observed decision-relevant improvement justifies its full runtime.

### Exact first experiment

Use a shallow three-scale convolutional U-Net with skip connections, starting widths 16/32/64 as an experimental configuration. Record actual parameter count and memory from the built artifact; do not treat this configuration as a performance claim. Pad/crop the native 340×340 grid deterministically and test orientation/edge effects. Avoid attention, recurrent state, and custom export operators initially.

Features: categorical fuel embedding, elevation, slope, sine/cosine of downslope aspect, initial burned/state-distance layers, earth-relative wind, available moisture, observation age/confidence, and missingness masks. Existing Weather does not supply every proposed feature; optional inputs require a research feature builder with provenance, not silent additions to frozen dataclasses. Fit transformations on training incidents only.

The first network predicts one shared log spread-correction field per forcing interval from the snapshot and forcing. Apply:

```text
multiplier = exp(log(2) * tanh(raw_output))
corrected_rate = baseline_rate * multiplier
```

The resulting 0.5–2 bounds are a proposed experimental constraint, not a physical confidence interval; select/freeze bounds using development evidence. Zero baseline rates remain zero and nonburnable barriers remain intact. This intentionally cannot create spotting, urban spread, or crown-fire behavior. Preserve any existing applicability flags and do not conflate a nonburnable cell with an unknown cell.

Compute the shared correction once per forcing interval, then apply it to all existing scenario members. This preserves scenario identity and avoids a neural call at every numerical substep. It also limits member-specific corrections: compare against per-member inference later if error analysis warrants it. Cache by model/feature/forcing/landscape/state hashes; invalidate after weather or observation changes. A shared correction does not provide additional model uncertainty by itself.

**Training path:** first validate features and the direct arrival-hazard U-Net using observed progression. For the correction network, use an offline differentiable raster spread approximation to train bounded parameters, with a masked progression/interval-arrival objective. Verify its numerical behavior against the Rust solver on representative fixtures and forcing changes. Gradients through that approximation are not gradients through Rust. Evaluate and select correction checkpoints using actual Rust rollouts on development incidents. If approximation mismatch prevents transfer, stop this path and test coarse spatial correction fields with documented black-box parameter fitting; fitted parameters are pseudo-labels, not physical truth. No claim of correction-model readiness until real-solver replay improves on the uncorrected baseline.

Synthetic pretraining may establish mechanics but cannot establish historical accuracy. Inspect learning curves against independent incident count before enlarging the model. If progression labels are insufficient, deliver the data gap and retain physics rather than training from the single Eaton replay and claiming generalization.

### Rust/Python boundary and contract compatibility

The first experiment is an explicitly selected research mode, default off; define its internal configuration without editing frozen v1 settings. Use the current HazardModel entry points (`init`, `update_weather`, `step`, `region_arrivals`, `save_state`, `load_state`). Keep weights, coherent per-member arrival fields, observation history, and seeded behavior in the existing engine. U-Net corrections do not replace assimilation.

The native contract is EPSG:32611, 90 m, 340×340, row zero north, upper-left origin. Keep epoch milliseconds on the wire and float32 minutes since t_ref at the Python API; apply explicit SI/time adapters inside Rust. Preserve five unconditional arrival quantiles at 0.1/0.3/0.5/0.7/0.9, zero for burned at issue and NaN beyond the horizon at the array boundary. Use the existing serializer for its uint8 wire representation. Obtain region arrivals from per-member regional minima, then existing weights; never from minima of cellwise quantiles.

Package model.onnx, feature order/schema, training normalization, fuel vocabulary/unknown policy, bounds, supported domain/horizons, calibration version, checksums, model/data version, and validated runtime/opset/provider metadata as an immutable research model bundle. Runtime binding choice is conditional on a deployment spike; Rust bindings may be community-maintained and ONNX compatibility must be demonstrated rather than assumed.

Keep one loaded inference session per worker, bound threads to avoid contention with Rust ensemble workers, cache immutable features, and benchmark CPU first. Compare available hardware acceleration only after export/operator parity checks. ONNX Runtime offers hardware-specific execution providers [3]; hardware support does not imply this model is faster on a given provider.

Reject incompatible/corrupt bundles and nonfinite outputs. On unavailable features, unsupported regimes, or failed model execution, run the documented physics fallback with an explicit reason. Do not disguise fallback as a successful learned forecast. Save model identity and correction/cache inputs in versioned research checkpoint metadata; preserve current byte-identical save/load and next-step parity requirements.

### Calculation-speed decision

Measure the whole update, not just the neural forward pass:

```text
T_cycle = state/observation update + feature construction + neural inference
        + corrected propagation across members + CDF/region aggregation
```

Also record serialization/publication separately for end-to-end service latency. Existing target: at most 0.5 seconds per cycle for the specified 128-member, 340×340, six-hour workload, on named demo hardware; this target remains unproven. Forecast issuance every five simulated minutes does not relax the target. Record cold/warm p50/p95, peak memory, worker/thread counts, all member counts, provider, hardware, and run conditions. Set the neural overhead budget after measuring baseline headroom; do not invent a milliseconds estimate.

Compare uncorrected physics, standalone U-Net, shared-correction hybrid, and any later surrogate under equal information and declared compute budgets. If propagation dominates, the hybrid does not solve the speed problem: profile solver improvements separately or advance the validated surrogate track. Do not reduce member count, horizon, or grid resolution silently to claim the target is met. Quantization is a later experiment requiring fresh parity, calibration, and exposure checks.

### First implementation backlog and acceptance

1. **Replay/data inventory:** identify independent incidents and issue-time weather archives; produce progression-label/coverage inventory and locked split manifest. Run the established physics baselines before fitting. A data shortage is a blocker to learned skill claims.
2. **Feature and model spike:** implement the standalone compact U-Net in an isolated Python research environment and export a randomly initialized model to validate mechanics only. Prove Python/ONNX/Rust tensor parity with predeclared numerical tolerances; check exact masks/status semantics. Record measured parameter count, latency, and memory. No changes to production dependency locks in this spike.
3. **Correction training:** implement bounded-field head, surrogate/Rust consistency study, and real-solver replay selection. Include identity multiplier equivalence and correction-bound/barrier tests. Retain the direct U-Net as a comparator.
4. **Rust research inference:** add an optional model-bundle loader and inference module alongside surface_v1, without dependencies in the core fire-behavior crate. Wire corrections at local-rate computation, preserving directional spread and numerical time-step constraints. Test changing forcing, corrected observations, stale caches, unknown fuel, corrupt artifacts, deterministic resume, and fallback reporting.
5. **Scientific and runtime gate:** evaluate incident-disjoint forecast skill, calibration, missed exposure/false-alert tradeoff, q30 lead time, missing sensors, strong winds, and exact-workload latency. Report uncertainty by incident. Recalibrate and select thresholds only on development/calibration sets.
6. **Shadow adapter:** preserve the existing API, region-arrival semantics, and frozen alert rules. Pass the existing component/contract tests plus new parity/fallback checks before considering activation. Have a human review the documented results and merge; deployment selection remains separate.

No implementation, trained weights, or measured speedup is delivered by this documentation change. The next concrete deliverable is the replay/data inventory and export/parity benchmark, not an unvalidated production network.

### Additional sources and limits

[3] [ONNX Runtime execution providers](https://onnxruntime.ai/docs/execution-providers/) — supported acceleration mechanisms; actual support/performance must be checked for the exported graph.

[4] [Wildfire spread forecasting with Deep Learning](https://arxiv.org/abs/2505.17556) — studies final burned extent and multi-day context. Its results are not evidence for this project's 30-minute to six-hour forecasting task; observations after a historical issue time cannot be used in that issue's inputs.

[5] [PyTorch ONNX export documentation](https://docs.pytorch.org/docs/stable/onnx.html) — export workflow; select and pin compatible versions during the deployment spike.
