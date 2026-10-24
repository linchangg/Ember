use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use fire_behavior::{behavior_with, inspect_json, BehaviorOptions, FuelCatalog};
use fire_io::{export_run, load_run_bundle, make_fm1_circle_fixture};
use fire_propagation::{run, validate_inputs};
use fire_types::{EngineError, LocalEnvironment, Moisture, WindLimitPolicy};

#[derive(Parser)]
#[command(name = "fire-cli", about = "surface_v1 wildfire physics engine")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Create a synthetic normalized input bundle
    MakeFixture {
        #[arg(long)]
        kind: String,
        #[arg(long)]
        out: PathBuf,
    },
    /// Validate a run bundle
    Validate {
        #[arg(long)]
        input: PathBuf,
        #[arg(long, default_value_t = 2147483648)]
        memory_budget_bytes: u64,
    },
    /// Print local behavior intermediates
    InspectBehavior {
        #[arg(long)]
        fuel: String,
        #[arg(long)]
        m1: f64,
        #[arg(long)]
        m10: f64,
        #[arg(long)]
        m100: f64,
        #[arg(long)]
        mlh: f64,
        #[arg(long)]
        mlw: f64,
        #[arg(long = "wind-east-m-s")]
        wind_east_m_s: f64,
        #[arg(long = "wind-north-m-s")]
        wind_north_m_s: f64,
        #[arg(long)]
        gx: f64,
        #[arg(long)]
        gy: f64,
        #[arg(long, default_value = "effective_0p9_ir")]
        wind_limit: String,
    },
    /// Run a deterministic simulation
    Simulate {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value_t = 2147483648)]
        memory_budget_bytes: u64,
    },
    /// Resume from a checkpoint
    Resume {
        #[arg(long)]
        checkpoint: PathBuf,
        #[arg(long = "until-s")]
        until_s: f64,
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        input: PathBuf,
        #[arg(long, default_value_t = 2147483648)]
        memory_budget_bytes: u64,
    },
    /// Apply a perimeter correction
    Correct {
        #[arg(long)]
        checkpoint: PathBuf,
        #[arg(long)]
        update: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value_t = 2147483648)]
        memory_budget_bytes: u64,
    },
    /// Run an uncalibrated demonstration ensemble
    Ensemble {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value_t = 2147483648)]
        memory_budget_bytes: u64,
    },
    /// Score predictions against truth under a frozen protocol
    Evaluate {
        #[arg(long)]
        predictions: PathBuf,
        #[arg(long)]
        truth: PathBuf,
        #[arg(long)]
        protocol: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
}

fn main() -> ExitCode {
    match run_cli() {
        Ok(()) => ExitCode::from(0),
        Err(e) => {
            eprintln!(
                "{}",
                serde_json::json!({
                    "code": e.code,
                    "message": e.message,
                    "artifact": e.artifact,
                    "cell_index": e.cell_index,
                    "time_s": e.time_s
                })
            );
            ExitCode::from(e.code.exit_code() as u8)
        }
    }
}

fn run_cli() -> Result<(), EngineError> {
    let cli = Cli::parse();
    match cli.command {
        Commands::MakeFixture { kind, out } => {
            if kind != "fm1-circle" {
                return Err(EngineError::new(
                    fire_types::ErrorCode::SchemaMismatch,
                    format!("unknown fixture kind {kind}"),
                ));
            }
            make_fm1_circle_fixture(&out)?;
            println!("wrote {}", out.display());
        }
        Commands::Validate {
            input,
            memory_budget_bytes,
        } => {
            let bundle = load_run_bundle(&input, memory_budget_bytes)?;
            let report = validate_inputs(&bundle)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&report.warnings).unwrap()
            );
            println!("quality_flags={:?}", report.quality_flags);
        }
        Commands::InspectBehavior {
            fuel,
            m1,
            m10,
            m100,
            mlh,
            mlw,
            wind_east_m_s,
            wind_north_m_s,
            gx,
            gy,
            wind_limit,
        } => {
            const CATALOG: &str = include_str!("../../../docs/spec/fuel_models.csv");
            let cat = FuelCatalog::parse_csv(CATALOG)?;
            let model = if let Ok(id) = fuel.parse::<u16>() {
                cat.get(id)?
            } else {
                cat.get_code(&fuel)?
            };
            let limit = if wind_limit == "none" {
                WindLimitPolicy::None
            } else {
                WindLimitPolicy::Effective0p9Ir
            };
            let beh = behavior_with(
                model,
                Moisture {
                    m1,
                    m10,
                    m100,
                    mlh,
                    mlw,
                },
                LocalEnvironment {
                    wind_east_m_s,
                    wind_north_m_s,
                    gx,
                    gy,
                },
                &BehaviorOptions {
                    wind_limit: limit,
                    ellipse_max_lb: 8.0,
                },
            )?;
            println!(
                "{}",
                serde_json::to_string_pretty(&inspect_json(&beh)).unwrap()
            );
        }
        Commands::Simulate {
            input,
            out,
            memory_budget_bytes,
        } => {
            let bundle = load_run_bundle(&input, memory_budget_bytes)?;
            let output = run(&bundle)?;
            export_run(&out, &output)?;
            println!(
                "status={} until={}",
                output.run.status, output.run.completed_until_s
            );
        }
        Commands::Resume {
            checkpoint,
            until_s,
            out,
            input,
            memory_budget_bytes,
        } => {
            let bundle = load_run_bundle(&input, memory_budget_bytes)?;
            let (prepared, state, _) = fire_state::resume_until(&checkpoint, &bundle, until_s)?;
            let output = fire_propagation::output_from_state(
                &bundle,
                &prepared,
                &state,
                until_s,
                Vec::new(),
            );
            export_run(&out, &output)?;
            println!(
                "status={} until={}",
                output.run.status, output.run.completed_until_s
            );
        }
        Commands::Correct {
            checkpoint,
            update,
            out,
            memory_budget_bytes,
        } => {
            let text = std::fs::read_to_string(&update).map_err(|e| {
                EngineError::new(
                    fire_types::ErrorCode::SchemaMismatch,
                    format!("update json: {e}"),
                )
            })?;
            let man: fire_types::CorrectionManifest = serde_json::from_str(&text).map_err(|e| {
                EngineError::new(fire_types::ErrorCode::SchemaMismatch, format!("{e}"))
            })?;
            let parent_manifest = PathBuf::from(&man.grid_manifest);
            let new_bundle = load_run_bundle(&parent_manifest, memory_budget_bytes)?;
            let n = new_bundle.manifest.grid.cell_count()?;
            let phi = fire_state::load_corrected_phi(
                &PathBuf::from(&update)
                    .parent()
                    .unwrap_or(std::path::Path::new("."))
                    .join(&man.corrected_phi),
                n,
            )?;
            let _ =
                fire_state::apply_correction(&checkpoint, &new_bundle, &man, &new_bundle, &phi)?;
            let output = run(&new_bundle)?;
            export_run(&out, &output)?;
            println!("correction applied; new origin exported");
        }
        Commands::Ensemble {
            input,
            config,
            out,
            memory_budget_bytes,
        } => {
            let bundle = load_run_bundle(&input, memory_budget_bytes)?;
            let cfg_text = std::fs::read_to_string(&config).map_err(|e| {
                EngineError::new(fire_types::ErrorCode::SchemaMismatch, format!("{e}"))
            })?;
            let mut cfg: fire_types::EnsembleConfig =
                serde_json::from_str(&cfg_text).map_err(|e| {
                    EngineError::new(fire_types::ErrorCode::SchemaMismatch, format!("{e}"))
                })?;
            cfg.memory_budget_bytes = cfg.memory_budget_bytes.min(memory_budget_bytes);
            let ens = fire_ensemble::run_ensemble(&bundle, &cfg)?;
            std::fs::create_dir_all(&out).ok();
            let payload = serde_json::json!({
                "preset": ens.preset,
                "label": "raw_scenario_frequency",
                "n_requested": ens.n_requested,
                "n_complete": ens.n_complete,
                "partial": ens.partial,
                "members": ens.members,
                "operational_probability_claim": false
            });
            std::fs::write(
                out.join("ensemble.json"),
                serde_json::to_vec_pretty(&payload).unwrap(),
            )
            .map_err(|e| EngineError::new(fire_types::ErrorCode::SchemaMismatch, format!("{e}")))?;
            println!("ensemble complete {}/{}", ens.n_complete, ens.n_requested);
        }
        Commands::Evaluate {
            predictions,
            truth,
            protocol,
            out,
        } => {
            let report = fire_eval::evaluate_paths(&predictions, &truth, &protocol)?;
            if let Some(parent) = out.parent() {
                std::fs::create_dir_all(parent).ok();
            }
            std::fs::write(&out, serde_json::to_vec_pretty(&report).unwrap()).map_err(|e| {
                EngineError::new(fire_types::ErrorCode::SchemaMismatch, format!("{e}"))
            })?;
            println!("wrote {}", out.display());
        }
    }
    Ok(())
}
