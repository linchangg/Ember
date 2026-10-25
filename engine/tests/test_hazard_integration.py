from dataclasses import replace
import numpy as np
import pytest
from ember.hazard.integration import weather_from_hrrr


def test_hrrr_adapter_uses_real_current_writer_and_blocks_unreleased_bands(setup_model):
    from ember.data.hrrr import forcing_dataset

    land, _, _, _, _ = setup_model
    g = land.grid
    init = g.t_ref_s * 1000
    x = g.x0 + (np.arange(g.W) + 0.5) * g.cell_m
    y = g.y0 - (np.arange(g.H) + 0.5) * g.cell_m
    shape = (10, g.H, g.W)
    available = [init + (60 + 2 * f) * 60_000 for f in range(10)]
    ds = forcing_dataset(
        np.full(shape, 6),
        np.full(shape, -3),
        np.full(10, 20),
        np.full(10, 0.2),
        x,
        y,
        init,
        available,
        "synthetic",
    )
    weather, audit = weather_from_hrrr(ds, g, init + 65 * 60_000)
    np.testing.assert_array_equal(weather.t_min, [0, 60, 120])
    assert (
        audit["selected_fxx"] == [0, 1, 2]
        and max(audit["available_ms_by_fxx"]) <= audit["issue_ms"]
    )
    assert weather.u10.shape == (3, g.H, g.W) and weather.v10[0, 0, 0] == -3
    with pytest.raises(ValueError, match="no HRRR band"):
        weather_from_hrrr(ds, g, init)
    with pytest.raises(ValueError, match="cell centers"):
        weather_from_hrrr(ds, replace(g, x0=g.x0 + 90), init + 65 * 60_000)
    ds.attrs["rotation"] = "grid-relative"
    with pytest.raises(ValueError, match="wind reference"):
        weather_from_hrrr(ds, g, init + 65 * 60_000)


def test_rust_forecast_passes_cody_assets_risk_alerts_and_cache(setup_model, tmp_path):
    from ember.runtime.summarize import write_assets, encode_arrival
    from ember.runtime.cache import (
        PhysicsOutput,
        save_cycle,
        load_cycle,
        save_checkpoint,
        latest_checkpoint,
    )
    from ember.risk.risk import zone_risks
    from ember.risk.alerts import evaluate, load_rules

    model = setup_model[-1]()
    forecast = model.step(5, [])
    g = model.land.grid
    mapping = {
        "crs": g.crs,
        "cell_m": g.cell_m,
        "nx": g.W,
        "ny": g.H,
        "x0": g.x0,
        "y0": g.y0,
    }
    assets, danger, stats = write_assets(
        forecast, tmp_path / "assets", "cache/test/main/issue", mapping, model.burnable
    )
    assert (tmp_path / "assets/arrival_q.bin").read_bytes() == encode_arrival(
        forecast.t_q
    ).tobytes()
    region_index = np.zeros((g.H, g.W), np.int32)
    arr = model.region_arrivals(region_index, 1)
    zone = {
        "zone_id": "test",
        "evac_min_default": 45,
        "population": 1000,
        "housing": 500,
        "n_critical": 0,
        "tier": 2,
        "egress_roads": 2,
    }
    stamp = g.t_ref_s * 1000 + 300_000
    risks = zone_risks(
        arr,
        forecast.weights,
        stamp,
        g.t_ref_s * 1000,
        [zone],
        {},
        None,
        {},
        set(),
        "cycle",
        "test",
        "main",
        horizon_min=60,
    )
    alerts = evaluate(
        risks, [], stamp, "cycle", "test", "main", load_rules(), {}, {}, stamp
    )
    assert (
        len(risks) == 1
        and risks[0].rank == 1
        and all(a.state == "proposed" for a in alerts.all)
    )
    output = PhysicsOutput(
        {"assets": assets, "danger": danger, **stats},
        arr,
        arr.copy(),
        forecast.weights,
        [],
        [],
        [],
        "native-key",
    )
    save_cycle(tmp_path, "test", "main", stamp, output)
    restored = load_cycle(tmp_path, "test", "main", stamp, "native-key")
    np.testing.assert_array_equal(restored.zone_arr, arr)
    np.testing.assert_array_equal(restored.weights, forecast.weights)
    save_checkpoint(tmp_path, "test", "main", stamp, model.save_state())
    assert latest_checkpoint(tmp_path, "test", "main", stamp)[0] == stamp
