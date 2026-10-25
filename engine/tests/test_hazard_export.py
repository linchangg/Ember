from shapely.geometry import shape
import numpy as np
from emberline.hazard.export import mask_geometry


def test_export_preserves_islands_holes_and_lonlat_order(setup_model):
    grid = setup_model[0].grid
    mask = np.zeros((grid.H, grid.W), bool)
    mask[3:12, 3:12] = True
    mask[6:9, 6:9] = False
    mask[15:18, 20:23] = True
    length, polygon, front = mask_geometry(mask, grid)
    geometry = shape(polygon)
    assert polygon["type"] == "MultiPolygon" and len(geometry.geoms) == 2
    assert sum(len(p.interiors) for p in geometry.geoms) == 1
    assert front["type"] == "MultiLineString" and len(front["coordinates"]) == 3
    assert (
        length > 0 and -119 < geometry.bounds[0] < -117 and 33 < geometry.bounds[1] < 35
    )


def test_export_empty_geometry_is_serializable(setup_model):
    import json

    grid = setup_model[0].grid
    length, polygon, front = mask_geometry(np.zeros((grid.H, grid.W), bool), grid)
    assert length == 0 and polygon["coordinates"] == [] and front["coordinates"] == []
    json.dumps([polygon, front], allow_nan=False)


def test_publication_branch_cannot_escape_asset_root(tmp_path):
    import pytest
    from emberline.hazard.export import publish
    with pytest.raises(ValueError, match="branch ID"):
        publish(tmp_path, *([None] * 12), branch="../escape")
