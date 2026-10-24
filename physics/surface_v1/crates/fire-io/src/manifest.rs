use std::path::Path;

use fire_types::{
    ArtifactHash, EngineError, EngineResult, EnvironmentFrame, EnvironmentSeries, ErrorCode,
    RunInput, RunManifest, SpeedSchedule,
};

use crate::arrays::{confined_path, read_f64le, read_u16le, read_u8, sha256_file, sha256_hex};

const FRAME_FIELDS: [&str; 7] = ["wind_east", "wind_north", "m1", "m10", "m100", "mlh", "mlw"];

pub fn load_run_bundle(manifest_path: &Path, memory_budget_bytes: u64) -> EngineResult<RunInput> {
    if memory_budget_bytes == 0 {
        return Err(EngineError::new(
            ErrorCode::MemoryBudgetExceeded,
            "memory budget must be positive",
        ));
    }
    let manifest_bytes = std::fs::read(manifest_path).map_err(|e| {
        EngineError::new(
            ErrorCode::SchemaMismatch,
            format!("cannot read manifest: {e}"),
        )
        .artifact(manifest_path.display().to_string())
    })?;
    if manifest_bytes.len() as u64 > memory_budget_bytes {
        return Err(EngineError::new(
            ErrorCode::MemoryBudgetExceeded,
            "manifest exceeds memory budget",
        ));
    }
    let manifest: RunManifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|e| EngineError::new(ErrorCode::SchemaMismatch, format!("manifest json: {e}")))?;
    manifest.validate_schema()?;
    let root = manifest_path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .to_path_buf();
    let n = manifest.grid.cell_count()?;
    let mut used = manifest_bytes.len() as u64;
    let mut artifacts = Vec::new();

    let mut load_named = |rel: &str| -> EngineResult<(Vec<u8>, ArtifactHash)> {
        let path = confined_path(&root, rel)?;
        let (hash, bytes_len, data) = sha256_file(&path)?;
        used = used.saturating_add(bytes_len);
        if used > memory_budget_bytes {
            return Err(EngineError::new(
                ErrorCode::MemoryBudgetExceeded,
                format!("bundle exceeds memory budget at {rel}"),
            ));
        }
        artifacts.push(ArtifactHash {
            path: rel.to_string(),
            sha256: hash.clone(),
            bytes: bytes_len,
        });
        Ok((
            data,
            ArtifactHash {
                path: rel.to_string(),
                sha256: hash,
                bytes: bytes_len,
            },
        ))
    };

    let (elev_b, _) = load_named(&manifest.landscape.elevation)?;
    let (fuel_b, _) = load_named(&manifest.landscape.fuel_id)?;
    let (valid_b, _) = load_named(&manifest.landscape.valid)?;
    let (burn_b, _) = load_named(&manifest.landscape.burnable)?;
    let (phi_b, _) = load_named(&manifest.initial.phi)?;
    let (cat_b, _) = load_named(&manifest.landscape.fuel_catalog)?;

    let elevation = read_f64le(&elev_b, n, &manifest.landscape.elevation)?;
    let fuel_id = read_u16le(&fuel_b, n, &manifest.landscape.fuel_id)?;
    let valid = read_u8(&valid_b, n, &manifest.landscape.valid)?;
    let burnable = read_u8(&burn_b, n, &manifest.landscape.burnable)?;
    let initial_phi = read_f64le(&phi_b, n, &manifest.initial.phi)?;
    let catalog_csv = String::from_utf8(cat_b)
        .map_err(|_| EngineError::new(ErrorCode::SchemaMismatch, "fuel catalog is not UTF-8"))?;

    let mut frames = Vec::new();
    for fr in &manifest.environment.frames {
        let mut arrays = Vec::new();
        for field in FRAME_FIELDS {
            let rel = format!("{}/{}.f64le", fr.directory, field);
            let (data, _) = load_named(&rel)?;
            arrays.push(read_f64le(&data, n, &rel)?);
        }
        frames.push(EnvironmentFrame {
            time_s: fr.time_s,
            observed_or_valid_utc: fr.observed_or_valid_utc.clone(),
            available_utc: fr.available_utc.clone(),
            wind_east: arrays.remove(0),
            wind_north: arrays.remove(0),
            m1: arrays.remove(0),
            m10: arrays.remove(0),
            m100: arrays.remove(0),
            mlh: arrays.remove(0),
            mlw: arrays.remove(0),
        });
    }

    Ok(RunInput {
        manifest,
        catalog_csv,
        elevation,
        fuel_id,
        valid,
        burnable,
        initial_phi,
        environment: EnvironmentSeries {
            interpolation: fire_types::EnvironmentInterpolation::HoldRightContinuous,
            coverage_end_s: 0.0, // filled below
            frames,
        }
        .with_coverage_placeholder(),
        artifacts,
        manifest_sha256: sha256_hex(&manifest_bytes),
        bundle_root: root.display().to_string(),
        context: fire_types::PropagationContext::default(),
        speed_schedule: SpeedSchedule::default(),
    }
    .fix_coverage())
}

trait FixCoverage {
    fn with_coverage_placeholder(self) -> Self;
    fn fix_coverage(self) -> Self;
}

impl FixCoverage for EnvironmentSeries {
    fn with_coverage_placeholder(self) -> Self {
        self
    }
    fn fix_coverage(self) -> Self {
        self
    }
}

impl FixCoverage for RunInput {
    fn with_coverage_placeholder(self) -> Self {
        self
    }
    fn fix_coverage(mut self) -> Self {
        self.environment.interpolation = self.manifest.environment.interpolation;
        self.environment.coverage_end_s = self.manifest.environment.coverage_end_s;
        self
    }
}
