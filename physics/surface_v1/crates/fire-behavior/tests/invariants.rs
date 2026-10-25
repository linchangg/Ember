use fire_behavior::{
    alpha_bounds, behavior, behavior_with, hamiltonian_h, BehaviorOptions, FuelCatalog,
};
use fire_types::{LocalEnvironment, Moisture};
const CATALOG: &str = include_str!("../../../docs/spec/fuel_models.csv");
fn moisture() -> Moisture {
    Moisture {
        m1: 0.05,
        m10: 0.07,
        m100: 0.09,
        mlh: 0.6,
        mlw: 0.9,
    }
}
#[test]
fn terrain_and_wind_rotation_preserve_physical_rates() {
    let catalog = FuelCatalog::parse_csv(CATALOG).unwrap();
    for id in [1, 2, 4, 10, 101] {
        for env in [
            LocalEnvironment {
                wind_east_m_s: 1.0,
                wind_north_m_s: 0.4,
                gx: 0.3,
                gy: -0.1,
            },
            LocalEnvironment {
                wind_east_m_s: 0.0,
                wind_north_m_s: 0.0,
                gx: 0.2,
                gy: 0.4,
            },
        ] {
            let a = behavior(catalog.get(id).unwrap(), moisture(), env).unwrap();
            let b = behavior(
                catalog.get(id).unwrap(),
                moisture(),
                LocalEnvironment {
                    wind_east_m_s: -env.wind_north_m_s,
                    wind_north_m_s: env.wind_east_m_s,
                    gx: -env.gy,
                    gy: env.gx,
                },
            )
            .unwrap();
            assert!((a.r_head_m_s - b.r_head_m_s).abs() < 1e-12);
            for k in 0..32 {
                let theta = k as f64 * std::f64::consts::TAU / 32.0;
                let p = [theta.cos(), theta.sin()];
                let q = [-p[1], p[0]];
                let h = |e: fire_behavior::ellipse::ProjectedEllipse, p| {
                    hamiltonian_h(e.d, e.a_vec, e.bv, p)
                };
                assert!((h(a.ellipse, p) - h(b.ellipse, q)).abs() < 1e-12);
                let (ax, ay) = alpha_bounds(a.ellipse.d, a.ellipse.a_vec, a.ellipse.bv);
                let eps = 1e-6;
                let gradx = (h(a.ellipse, [p[0] + eps, p[1]]) - h(a.ellipse, [p[0] - eps, p[1]]))
                    / (2.0 * eps);
                let grady = (h(a.ellipse, [p[0], p[1] + eps]) - h(a.ellipse, [p[0], p[1] - eps]))
                    / (2.0 * eps);
                assert!(gradx.abs() <= ax + 1e-9 && grady.abs() <= ay + 1e-9);
                assert!(
                    (h(a.ellipse, [3.0 * p[0], 3.0 * p[1]]) - 3.0 * h(a.ellipse, p)).abs() < 1e-12
                );
            }
        }
    }
}
#[test]
fn nonfinite_local_inputs_rejected_even_when_extinct() {
    let catalog = FuelCatalog::parse_csv(CATALOG).unwrap();
    let fuel = catalog.get(1).unwrap();
    for m1 in [0.05, 0.2] {
        let mut m = moisture();
        m.m1 = m1;
        assert!(behavior(
            fuel,
            m,
            LocalEnvironment {
                wind_east_m_s: f64::NAN,
                wind_north_m_s: 0.0,
                gx: 0.0,
                gy: 0.0
            }
        )
        .is_err());
        assert!(behavior_with(
            fuel,
            m,
            LocalEnvironment {
                wind_east_m_s: 0.0,
                wind_north_m_s: 0.0,
                gx: 0.0,
                gy: 0.0
            },
            &BehaviorOptions {
                ellipse_max_lb: f64::NAN,
                ..BehaviorOptions::default()
            }
        )
        .is_err());
    }
}
