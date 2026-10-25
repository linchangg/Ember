"""Run the complete synthetic Rust -> HazardModel -> frontend-file-contract demo."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import time
import numpy as np
from pyproj import Transformer
from shapely import contains_xy
from shapely.geometry import Polygon
from emberline.contracts import CycleBundle, ObservationRecord
from emberline.scripts import make_fixture as fixture
from .api import Grid, Landscape, Weather, ModelConfig, Ignition
from .bridge import BackendOptions, sha256_file
from .model import RustHazardModel
from .export import publish, write_json
from .geometry import centers

SCENARIO = "rust-fixture"


def rename(value):
    if isinstance(value, dict):
        return {k: rename(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rename(v) for v in value]
    if isinstance(value, str):
        return value.replace("fixture", SCENARIO)
    return value


def build_inputs(members, mode):
    # Same extent and grid as C0's synthetic fixture. Fuels/weather are explicitly synthetic.
    grid = Grid(
        "EPSG:32611",
        fixture.X0,
        fixture.Y0,
        fixture.CELL,
        fixture.NY,
        fixture.NX,
        1736294400,
    )
    shape = (grid.H, grid.W)
    fuel = np.full(shape, 102, np.int16)
    fuel[75:, 25:65] = 91
    fuel[:, 5] = 98
    land = Landscape(
        grid,
        fuel,
        np.zeros(shape, np.float32),
        np.zeros(shape, np.float32),
        np.zeros(shape, np.float32),
        np.zeros(shape, np.float32),
    )
    bearing = np.deg2rad(fixture.HEADING)
    times = np.arange(0, 781, 60, dtype=np.float32)
    u = np.full((len(times), *shape), 6 * np.sin(bearing), np.float32)
    v = np.full_like(u, 6 * np.cos(bearing))
    weather = Weather(
        times,
        u,
        v,
        np.full(len(times), 22, np.float32),
        np.full(len(times), 0.25, np.float32),
    )
    cfg = ModelConfig(n_members=members, spotting=False, filter=mode)
    ignition = Ignition(
        135, np.array([fixture.utm_forward(fixture.IGN_LON, fixture.IGN_LAT)]), 300
    )
    return land, weather, cfg, ignition


def run_demo(out: Path, *, members=16, cycles=36, mode="pf", workers=2):
    if out.exists():
        raise FileExistsError(f"Choose a new output directory: {out}")
    if not 1 <= cycles <= 36:
        raise ValueError("fixture demo cycles must be 1..36")
    out.mkdir(parents=True)
    source = fixture.REPO / "fixtures"
    source_static = source / "static/fixture"
    source_index = source / "cache/fixture/main/index.json"
    if not source_index.is_file():
        raise FileNotFoundError(
            "C0 fixture absent: run python -m emberline.scripts.make_fixture first"
        )
    static = out / "static" / SCENARIO
    shutil.copytree(source_static, static)
    for path in static.glob("*.json"):
        write_json(path, rename(json.loads(path.read_text())))
    for path in static.glob("*.geojson"):
        write_json(path, rename(json.loads(path.read_text())))
    manifest = json.loads((static / "manifest.json").read_text())
    manifest.update(
        title="RUST FIXTURE (synthetic)",
        honesty={
            "real": [],
            "simulated": [
                "terrain",
                "fuels",
                "weather",
                "observations",
                "zones",
                "residents",
            ],
            "derived": [
                "Rust level-set ensemble forecasts",
                "tempered observation assimilation",
            ],
        },
    )
    write_json(static / "manifest.json", manifest)
    land, weather, cfg, ign = build_inputs(members, mode)
    model = RustHazardModel(BackendOptions(workers=workers))
    model.init(land, weather, cfg, ign)
    zone_features = json.loads((static / "zones.geojson").read_text())["features"]
    index = np.full((land.grid.H, land.grid.W), -1, np.int32)
    forward = Transformer.from_crs("EPSG:4326", land.grid.crs, always_xy=True)
    x, y = centers(land.grid)
    for z, feature in enumerate(zone_features):
        ring = np.array(feature["geometry"]["coordinates"][0])
        east, north = forward.transform(ring[:, 0], ring[:, 1])
        index[contains_xy(Polygon(np.column_stack([east, north])), x, y)] = z
    if any(not (index == z).any() for z in range(len(zone_features))):
        raise ValueError("zone rasterization lost a fixture zone")
    entries = json.loads(source_index.read_text())["cycles"]
    generated = []
    diagnostics = []
    previous = {}
    alert_history = []
    previous_ha = None
    started = time.perf_counter()
    steps = 0
    used = 0
    reanchored = 0
    resume_checked = False
    for entry in entries:
        original = CycleBundle.model_validate_json(
            (source / entry["bundle"]).read_text()
        )
        issue = original.knowledge_sim_ms
        if original.cycle is None:
            bundle = rename(original.model_dump(mode="json"))
            bundle["bundle_id"] = f"bnd_{SCENARIO}_main_{issue}"
            rel = f"cache/{SCENARIO}/main/{issue}/bundle.json"
            write_json(out / rel, bundle)
            generated.append({"knowledge_sim_ms": issue, "bundle": rel})
            continue
        if steps >= cycles:
            break
        steps += 1
        now = (issue / 1000 - land.grid.t_ref_s) / 60
        records = [
            ObservationRecord.model_validate(rename(o.model_dump(mode="json")))
            for o in original.observations
        ]
        # Two synthetic high-resolution passes and a delayed ground report exercise non-GOES paths.
        if steps in {18, 30} and records:
            records.append(
                records[0].model_copy(
                    update={
                        "obs_id": f"obs_viirs_fixture_{issue}",
                        "sensor_id": "viirs_n20",
                        "kind": "viirs_pixel",
                        "fdc_class": None,
                        "confidence": 0.9,
                        "source_ref": "synthetic integration pass",
                    }
                )
            )
        if steps == 24:
            east, north = fixture.front_centers(90, 1)[0]
            lon, lat = Transformer.from_crs(
                land.grid.crs, "EPSG:4326", always_xy=True
            ).transform(east, north)
            records.append(
                ObservationRecord(
                    obs_id=f"obs_rep_fixture_{issue}",
                    scenario_id=SCENARIO,
                    sensor_id="fixture-report",
                    kind="ground_report",
                    observed_sim_ms=issue - 10 * 60_000,
                    available_sim_ms=issue,
                    geometry={
                        "type": "point_radius",
                        "lon": lon,
                        "lat": lat,
                        "radius_m": 150,
                    },
                    hot=True,
                    trust=0.25,
                    report_kind="fire",
                    provenance="simulated",
                    source_ref="synthetic delayed report",
                )
            )
        forecast = model.step(now, records)
        if steps == min(12, cycles):
            # Loading a fresh object must preserve the next forecast, not just array serialization.
            restored = RustHazardModel(BackendOptions(workers=workers))
            restored.load_state(model.save_state())
        if steps == min(12, cycles) + 1:
            comparison = restored.step(now, records)
            np.testing.assert_array_equal(forecast.t_q, comparison.t_q)
            np.testing.assert_array_equal(forecast.weights, comparison.weights)
            resume_checked = True
        bundle, previous_ha = publish(
            out,
            model,
            forecast,
            SCENARIO,
            issue,
            records,
            rename([s.model_dump(mode="json") for s in original.scan_events]),
            rename([s.model_dump(mode="json") for s in original.sensors]),
            zone_features,
            index,
            previous,
            alert_history,
            previous_ha,
        )
        relative = f"cache/{SCENARIO}/main/{issue}/bundle.json"
        generated.append({"knowledge_sim_ms": issue, "bundle": relative})
        used += sum(o["used"] for o in forecast.obs_used)
        reanchored += sum(o.get("reanchored", False) for o in forecast.obs_used)
        diagnostics.append(
            {
                "cycle": steps,
                "issue_ms": issue,
                "duration_ms": forecast.duration_ms,
                "ess": forecast.ess,
                "alpha": forecast.tempering_alpha,
                "observations_used": sum(o["used"] for o in forecast.obs_used),
                "reanchored": sum(
                    o.get("reanchored", False) for o in forecast.obs_used
                ),
            }
        )
        print(
            f"Rust fixture cycle {steps}/{cycles}: {forecast.duration_ms} ms, ESS {forecast.ess:.2f}/{members}, observations {len(records)}",
            flush=True,
        )
    write_json(
        out / f"cache/{SCENARIO}/main/index.json",
        {"scenario_id": SCENARIO, "branch_id": "main", "cycles": generated},
    )
    model.save_state()  # Check final state is serializable without storing a disposable checkpoint.
    summary = {
        "scenario_id": SCENARIO,
        "synthetic": True,
        "model_version": model.model_version,
        "forecast_cycles": steps,
        "preignition_cycles": 3,
        "members": members,
        "grid": [land.grid.H, land.grid.W],
        "horizon_min": cfg.horizon_min,
        "filter": mode,
        "observations_used": used,
        "reanchor_events": reanchored,
        "checkpoint_next_step_parity_checked": resume_checked,
        "wall_seconds": time.perf_counter() - started,
        "cycle_diagnostics": diagnostics,
        "backend": {
            k: v for k, v in model.native_diagnostics.items() if k != "arrival_path"
        },
        "limits": [
            "Synthetic integration evidence, not historical forecast skill",
            "Linda dashboard hookup and Cody runtime integration remain separate",
            "Spotting is explicitly disabled",
        ],
    }
    write_json(out / "verification.json", summary)
    shutil.copyfile(Path(__file__).with_name("demo_viewer.html"), out / "demo.html")
    hashes = {
        str(p.relative_to(out)): sha256_file(p) for p in out.rglob("*") if p.is_file()
    }
    write_json(out / "artifacts.sha256.json", hashes)
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k not in {"cycle_diagnostics", "backend"}
            },
            indent=2,
        ),
        flush=True,
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--members", type=int, default=16)
    parser.add_argument("--cycles", type=int, default=36)
    parser.add_argument("--filter", choices=["pf", "reinit", "none"], default="pf")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    run_demo(
        args.out,
        members=args.members,
        cycles=args.cycles,
        mode=args.filter,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
