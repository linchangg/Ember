//! Versioned file IPC: Python owns posterior inference; Rust owns all propagation.
#![forbid(unsafe_code)]
use fire_propagation::{advance, initialize, prepare, SimulationState};
use fire_types::*;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::{
    io::Write,
    path::{Path, PathBuf},
};

const VERSION: u32 = 1;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    protocol_version: u32,
    manifest: PathBuf,
    gx: PathBuf,
    gy: PathBuf,
    issue_s: f64,
    until_s: f64,
    out: PathBuf,
    workers: usize,
    memory_budget_bytes: u64,
    urban: bool,
    members: Vec<Member>,
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct Member {
    ros_mult: f64,
    wind_speed_mult: f64,
    wind_dir_bias_deg: f64,
    dfm_1h: f64,
    urban_ros: f64,
    ignition_xy: [f64; 2],
    ignition_time_s: f64,
    #[serde(default)]
    anchors: Vec<Anchor>,
    #[serde(default)]
    seed_phi: Option<PathBuf>,
    #[serde(default)]
    seed_arrival: Option<PathBuf>,
    #[serde(default)]
    seed_time_s: Option<f64>,
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct Anchor {
    time_s: f64,
    index: usize,
}
#[derive(Serialize)]
struct Response {
    protocol_version: u32,
    physics_sha256: String,
    model_version: &'static str,
    members: usize,
    cells: usize,
    arrival_path: String,
    sha256: String,
    steps: u64,
    quality_flags: Vec<String>,
    build: BuildInfo,
}

fn error(message: impl Into<String>) -> EngineError {
    EngineError::new(ErrorCode::SchemaMismatch, message)
}
fn read_array(path: &Path, n: usize) -> EngineResult<Vec<f64>> {
    let bytes = std::fs::read(path).map_err(|e| error(e.to_string()))?;
    fire_io::arrays::read_f64le(&bytes, n, "bridge array")
}
fn seed_disk(input: &RunInput, xy: [f64; 2]) -> Vec<f64> {
    let grid = &input.manifest.grid;
    (0..input.initial_phi.len())
        .map(|i| {
            if !modeled(input.valid[i], input.burnable[i]) {
                return grid.dx();
            }
            let (r, c) = grid.row_col(i);
            let (x, y) = grid.center(r, c);
            (x - xy[0]).hypot(y - xy[1]) - 1.25 * grid.dx()
        })
        .collect()
}
fn add_anchor(state: &mut SimulationState, input: &RunInput, anchor: &Anchor) -> EngineResult<()> {
    let grid = &input.manifest.grid;
    let n = grid.cell_count()?;
    if anchor.index >= n || !modeled(input.valid[anchor.index], input.burnable[anchor.index]) {
        return Err(error("anchor is not in a burnable cell"));
    }
    let (r, c) = grid.row_col(anchor.index);
    let (x, y) = grid.center(r, c);
    let disk = seed_disk(input, [x, y]);
    for (i, value) in disk.iter().enumerate() {
        if *value < state.phi[i] {
            state.phi[i] = *value;
        }
        if state.phi[i] < 0.0 && state.arrival_status[i] == ARRIVAL_NOT_REACHED {
            state.arrival_status[i] = ARRIVAL_MODELED;
            state.arrival_time_s[i] = anchor.time_s;
        }
    }
    state.phi_scratch.clone_from(&state.phi);
    Ok(())
}
fn solve(
    base: &RunInput,
    gx: &[f64],
    gy: &[f64],
    member: &Member,
    until: f64,
) -> EngineResult<(Vec<f64>, u64, Vec<String>)> {
    for v in [
        member.ros_mult,
        member.wind_speed_mult,
        member.dfm_1h,
        member.urban_ros,
    ] {
        if !v.is_finite() || v <= 0.0 {
            return Err(error("member scales/moisture must be finite and positive"));
        }
    }
    if !member.wind_dir_bias_deg.is_finite()
        || !member.ignition_xy.iter().all(|v| v.is_finite())
        || !member.ignition_time_s.is_finite()
        || member.ignition_time_s < 0.0
        || member.ignition_time_s > base.context.forecast_issue_s
    {
        return Err(error("invalid member ignition or angle"));
    }
    let mut input = base.clone();
    input.context.ros_multiplier = member.ros_mult;
    if input.context.urban_rate_m_s.is_some() {
        input.context.urban_rate_m_s = Some(member.urban_ros / 60.0);
    }
    let angle = member.wind_dir_bias_deg.to_radians();
    let (sn, cs) = angle.sin_cos();
    for frame in &mut input.environment.frames {
        for i in 0..frame.wind_east.len() {
            let (u, v) = (frame.wind_east[i], frame.wind_north[i]);
            // Compass-clockwise rotation in east/north coordinates.
            frame.wind_east[i] = member.wind_speed_mult * (cs * u + sn * v);
            frame.wind_north[i] = member.wind_speed_mult * (-sn * u + cs * v);
            frame.m1[i] = member.dfm_1h;
            frame.m10[i] = member.dfm_1h + 0.01;
            frame.m100[i] = member.dfm_1h + 0.02;
            frame.mlh[i] = 0.30;
            frame.mlw[i] = 0.60;
        }
    }
    let start = member.seed_time_s.unwrap_or(member.ignition_time_s);
    if !start.is_finite() || start < member.ignition_time_s || start > base.context.forecast_issue_s
    {
        return Err(error("invalid reinitialization time"));
    }
    input.initial_phi = match &member.seed_phi {
        Some(path) => read_array(path, input.valid.len())?,
        None => seed_disk(&input, member.ignition_xy),
    };
    let mut prepared = prepare(&input)?;
    prepared.gx.clone_from_slice(gx);
    prepared.gy.clone_from_slice(gy);
    let mut state = initialize(
        &prepared,
        &InitialState {
            phi: input.initial_phi.clone(),
        },
    )?;
    state.t = start;
    let explicit = member
        .seed_arrival
        .as_ref()
        .map(|path| read_array(path, input.valid.len()))
        .transpose()?;
    for i in 0..input.valid.len() {
        if state.arrival_status[i] == ARRIVAL_INITIAL_UNKNOWN {
            let t = explicit.as_ref().map(|a| a[i]).unwrap_or(start);
            if !t.is_finite() || t < 0.0 || t > start {
                return Err(error("invalid seeded arrival time"));
            }
            state.arrival_status[i] = ARRIVAL_MODELED;
            state.arrival_time_s[i] = t;
        }
    }
    let mut anchors = member.anchors.clone();
    anchors.sort_by(|a, b| a.time_s.total_cmp(&b.time_s).then(a.index.cmp(&b.index)));
    for anchor in &anchors {
        if !anchor.time_s.is_finite()
            || anchor.time_s < start
            || anchor.time_s > input.context.forecast_issue_s
        {
            return Err(error("anchor time outside assimilated history"));
        }
        advance(&mut state, &prepared, &input, anchor.time_s)?;
        add_anchor(&mut state, &input, anchor)?;
    }
    advance(&mut state, &prepared, &input, until)?;
    let arrivals = state
        .arrival_time_s
        .iter()
        .zip(&state.arrival_status)
        .map(|(t, s)| {
            if *s == ARRIVAL_MODELED {
                *t
            } else {
                f64::INFINITY
            }
        })
        .collect();
    Ok((arrivals, state.diagnostics.step_count, state.quality_flags))
}
fn execute(req: Request) -> EngineResult<Response> {
    if req.protocol_version != VERSION
        || req.members.is_empty()
        || req.workers == 0
        || !req.issue_s.is_finite()
        || req.issue_s < 0.0
        || !req.until_s.is_finite()
        || req.until_s < req.issue_s
        || req.until_s.fract() != 0.0
    {
        return Err(error("invalid protocol, sizes or times"));
    }
    let mut input = fire_io::load_run_bundle(&req.manifest, req.memory_budget_bytes)?;
    input.context.forecast_issue_s = req.issue_s;
    input.context.urban_rate_m_s = if req.urban { Some(1.0 / 60.0) } else { None };
    let n = input.manifest.grid.cell_count()?;
    let gx = read_array(&req.gx, n)?;
    let gy = read_array(&req.gy, n)?;
    if gx.iter().chain(&gy).any(|v| !v.is_finite()) {
        return Err(error("nonfinite terrain gradient"));
    }
    if req.out.exists() {
        return Err(error("bridge output already exists"));
    }
    // Reserve final arrival matrix and worker scratch, including cloned weather bands.
    let final_bytes = (n as u64)
        .saturating_mul(req.members.len() as u64)
        .saturating_mul(8);
    let per = (n as u64).saturating_mul(640 + 56 * input.environment.frames.len() as u64);
    let workers = req
        .workers
        .min(((req.memory_budget_bytes.saturating_sub(final_bytes)) / per.max(1)) as usize)
        .min(
            std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(1),
        );
    if workers == 0 {
        return Err(EngineError::new(
            ErrorCode::MemoryBudgetExceeded,
            "bridge budget cannot hold one worker plus arrival matrix",
        ));
    }
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers)
        .build()
        .map_err(|e| error(e.to_string()))?;
    let results = pool.install(|| {
        req.members
            .par_iter()
            .map(|m| solve(&input, &gx, &gy, m, req.until_s))
            .collect::<EngineResult<Vec<_>>>()
    })?;
    let parent = req
        .out
        .parent()
        .ok_or_else(|| error("missing output parent"))?;
    std::fs::create_dir_all(parent).map_err(|e| error(e.to_string()))?;
    let temporary = req.out.with_extension("partial");
    let mut file = std::fs::File::create(&temporary).map_err(|e| error(e.to_string()))?;
    let mut steps = 0;
    let mut flags = Vec::new();
    for (values, count, quality) in results {
        for v in values {
            file.write_all(&v.to_le_bytes())
                .map_err(|e| error(e.to_string()))?;
        }
        steps += count;
        flags.extend(quality);
    }
    file.flush().map_err(|e| error(e.to_string()))?;
    drop(file);
    std::fs::rename(temporary, &req.out).map_err(|e| error(e.to_string()))?;
    flags.sort();
    flags.dedup();
    Ok(Response {
        protocol_version: VERSION,
        physics_sha256: env!("FIRE_PHYSICS_SHA256").into(),
        model_version: "rust-levelset-pf-1",
        members: req.members.len(),
        cells: n,
        arrival_path: req.out.display().to_string(),
        sha256: fire_io::sha256_file(&req.out)?.0,
        steps,
        quality_flags: flags,
        build: BuildInfo::default(),
    })
}
fn main() {
    if std::env::args().nth(1).as_deref() == Some("--describe") {
        println!(
            "{}",
            serde_json::json!({"protocol_version":VERSION,"model_version":"rust-levelset-pf-1","physics_sha256":env!("FIRE_PHYSICS_SHA256")})
        );
        return;
    }
    let result = (|| {
        let path = std::env::args_os()
            .nth(1)
            .ok_or_else(|| error("usage: fire-bridge REQUEST.json"))?;
        let bytes = std::fs::read(path).map_err(|e| error(e.to_string()))?;
        let request: Request = serde_json::from_slice(&bytes).map_err(|e| error(e.to_string()))?;
        execute(request)
    })();
    match result {
        Ok(response) => println!(
            "{}",
            serde_json::to_string(&response).expect("finite bridge metadata")
        ),
        Err(e) => {
            eprintln!("{}", serde_json::to_string(&e).unwrap());
            std::process::exit(e.code.exit_code());
        }
    }
}
