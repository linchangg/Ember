use fire_propagation::run;
use fire_types::{EngineError, EngineResult, EnsembleConfig, EnsembleOutput, ErrorCode, RunInput};
use rayon::ThreadPoolBuilder;

use crate::aggregate::aggregate;
use crate::sampling::{apply_perturbation, draw_member};

pub fn estimate_bytes_per_scenario(n_cells: usize) -> u64 {
    (n_cells as u64).saturating_mul(160)
}

pub fn run_ensemble(input: &RunInput, config: &EnsembleConfig) -> EngineResult<EnsembleOutput> {
    config.validate()?;
    let n_cells = input.manifest.grid.cell_count()?;
    let per = estimate_bytes_per_scenario(n_cells);
    let shared = (n_cells as u64).saturating_mul(40);
    let budget_scen = config.memory_budget_bytes.saturating_sub(shared);
    let by_mem = budget_scen
        .checked_div(per)
        .map(|v| v as u32)
        .unwrap_or(config.workers);
    let cpus = std::thread::available_parallelism()
        .map(|p| p.get() as u32)
        .unwrap_or(1);
    let workers = config.workers.min(cpus).min(by_mem);
    if workers < 1 {
        return Err(EngineError::new(
            ErrorCode::MemoryBudgetExceeded,
            "workers<1 after memory budget",
        ));
    }
    let mut metas = Vec::new();
    let mut perturbed = Vec::new();
    for k in 0..config.members {
        let meta = draw_member(config, k)?;
        let mut clone = input.clone();
        match apply_perturbation(&mut clone, &meta) {
            Ok(()) => {
                metas.push(meta);
                perturbed.push(Some(clone));
            }
            Err(e) => {
                let mut m = meta;
                m.failed = Some(e.message);
                metas.push(m);
                perturbed.push(None);
            }
        }
    }
    let pool = ThreadPoolBuilder::new()
        .num_threads(workers as usize)
        .build()
        .map_err(|e| EngineError::new(ErrorCode::SchemaMismatch, format!("thread pool: {e}")))?;
    let results: Vec<Option<fire_types::RunOutput>> = pool.install(|| {
        use rayon::prelude::*;
        perturbed
            .into_par_iter()
            .map(|inp| inp.and_then(|i| run(&i).ok()))
            .collect()
    });
    for (meta, res) in metas.iter_mut().zip(results.iter()) {
        if res.is_none() && meta.failed.is_none() {
            meta.failed = Some("scenario failed validation or solve".into());
        }
    }
    if !config.allow_partial && results.iter().any(|r| r.is_none()) {
        return Err(EngineError::new(
            ErrorCode::InvalidInitialFront,
            "ensemble member failed and allow_partial is false",
        ));
    }
    let mut out = aggregate(&results, config.preset.clone(), config.members);
    out.members = metas;
    Ok(out)
}
