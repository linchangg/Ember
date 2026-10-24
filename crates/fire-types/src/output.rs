use serde::{Deserialize, Serialize};

use crate::grid::GridSpec;
use crate::input::ArtifactHash;

pub fn modeled(valid: u8, burnable: u8) -> bool {
    valid == 1 && burnable == 1
}

pub const ARRIVAL_INVALID: u8 = 0;
pub const ARRIVAL_NOT_REACHED: u8 = 1;
pub const ARRIVAL_INITIAL_UNKNOWN: u8 = 2;
pub const ARRIVAL_MODELED: u8 = 3;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildInfo {
    pub git_commit: String,
    pub rustc: String,
    pub target: String,
}

impl Default for BuildInfo {
    fn default() -> Self {
        Self {
            git_commit: option_env!("GIT_COMMIT").unwrap_or("unknown").to_string(),
            rustc: option_env!("RUSTC_RELEASE")
                .unwrap_or("unknown")
                .to_string(),
            target: option_env!("RUSTC_HOST")
                .or(option_env!("CARGO_CFG_TARGET_ARCH"))
                .unwrap_or("unknown")
                .to_string(),
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DiagnosticCounters {
    pub step_count: u64,
    pub min_step_s: f64,
    pub max_step_s: f64,
    pub wind_limit_counts: u64,
    pub lb_clamp_counts: u64,
    pub irreversibility_constraints: u64,
    pub cancelled: bool,
}

impl DiagnosticCounters {
    pub fn new() -> Self {
        Self {
            min_step_s: f64::INFINITY,
            max_step_s: 0.0,
            ..Self::default()
        }
    }

    pub fn record_step(&mut self, dt: f64) {
        self.step_count += 1;
        self.min_step_s = self.min_step_s.min(dt);
        self.max_step_s = self.max_step_s.max(dt);
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HorizonArrays {
    pub time_s: f64,
    pub phi: Vec<f64>,
    pub burned: Vec<u8>,
    pub front_cells: Vec<u8>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunJson {
    pub schema_version: u32,
    pub status: String,
    pub model: String,
    pub solver: String,
    pub incident_id: String,
    pub start_utc: String,
    pub completed_until_s: f64,
    pub horizons_s: Vec<f64>,
    pub input_manifest_sha256: String,
    pub input_artifacts: Vec<ArtifactRef>,
    pub build: BuildInfo,
    pub configuration: serde_json::Value,
    pub quality_flags: Vec<String>,
    pub parent_run: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactRef {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
}

impl From<&ArtifactHash> for ArtifactRef {
    fn from(value: &ArtifactHash) -> Self {
        Self {
            path: value.path.clone(),
            sha256: value.sha256.clone(),
            bytes: value.bytes,
        }
    }
}

#[derive(Debug, Clone)]
pub struct RunOutput {
    pub run: RunJson,
    pub grid: GridSpec,
    pub arrival_time_s: Vec<f64>,
    pub arrival_status: Vec<u8>,
    pub horizons: Vec<HorizonArrays>,
    pub diagnostics: serde_json::Value,
}

#[derive(Debug, Clone)]
pub struct AdvanceReport {
    pub time_s: f64,
    pub steps: u64,
    pub events_processed: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnsembleMemberMeta {
    pub member: u32,
    pub seed: u64,
    pub wind_east_bias_m_s: f64,
    pub wind_north_bias_m_s: f64,
    pub dead_moisture_multiplier: f64,
    pub live_moisture_multiplier: f64,
    pub front_bias_m: f64,
    pub failed: Option<String>,
}

#[derive(Debug, Clone)]
pub struct EnsembleOutput {
    pub preset: String,
    pub n_requested: u32,
    pub n_complete: u32,
    pub partial: bool,
    pub members: Vec<EnsembleMemberMeta>,
    pub probability: Vec<Vec<f64>>,
    pub p_no_arrival: Vec<f64>,
    pub arrival_q10: Vec<f64>,
    pub arrival_q50: Vec<f64>,
    pub arrival_q90: Vec<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvalProtocol {
    pub schema_version: u32,
    pub split: String,
    pub incident_ids: Vec<String>,
    pub horizons_s: Vec<f64>,
    pub baseline: String,
    pub mask_rule: String,
    pub bootstrap_incident_replicates: u32,
    pub bootstrap_seed: u64,
    pub release_claim: String,
}
