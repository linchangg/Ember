use std::path::Path;

use fire_propagation::{initialize, prepare, AdvanceReport, PreparedLandscape, SimulationState};
use fire_types::{
    DiagnosticCounters, EngineError, EngineResult, ErrorCode, InitialState, RunInput, RunManifest,
};
use serde::{Deserialize, Serialize};

use fire_io::arrays::{read_f64le, read_u8, write_atomic, write_f64le};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckpointMeta {
    pub schema_version: u32,
    pub model: String,
    pub solver: String,
    pub t: f64,
    pub manifest_sha256: String,
    pub bundle_root: String,
    pub env_frame_index: usize,
    pub next_output_index: usize,
    pub diagnostics: DiagnosticCounters,
    pub quality_flags: Vec<String>,
    pub run_manifest: RunManifest,
    #[serde(default)]
    pub context: fire_types::PropagationContext,
    pub state_hashes: Vec<(String, String)>,
    pub input_artifact_hashes: Vec<(String, String)>,
}

pub fn save_checkpoint(dir: &Path, input: &RunInput, state: &SimulationState) -> EngineResult<()> {
    if dir.exists() {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "checkpoint destination already exists",
        ));
    }
    let staging = dir.with_extension("ckpt-staging");
    if staging.exists() {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "checkpoint staging already exists",
        ));
    }
    std::fs::create_dir_all(&staging).map_err(|e| {
        EngineError::new(ErrorCode::SchemaMismatch, format!("checkpoint mkdir: {e}"))
    })?;
    write_atomic(&staging.join("phi.f64le"), &write_f64le(&state.phi))?;
    write_atomic(
        &staging.join("arrival_time_s.f64le"),
        &write_f64le(&state.arrival_time_s),
    )?;
    write_atomic(&staging.join("arrival_status.u8"), &state.arrival_status)?;
    write_atomic(&staging.join("initial_burned.u8"), &state.initial_burned)?;
    let meta = CheckpointMeta {
        schema_version: 2,
        model: "surface_v1".into(),
        solver: "level_set_lf_euler_v1".into(),
        t: state.t,
        manifest_sha256: input.manifest_sha256.clone(),
        bundle_root: input.bundle_root.clone(),
        env_frame_index: state.env_frame_index,
        next_output_index: state.next_output_index,
        diagnostics: state.diagnostics.clone(),
        quality_flags: state.quality_flags.clone(),
        run_manifest: input.manifest.clone(),
        context: input.context.clone(),
        state_hashes: [
            "phi.f64le",
            "arrival_time_s.f64le",
            "arrival_status.u8",
            "initial_burned.u8",
        ]
        .iter()
        .map(|name| {
            Ok((
                name.to_string(),
                fire_io::sha256_file(&staging.join(name))?.0,
            ))
        })
        .collect::<EngineResult<_>>()?,
        input_artifact_hashes: input
            .artifacts
            .iter()
            .map(|a| (a.path.clone(), a.sha256.clone()))
            .collect(),
    };
    let bytes = serde_json::to_vec_pretty(&meta).map_err(|e| {
        EngineError::new(ErrorCode::SchemaMismatch, format!("checkpoint meta: {e}"))
    })?;
    write_atomic(&staging.join("meta.json"), &bytes)?;

    std::fs::rename(&staging, dir).map_err(|e| {
        EngineError::new(
            ErrorCode::CheckpointMismatch,
            format!("publish checkpoint: {e}"),
        )
    })?;
    Ok(())
}

pub fn load_checkpoint(
    dir: &Path,
    input: &RunInput,
) -> EngineResult<(PreparedLandscape, SimulationState)> {
    let meta_bytes = std::fs::read(dir.join("meta.json"))
        .map_err(|e| EngineError::new(ErrorCode::CheckpointMismatch, format!("read meta: {e}")))?;
    let meta: CheckpointMeta = serde_json::from_slice(&meta_bytes)
        .map_err(|e| EngineError::new(ErrorCode::CheckpointMismatch, format!("meta json: {e}")))?;
    if meta.schema_version != 2
        || meta.model != "surface_v1"
        || meta.solver != "level_set_lf_euler_v1"
    {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "solver/model version mismatch",
        ));
    }
    if meta.manifest_sha256 != input.manifest_sha256 {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "input manifest hash mismatch",
        ));
    }
    if meta.run_manifest != input.manifest || meta.context != input.context {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "run configuration mismatch",
        ));
    }
    let expected: Vec<_> = input
        .artifacts
        .iter()
        .map(|a| (a.path.clone(), a.sha256.clone()))
        .collect();
    if meta.input_artifact_hashes != expected {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "input artifact hash mismatch",
        ));
    }
    let required = [
        "phi.f64le",
        "arrival_time_s.f64le",
        "arrival_status.u8",
        "initial_burned.u8",
    ];
    if meta.state_hashes.len() != required.len() {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "missing state hashes",
        ));
    }
    for name in required {
        let expected = meta
            .state_hashes
            .iter()
            .find(|(path, _)| path == name)
            .ok_or_else(|| EngineError::new(ErrorCode::CheckpointMismatch, "missing state hash"))?;
        if fire_io::sha256_file(&dir.join(name))?.0 != expected.1 {
            return Err(EngineError::new(
                ErrorCode::CheckpointMismatch,
                "checkpoint state hash mismatch",
            ));
        }
    }
    if !meta.t.is_finite() || meta.t < 0.0 || meta.t > input.environment.coverage_end_s {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "invalid checkpoint time",
        ));
    }
    let n = input.manifest.grid.cell_count()?;
    let phi_b = std::fs::read(dir.join("phi.f64le"))
        .map_err(|e| EngineError::new(ErrorCode::CheckpointMismatch, format!("phi: {e}")))?;
    let arr_t = std::fs::read(dir.join("arrival_time_s.f64le")).map_err(|e| {
        EngineError::new(ErrorCode::CheckpointMismatch, format!("arrival time: {e}"))
    })?;
    let arr_s = std::fs::read(dir.join("arrival_status.u8")).map_err(|e| {
        EngineError::new(
            ErrorCode::CheckpointMismatch,
            format!("arrival status: {e}"),
        )
    })?;
    let init_b = std::fs::read(dir.join("initial_burned.u8")).map_err(|e| {
        EngineError::new(
            ErrorCode::CheckpointMismatch,
            format!("initial burned: {e}"),
        )
    })?;
    let phi = read_f64le(&phi_b, n, "phi")?;
    let arrival_time_s = read_f64le(&arr_t, n, "arrival_time")?;
    let arrival_status = read_u8(&arr_s, n, "arrival_status")?;
    let initial_burned = read_u8(&init_b, n, "initial_burned")?;
    for i in 0..n {
        if !phi[i].is_finite()
            || !arrival_time_s[i].is_finite()
            || arrival_time_s[i] < 0.0
            || arrival_time_s[i] > meta.t
            || arrival_status[i] > fire_types::ARRIVAL_MODELED
            || initial_burned[i] > 1
        {
            return Err(EngineError::new(
                ErrorCode::CheckpointMismatch,
                "invalid checkpoint state array",
            )
            .cell(i));
        }
    }
    if meta.env_frame_index >= input.environment.frames.len()
        || meta.next_output_index > input.manifest.simulation.horizons_s.len()
    {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "invalid checkpoint event index",
        ));
    }
    let prepared = prepare(input)?;
    let mut state = initialize(&prepared, &InitialState { phi: phi.clone() })?;
    state.t = meta.t;
    state.phi = phi;
    state.phi_scratch = state.phi.clone();
    state.arrival_time_s = arrival_time_s;
    state.arrival_status = arrival_status;
    state.initial_burned = initial_burned;
    state.env_frame_index = meta.env_frame_index;
    state.next_output_index = meta.next_output_index;
    state.diagnostics = meta.diagnostics;
    state.quality_flags = meta.quality_flags;
    Ok((prepared, state))
}

pub fn resume_until(
    dir: &Path,
    input: &RunInput,
    until_s: f64,
) -> EngineResult<(PreparedLandscape, SimulationState, AdvanceReport)> {
    let (prepared, mut state) = load_checkpoint(dir, input)?;
    let report = fire_propagation::advance(&mut state, &prepared, input, until_s)?;
    Ok((prepared, state, report))
}

#[cfg(test)]
mod tests {
    use super::*;
    use fire_types::*;

    const CATALOG: &str = include_str!("../../../docs/spec/fuel_models.csv");

    pub(super) fn sample_input() -> RunInput {
        let g = GridSpec {
            width: 16,
            height: 16,
            cell_size_m: 10.0,
            x0_m: 0.0,
            y0_m: 0.0,
            crs: "LOCAL_METRIC_TEST".into(),
        };
        let n = 256;
        let mut phi = vec![12.0; n];
        for r in 5..11 {
            for c in 5..11 {
                phi[(r * 16 + c) as usize] = -8.0;
            }
        }
        RunInput {
            manifest: RunManifest {
                schema_version: 1,
                incident_id: "ckpt".into(),
                start_utc: "2026-01-01T00:00:00Z".into(),
                grid: g,
                landscape: LandscapeManifest {
                    elevation: "e".into(),
                    fuel_id: "f".into(),
                    valid: "v".into(),
                    burnable: "b".into(),
                    fuel_catalog: "c".into(),
                },
                initial: InitialManifest {
                    phi: "p".into(),
                    perimeter_activity: PerimeterActivity::AssumeEntireBoundaryActive,
                },
                environment: EnvironmentManifest {
                    wind_reference: WindReference::MidflameHorizontalHeadingSurfaceSpeed,
                    interpolation: EnvironmentInterpolation::HoldRightContinuous,
                    frames: vec![EnvironmentFrameManifest {
                        time_s: 0.0,
                        observed_or_valid_utc: "2026-01-01T00:00:00Z".into(),
                        available_utc: "2026-01-01T00:00:00Z".into(),
                        directory: "weather/000000".into(),
                    }],
                    coverage_end_s: 600.0,
                },
                simulation: SimulationManifest {
                    horizons_s: vec![300.0, 600.0],
                    cfl: 0.4,
                    max_step_s: 30.0,
                    wind_limit: WindLimitPolicy::Effective0p9Ir,
                    ellipse_max_lb: 8.0,
                    reinitialize: false,
                    solver: SolverKind::LevelSetLfEulerV1,
                },
            },
            catalog_csv: CATALOG.into(),
            elevation: vec![0.0; n],
            fuel_id: vec![1; n],
            valid: vec![1; n],
            burnable: vec![1; n],
            initial_phi: phi,
            environment: EnvironmentSeries {
                interpolation: EnvironmentInterpolation::HoldRightContinuous,
                coverage_end_s: 600.0,
                frames: vec![EnvironmentFrame {
                    time_s: 0.0,
                    observed_or_valid_utc: "2026-01-01T00:00:00Z".into(),
                    available_utc: "2026-01-01T00:00:00Z".into(),
                    wind_east: vec![0.0; n],
                    wind_north: vec![0.0; n],
                    m1: vec![0.05; n],
                    m10: vec![0.07; n],
                    m100: vec![0.09; n],
                    mlh: vec![0.6; n],
                    mlw: vec![0.9; n],
                }],
            },
            artifacts: Vec::new(),
            manifest_sha256: "same".into(),
            bundle_root: ".".into(),
            context: fire_types::PropagationContext::default(),
            speed_schedule: SpeedSchedule {
                epochs: vec![(
                    0.0,
                    SpeedMode::Uniform {
                        d: [0.0, 0.0],
                        a: [0.05, 0.0],
                        bv: [0.0, 0.05],
                        spread: true,
                    },
                )],
            },
        }
    }

    #[test]
    fn resume_matches_continuous() {
        let input = sample_input();
        let continuous = fire_propagation::run(&input).unwrap();
        let prepared = fire_propagation::prepare(&input).unwrap();
        let mut state = fire_propagation::initialize(
            &prepared,
            &InitialState {
                phi: input.initial_phi.clone(),
            },
        )
        .unwrap();
        fire_propagation::advance(&mut state, &prepared, &input, 300.0).unwrap();
        let dir = std::env::temp_dir().join(format!("ckpt-{}", std::process::id()));
        save_checkpoint(&dir, &input, &state).unwrap();
        let (_, resumed, _) = resume_until(&dir, &input, 600.0).unwrap();
        assert_eq!(resumed.phi, continuous.horizons.last().unwrap().phi);
        std::fs::remove_dir_all(&dir).ok();
    }
}

#[cfg(test)]
mod regressions {
    use super::tests::sample_input;
    use super::*;
    fn directory(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("fire-{name}-{}", std::process::id()))
    }
    #[test]
    fn zero_step_checkpoint_roundtrip_and_immutable_publication() {
        let input = sample_input();
        let prepared = prepare(&input).unwrap();
        let state = initialize(
            &prepared,
            &InitialState {
                phi: input.initial_phi.clone(),
            },
        )
        .unwrap();
        let dir = directory("zero");
        save_checkpoint(&dir, &input, &state).unwrap();
        assert_eq!(load_checkpoint(&dir, &input).unwrap().1.t, 0.0);
        assert!(save_checkpoint(&dir, &input, &state).is_err());
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn changed_input_artifact_and_corrupt_state_rejected() {
        let mut input = sample_input();
        input.artifacts.push(fire_types::ArtifactHash {
            path: "weather/m1".into(),
            sha256: "old".into(),
            bytes: 8,
        });
        let prepared = prepare(&input).unwrap();
        let state = initialize(
            &prepared,
            &InitialState {
                phi: input.initial_phi.clone(),
            },
        )
        .unwrap();
        let dir = directory("corrupt");
        save_checkpoint(&dir, &input, &state).unwrap();
        let mut changed = input.clone();
        changed.artifacts[0].sha256 = "changed".into();
        assert!(load_checkpoint(&dir, &changed).is_err());
        let mut changed_context = input.clone();
        changed_context.context.ros_multiplier = 1.5;
        assert!(load_checkpoint(&dir, &changed_context).is_err());
        let mut bytes = std::fs::read(dir.join("phi.f64le")).unwrap();
        bytes[0] ^= 1;
        std::fs::write(dir.join("phi.f64le"), bytes).unwrap();
        assert!(load_checkpoint(&dir, &input).is_err());
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn correction_preserves_origin_and_changes_resumed_state() {
        let input = sample_input();
        let prepared = prepare(&input).unwrap();
        let mut state = initialize(
            &prepared,
            &InitialState {
                phi: input.initial_phi.clone(),
            },
        )
        .unwrap();
        fire_propagation::advance(&mut state, &prepared, &input, 300.0).unwrap();
        let dir = directory("correct");
        save_checkpoint(&dir, &input, &state).unwrap();
        let mut corrected = vec![12.0; 256];
        for r in 4..12 {
            for c in 4..12 {
                corrected[r * 16 + c] = -8.0;
            }
        }
        let update = fire_types::CorrectionManifest {
            schema_version: 1,
            observation_id: "test-observation".into(),
            observed_utc: "2026-01-01T00:05:00Z".into(),
            available_utc: "2026-01-01T00:05:00Z".into(),
            time_s_in_parent: 300.0,
            grid_manifest: "manifest.json".into(),
            corrected_phi: "corrected.f64le".into(),
            policy: fire_types::CorrectionPolicy::ReplaceStateAtObservation,
            arrival_history: fire_types::ArrivalHistoryPolicy::ResetCorrectedInteriorToUnknown,
            provenance: "synthetic test".into(),
        };
        let (prepared, mut fixed) =
            crate::apply_correction(&dir, &input, &update, &input, &corrected).unwrap();
        assert_eq!(fixed.t, 300.0);
        assert_eq!(fixed.phi, corrected);
        fire_propagation::advance(&mut fixed, &prepared, &input, 600.0).unwrap();
        assert_ne!(
            fixed.phi,
            fire_propagation::run(&input)
                .unwrap()
                .horizons
                .last()
                .unwrap()
                .phi
        );
        let mut invalid = corrected;
        invalid[20] = f64::NAN;
        assert!(crate::apply_correction(&dir, &input, &update, &input, &invalid).is_err());
        std::fs::remove_dir_all(dir).unwrap();
    }
}
