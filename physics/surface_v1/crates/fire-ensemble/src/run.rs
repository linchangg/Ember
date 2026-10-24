use crate::aggregate::aggregate;
use crate::sampling::{apply_perturbation, draw_member};
use fire_propagation::run;
use fire_types::{EngineError, EngineResult, EnsembleConfig, EnsembleOutput, ErrorCode, RunInput};
use rayon::ThreadPoolBuilder;

/// Conservative array reservation; allocator/OS overhead is not a process RSS limit.
pub fn estimate_bytes_per_scenario(n_cells: usize) -> u64 {
    (n_cells as u64).saturating_mul(512)
}

pub fn run_ensemble(input: &RunInput, config: &EnsembleConfig) -> EngineResult<EnsembleOutput> {
    config.validate()?;
    fire_propagation::validate_inputs(input)?;
    let cells = input.manifest.grid.cell_count()? as u64;
    let horizons = input.manifest.simulation.horizons_s.len() as u64;
    let per = estimate_bytes_per_scenario(cells as usize).saturating_add(
        cells.saturating_mul(56 * input.environment.frames.len() as u64 + 20 * horizons),
    );
    // Retained arrival results plus aggregate arrays, input copy, and metadata.
    let retained = cells
        .saturating_mul(9 * config.members as u64 + 8 * (horizons + 4) + 5)
        .saturating_add(cells.saturating_mul(56 * input.environment.frames.len() as u64 + 64))
        .saturating_add(config.members as u64 * 1024);
    let by_mem = config.memory_budget_bytes.saturating_sub(retained) / per.max(1);
    let cpus = std::thread::available_parallelism()
        .map(|p| p.get())
        .unwrap_or(1);
    let workers = (config.workers as usize)
        .min(cpus)
        .min(by_mem.min(usize::MAX as u64) as usize);
    if workers == 0 {
        return Err(EngineError::new(
            ErrorCode::MemoryBudgetExceeded,
            "budget cannot hold retained results and one active scenario",
        ));
    }
    let pool = ThreadPoolBuilder::new()
        .num_threads(workers)
        .build()
        .map_err(|e| EngineError::new(ErrorCode::SchemaMismatch, format!("thread pool: {e}")))?;
    let pairs: Vec<_> = pool.install(|| {
        use rayon::prelude::*;
        (0..config.members)
            .into_par_iter()
            .map(|k| -> EngineResult<_> {
                let mut meta = draw_member(config, k)?;
                // Clone only when a worker starts; never materialize every member input.
                let mut scenario = input.clone();
                let result = apply_perturbation(&mut scenario, &meta).and_then(|()| run(&scenario));
                let result = match result {
                    Ok(mut output) => {
                        output.horizons.clear();
                        Some(output)
                    }
                    Err(error) => {
                        meta.failed = Some(error.message);
                        None
                    }
                };
                Ok((meta, result))
            })
            .collect::<EngineResult<Vec<_>>>()
    })?;
    let (metas, results): (Vec<_>, Vec<_>) = pairs.into_iter().unzip();
    if results.iter().all(Option::is_none)
        || (!config.allow_partial && results.iter().any(Option::is_none))
    {
        return Err(EngineError::new(
            ErrorCode::InvalidInitialFront,
            "ensemble has failed members without a usable permitted aggregate",
        ));
    }
    let mut out = aggregate(&results, config.preset.clone(), config.members)?;
    out.members = metas;
    Ok(out)
}
