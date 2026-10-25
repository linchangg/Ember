"""Profile selection reads stored samples and ignores the execution-eligibility flag."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from emberline.hazard.api import ModelConfig
from emberline.hazard.benchmark import load_benchmark_config
from emberline.hazard.select_profile import (
    evaluate_report,
    identify_report,
    load_declared_candidates,
    main,
    select_profiles,
)

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "config/hazard_experiments/benchmark-smoke.json"
TARGET = ROOT / "config/hazard_experiments/benchmark-target.json"
MEASURED = Path("/tmp/emberline-e1-smoke/benchmark.json")
WORKLOADS = ("no_observation", "satellite", "delayed_ground")
PASSED = {
    "checkpoint_next_cycle_parity": "passed",
    "canonical_export_validation": "passed",
}


def _sample(workload: str, category: str, elapsed_ms: float) -> dict:
    return {
        "workload": workload,
        "category": category,
        "elapsed_ns": int(round(elapsed_ms * 1_000_000)),
        "timeout": False,
        "error": None,
        "python_peak_rss_bytes": 1_000,
        "child_peak_rss_bytes": 1_000,
    }


def _cover(elapsed_ms: float) -> list[dict]:
    return [_sample(workload, category, elapsed_ms) for workload in WORKLOADS for category in ("cold", "warm")]


def _report(config_path: Path, samples: list[dict], **extra) -> dict:
    config = load_benchmark_config(config_path).model_dump(mode="json")
    execution_eligible = extra.pop("execution_eligible", True)
    report = {
        "schema_version": 1,
        "experiment_kind": "benchmark",
        "run_id": config["run_id"],
        "status": "complete",
        "resolved_config": config,
        "samples": samples,
        "summary": {"eligible_for_profile_selection": execution_eligible},
    }
    report.update(extra)
    return report


def test_smoke_median_exceeds_the_ten_second_budget_without_a_budget_block() -> None:
    samples = [
        row
        for row in _cover(1_000)
        if (row["workload"], row["category"]) not in {("satellite", "warm"), ("delayed_ground", "cold")}
    ]
    samples.append(_sample("satellite", "warm", 11190.50225))
    samples.append(_sample("delayed_ground", "cold", 10202.657292))
    report = _report(SMOKE, samples)
    assert "latency_budgets" not in report
    decision = select_profiles([report])
    candidate = decision["candidates"][0]
    text = "\n".join(candidate["reasons"])
    assert decision["selected_profile"] is None
    assert decision["execution_mode"] == "cached_replay"
    assert decision["scientific_acceptance"] == "not_established_by_performance"
    assert candidate["latency_source"] == "validated_samples"
    assert candidate["report_version"]["latency_budgets_present"] is False
    assert candidate["report_version"]["version_label"] == "benchmark-schema-1-samples"
    assert "satellite:warm median_elapsed_ms 11190.502 exceeds interactive budget 10000" in text
    assert "checkpoint next-cycle parity was not recorded as passed" in text
    assert select_profiles([_report(SMOKE, samples, execution_eligible=False)])["selected_profile"] is None


def test_saved_smoke_report_keeps_its_bytes_and_schema() -> None:
    if not MEASURED.is_file():
        pytest.skip("measured smoke report is not on this machine")
    original = MEASURED.read_bytes()
    report = json.loads(original)
    decision = select_profiles([report])
    assert MEASURED.read_bytes() == original
    assert identify_report(report) == {
        "schema_version": 1,
        "experiment_kind": "benchmark",
        "run_id": "benchmark-smoke-v1",
        "latency_budgets_present": False,
        "sample_count": 108,
        "version_label": "benchmark-schema-1-samples",
    }
    reasons = "\n".join(decision["candidates"][0]["reasons"])
    assert decision["selected_profile"] is None
    assert decision["execution_mode"] == "cached_replay"
    assert decision["candidates"][0]["latency_source"] == "validated_samples"
    assert "satellite:warm median_elapsed_ms 11190.502 exceeds interactive budget 10000" in reasons


def test_missing_samples_are_insufficient() -> None:
    decision = select_profiles(
        [{"schema_version": 1, "summary": {"eligible_for_profile_selection": True}}]
    )
    assert decision["selected_profile"] is None
    assert decision["candidates"][0]["latency_source"] == "insufficient"
    assert "timing samples are missing or invalid" in decision["candidates"][0]["reasons"]


@pytest.mark.parametrize("field,value", [
    ("schema_version", 2), ("schema_version", True),
    ("experiment_kind", "historical"), ("status", "failed"), ("status", None),
])
def test_incomplete_or_unsupported_reports_cannot_be_selected(field, value) -> None:
    report = _report(TARGET, _cover(100), verification=PASSED)
    report[field] = value
    assert select_profiles([report])["selected_profile"] is None


def test_budget_block_must_match_samples() -> None:
    report = _report(
        SMOKE,
        _cover(1_000),
        latency_budgets={"forecasts": {"satellite:warm": {"median_elapsed_ms": 1}}},
        verification=PASSED,
    )
    reasons = evaluate_report(report, load_declared_candidates())["reasons"]
    assert "latency_budgets does not match validated samples" in reasons


def test_selection_is_independent_of_report_order() -> None:
    smoke = _report(SMOKE, _cover(4_000), verification=PASSED)
    target = _report(TARGET, _cover(4_000), verification=PASSED)
    forward = select_profiles([smoke, target])
    backward = select_profiles([target, smoke])
    assert forward["selected_profile"] == "benchmark-target-v1"
    assert backward["selected_profile"] == forward["selected_profile"]
    assert forward["execution_mode"] == "measured_development"
    assert forward["provisional"] is True
    assert [item["run_id"] for item in forward["candidates"]] == [
        item["run_id"] for item in backward["candidates"]
    ]
    fast = _report(TARGET, _cover(100), verification=PASSED, execution_eligible=False)
    assert select_profiles([fast])["selected_profile"] == "benchmark-target-v1"
    assert ModelConfig().spotting is True
    assert ModelConfig().n_members == 128


def test_tampered_hash_and_cli(tmp_path: Path) -> None:
    report = _report(TARGET, _cover(100), verification=PASSED)
    report["content_sha256"] = "0" * 64
    decision = select_profiles([report])
    assert decision["selected_profile"] is None
    assert "report content hash does not match its samples" in decision["candidates"][0]["reasons"]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(_report(SMOKE, _cover(1_000))), encoding="utf-8")
    out = tmp_path / "selected-profile.json"
    assert main(["--report", str(path), "--out", str(out)]) == 0
    stored = json.loads(out.read_text(encoding="utf-8"))
    assert stored["selected_profile"] is None
    assert stored["execution_mode"] == "cached_replay"
    assert main(["--report", str(path), "--out", str(out)]) == 2
