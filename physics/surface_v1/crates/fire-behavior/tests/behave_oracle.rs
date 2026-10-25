use fire_behavior::{behavior_with, BehaviorOptions, FuelCatalog};
use fire_types::{LocalEnvironment, Moisture, WindLimitPolicy};
#[test]
fn independent_usfs_behave_reference() {
    let catalog =
        FuelCatalog::parse_csv(include_str!("../../../docs/spec/fuel_models.csv")).unwrap();
    let cases: serde_json::Value =
        serde_json::from_str(include_str!("../../../tools/oracle/behave_reference.json")).unwrap();
    for case in cases.as_array().unwrap() {
        let id = case["fuel"].as_u64().unwrap() as u16;
        // The six GR1/1.20 cases deliberately have a different curing formula;
        // they are tested separately below, never absorbed into numerical tolerance.
        if id == 101 && case["mlh"].as_f64().unwrap() == 1.20 {
            continue;
        }
        let wind = case["wind_m_s"].as_f64().unwrap();
        let b = behavior_with(
            catalog.get(id).unwrap(),
            Moisture {
                m1: 0.05,
                m10: 0.07,
                m100: 0.09,
                mlh: case["mlh"].as_f64().unwrap(),
                mlw: 0.90,
            },
            LocalEnvironment {
                wind_east_m_s: wind,
                wind_north_m_s: 0.0,
                gx: case["slope"].as_f64().unwrap(),
                gy: 0.0,
            },
            &BehaviorOptions {
                wind_limit: WindLimitPolicy::None,
                ellipse_max_lb: 8.0,
            },
        )
        .unwrap();
        for (name, actual) in [
            ("r_head_m_s", b.r_head_m_s),
            ("ir", b.ir),
            ("h_sink", b.h_sink),
            ("beta", b.beta),
        ] {
            let expected = case[name].as_f64().unwrap();
            assert!(
                (actual - expected).abs() <= 1e-9 * expected.abs().max(1e-6),
                "fuel={id} wind={wind} {name}: Rust={actual} Behave={expected}"
            );
        }
    }
}

#[test]
fn project_wind_cap_is_explicit_and_preserves_heading() {
    let catalog =
        FuelCatalog::parse_csv(include_str!("../../../docs/spec/fuel_models.csv")).unwrap();
    let moisture = Moisture {
        m1: 0.05,
        m10: 0.07,
        m100: 0.09,
        mlh: 0.30,
        mlw: 0.90,
    };
    let env = LocalEnvironment {
        wind_east_m_s: 3.0,
        wind_north_m_s: 0.0,
        gx: 0.0,
        gy: 0.0,
    };
    let capped = behavior_with(
        catalog.get(101).unwrap(),
        moisture,
        env,
        &BehaviorOptions::default(),
    )
    .unwrap();
    let uncapped = behavior_with(
        catalog.get(101).unwrap(),
        moisture,
        env,
        &BehaviorOptions {
            wind_limit: WindLimitPolicy::None,
            ellipse_max_lb: 8.0,
        },
    )
    .unwrap();
    assert!(capped.wind_limited);
    assert!(!uncapped.wind_limited);
    assert!((capped.u_eff_ft_min - 0.9 * capped.ir).abs() < 1e-10);
    assert!(capped.r_head_m_s < uncapped.r_head_m_s);
    assert_eq!(capped.ellipse.d[1], 0.0);
    assert!((uncapped.r_head_m_s - 0.20508268332189447).abs() < 1e-10);
}

#[test]
fn documented_behave_curing_rounding_difference() {
    let catalog =
        FuelCatalog::parse_csv(include_str!("../../../docs/spec/fuel_models.csv")).unwrap();
    let cases: serde_json::Value =
        serde_json::from_str(include_str!("../../../tools/oracle/behave_reference.json")).unwrap();
    let mut count = 0;
    for case in cases
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["fuel"] == 101 && c["mlh"].as_f64().unwrap() == 1.20)
    {
        let b = behavior_with(
            catalog.get(101).unwrap(),
            Moisture {
                m1: 0.05,
                m10: 0.07,
                m100: 0.09,
                mlh: 1.20,
                mlw: 0.90,
            },
            LocalEnvironment {
                wind_east_m_s: case["wind_m_s"].as_f64().unwrap(),
                wind_north_m_s: 0.0,
                gx: case["slope"].as_f64().unwrap(),
                gy: 0.0,
            },
            &BehaviorOptions {
                wind_limit: WindLimitPolicy::None,
                ellipse_max_lb: 8.0,
            },
        )
        .unwrap();
        assert_eq!(b.cured_fraction, 0.0);
        // Upstream's 1.333 - 1.11*1.20 = .001 differs from the project's exact 0 endpoint.
        let expected = case["r_head_m_s"].as_f64().unwrap();
        let relative = (b.r_head_m_s - expected) / expected;
        assert!(
            relative < -0.003 && relative > -0.005,
            "unexpected convention delta {relative}"
        );
        count += 1;
    }
    assert_eq!(count, 6);
}
