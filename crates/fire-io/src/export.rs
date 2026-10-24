use std::fs;
use std::path::Path;

use fire_types::{
    ArtifactRef, EngineError, EngineResult, ErrorCode, HorizonArrays, RunJson, RunOutput,
};

use crate::arrays::{sha256_hex, write_atomic, write_f64le};

pub fn export_run(out_dir: &Path, output: &RunOutput) -> EngineResult<()> {
    let staging = out_dir.with_extension("staging");
    if staging.exists() {
        fs::remove_dir_all(&staging).ok();
    }
    fs::create_dir_all(&staging)
        .map_err(|e| EngineError::new(ErrorCode::SchemaMismatch, format!("create staging: {e}")))?;
    write_atomic(
        &staging.join("arrival_time_s.f64le"),
        &write_f64le(&output.arrival_time_s),
    )?;
    write_atomic(&staging.join("arrival_status.u8"), &output.arrival_status)?;
    for h in &output.horizons {
        let name = format!("horizon_{:06}", h.time_s as i64);
        let d = staging.join(&name);
        fs::create_dir_all(&d).map_err(|e| {
            EngineError::new(ErrorCode::SchemaMismatch, format!("horizon dir: {e}"))
        })?;
        write_atomic(&d.join("phi.f64le"), &write_f64le(&h.phi))?;
        write_atomic(&d.join("burned.u8"), &h.burned)?;
        write_atomic(&d.join("front_cells.u8"), &h.front_cells)?;
    }
    let run_bytes = serde_json::to_vec_pretty(&output.run)
        .map_err(|e| EngineError::new(ErrorCode::SchemaMismatch, format!("run.json: {e}")))?;
    write_atomic(&staging.join("run.json"), &run_bytes)?;
    let diag = serde_json::to_vec_pretty(&output.diagnostics).map_err(|e| {
        EngineError::new(ErrorCode::SchemaMismatch, format!("diagnostics.json: {e}"))
    })?;
    write_atomic(&staging.join("diagnostics.json"), &diag)?;
    write_atomic(
        &staging.join("manifest.sha256"),
        format!("{}\n", output.run.input_manifest_sha256).as_bytes(),
    )?;
    if out_dir.exists() {
        fs::remove_dir_all(out_dir).map_err(|e| {
            EngineError::new(ErrorCode::SchemaMismatch, format!("replace output: {e}"))
        })?;
    }
    fs::rename(&staging, out_dir)
        .map_err(|e| EngineError::new(ErrorCode::SchemaMismatch, format!("publish output: {e}")))?;
    Ok(())
}

pub fn run_identifier(run: &RunJson) -> String {
    let mut artifacts = run.input_artifacts.clone();
    artifacts.sort_by(|a, b| a.path.cmp(&b.path));
    let payload = serde_json::json!({
        "artifacts": artifacts,
        "configuration": run.configuration,
        "build": run.build,
    });
    sha256_hex(serde_json::to_vec(&payload).unwrap_or_default().as_slice())
}

pub fn artifact_refs(input: &fire_types::RunInput) -> Vec<ArtifactRef> {
    input.artifacts.iter().map(ArtifactRef::from).collect()
}

pub fn horizon_from_state(
    time_s: f64,
    phi: &[f64],
    _arrival_status: &[u8],
    valid: &[u8],
    burnable: &[u8],
    width: u32,
    height: u32,
) -> HorizonArrays {
    let n = phi.len();
    let mut burned = vec![0u8; n];
    let mut front = vec![0u8; n];
    for i in 0..n {
        if valid[i] == 1 && burnable[i] == 1 && phi[i] <= 0.0 {
            burned[i] = 1;
        }
        if valid[i] != 1 || burnable[i] != 1 {
            continue;
        }
        if sign_change_neighbor(phi, valid, burnable, i, width, height) {
            front[i] = 1;
        }
    }
    HorizonArrays {
        time_s,
        phi: phi.to_vec(),
        burned,
        front_cells: front,
    }
}

fn sign_change_neighbor(
    phi: &[f64],
    valid: &[u8],
    burnable: &[u8],
    i: usize,
    width: u32,
    height: u32,
) -> bool {
    let w = width as usize;
    let r = i / w;
    let c = i % w;
    let dirs = [
        (r as i32, c as i32 - 1),
        (r as i32, c as i32 + 1),
        (r as i32 - 1, c as i32),
        (r as i32 + 1, c as i32),
    ];
    for (rr, cc) in dirs {
        if rr < 0 || cc < 0 || rr >= height as i32 || cc >= w as i32 {
            continue;
        }
        let j = rr as usize * w + cc as usize;
        if valid[j] != 1 || burnable[j] != 1 {
            continue;
        }
        if (phi[i] > 0.0) != (phi[j] > 0.0) {
            return true;
        }
    }
    let _ = height;
    false
}

pub fn make_fm1_circle_fixture(out: &Path) -> EngineResult<()> {
    const CATALOG: &str = include_str!("../../../docs/spec/fuel_models.csv");
    if out.exists() {
        fs::remove_dir_all(out).ok();
    }
    fs::create_dir_all(out)
        .map_err(|e| EngineError::new(ErrorCode::SchemaMismatch, format!("fixture mkdir: {e}")))?;
    let width = 64u32;
    let height = 64u32;
    let dx = 10.0;
    let n = (width * height) as usize;
    let elevation = vec![0.0; n];
    let fuel = vec![1u16; n];
    let valid = vec![1u8; n];
    let burnable = vec![1u8; n];
    let mut phi = Vec::with_capacity(n);
    for row in 0..height {
        for col in 0..width {
            let x = (col as f64 + 0.5) * dx;
            let y = (row as f64 + 0.5) * dx;
            phi.push(((x - 320.0).hypot(y - 320.0)) - 45.0);
        }
    }
    write_atomic(&out.join("elevation.f64le"), &write_f64le(&elevation))?;
    write_atomic(
        &out.join("fuel_id.u16le"),
        &crate::arrays::write_u16le(&fuel),
    )?;
    write_atomic(&out.join("valid.u8"), &valid)?;
    write_atomic(&out.join("burnable.u8"), &burnable)?;
    write_atomic(&out.join("initial_phi.f64le"), &write_f64le(&phi))?;
    write_atomic(&out.join("fuel_models.csv"), CATALOG.as_bytes())?;
    let moist = [0.05, 0.07, 0.09, 0.60, 0.90];
    for (dir, _) in [("000000", 0.0), ("000600", 600.0)] {
        let d = out.join("weather").join(dir);
        fs::create_dir_all(&d).unwrap();
        for (name, val) in [
            ("wind_east", 0.0),
            ("wind_north", 0.0),
            ("m1", moist[0]),
            ("m10", moist[1]),
            ("m100", moist[2]),
            ("mlh", moist[3]),
            ("mlw", moist[4]),
        ] {
            write_atomic(
                &d.join(format!("{name}.f64le")),
                &write_f64le(&vec![val; n]),
            )?;
        }
    }
    let manifest = serde_json::json!({
        "schema_version": 1,
        "incident_id": "synthetic-fm1",
        "start_utc": "2026-01-01T00:00:00Z",
        "grid": {
            "width": 64, "height": 64, "cell_size_m": 10.0,
            "x0_m": 0.0, "y0_m": 0.0, "crs": "LOCAL_METRIC_TEST"
        },
        "landscape": {
            "elevation": "elevation.f64le",
            "fuel_id": "fuel_id.u16le",
            "valid": "valid.u8",
            "burnable": "burnable.u8",
            "fuel_catalog": "fuel_models.csv"
        },
        "initial": {
            "phi": "initial_phi.f64le",
            "perimeter_activity": "assume_entire_boundary_active"
        },
        "environment": {
            "wind_reference": "midflame_horizontal_heading_surface_speed",
            "interpolation": "hold_right_continuous",
            "frames": [
                {
                    "time_s": 0.0,
                    "observed_or_valid_utc": "2026-01-01T00:00:00Z",
                    "available_utc": "2026-01-01T00:00:00Z",
                    "directory": "weather/000000"
                },
                {
                    "time_s": 600.0,
                    "observed_or_valid_utc": "2026-01-01T00:10:00Z",
                    "available_utc": "2026-01-01T00:00:00Z",
                    "directory": "weather/000600"
                }
            ],
            "coverage_end_s": 3600.0
        },
        "simulation": {
            "horizons_s": [300.0, 600.0, 1800.0, 3600.0],
            "cfl": 0.4,
            "max_step_s": 30.0,
            "wind_limit": "effective_0p9_ir",
            "ellipse_max_lb": 8.0,
            "reinitialize": false,
            "solver": "level_set_lf_euler_v1"
        }
    });
    write_atomic(
        &out.join("manifest.json"),
        format!("{}\n", serde_json::to_string_pretty(&manifest).unwrap()).as_bytes(),
    )?;
    Ok(())
}
