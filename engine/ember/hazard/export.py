"""Offline publication helpers; ForecastArrays remain the physics/runtime boundary."""

from __future__ import annotations
from pathlib import Path
import json
import re
import numpy as np
from pyproj import Transformer
from shapely.geometry import box, MultiPolygon, mapping, MultiLineString
from shapely.ops import unary_union
from shapely import transform
from ember.contracts import CycleBundle, SCHEMA_VERSION


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n"
    )


def mask_geometry(mask, grid):
    """Run-length rectangles preserve islands/holes, then project metres to lon/lat."""
    rectangles = []
    for r, row in enumerate(mask):
        changes = np.diff(np.r_[False, row, False].astype(np.int8))
        for left, right in zip(
            np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)
        ):
            rectangles.append(
                box(
                    grid.x0 + left * grid.cell_m,
                    grid.y0 - (r + 1) * grid.cell_m,
                    grid.x0 + right * grid.cell_m,
                    grid.y0 - r * grid.cell_m,
                )
            )
    merged = unary_union(rectangles)
    if merged.is_empty:
        return (
            0.0,
            {"type": "MultiPolygon", "coordinates": []},
            {"type": "MultiLineString", "coordinates": []},
        )
    length = merged.length / 1000
    projected = merged.simplify(grid.cell_m / 2, preserve_topology=True)
    inverse = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    geographic = transform(
        projected, lambda xy: np.column_stack(inverse.transform(xy[:, 0], xy[:, 1]))
    )
    multi = (
        MultiPolygon([geographic]) if geographic.geom_type == "Polygon" else geographic
    )
    lines = []
    for polygon in multi.geoms:
        lines.append(list(polygon.exterior.coords))
        lines.extend(list(interior.coords) for interior in polygon.interiors)
    return length, mapping(multi), mapping(MultiLineString(lines))


def publish(
    root,
    model,
    forecast,
    scenario,
    issue_ms,
    observations,
    scans,
    sensors,
    zone_features,
    zone_index,
    previous,
    alert_history,
    previous_ha,
    *,
    branch="main",
):
    """Use Cody's current risk, alert and summarizer implementations, unchanged."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", branch):
        raise ValueError("Invalid publication branch ID")
    from ember.runtime.summarize import write_assets
    from ember.risk.risk import zone_risks, apply_open_orders
    from ember.risk.alerts import evaluate, load_rules, ACTIVE

    grid = model.land.grid
    cycle_id = f"cyc_{scenario}_{branch}_{issue_ms}"
    relative = f"cache/{scenario}/{branch}/{issue_ms}"
    directory = Path(root) / relative
    if directory.exists():
        raise FileExistsError(directory)
    directory.mkdir(parents=True)
    g = {
        "crs": grid.crs,
        "nx": grid.W,
        "ny": grid.H,
        "cell_m": grid.cell_m,
        "x0": grid.x0,
        "y0": grid.y0,
    }
    assets, danger, stats = write_assets(
        forecast,
        directory,
        relative,
        g,
        model.burnable,
        previous_ha,
        model.cfg.cycle_min,
    )
    zones = [f["properties"] for f in zone_features]
    open_orders = {
        z
        for a in alert_history
        if a.level == "order_recommended" and a.state in ACTIVE
        for z in a.zone_ids
    }
    critical_types = {}  # No invented facility categories; fixture assets remain separately available.
    rows = zone_risks(
        model.region_arrivals(zone_index, len(zones)),
        forecast.weights,
        issue_ms,
        grid.t_ref_s * 1000,
        zones,
        {},
        previous,
        critical_types,
        open_orders,
        cycle_id,
        scenario,
        branch,
        horizon_min=model.cfg.horizon_min,
    )
    latest = max(
        (r.observed_sim_ms for r in model._history),
        default=round((grid.t_ref_s + model.ign.t_min * 60) * 1000),
    )
    result = evaluate(
        rows,
        alert_history,
        issue_ms,
        cycle_id,
        scenario,
        branch,
        load_rules(),
        {z["zone_id"]: z.get("name", z["zone_id"]) for z in zones},
        critical_types,
        latest,
    )
    updates = {a.alert_id: a for a in result.all}
    alert_history[:] = [updates.pop(a.alert_id, a) for a in alert_history] + list(
        updates.values()
    )
    open_orders = {
        z
        for a in alert_history
        if a.level == "order_recommended" and a.state in ACTIVE
        for z in a.zone_ids
    }
    rows = apply_open_orders(rows, open_orders)
    previous.update({r.zone_id: r.p_by_lead[3] for r in rows})
    counts = {}
    used = {entry["obs_id"]: entry for entry in forecast.obs_used}
    published = []
    for rec in observations:
        entry = used[rec.obs_id]
        counts[rec.kind] = counts.get(rec.kind, 0) + int(entry["used"])
        if rec.hot:
            published.append(
                rec.model_copy(
                    update={
                        "status": "assimilated" if entry["used"] else "rejected",
                        "influence": entry["ll_spread"],
                        "cycle_id": cycle_id,
                        "branch_id": branch,
                    }
                )
            )
    cycle = dict(
        cycle_id=cycle_id,
        scenario_id=scenario,
        branch_id=branch,
        knowledge_sim_ms=issue_ms,
        data_through_sim_ms=latest,
        created_wall_ms=1792868400000,
        source="cache",
        filter_mode=forecast.filter_mode,
        n_members=model.cfg.n_members,
        ess=forecast.ess,
        tempering_alpha=forecast.tempering_alpha,
        resampled=forecast.resampled,
        n_obs_by_kind=counts,
        horizon_min=model.cfg.horizon_min,
        step_min=model.cfg.out_step_min,
        assets=assets,
        danger=danger,
        **stats,
        params_summary=forecast.params_summary,
        model_version=model.model_version,
        config_hash=None,
        duration_ms=forecast.duration_ms,
    )
    bundle = CycleBundle.model_validate(
        dict(
            schema_version=SCHEMA_VERSION,
            bundle_id=f"bnd_{scenario}_{branch}_{issue_ms}",
            scenario_id=scenario,
            branch_id=branch,
            knowledge_sim_ms=issue_ms,
            cycle=cycle,
            zone_risk=rows,
            observations=published,
            scan_events=[dict(s, branch_id=branch) for s in scans],
            sensors=sensors,
            alerts=result.all,
            briefings=[],
            people=[],
            reports=[],
            metrics=[],
            events=[],
        )
    )
    write_json(directory / "bundle.json", bundle.model_dump(mode="json"))
    write_json(
        directory / "diagnostics.json",
        {
            "native": {
                k: v for k, v in model.native_diagnostics.items() if k != "arrival_path"
            },
            "observations": forecast.obs_used,
            "synthetic": True,
        },
    )
    return bundle, stats["burned_ha"]
