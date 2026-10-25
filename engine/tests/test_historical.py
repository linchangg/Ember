"""Historical harness safety/metric tests use synthetic arrays, never historical claims."""

import json
import numpy as np
import pytest
from ember.hazard.historical import evaluate, make_example, score, digest


def edit_manifest(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def edit_npz(path, **changes):
    with np.load(path, allow_pickle=False) as data:
        values = {key: data[key] for key in data.files}
    values.update(changes)
    np.savez_compressed(path, **values)


def test_example_is_explicitly_not_historical(tmp_path):
    report = evaluate(make_example(tmp_path))
    assert report["status"] == "synthetic_harness_only"
    assert report["scientific_acceptance"] == "not_established_by_this_report"
    assert len(report["cases"]) == 4
    rows = {r["mode"]: r for r in report["cases"]}
    assert rows["pf"]["brier"] == 0
    assert rows["persistence"]["brier"] == pytest.approx(8 / 25)
    assert rows["pf"]["iou"] == 1


def test_unknown_coverage_is_excluded_and_reliability_counts_agree():
    p = np.array([[1.0, 0.5], [0, 1]])
    y = np.array([[1, 0], [0, 0]])
    valid = np.array([[True, True], [True, False]])
    result = score(p, y, valid, 90)
    assert result["brier"] == pytest.approx(0.25 / 3)
    assert result["iou"] == 0.5
    assert result["sorensen"] == pytest.approx(2 / 3)
    assert sum(b["n"] for b in result["reliability"]) == 3


def test_empty_and_one_empty_boundary_are_explicit():
    y = np.zeros((5, 5), dtype=np.uint8)
    valid = np.ones_like(y, dtype=bool)
    result = score(y, y, valid, 90)
    assert result["both_empty"] and result["iou"] == 1
    assert result["area_ratio"] is None and result["boundary_mean_m"] is None
    p = y.astype(float)
    p[2, 2] = 1
    result = score(p, y, valid, 90)
    assert result["iou"] == 0 and result["boundary_mean_m"] is None


def test_boundary_distance_uses_meters():
    p, y = np.zeros((7, 7)), np.zeros((7, 7), dtype=np.uint8)
    p[3, 3], y[3, 4] = 1, 1
    result = score(p, y, np.ones_like(y, dtype=bool), 90)
    assert result["boundary_mean_m"] == result["boundary_p95_m"] == 90


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda d: d["sources"][0].update(available_sim_ms=1), "future"),
        (
            lambda d: d["cases"][0]["predictions"]["pf"]["input_ids"].append(
                "independent-truth"
            ),
            "future|truth",
        ),
        (
            lambda d: d["incidents"].append(
                dict(d["incidents"][0], split="calibration")
            ),
            "disjoint",
        ),
        (lambda d: d["cases"][0]["predictions"].pop("none"), "baselines"),
        (lambda d: d["cases"].append(d["cases"][0]), "duplicate case"),
        (
            lambda d: d["cases"][0]["predictions"]["pf"].update(path="../escaped.npz"),
            "confined",
        ),
        (
            lambda d: d["cases"][0]["predictions"]["pf"].update(config_sha256="0" * 64),
            "config",
        ),
        (
            lambda d: d["cases"][0]["predictions"]["pf"].update(input_ids=[]),
            "input audit",
        ),
    ],
)
def test_audit_failures(tmp_path, mutation, message):
    path = make_example(tmp_path)
    edit_manifest(path, mutation)
    with pytest.raises(ValueError, match=message):
        evaluate(path)


def test_hash_detects_artifact_changes(tmp_path):
    manifest = make_example(tmp_path)
    (tmp_path / "config.json").write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        evaluate(manifest)


def test_alias_of_truth_cannot_be_an_input(tmp_path):
    path = make_example(tmp_path)

    def mutate(d):
        alias = dict(
            d["sources"][1], source_id="alias", role="observation", available_sim_ms=0
        )
        d["sources"].append(alias)
        d["cases"][0]["predictions"]["pf"]["input_ids"].append("alias")

    edit_manifest(path, mutate)
    with pytest.raises(ValueError, match="truth reused"):
        evaluate(path)


@pytest.mark.parametrize(
    "updates,message",
    [
        ({"grid_sha256": "wrong"}, "grid"),
        ({"issue_sim_ms": 300000}, "issue"),
        ({"horizon_min": 30}, "horizon"),
        ({"arrival_min": np.zeros((2, 4, 4))}, "ensemble"),
        ({"weights": np.array([0.2, 0.2])}, "weights"),
        ({"arrival_min": np.full((2, 5, 5), np.nan)}, "ensemble"),
    ],
)
def test_invalid_prediction_metadata(tmp_path, updates, message):
    manifest = make_example(tmp_path)
    artifact = tmp_path / "pf.npz"
    edit_npz(artifact, **updates)
    edit_manifest(
        manifest,
        lambda d: d["cases"][0]["predictions"]["pf"].update(sha256=digest(artifact)),
    )
    with pytest.raises(ValueError, match=message):
        evaluate(manifest)


def test_unreached_members_keep_probability_mass(tmp_path):
    manifest = make_example(tmp_path)
    artifact = tmp_path / "pf.npz"
    arrivals = np.full((2, 5, 5), np.inf)
    arrivals[0, 1:4, 1:4] = 30
    edit_npz(artifact, arrival_min=arrivals)
    edit_manifest(
        manifest,
        lambda d: d["cases"][0]["predictions"]["pf"].update(sha256=digest(artifact)),
    )
    pf = next(r for r in evaluate(manifest)["cases"] if r["mode"] == "pf")
    assert pf["brier"] == pytest.approx(9 * 0.25 / 25)


def test_persistence_cannot_grow(tmp_path):
    manifest = make_example(tmp_path)
    artifact = tmp_path / "persistence.npz"
    edit_npz(artifact, arrival_min=np.full((2, 5, 5), 30.0))
    edit_manifest(
        manifest,
        lambda d: d["cases"][0]["predictions"]["persistence"].update(
            sha256=digest(artifact)
        ),
    )
    with pytest.raises(ValueError, match="must not advance"):
        evaluate(manifest)


def test_truth_timestamp_is_exact(tmp_path):
    manifest = make_example(tmp_path)
    truth = tmp_path / "truth.npz"
    edit_npz(truth, valid_sim_ms=3600001)
    edit_manifest(manifest, lambda d: d["sources"][1].update(sha256=digest(truth)))
    with pytest.raises(ValueError, match="truth time"):
        evaluate(manifest)


def test_incident_macro_summary_does_not_pool_splits(tmp_path):
    manifest = make_example(tmp_path)

    def mutate(d):
        d["incidents"].append({"incident_id": "other", "split": "calibration"})
        d["cases"].append(
            dict(d["cases"][0], case_id="other-case", incident_id="other")
        )

    edit_manifest(manifest, mutate)
    report = evaluate(manifest)
    assert len(report["summary"]) == 8
    assert {r["split"] for r in report["summary"]} == {"calibration", "test"}
    assert all(r["n_incidents"] == 1 for r in report["summary"])


def test_no_empty_truth_coverage():
    with pytest.raises(ValueError):
        score(np.zeros((3, 3)), np.zeros((3, 3)), np.zeros((3, 3), dtype=bool), 90)


def test_cli_refuses_overwriting_any_frozen_input(tmp_path, monkeypatch):
    from ember.hazard.historical import main

    manifest = make_example(tmp_path)
    config = tmp_path / "config.json"
    original = config.read_bytes()
    monkeypatch.setattr(
        "sys.argv", ["historical", "--manifest", str(manifest), "--out", str(config)]
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert config.read_bytes() == original
