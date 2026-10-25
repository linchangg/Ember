use serde::{Deserialize, Serialize};

use crate::error::{EngineError, EngineResult, ErrorCode};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GridSpec {
    pub width: u32,
    pub height: u32,
    pub cell_size_m: f64,
    pub x0_m: f64,
    pub y0_m: f64,
    pub crs: String,
}

impl GridSpec {
    pub fn validate(&self) -> EngineResult<()> {
        if self.width < 3 || self.height < 3 {
            return Err(EngineError::new(
                ErrorCode::InvalidArrayLength,
                "grid must be at least 3x3",
            ));
        }
        if self.cell_size_m <= 0.0 || !self.cell_size_m.is_finite() {
            return Err(EngineError::new(
                ErrorCode::InvalidUnits,
                "cell_size_m must be positive and finite",
            ));
        }
        if !self.x0_m.is_finite() || !self.y0_m.is_finite() {
            return Err(EngineError::new(
                ErrorCode::NonFiniteInput,
                "grid origin must be finite",
            ));
        }
        let crs = self.crs.to_ascii_uppercase();
        if crs.contains("4326") || crs.contains("WGS84") || crs.contains("LATLON") {
            return Err(EngineError::new(
                ErrorCode::InvalidUnits,
                "latitude/longitude grids are rejected in v1",
            ));
        }
        let _ = self.cell_count()?;
        Ok(())
    }

    pub fn cell_count(&self) -> EngineResult<usize> {
        let w = self.width as usize;
        let h = self.height as usize;
        w.checked_mul(h).ok_or_else(|| {
            EngineError::new(
                ErrorCode::InvalidArrayLength,
                "width*height overflows usize",
            )
        })
    }

    pub fn dx(&self) -> f64 {
        self.cell_size_m
    }

    pub fn dy(&self) -> f64 {
        self.cell_size_m
    }

    pub fn index(&self, row: u32, col: u32) -> usize {
        row as usize * self.width as usize + col as usize
    }

    pub fn row_col(&self, index: usize) -> (u32, u32) {
        let w = self.width as usize;
        ((index / w) as u32, (index % w) as u32)
    }

    pub fn center(&self, row: u32, col: u32) -> (f64, f64) {
        (
            self.x0_m + (col as f64 + 0.5) * self.dx(),
            self.y0_m + (row as f64 + 0.5) * self.dy(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn index_overflow_rejected() {
        let g = GridSpec {
            width: u32::MAX,
            height: u32::MAX,
            cell_size_m: 1.0,
            x0_m: 0.0,
            y0_m: 0.0,
            crs: "LOCAL_METRIC_TEST".into(),
        };
        let product = (g.width as usize).checked_mul(g.height as usize);
        match product {
            None => assert!(g.cell_count().is_err()),
            Some(n) => assert_eq!(g.cell_count().unwrap(), n),
        }
    }

    #[test]
    fn cell_center() {
        let g = GridSpec {
            width: 4,
            height: 4,
            cell_size_m: 10.0,
            x0_m: 0.0,
            y0_m: 0.0,
            crs: "LOCAL_METRIC_TEST".into(),
        };
        assert_eq!(g.center(0, 0), (5.0, 5.0));
        assert_eq!(g.index(1, 2), 6);
    }
}
