use fire_types::{ft_min_to_m_s, EngineError, EngineResult, ErrorCode};

use crate::reaction::finite;
use crate::wind::Vec3;

#[derive(Debug, Clone, Copy, Default)]
pub struct ProjectedEllipse {
    pub d: [f64; 2],
    pub a_vec: [f64; 2],
    pub bv: [f64; 2],
    pub a: f64,
    pub b: f64,
    pub c: f64,
    pub lb: f64,
    pub eccentricity: f64,
    pub r_head_m_s: f64,
    pub r_back_m_s: f64,
    pub lb_clamped: bool,
}

pub fn ellipse_from_head(
    r0_ft_min: f64,
    phi_eff: f64,
    u_eff_ft_min: f64,
    heading: Vec3,
    side: Vec3,
    ellipse_max_lb: f64,
) -> EngineResult<ProjectedEllipse> {
    let r_head_ft_min = r0_ft_min * (1.0 + phi_eff);
    finite(r_head_ft_min, "R_head")?;
    let r_head_m_s = ft_min_to_m_s(r_head_ft_min);
    let ue = ft_min_to_m_s(u_eff_ft_min);
    let lb_raw = 0.936 * (0.2566 * ue).exp() + 0.461 * (-0.1548 * ue).exp() - 0.397;
    finite(lb_raw, "LB_raw")?;
    if !ellipse_max_lb.is_finite() || ellipse_max_lb < 1.0 {
        return Err(EngineError::new(
            ErrorCode::InvalidUnits,
            "ellipse_max_lb must be >= 1",
        ));
    }
    let lb = lb_raw.clamp(1.0, ellipse_max_lb);
    let lb_clamped = lb != lb_raw;
    let eccentricity = (1.0 - 1.0 / (lb * lb)).max(0.0).sqrt();
    let r_back_m_s = r_head_m_s * (1.0 - eccentricity) / (1.0 + eccentricity);
    let a = (r_head_m_s + r_back_m_s) / 2.0;
    let b = a / lb;
    let c = (r_head_m_s - r_back_m_s) / 2.0;
    if a > 0.0 && ((a + c - r_head_m_s).abs() > 1e-12 || (a - c - r_back_m_s).abs() > 1e-12) {
        return Err(EngineError::new(
            ErrorCode::NumericalOverflow,
            "ellipse identity a±c failed",
        ));
    }
    Ok(ProjectedEllipse {
        d: [c * heading.x, c * heading.y],
        a_vec: [a * heading.x, a * heading.y],
        bv: [b * side.x, b * side.y],
        a,
        b,
        c,
        lb,
        eccentricity,
        r_head_m_s,
        r_back_m_s,
        lb_clamped,
    })
}

pub fn hamiltonian(d: [f64; 2], a: [f64; 2], bv: [f64; 2], p: [f64; 2]) -> f64 {
    if p[0] == 0.0 && p[1] == 0.0 {
        return 0.0;
    }
    let dp = d[0] * p[0] + d[1] * p[1];
    let ap = a[0] * p[0] + a[1] * p[1];
    let bp = bv[0] * p[0] + bv[1] * p[1];
    dp + (ap * ap + bp * bp).max(0.0).sqrt()
}

pub fn alpha_bounds(d: [f64; 2], a: [f64; 2], bv: [f64; 2]) -> (f64, f64) {
    let ax = d[0].abs() + (a[0] * a[0] + bv[0] * bv[0]).sqrt();
    let ay = d[1].abs() + (a[1] * a[1] + bv[1] * bv[1]).sqrt();
    (ax, ay)
}

pub fn radial_rate(r_head: f64, eccentricity: f64, theta: f64) -> f64 {
    r_head * (1.0 - eccentricity) / (1.0 - eccentricity * theta.cos())
}

#[cfg(test)]
mod tests {
    use super::*;
    use fire_types::fixture_close;

    #[test]
    fn zero_ellipse() {
        assert_eq!(
            hamiltonian([0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [1.0, 1.0]),
            0.0
        );
        assert_eq!(alpha_bounds([0.0, 0.0], [0.0, 0.0], [0.0, 0.0]), (0.0, 0.0));
    }

    #[test]
    fn identities_and_normal_vs_radial() {
        let a = 0.2_f64;
        let b = 0.1_f64;
        let c = (a * a - b * b).sqrt();
        let d = [c, 0.0];
        let av = [a, 0.0];
        let bv = [0.0, b];
        let h_head = hamiltonian(d, av, bv, [1.0, 0.0]);
        let h_back = hamiltonian(d, av, bv, [-1.0, 0.0]);
        let h_n = hamiltonian(d, av, bv, [0.0, 1.0]);
        assert!(h_head > 0.0 && h_back > 0.0);
        assert!(fixture_close(
            hamiltonian(d, av, bv, [2.0, 0.0]),
            2.0 * h_head
        ));
        assert!(fixture_close(h_n, b));
        let e = (1.0 - 1.0 / ((a / b) * (a / b))).sqrt();
        let r_radial_n = radial_rate(a + c, e, std::f64::consts::FRAC_PI_2);
        assert!(fixture_close(r_radial_n, (b * b) / a));
        assert!(!fixture_close(h_n, r_radial_n));
    }

    #[test]
    fn terrain_projection_isotropic() {
        let gx = 0.5;
        let r = 0.1;
        let h = crate::wind::lift_heading(gx, 0.0, 1.0, 0.0).unwrap();
        let n = crate::wind::Vec3::new(-gx, 0.0, 1.0).normalized().unwrap();
        let k = n.cross(h);
        let k = k.scale(1.0 / k.norm());
        let d = [0.0, 0.0];
        let av = [r * h.x, r * h.y];
        let bv = [r * k.x, r * k.y];
        let he = hamiltonian(d, av, bv, [1.0, 0.0]);
        let hn = hamiltonian(d, av, bv, [0.0, 1.0]);
        assert!(fixture_close(he, 0.1 / (1.25_f64).sqrt()));
        assert!(fixture_close(hn, 0.1));
    }
}
