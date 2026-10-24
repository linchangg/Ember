use fire_types::{EngineError, EngineResult, ErrorCode};

#[derive(Debug, Clone, serde::Serialize)]
pub struct BinaryMetrics {
    pub iou: Option<f64>,
    pub dice: Option<f64>,
    pub missed_fraction: Option<f64>,
    pub false_burn_area: f64,
}

pub fn binary_metrics(
    pred: &[u8],
    obs: &[u8],
    valid: &[u8],
    dx: f64,
    dy: f64,
) -> EngineResult<BinaryMetrics> {
    if pred.len() != obs.len() || pred.len() != valid.len() {
        return Err(EngineError::new(
            ErrorCode::InvalidArrayLength,
            "metric mask lengths differ",
        ));
    }
    if !dx.is_finite() || !dy.is_finite() || dx <= 0.0 || dy <= 0.0 {
        return Err(EngineError::new(
            ErrorCode::InvalidUnits,
            "metric cell size must be positive",
        ));
    }
    let mut both = 0u64;
    let mut pred_n = 0u64;
    let mut obs_n = 0u64;
    let mut inter = 0u64;
    let mut false_pos = 0u64;
    let mut miss = 0u64;
    for i in 0..pred.len() {
        if valid[i] != 1 {
            continue;
        }
        let b = pred[i] == 1;
        let o = obs[i] == 1;
        both += 1;
        if b {
            pred_n += 1;
        }
        if o {
            obs_n += 1;
        }
        if b && o {
            inter += 1;
        }
        if b && !o {
            false_pos += 1;
        }
        if o && !b {
            miss += 1;
        }
    }
    let area = dx * dy;
    let (iou, dice) = if both == 0 {
        (None, None)
    } else if pred_n == 0 && obs_n == 0 {
        (Some(1.0), Some(1.0))
    } else if pred_n == 0 || obs_n == 0 {
        (Some(0.0), Some(0.0))
    } else {
        let union = pred_n + obs_n - inter;
        (
            Some(inter as f64 / union as f64),
            Some(2.0 * inter as f64 / (pred_n + obs_n) as f64),
        )
    };
    let missed = if obs_n == 0 {
        None
    } else {
        Some(miss as f64 / obs_n as f64)
    };
    Ok(BinaryMetrics {
        iou,
        dice,
        missed_fraction: missed,
        false_burn_area: false_pos as f64 * area,
    })
}

pub fn brier(p: &[f64], y: &[u8], valid: &[u8]) -> EngineResult<Option<f64>> {
    if p.len() != y.len() || p.len() != valid.len() {
        return Err(EngineError::new(
            ErrorCode::InvalidArrayLength,
            "Brier arrays differ",
        ));
    }
    let mut s = 0.0;
    let mut n = 0.0;
    for i in 0..p.len() {
        if valid[i] != 1 || !p[i].is_finite() {
            continue;
        }
        if !(0.0..=1.0).contains(&p[i]) || y[i] > 1 {
            return Err(EngineError::new(
                ErrorCode::InvalidUnits,
                "invalid probability or binary target",
            ));
        }
        let yi = y[i] as f64;
        s += (p[i] - yi) * (p[i] - yi);
        n += 1.0;
    }
    Ok(if n == 0.0 { None } else { Some(s / n) })
}

pub fn arrival_mae(
    pred_t: &[f64],
    pred_status: &[u8],
    obs_t: &[f64],
    obs_valid: &[u8],
) -> EngineResult<(Option<f64>, u64, u64)> {
    if pred_t.len() != pred_status.len()
        || pred_t.len() != obs_t.len()
        || pred_t.len() != obs_valid.len()
    {
        return Err(EngineError::new(
            ErrorCode::InvalidArrayLength,
            "arrival metric arrays differ",
        ));
    }
    let mut se = 0.0;
    let mut n = 0u64;
    let mut missed = 0u64;
    for i in 0..pred_t.len() {
        if obs_valid[i] != 1 {
            continue;
        }
        if pred_status[i] != fire_types::ARRIVAL_MODELED {
            missed += 1;
            continue;
        }
        if !pred_t[i].is_finite() || !obs_t[i].is_finite() {
            return Err(EngineError::new(
                ErrorCode::NonFiniteInput,
                "arrival metric nonfinite time",
            ));
        }
        se += (pred_t[i] - obs_t[i]).abs();
        n += 1;
    }
    let mae = if n == 0 { None } else { Some(se / n as f64) };
    Ok((mae, missed, n))
}
