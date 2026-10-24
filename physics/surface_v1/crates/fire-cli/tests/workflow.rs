use std::{
    path::{Path, PathBuf},
    process::{Command, Output},
};
struct Sandbox(PathBuf);
impl Sandbox {
    fn new(name: &str) -> Self {
        let p = std::env::temp_dir().join(format!("fire-cli-{name}-{}", std::process::id()));
        std::fs::create_dir(&p).unwrap();
        Self(p)
    }
}
impl Drop for Sandbox {
    fn drop(&mut self) {
        std::fs::remove_dir_all(&self.0).ok();
    }
}
fn cli(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_fire-cli"))
        .args(args)
        .output()
        .unwrap()
}
fn ok(args: &[&str]) {
    let o = cli(args);
    assert!(
        o.status.success(),
        "{args:?}: {}",
        String::from_utf8_lossy(&o.stderr)
    );
}
fn s(path: &Path) -> &str {
    path.to_str().unwrap()
}
fn json(path: &Path) -> serde_json::Value {
    serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap()
}
fn write_json(path: &Path, value: &serde_json::Value) {
    std::fs::write(path, serde_json::to_vec_pretty(value).unwrap()).unwrap();
}
fn fixture(root: &Path) -> PathBuf {
    let input = root.join("input");
    ok(&["make-fixture", "--kind", "fm1-circle", "--out", s(&input)]);
    input.join("manifest.json")
}
#[test]
fn checkpoint_resume_and_cli_correction_use_saved_state() {
    let root = Sandbox::new("correction");
    let manifest = fixture(&root.0);
    let checkpoint = root.0.join("checkpoint");
    let parent = root.0.join("parent");
    ok(&["simulate", "--input", s(&manifest), "--out", s(&parent)]);
    ok(&[
        "checkpoint",
        "--input",
        s(&manifest),
        "--out",
        s(&checkpoint),
        "--until-s",
        "600",
    ]);
    let resume = root.0.join("resume");
    ok(&[
        "resume",
        "--input",
        s(&manifest),
        "--checkpoint",
        s(&checkpoint),
        "--until-s",
        "3600",
        "--out",
        s(&resume),
    ]);
    assert_eq!(
        std::fs::read(parent.join("arrival_time_s.f64le")).unwrap(),
        std::fs::read(resume.join("arrival_time_s.f64le")).unwrap()
    );
    let mut phi = Vec::new();
    for r in 0..64 {
        for c in 0..64 {
            phi.push(
                (((c as f64 + 0.5) * 10.0 - 320.0).hypot((r as f64 + 0.5) * 10.0 - 320.0)) - 95.0,
            );
        }
    }
    std::fs::write(root.0.join("observed.f64le"), fire_io::write_f64le(&phi)).unwrap();
    let update = root.0.join("update.json");
    write_json(
        &update,
        &serde_json::json!({"schema_version":1,"observation_id":"synthetic-wide","observed_utc":"2026-01-01T00:10:00Z","available_utc":"2026-01-01T00:10:00Z","time_s_in_parent":600.0,"grid_manifest":"input/manifest.json","corrected_phi":"observed.f64le","policy":"replace_state_at_observation","arrival_history":"reset_corrected_interior_to_unknown","provenance":"synthetic regression observation"}),
    );
    let corrected = root.0.join("corrected");
    ok(&[
        "correct",
        "--parent-input",
        s(&manifest),
        "--checkpoint",
        s(&checkpoint),
        "--update",
        s(&update),
        "--out",
        s(&corrected),
    ]);
    assert_ne!(
        std::fs::read(parent.join("arrival_status.u8")).unwrap(),
        std::fs::read(corrected.join("arrival_status.u8")).unwrap()
    );
    let meta = json(&corrected.join("run.json"));
    assert_eq!(meta["start_utc"], "2026-01-01T00:00:00Z");
    assert!(meta["quality_flags"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v.as_str().unwrap().contains("synthetic-wide")));
    assert!(
        !cli(&["simulate", "--input", s(&manifest), "--out", s(&parent)])
            .status
            .success()
    );
}
#[test]
fn ensemble_exports_arrays_and_is_worker_deterministic() {
    let root = Sandbox::new("ensemble");
    let manifest = fixture(&root.0);
    let config = root.0.join("ensemble-config.json");
    let mut cfg = serde_json::json!({"schema_version":1,"preset":"test_uncalibrated","members":3,"master_seed":42,"workers":1,"memory_budget_bytes":134217728,"wind_bias_std_m_s":0.1,"dead_moisture_log_std":0.02,"live_moisture_log_std":0.02,"front_bias_std_m":1.0,"allow_partial":false});
    write_json(&config, &cfg);
    let a = root.0.join("one");
    ok(&[
        "ensemble",
        "--input",
        s(&manifest),
        "--config",
        s(&config),
        "--out",
        s(&a),
    ]);
    cfg["workers"] = 2.into();
    write_json(&config, &cfg);
    let b = root.0.join("two");
    ok(&[
        "ensemble",
        "--input",
        s(&manifest),
        "--config",
        s(&config),
        "--out",
        s(&b),
    ]);
    let metadata = json(&a.join("ensemble.json"));
    assert_eq!(metadata["n_complete"], 3);
    assert_eq!(metadata["operational_probability_claim"], false);
    for artifact in metadata["artifacts"].as_array().unwrap() {
        let name = artifact["path"].as_str().unwrap();
        let bytes = std::fs::read(a.join(name)).unwrap();
        assert_eq!(bytes, std::fs::read(b.join(name)).unwrap());
        assert_eq!(
            fire_io::sha256_hex(&bytes),
            artifact["sha256"].as_str().unwrap()
        );
    }
    cfg["memory_budget_bytes"] = 1.into();
    write_json(&config, &cfg);
    assert!(!cli(&[
        "ensemble",
        "--input",
        s(&manifest),
        "--config",
        s(&config),
        "--out",
        s(&root.0.join("budget-fail"))
    ])
    .status
    .success());
}
#[test]
fn evaluation_checks_grid_lengths_and_metric_area() {
    let root = Sandbox::new("evaluate");
    let manifest = fixture(&root.0);
    let predictions = root.0.join("predictions");
    ok(&[
        "simulate",
        "--input",
        s(&manifest),
        "--out",
        s(&predictions),
    ]);
    let mut observed = std::fs::read(predictions.join("horizon_000300/burned.u8")).unwrap();
    let i = observed.iter().position(|v| *v == 1).unwrap();
    observed[i] = 0;
    std::fs::write(root.0.join("observed.u8"), observed).unwrap();
    std::fs::write(root.0.join("valid.u8"), vec![1u8; 4096]).unwrap();
    let truth_manifest = root.0.join("truth-manifest.json");
    write_json(
        &truth_manifest,
        &serde_json::json!({"schema_version":1,"incident_id":"synthetic-fm1","grid_manifest":"input/manifest.json","snapshots":[{"time_s":300.0,"burned":"observed.u8","valid":"valid.u8","acquisition_uncertainty_s":0.0,"boundary_uncertainty_m":0.0}],"arrival":null}),
    );
    let runs = root.0.join("runs.json");
    write_json(
        &runs,
        &serde_json::json!({"schema_version":1,"runs":[{"incident_id":"synthetic-fm1","issue_utc":"2026-01-01T00:00:00Z","directory":s(&predictions)}]}),
    );
    let truth = root.0.join("truth.json");
    write_json(
        &truth,
        &serde_json::json!({"schema_version":1,"incidents":[{"incident_id":"synthetic-fm1","manifest":s(&truth_manifest)}]}),
    );
    let protocol = root.0.join("protocol.json");
    let mut config = serde_json::json!({"schema_version":1,"split":"exploratory","incident_ids":["synthetic-fm1"],"horizons_s":[300.0],"baseline":"none","mask_rule":"intersection_valid","bootstrap_incident_replicates":0,"bootstrap_seed":42,"release_claim":"exploratory_only"});
    write_json(&protocol, &config);
    let report = root.0.join("report.json");
    let args = [
        "evaluate",
        "--predictions",
        s(&runs),
        "--truth",
        s(&truth),
        "--protocol",
        s(&protocol),
        "--out",
        s(&report),
    ];
    ok(&args);
    assert_eq!(
        json(&report)["per_incident"][0]["horizons"][0]["false_burn_area"],
        100.0
    );
    std::fs::write(root.0.join("valid.u8"), [1u8]).unwrap();
    ok(&args);
    assert_eq!(
        json(&report)["per_incident"][0]["horizons"][0]["missing"],
        true
    );
    std::fs::write(root.0.join("valid.u8"), vec![1u8; 4096]).unwrap();
    let mut wrong_origin = json(&manifest);
    wrong_origin["start_utc"] = "2026-01-02T00:00:00Z".into();
    write_json(&manifest, &wrong_origin);
    assert!(
        !cli(&args).status.success(),
        "truth and prediction time origins must match"
    );
    config["bootstrap_incident_replicates"] = 100.into();
    write_json(&protocol, &config);
    assert!(!cli(&args).status.success());
}
