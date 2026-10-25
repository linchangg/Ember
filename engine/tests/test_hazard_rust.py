from dataclasses import replace
import io
import json
import numpy as np
import pytest
from ember.hazard.model import RustHazardModel
from ember.hazard.bridge import BackendOptions
from conftest import observation


def test_native_forecast_shapes_weights_and_regions(setup_model):
    model = setup_model[-1]()
    forecast = model.step(5, [])
    assert forecast.t_q.shape == (5, 21, 31) and forecast.weights.shape == (8,)
    assert (
        forecast.t_q.dtype == np.float32 and forecast.p_burned_now.dtype == np.float32
    )
    assert forecast.ess == pytest.approx(8)
    assert model.native_diagnostics["model_version"] == model.model_version
    assert model.native_diagnostics["steps"] > 0
    finite = np.isfinite(forecast.t_q)
    assert (forecast.t_q[finite] >= 0).all() and (forecast.t_q[finite] <= 60).all()
    index = np.zeros((21, 31), np.int32)
    np.testing.assert_array_equal(
        model.region_arrivals(index, 1)[:, 0],
        model._arrivals.reshape(8, -1).min(axis=1),
    )


def test_checkpoint_roundtrip_next_step_is_bit_identical(setup_model):
    model = setup_model[-1]()
    model.step(5, [])
    model.step(10, [observation(model, observed=5, available=10)])
    blob = model.save_state()
    restored = RustHazardModel()
    restored.load_state(blob)
    a = model.step(15, [])
    b = restored.step(15, [])
    np.testing.assert_array_equal(a.t_q, b.t_q)
    np.testing.assert_array_equal(a.weights, b.weights)
    np.testing.assert_array_equal(model._arrivals, restored._arrivals)
    assert a.params_summary == b.params_summary and a.obs_used == b.obs_used


def test_branch_name_does_not_affect_rng_or_physics(setup_model):
    a = setup_model[-1]()
    b = setup_model[-1]()
    ra = observation(a)
    rb = ra.model_copy(update={"branch_id": "b7"})
    fa = a.step(5, [ra])
    fb = b.step(5, [rb])
    np.testing.assert_array_equal(fa.t_q, fb.t_q)
    np.testing.assert_array_equal(fa.weights, fb.weights)
    assert a._anchors == b._anchors


def test_late_hot_observation_replays_from_its_own_time(setup_model):
    model = setup_model[-1](reseed_frac=0)
    model.step(5, [])
    rec = observation(model, observed=5, available=10, trust=1, east_cells=12)
    result = model.step(10, [rec])
    assert result.obs_used[0]["used"] and result.obs_used[0]["reanchored"]
    assert all(any(a["time_min"] == 5 for a in member) for member in model._anchors)
    support = model.geometry.prepare(rec)["support"]
    assert np.all(np.min(model._arrivals[:, support], axis=1) <= 5)


def test_duplicate_is_not_assimilated_twice_and_changed_id_rejected(setup_model):
    model = setup_model[-1]()
    rec = observation(model)
    model.step(5, [rec])
    result = model.step(10, [rec])
    assert result.obs_used[0]["reason"] == "duplicate"
    with pytest.raises(ValueError, match="changed its evidence"):
        model.step(15, [rec.model_copy(update={"trust": 0.8})])
    assert model._last_issue == 10


def test_future_observation_and_backward_cycles_rejected(setup_model):
    model = setup_model[-1]()
    with pytest.raises(ValueError, match="unavailable"):
        model.step(5, [observation(model, available=10)])
    assert model._last_issue is None
    model.step(5, [])
    for now in [5, 0, 6, float("nan")]:
        with pytest.raises(ValueError):
            model.step(now, [])


def test_native_failure_rolls_back_posterior_and_pending_weather(
    setup_model, monkeypatch
):
    model = setup_model[-1]()
    model.step(5, [])
    model.update_weather(setup_model[1])
    before = model.save_state()

    def fail(now):
        raise TimeoutError("test deadline")

    monkeypatch.setattr(model, "_predict", fail)
    with pytest.raises(TimeoutError):
        model.step(10, [])
    assert before == model.save_state()


def test_open_loop_records_unused_observations(setup_model):
    model = setup_model[-1](filter="none")
    result = model.step(5, [observation(model)])
    assert (
        result.obs_used[0]["used"] is False
        and result.obs_used[0]["reason"] == "assimilation_disabled"
    )


def test_reinit_uses_observed_mask_and_checkpoints(setup_model):
    model = setup_model[-1](filter="reinit")
    result = model.step(5, [observation(model, trust=1, east_cells=8)])
    assert result.filter_mode == "reinit" and any(s is not None for s in model._seeds)
    restored = RustHazardModel()
    restored.load_state(model.save_state())
    a = model.step(10, [])
    b = restored.step(10, [])
    np.testing.assert_array_equal(a.t_q, b.t_q)


def test_weather_update_changes_future_without_rewriting_past(setup_model):
    a = setup_model[-1]()
    b = setup_model[-1]()
    a.step(5, [])
    b.step(5, [])
    w = setup_model[1]
    a.update_weather(replace(w, u10=-w.u10.copy()))
    x = a.step(10, [])
    y = b.step(10, [])
    assert not np.array_equal(x.t_q, y.t_q, equal_nan=True)
    known = b._arrivals <= 5
    np.testing.assert_array_equal(a._arrivals[known], b._arrivals[known])


def test_bad_fuel_weather_spotting_and_backend_rejected(setup_model, tmp_path):
    land, weather, cfg, ign, _ = setup_model
    with pytest.raises(ValueError, match="spotting"):
        RustHazardModel().init(land, weather, replace(cfg, spotting=True), ign)
    bad = replace(land, fuel=np.full_like(land.fuel, 999))
    with pytest.raises(ValueError, match="unsupported fuel"):
        RustHazardModel().init(bad, weather, cfg, ign)
    with pytest.raises(ValueError, match="grid mismatch"):
        RustHazardModel().init(
            land, replace(weather, u10=weather.u10[:, :, :2]), cfg, ign
        )
    with pytest.raises(RuntimeError, match="backend missing"):
        RustHazardModel(BackendOptions(binary=tmp_path / "absent")).init(
            land, weather, cfg, ign
        )


def test_checkpoint_corruption_and_version_changes_rejected(setup_model):
    model = setup_model[-1]()
    model.step(5, [])
    raw = model.save_state()
    with pytest.raises(ValueError):
        RustHazardModel().load_state(raw[:100])
    with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
        arrays = {k: archive[k].copy() for k in archive.files}
    metadata = json.loads(arrays["metadata"].tobytes())
    metadata["format"] = "future-version"
    arrays["metadata"] = np.frombuffer(json.dumps(metadata).encode(), np.uint8)
    stream = io.BytesIO()
    np.savez_compressed(stream, **arrays)
    with pytest.raises(ValueError, match="version mismatch"):
        RustHazardModel().load_state(stream.getvalue())


def test_hot_evidence_updates_posterior_toward_supported_members(setup_model):
    from pyproj import Transformer
    from ember.contracts import PointRadius

    model = setup_model[-1](n_members=16, horizon_min=120, reseed_frac=0)
    model.step(60, [])
    support = (model._arrivals <= 70).mean(axis=0)
    candidates = np.argwhere((support >= 0.2) & (support <= 0.8))
    assert len(candidates)
    r, c = candidates[len(candidates) // 2]
    lon, lat = Transformer.from_crs(
        model.land.grid.crs, "EPSG:4326", always_xy=True
    ).transform(model.geometry.x[r, c], model.geometry.y[r, c])
    rec = observation(model, observed=60, available=65, trust=0.8).model_copy(
        update={"geometry": PointRadius(lon=lon, lat=lat, radius_m=20)}
    )
    prior = float(np.sum(model._weights * (model._arrivals[:, r, c] <= 70)))
    result = model.step(65, [rec])
    posterior = float(np.sum(result.weights * (model._arrivals[:, r, c] <= 70)))
    assert posterior > prior and result.obs_used[0]["ll_spread"] > 0
    assert result.obs_used[0].get("reanchored") is False


def test_persistent_goes_requires_previous_strong_detection(setup_model):
    from ember.contracts import Footprint

    model = setup_model[-1](reseed_frac=0)
    point = observation(model, east_cells=12, trust=1).geometry
    lon, lat = point.lon, point.lat
    size = 0.0004
    polygon = Footprint(
        ring=[
            (lon - size, lat - size),
            (lon + size, lat - size),
            (lon + size, lat + size),
            (lon - size, lat + size),
            (lon - size, lat - size),
        ]
    )
    first = observation(model, identifier="goes-1", trust=1).model_copy(
        update={
            "kind": "goes_pixel",
            "sensor_id": "goes18",
            "footprint_key": "fixed-pixel",
            "fdc_class": 10,
            "geometry": polygon,
        }
    )
    model.step(5, [first])
    assert not any(model._anchors)
    second = first.model_copy(
        update={
            "obs_id": "goes-2",
            "observed_sim_ms": first.observed_sim_ms + 300_000,
            "available_sim_ms": first.available_sim_ms + 300_000,
            "fdc_class": 30,
        }
    )
    b = model.step(10, [second])
    assert b.obs_used[0]["reanchored"]
    assert all(
        any(item["obs_id"] == "goes-2" for item in anchors)
        for anchors in model._anchors
    )


def test_worker_counts_produce_identical_adapter_outputs(setup_model):
    a = setup_model[-1]()
    b = setup_model[-1]()
    a.options = BackendOptions(workers=1)
    b.options = BackendOptions(workers=2)
    x = a.step(5, [])
    y = b.step(5, [])
    np.testing.assert_array_equal(x.t_q, y.t_q)
    np.testing.assert_array_equal(a._arrivals, b._arrivals)


def test_native_checksum_failure_does_not_commit_state(setup_model, monkeypatch):
    from ember.hazard import bridge

    model = setup_model[-1]()
    monkeypatch.setattr(bridge, "sha256_file", lambda path: "invalid-checksum")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        model.step(5, [])
    assert model._last_issue is None and np.isinf(model._arrivals).all()


def test_wrong_native_protocol_is_rejected_before_init(setup_model, monkeypatch):
    from ember.hazard import bridge
    import subprocess

    monkeypatch.setattr(
        bridge.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a, 0, '{"protocol_version":999}', ""
        ),
    )
    with pytest.raises(RuntimeError, match="version mismatch"):
        setup_model[-1]()


def test_partial_tempering_resamples_instead_of_stalling_at_ess_floor(
    setup_model, monkeypatch
):
    from ember.hazard.likelihoods import GroundReportLikelihood

    model = setup_model[-1](reseed_frac=0)
    monkeypatch.setattr(
        GroundReportLikelihood,
        "loglik",
        staticmethod(lambda *args: np.arange(8, dtype=float) * 10),
    )
    result = model.step(5, [observation(model, trust=0.8, east_cells=0)])
    assert 0 < result.tempering_alpha < 1 and result.resampled
    np.testing.assert_array_equal(result.weights, np.full(8, 1 / 8))
    assert result.ess == pytest.approx(8)


def test_native_memory_limit_failure_is_transactional(setup_model):
    from dataclasses import replace

    model = setup_model[-1]()
    model.options = replace(model.options, memory_budget_bytes=1)
    with pytest.raises(RuntimeError, match="Rust propagation failed"):
        model.step(5, [])
    assert model._last_issue is None and np.isinf(model._arrivals).all()
