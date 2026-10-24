"""Offline publication helpers; ForecastArrays remain the physics/runtime boundary."""

from __future__ import annotations
import math
from pathlib import Path
import json
import numpy as np
from pyproj import Transformer
from shapely.geometry import box, MultiPolygon, mapping, MultiLineString
from shapely.ops import unary_union
from shapely import transform
from emberline.contracts import CycleBundle, SCHEMA_VERSION, LEADS_MIN
from .cdf import encode_arrival


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


def weighted_quantile(times, weights, q):
    order = np.argsort(times, kind="stable")
    c = np.cumsum(weights[order])
    c[-1] = 1.0
    return float(times[order[min(np.searchsorted(c, q, side="left"), len(order) - 1)]])


def zone_rows(
    model, forecast, zone_features, index, scenario, cycle_id, issue_ms, previous
):
    arrivals = model.region_arrivals(index, len(zone_features))
    rows = []
    for z, feature in enumerate(zone_features):
        properties = feature["properties"]
        times = arrivals[:, z]
        p = [
            float(np.sum(forecast.weights * (times <= forecast.t_issue_min + lead)))
            for lead in LEADS_MIN
        ]

        def eta(q):
            value = weighted_quantile(times, forecast.weights, q)
            return (
                None
                if not np.isfinite(value)
                or value > forecast.t_issue_min + model.cfg.horizon_min
                else round((model.land.grid.t_ref_s + value * 60) * 1000)
            )

        eta30 = eta(0.3)
        evac = properties.get("evac_min_default", 45)
        act = None if eta30 is None else eta30 - round(evac * 60_000)
        status = (
            "order"
            if p[1] >= 0.5 or (act is not None and act - issue_ms <= 15 * 60_000)
            else "warning"
            if p[3] >= 0.3
            else "watch"
            if p[-1] >= 0.1
            else "clear"
        )
        zone = properties["zone_id"]
        delta = p[3] - previous.get(zone, p[3])
        previous[zone] = p[3]
        tier = properties["tier"]
        population = properties["population"]
        rows.append(
            dict(
                cycle_id=cycle_id,
                scenario_id=scenario,
                branch_id="main",
                zone_id=zone,
                knowledge_sim_ms=issue_ms,
                p_by_lead=p,
                eta_q10_sim_ms=eta(0.1),
                eta_q50_sim_ms=eta(0.5),
                eta_q90_sim_ms=eta(0.9),
                act_by_sim_ms=act,
                act_by_basis=f"q30 arrival − {evac:g} min evac",
                evac_min=evac,
                population=population,
                housing=properties["housing"],
                n_critical=properties["n_critical"],
                tier=tier,
                risk_score=p[3]
                * (1 + math.log10(1 + population))
                * {1: 2.0, 2: 1.3, 3: 1.0}[tier],
                rank=0,
                status=status,
                trend="up" if delta > 0.05 else "down" if delta < -0.05 else "flat",
                drivers=[
                    f"P {p[3] * 100:.0f}% by +2h",
                    "Synthetic integration fixture",
                ],
            )
        )
    severity = {"order": 3, "warning": 2, "watch": 1, "clear": 0}
    rows.sort(key=lambda r: (-severity[r["status"]], -r["risk_score"], r["zone_id"]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def proposed_alerts(rows, issue_ms, scenario, cycle_id, history):
    """Fixture-only proposals: no external notification or operator action is performed."""
    active = {"proposed", "approved", "calling", "called", "acknowledged"}
    transitions = []
    groups = {"R1_WARNING": [], "R2_ORDER": []}
    for row in rows:
        if row["population"] < 1:
            continue
        conditions = {
            "R1_WARNING": row["p_by_lead"][3] >= 0.3,
            "R2_ORDER": row["p_by_lead"][1] >= 0.5
            or (
                row["act_by_sim_ms"] is not None
                and row["act_by_sim_ms"] - issue_ms <= 900_000
            ),
        }
        for rule, holds in conditions.items():
            level = "warning" if rule == "R1_WARNING" else "order_recommended"
            if not holds:
                continue
            blocked = any(
                row["zone_id"] in a["zone_ids"]
                and (
                    (
                        a["level"] == level
                        and (
                            a["state"] in active
                            or issue_ms - a["triggered_sim_ms"] < 1_800_000
                        )
                    )
                    or (
                        level == "warning"
                        and a["level"] == "order_recommended"
                        and a["state"] in active
                    )
                )
                for a in history
            )
            if not blocked:
                groups[rule].append(row)
    new = []
    for rule, crossed in groups.items():
        batches = [crossed] if len(crossed) >= 3 else [[r] for r in crossed]
        for batch in batches:
            if not batch:
                continue
            level = "warning" if rule == "R1_WARNING" else "order_recommended"
            ids = [r["zone_id"] for r in batch]
            top = batch[0]
            text = f"DEMO REPLAY: {', '.join(ids)} — {'warning' if level == 'warning' else 'order recommended'}. Synthetic Rust forecast; operator review required."
            alert = dict(
                alert_id=f"al_main_{ids[0]}_{rule}_{issue_ms}",
                scenario_id=scenario,
                branch_id="main",
                zone_ids=ids,
                level=level,
                rule_id=rule,
                rule_text="P(fire reaches zone within 2 h) ≥ 30%"
                if level == "warning"
                else "P(fire reaches zone within 1 h) ≥ 50%, or act-by time within 15 min",
                cycle_id=cycle_id,
                triggered_sim_ms=issue_ms,
                act_by_sim_ms=top["act_by_sim_ms"],
                state="proposed",
                text=text[:300],
                voice_script=text,
                draft_source="template",
            )
            if level == "order_recommended":
                for old in history + new:
                    if (
                        old["level"] == "warning"
                        and old["state"] == "proposed"
                        and set(old["zone_ids"]) & set(ids)
                    ):
                        old["state"] = "superseded"
                        transitions.append(old.copy())
            new.append(alert)
    history.extend(new)
    return [a for a in new if a["state"] == "proposed"] + transitions


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
):
    grid = model.land.grid
    cycle_id = f"cyc_{scenario}_main_{issue_ms}"
    relative = f"cache/{scenario}/main/{issue_ms}"
    directory = Path(root) / relative
    if directory.exists():
        raise FileExistsError(directory)
    directory.mkdir(parents=True)
    encode_arrival(forecast.t_q).tofile(directory / "arrival_q.bin")
    burned = forecast.p_burned_now >= 0.5
    length, burned_geom, front = mask_geometry(burned, grid)
    write_json(
        directory / "perimeter.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "burned",
                    "properties": {"kind": "burned"},
                    "geometry": burned_geom,
                },
                {
                    "type": "Feature",
                    "id": "front",
                    "properties": {"kind": "front"},
                    "geometry": front,
                },
            ],
        },
    )
    contours = []
    for lead in range(60, model.cfg.horizon_min + 1, 60):
        _, _, line = mask_geometry(forecast.t_q[2] <= lead, grid)
        contours.append(
            {
                "type": "Feature",
                "properties": {"lead_min": lead, "q": 0.5},
                "geometry": line,
            }
        )
    write_json(
        directory / "isochrones.geojson",
        {"type": "FeatureCollection", "features": contours},
    )
    danger = {}
    for lead in [60, 120]:
        _, polygon, _ = mask_geometry(forecast.t_q[1] <= lead, grid)
        danger[f"lead{lead}_p30"] = polygon
    if len(json.dumps(danger)) > 20_000:
        raise ValueError("danger geometry exceeds frontend contract size budget")
    rows = zone_rows(
        model,
        forecast,
        zone_features,
        zone_index,
        scenario,
        cycle_id,
        issue_ms,
        previous,
    )
    alerts = proposed_alerts(rows, issue_ms, scenario, cycle_id, alert_history)
    counts = {}
    used = {entry["obs_id"]: entry for entry in forecast.obs_used}
    obs = []
    for rec in observations:
        entry = used[rec.obs_id]
        counts[rec.kind] = counts.get(rec.kind, 0) + int(entry["used"])
        if rec.hot:
            obs.append(
                rec.model_copy(
                    update={
                        "status": "assimilated" if entry["used"] else "rejected",
                        "influence": entry["ll_spread"],
                        "cycle_id": cycle_id,
                    }
                )
            )
    latest = max(
        (r.observed_sim_ms for r in model._history),
        default=round((model.land.grid.t_ref_s + model.ign.t_min * 60) * 1000),
    )
    hectares = float(np.sum(forecast.p_burned_now)) * grid.cell_m**2 / 10000
    cycle = dict(
        cycle_id=cycle_id,
        scenario_id=scenario,
        branch_id="main",
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
        assets={
            "arrival_q": relative + "/arrival_q.bin",
            "perimeter": relative + "/perimeter.geojson",
            "isochrones": relative + "/isochrones.geojson",
        },
        danger=danger,
        burned_ha=hectares,
        front_km=length,
        growth_ha_per_h=0
        if previous_ha is None
        else (hectares - previous_ha) * 60 / model.cfg.cycle_min,
        head_bearing_deg=None,
        params_summary=forecast.params_summary,
        model_version=model.model_version,
        config_hash=None,
        duration_ms=forecast.duration_ms,
    )
    bundle = CycleBundle.model_validate(
        dict(
            schema_version=SCHEMA_VERSION,
            bundle_id=f"bnd_{scenario}_main_{issue_ms}",
            scenario_id=scenario,
            branch_id="main",
            knowledge_sim_ms=issue_ms,
            cycle=cycle,
            zone_risk=rows,
            observations=obs,
            scan_events=scans,
            sensors=sensors,
            alerts=alerts,
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
    return bundle, hectares
