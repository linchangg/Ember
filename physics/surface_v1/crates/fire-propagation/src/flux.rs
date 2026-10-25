use fire_behavior::ellipse::hamiltonian;

pub fn neighbor_phi(
    phi: &[f64],
    valid: &[u8],
    burnable: &[u8],
    width: u32,
    height: u32,
    i: usize,
    rr: i32,
    cc: i32,
) -> f64 {
    let w = width as usize;
    if rr < 0 || cc < 0 || rr >= height as i32 || cc >= width as i32 {
        return phi[i];
    }
    let j = rr as usize * w + cc as usize;
    if valid[j] != 1 || burnable[j] != 1 {
        phi[i]
    } else {
        phi[j]
    }
}

pub fn derivatives(
    phi: &[f64],
    valid: &[u8],
    burnable: &[u8],
    width: u32,
    height: u32,
    i: usize,
    dx: f64,
    dy: f64,
) -> (f64, f64, f64, f64, f64, f64) {
    let w = width as usize;
    let r = (i / w) as i32;
    let c = (i % w) as i32;
    let west = neighbor_phi(phi, valid, burnable, width, height, i, r, c - 1);
    let east = neighbor_phi(phi, valid, burnable, width, height, i, r, c + 1);
    let south = neighbor_phi(phi, valid, burnable, width, height, i, r - 1, c);
    let north = neighbor_phi(phi, valid, burnable, width, height, i, r + 1, c);
    let dxm = (phi[i] - west) / dx;
    let dxp = (east - phi[i]) / dx;
    let dym = (phi[i] - south) / dy;
    let dyp = (north - phi[i]) / dy;
    let px = 0.5 * (dxm + dxp);
    let py = 0.5 * (dym + dyp);
    (dxm, dxp, dym, dyp, px, py)
}

pub fn numerical_hamiltonian(
    d: [f64; 2],
    a: [f64; 2],
    bv: [f64; 2],
    px: f64,
    py: f64,
    dxm: f64,
    dxp: f64,
    dym: f64,
    dyp: f64,
    ax: f64,
    ay: f64,
) -> f64 {
    hamiltonian(d, a, bv, [px, py]) - 0.5 * ax * (dxp - dxm) - 0.5 * ay * (dyp - dym)
}
