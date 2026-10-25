"""Benchmark workload measurement with a fake runner and clock."""

from __future__ import annotations

import json
import platform
import resource
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from emberline.hazard.api import Grid, ModelConfig
from emberline.hazard.benchmark import (
    LEGACY_MEMBERS,
    LEGACY_REPORT_KEYS,
    LEGACY_SIZE,
    LEGACY_WORKERS,
    BenchmarkConfig,
    bridge_build_command,
    build_release_bridge,
    child_command,
    execute_config_run,
    issue_schedule,
    latency_budgets,
    legacy_benchmark,
    load_benchmark_config,
    main,
    measure_repetition,
    memory_summary,
    nearest_rank_p95,
    parse_invocation,
    physical_extent,
    repetition_samples,
    rss_bytes,
    summarize,
    synthetic_inputs,
    synthetic_observations,
)
from emberline.hazard.experiment_io import ConfigError, ExecutionError

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "config/hazard_experiments/benchmark-smoke.json"
TARGET = ROOT / "config/hazard_experiments/benchmark-target.json"


class ManualClock:
    def __init__(self) -> None:
        self.now = 0

    def ns(self) -> int:
        return self.now

    def advance(self, amount: int) -> None:
        self.now += amount


def _unit_config(**overrides) -> BenchmarkConfig:
    raw = {
        "schema_version": 1,
        "run_id": "bench-unit",
        "members": 1,
        "height": 2,
        "width": 2,
        "cell_m": 90,
        "horizon_min": 360,
        "workers": 1,
        "timeout_s": 600,
        "memory_budget_bytes": 1024,
        "seeds": [7],
        "warmup_cycles": 2,
        "measured_cycles": 1,
        "workloads": ["no_observation"],
        "interactive_budget_ms": 10000,
        "original_target_ms": 500,
    }
    raw.update(overrides)
    return BenchmarkConfig.model_validate(raw)


def _finished(workload: str, category: str, elapsed_ns: int, **extra) -> dict:
    sample = {
        "seed": 7,
        "issue_min": extra.get("issue_min", 5),
        "workload": workload,
        "category": category,
        "native_steps": 2,
        "accepted_observations": 0,
        "python_peak_rss_bytes": extra.get("python_peak_rss_bytes"),
        "child_peak_rss_bytes": extra.get("child_peak_rss_bytes"),
        "memory_reason": None,
        "elapsed_ns": elapsed_ns,
        "internal_duration_ms": extra.get("internal_duration_ms", 1),
        "timeout": extra.get("timeout", False),
        "error": extra.get("error"),
        "run_id": "bench-unit",
    }
    return sample


def test_legacy_and_config_outputs_differ(tmp_path: Path) -> None:
    legacy = parse_invocation(["--out", "latency.json"])
    assert legacy.mode == "legacy"
    assert legacy.out_kind == "json_file"
    assert (legacy.members, legacy.size, legacy.workers) == (
        LEGACY_MEMBERS,
        LEGACY_SIZE,
        LEGACY_WORKERS,
    )
    chosen = parse_invocation(["--out", "latency.json", "--members", "16"])
    assert chosen.members == 16
    configured = parse_invocation(["--config", "c.json", "--out", "run-1"])
    assert configured.mode == "config"
    assert configured.out_kind == "directory"
    with pytest.raises(ConfigError):
        parse_invocation(["--config", "c.json", "--out", "run-1", "--members", "16"])
    assert main(["--config", "c.json", "--out", str(tmp_path / "run-1"), "--workers", "2"]) == 2
    existing = tmp_path / "latency.json"
    existing.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        legacy_benchmark(existing, 1, 2, 1)
    assert LEGACY_REPORT_KEYS[0] == "synthetic"
    assert "forecast_duration_ms" in LEGACY_REPORT_KEYS
    assert "target_ms" in LEGACY_REPORT_KEYS


def test_shipped_profiles_report_extent_and_keep_both_budgets() -> None:
    smoke = load_benchmark_config(SMOKE)
    target = load_benchmark_config(TARGET)
    smoke_extent = physical_extent(smoke)
    target_extent = physical_extent(target)
    assert smoke.workloads == ["no_observation", "satellite", "delayed_ground"]
    assert target.workloads == smoke.workloads
    assert smoke_extent["domain_label"] == "reduced_resolution_or_members"
    assert target_extent["domain_label"] == "target"
    assert smoke_extent["same_physical_domain_as_target"] is False
    assert target_extent["same_physical_domain_as_target"] is True
    assert smoke_extent["east_m"] == 120 * 255
    assert smoke_extent["north_m"] == 120 * 255
    assert target_extent["east_m"] == 340 * 90
    assert target_extent["north_m"] == 340 * 90
    assert smoke.interactive_budget_ms == 10000
    assert smoke.original_target_ms == 500
    assert target.members == 128
    assert target.height == 340 and target.cell_m == 90
    assert target.horizon_min == 360
    schedule = issue_schedule(smoke, ModelConfig().cycle_min)
    assert schedule == [
        ("cold", 5),
        ("warm", 10),
        ("warm", 15),
        ("warm", 20),
        ("warm", 25),
        ("warm", 30),
    ]
    issues = [minute for _, minute in schedule]
    assert issues == sorted(set(issues))


def test_invalid_dimensions_and_workload_labels(tmp_path: Path) -> None:
    raw = json.loads(SMOKE.read_text(encoding="utf-8"))
    for key, value in (
        ("members", 0),
        ("height", -1),
        ("width", 0),
        ("cell_m", 0),
        ("cell_m", -5),
        ("members", True),
        ("workloads", ["historical"]),
    ):
        broken = dict(raw)
        broken[key] = value
        path = tmp_path / f"{key}.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(ConfigError):
            load_benchmark_config(path)
    broken = dict(raw)
    broken["unexpected"] = 1
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_benchmark_config(path)


def test_production_defaults_stay_put() -> None:
    assert ModelConfig().spotting is True
    assert ModelConfig().n_members == 128
    _, _, configured, _ = synthetic_inputs(load_benchmark_config(SMOKE), 8)
    assert configured.spotting is False
    assert configured.n_members == 16
    assert configured.seed == 8
    assert ModelConfig().spotting is True


def test_workload_timestamps_are_causal_and_synthetic() -> None:
    grid = Grid("EPSG:32611", 380000, 3803000, 255, 4, 4, 1736294400)
    assert synthetic_observations("no_observation", grid, 5, 7) == []
    with pytest.raises(ConfigError):
        synthetic_observations("historical", grid, 5, 7)
    issue_ms = round((grid.t_ref_s + 5 * 60) * 1000)
    satellite = synthetic_observations("satellite", grid, 5, 7)
    delayed = synthetic_observations("delayed_ground", grid, 5, 7)
    assert satellite[0].kind == "goes_pixel"
    assert delayed[0].kind == "ground_report"
    assert satellite[0].provenance == "simulated"
    assert delayed[0].provenance == "simulated"
    assert satellite[0].available_sim_ms == issue_ms
    assert satellite[0].observed_sim_ms == issue_ms
    assert delayed[0].available_sim_ms == issue_ms
    assert delayed[0].observed_sim_ms < delayed[0].available_sim_ms
    assert satellite[0].obs_id == synthetic_observations("satellite", grid, 5, 7)[0].obs_id


def test_percentiles_and_timeouts_are_not_zeroed() -> None:
    p95, label = nearest_rank_p95([float(value) for value in range(1, 21)])
    assert p95 == 19
    assert label == "nearest-rank"
    _, low_label = nearest_rank_p95([float(value) for value in range(1, 20)])
    assert low_label == "descriptive/low-sample"
    assert nearest_rank_p95([]) == (None, "no samples")
    finished = _finished("satellite", "warm", 100_000_000, issue_min=10)
    timed_out = _finished("satellite", "warm", 99_999_000_000, issue_min=15, timeout=True)
    summary = summarize([finished, timed_out])
    stats = summary["forecasts"]["satellite:warm"]
    assert stats["count"] == 2
    assert stats["timeout_count"] == 1
    assert stats["median_elapsed_ms"] == pytest.approx(100)
    assert stats["max_elapsed_ms"] == pytest.approx(100)
    assert timed_out["elapsed_ns"] == 99_999_000_000
    only = summarize([timed_out])
    assert only["forecasts"]["satellite:warm"]["median_elapsed_ms"] is None
    assert only["eligible_for_profile_selection"] is False
    assert "E2 must evaluate" in only["eligibility_meaning"]


def test_timing_boundaries_and_warmup_exclusion() -> None:
    clock = ManualClock()
    seen: list[int] = []

    def observations_for(workload, grid, issue, seed):
        seen.append(issue)
        return []

    class Model:
        native_diagnostics = {"steps": 4}

        def init(self, land, weather, model_config, ignition) -> None:
            clock.advance(1_000)

        def step(self, issue, observations):
            clock.advance({5: 100, 10: 9_999, 15: 50}[issue])
            return SimpleNamespace(
                duration_ms={5: 3, 10: 80, 15: 4}[issue],
                obs_used=[],
            )

        def save_state(self) -> bytes:
            clock.advance(7)
            return b"state"

        def load_state(self, blob: bytes) -> None:
            clock.advance(8)

    config = _unit_config()
    samples = measure_repetition(
        {
            "seed": 7,
            "workload": "no_observation",
            "config": config.model_dump(mode="json"),
            "land": SimpleNamespace(grid=Grid("EPSG:32611", 0, 0, 90, 2, 2, 1)),
            "weather": None,
            "model_config": None,
            "ignition": None,
        },
        Model(),
        clock,
        observations_for,
        cycle_min=5,
    )
    assert [sample["category"] for sample in samples] == [
        "init",
        "cold",
        "warmup",
        "warm",
        "checkpoint_save",
        "checkpoint_load",
    ]
    assert [sample["elapsed_ns"] for sample in samples] == [1000, 100, 9999, 50, 7, 8]
    assert samples[1]["internal_duration_ms"] == 3
    assert samples[1]["elapsed_ns"] != samples[1]["internal_duration_ms"]
    assert seen == [5, 10, 15]
    summary = summarize(samples)
    assert "no_observation:warmup" not in summary["forecasts"]
    assert summary["forecasts"]["no_observation:warm"]["median_elapsed_ms"] == pytest.approx(50 / 1e6)
    assert summary["forecasts"]["no_observation:cold"]["median_elapsed_ms"] == pytest.approx(100 / 1e6)
    assert summary["warmup_excluded"] is True


def test_step_timeout_keeps_elapsed_time() -> None:
    clock = ManualClock()

    class Model:
        native_diagnostics = {"steps": 1}

        def init(self, *args) -> None:
            clock.advance(10)

        def step(self, issue, observations):
            clock.advance(5_000)
            return SimpleNamespace(duration_ms=1, obs_used=[])

    config = _unit_config(timeout_s=1e-6, warmup_cycles=1, measured_cycles=2)
    samples = measure_repetition(
        {
            "seed": 7,
            "workload": "satellite",
            "config": config.model_dump(mode="json"),
            "land": SimpleNamespace(grid=Grid("EPSG:32611", 0, 0, 90, 2, 2, 1)),
            "weather": None,
            "model_config": None,
            "ignition": None,
        },
        Model(),
        clock,
        lambda *args: [],
        cycle_min=5,
    )
    cold = [sample for sample in samples if sample["category"] == "cold"]
    assert len(cold) == 1
    assert cold[0]["timeout"] is True
    assert cold[0]["elapsed_ns"] == 5_000
    assert all(sample["category"] != "warm" for sample in samples)
    summary = summarize(samples)
    assert summary["forecasts"]["satellite:cold"]["median_elapsed_ms"] is None
    assert summary["eligible_for_profile_selection"] is False


def test_execution_eligibility_does_not_qualify_a_slow_satellite_median() -> None:
    """The published smoke warm median for satellite is above the 10 s budget."""
    config = _unit_config(interactive_budget_ms=10000, original_target_ms=500)
    samples = [
        _finished("no_observation", "warm", 5_537_948_291, issue_min=10),
        _finished("delayed_ground", "warm", 5_203_169_042, issue_min=10),
        _finished("satellite", "warm", 11_190_502_250, issue_min=10),
    ]
    summary = summarize(samples)
    budgets = latency_budgets(config, summary)
    assert summary["eligible_for_profile_selection"] is True
    assert "checkpoint parity" in summary["eligibility_meaning"]
    satellite = next(row for row in budgets["series"] if row["series"] == "satellite:warm")
    assert satellite["median_elapsed_ms"] == pytest.approx(11190.50225)
    assert satellite["within_interactive_budget"] is False
    assert budgets["qualifies_for_interactive_budget"] is False
    assert "satellite:warm" in budgets["interactive_budget_failures"]
    assert budgets["qualifies_for_original_target"] is False
    assert budgets["not_evaluated"] == ["memory budget", "checkpoint parity", "export checks"]


def test_subprocess_failure_is_stored_with_wall_time(tmp_path: Path) -> None:
    failed = repetition_samples(7, "satellite", 12_345, 1, None, False, "bench-unit")
    assert failed[0]["elapsed_ns"] == 12_345
    assert failed[0]["error"] == "child exited 1"
    assert failed[0]["timeout"] is False
    timed = repetition_samples(7, "satellite", 50_000_000, None, None, True, "bench-unit")
    assert timed[0]["elapsed_ns"] == 50_000_000
    assert timed[0]["timeout"] is True
    config = load_benchmark_config(SMOKE)
    out = tmp_path / "run-1"

    def fail(job_path: Path, result_path: Path, job: dict):
        return 12_345, 1, False, None

    report = execute_config_run(config, out, runner=fail, identity=None)
    assert out.is_dir()
    assert report["status"] == "complete"
    assert report["summary"]["eligible_for_profile_selection"] is False
    assert report["original_target_ms"] == 500
    assert report["interactive_budget_ms"] == 10000
    assert any(sample["elapsed_ns"] == 12_345 and sample["error"] for sample in report["samples"])
    stored = json.loads((out / "benchmark.json").read_text(encoding="utf-8"))
    assert stored["run_id"] == "benchmark-smoke-v1"
    assert stored["summary"]["eligible_for_profile_selection"] is False
    assert stored["latency_budgets"]["not_evaluated"] == [
        "memory budget",
        "checkpoint parity",
        "export checks",
    ]
    with pytest.raises(ConfigError):
        execute_config_run(config, out, runner=fail)
    assert (out / "benchmark.json").is_file()


def test_runner_crash_keeps_a_failed_directory(tmp_path: Path, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("the fake runner built the bridge")

    monkeypatch.setattr("emberline.hazard.benchmark.build_release_bridge", boom)
    out = tmp_path / "run-2"

    def crash(job_path: Path, result_path: Path, job: dict):
        raise RuntimeError("child could not start")

    with pytest.raises(ExecutionError):
        execute_config_run(_unit_config(), out, runner=crash)
    assert not out.exists()
    assert (tmp_path / "run-2.failed").is_dir()
    assert (tmp_path / "run-2.failed" / "failure.json").is_file()


def test_child_command_is_an_argument_array() -> None:
    command = child_command(Path("job.json"), Path("result.json"))
    assert command[0] == sys.executable
    assert command[1:3] == ["-m", "emberline.hazard.benchmark"]
    assert command[3:5] == ["--child-job", "job.json"]
    assert all(token not in "".join(command) for token in ("|", ";", "&&"))
    workspace = Path("/repo/physics/surface_v1")
    build = bridge_build_command(workspace, "1.99.1")
    assert build[build.index("--target-dir") + 1] == str(workspace / "target")
    assert "--locked" in build
    assert build_release_bridge.__doc__


def test_rss_peaks_stay_separate() -> None:
    system = platform.system()
    python_rss, python_reason = rss_bytes(resource.RUSAGE_SELF)
    child_rss, child_reason = rss_bytes(resource.RUSAGE_CHILDREN)
    if system == "Darwin":
        assert python_rss == resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        assert child_rss == resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        assert python_reason is None and child_reason is None
    elif system == "Linux":
        assert python_rss == resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        assert child_rss == resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
        assert python_reason is None and child_reason is None
    else:
        assert python_rss is None and child_rss is None
        assert python_reason and child_reason
    summary = memory_summary(
        [
            _finished("no_observation", "cold", 1, python_peak_rss_bytes=100, child_peak_rss_bytes=40),
            _finished("no_observation", "warm", 1, python_peak_rss_bytes=80, child_peak_rss_bytes=70),
        ]
    )
    assert summary["python_peak_rss_bytes"] == 100
    assert summary["native_child_peak_rss_bytes"] == 70
    assert "sum" not in summary
    assert summary["peaks_are_independent"] is True
