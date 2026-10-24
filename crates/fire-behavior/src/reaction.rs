use fire_types::{EngineError, EngineResult, ErrorCode};

use crate::fuel::Particle;
use crate::weights::WeightedBed;

#[derive(Debug, Clone, Default)]
pub struct ReactionTerms {
    pub rho_b: f64,
    pub beta: f64,
    pub beta_op: f64,
    pub r_beta: f64,
    pub mx_live: f64,
    pub mfd: f64,
    pub eta_m_dead: f64,
    pub eta_m_live: f64,
    pub eta_s_dead: f64,
    pub eta_s_live: f64,
    pub a_gamma: f64,
    pub gamma_max: f64,
    pub gamma: f64,
    pub ir_dead: f64,
    pub ir_live: f64,
    pub ir: f64,
    pub h_sink: f64,
    pub xi: f64,
    pub r0_ft_min: f64,
    pub live_only: bool,
}

pub fn packing_and_reaction(
    model_depth_ft: f64,
    mx_dead: f64,
    parts: &[Particle],
    bed: &WeightedBed,
) -> EngineResult<ReactionTerms> {
    if parts.is_empty() {
        return Ok(ReactionTerms::default());
    }
    if !positive(model_depth_ft) || !positive(mx_dead) {
        return Err(EngineError::new(
            ErrorCode::InvalidFuelParameter,
            "fuel depth and dead extinction moisture must be positive",
        ));
    }
    for p in parts {
        if !positive(p.sigma) || !positive(p.rho_p) {
            return Err(EngineError::new(
                ErrorCode::InvalidFuelParameter,
                "particle SAV and density must be positive for nonzero load",
            ));
        }
        if !positive(p.s_e) {
            return Err(EngineError::new(
                ErrorCode::InvalidFuelParameter,
                "S_e must be positive for nonzero-load particles",
            ));
        }
    }
    let wsum: f64 = parts.iter().map(|p| p.w).sum();
    let rho_b = wsum / model_depth_ft;
    let beta: f64 = parts.iter().map(|p| p.w / (model_depth_ft * p.rho_p)).sum();
    if !positive(beta) {
        return Err(EngineError::new(
            ErrorCode::InvalidFuelParameter,
            "nonpositive packing ratio",
        ));
    }
    let sigma = bed.sigma;
    if !positive(sigma) {
        return Err(EngineError::new(
            ErrorCode::InvalidFuelParameter,
            "nonpositive characteristic SAV",
        ));
    }
    let beta_op = 3.348 * sigma.powf(-0.8189);
    finite(beta_op, "beta_op")?;
    let r_beta = beta / beta_op;
    if !positive(r_beta) {
        return Err(EngineError::new(
            ErrorCode::InvalidFuelParameter,
            "r_beta must be positive",
        ));
    }

    let mut d = 0.0;
    let mut d_m = 0.0;
    for p in &bed.dead.particles {
        let w = p.w * (-138.0 / p.sigma).exp();
        d += w;
        d_m += w * p.m;
    }
    let mfd = if d > 0.0 { d_m / d } else { 0.0 };
    let mut live_mass_term = 0.0;
    for p in &bed.live.particles {
        live_mass_term += p.w * (-500.0 / p.sigma).exp();
    }
    let live_only = bed.dead.particles.is_empty() && !bed.live.particles.is_empty();
    let mx_live = if !bed.live.particles.is_empty() && live_mass_term > 0.0 {
        mx_dead.max(2.9 * (d / live_mass_term) * (1.0 - mfd / mx_dead) - 0.226)
    } else {
        mx_dead
    };

    let (eta_m_dead, eta_s_dead) =
        damping(&bed.dead.particles, bed.dead.mbar, bed.dead.sebar, mx_dead)?;
    let (eta_m_live, eta_s_live) = if bed.live.particles.is_empty() || live_mass_term == 0.0 {
        (0.0, 0.0)
    } else {
        damping(&bed.live.particles, bed.live.mbar, bed.live.sebar, mx_live)?
    };

    let a_gamma = 133.0 * sigma.powf(-0.7913);
    let sig15 = sigma.powf(1.5);
    let gamma_max = sig15 / (495.0 + 0.0594 * sig15);
    let gamma = gamma_max * (a_gamma * (r_beta.ln() + 1.0 - r_beta)).exp();
    finite(gamma, "reaction velocity")?;
    let ir_dead = gamma * bed.dead.wn * bed.dead.hbar * eta_m_dead * eta_s_dead;
    let ir_live = gamma * bed.live.wn * bed.live.hbar * eta_m_live * eta_s_live;
    let ir = ir_dead + ir_live;
    finite(ir, "reaction intensity")?;

    let mut terms = ReactionTerms {
        rho_b,
        beta,
        beta_op,
        r_beta,
        mx_live,
        mfd,
        eta_m_dead,
        eta_m_live,
        eta_s_dead,
        eta_s_live,
        a_gamma,
        gamma_max,
        gamma,
        ir_dead,
        ir_live,
        ir,
        live_only,
        ..ReactionTerms::default()
    };
    if ir == 0.0 {
        return Ok(terms);
    }

    let mut h_sink = 0.0;
    for (group, f_life) in [(&bed.dead, bed.dead.f_life), (&bed.live, bed.live.f_life)] {
        let mut inner = 0.0;
        for (p, f) in group.particles.iter().zip(group.f.iter()) {
            let eps = (-138.0 / p.sigma).exp();
            let qig = 250.0 + 1116.0 * p.m;
            inner += f * eps * qig;
        }
        h_sink += rho_b * f_life * inner;
    }
    if !positive(h_sink) {
        return Err(EngineError::new(
            ErrorCode::InvalidFuelParameter,
            "nonpositive heat sink with positive IR",
        ));
    }
    let xi = ((0.792 + 0.681 * sigma.sqrt()) * (beta + 0.1)).exp() / (192.0 + 0.2595 * sigma);
    finite(xi, "propagating flux ratio")?;
    let r0_ft_min = ir * xi / h_sink;
    finite(r0_ft_min, "R0")?;
    terms.h_sink = h_sink;
    terms.xi = xi;
    terms.r0_ft_min = r0_ft_min;
    Ok(terms)
}

fn damping(particles: &[Particle], mbar: f64, sebar: f64, mx: f64) -> EngineResult<(f64, f64)> {
    if particles.is_empty() {
        return Ok((0.0, 0.0));
    }
    let r_m = mbar / mx;
    let eta_m = if r_m >= 1.0 {
        0.0
    } else {
        (1.0 - 2.59 * r_m + 5.11 * r_m * r_m - 3.52 * r_m * r_m * r_m).clamp(0.0, 1.0)
    };
    if !positive(sebar) {
        return Err(EngineError::new(
            ErrorCode::InvalidFuelParameter,
            "effective mineral fraction must be positive",
        ));
    }
    let eta_s = (0.174 * sebar.powf(-0.19)).min(1.0);
    Ok((eta_m, eta_s))
}

fn positive(value: f64) -> bool {
    value.is_finite() && value > 0.0
}

pub fn finite(value: f64, what: &str) -> EngineResult<f64> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(EngineError::new(
            ErrorCode::NumericalOverflow,
            format!("nonfinite {what}"),
        ))
    }
}
