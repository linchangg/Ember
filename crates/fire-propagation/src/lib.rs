#![forbid(unsafe_code)]
#![allow(clippy::too_many_arguments)]

pub mod arrival;
pub mod flux;
pub mod level_set;
pub mod timestep;

pub use fire_types::{AdvanceReport, RunOutput};

use std::sync::Arc;

use fire_behavior::{behavior_with, BehaviorOptions, FuelCatalog, LocalBehavior};
use fire_types::{
    modeled, BuildInfo, DiagnosticCounters, EngineError, EngineResult, ErrorCode, HorizonArrays,
    InitialState, LocalEnvironment, Moisture, RunInput, RunJson, SpeedMode, ValidationReport,
    ARRIVAL_INITIAL_UNKNOWN, ARRIVAL_INVALID, ARRIVAL_MODELED,
};

use crate::arrival::init_arrival;
use crate::level_set::{global_bounds, step_phi, CellSpeed};
use crate::timestep::{choose_dt, dt_cfl, event_eps, snap_event};

#[derive(Clone, Debug)]
pub struct PreparedLandscape {
    pub grid: fire_types::GridSpec,
    pub elevation: Vec<f64>,
    pub fuel_id: Vec<u16>,
    pub valid: Vec<u8>,
    pub burnable: Vec<u8>,
    pub gx: Vec<f64>,
    pub gy: Vec<f64>,
    pub terrain_incomplete: Vec<u8>,
    pub catalog: FuelCatalog,
    pub modeled: Vec<usize>,
}

#[derive(Clone, Debug)]
pub struct SimulationState {
    pub t: f64,
    pub phi: Vec<f64>,
    pub phi_scratch: Vec<f64>,
    pub arrival_time_s: Vec<f64>,
    pub arrival_status: Vec<u8>,
    pub initial_burned: Vec<u8>,
    pub env_frame_index: usize,
    pub next_output_index: usize,
    pub diagnostics: DiagnosticCounters,
    pub speeds: Vec<CellSpeed>,
    pub ax: f64,
    pub ay: f64,
    pub horizons_written: Vec<HorizonArrays>,
    pub quality_flags: Vec<String>,
}

pub fn validate_inputs(input: &RunInput) -> EngineResult<ValidationReport> {
    input.manifest.validate_schema()?;
    let n = input.manifest.grid.cell_count()?;
    for (name, len) in [
        ("elevation", input.elevation.len()),
        ("fuel_id", input.fuel_id.len()),
        ("valid", input.valid.len()),
        ("burnable", input.burnable.len()),
        ("initial_phi", input.initial_phi.len()),
    ] {
        if len != n {
            return Err(EngineError::new(
                ErrorCode::InvalidArrayLength,
                format!("{name} length {len} != {n}"),
            ));
        }
    }
    for (i, v) in input.valid.iter().enumerate() {
        if *v > 1 {
            return Err(
                EngineError::new(ErrorCode::NonFiniteInput, "valid must be 0 or 1").cell(i),
            );
        }
    }
    for (i, v) in input.burnable.iter().enumerate() {
        if *v > 1 {
            return Err(
                EngineError::new(ErrorCode::NonFiniteInput, "burnable must be 0 or 1").cell(i),
            );
        }
    }
    for (i, z) in input.elevation.iter().enumerate() {
        if !z.is_finite() {
            return Err(EngineError::new(ErrorCode::NonFiniteInput, "nonfinite elevation").cell(i));
        }
    }
    for (i, p) in input.initial_phi.iter().enumerate() {
        if !p.is_finite() {
            return Err(EngineError::new(ErrorCode::NonFiniteInput, "nonfinite phi").cell(i));
        }
    }
    let cat = FuelCatalog::parse_csv(&input.catalog_csv)?;
    let mut neg = 0usize;
    let mut pos = 0usize;
    for i in 0..n {
        if input.valid[i] == 1 && input.burnable[i] == 1 {
            cat.get(input.fuel_id[i])?;
            if input.initial_phi[i] == 0.0 {
                return Err(EngineError::new(
                    ErrorCode::InvalidInitialFront,
                    "exact-zero initial phi is rejected in v1",
                )
                .cell(i));
            }
            if input.initial_phi[i] < 0.0 {
                neg += 1;
            } else {
                pos += 1;
            }
        } else if input.initial_phi[i] <= 0.0 {
            return Err(EngineError::new(
                ErrorCode::InvalidInitialFront,
                "blocked cells must have positive initial phi",
            )
            .cell(i));
        }
    }
    if neg == 0 || pos == 0 {
        return Err(EngineError::new(
            ErrorCode::InvalidInitialFront,
            "need at least one burned and one unburned modeled cell",
        ));
    }
    reject_isolated_ignition(input, n)?;
    for frame in &input.environment.frames {
        for (name, arr) in [
            ("wind_east", &frame.wind_east),
            ("wind_north", &frame.wind_north),
            ("m1", &frame.m1),
            ("m10", &frame.m10),
            ("m100", &frame.m100),
            ("mlh", &frame.mlh),
            ("mlw", &frame.mlw),
        ] {
            if arr.len() != n {
                return Err(EngineError::new(
                    ErrorCode::InvalidArrayLength,
                    format!("{name} frame length"),
                ));
            }
            for (i, v) in arr.iter().enumerate() {
                if !v.is_finite() {
                    return Err(EngineError::new(
                        ErrorCode::NonFiniteInput,
                        format!("nonfinite {name}"),
                    )
                    .cell(i));
                }
                if name.starts_with('m') && *v < 0.0 {
                    return Err(EngineError::new(
                        ErrorCode::InvalidFuelParameter,
                        format!("{name} must be nonnegative"),
                    )
                    .cell(i));
                }
            }
        }
    }
    let last_h = input
        .manifest
        .simulation
        .horizons_s
        .iter()
        .copied()
        .fold(0.0, f64::max);
    if input.environment.coverage_end_s + 1e-12 < last_h {
        return Err(EngineError::new(
            ErrorCode::MissingCoverage,
            "environmental coverage ends before last horizon",
        ));
    }
    let mut report = ValidationReport::default();
    if input.manifest.initial.perimeter_activity
        == fire_types::PerimeterActivity::PartialActiveFrontUnsupported
    {
        report
            .quality_flags
            .push("partial_active_front_unsupported".into());
    }
    Ok(report)
}

fn reject_isolated_ignition(input: &RunInput, n: usize) -> EngineResult<()> {
    let w = input.manifest.grid.width as usize;
    let h = input.manifest.grid.height as usize;
    for i in 0..n {
        if input.valid[i] != 1 || input.burnable[i] != 1 || input.initial_phi[i] >= 0.0 {
            continue;
        }
        let r = i / w;
        let c = i % w;
        let mut neighbor = false;
        for dr in -1i32..=1 {
            for dc in -1i32..=1 {
                if dr == 0 && dc == 0 {
                    continue;
                }
                let rr = r as i32 + dr;
                let cc = c as i32 + dc;
                if rr < 0 || cc < 0 || rr >= h as i32 || cc >= w as i32 {
                    continue;
                }
                let j = rr as usize * w + cc as usize;
                if input.valid[j] == 1 && input.burnable[j] == 1 && input.initial_phi[j] < 0.0 {
                    neighbor = true;
                }
            }
        }
        if !neighbor {
            return Err(EngineError::new(
                ErrorCode::InvalidInitialFront,
                "isolated negative cell is an unresolved point ignition",
            )
            .cell(i));
        }
    }
    Ok(())
}

pub fn prepare(input: &RunInput) -> EngineResult<PreparedLandscape> {
    let _ = validate_inputs(input)?;
    let grid = input.manifest.grid.clone();
    let n = grid.cell_count()?;
    let catalog = FuelCatalog::parse_csv(&input.catalog_csv)?;
    let mut gx = vec![0.0; n];
    let mut gy = vec![0.0; n];
    let mut incomplete = vec![0u8; n];
    let w = grid.width;
    let h = grid.height;
    let dx = grid.dx();
    let dy = grid.dy();
    let mut modeled = Vec::new();
    for r in 0..h {
        for c in 0..w {
            let i = grid.index(r, c);
            if input.valid[i] == 1 && input.burnable[i] == 1 {
                modeled.push(i);
            }
            if input.valid[i] != 1 {
                continue;
            }
            gx[i] = gradient_component(
                &input.elevation,
                &input.valid,
                w,
                h,
                r,
                c,
                0,
                1,
                dx,
                &mut incomplete[i],
            );
            gy[i] = gradient_component(
                &input.elevation,
                &input.valid,
                w,
                h,
                r,
                c,
                1,
                0,
                dy,
                &mut incomplete[i],
            );
        }
    }
    Ok(PreparedLandscape {
        grid,
        elevation: input.elevation.clone(),
        fuel_id: input.fuel_id.clone(),
        valid: input.valid.clone(),
        burnable: input.burnable.clone(),
        gx,
        gy,
        terrain_incomplete: incomplete,
        catalog,
        modeled,
    })
}

fn gradient_component(
    z: &[f64],
    valid: &[u8],
    width: u32,
    height: u32,
    r: u32,
    c: u32,
    dr: i32,
    dc: i32,
    ds: f64,
    incomplete: &mut u8,
) -> f64 {
    let w = width as i32;
    let h = height as i32;
    let idx = |rr: i32, cc: i32| -> Option<usize> {
        if rr < 0 || cc < 0 || rr >= h || cc >= w {
            return None;
        }
        let j = rr as usize * width as usize + cc as usize;
        if valid[j] != 1 {
            None
        } else {
            Some(j)
        }
    };
    let plus = idx(r as i32 + dr, c as i32 + dc);
    let minus = idx(r as i32 - dr, c as i32 - dc);
    match (minus, plus) {
        (Some(a), Some(b)) => (z[b] - z[a]) / (2.0 * ds),
        (Some(a), None) => {
            *incomplete = 1;
            let i = r as usize * width as usize + c as usize;
            (z[i] - z[a]) / ds
        }
        (None, Some(b)) => {
            *incomplete = 1;
            let i = r as usize * width as usize + c as usize;
            (z[b] - z[i]) / ds
        }
        (None, None) => {
            *incomplete = 1;
            0.0
        }
    }
}

pub fn initialize(
    prepared: &PreparedLandscape,
    initial: &InitialState,
) -> EngineResult<SimulationState> {
    let n = prepared.grid.cell_count()?;
    if initial.phi.len() != n {
        return Err(EngineError::new(
            ErrorCode::InvalidArrayLength,
            "initial phi length",
        ));
    }
    let (arrival_time_s, arrival_status, initial_burned) =
        init_arrival(&initial.phi, &prepared.valid, &prepared.burnable);
    Ok(SimulationState {
        t: 0.0,
        phi: initial.phi.clone(),
        phi_scratch: initial.phi.clone(),
        arrival_time_s,
        arrival_status,
        initial_burned,
        env_frame_index: 0,
        next_output_index: 0,
        diagnostics: DiagnosticCounters::new(),
        speeds: vec![CellSpeed::default(); n],
        ax: 0.0,
        ay: 0.0,
        horizons_written: Vec::new(),
        quality_flags: Vec::new(),
    })
}

fn fill_speeds(
    prepared: &PreparedLandscape,
    input: &RunInput,
    state: &mut SimulationState,
) -> EngineResult<()> {
    let n = prepared.grid.cell_count()?;
    let t = state.t;
    let mode = schedule_mode(&input.speed_schedule, t);
    match mode {
        SpeedMode::Uniform { d, a, bv, spread } => {
            for i in 0..n {
                if modeled(prepared.valid[i], prepared.burnable[i]) {
                    state.speeds[i] = CellSpeed {
                        d,
                        a,
                        bv,
                        spread_active: spread,
                    };
                } else {
                    state.speeds[i] = CellSpeed::default();
                }
            }
        }
        SpeedMode::Physics => {
            let fi = input.environment.frame_index_at(t)?;
            state.env_frame_index = fi;
            let frame = &input.environment.frames[fi];
            let opts = BehaviorOptions {
                wind_limit: input.manifest.simulation.wind_limit,
                ellipse_max_lb: input.manifest.simulation.ellipse_max_lb,
            };
            for &i in &prepared.modeled {
                let moist = Moisture {
                    m1: frame.m1[i],
                    m10: frame.m10[i],
                    m100: frame.m100[i],
                    mlh: frame.mlh[i],
                    mlw: frame.mlw[i],
                };
                let env = LocalEnvironment {
                    wind_east_m_s: frame.wind_east[i],
                    wind_north_m_s: frame.wind_north[i],
                    gx: prepared.gx[i],
                    gy: prepared.gy[i],
                };
                let fuel = prepared.catalog.get(prepared.fuel_id[i])?;
                let b = behavior_with(fuel, moist, env, &opts)?;
                if b.wind_limited {
                    state.diagnostics.wind_limit_counts += 1;
                }
                if b.lb_clamped {
                    state.diagnostics.lb_clamp_counts += 1;
                }
                if b.live_only {
                    push_flag(&mut state.quality_flags, "unusual_live_only_bed");
                }
                let ell = b.ellipse;
                state.speeds[i] = CellSpeed {
                    d: ell.d,
                    a: ell.a_vec,
                    bv: ell.bv,
                    spread_active: b.spread_active,
                };
            }
        }
    }
    let (ax, ay) = global_bounds(&state.speeds, &prepared.modeled);
    state.ax = ax;
    state.ay = ay;
    Ok(())
}

fn schedule_mode(schedule: &fire_types::SpeedSchedule, t: f64) -> SpeedMode {
    let mut mode = SpeedMode::Physics;
    for (tk, m) in &schedule.epochs {
        if *tk <= t + 1e-15 {
            mode = *m;
        }
    }
    mode
}

fn push_flag(flags: &mut Vec<String>, f: &str) {
    if !flags.iter().any(|x| x == f) {
        flags.push(f.to_string());
    }
}

fn next_event_time(input: &RunInput, state: &SimulationState, until_s: f64) -> f64 {
    let mut next = until_s;
    if let Some(tk) = input.environment.next_frame_time(state.t) {
        if tk < next {
            next = tk;
        }
    }
    if let Some(tk) = input
        .speed_schedule
        .epochs
        .iter()
        .map(|e| e.0)
        .find(|tk| *tk > state.t + 1e-15)
    {
        if tk < next {
            next = tk;
        }
    }
    for h in &input.manifest.simulation.horizons_s {
        if *h > state.t + 1e-15 && *h < next {
            next = *h;
        }
    }
    next
}

fn capture_horizon(prepared: &PreparedLandscape, state: &mut SimulationState, time_s: f64) {
    if state
        .horizons_written
        .iter()
        .any(|h| (h.time_s - time_s).abs() <= event_eps(time_s))
    {
        return;
    }
    let h = fire_io_horizon(prepared, state, time_s);
    state.horizons_written.push(h);
    inspect_front_flags(prepared, state);
}

fn fire_io_horizon(
    prepared: &PreparedLandscape,
    state: &SimulationState,
    time_s: f64,
) -> HorizonArrays {
    // local copy to avoid fire-io dependency from propagation
    let n = state.phi.len();
    let mut burned = vec![0u8; n];
    let mut front = vec![0u8; n];
    let w = prepared.grid.width;
    let h = prepared.grid.height;
    for i in 0..n {
        if prepared.valid[i] == 1 && prepared.burnable[i] == 1 && state.phi[i] <= 0.0 {
            burned[i] = 1;
        }
        if prepared.valid[i] != 1 || prepared.burnable[i] != 1 {
            continue;
        }
        let r = (i / w as usize) as i32;
        let c = (i % w as usize) as i32;
        for (rr, cc) in [(r, c - 1), (r, c + 1), (r - 1, c), (r + 1, c)] {
            if rr < 0 || cc < 0 || rr >= h as i32 || cc >= w as i32 {
                continue;
            }
            let j = rr as usize * w as usize + cc as usize;
            if prepared.valid[j] == 1
                && prepared.burnable[j] == 1
                && (state.phi[i] > 0.0) != (state.phi[j] > 0.0)
            {
                front[i] = 1;
                break;
            }
        }
    }
    HorizonArrays {
        time_s,
        phi: state.phi.clone(),
        burned,
        front_cells: front,
    }
}

fn inspect_front_flags(prepared: &PreparedLandscape, state: &mut SimulationState) {
    let w = prepared.grid.width;
    let h = prepared.grid.height;
    for r in 0..h {
        for c in 0..w {
            let i = prepared.grid.index(r, c);
            let burned = state.phi[i] <= 0.0 && modeled(prepared.valid[i], prepared.burnable[i]);
            let last = state.horizons_written.last();
            let front = last.map(|x| x.front_cells[i] == 1).unwrap_or(false);
            if !(burned || front) {
                continue;
            }
            if r == 0 || c == 0 || r + 1 == h || c + 1 == w {
                push_flag(&mut state.quality_flags, "front_at_outer_boundary");
            }
            for (rr, cc) in [
                (r as i32, c as i32 - 1),
                (r as i32, c as i32 + 1),
                (r as i32 - 1, c as i32),
                (r as i32 + 1, c as i32),
            ] {
                if rr < 0 || cc < 0 || rr >= h as i32 || cc >= w as i32 {
                    continue;
                }
                let j = prepared.grid.index(rr as u32, cc as u32);
                if prepared.valid[j] != 1 {
                    push_flag(&mut state.quality_flags, "front_near_invalid");
                } else if prepared.burnable[j] != 1 {
                    push_flag(&mut state.quality_flags, "front_near_blocked");
                }
            }
        }
    }
}

pub fn advance(
    state: &mut SimulationState,
    prepared: &PreparedLandscape,
    input: &RunInput,
    until_s: f64,
) -> EngineResult<AdvanceReport> {
    if until_s < state.t {
        return Err(EngineError::new(
            ErrorCode::InvalidUnits,
            "advance target is before current time",
        ));
    }
    fill_speeds(prepared, input, state)?;
    let mut events = 0usize;
    let steps_before = state.diagnostics.step_count;
    let dx = prepared.grid.dx();
    let dy = prepared.grid.dy();
    let cfl = input.manifest.simulation.cfl;
    let max_step = input.manifest.simulation.max_step_s;
    loop {
        state.t = snap_event(state.t, until_s);
        let horizons: Vec<f64> = input.manifest.simulation.horizons_s.clone();
        for h in &horizons {
            if (*h - state.t).abs() <= event_eps(state.t.max(*h))
                && *h <= until_s + event_eps(until_s)
            {
                capture_horizon(prepared, state, *h);
                events += 1;
            }
        }
        if state.t + event_eps(state.t.max(until_s)) >= until_s {
            break;
        }
        let env_now = input.environment.frame_index_at(state.t)?;
        if env_now != state.env_frame_index {
            fill_speeds(prepared, input, state)?;
        }
        let next = next_event_time(input, state, until_s);
        let dt_bound = dt_cfl(cfl, state.ax, state.ay, dx, dy);
        let dt = choose_dt(state.t, dt_bound, max_step, next)?;
        if dt == 0.0 {
            state.t = snap_event(next.min(until_s), next);
            fill_speeds(prepared, input, state)?;
            continue;
        }
        let mut irr = 0u64;
        step_phi(
            &state.phi,
            &mut state.phi_scratch,
            &state.speeds,
            &prepared.valid,
            &prepared.burnable,
            prepared.grid.width,
            prepared.grid.height,
            dx,
            dy,
            state.ax,
            state.ay,
            state.t,
            dt,
            &mut state.arrival_status,
            &mut state.arrival_time_s,
            &mut irr,
        )?;
        state.diagnostics.irreversibility_constraints += irr;
        std::mem::swap(&mut state.phi, &mut state.phi_scratch);
        state.t += dt;
        state.diagnostics.record_step(dt);
        let env_after = input.environment.frame_index_at(state.t.min(until_s))?;
        let speed_change = input
            .speed_schedule
            .epochs
            .iter()
            .any(|(tk, _)| (*tk - state.t).abs() <= event_eps(state.t.max(*tk)));
        if env_after != state.env_frame_index || speed_change {
            fill_speeds(prepared, input, state)?;
        }
    }
    Ok(AdvanceReport {
        time_s: state.t,
        steps: state.diagnostics.step_count - steps_before,
        events_processed: events,
    })
}

pub fn output_from_state(
    input: &RunInput,
    prepared: &PreparedLandscape,
    state: &SimulationState,
    until: f64,
    extra_flags: Vec<String>,
) -> RunOutput {
    let mut flags = extra_flags;
    flags.extend(state.quality_flags.iter().cloned());
    flags.sort();
    flags.dedup();
    let configuration = serde_json::json!({
        "simulation": input.manifest.simulation,
        "model_conventions": {
            "curing": "endpoint_preserving_1_20_0_90",
            "reaction_velocity": "albini_behave",
            "wind_limit": input.manifest.simulation.wind_limit,
            "projected_ellipse_hamiltonian": true,
            "reinitialize": false
        }
    });
    let run = RunJson {
        schema_version: 1,
        status: "complete".into(),
        model: "surface_v1".into(),
        solver: "level_set_lf_euler_v1".into(),
        incident_id: input.manifest.incident_id.clone(),
        start_utc: input.manifest.start_utc.clone(),
        completed_until_s: until,
        horizons_s: input.manifest.simulation.horizons_s.clone(),
        input_manifest_sha256: input.manifest_sha256.clone(),
        input_artifacts: input.artifacts.iter().map(Into::into).collect(),
        build: BuildInfo::default(),
        configuration,
        quality_flags: flags,
        parent_run: None,
    };
    let diagnostics = serde_json::to_value(&state.diagnostics).unwrap_or(serde_json::json!({}));
    RunOutput {
        run,
        grid: prepared.grid.clone(),
        arrival_time_s: state.arrival_time_s.clone(),
        arrival_status: state.arrival_status.clone(),
        horizons: state.horizons_written.clone(),
        diagnostics,
    }
}

pub fn run(input: &RunInput) -> EngineResult<RunOutput> {
    let report = validate_inputs(input)?;
    let prepared = Arc::new(prepare(input)?);
    let initial = InitialState {
        phi: input.initial_phi.clone(),
    };
    let mut state = initialize(&prepared, &initial)?;
    state.quality_flags.extend(report.quality_flags.clone());
    if prepared.terrain_incomplete.contains(&1) {
        push_flag(&mut state.quality_flags, "terrain_gradient_incomplete");
    }
    warn_distance(&prepared, &state.phi, &mut state.quality_flags);
    let until = input
        .manifest
        .simulation
        .horizons_s
        .iter()
        .copied()
        .fold(0.0, f64::max);
    advance(&mut state, &prepared, input, until)?;
    Ok(output_from_state(
        input,
        &prepared,
        &state,
        until,
        report.warnings,
    ))
}

fn warn_distance(prepared: &PreparedLandscape, phi: &[f64], flags: &mut Vec<String>) {
    let mut near = 0usize;
    let mut bad = 0usize;
    let dx = prepared.grid.dx();
    let w = prepared.grid.width;
    let h = prepared.grid.height;
    for &i in &prepared.modeled {
        if phi[i].abs() > 3.0 * dx {
            continue;
        }
        let r = (i / w as usize) as i32;
        let c = (i % w as usize) as i32;
        if r == 0 || c == 0 || r + 1 == h as i32 || c + 1 == w as i32 {
            continue;
        }
        let east = i + 1;
        let west = i - 1;
        let north = i + w as usize;
        let south = i - w as usize;
        if !modeled(prepared.valid[east], prepared.burnable[east])
            || !modeled(prepared.valid[west], prepared.burnable[west])
            || !modeled(prepared.valid[north], prepared.burnable[north])
            || !modeled(prepared.valid[south], prepared.burnable[south])
        {
            continue;
        }
        let gx = (phi[east] - phi[west]) / (2.0 * dx);
        let gy = (phi[north] - phi[south]) / (2.0 * dx);
        let nrm = gx.hypot(gy);
        near += 1;
        if !(0.5..=1.5).contains(&nrm) {
            bad += 1;
        }
    }
    if near > 0 && (bad as f64) / (near as f64) > 0.10 {
        push_flag(flags, "distance_gradient_warning");
    }
}

pub fn extract_front_points(
    phi: &[f64],
    valid: &[u8],
    burnable: &[u8],
    grid: &fire_types::GridSpec,
) -> Vec<(f64, f64)> {
    let w = grid.width as usize;
    let h = grid.height as usize;
    let dx = grid.dx();
    let mut pts = Vec::new();
    let ok = |r: i32, c: i32| {
        if r < 0 || c < 0 || r >= h as i32 || c >= w as i32 {
            return false;
        }
        let i = r as usize * w + c as usize;
        valid[i] == 1 && burnable[i] == 1
    };
    for r in 0..h as i32 {
        for c in 0..w as i32 {
            if !ok(r, c) {
                continue;
            }
            let i = r as usize * w + c as usize;
            let (xi, yi) = grid.center(r as u32, c as u32);
            if phi[i] == 0.0 {
                pts.push((xi, yi));
            }
            for (dr, dc) in [(0, 1), (1, 0)] {
                let r2 = r + dr;
                let c2 = c + dc;
                if !ok(r2, c2) {
                    continue;
                }
                let j = r2 as usize * w + c2 as usize;
                if (phi[i] > 0.0) == (phi[j] > 0.0) && phi[i] != 0.0 && phi[j] != 0.0 {
                    continue;
                }
                if phi[i] == phi[j] {
                    continue;
                }
                let lam = phi[i] / (phi[i] - phi[j]);
                let (xj, yj) = grid.center(r2 as u32, c2 as u32);
                pts.push((xi + lam * (xj - xi), yi + lam * (yj - yi)));
            }
        }
    }
    let mut uniq = Vec::new();
    for p in pts {
        if !uniq
            .iter()
            .any(|q: &(f64, f64)| (p.0 - q.0).hypot(p.1 - q.1) <= 1e-9)
        {
            uniq.push(p);
        }
    }
    let _ = dx;
    uniq
}

pub fn cell_behavior(
    prepared: &PreparedLandscape,
    input: &RunInput,
    i: usize,
    t: f64,
) -> EngineResult<LocalBehavior> {
    let fi = input.environment.frame_index_at(t)?;
    let frame = &input.environment.frames[fi];
    let opts = BehaviorOptions {
        wind_limit: input.manifest.simulation.wind_limit,
        ellipse_max_lb: input.manifest.simulation.ellipse_max_lb,
    };
    let moist = Moisture {
        m1: frame.m1[i],
        m10: frame.m10[i],
        m100: frame.m100[i],
        mlh: frame.mlh[i],
        mlw: frame.mlw[i],
    };
    let env = LocalEnvironment {
        wind_east_m_s: frame.wind_east[i],
        wind_north_m_s: frame.wind_north[i],
        gx: prepared.gx[i],
        gy: prepared.gy[i],
    };
    behavior_with(
        prepared.catalog.get(prepared.fuel_id[i])?,
        moist,
        env,
        &opts,
    )
}

// keep arrival constants referenced for documentation
#[allow(dead_code)]
fn _status_docs() -> [u8; 4] {
    [
        ARRIVAL_INVALID,
        fire_types::ARRIVAL_NOT_REACHED,
        ARRIVAL_INITIAL_UNKNOWN,
        ARRIVAL_MODELED,
    ]
}
