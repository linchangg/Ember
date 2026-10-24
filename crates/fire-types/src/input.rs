use serde::{Deserialize, Serialize};

use crate::error::{EngineError, EngineResult, ErrorCode};
use crate::grid::GridSpec;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PerimeterActivity {
    AssumeEntireBoundaryActive,
    PartialActiveFrontUnsupported,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WindReference {
    MidflameHorizontalHeadingSurfaceSpeed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EnvironmentInterpolation {
    HoldRightContinuous,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WindLimitPolicy {
    #[serde(rename = "effective_0p9_ir")]
    Effective0p9Ir,
    None,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SolverKind {
    LevelSetLfEulerV1,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LandscapeManifest {
    pub elevation: String,
    pub fuel_id: String,
    pub valid: String,
    pub burnable: String,
    pub fuel_catalog: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InitialManifest {
    pub phi: String,
    pub perimeter_activity: PerimeterActivity,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentFrameManifest {
    pub time_s: f64,
    pub observed_or_valid_utc: String,
    pub available_utc: String,
    pub directory: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentManifest {
    pub wind_reference: WindReference,
    pub interpolation: EnvironmentInterpolation,
    pub frames: Vec<EnvironmentFrameManifest>,
    pub coverage_end_s: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SimulationManifest {
    pub horizons_s: Vec<f64>,
    pub cfl: f64,
    pub max_step_s: f64,
    pub wind_limit: WindLimitPolicy,
    pub ellipse_max_lb: f64,
    pub reinitialize: bool,
    pub solver: SolverKind,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunManifest {
    pub schema_version: u32,
    pub incident_id: String,
    pub start_utc: String,
    pub grid: GridSpec,
    pub landscape: LandscapeManifest,
    pub initial: InitialManifest,
    pub environment: EnvironmentManifest,
    pub simulation: SimulationManifest,
}

impl RunManifest {
    pub fn validate_schema(&self) -> EngineResult<()> {
        if self.schema_version != 1 {
            return Err(EngineError::new(
                ErrorCode::SchemaMismatch,
                format!("unsupported schema_version {}", self.schema_version),
            ));
        }
        self.grid.validate()?;
        if self.simulation.solver != SolverKind::LevelSetLfEulerV1 {
            return Err(EngineError::new(
                ErrorCode::SchemaMismatch,
                "unsupported solver",
            ));
        }
        if self.simulation.reinitialize {
            return Err(EngineError::new(
                ErrorCode::SchemaMismatch,
                "reinitialize must be false in v1",
            ));
        }
        if !(self.simulation.cfl > 0.0 && self.simulation.cfl <= 0.5) {
            return Err(EngineError::new(
                ErrorCode::InvalidUnits,
                "CFL must be in (0, 0.5]",
            ));
        }
        if self.simulation.max_step_s <= 0.0 {
            return Err(EngineError::new(
                ErrorCode::InvalidUnits,
                "max_step_s must be positive",
            ));
        }
        if self.simulation.horizons_s.is_empty() {
            return Err(EngineError::new(
                ErrorCode::SchemaMismatch,
                "at least one horizon is required",
            ));
        }
        let mut seen = std::collections::BTreeSet::new();
        for h in &self.simulation.horizons_s {
            if !h.is_finite() || *h < 0.0 || h.fract() != 0.0 {
                return Err(EngineError::new(
                    ErrorCode::InvalidUnits,
                    "v1 horizons must be integer seconds",
                ));
            }
            if !seen.insert(*h as i64) {
                return Err(EngineError::new(
                    ErrorCode::SchemaMismatch,
                    "duplicate horizons",
                ));
            }
        }
        if self.environment.frames.is_empty() {
            return Err(EngineError::new(
                ErrorCode::MissingCoverage,
                "no environmental frames",
            ));
        }
        let mut prev = f64::NEG_INFINITY;
        for (i, frame) in self.environment.frames.iter().enumerate() {
            if !frame.time_s.is_finite() {
                return Err(EngineError::new(
                    ErrorCode::NonFiniteInput,
                    "frame time must be finite",
                ));
            }
            if i == 0 && frame.time_s != 0.0 {
                return Err(EngineError::new(
                    ErrorCode::MissingCoverage,
                    "first frame time must be 0",
                ));
            }
            if frame.time_s <= prev {
                return Err(EngineError::new(
                    ErrorCode::SchemaMismatch,
                    "frame times must be strictly increasing",
                ));
            }
            prev = frame.time_s;
        }
        let last_h = self
            .simulation
            .horizons_s
            .iter()
            .copied()
            .fold(0.0_f64, f64::max);
        if self.environment.coverage_end_s + 1e-12 < last_h {
            return Err(EngineError::new(
                ErrorCode::MissingCoverage,
                "environmental coverage ends before the last horizon",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Moisture {
    pub m1: f64,
    pub m10: f64,
    pub m100: f64,
    pub mlh: f64,
    pub mlw: f64,
}

impl Moisture {
    pub fn validate(&self) -> EngineResult<()> {
        for (name, v) in [
            ("m1", self.m1),
            ("m10", self.m10),
            ("m100", self.m100),
            ("mlh", self.mlh),
            ("mlw", self.mlw),
        ] {
            if !v.is_finite() {
                return Err(EngineError::new(
                    ErrorCode::NonFiniteInput,
                    format!("{name} is not finite"),
                ));
            }
            if v < 0.0 {
                return Err(EngineError::new(
                    ErrorCode::InvalidFuelParameter,
                    format!("{name} must be nonnegative"),
                ));
            }
        }
        Ok(())
    }

    pub fn as_tuple(self) -> (f64, f64, f64, f64, f64) {
        (self.m1, self.m10, self.m100, self.mlh, self.mlw)
    }

    pub fn scale_dead(self, factor: f64) -> Self {
        Self {
            m1: self.m1 * factor,
            m10: self.m10 * factor,
            m100: self.m100 * factor,
            ..self
        }
    }

    pub fn scale_live(self, factor: f64) -> Self {
        Self {
            mlh: self.mlh * factor,
            mlw: self.mlw * factor,
            ..self
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct LocalEnvironment {
    pub wind_east_m_s: f64,
    pub wind_north_m_s: f64,
    pub gx: f64,
    pub gy: f64,
}

#[derive(Debug, Clone)]
pub struct EnvironmentFrame {
    pub time_s: f64,
    pub observed_or_valid_utc: String,
    pub available_utc: String,
    pub wind_east: Vec<f64>,
    pub wind_north: Vec<f64>,
    pub m1: Vec<f64>,
    pub m10: Vec<f64>,
    pub m100: Vec<f64>,
    pub mlh: Vec<f64>,
    pub mlw: Vec<f64>,
}

#[derive(Debug, Clone)]
pub struct EnvironmentSeries {
    pub interpolation: EnvironmentInterpolation,
    pub coverage_end_s: f64,
    pub frames: Vec<EnvironmentFrame>,
}

impl EnvironmentSeries {
    pub fn frame_index_at(&self, t: f64) -> EngineResult<usize> {
        if t > self.coverage_end_s + 1e-12 {
            return Err(EngineError::new(
                ErrorCode::MissingCoverage,
                format!("time {t} exceeds coverage_end_s"),
            ));
        }
        let mut idx = 0;
        for (i, frame) in self.frames.iter().enumerate() {
            if frame.time_s <= t + 1e-15 {
                idx = i;
            } else {
                break;
            }
        }
        Ok(idx)
    }

    pub fn next_frame_time(&self, t: f64) -> Option<f64> {
        self.frames
            .iter()
            .map(|f| f.time_s)
            .find(|tk| *tk > t + 1e-15)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactHash {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone)]
pub struct InitialState {
    pub phi: Vec<f64>,
}

#[derive(Debug, Clone)]
pub struct RunInput {
    pub manifest: RunManifest,
    pub catalog_csv: String,
    pub elevation: Vec<f64>,
    pub fuel_id: Vec<u16>,
    pub valid: Vec<u8>,
    pub burnable: Vec<u8>,
    pub initial_phi: Vec<f64>,
    pub environment: EnvironmentSeries,
    pub artifacts: Vec<ArtifactHash>,
    pub manifest_sha256: String,
    pub bundle_root: String,
    pub speed_schedule: SpeedSchedule,
}

#[derive(Debug, Clone, Default)]
pub struct ValidationReport {
    pub warnings: Vec<String>,
    pub quality_flags: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ArrivalHistoryPolicy {
    ResetCorrectedInteriorToUnknown,
    PreserveKnownModeledArrivals,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CorrectionPolicy {
    ReplaceStateAtObservation,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CorrectionManifest {
    pub schema_version: u32,
    pub observation_id: String,
    pub observed_utc: String,
    pub available_utc: String,
    pub time_s_in_parent: f64,
    pub grid_manifest: String,
    pub corrected_phi: String,
    pub policy: CorrectionPolicy,
    pub arrival_history: ArrivalHistoryPolicy,
    pub provenance: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnsembleConfig {
    pub schema_version: u32,
    pub preset: String,
    pub members: u32,
    pub master_seed: u64,
    pub workers: u32,
    pub memory_budget_bytes: u64,
    pub wind_bias_std_m_s: f64,
    pub dead_moisture_log_std: f64,
    pub live_moisture_log_std: f64,
    pub front_bias_std_m: f64,
    pub allow_partial: bool,
}

impl EnsembleConfig {
    pub fn validate(&self) -> EngineResult<()> {
        if self.schema_version != 1 {
            return Err(EngineError::new(
                ErrorCode::SchemaMismatch,
                "ensemble schema_version must be 1",
            ));
        }
        if self.members == 0 || self.workers == 0 || self.memory_budget_bytes == 0 {
            return Err(EngineError::new(
                ErrorCode::InvalidUnits,
                "members, workers, and memory budget must be positive",
            ));
        }
        for (name, v) in [
            ("wind_bias_std_m_s", self.wind_bias_std_m_s),
            ("dead_moisture_log_std", self.dead_moisture_log_std),
            ("live_moisture_log_std", self.live_moisture_log_std),
            ("front_bias_std_m", self.front_bias_std_m),
        ] {
            if !v.is_finite() || v < 0.0 {
                return Err(EngineError::new(
                    ErrorCode::InvalidUnits,
                    format!("{name} must be nonnegative"),
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SpeedMode {
    Physics,
    Uniform {
        d: [f64; 2],
        a: [f64; 2],
        bv: [f64; 2],
        spread: bool,
    },
}

#[derive(Debug, Clone)]
pub struct SpeedSchedule {
    pub epochs: Vec<(f64, SpeedMode)>,
}

impl Default for SpeedSchedule {
    fn default() -> Self {
        Self {
            epochs: vec![(0.0, SpeedMode::Physics)],
        }
    }
}
