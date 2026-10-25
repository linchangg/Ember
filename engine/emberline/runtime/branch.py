"""Report acceptance and durable branch evidence without changing wire contracts."""

from __future__ import annotations
import asyncio
import json
import math
from pathlib import Path
import re
import numpy as np

from emberline.contracts import REPORT_HOT, ObservationRecord, Report, ReportStatus
from emberline.data.grid import lonlat_to_xy
from emberline.runtime import cache


class BranchSource:
    """Overlay durable injected evidence on the original availability-based source."""

    def __init__(self, base, records, start_ms=0, cycle_ms=300000):
        self.base = base.base if isinstance(base, BranchSource) else base
        self.records = list(records)
        self.start_ms, self.cycle_ms = start_ms, cycle_ms

    def collect(self, a, b):
        obs, scans = self.base.collect(a, b)

        def release(r):
            return (
                self.start_ms
                + ((r.available_sim_ms - self.start_ms) // self.cycle_ms + 1)
                * self.cycle_ms
            )

        return [*obs, *(r for r in self.records if a < release(r) <= b)], scans


def report_observation(report: Report, ctx, now: int, reports: list[Report]):
    if report.scenario_id != ctx.scenario_id:
        return None, "Wrong scenario"
    values = [report.lon, report.lat, report.radius_m, report.trust]
    if (
        not np.isfinite(values).all()
        or not -180 <= report.lon <= 180
        or not -90 <= report.lat <= 90
        or report.radius_m <= 0
        or not 0 <= report.trust <= 1
    ):
        return None, "Invalid report geometry or trust"
    if report.observed_sim_ms > now:
        return None, "Report observed time is in the future"
    if report.observed_sim_ms < ctx.t_ign_ms:
        return None, "Report precedes configured ignition"
    x, y = lonlat_to_xy(report.lon, report.lat, ctx.grid)
    g = ctx.grid
    if not (
        g["x0"] <= x < g["x0"] + g["nx"] * g["cell_m"]
        and g["y0"] - g["ny"] * g["cell_m"] < y <= g["y0"]
    ):
        return None, "Report is outside the model domain"
    if report.person_id and any(
        r.report_id != report.report_id
        and r.person_id == report.person_id
        and 0 <= report.created_wall_ms - r.created_wall_ms < 60000
        for r in reports
    ):
        return None, "Please wait 60 seconds between reports"
    hot, factor = REPORT_HOT[report.kind]
    return ObservationRecord(
        obs_id=f"obs_rep_{report.report_id}",
        scenario_id=ctx.scenario_id,
        branch_id=report.branch_id,
        sensor_id="ground_report",
        kind="ground_report",
        observed_sim_ms=report.observed_sim_ms,
        available_sim_ms=now,
        geometry=dict(
            type="point_radius",
            lon=report.lon,
            lat=report.lat,
            radius_m=report.radius_m,
        ),
        hot=hot,
        report_kind=report.kind,
        trust=report.trust * factor,
        provenance=report.provenance,
        source_ref=report.report_id,
    ), None


class BranchManager:
    def __init__(self, loop):
        self.loop = loop
        self.path = loop.root / loop.ctx.scenario_id / "branch-evidence.json"
        self.data = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {"branches": {}, "reports": {}}
        )
        self.base_source = (
            loop.source.base if isinstance(loop.source, BranchSource) else loop.source
        )

    def save(self):
        cache._write_json(self.path, self.data)

    def records(self, branch):
        chain = []
        seen = set()
        while branch != "main":
            if branch in seen or branch not in self.data["branches"]:
                raise ValueError("Unknown or cyclic persisted branch")
            seen.add(branch)
            item = self.data["branches"][branch]
            chain.append(ObservationRecord.model_validate(item["observation"]))
            branch = item["parent_id"]
        return list(reversed(chain))

    def queue(self, status):
        pending = getattr(self.loop, "report_status_updates", {})
        pending[status.report_id] = status
        self.loop.report_status_updates = pending

    async def restore(self, branch):
        loop = self.loop
        if branch not in self.data["branches"]:
            raise ValueError("Active hub branch has no local evidence archive")
        loop.branch = branch
        loop.source = BranchSource(
            self.base_source, self.records(branch), loop.ctx.start_ms, loop.ctx.cycle_ms
        )
        # Rebuild state/evidence before the fork without publishing ancestor cycles.
        await loop._replay_silently(self.data["branches"][branch]["fork_sim_ms"] + 1)
        loop._pending_publication = None
        loop._last = None
        loop._wake()

    async def accept(self, report: Report, sink, clock, all_reports, branches):
        loop = self.loop
        ctx = loop.ctx
        now = int(clock.sim_now())
        previous = self.data["reports"].get(report.report_id)
        if previous is not None:
            if previous.get("publication") == "pending":
                child = previous["result"]["branch_id"]
                item = self.data["branches"][child]
                existing = next((b for b in branches if b["branch_id"] == child), None)
                if existing is not None and any(
                    existing.get(k) != item[k]
                    for k in ("parent_id", "fork_sim_ms", "reason")
                ):
                    raise ValueError(
                        "Persisted report branch conflicts with hub lineage"
                    )
                if existing is None:
                    await sink.create_branch(
                        {k: v for k, v in item.items() if k != "observation"}
                    )
                await self.restore(child)
                previous["publication"] = "committed"
                self.save()
            self.queue(ReportStatus.model_validate(previous["status"]))
            return previous["result"]
        if loop.host is None:
            obs, reason = None, "Bundle replay cannot recompute report branches"
        elif report.branch_id != loop.branch:
            obs, reason = None, "Report belongs to a different active branch"
        else:
            obs, reason = report_observation(report, ctx, now, all_reports)
        if reason is None and (now < ctx.k0_ms or now >= ctx.end_ms):
            reason = "No next physics cycle in replay"
        if reason is not None:
            status = ReportStatus(
                report_id=report.report_id,
                status="rejected",
                reject_reason=reason,
                observation_id=None,
            )
            result = {"accepted": False, "reason": reason}
            self.data["reports"][report.report_id] = {
                "status": status.model_dump(),
                "result": result,
            }
            self.save()
            self.queue(status)
            loop.request_republish()
            return result
        if loop._pending_publication is not None:
            raise RuntimeError("Finish pending parent publication before branching")
        fork = ctx.start_ms + ((now - ctx.start_ms) // ctx.cycle_ms) * ctx.cycle_ms
        following = fork + ctx.cycle_ms
        parent = loop.branch
        await sink.set_clock_status(ctx.scenario_id, "computing")
        try:
            # Rebuild exactly the parent evidence chain through the fork, never publish it.
            await loop._replay_silently(following)
            await asyncio.to_thread(
                loop.host.ensure_state, parent, fork, loop._obs_for, loop.state.keys.get
            )
            if report.kind == "all_clear":
                g = ctx.grid
                x, y = lonlat_to_xy(report.lon, report.lat, g)
                row, col = (
                    int((g["y0"] - y) // g["cell_m"]),
                    int((x - g["x0"]) // g["cell_m"]),
                )
                burned = (
                    loop.host.model._arrivals[:, row, col]
                    <= (fork - ctx.t_ref_ms) / 60000
                )
                if float(np.sum(loop.host.model._weights * burned)) >= 0.9:
                    status = ReportStatus(
                        report_id=report.report_id,
                        status="rejected",
                        reject_reason="All-clear contradicts certainly burned cell",
                        observation_id=None,
                    )
                    result = {"accepted": False, "reason": status.reject_reason}
                    self.data["reports"][report.report_id] = {
                        "status": status.model_dump(),
                        "result": result,
                    }
                    self.save()
                    self.queue(status)
                    return result
            numbers = [
                int(m.group(1))
                for b in [*(x["branch_id"] for x in branches), *self.data["branches"]]
                if (m := re.fullmatch(r"b(\d+)", b))
            ]
            child = f"b{max(numbers, default=0) + 1}"
            obs = obs.model_copy(update={"branch_id": child})
            payload = dict(
                branch_id=child,
                scenario_id=ctx.scenario_id,
                parent_id=parent,
                fork_sim_ms=fork,
                reason=f"report:{report.report_id}",
            )
            # Persist the state/evidence BEFORE publishing the new hub branch. A lost
            # response can be reconciled from this sidecar, without redrawing evidence.
            cache.save_checkpoint(
                loop.root,
                ctx.scenario_id,
                child,
                fork,
                loop.host.model.save_state(),
                loop.state.keys.get(fork),
            )
            self.data["branches"][child] = {
                **payload,
                "observation": obs.model_dump(mode="json"),
            }
            status = ReportStatus(
                report_id=report.report_id,
                status="accepted",
                reject_reason=None,
                observation_id=obs.obs_id,
            )
            result = {
                "accepted": True,
                "branch_id": child,
                "fork_sim_ms": fork,
                "next_issue_sim_ms": following,
            }
            self.data["reports"][report.report_id] = {
                "status": status.model_dump(),
                "result": result,
                "publication": "pending",
            }
            self.save()
            await sink.create_branch(payload)
            loop.branch = child
            loop.source = BranchSource(
                self.base_source, self.records(child), ctx.start_ms, ctx.cycle_ms
            )
            loop.host.branch = child
            loop._last = None
            loop._target = None
            loop._cancel_briefings(following)
            status = ReportStatus(
                report_id=report.report_id,
                status="accepted",
                reject_reason=None,
                observation_id=obs.obs_id,
            )
            result = {
                "accepted": True,
                "branch_id": child,
                "fork_sim_ms": fork,
                "next_issue_sim_ms": following,
            }
            self.data["reports"][report.report_id] = {
                "status": status.model_dump(),
                "result": result,
                "publication": "committed",
            }
            self.save()
            self.queue(status)
            loop._wake()
            return result
        finally:
            await sink.set_clock_status(ctx.scenario_id, "ok")
