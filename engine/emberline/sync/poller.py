"""Hub poller: clock + commands -> SimClock + loop. spec/03 §10, spec/04 §3 (poll loop).

Every 500 ms while playing, 1 s while paused. Commands are handled in created_wall_ms
order and each is marked done or failed. Nothing in here may crash the engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from emberline.contracts import ActivityEvent, Alert, Report
from emberline.runtime.loop import Loop
from emberline.sync.sink import HubSnapshot, PublicationUnavailable

log = logging.getLogger(__name__)
PLAYING_S, PAUSED_S = 0.5, 1.0
Result = tuple[str, dict]


class Poller:
    def __init__(self, sink: Any, clock: Any, loop: Loop, profile: str = "online", local_state: Any = None) -> None:
        self.sink, self.clock, self.loop = sink, clock, loop
        self.scenario_id = loop.ctx.scenario_id
        self.profile, self.local = profile, local_state
        self.snapshot = HubSnapshot()
        self.handled: list[tuple[str, str]] = []  # (kind, status), for tests and logs
        self._n_ev = 0
        self._recovered_report_statuses = False

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("poll tick failed")
            playing = self.clock.state is not None and self.clock.state.playing
            await asyncio.sleep(PLAYING_S if playing else PAUSED_S)

    async def tick(self) -> HubSnapshot:
        snap = await self.sink.poll()
        if snap.clock is not None:
            self.clock.update(snap.clock)
        self.snapshot = snap
        if not self._recovered_report_statuses:
            from emberline.runtime.branch import BranchManager
            BranchManager(self.loop).recover_statuses()
            self._recovered_report_statuses = True
        active = snap.clock.active_branch_id if snap.clock is not None else self.loop.branch
        if active != self.loop.branch and self.loop.host is not None:
            from emberline.runtime.branch import BranchManager
            async with self.loop.lock:
                manager = BranchManager(self.loop)
                if active == "main":
                    self.loop.branch = "main"
                    self.loop.source = manager.base_source
                    self.loop.report_status_updates = {}
                    await self.loop._replay_silently(self.loop.ctx.start_ms)
                    self.loop._pending_publication = None
                else:
                    await manager.restore(active)
        for cmd in sorted(snap.commands, key=lambda c: c["created_wall_ms"]):
            await self.dispatch(cmd, snap)
        self._status()
        return snap

    def _status(self) -> None:
        if self.local is not None:
            self.local.status.update({**self.loop.status, "active_branch_id": self.loop.branch, "hub_ok": bool(getattr(self.sink, "ok", True))})

    async def dispatch(self, cmd: dict, snap: HubSnapshot) -> None:
        kind = cmd["kind"]
        handler = getattr(self, f"_on_{kind}", None)
        try:
            args = json.loads(cmd.get("args_json") or "{}")
            status, result = ("failed", {"reason": f"unknown command {kind}"}) if handler is None else await handler(args, snap)
        except PublicationUnavailable:
            # Leave this hub command pending so a lost branch acknowledgement or
            # parent-publication backlog can be reconciled on the next poll.
            log.warning("command %s awaiting publication acknowledgement", kind)
            return
        except Exception as e:
            log.exception("command %s failed", kind)
            status, result = "failed", {"error": f"{type(e).__name__}: {e}"[:200]}
        self.handled.append((kind, status))
        await self.sink.mark_command(cmd["command_id"], status, result)

    # ------------------------------------------------------------ 03 §10 table

    async def _on_clock_play(self, args: dict, snap: HubSnapshot) -> Result:
        self.loop._wake()
        return "done", {}

    _on_clock_pause = _on_clock_play
    _on_clock_speed = _on_clock_play

    async def _on_clock_seek(self, args: dict, snap: HubSnapshot) -> Result:
        sim_ms, from_ms = int(args["sim_ms"]), int(args.get("from_sim_ms", args["sim_ms"]))
        if sim_ms >= from_ms:
            self.loop._wake()  # forward: the loop sees sim_now jump and catches up in batches of <= 10
            return "done", {"direction": "forward"}
        async with self.loop.lock:
            await self.sink.rewind(self.scenario_id, self.loop.branch, sim_ms)
            if getattr(self.sink, "hub", None) is not None:
                updated = await self.sink.poll()
                if updated.clock is not None:
                    self.clock.update(updated.clock)
                    active = updated.clock.active_branch_id
                    if active != self.loop.branch:
                        from emberline.runtime.branch import BranchManager
                        manager = BranchManager(self.loop)
                        if active == "main":
                            self.loop.branch = active
                            self.loop.source = manager.base_source
                            self.loop.report_status_updates = {}
                            self.loop._pending_publication = None
                        else:
                            await manager.restore(active)
            self.loop.retarget(sim_ms + 1)
        return "done", {"direction": "back", "rewound_to": sim_ms}

    async def _on_reset(self, args: dict, snap: HubSnapshot) -> Result:
        async with self.loop.lock:
            self.loop.reset()
        return "done", {}

    async def _on_evac_min(self, args: dict, snap: HubSnapshot) -> Result:
        self.loop.request_republish()
        return "done", {}

    _on_person_joined = _on_evac_min

    async def _on_alert_declined(self, args: dict, snap: HubSnapshot) -> Result:
        await self._event("human", "operator", "Operator declined alert", args.get("alert_id"))
        return "done", {}

    async def _on_report(self, args: dict, snap: HubSnapshot) -> Result:
        from emberline.runtime.branch import BranchManager
        from emberline.sync.sink import _adapt
        report_id = args.get("report_id", "")
        import re
        if not isinstance(report_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", report_id):
            return "failed", {"reason": "invalid report ID"}
        hub = getattr(self.sink, "hub", None)
        if hub is None:
            return "failed", {"reason": "report branching needs a connected runtime hub"}
        sid = self.scenario_id.replace("'", "''")
        reports, branches = await hub.sql(f"SELECT * FROM report WHERE scenario_id = '{sid}'; SELECT * FROM branch WHERE scenario_id = '{sid}'")
        reports = [Report.model_validate(_adapt(row)) for row in reports]
        report = next((r for r in reports if r.report_id == report_id), None)
        if report is None:
            return "failed", {"reason": "unknown report"}
        async with self.loop.lock:
            manager = BranchManager(self.loop)
            result = await manager.accept(report, self.sink, self.clock, reports, branches)
        return "done", result

    async def _on_drop_sensor(self, args: dict, snap: HubSnapshot) -> Result:
        return "done", {"note": "drop_sensor is P2; the hub already toggled the sensor row"}

    async def _on_alert_approved(self, args: dict, snap: HubSnapshot) -> Result:
        alert = next((a for a in snap.alerts if a.alert_id == args.get("alert_id")), None)
        if alert is None:
            return "failed", {"reason": "unknown alert"}
        if alert.state != "approved" or not alert.decided_by:
            return "done", {"skipped": f"alert is {alert.state}"}  # the engine calls only for approved alerts
        call_status = await self._call(alert)
        return "done", {"call_status": call_status}

    # ------------------------------------------------------------ Cartesia (WAVE2 §1.1)

    def _duty_number(self) -> str | None:
        number = getattr(self.local, "duty_officer_number", None) if self.local is not None else None
        env = [n.strip() for n in os.environ.get("DUTY_OFFICER_NUMBERS", "").split(",") if n.strip()]
        return number or (env[0] if env else None)

    async def _call(self, alert: Alert) -> str:
        aid = alert.alert_id
        if self.profile == "offline":
            return await self._call_failed(aid, "offline", None)
        try:
            from emberline.notify import cartesia  # lazy: T11
        except ImportError:
            return await self._call_failed(aid, "unconfigured", None)
        number = self._duty_number()
        if not cartesia.configured() or not number:
            return await self._call_failed(aid, "unconfigured", None)
        from emberline.notify.dispatch import DispatchJournal
        journal = DispatchJournal(self.loop.root / self.scenario_id / "dispatch.sqlite3")
        # An alert is dispatched once. A replayed approval or worker restart must
        # not produce a second call, including after an uncertain network outcome.
        fresh, previous = journal.reserve(aid)
        if not fresh:
            if previous.state == "placed":
                await self.sink.update_alert_call(aid, "calling", previous.call_id, previous.call_status)
                await self.sink.update_alert_call(aid, "called", previous.call_id, previous.call_status)
                return previous.call_status or "placed"
            return (await self._call_uncertain(aid, previous.call_id) if previous.state in ("reserved", "unknown")
                    else await self._call_failed(aid, "previous_failure", previous.call_id))
        try:
            res = await cartesia.place_call(alert, number)
        except Exception:
            journal.finish(aid, "unknown", None, "delivery_unknown")
            return await self._call_uncertain(aid, None)
        # The current adapter cannot distinguish all rejection/timeout outcomes;
        # conservatively retain any unsuccessful attempt for manual reconciliation.
        journal.finish(aid, "placed" if res.ok else "unknown" if res.uncertain else "failed", res.call_id, res.call_status or "error")
        if not res.ok:
            return (await self._call_uncertain(aid, res.call_id) if res.uncertain
                    else await self._call_failed(aid, res.call_status or "error", res.call_id))
        await self.sink.update_alert_call(aid, "calling", res.call_id, res.call_status)
        await self._event("call", "cartesia", "Cartesia calling duty officer…", aid)
        await self.sink.update_alert_call(aid, "called", res.call_id, res.call_status)  # API confirmed placement
        return res.call_status

    async def _call_uncertain(self, aid: str, call_id: str | None) -> str:
        # Keep the existing calling state eligible for a late authenticated ack.
        # A timeout is not proof the recipient's phone never rang.
        await self.sink.update_alert_call(aid, "calling", call_id, "delivery_unknown")
        await self._event("call", "engine", "Call outcome unknown; reconcile provider status before retry", aid, level="warn")
        return "delivery_unknown"

    async def _call_failed(self, aid: str, call_status: str, call_id: str | None) -> str:
        await self.sink.update_alert_call(aid, "failed", call_id, call_status)
        await self._event("call", "engine", f"No call ({call_status}) → Play briefing here", aid, level="warn")
        return call_status

    async def _event(self, stage: str, actor: str, message: str, alert_id: str | None, level: str = "notice") -> None:
        self._n_ev += 1
        wall = int(time.time() * 1000)
        await self.sink.append_events([ActivityEvent(
            event_id=f"ev_{alert_id or 'cmd'}_{stage}{wall}_{self._n_ev}", scenario_id=self.scenario_id,
            branch_id=self.loop.branch, sim_ms=self.clock.sim_now(), wall_ms=wall, stage=stage, actor=actor,
            level=level, message=message[:140], ref_kind="alert" if alert_id else None, ref_id=alert_id,
        )])
