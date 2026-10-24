use std::path::Path;

use fire_propagation::{advance, SimulationState};
use fire_types::{
    ArrivalHistoryPolicy, CorrectionManifest, EngineError, EngineResult, ErrorCode, RunInput,
    ARRIVAL_INITIAL_UNKNOWN, ARRIVAL_NOT_REACHED,
};

use crate::checkpoint::load_checkpoint;
use fire_io::arrays::read_f64le;

pub fn apply_correction(
    checkpoint_dir: &Path,
    parent_input: &RunInput,
    update: &CorrectionManifest,
    new_input: &RunInput,
    corrected_phi: &[f64],
) -> EngineResult<(fire_propagation::PreparedLandscape, SimulationState)> {
    let observed = fire_types::time::utc_seconds(&update.observed_utc)?;
    let available = fire_types::time::utc_seconds(&update.available_utc)?;
    let origin = fire_types::time::utc_seconds(&parent_input.manifest.start_utc)?;
    if (observed as f64 - origin as f64 - update.time_s_in_parent).abs() > 1e-6
        || available < observed
        || available as f64 > origin as f64 + update.time_s_in_parent
    {
        return Err(EngineError::new(ErrorCode::MissingCoverage, "correction must be available at its observation time; delayed-data replay is unsupported"));
    }
    if update.provenance.trim().is_empty() {
        return Err(EngineError::new(
            ErrorCode::SchemaMismatch,
            "correction provenance is required",
        ));
    }
    if update.schema_version != 1 {
        return Err(EngineError::new(
            ErrorCode::SchemaMismatch,
            "correction schema_version must be 1",
        ));
    }
    if update.policy != fire_types::CorrectionPolicy::ReplaceStateAtObservation {
        return Err(EngineError::new(
            ErrorCode::SchemaMismatch,
            "unsupported correction policy",
        ));
    }
    if new_input.manifest.start_utc != parent_input.manifest.start_utc {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "correction must preserve parent time origin",
        ));
    }
    if !update.time_s_in_parent.is_finite() || update.time_s_in_parent < 0.0 {
        return Err(EngineError::new(
            ErrorCode::InvalidUnits,
            "invalid observation time",
        ));
    }
    if new_input.manifest.grid != parent_input.manifest.grid {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "corrected grid must match parent",
        ));
    }
    let mut checked = new_input.clone();
    checked.initial_phi = corrected_phi.to_vec();
    fire_propagation::validate_inputs(&checked)?;
    let n = parent_input.manifest.grid.cell_count()?;
    if corrected_phi.len() != n {
        return Err(EngineError::new(
            ErrorCode::InvalidArrayLength,
            "corrected phi length",
        ));
    }
    let (prepared, mut state) = if checkpoint_dir.join("meta.json").exists() {
        load_checkpoint(checkpoint_dir, parent_input)?
    } else {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "checkpoint not found; re-run parent to t_obs first",
        ));
    };
    if state.t + 1e-12 < update.time_s_in_parent {
        advance(&mut state, &prepared, parent_input, update.time_s_in_parent)?;
    }
    if (state.t - update.time_s_in_parent).abs() > 1e-6 {
        return Err(EngineError::new(
            ErrorCode::CheckpointMismatch,
            "could not advance exactly to observation time",
        ));
    }
    let old_status = state.arrival_status.clone();
    let old_time = state.arrival_time_s.clone();
    state.phi = corrected_phi.to_vec();
    state.phi_scratch = state.phi.clone();
    for i in 0..n {
        if new_input.valid[i] != 1 {
            state.arrival_status[i] = fire_types::ARRIVAL_INVALID;
            state.arrival_time_s[i] = 0.0;
            continue;
        }
        if new_input.burnable[i] != 1 {
            state.arrival_status[i] = ARRIVAL_NOT_REACHED;
            state.arrival_time_s[i] = 0.0;
            continue;
        }
        if corrected_phi[i] < 0.0 {
            let preserve = matches!(
                update.arrival_history,
                ArrivalHistoryPolicy::PreserveKnownModeledArrivals
            ) && old_status[i] == fire_types::ARRIVAL_MODELED;
            if preserve {
                state.arrival_status[i] = old_status[i];
                state.arrival_time_s[i] = old_time[i];
            } else {
                state.arrival_status[i] = ARRIVAL_INITIAL_UNKNOWN;
                state.arrival_time_s[i] = 0.0;
                state.initial_burned[i] = 1;
            }
        } else {
            state.arrival_status[i] = ARRIVAL_NOT_REACHED;
            state.arrival_time_s[i] = 0.0;
            state.initial_burned[i] = 0;
        }
    }
    // Preserve seconds since the parent start_utc, including known modeled arrivals.
    state.env_frame_index = new_input.environment.frame_index_at(state.t)?;
    state.next_output_index = new_input
        .manifest
        .simulation
        .horizons_s
        .partition_point(|h| *h <= state.t);
    state.horizons_written.clear();
    state
        .quality_flags
        .push(format!("corrected_from:{}", update.observation_id));
    let prepared_new = fire_propagation::prepare(new_input)?;
    Ok((prepared_new, state))
}

pub fn load_corrected_phi(path: &Path, n: usize) -> EngineResult<Vec<f64>> {
    let b = std::fs::read(path)
        .map_err(|e| EngineError::new(ErrorCode::SchemaMismatch, format!("corrected phi: {e}")))?;
    read_f64le(&b, n, "corrected_phi")
}
