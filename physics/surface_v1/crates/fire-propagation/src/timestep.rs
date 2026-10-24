use fire_types::{EngineError, EngineResult, ErrorCode};

pub const EVENT_REL: f64 = 1e-9;
pub const MIN_STEP_S: f64 = 1e-6;

pub fn event_eps(t: f64) -> f64 {
    EVENT_REL * 1.0_f64.max(t.abs())
}

pub fn snap_event(t: f64, event: f64) -> f64 {
    if (t - event).abs() <= event_eps(t.max(event)) {
        event
    } else {
        t
    }
}

pub fn dt_cfl(cfl: f64, ax: f64, ay: f64, dx: f64, dy: f64) -> f64 {
    let den = ax / dx + ay / dy;
    if den > 0.0 {
        cfl / den
    } else {
        f64::INFINITY
    }
}

pub fn choose_dt(t: f64, dt_cfl: f64, max_step_s: f64, next_event: f64) -> EngineResult<f64> {
    let remaining = next_event - t;
    if remaining <= event_eps(t.max(next_event)) {
        return Ok(0.0);
    }
    let dt = dt_cfl.min(max_step_s).min(remaining);
    if !dt.is_finite() || dt <= 0.0 {
        return Err(EngineError::new(
            ErrorCode::TimeStepTooSmall,
            format!("non-positive time step at t={t}"),
        )
        .time(t));
    }
    if dt < MIN_STEP_S {
        return Err(EngineError::new(
            ErrorCode::TimeStepTooSmall,
            format!("dt={dt} s below {MIN_STEP_S} at t={t}"),
        )
        .time(t));
    }
    Ok(dt)
}

#[cfg(test)]
mod tests {
    use super::*;
    use fire_types::fixture_close;

    #[test]
    fn cfl_example() {
        assert!(fixture_close(dt_cfl(0.4, 0.2, 0.0, 10.0, 10.0), 20.0));
    }
}
