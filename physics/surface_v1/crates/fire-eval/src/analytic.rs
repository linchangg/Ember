use fire_behavior::ellipse::hamiltonian;
use fire_propagation::extract_front_points;
use fire_types::GridSpec;

pub fn support(points: &[(f64, f64)], center: (f64, f64), n: (f64, f64)) -> f64 {
    points
        .iter()
        .map(|p| (p.0 - center.0) * n.0 + (p.1 - center.1) * n.1)
        .fold(f64::NEG_INFINITY, f64::max)
}

pub fn support_errors(
    points: &[(f64, f64)],
    center: (f64, f64),
    analytic: impl Fn((f64, f64)) -> f64,
    n_dir: usize,
) -> (f64, f64) {
    let mut errs = Vec::new();
    for k in 0..n_dir {
        let th = 2.0 * std::f64::consts::PI * (k as f64) / (n_dir as f64);
        let n = (th.cos(), th.sin());
        let num = support(points, center, n);
        errs.push((num - analytic(n)).abs());
    }
    let max = errs.iter().copied().fold(0.0, f64::max);
    let mean = errs.iter().sum::<f64>() / errs.len() as f64;
    (max, mean)
}

pub fn front_points(
    phi: &[f64],
    valid: &[u8],
    burnable: &[u8],
    grid: &GridSpec,
) -> Vec<(f64, f64)> {
    extract_front_points(phi, valid, burnable, grid)
}

pub fn ellipse_hamiltonian(
    a: f64,
    b: f64,
    c: f64,
    heading_east: bool,
) -> impl Fn((f64, f64)) -> f64 {
    let (d, av, bv) = if heading_east {
        ([c, 0.0], [a, 0.0], [0.0, b])
    } else {
        ([0.0, c], [0.0, a], [-b, 0.0])
    };
    move |n| hamiltonian(d, av, bv, [n.0, n.1])
}
