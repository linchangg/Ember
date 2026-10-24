#![forbid(unsafe_code)]

pub mod ellipse;
pub mod fuel;
pub mod reaction;
pub mod weights;
pub mod wind;

use fire_types::{ft_min_to_m_s, EngineResult, LocalEnvironment, Moisture, WindLimitPolicy};

use crate::ellipse::{ellipse_from_head, hamiltonian, ProjectedEllipse};
use crate::fuel::{particles_for, FuelModel};
use crate::reaction::packing_and_reaction;
use crate::weights::weight_bed;
use crate::wind::wind_slope_factors;

pub use crate::ellipse::{alpha_bounds, hamiltonian as hamiltonian_h};
pub use crate::fuel::{curing_fraction, FuelCatalog, Particle};

#[derive(Debug, Clone)]
pub struct BehaviorOptions {
    pub wind_limit: WindLimitPolicy,
    pub ellipse_max_lb: f64,
}

impl Default for BehaviorOptions {
    fn default() -> Self {
        Self {
            wind_limit: WindLimitPolicy::Effective0p9Ir,
            ellipse_max_lb: 8.0,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct LocalBehavior {
    pub cured_fraction: f64,
    pub sigma: f64,
    pub beta: f64,
    pub r_beta: f64,
    pub gamma: f64,
    pub ir: f64,
    pub h_sink: f64,
    pub xi: f64,
    pub r0_ft_min: f64,
    pub r0_m_s: f64,
    pub r_head_m_s: f64,
    pub r_back_m_s: f64,
    pub lb: f64,
    pub eccentricity: f64,
    pub a: f64,
    pub b: f64,
    pub c: f64,
    pub phi_w: f64,
    pub phi_s: f64,
    pub phi_eff: f64,
    pub u_eff_ft_min: f64,
    pub wind_limited: bool,
    pub lb_clamped: bool,
    pub spread_active: bool,
    pub live_only: bool,
    pub wn_dead: f64,
    pub wn_live: f64,
    pub mbar_dead: f64,
    pub mbar_live: f64,
    pub eta_m_dead: f64,
    pub eta_m_live: f64,
    pub eta_s_dead: f64,
    pub eta_s_live: f64,
    pub f_dead: f64,
    pub f_live: f64,
    pub ir_dead: f64,
    pub ir_live: f64,
    pub mx_live: f64,
    pub rho_b: f64,
    pub beta_op: f64,
    pub c_wind: f64,
    pub b_wind: f64,
    pub e_wind: f64,
    pub ellipse: ProjectedEllipse,
}

pub fn behavior(
    fuel: &FuelModel,
    moisture: Moisture,
    env: LocalEnvironment,
) -> EngineResult<LocalBehavior> {
    behavior_with(fuel, moisture, env, &BehaviorOptions::default())
}

pub fn behavior_with(
    fuel: &FuelModel,
    moisture: Moisture,
    env: LocalEnvironment,
    opts: &BehaviorOptions,
) -> EngineResult<LocalBehavior> {
    for value in [env.wind_east_m_s, env.wind_north_m_s, env.gx, env.gy] {
        fire_types::require_finite(value, "local environment")?;
    }
    if !opts.ellipse_max_lb.is_finite() || !(1.0..=8.0).contains(&opts.ellipse_max_lb) {
        return Err(fire_types::EngineError::new(
            fire_types::ErrorCode::InvalidUnits,
            "ellipse_max_lb must be in [1,8]",
        ));
    }
    let (parts, fc) = particles_for(fuel, moisture)?;
    if parts.is_empty() {
        return Ok(LocalBehavior {
            cured_fraction: fc,
            lb: 1.0,
            ..LocalBehavior::default()
        });
    }
    let bed = weight_bed(&parts);
    let react = packing_and_reaction(fuel.depth_ft, fuel.mx_dead, &parts, &bed)?;
    let mut out = LocalBehavior {
        cured_fraction: fc,
        sigma: bed.sigma,
        beta: react.beta,
        r_beta: react.r_beta,
        gamma: react.gamma,
        ir: react.ir,
        h_sink: react.h_sink,
        xi: react.xi,
        r0_ft_min: react.r0_ft_min,
        r0_m_s: ft_min_to_m_s(react.r0_ft_min),
        wn_dead: bed.dead.wn,
        wn_live: bed.live.wn,
        mbar_dead: bed.dead.mbar,
        mbar_live: bed.live.mbar,
        eta_m_dead: react.eta_m_dead,
        eta_m_live: react.eta_m_live,
        eta_s_dead: react.eta_s_dead,
        eta_s_live: react.eta_s_live,
        f_dead: bed.dead.f_life,
        f_live: bed.live.f_life,
        ir_dead: react.ir_dead,
        ir_live: react.ir_live,
        mx_live: react.mx_live,
        rho_b: react.rho_b,
        beta_op: react.beta_op,
        live_only: react.live_only,
        lb: 1.0,
        ..LocalBehavior::default()
    };
    if react.ir == 0.0 {
        return Ok(out);
    }
    let enh = wind_slope_factors(
        bed.sigma,
        react.beta,
        react.r_beta,
        react.ir,
        env,
        opts.wind_limit,
    )?;
    let ell = ellipse_from_head(
        react.r0_ft_min,
        enh.phi_eff,
        enh.u_eff_ft_min,
        enh.heading,
        enh.side,
        opts.ellipse_max_lb,
    )?;
    out.r0_m_s = ft_min_to_m_s(react.r0_ft_min);
    out.r_head_m_s = ell.r_head_m_s;
    out.r_back_m_s = ell.r_back_m_s;
    out.lb = ell.lb;
    out.eccentricity = ell.eccentricity;
    out.a = ell.a;
    out.b = ell.b;
    out.c = ell.c;
    out.phi_w = enh.phi_w;
    out.phi_s = enh.phi_s;
    out.phi_eff = enh.phi_eff;
    out.u_eff_ft_min = enh.u_eff_ft_min;
    out.wind_limited = enh.wind_limited;
    out.lb_clamped = ell.lb_clamped;
    out.spread_active = ell.r_head_m_s > 0.0;
    out.c_wind = enh.c_wind;
    out.b_wind = enh.b_wind;
    out.e_wind = enh.e_wind;
    out.ellipse = ell;
    Ok(out)
}

pub fn head_intensity_potential_kw_m(beh: &LocalBehavior) -> Option<f64> {
    if beh.sigma <= 0.0 || beh.ir <= 0.0 {
        return None;
    }
    let residence_time_min = 384.0 / beh.sigma;
    let heat_per_area = beh.ir * residence_time_min;
    let r_head_ft_min = beh.r_head_m_s / ft_min_to_m_s(1.0);
    let i_btu = heat_per_area * (r_head_ft_min / 60.0);
    Some(i_btu * fire_types::BTU_FT_S_TO_KW_M)
}

pub fn inspect_json(beh: &LocalBehavior) -> serde_json::Value {
    serde_json::json!({
        "cured_fraction": beh.cured_fraction,
        "sigma": beh.sigma,
        "beta": beh.beta,
        "r_beta": beh.r_beta,
        "gamma": beh.gamma,
        "ir": beh.ir,
        "h_sink": beh.h_sink,
        "xi": beh.xi,
        "r0_m_s": beh.r0_m_s,
        "r_head_m_s": beh.r_head_m_s,
        "r_back_m_s": beh.r_back_m_s,
        "lb": beh.lb,
        "a": beh.a,
        "b": beh.b,
        "c": beh.c,
        "phi_w": beh.phi_w,
        "phi_s": beh.phi_s,
        "phi_eff": beh.phi_eff,
        "u_eff_ft_min": beh.u_eff_ft_min,
        "wind_limited": beh.wind_limited,
        "Wn": {"dead": beh.wn_dead, "live": beh.wn_live},
        "Mbar": {"dead": beh.mbar_dead, "live": beh.mbar_live},
        "eta_M": {"dead": beh.eta_m_dead, "live": beh.eta_m_live},
        "eta_S": {"dead": beh.eta_s_dead, "live": beh.eta_s_live},
        "F_l": {"dead": beh.f_dead, "live": beh.f_live},
        "IR_l": {"dead": beh.ir_dead, "live": beh.ir_live},
        "Mx_l": beh.mx_live,
        "rho_b": beh.rho_b,
        "beta_op": beh.beta_op,
        "C": beh.c_wind,
        "B": beh.b_wind,
        "E": beh.e_wind,
        "hamiltonian_east": hamiltonian(beh.ellipse.d, beh.ellipse.a_vec, beh.ellipse.bv, [1.0, 0.0]),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fuel::FuelCatalog;
    use fire_types::{fixture_close, Moisture};

    const CATALOG: &str = include_str!("../../../docs/spec/fuel_models.csv");

    fn moist() -> Moisture {
        Moisture {
            m1: 0.05,
            m10: 0.07,
            m100: 0.09,
            mlh: 0.60,
            mlw: 0.90,
        }
    }

    fn env0() -> LocalEnvironment {
        LocalEnvironment {
            wind_east_m_s: 0.0,
            wind_north_m_s: 0.0,
            gx: 0.0,
            gy: 0.0,
        }
    }

    #[test]
    fn appendix_a_fm1_fm2_fm4() {
        let cat = FuelCatalog::parse_csv(CATALOG).unwrap();
        let cases = [
            (
                1,
                3500.0,
                0.0010625,
                14.201359906473266,
                842.6651820057328,
                9.995229885125415,
                0.024733996158492002,
            ),
            (
                2,
                2784.016729706139,
                0.00575,
                15.897033163076461,
                3625.4191568692754,
                64.01525474217043,
                0.015289511177370011,
            ),
            (
                4,
                1739.2378306290284,
                0.003833333333333333,
                14.155579218905377,
                12362.7718237753,
                78.22245472881315,
                0.025859989270422667,
            ),
        ];
        for (id, sigma, beta, gamma, ir, h_sink, r0) in cases {
            let b = behavior(cat.get(id).unwrap(), moist(), env0()).unwrap();
            assert!(fixture_close(b.sigma, sigma), "sigma {id}");
            assert!(fixture_close(b.beta, beta), "beta {id}");
            assert!(
                fixture_close(b.gamma, gamma),
                "gamma {id} {} vs {gamma}",
                b.gamma
            );
            assert!(fixture_close(b.ir, ir), "ir {id} {} vs {ir}", b.ir);
            assert!(fixture_close(b.h_sink, h_sink), "hsink {id}");
            assert!(fixture_close(b.r0_m_s, r0), "r0 {id} {} vs {r0}", b.r0_m_s);
        }
        let wind = LocalEnvironment {
            wind_east_m_s: 1.0,
            ..env0()
        };
        let b = behavior(cat.get(1).unwrap(), moist(), wind).unwrap();
        assert!(
            fixture_close(b.r_head_m_s, 0.12490103558085179),
            "{}",
            b.r_head_m_s
        );
        assert!(fixture_close(b.lb, 1.207692585557591), "{}", b.lb);
    }

    #[test]
    fn extinction_zero() {
        let cat = FuelCatalog::parse_csv(CATALOG).unwrap();
        for m1 in [0.12, 0.20] {
            let mut m = moist();
            m.m1 = m1;
            let b = behavior(cat.get(1).unwrap(), m, env0()).unwrap();
            assert!(fixture_close(b.ir, 0.0));
            assert!(fixture_close(b.r0_m_s, 0.0));
        }
    }
}
