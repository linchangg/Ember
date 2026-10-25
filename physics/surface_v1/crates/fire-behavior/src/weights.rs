use crate::fuel::{LifeState, Particle};

#[derive(Debug, Clone, Default)]
pub struct LifeGroup {
    pub particles: Vec<Particle>,
    pub f: Vec<f64>,
    pub a: f64,
    pub f_life: f64,
    pub wn: f64,
    pub mbar: f64,
    pub hbar: f64,
    pub sebar: f64,
}

#[derive(Debug, Clone)]
pub struct WeightedBed {
    pub dead: LifeGroup,
    pub live: LifeGroup,
    pub sigma: f64,
    pub a_total: f64,
}

pub fn sav_bin(sigma: f64) -> Option<usize> {
    if sigma >= 1200.0 {
        Some(0)
    } else if (192.0..1200.0).contains(&sigma) {
        Some(1)
    } else if (96.0..192.0).contains(&sigma) {
        Some(2)
    } else if (48.0..96.0).contains(&sigma) {
        Some(3)
    } else if (16.0..48.0).contains(&sigma) {
        Some(4)
    } else {
        None
    }
}

fn area(p: &Particle) -> f64 {
    p.w * p.sigma / p.rho_p
}

pub fn weight_bed(parts: &[Particle]) -> WeightedBed {
    let mut dead = LifeGroup::default();
    let mut live = LifeGroup::default();
    for p in parts {
        match p.life {
            LifeState::Dead => dead.particles.push(p.clone()),
            LifeState::Live => live.particles.push(p.clone()),
        }
    }
    fill_group(&mut dead);
    fill_group(&mut live);
    let a_total = dead.a + live.a;
    if a_total > 0.0 {
        dead.f_life = dead.a / a_total;
        live.f_life = live.a / a_total;
    }
    let sigma = dead.f_life
        * dead
            .particles
            .iter()
            .zip(dead.f.iter())
            .map(|(p, f)| f * p.sigma)
            .sum::<f64>()
        + live.f_life
            * live
                .particles
                .iter()
                .zip(live.f.iter())
                .map(|(p, f)| f * p.sigma)
                .sum::<f64>();
    WeightedBed {
        dead,
        live,
        sigma,
        a_total,
    }
}

fn fill_group(g: &mut LifeGroup) {
    g.a = g.particles.iter().map(area).sum();
    g.f = g
        .particles
        .iter()
        .map(|p| if g.a > 0.0 { area(p) / g.a } else { 0.0 })
        .collect();
    let mut bins = [0.0; 5];
    for (p, f) in g.particles.iter().zip(g.f.iter()) {
        if let Some(b) = sav_bin(p.sigma) {
            bins[b] += *f;
        }
    }
    let mut wn = 0.0;
    for p in &g.particles {
        if let Some(b) = sav_bin(p.sigma) {
            wn += bins[b] * p.w * (1.0 - p.s_t);
        }
    }
    g.wn = wn;
    g.mbar = g
        .particles
        .iter()
        .zip(g.f.iter())
        .map(|(p, f)| f * p.m)
        .sum();
    g.hbar = g
        .particles
        .iter()
        .zip(g.f.iter())
        .map(|(p, f)| f * p.h)
        .sum();
    g.sebar = g
        .particles
        .iter()
        .zip(g.f.iter())
        .map(|(p, f)| f * p.s_e)
        .sum();
}
