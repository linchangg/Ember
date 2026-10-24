use fire_types::{ARRIVAL_INITIAL_UNKNOWN, ARRIVAL_INVALID, ARRIVAL_MODELED, ARRIVAL_NOT_REACHED};

pub fn init_arrival(phi: &[f64], valid: &[u8], burnable: &[u8]) -> (Vec<f64>, Vec<u8>, Vec<u8>) {
    let n = phi.len();
    let time = vec![0.0; n];
    let mut status = vec![ARRIVAL_INVALID; n];
    let mut initial_burned = vec![0u8; n];
    for i in 0..n {
        if valid[i] != 1 {
            continue;
        }
        if burnable[i] != 1 {
            status[i] = ARRIVAL_NOT_REACHED;
            continue;
        }
        if phi[i] < 0.0 {
            status[i] = ARRIVAL_INITIAL_UNKNOWN;
            initial_burned[i] = 1;
        } else {
            status[i] = ARRIVAL_NOT_REACHED;
        }
    }
    (time, status, initial_burned)
}

pub fn maybe_arrive(
    i: usize,
    t: f64,
    dt: f64,
    phi_old: f64,
    phi_new: f64,
    status: &mut [u8],
    time: &mut [f64],
) {
    if status[i] != ARRIVAL_NOT_REACHED {
        return;
    }
    if phi_old > 0.0 && phi_new <= 0.0 {
        let den = phi_old - phi_new;
        let frac = if den == 0.0 { 1.0 } else { phi_old / den };
        time[i] = t + dt * frac;
        status[i] = ARRIVAL_MODELED;
    }
}
