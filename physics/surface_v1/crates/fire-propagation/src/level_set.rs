use fire_types::{EngineError, EngineResult, ErrorCode, ARRIVAL_INITIAL_UNKNOWN, ARRIVAL_INVALID};

use crate::arrival::maybe_arrive;
use crate::flux::{derivatives, numerical_hamiltonian};

#[derive(Clone, Debug, Default)]
pub struct CellSpeed {
    pub d: [f64; 2],
    pub a: [f64; 2],
    pub bv: [f64; 2],
    pub spread_active: bool,
}

pub fn global_bounds(speeds: &[CellSpeed], modeled: &[usize]) -> (f64, f64) {
    let mut ax = 0.0;
    let mut ay = 0.0;
    for &i in modeled {
        if !speeds[i].spread_active {
            continue;
        }
        let (x, y) = fire_behavior::alpha_bounds(speeds[i].d, speeds[i].a, speeds[i].bv);
        if x > ax {
            ax = x;
        }
        if y > ay {
            ay = y;
        }
    }
    (ax, ay)
}

pub fn step_phi(
    phi_old: &[f64],
    phi_new: &mut [f64],
    speeds: &[CellSpeed],
    valid: &[u8],
    burnable: &[u8],
    width: u32,
    height: u32,
    dx: f64,
    dy: f64,
    ax: f64,
    ay: f64,
    t: f64,
    dt: f64,
    arrival_status: &mut [u8],
    arrival_time: &mut [f64],
    irreversibility: &mut u64,
) -> EngineResult<()> {
    let n = phi_old.len();
    phi_new.copy_from_slice(phi_old);
    for i in 0..n {
        if valid[i] != 1 || burnable[i] != 1 {
            continue;
        }
        if !speeds[i].spread_active {
            phi_new[i] = phi_old[i];
            continue;
        }
        let (dxm, dxp, dym, dyp, px, py) =
            derivatives(phi_old, valid, burnable, width, height, i, dx, dy);
        let hlf = numerical_hamiltonian(
            speeds[i].d,
            speeds[i].a,
            speeds[i].bv,
            px,
            py,
            dxm,
            dxp,
            dym,
            dyp,
            ax,
            ay,
        );
        if !hlf.is_finite() {
            return Err(EngineError::new(
                ErrorCode::NumericalOverflow,
                "nonfinite numerical Hamiltonian",
            )
            .cell(i)
            .time(t));
        }
        let cand = phi_old[i] - dt * hlf;
        if !cand.is_finite() {
            return Err(
                EngineError::new(ErrorCode::NumericalOverflow, "nonfinite phi update")
                    .cell(i)
                    .time(t),
            );
        }
        let newv = phi_old[i].min(cand);
        if newv != cand && phi_old[i].abs() <= 3.0 * dx {
            *irreversibility += 1;
        }
        maybe_arrive(i, t, dt, phi_old[i], newv, arrival_status, arrival_time);
        phi_new[i] = newv;
    }
    let _ = (ARRIVAL_INVALID, ARRIVAL_INITIAL_UNKNOWN);
    Ok(())
}
