use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ErrorCode {
    SchemaMismatch,
    InvalidArrayLength,
    UnsupportedFuel,
    InvalidUnits,
    InvalidWindReference,
    MissingCoverage,
    NonFiniteInput,
    InvalidFuelParameter,
    InvalidInitialFront,
    TimeStepTooSmall,
    NumericalOverflow,
    CheckpointMismatch,
    MemoryBudgetExceeded,
    Cancelled,
}

impl ErrorCode {
    pub fn exit_code(self) -> i32 {
        match self {
            Self::TimeStepTooSmall | Self::NumericalOverflow => 3,
            Self::MemoryBudgetExceeded => 4,
            Self::Cancelled => 130,
            _ => 2,
        }
    }
}

#[derive(Debug, Error, Clone, Serialize, Deserialize)]
#[error("{code:?}: {message}")]
pub struct EngineError {
    pub code: ErrorCode,
    pub message: String,
    pub artifact: Option<String>,
    pub cell_index: Option<usize>,
    pub time_s: Option<f64>,
}

impl EngineError {
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            artifact: None,
            cell_index: None,
            time_s: None,
        }
    }

    pub fn artifact(mut self, artifact: impl Into<String>) -> Self {
        self.artifact = Some(artifact.into());
        self
    }

    pub fn cell(mut self, index: usize) -> Self {
        self.cell_index = Some(index);
        self
    }

    pub fn time(mut self, time_s: f64) -> Self {
        self.time_s = Some(time_s);
        self
    }
}

pub type EngineResult<T> = Result<T, EngineError>;
