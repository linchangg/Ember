use fire_types::{
    m_s_to_ft_min, EngineError, EngineResult, ErrorCode, LocalEnvironment, WindLimitPolicy,
};

use crate::reaction::finite;

#[derive(Debug, Clone, Copy)]
pub struct Vec3 {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}

impl Vec3 {
    pub fn new(x: f64, y: f64, z: f64) -> Self {
        Self { x, y, z }
    }

    pub fn dot(self, o: Self) -> f64 {
        self.x * o.x + self.y * o.y + self.z * o.z
    }

    pub fn norm(self) -> f64 {
        self.dot(self).sqrt()
    }

    pub fn normalized(self) -> EngineResult<Self> {
        let n = self.norm();
        if n <= 0.0 || !n.is_finite() {
            return Err(EngineError::new(
                ErrorCode::NumericalOverflow,
                "cannot normalize zero vector",
            ));
        }
        Ok(Self {
            x: self.x / n,
            y: self.y / n,
            z: self.z / n,
        })
    }

    pub fn cross(self, o: Self) -> Self {
        Self {
            x: self.y * o.z - self.z * o.y,
            y: self.z * o.x - self.x * o.z,
            z: self.x * o.y - self.y * o.x,
        }
    }

    pub fn scale(self, s: f64) -> Self {
        Self {
            x: self.x * s,
            y: self.y * s,
            z: self.z * s,
        }
    }

    pub fn plus(self, o: Self) -> Self {
        Self {
            x: self.x + o.x,
            y: self.y + o.y,
            z: self.z + o.z,
        }
    }
}

#[derive(Debug, Clone)]
pub struct TerrainBasis {
    pub s: f64,
    pub e_up: Vec3,
    pub e_cross: Vec3,
}

pub fn terrain_basis(gx: f64, gy: f64) -> TerrainBasis {
    let s = gx.hypot(gy);
    if s > 1e-12 {
        let q = (1.0 + s * s).sqrt();
        TerrainBasis {
            s,
            e_up: Vec3::new(gx / (s * q), gy / (s * q), s / q),
            e_cross: Vec3::new(-gy / s, gx / s, 0.0),
        }
    } else {
        TerrainBasis {
            s,
            e_up: Vec3::new(1.0, 0.0, 0.0),
            e_cross: Vec3::new(0.0, 1.0, 0.0),
        }
    }
}

pub fn lift_heading(gx: f64, gy: f64, dxh: f64, dyh: f64) -> EngineResult<Vec3> {
    Vec3::new(dxh, dyh, gx * dxh + gy * dyh).normalized()
}

#[derive(Debug, Clone)]
pub struct Enhancement {
    pub phi_w: f64,
    pub phi_s: f64,
    pub phi_eff: f64,
    pub u_eff_ft_min: f64,
    pub wind_limited: bool,
    pub heading: Vec3,
    pub side: Vec3,
    pub e_up: Vec3,
    pub e_cross: Vec3,
    pub c_wind: f64,
    pub b_wind: f64,
    pub e_wind: f64,
}

pub fn wind_slope_factors(
    sigma: f64,
    beta: f64,
    r_beta: f64,
    ir: f64,
    env: LocalEnvironment,
    wind_limit: WindLimitPolicy,
) -> EngineResult<Enhancement> {
    let c_wind = 7.47 * (-0.133 * sigma.powf(0.55)).exp();
    let b_wind = 0.02526 * sigma.powf(0.54);
    let e_wind = 0.715 * (-0.000359 * sigma).exp();
    finite(c_wind, "C")?;
    finite(b_wind, "B")?;
    finite(e_wind, "E")?;
    let u = env.wind_east_m_s.hypot(env.wind_north_m_s);
    let u_ft_min = m_s_to_ft_min(u);
    let phi_w = if u == 0.0 {
        0.0
    } else {
        c_wind * u_ft_min.powf(b_wind) * r_beta.powf(-e_wind)
    };
    finite(phi_w, "wind_factor")?;
    let basis = terrain_basis(env.gx, env.gy);
    let phi_s = 5.275 * beta.powf(-0.3) * basis.s * basis.s;
    finite(phi_s, "slope_factor")?;
    let w_hat = if u > 0.0 {
        lift_heading(
            env.gx,
            env.gy,
            env.wind_east_m_s / u,
            env.wind_north_m_s / u,
        )?
    } else {
        Vec3::new(0.0, 0.0, 0.0)
    };
    let v_phi = w_hat.scale(phi_w).plus(basis.e_up.scale(phi_s));
    let phi_eff_raw = v_phi.norm();
    let heading = if phi_eff_raw > 1e-12 {
        v_phi.scale(1.0 / phi_eff_raw)
    } else {
        basis.e_up
    };
    let n = Vec3::new(-env.gx, -env.gy, 1.0).normalized()?;
    let mut k = n.cross(heading);
    let kn = k.norm();
    if kn <= 0.0 {
        return Err(EngineError::new(
            ErrorCode::NumericalOverflow,
            "degenerate ellipse side vector",
        ));
    }
    k = k.scale(1.0 / kn);
    let h_norm = heading.norm();
    if (h_norm - 1.0).abs() > 1e-8 || (k.norm() - 1.0).abs() > 1e-8 || heading.dot(k).abs() > 1e-6 {
        return Err(EngineError::new(
            ErrorCode::NumericalOverflow,
            "heading/side are not orthonormal",
        ));
    }

    let u_eff_raw = if phi_eff_raw > 0.0 {
        (phi_eff_raw / (c_wind * r_beta.powf(-e_wind))).powf(1.0 / b_wind)
    } else {
        0.0
    };
    finite(u_eff_raw, "U_eff_raw")?;
    let u_limit = 0.9 * ir;
    let (u_eff, wind_limited) = match wind_limit {
        WindLimitPolicy::Effective0p9Ir => {
            let limited = u_eff_raw > u_limit && u_eff_raw > 0.0;
            (u_eff_raw.min(u_limit), limited)
        }
        WindLimitPolicy::None => (u_eff_raw, false),
    };
    let phi_eff = if u_eff == 0.0 {
        0.0
    } else {
        c_wind * u_eff.powf(b_wind) * r_beta.powf(-e_wind)
    };
    finite(phi_eff, "phi_eff")?;
    Ok(Enhancement {
        phi_w,
        phi_s,
        phi_eff,
        u_eff_ft_min: u_eff,
        wind_limited,
        heading,
        side: k,
        e_up: basis.e_up,
        e_cross: basis.e_cross,
        c_wind,
        b_wind,
        e_wind,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use fire_types::fixture_close;

    #[test]
    fn terrain_basis_example() {
        let b = terrain_basis(0.3, 0.4);
        assert!(fixture_close(b.s, 0.5));
        assert!(fixture_close(b.e_up.norm(), 1.0));
        assert!(fixture_close(b.e_cross.norm(), 1.0));
        assert!(fixture_close(b.e_up.dot(b.e_cross), 0.0));
    }
}
