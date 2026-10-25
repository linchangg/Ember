"""Synthetic engine package: copied forecast, checksums, and restart parity."""

from __future__ import annotations

import json
import shutil

import pytest

from emberline.hazard.demo import run_demo
from emberline.hazard.experiment_io import ConfigError, ExecutionError, read_strict_json
from emberline.hazard.package_demo import (
    HASH_MANIFEST,
    LABELS,
    LIMITATIONS,
    PUSHED_COMMIT,
    build_package,
    interpret_ci,
    main,
    verify_package,
)

PENDING_CI = interpret_ci(
    {
        "check_runs": [
            {
                "name": "fixture (ubuntu-latest)",
                "status": "in_progress",
                "conclusion": None,
                "html_url": "https://example.test/ubuntu",
            }
        ]
    }
)


def test_pending_ci_is_not_a_pass_and_success_must_be_complete():
    pending = interpret_ci({"check_runs": []})
    assert pending["status"] == "pending"
    assert pending["passed"] is False
    assert PENDING_CI["passed"] is False
    success = interpret_ci(
        {
            "check_runs": [
                {
                    "name": "fixture (ubuntu-latest)",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "name": "fixture (macos-latest)",
                    "status": "completed",
                    "conclusion": "success",
                },
            ]
        }
    )
    assert success["status"] == "success"
    assert success["passed"] is True
    mixed = interpret_ci(
        {
            "check_runs": [
                {
                    "name": "fixture (ubuntu-latest)",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "name": "fixture (macos-latest)",
                    "status": "in_progress",
                    "conclusion": None,
                },
            ]
        }
    )
    assert mixed["status"] == "pending"
    assert mixed["passed"] is False


def test_package_copy_verifies_hashes_decoder_and_limitations(tmp_path):
    forecast = tmp_path / "forecast"
    run_demo(forecast, members=2, cycles=2, mode="pf", workers=2)
    smoke = tmp_path / "smoke.json"
    smoke.write_text(
        json.dumps(
            {
                "purpose": "pipeline_verification",
                "scores": [
                    {
                        "mode": "persistence",
                        "issue_min": 20,
                        "lead_min": 0,
                        "probability_sum": 23.0,
                    },
                    {
                        "mode": "persistence",
                        "issue_min": 20,
                        "lead_min": 30,
                        "probability_sum": 23.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    extra = tmp_path / "evidence"
    extra.mkdir()
    (extra / "note.txt").write_text("synthetic note\n", encoding="utf-8")
    out = tmp_path / "package"
    build_package(
        forecast,
        out,
        smoke_report=smoke,
        evidence_root=extra,
        ci=PENDING_CI,
        workers=2,
    )
    source_verification = (forecast / "verification.json").read_bytes()
    assert (out / "forecast" / "verification.json").read_bytes() == source_verification
    manifest = read_strict_json(out / HASH_MANIFEST)
    assert HASH_MANIFEST not in manifest
    assert "package.json" in manifest
    assert "limitations.json" in manifest
    document = read_strict_json(out / "package.json")
    assert document["labels"] == list(LABELS)
    assert document["dataset_kind"] == "synthetic"
    assert document["execution_mode"] == "cached_replay"
    assert document["forecast_skill"] is False
    assert document["ci"]["commit"] == PUSHED_COMMIT
    assert document["ci"]["status"] == "pending"
    assert document["ci"]["passed"] is False
    assert read_strict_json(out / "limitations.json") == LIMITATIONS
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "not forecast skill" in readme
    assert "not an Eaton historical forecast" in readme
    branch = read_strict_json(out / "branches" / "report-demo" / "metadata.json")
    assert branch["status"] == "not_executed"
    assert branch["branch_id"] is None
    assert branch["bundle_id"] is None
    restart = read_strict_json(out / "checkpoints" / "restart.json")
    assert restart["parity"] == "matched"
    assert restart["execution_mode"] == "computed_offline"
    assert restart["accepted_observation_ids"] == ["restart-report"]
    assert restart["control_arrivals_sha256"] == restart["treatment_arrivals_sha256"]
    units = read_strict_json(out / "experiments" / "smoke" / "probability_sum_units.json")
    assert units["unit"] == "expected burned cells"
    assert units["by_issue"][0]["probability_sum_expected_burned_cells"] == 23.0
    assert units["by_issue"][0]["expected_burned_area_ha"] == pytest.approx(18.63)
    shutil.rmtree(forecast)
    copied = tmp_path / "package-copy"
    shutil.copytree(out, copied)
    verified = verify_package(copied)
    assert verified["hashes"] == "matched"
    assert verified["decoder"]["frontend_contract"] == "passed"
    assert verified["ci_passed"] is False
    tampered = copied / "README.md"
    tampered.write_text(tampered.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ExecutionError, match="hash mismatch"):
        verify_package(copied)


def test_cli_rejects_an_existing_package_and_unmarked_forecast(tmp_path):
    forecast = tmp_path / "forecast"
    forecast.mkdir()
    (forecast / "verification.json").write_text(
        '{"synthetic": false}\n', encoding="utf-8"
    )
    assert main(["--forecast-root", str(forecast), "--out", str(tmp_path / "out")]) == 2
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ConfigError, match="already exists"):
        build_package(forecast, out, ci=PENDING_CI, workers=2)
