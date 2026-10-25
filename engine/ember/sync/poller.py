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

from ember.contracts import ActivityEvent, Alert
from ember.runtime.loop import Loop
from ember.sync.sink import HubSnapshot

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
        for cmd in sorted(snap.commands, key=lambda c: c["created_wall_ms"]):
            await self.dispatch(cmd, snap)
        self._status()
        return snap

    def _status(self) -> None:
        if self.local is not None:
            self.local.status.update({**self.loop.status, "hub_ok": bool(getattr(self.sink, "ok", True))})

    async def dispatch(self, cmd: dict, snap: HubSnapshot) -> None:
        kind = cmd["kind"]
        handler = getattr(self, f"_on_{kind}", None)
        try:
            args = json.loads(cmd.get("args_json") or "{}")
            status, result = ("failed", {"reason": f"unknown command {kind}"}) if handler is None else await handler(args, snap)
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
        # ponytail: main lineage only; rewinding a branch to its fork and switching to the parent is wave 3.
        async with self.loop.lock:
            await self.sink.rewind(self.scenario_id, self.loop.branch, sim_ms)
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
        return "failed", {"reason": "report branching lands in wave 3"}

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
            from ember.notify import cartesia  # lazy: T11
        except ImportError:
            return await self._call_failed(aid, "unconfigured", None)
        number = self._duty_number()
        if not cartesia.configured() or not number:
            return await self._call_failed(aid, "unconfigured", None)
        res = await cartesia.place_call(alert, number)
        if not res.ok:
            return await self._call_failed(aid, res.call_status or "error", res.call_id)
        await self.sink.update_alert_call(aid, "calling", res.call_id, res.call_status)
        await self._event("call", "cartesia", "Cartesia calling duty officer…", aid)
        await self.sink.update_alert_call(aid, "called", res.call_id, res.call_status)  # API confirmed placement
        return res.call_status

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
