"""Synthetic compatibility path through conversion, Rust, and quantiles.

This is not a HazardModel acceptance test. The application protocol has not
landed, and these runs are an uncalibrated research fixture.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from adapter.arrivals import (
    ARRIVAL_INITIAL_UNKNOWN,
    ARRIVAL_INVALID,
    ARRIVAL_MODELED,
    ARRIVAL_NOT_REACHED,
    PARTIAL_REJECT,
    Q_CENSORED,
    Q_INITIAL_UNKNOWN,
    Q_INVALID,
    MemberOutcome,
    unconditional_arrival_quantiles,
)
from adapter.bridge import ensemble_publishes_member_arrivals, simulate
from adapter.conversions import (
    SPECIFIED_APPLICATION_QUANTILES,
    AppGrid,
    TimeOrigins,
    WeatherFrame,
    app_grid_to_rust,
    flip_rows_rust_to_app,
    format_rust_utc,
    minutes_since_t_ref,
    prepare_research_bundle,
    signed_distance_phi,
    write_research_bundle,
)
from adapter.errors import UnsupportedMapping

SURFACE = Path(__file__).resolve().parents[2]
TIMEOUT_S = 180.0


def _epoch_ms() -> tuple[int, int, int]:
    # 2025-01-08T00:00:00Z and 02:15:00Z. Issue equals the Rust origin and differs from t_ref.
    t_ref = 1_736_294_400_000
    t_start = t_ref + (2 * 60 + 15) * 60_000
    t_issue = t_start
    assert format_rust_utc(t_ref) == "2025-01-08T00:00:00Z"
    assert format_rust_utc(t_start) == "2025-01-08T02:15:00Z"
    return t_ref, t_start, t_issue


@pytest.fixture(scope="module")
def fire_cli() -> Path:
    override = os.environ.get("FIRE_CLI")
    path = Path(override) if override else SURFACE / "target" / "release" / "fire-cli"
    if not path.is_file():
        pytest.fail(
            f"release fire-cli is required at {path}. "
            "Build it once with: cargo build --release -p fire-cli --locked"
        )
    return path


def _scenario(wind_east: float, wind_north: float, directory: Path) -> tuple[Path, dict]:
    t_ref, t_start, t_issue = _epoch_ms()
    grid = AppGrid("EPSG:32611", 500_000.0, 3_800_000.0, 90.0, 8, 6)
    elevation = np.arange(grid.height * grid.width, dtype=np.float64).reshape(grid.height, grid.width)
    elevation = elevation + 0.25
    valid = np.ones((grid.height, grid.width), dtype=np.uint8)
    valid[0, 0] = 0
    burnable = np.ones((grid.height, grid.width), dtype=np.uint8)
    burnable[0, 1] = 0
    fuel = np.ones((grid.height, grid.width), dtype=np.int16)
    center_x = grid.x0_m + (3.0 + 0.5) * grid.cell_m
    center_y = grid.y0_m - (6.0 + 0.5) * grid.cell_m
    phi = signed_distance_phi(grid, center_x, center_y, 200.0)
    phi = phi.copy()
    phi[valid == 0] = grid.cell_m
    phi[burnable == 0] = grid.cell_m
    frame = WeatherFrame(
        time_s=0.0,
        valid_time_ms=t_start + 600_000,
        available_time_ms=t_start - 60_000,
        wind_east_m_s=wind_east,
        wind_north_m_s=wind_north,
        m1=0.06,
        m10=0.07,
        m100=0.08,
        mlh=0.60,
        mlw=0.90,
    )
    bundle = prepare_research_bundle(
        grid,
        elevation_m=elevation,
        fuel_id=fuel,
        valid=valid,
        burnable=burnable,
        phi=phi,
        frames=[frame],
        origins=TimeOrigins(t_ref, t_start, t_issue),
        incident_id="synthetic-asymmetric-uncalibrated",
        horizons_s=[300.0, 600.0],
        coverage_end_s=600.0,
        components_are_midflame=True,
    )
    manifest = write_research_bundle(directory, bundle)
    context = {
        "grid": grid,
        "elevation": elevation,
        "valid": valid,
        "burnable": burnable,
        "phi": phi,
        "t_ref": t_ref,
        "t_start": t_start,
        "t_issue": t_issue,
    }
    return manifest, context


def _manual_quantile(statuses: list[int], times: list[float], level: float, horizon: float) -> tuple[str, float]:
    finite = sorted(time_s for status, time_s in zip(statuses, times, strict=True) if status == ARRIVAL_MODELED and 0.0 <= time_s <= horizon)
    usable = len(statuses)
    scaled = round(level * 1_000_000)
    rank = (scaled * usable + 1_000_000 - 1) // 1_000_000
    if rank > len(finite):
        return "censored", float("nan")
    return "finite", finite[rank - 1]


def test_synthetic_path_matches_direct_invocation_and_distribution(tmp_path: Path, fire_cli: Path) -> None:
    manifest, context = _scenario(1.0, 0.25, tmp_path / "member-a")
    grid: AppGrid = context["grid"]
    rust_grid = app_grid_to_rust(grid)
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert stored["grid"]["x0_m"] == rust_grid.x0_m
    assert stored["grid"]["y0_m"] == pytest.approx(rust_grid.y0_m)
    assert stored["grid"]["crs"] == "EPSG:32611"
    assert stored["grid"]["cell_size_m"] == 90.0
    elevation = np.fromfile(manifest.parent / "elevation.f64le", dtype="<f8").reshape(grid.height, grid.width)
    assert elevation[-1, 0] == context["elevation"][0, 0]
    assert elevation[0, -1] == context["elevation"][-1, -1]
    frame = stored["environment"]["frames"][0]
    assert frame["available_utc"] == format_rust_utc(context["t_start"] - 60_000)
    assert frame["observed_or_valid_utc"] == format_rust_utc(context["t_start"] + 600_000)
    assert stored["start_utc"] == format_rust_utc(context["t_start"])
    assert stored["start_utc"] != format_rust_utc(context["t_ref"])

    published = simulate(fire_cli, manifest, tmp_path / "bridge-a", timeout_s=TIMEOUT_S)
    direct = tmp_path / "direct-a"
    completed = subprocess.run(
        [str(fire_cli), "simulate", "--input", str(manifest), "--out", str(direct)],
        check=False,
        shell=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    bridge_time = np.fromfile(published.out_dir / "arrival_time_s.f64le", dtype="<f8")
    direct_time = np.fromfile(direct / "arrival_time_s.f64le", dtype="<f8")
    bridge_status = np.fromfile(published.out_dir / "arrival_status.u8", dtype=np.uint8)
    direct_status = np.fromfile(direct / "arrival_status.u8", dtype=np.uint8)
    assert np.array_equal(bridge_time, direct_time)
    assert np.array_equal(bridge_status, direct_status)

    repeated = simulate(fire_cli, manifest, tmp_path / "bridge-a-repeat", timeout_s=TIMEOUT_S)
    assert np.array_equal(repeated.arrival_time_s, published.arrival_time_s)
    assert np.array_equal(repeated.arrival_status, published.arrival_status)
    assert published.run["model"] == "surface_v1"
    assert published.run["solver"] == "level_set_lf_euler_v1"
    assert published.run["status"] == "complete"
    assert published.run["schema_version"] == 2
    assert published.run["start_utc"] == stored["start_utc"]
    assert "quality_flags" in published.run
    assert "step_count" in published.diagnostics
    assert published.arrival_time_s.shape == (grid.height, grid.width)
    assert not (published.out_dir / "arrival_q.bin").exists()

    status_app = flip_rows_rust_to_app(published.arrival_status)
    time_app = flip_rows_rust_to_app(published.arrival_time_s)
    assert status_app[0, 0] == ARRIVAL_INVALID
    assert status_app[0, 1] == ARRIVAL_NOT_REACHED
    burned = context["phi"] < 0.0
    assert burned.any()
    assert np.all(status_app[burned] == ARRIVAL_INITIAL_UNKNOWN)
    unburned_modeled = (context["valid"] == 1) & (context["burnable"] == 1) & ~burned
    assert np.isin(status_app[unburned_modeled], (ARRIVAL_NOT_REACHED, ARRIVAL_MODELED)).all()
    modeled = status_app == ARRIVAL_MODELED
    if modeled.any():
        assert np.all((time_app[modeled] >= 0.0) & (time_app[modeled] <= 600.0))
        sample = float(time_app[modeled][0])
        assert minutes_since_t_ref(context["t_start"], context["t_ref"], sample) == np.float32(
            (context["t_start"] - context["t_ref"]) / 60_000 + sample / 60.0
        )

    members = [published]
    manifests = [manifest]
    for index, (east, north) in enumerate(((0.0, 0.0), (2.5, -0.5))):
        other_manifest, _other = _scenario(east, north, tmp_path / f"member-{index}")
        manifests.append(other_manifest)
        members.append(simulate(fire_cli, other_manifest, tmp_path / f"run-{index}", timeout_s=TIMEOUT_S))
    outcomes = []
    for member in members:
        outcomes.append(
            MemberOutcome(
                status=flip_rows_rust_to_app(member.arrival_status),
                time_s=flip_rows_rust_to_app(member.arrival_time_s),
            )
        )
    distribution = unconditional_arrival_quantiles(
        outcomes,
        requested_member_count=3,
        quantile_levels=SPECIFIED_APPLICATION_QUANTILES,
        horizon_s=600.0,
        partial_policy=PARTIAL_REJECT,
        t_start_ms=context["t_start"],
        t_ref_ms=context["t_ref"],
    )
    assert distribution.time_since_t_ref_min.shape == (5, grid.height, grid.width)
    assert distribution.ensemble_coverage == 1.0
    assert distribution.calibrated_probability is False
    assert distribution.label.startswith("unweighted_scenario_frequency")
    assert np.all(distribution.status[:, 0, 0] == Q_INVALID)
    assert np.all(np.isnan(distribution.time_since_t_ref_min[:, 0, 0]))
    assert np.all(distribution.status[:, burned] == Q_INITIAL_UNKNOWN)
    assert np.all(distribution.status[:, 0, 1] == Q_CENSORED)
    # Independent check at a far north-east cell, from the definition rather than the helper's rank function.
    assert context["phi"][0, 5] > 0.0
    assert context["valid"][0, 5] == 1 and context["burnable"][0, 5] == 1
    far_status = [int(item.status[0, 5]) for item in outcomes]
    far_time = [float(item.time_s[0, 5]) for item in outcomes]
    for level, minutes, status in zip(
        distribution.quantile_levels,
        distribution.time_since_t_ref_min[:, 0, 5],
        distribution.status[:, 0, 5],
        strict=True,
    ):
        kind, seconds = _manual_quantile(far_status, far_time, level, 600.0)
        if kind == "censored":
            assert status == Q_CENSORED
            assert np.isnan(minutes)
        else:
            expected = minutes_since_t_ref(context["t_start"], context["t_ref"], seconds)
            assert minutes == expected

    ensemble_out = tmp_path / "ensemble"
    config = tmp_path / "ensemble.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "preset": "synthetic_uncalibrated",
                "members": 2,
                "master_seed": 7,
                "workers": 1,
                "memory_budget_bytes": 268_435_456,
                "wind_bias_std_m_s": 0.1,
                "dead_moisture_log_std": 0.01,
                "live_moisture_log_std": 0.01,
                "front_bias_std_m": 0.0,
                "allow_partial": False,
            }
        ),
        encoding="utf-8",
    )
    ensemble = subprocess.run(
        [
            str(fire_cli),
            "ensemble",
            "--input",
            str(manifest),
            "--config",
            str(config),
            "--out",
            str(ensemble_out),
        ],
        check=False,
        shell=False,
        capture_output=True,
        text=True,
    )
    assert ensemble.returncode == 0, ensemble.stderr
    metadata = json.loads((ensemble_out / "ensemble.json").read_text(encoding="utf-8"))
    assert metadata["n_complete"] == 2
    assert metadata["operational_probability_claim"] is False
    assert "conditional" in metadata["quantiles"]
    assert ensemble_publishes_member_arrivals(ensemble_out) is False
    with pytest.raises(UnsupportedMapping):
        # Humidity is not a moisture input, and this fixture does not invent that mapping.
        from adapter.conversions import reject_humidity_as_moisture

        reject_humidity_as_moisture(0.2)
