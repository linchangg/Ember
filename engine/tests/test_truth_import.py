import json
import numpy as np
import pytest
from shapely.geometry import Polygon, box, mapping
from emberline.hazard.historical import Grid
from emberline.hazard.truth_import import import_truth


@pytest.fixture
def inputs(tmp_path):
    grid = Grid(nx=5, ny=5, cell_m=10, x0=380000, y0=3803000)
    coverage = box(grid.x0, grid.y0 - 50, grid.x0 + 50, grid.y0)
    outer = box(grid.x0 + 10, grid.y0 - 40, grid.x0 + 40, grid.y0 - 10)
    hole = box(grid.x0 + 20, grid.y0 - 30, grid.x0 + 30, grid.y0 - 20)
    perimeter = Polygon(outer.exterior.coords, [hole.exterior.coords])
    paths = [tmp_path / "perimeter.json", tmp_path / "coverage.json"]
    for path, geometry in zip(paths, [perimeter, coverage]):
        path.write_text(json.dumps(mapping(geometry)))
    kwargs = dict(
        grid=grid,
        source_crs="EPSG:32611",
        valid_sim_ms=1000,
        published_sim_ms=2000,
        source_id="synthetic-import-test",
        source_url="synthetic-test-only",
        out=tmp_path / "truth.npz",
    )
    return paths, kwargs


def test_hole_north_first_and_coverage(inputs):
    paths, kw = inputs
    result = import_truth(*paths, **kw)
    with np.load(kw["out"]) as data:
        assert data["burned"].sum() == 8 and data["burned"][2, 2] == 0
        assert data["valid"].sum() == 25
        assert data["valid_sim_ms"] == 1000
    assert result["independence"] == "requires_source_review"
    with pytest.raises(FileExistsError):
        import_truth(*paths, **kw)


def test_unknown_coverage_remains_unknown(inputs):
    paths, kw = inputs
    paths[1].write_text(json.dumps(mapping(box(380010, 3802960, 380040, 3802990))))
    import_truth(*paths, **kw)
    with np.load(kw["out"]) as data:
        assert data["valid"].sum() == 9


def test_invalid_geometry_rejected(inputs):
    paths, kw = inputs
    paths[0].write_text(
        json.dumps(mapping(Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])))
    )
    with pytest.raises(ValueError, match="Invalid"):
        import_truth(*paths, **kw)


def test_wrong_crs_outside_domain_rejected(inputs):
    paths, kw = inputs
    kw["source_crs"] = "EPSG:3857"
    with pytest.raises(ValueError, match="evaluation grid"):
        import_truth(*paths, **kw)


def test_publication_before_valid_rejected(inputs):
    paths, kw = inputs
    kw["published_sim_ms"] = 0
    with pytest.raises(ValueError, match="publication"):
        import_truth(*paths, **kw)


def test_geometry_outside_coverage_rejected(inputs):
    paths, kw = inputs
    paths[1].write_text(json.dumps(mapping(box(380020, 3802970, 380030, 3802980))))
    with pytest.raises(ValueError, match="Coverage"):
        import_truth(*paths, **kw)
