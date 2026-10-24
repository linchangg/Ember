use crate::error::{EngineError, EngineResult, ErrorCode};

pub const METERS_PER_FOOT: f64 = 0.3048;
pub const KG_M2_PER_LB_FT2: f64 = 4.88242763638305;
pub const KG_M3_PER_LB_FT3: f64 = 16.0184633739601;
pub const J_PER_KG_PER_BTU_LB: f64 = 2326.00032491728;
pub const BTU_FT2_MIN_TO_KW_M2: f64 = 1055.05585262 / (0.09290304 * 60.0 * 1000.0);
pub const BTU_FT_S_TO_KW_M: f64 = 1055.05585262 / (0.3048 * 1000.0);

pub fn meters_to_feet(m: f64) -> f64 {
    m / METERS_PER_FOOT
}

pub fn kg_m2_to_lb_ft2(v: f64) -> f64 {
    v / KG_M2_PER_LB_FT2
}

pub fn kg_m3_to_lb_ft3(v: f64) -> f64 {
    v / KG_M3_PER_LB_FT3
}

pub fn sav_m_to_ft(sav_per_m: f64) -> f64 {
    sav_per_m * METERS_PER_FOOT
}

pub fn m_s_to_ft_min(v: f64) -> f64 {
    v * 60.0 / METERS_PER_FOOT
}

pub fn ft_min_to_m_s(v: f64) -> f64 {
    v * METERS_PER_FOOT / 60.0
}

pub fn btu_lb_to_j_kg(v: f64) -> f64 {
    v * J_PER_KG_PER_BTU_LB
}

pub fn require_finite(value: f64, what: &str) -> EngineResult<f64> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(EngineError::new(
            ErrorCode::NonFiniteInput,
            format!("non-finite value for {what}"),
        ))
    }
}

pub fn relative_close(a: f64, b: f64, atol: f64, rtol: f64) -> bool {
    (a - b).abs() <= atol + rtol * a.abs().max(b.abs())
}

pub fn fixture_close(a: f64, b: f64) -> bool {
    relative_close(a, b, 1e-10, 1e-8)
}

pub fn wind_to_from_bearing(speed_m_s: f64, from_deg_cw_from_north: f64) -> (f64, f64) {
    let rad = from_deg_cw_from_north.to_radians();
    (-speed_m_s * rad.sin(), -speed_m_s * rad.cos())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_ft_min_is_si() {
        assert!(fixture_close(ft_min_to_m_s(1.0), 0.00508));
    }

    #[test]
    fn wind_from_north() {
        let (u, v) = wind_to_from_bearing(2.0, 0.0);
        assert!(fixture_close(u, 0.0));
        assert!(fixture_close(v, -2.0));
    }
}
