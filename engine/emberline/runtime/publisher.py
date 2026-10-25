"""Cached physics + live hub state -> CycleBundle. spec/03 §7–8, spec/00 §3.1, §8.

Risk, alerts and people are re-derived here on every publish from the cached
`zone_arrivals` plus the hub's current evac_min values, people and alert states, so
operator edits and joined phones survive a cached replay. No Claude call in this path:
a cached briefing (same input_hash as precompute) is reused, anything else is the
template and the loop may draft in the background.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from emberline.ai import briefing as brf
from emberline.contracts import (
    ActivityEvent,
    Alert,
    Briefing,
    Cycle,
    CycleBundle,
    ObservationRecord,
    ReportStatus,
    ScanEvent,
    SensorInfo,
    ZoneRisk,
)
from emberline.risk import alerts as alerts_mod
from emberline.risk import risk
from emberline.runtime import cache
from emberline.runtime.cache import PhysicsOutput
from emberline.runtime.sources import sensor_status
from emberline.sync.sink import HubSnapshot

if TYPE_CHECKING:
    from emberline.runtime.loop import Scenario

SAT_KINDS = frozenset({"goes_pixel", "viirs_pixel"})
LEVEL_RANK = {"advisory": 0, "warning": 1, "order_recommended": 2}


@dataclass
class CycleInputs:
    """One cycle's engine-side inputs: physics (None before ignition) and what became available."""

    k_ms: int
    phys: PhysicsOutput | None
    new_obs: list[ObservationRecord]
    scans: list[ScanEvent]
    scans_so_far: list[ScanEvent]
    prev_p120: dict[str, float] | None


def publishable(o: ObservationRecord) -> bool:
    """Hot satellite pixels and every non-satellite observation; never clear pixels."""
    return o.hot or o.kind not in SAT_KINDS


def _hm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, alerts_mod.PST).strftime("%H:%M")


def _sensors(ctx: Scenario, scans_so_far: list[ScanEvent], k_ms: int) -> list[SensorInfo]:
    # ponytail: every sensor every bundle (a handful of rows); deltas only if P2 drop_sensor needs it.
    by_id = {s.sensor_id: s for s in ctx.sensors}
    status = sensor_status(scans_so_far, k_ms, {s.sensor_id: s.enabled for s in ctx.sensors})
    return [by_id[d["sensor_id"]].model_copy(update={"status": d["status"], "last_data_sim_ms": d["last_data_sim_ms"]})
            for d in status]


def _ingest_events(ctx: Scenario, scans: list[ScanEvent]) -> list[dict]:
    out = []
    for s in scans:
        name = ctx.sensor_names.get(s.sensor_id, s.sensor_id)
        if s.mode == "pass":
            msg = f"{name} pass {_hm(s.t_start_sim_ms)} → {s.n_hot} detections"
        else:
            msg = f"{name} {_hm(s.t_start_sim_ms)} → {s.n_hot} hot px, {s.n_clear} clear"
        if not s.assimilated:
            msg += " (display only)"
        out.append({"stage": "ingest", "actor": s.sensor_id, "message": msg, "ref_kind": "scan_event", "ref_id": s.scan_id})
    return out


def _rule_lead(ctx: Scenario, rule_id: str) -> int:
    return next(r["lead_min"] for r in ctx.rules["rules"] if r["id"] == rule_id)


def _zones_label(zone_ids: list[str]) -> str:
    return zone_ids[0] if len(zone_ids) == 1 else f"{zone_ids[0]} +{len(zone_ids) - 1}"


def _alert_events(ctx: Scenario, new: list[Alert], superseded: list[Alert], risks: list[ZoneRisk]) -> list[dict]:
    by_zone = {r.zone_id: r for r in risks}
    out = []
    for a in new:
        lead = _rule_lead(ctx, a.rule_id)
        p = risk.p_at(by_zone[a.zone_ids[0]].p_by_lead, lead)
        rule = a.rule_id.split("_")[0]
        out.append({"stage": "alert", "actor": "rules", "level": "notice", "ref_kind": "alert", "ref_id": a.alert_id,
                    "message": f"{_zones_label(a.zone_ids)} crossed {rule} (P {p:.0%} by +{lead // 60}h)"})
    for a in superseded:
        out.append({"stage": "alert", "actor": "rules", "ref_kind": "alert", "ref_id": a.alert_id,
                    "message": f"{_zones_label(a.zone_ids)} {a.rule_id.split('_')[0]} superseded by an order recommendation"})
    return out


def _events(raw: list[dict], prefix: str, ctx: Scenario, branch: str, k_ms: int, wall_ms: int) -> list[ActivityEvent]:
    """ev_{cycle_id}_{i} (or ev_{bundle_id}_{i} before ignition), WAVE2 §1.5; wall_ms re-stamped now."""
    return [
        ActivityEvent(event_id=f"ev_{prefix}_{i}", scenario_id=ctx.scenario_id, branch_id=branch, sim_ms=k_ms,
                      wall_ms=wall_ms, **{**e, "message": e["message"][:140]})
        for i, e in enumerate(raw)
    ]


def _observations(new_obs: list[ObservationRecord], phys: PhysicsOutput | None, branch: str) -> list[ObservationRecord]:
    status = {s["obs_id"]: s for s in phys.obs_status} if phys else {}
    cycle_id = phys.cycle["cycle_id"] if phys else None
    out = []
    for o in new_obs:
        if not publishable(o):
            continue
        upd: dict = {"branch_id": branch}
        if o.obs_id in status:
            upd |= {"status": status[o.obs_id]["status"], "influence": status[o.obs_id]["influence"], "cycle_id": cycle_id}
        out.append(o.model_copy(update=upd))
    return out


def top_alert(alerts: list[Alert]) -> Alert | None:
    """The highest-level new proposal (ties: rank order); the one cycle briefing is for it."""
    proposed = [a for a in alerts if a.state == "proposed"]
    return max(proposed, key=lambda a: LEVEL_RANK[a.level], default=None)


def briefing_input(ctx: Scenario, alert: Alert, risks: list[ZoneRisk], cycle: Cycle, sensors: list[SensorInfo]) -> dict:
    return brf.build_input(alert, risks, ctx.zone_names, ctx.egress, ctx.critical, cycle, sensors, cycle.knowledge_sim_ms)


def cached_briefing(root: Path, ctx: Scenario, alert: Alert, inp: dict) -> Briefing | None:
    d = cache.load_briefing(root, ctx.scenario_id, brf.input_hash(inp))
    if d is None:
        return None
    ids = {"briefing_id": f"brf_{alert.alert_id}", "alert_id": alert.alert_id, "cycle_id": alert.cycle_id,
           "branch_id": alert.branch_id, "scenario_id": alert.scenario_id}
    return Briefing.model_validate({**d, **ids})


def with_briefing(alert: Alert, b: Briefing) -> Alert:
    """Claude's text replaces the template only if it validated."""
    if not b.grounded or b.fallback_used:
        return alert.model_copy(update={"briefing_id": b.briefing_id})
    return alert.model_copy(update={"text": b.text, "voice_script": b.voice_script, "draft_source": "claude",
                                    "briefing_id": b.briefing_id})


def build_bundle(
    ctx: Scenario,
    branch: str,
    inp: CycleInputs,
    hub: HubSnapshot,
    alerts: list[Alert],
    briefing_root: Path | None = None,
    reports: list[ReportStatus] | None = None,
) -> CycleBundle:
    """`alerts` = the hub's alerts on this lineage (the loop's mirror), used for cooldown/supersede/open orders."""
    k, phys, wall = inp.k_ms, inp.phys, int(time.time() * 1000)
    bundle_id = f"bnd_{ctx.scenario_id}_{branch}_{k}"
    sensors = _sensors(ctx, inp.scans_so_far, k)
    scans = [s.model_copy(update={"branch_id": branch}) for s in inp.scans]
    common = {"bundle_id": bundle_id, "scenario_id": ctx.scenario_id, "branch_id": branch, "knowledge_sim_ms": k,
              "observations": _observations(inp.new_obs, phys, branch), "scan_events": scans, "sensors": sensors,
              "reports": reports or [], "metrics": []}
    raw_events = _ingest_events(ctx, inp.scans)
    if phys is None:
        return CycleBundle(**common, cycle=None, zone_risk=[], alerts=[], briefings=[], people=[],
                           events=_events(raw_events, bundle_id, ctx, branch, k, wall))

    cycle = Cycle.model_validate({**phys.cycle, "source": "cache" if phys.cached else "live"})
    zone_ids = {z["zone_id"] for z in ctx.zones}
    evac = {z: float(row["evac_min"]) for z, row in hub.zones.items() if z in zone_ids}
    open_r2 = {z for a in alerts if a.level == "order_recommended" and a.state in alerts_mod.ACTIVE for z in a.zone_ids}
    risks = risk.zone_risks(
        phys.zone_arr, phys.weights, k, ctx.t_ref_ms, ctx.zones, evac, inp.prev_p120, ctx.critical, open_r2,
        cycle.cycle_id, ctx.scenario_id, branch, ctx.horizon_min, ctx.act_by_q,
    )
    res = alerts_mod.evaluate(risks, alerts, k, cycle.cycle_id, ctx.scenario_id, branch, ctx.rules,
                              ctx.zone_names, ctx.critical, cycle.data_through_sim_ms)
    new_r2 = {z for a in res.new if a.level == "order_recommended" for z in a.zone_ids}
    risks = risk.apply_open_orders(risks, open_r2 | new_r2)

    new, briefings = list(res.new), []
    top = top_alert(new)
    if top is not None and briefing_root is not None:
        cached = cached_briefing(briefing_root, ctx, top, briefing_input(ctx, top, risks, cycle, sensors))
        if cached is not None:
            briefings = [cached]
            new = [with_briefing(a, cached) if a.alert_id == top.alert_id else a for a in new]
    raw_events += phys.events + _alert_events(ctx, res.new, res.superseded, risks)
    return CycleBundle(
        **common, cycle=cycle, zone_risk=risks, alerts=new + res.superseded, briefings=briefings,
        people=risk.people(hub.people, risks), events=_events(raw_events, cycle.cycle_id, ctx, branch, k, wall),
    )
