import numpy as np
import pytest
from emberline.hazard.api import Grid, Landscape, Weather, ModelConfig, Ignition
from emberline.hazard.model import RustHazardModel
from emberline.hazard.bridge import BackendOptions


@pytest.fixture
def setup_model():
    shape = (21, 31)
    grid = Grid("EPSG:32611", 381480, 3800280, 90, *shape, 1736294400)
    land = Landscape(
        grid,
        np.full(shape, 102, dtype=np.int16),
        np.zeros(shape, np.float32),
        np.zeros(shape, np.float32),
        np.zeros(shape, np.float32),
    )
    weather = Weather(
        np.array([0, 60, 120], np.float32),
        np.full((3, *shape), 3, np.float32),
        np.zeros((3, *shape), np.float32),
        np.full(3, 20, np.float32),
        np.full(3, 0.3, np.float32),
    )
    cfg = ModelConfig(
        n_members=8, horizon_min=60, out_step_min=30, spotting=False, filter="pf"
    )
    ign = Ignition(0, np.array([[grid.x0 + 10.5 * 90, grid.y0 - 10.5 * 90]]), 90)

    def make(**changes):
        from dataclasses import replace

        model = RustHazardModel(BackendOptions(workers=2))
        model.init(land, weather, replace(cfg, **changes), ign)
        return model

    return land, weather, cfg, ign, make


def observation(
    model,
    *,
    identifier="report",
    observed=5,
    available=5,
    east_cells=8,
    trust=0.25,
    hot=True,
    kind="ground_report",
):
    from pyproj import Transformer
    from emberline.contracts import ObservationRecord

    g = model.land.grid
    x = model._ignition_xy[0, 0] + east_cells * g.cell_m
    y = model._ignition_xy[0, 1]
    lon, lat = Transformer.from_crs(g.crs, "EPSG:4326", always_xy=True).transform(x, y)
    return ObservationRecord(
        obs_id=identifier,
        scenario_id="test",
        sensor_id="test-report",
        kind=kind,
        observed_sim_ms=round((g.t_ref_s + observed * 60) * 1000),
        available_sim_ms=round((g.t_ref_s + available * 60) * 1000),
        geometry={"type": "point_radius", "lon": lon, "lat": lat, "radius_m": 90},
        hot=hot,
        trust=trust,
        provenance="simulated",
    )
