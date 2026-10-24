use criterion::{criterion_group, criterion_main, Criterion};
use fire_propagation::level_set::{global_bounds, step_phi, CellSpeed};

fn bench_step(c: &mut Criterion) {
    let width = 128u32;
    let height = 128u32;
    let n = (width * height) as usize;
    let phi = vec![1.0; n];
    let mut phi_new = vec![1.0; n];
    let speeds = vec![
        CellSpeed {
            d: [0.0, 0.0],
            a: [0.1, 0.0],
            bv: [0.0, 0.1],
            spread_active: true,
        };
        n
    ];
    let valid = vec![1u8; n];
    let burnable = vec![1u8; n];
    let modeled: Vec<usize> = (0..n).collect();
    let (ax, ay) = global_bounds(&speeds, &modeled);
    let mut status = vec![1u8; n];
    let mut time = vec![0.0; n];
    c.bench_function("lf_step_128", |b| {
        b.iter(|| {
            let mut irr = 0u64;
            step_phi(
                &phi,
                &mut phi_new,
                &speeds,
                &valid,
                &burnable,
                width,
                height,
                30.0,
                30.0,
                ax,
                ay,
                0.0,
                1.0,
                &mut status,
                &mut time,
                &mut irr,
            )
            .unwrap();
        })
    });
}

criterion_group!(benches, bench_step);
criterion_main!(benches);
