import json
import shutil
import pytest
from emberline.api.assets import inventory
from emberline.run import FIXTURES


def test_partial_inventory_is_explicit(tmp_path):
    shutil.copytree(FIXTURES / "static/fixture", tmp_path / "static/fixture")
    target = tmp_path / "cache/fixture/main"
    target.mkdir(parents=True)
    index = json.loads((FIXTURES / "cache/fixture/main/index.json").read_text())
    index["cycles"] = index["cycles"][:2]
    (target / "index.json").write_text(json.dumps(index))
    assert inventory(tmp_path, "fixture")["complete"] is False
    index["cycles"].append(index["cycles"][0])
    (target / "index.json").write_text(json.dumps(index))
    with pytest.raises(ValueError, match="inventory"):
        inventory(tmp_path, "fixture")


def test_invalid_scenario_rejected(tmp_path):
    with pytest.raises(ValueError):
        inventory(tmp_path, "../secret")
