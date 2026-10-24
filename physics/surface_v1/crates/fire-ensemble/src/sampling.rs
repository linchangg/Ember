use fire_types::{EngineError, EngineResult, EnsembleConfig, EnsembleMemberMeta, ErrorCode};

const GOLDEN: u64 = 0x9E3779B97F4A7C15;
const M1: u64 = 0xBF58476D1CE4E5B9;
const M2: u64 = 0x94D049BB133111EB;

#[derive(Clone, Debug)]
pub struct SplitMix64 {
    pub state: u64,
}

impl SplitMix64 {
    pub fn new(state: u64) -> Self {
        Self { state }
    }

    pub fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(GOLDEN);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(M1);
        z = (z ^ (z >> 27)).wrapping_mul(M2);
        z ^ (z >> 31)
    }

    pub fn uniform_open(&mut self) -> f64 {
        let u = ((self.next_u64() >> 11) as f64) + 0.5;
        u / (1u64 << 53) as f64
    }

    pub fn box_muller_pair(&mut self) -> (f64, f64) {
        let u1 = self.uniform_open().clamp(f64::MIN_POSITIVE, 1.0);
        let u2 = self.uniform_open().clamp(f64::MIN_POSITIVE, 1.0);
        let r = (-2.0 * u1.ln()).sqrt();
        let th = 2.0 * std::f64::consts::PI * u2;
        (r * th.cos(), r * th.sin())
    }
}

pub fn scenario_seed(master: u64, k: u32) -> u64 {
    let mut gen = SplitMix64::new(master.wrapping_add((k as u64).wrapping_mul(GOLDEN)));
    gen.next_u64()
}

pub fn draw_member(config: &EnsembleConfig, k: u32) -> EngineResult<EnsembleMemberMeta> {
    config.validate()?;
    let seed = scenario_seed(config.master_seed, k);
    let mut rng = SplitMix64::new(seed);
    let (z_we, z_wn) = rng.box_muller_pair();
    let (z_d, z_l) = rng.box_muller_pair();
    let (z_f, _) = rng.box_muller_pair();
    let dead_std = config.dead_moisture_log_std;
    let live_std = config.live_moisture_log_std;
    Ok(EnsembleMemberMeta {
        member: k,
        seed,
        wind_east_bias_m_s: config.wind_bias_std_m_s * z_we,
        wind_north_bias_m_s: config.wind_bias_std_m_s * z_wn,
        dead_moisture_multiplier: (dead_std * z_d - 0.5 * dead_std * dead_std).exp(),
        live_moisture_multiplier: (live_std * z_l - 0.5 * live_std * live_std).exp(),
        front_bias_m: config.front_bias_std_m * z_f,
        failed: None,
    })
}

pub fn apply_perturbation(
    input: &mut fire_types::RunInput,
    meta: &EnsembleMemberMeta,
) -> EngineResult<()> {
    for frame in &mut input.environment.frames {
        for i in 0..frame.wind_east.len() {
            frame.wind_east[i] += meta.wind_east_bias_m_s;
            frame.wind_north[i] += meta.wind_north_bias_m_s;
            frame.m1[i] *= meta.dead_moisture_multiplier;
            frame.m10[i] *= meta.dead_moisture_multiplier;
            frame.m100[i] *= meta.dead_moisture_multiplier;
            frame.mlh[i] *= meta.live_moisture_multiplier;
            frame.mlw[i] *= meta.live_moisture_multiplier;
            if !frame.wind_east[i].is_finite() || !frame.wind_north[i].is_finite() {
                return Err(EngineError::new(
                    ErrorCode::NonFiniteInput,
                    "perturbed wind is nonfinite",
                ));
            }
        }
    }
    if meta.front_bias_m != 0.0 {
        for p in &mut input.initial_phi {
            *p -= meta.front_bias_m;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seeds_independent_of_draw_order_across_members() {
        let cfg = fire_types::EnsembleConfig {
            schema_version: 1,
            preset: "demo_global_bias_v1".into(),
            members: 4,
            master_seed: 12345,
            workers: 2,
            memory_budget_bytes: 1 << 30,
            wind_bias_std_m_s: 1.0,
            dead_moisture_log_std: 0.15,
            live_moisture_log_std: 0.15,
            front_bias_std_m: 0.0,
            allow_partial: false,
        };
        let a = draw_member(&cfg, 3).unwrap();
        let b = draw_member(&cfg, 3).unwrap();
        assert_eq!(a.seed, b.seed);
        assert_eq!(a.wind_east_bias_m_s, b.wind_east_bias_m_s);
        let c = draw_member(&cfg, 2).unwrap();
        assert_ne!(a.seed, c.seed);
    }
}
