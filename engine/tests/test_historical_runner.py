from types import SimpleNamespace
from pathlib import Path
import json
import numpy as np
import pytest
from ember.hazard.api import Grid, ModelConfig
from ember.hazard.historical_runner import ForecastArchive


def host():
    g = Grid("EPSG:32611", 380000, 3803000, 90, 2, 3, 1736294400)
    model = SimpleNamespace(
        _arrivals=np.array(
            [[[0, 1, np.inf], [3, 4, 5]], [[0, 2, 3], [4, 5, 6]]], np.float32
        ),
        _history=[],
        model_version="synthetic-export-test",
    )
    return SimpleNamespace(
        land=SimpleNamespace(grid=g),
        model=model,
        cfg=ModelConfig(n_members=2),
        identity={"physics_sha256": "synthetic"},
        forcing=SimpleNamespace(
            weather_as_of=lambda _: SimpleNamespace(audit={"available_ms": 0})
        ),
    )


def test_archive_retains_raw_members_and_issue(tmp_path):
    issue = 1736302800000
    writer = ForecastArchive(tmp_path, {issue})
    h = host()
    f = SimpleNamespace(weights=np.array([0.2, 0.8]), duration_ms=12)
    writer(issue, h, f, {"run_hash": "test"})
    with np.load(tmp_path / str(issue) / "prediction.npz") as a:
        assert a["arrival_min"].shape == (2, 2, 3)
        assert np.isposinf(a["arrival_min"][0, 0, 2])
        assert np.allclose(a["weights"], [0.2, 0.8])
    meta = json.loads((tmp_path / str(issue) / "metadata.json").read_text())
    assert meta["scientific_acceptance"] == "not_established"
    with pytest.raises(FileExistsError):
        writer(issue, h, f, {})


def test_unrequested_issue_is_not_exported(tmp_path):
    writer = ForecastArchive(tmp_path, {1000})
    writer(2000, None, None, {})
    assert not list(tmp_path.iterdir())
