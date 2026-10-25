"""Future, unassimilated positive-detection consistency; NOT perimeter validation.

Positive detections alone cannot measure false positives, area accuracy or full
probability calibration. No missing/cloudy pixel is converted to negative evidence.
"""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from emberline.runtime.sources import ReplaySource
from emberline.data.config import scenario_dir, load_scenario
from .bridge import BackendOptions
from .historical import digest
from .model import RustHazardModel


def consistency(arrivals, weights, support, event_min):
    a = np.asarray(arrivals)
    w = np.asarray(weights)
    support = np.asarray(support)
    if (
        a.ndim != 3
        or support.shape != a.shape[1:]
        or support.dtype != bool
        or not support.any()
    ):
        raise ValueError("Invalid footprint support")
    if (
        w.shape != (a.shape[0],)
        or not np.isfinite(w).all()
        or (w < 0).any()
        or not np.isclose(w.sum(), 1)
    ):
        raise ValueError("Invalid member weights")
    if np.isnan(a).any() or np.isneginf(a).any() or not np.isfinite(event_min):
        raise ValueError("Invalid arrivals or event time")
    burned = a[:, support] <= event_min
    return dict(
        weighted_fraction_of_burned_footprint_centers=float(w @ burned.mean(axis=1)),
        member_mass_with_any_burned_center=float(w @ burned.any(axis=1)),
        footprint_cell_centers=int(support.sum()),
    )


def evaluate(model, events):
    g = model.land.grid
    issue_ms = round((g.t_ref_s + model._last_issue * 60) * 1000)
    end_ms = issue_ms + model.cfg.horizon_min * 60000
    rows = []
    excluded = []
    for event in events:
        reason = None
        if not event.hot:
            reason = "not_a_positive_detection"
        elif event.obs_id in model._seen:
            reason = "already_present_in_forecast_evidence"
        elif event.observed_sim_ms <= issue_ms:
            reason = "not_after_forecast_issue"
        elif event.observed_sim_ms > end_ms:
            reason = "beyond_forecast_horizon"
        if reason:
            excluded.append(dict(obs_id=event.obs_id, reason=reason))
            continue
        prepared = model.geometry.prepare(event)
        if not prepared["in_domain"]:
            excluded.append(dict(obs_id=event.obs_id, reason="outside_grid_support"))
            continue
        values = consistency(
            model._arrivals,
            model._weights,
            prepared["support"],
            (event.observed_sim_ms / 1000 - g.t_ref_s) / 60,
        )
        rows.append(
            dict(
                obs_id=event.obs_id,
                sensor_id=event.sensor_id,
                observed_sim_ms=event.observed_sim_ms,
                available_sim_ms=event.available_sim_ms,
                lead_min=(event.observed_sim_ms - issue_ms) / 60000,
                **values,
            )
        )
    return dict(
        issue_sim_ms=issue_ms,
        evaluated_events=rows,
        excluded_events=excluded,
        diagnostic="future_positive_detection_geometric_consistency",
        scientific_acceptance="not_established",
        limits=[
            "Positive events only; false positives and probability calibration are not measured",
            "Not independent burned-perimeter or arrival-time ground truth",
            "Footprints may be correlated or clipped at model boundaries; no pixel-level confidence interval",
            "No forecast parameters are fitted by this diagnostic",
        ],
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--scenario", default="eaton")
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise FileExistsError(a.out)
    model = RustHazardModel(BackendOptions(timeout_s=900))
    model.load_state(a.checkpoint.read_bytes())
    cfg = load_scenario(a.scenario)
    scn = scenario_dir(a.scenario)
    from emberline.data.config import utc_ms

    source = ReplaySource(
        scn, a.scenario, [s for s in cfg["assimilate_sensors"] if s.startswith("viirs")]
    )
    events, _ = source.collect(-(2**62), utc_ms(cfg["replay_end_utc"]))
    report = evaluate(model, events)
    report.update(
        checkpoint_sha256=digest(a.checkpoint),
        event_table_sha256=digest(scn / "obs/viirs.parquet"),
        members=model.cfg.n_members,
        model_version=model.model_version,
        physics_sha256=model.backend_identity["physics_sha256"],
    )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("x") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    print(
        json.dumps(
            dict(
                evaluated=len(report["evaluated_events"]),
                excluded=len(report["excluded_events"]),
                diagnostic=report["diagnostic"],
                scientific_acceptance=report["scientific_acceptance"],
            )
        )
    )


if __name__ == "__main__":
    main()
