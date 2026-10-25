"""Reproducible synthetic skill checks, with explicit pass/fail gates (not historical skill)."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import time
import numpy as np
from scipy.ndimage import binary_erosion, distance_transform_edt
from emberline.contracts import ObservationRecord
from .api import Grid, Landscape, Weather, ModelConfig, Ignition
from .bridge import BackendOptions
from .export import write_json
from .likelihoods import SatelliteLikelihood
from .experiments import apply_synthetic_truth_override
from .model import RustHazardModel


def inputs(members=32, seed=7):
    grid = Grid("EPSG:32611", 380000, 3803000, 90, 65, 81, 1736294400)
    shape = (grid.H, grid.W)
    fuel = np.full(shape, 102, np.int16)
    fuel[38:50, 35:65] = 91
    zeros = np.zeros(shape, np.float32)
    land = Landscape(grid, fuel, zeros, zeros.copy(), zeros.copy())
    weather = Weather(
        np.array([0, 600], np.float32),
        np.full((2, *shape), 6, np.float32),
        np.zeros((2, *shape), np.float32),
        np.full(2, 22, np.float32),
        np.full(2, 0.25, np.float32),
    )
    cfg = ModelConfig(n_members=members, spotting=False, seed=seed)
    ign = Ignition(0, np.array([[grid.x0 + 20.5 * 90, grid.y0 - 25.5 * 90]]), 90)
    return land, weather, cfg, ign


def model_for(values, mode="pf", workers=2):
    land, weather, cfg, ign = values
    model = RustHazardModel(BackendOptions(workers=workers, timeout_s=300))
    model.init(land, weather, replace(cfg, filter=mode), ign)
    return model


def pixel(model, identifier, now, bounds, kind, hot, repeated=False):
    x0, y0, x1, y1 = bounds
    from pyproj import Transformer

    inverse = Transformer.from_crs(model.land.grid.crs, "EPSG:4326", always_xy=True)
    ring = [
        inverse.transform(x, y)
        for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    ]
    stamp = round((model.land.grid.t_ref_s + now * 60) * 1000)
    return ObservationRecord(
        obs_id=identifier,
        scenario_id="synthetic-twin",
        sensor_id="twin-goes" if kind == "goes_pixel" else "twin-viirs",
        kind=kind,
        observed_sim_ms=stamp,
        available_sim_ms=stamp,
        geometry={"type": "footprint", "ring": ring},
        hot=hot,
        trust=1.0,
        confidence=0.9,
        fdc_class=(30 if repeated else 10)
        if hot and kind == "goes_pixel"
        else 15
        if kind == "goes_pixel"
        else None,
        footprint_key=f"{kind}-{bounds}",
        provenance="simulated",
        source_ref="synthetic hidden-parameter experiment",
    )


def sorensen(a, b):
    total = int(a.sum() + b.sum())
    return 1.0 if total == 0 else float(2 * np.sum(a & b) / total)


def twin(members, workers):
    values = inputs(members)
    pf = model_for(values, workers=workers)
    baseline = model_for(values, "none", workers)
    truth = model_for(
        (*values[:2], replace(values[2], n_members=1), values[3]), "none", workers
    )
    apply_synthetic_truth_override(
        truth,
        ros_mult=1.6,
        wind_speed_mult=1.0,
        wind_dir_bias_deg=20.0,
        dfm_1h=0.04,
        urban_ros=2.0,
        member=0,
    )
    truth._ignition_xy[:] = values[3].points_xy
    truth.step(0, [])
    actual = truth._arrivals.copy()
    grid = values[0].grid
    # Actual 2.3 x 2.8 km GOES-like footprints, including negative observations.
    bounds = [
        (grid.x0 + x, grid.y0 - y - 2800, grid.x0 + x + 2300, grid.y0 - y)
        for x in range(0, grid.W * 90, 2300)
        for y in range(0, grid.H * 90, 2800)
    ]
    rng = np.random.default_rng(20261024)
    previous = {}
    cycles = []
    for now in range(5, 121, 5):
        records = []
        for k, box in enumerate(bounds):
            rec = pixel(
                pf,
                f"goes-{now}-{k}",
                now,
                box,
                "goes_pixel",
                True,
                previous.get(k, False),
            )
            prepared = pf.geometry.prepare(rec)
            logp = SatelliteLikelihood.loglik(
                actual, rec, prepared, pf._tau, grid.cell_m**2 / 10000, now
            )
            hot = bool(rng.random() < np.exp(logp[0]))
            rec = rec.model_copy(
                update={
                    "hot": hot,
                    "fdc_class": 30
                    if hot and previous.get(k, False)
                    else 10
                    if hot
                    else 15,
                }
            )
            previous[k] = hot
            records.append(rec)
        if now in {55, 115}:
            active = (
                (actual[0] <= now) & (actual[0] >= now - 15) & (values[0].fuel != 91)
            )
            candidates = np.argwhere(active)
            # A high-resolution pass samples every third active cell and clear controls.
            selected = (
                candidates[:: max(1, len(candidates) // 8)] if len(candidates) else []
            )
            for k, (r, c) in enumerate(selected):
                x, y = pf.geometry.x[r, c], pf.geometry.y[r, c]
                records.append(
                    pixel(
                        pf,
                        f"viirs-{now}-{k}",
                        now,
                        (x - 187.5, y - 187.5, x + 187.5, y + 187.5),
                        "viirs_pixel",
                        True,
                    )
                )
        result = pf.step(now, records)
        cycles.append(
            {
                "now_min": now,
                "ess_fraction": result.ess / members,
                "alpha": result.tempering_alpha,
                "resampled": result.resampled,
                "params": result.params_summary,
                "duration_ms": result.duration_ms,
            }
        )
        print(
            f"Twin {now}/120 min: ESS {result.ess:.2f}/{members}, alpha {result.tempering_alpha:.5f}",
            flush=True,
        )
    open_loop = baseline.step(120, [])
    true_mask = actual[0] <= 240
    pf_score = sorensen(result.t_q[2] <= 120, true_mask)
    baseline_score = sorensen(open_loop.t_q[2] <= 120, true_mask)
    angle = result.params_summary["wind_dir_bias_deg"][0]
    ros = result.params_summary["ros_mult"][0]
    ess_fraction = float(np.mean([c["ess_fraction"] >= 0.2 for c in cycles]))
    gates = {
        "angle_within_8_deg": abs(angle - 20) <= 8,
        "ros_within_25_percent": abs(ros - 1.6) <= 0.25 * 1.6,
        "ess_stable_80_percent_cycles": ess_fraction >= 0.8,
        "pf_beats_none_sorensen": pf_score > baseline_score,
    }
    return {
        "members": members,
        "truth": {"wind_dir_bias_deg": 20, "ros_mult": 1.6, "urban_ros": 2.0},
        "posterior": result.params_summary,
        "pf_sorensen_plus_2h": pf_score,
        "none_sorensen_plus_2h": baseline_score,
        "ess_stable_fraction": ess_fraction,
        "gates": gates,
        "passed": all(gates.values()),
        "cycles": cycles,
        "limits": [
            "Synthetic surface experiment only; no historical data",
            "GOES draws use model likelihood; VIIRS positives are idealized",
            "Urban parameter recovery is not an acceptance gate",
        ],
    }


def report_effect(members, workers, seeds=(7, 8, 9)):
    shifts = []
    heads = []
    for seed in seeds:
        values = inputs(members, seed)
        baseline = model_for(values, "none", workers)
        treatment = model_for(values, workers=workers)
        before = baseline.step(60, [])
        frontier = np.argwhere(before.t_q[2] == 0)
        r, c = frontier[np.argmax(frontier[:, 1])]
        x, y = baseline.geometry.x[r, c] + 1000, baseline.geometry.y[r, c]
        from pyproj import Transformer

        lon, lat = Transformer.from_crs(
            values[0].grid.crs, "EPSG:4326", always_xy=True
        ).transform(x, y)
        stamp = round((values[0].grid.t_ref_s + 60 * 60) * 1000)
        report = ObservationRecord(
            obs_id=f"report-{seed}",
            scenario_id="synthetic-twin",
            sensor_id="report",
            kind="ground_report",
            observed_sim_ms=stamp,
            available_sim_ms=stamp,
            geometry={"type": "point_radius", "lon": lon, "lat": lat, "radius_m": 150},
            hot=True,
            trust=0.25,
            report_kind="fire",
            provenance="simulated",
        )
        after = treatment.step(60, [report])
        a = before.t_q[2] <= 120
        b = after.t_q[2] <= 120
        edge_a = a & ~binary_erosion(a)
        edge_b = b & ~binary_erosion(b)
        symmetric = np.r_[
            distance_transform_edt(~edge_a, sampling=90)[edge_b],
            distance_transform_edt(~edge_b, sampling=90)[edge_a],
        ]
        shifts.append(float(symmetric.mean()))
        heads.append(
            float((np.argwhere(b)[:, 1].max() - np.argwhere(a)[:, 1].max()) * 90)
        )
    return {
        "seeds": list(seeds),
        "members": members,
        "mean_symmetric_boundary_shift_m": float(np.mean(shifts)),
        "per_seed_boundary_shift_m": shifts,
        "per_seed_east_head_shift_m": heads,
        "gate_m": 300,
        "passed": bool(np.mean(shifts) >= 300),
        "definition": "Mean nearest-boundary distance in both directions for the q50 +2h contour, 90m grid",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--members", type=int, default=32)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    started = time.perf_counter()
    result = {
        "synthetic": True,
        "twin": twin(args.members, args.workers),
        "report_effect": report_effect(args.members, args.workers),
    }
    result["wall_seconds"] = time.perf_counter() - started
    write_json(args.out, result)
    print(
        f"Evaluation saved to {args.out}: twin={result['twin']['passed']}, report={result['report_effect']['passed']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
