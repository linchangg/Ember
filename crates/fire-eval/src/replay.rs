use std::path::Path;

use fire_types::{EngineError, EngineResult, ErrorCode, EvalProtocol};
use serde::{Deserialize, Serialize};

use crate::metrics::binary_metrics;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunsFile {
    pub schema_version: u32,
    pub runs: Vec<RunRef>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunRef {
    pub incident_id: String,
    pub issue_utc: String,
    pub directory: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TruthFile {
    pub schema_version: u32,
    pub incidents: Vec<TruthRef>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TruthRef {
    pub incident_id: String,
    pub manifest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TruthManifest {
    pub schema_version: u32,
    pub incident_id: String,
    pub grid_manifest: String,
    pub snapshots: Vec<TruthSnapshot>,
    pub arrival: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TruthSnapshot {
    pub time_s: f64,
    pub burned: String,
    pub valid: String,
    pub acquisition_uncertainty_s: f64,
    pub boundary_uncertainty_m: f64,
}

pub fn evaluate_paths(
    runs_path: &Path,
    truth_path: &Path,
    protocol_path: &Path,
) -> EngineResult<serde_json::Value> {
    let protocol: EvalProtocol = read_json(protocol_path)?;
    if protocol.schema_version != 1 {
        return Err(EngineError::new(
            ErrorCode::SchemaMismatch,
            "protocol schema",
        ));
    }
    let runs: RunsFile = read_json(runs_path)?;
    let truth: TruthFile = read_json(truth_path)?;
    let mut per_incident = Vec::new();
    for id in &protocol.incident_ids {
        if protocol.split != "development"
            && protocol.split != "calibration"
            && protocol.split != "test"
            && protocol.split != "exploratory"
        {
            return Err(EngineError::new(
                ErrorCode::SchemaMismatch,
                format!("unknown split {}", protocol.split),
            ));
        }
        let run = runs.runs.iter().find(|r| &r.incident_id == id);
        let tref = truth.incidents.iter().find(|t| &t.incident_id == id);
        if run.is_none() || tref.is_none() {
            per_incident.push(serde_json::json!({
                "incident_id": id,
                "missing": true,
                "reason": "missing run or truth"
            }));
            continue;
        }
        let tman: TruthManifest = read_json(Path::new(&tref.unwrap().manifest))?;
        let run_dir = Path::new(&run.unwrap().directory);
        let mut horizons = Vec::new();
        for h in &protocol.horizons_s {
            let snap = tman.snapshots.iter().find(|s| (s.time_s - *h).abs() < 1e-9);
            if snap.is_none() {
                horizons.push(serde_json::json!({
                    "time_s": h,
                    "missing": true,
                    "reason": "no matched truth horizon"
                }));
                continue;
            }
            let snap = snap.unwrap();
            let truth_root = Path::new(&tref.unwrap().manifest)
                .parent()
                .unwrap_or(Path::new("."));
            let obs = std::fs::read(truth_root.join(&snap.burned)).unwrap_or_default();
            let valid = std::fs::read(truth_root.join(&snap.valid)).unwrap_or_default();
            let pred_path = run_dir.join(format!("horizon_{:06}/burned.u8", *h as i64));
            let pred = std::fs::read(&pred_path).unwrap_or_default();
            if pred.is_empty() || obs.is_empty() || pred.len() != obs.len() {
                horizons.push(serde_json::json!({
                    "time_s": h,
                    "missing": true,
                    "reason": "array length mismatch or missing burned mask"
                }));
                continue;
            }
            let m = binary_metrics(&pred, &obs, &valid, 1.0, 1.0);
            horizons.push(serde_json::json!({
                "time_s": h,
                "iou": m.iou,
                "dice": m.dice,
                "missed_fraction": m.missed_fraction,
                "false_burn_area": m.false_burn_area,
                "baseline": protocol.baseline
            }));
        }
        per_incident.push(serde_json::json!({
            "incident_id": id,
            "horizons": horizons
        }));
    }
    Ok(serde_json::json!({
        "schema_version": 1,
        "protocol": protocol,
        "release_claim": protocol.release_claim,
        "per_incident": per_incident
    }))
}

fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> EngineResult<T> {
    let b = std::fs::read(path).map_err(|e| {
        EngineError::new(
            ErrorCode::SchemaMismatch,
            format!("read {}: {e}", path.display()),
        )
    })?;
    serde_json::from_slice(&b).map_err(|e| {
        EngineError::new(
            ErrorCode::SchemaMismatch,
            format!("json {}: {e}", path.display()),
        )
    })
}
