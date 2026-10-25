"""Engine M1 acceptance tests, spec/03 §14 tests 1–5 (WAVE2 T10), plus the forcing/cache/profile wiring.

SyntheticSource + the FakeHazardModel double (on the real ember.hazard.api) + an in-memory
fake hub, over a flat synthetic scenario written to tmp_path. Test 1 also has a hub-gated variant
on scenario "synth" (EMBER_HUB_TESTS=1, local `spacetime start`). The same orchestrator on
Abby's Rust backend is tests/test_engine_native.py (needs a built fire-bridge).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import hazard_standin
import numpy as np
import pytest
import rasterio
import yaml
from hazard_standin import flat_landscape
from rasterio.transform import Affine

from ember.ai import briefing as brf
from ember.contracts import ClockState, CycleBundle, ScanEvent
from ember.data import grid as gridlib
from ember.hazard.api import MODEL_VERSION, HazardModel, Weather
from ember.hazard import bridge, checkpoint
from ember.hazard.bridge import sha256_file
from ember.runtime import cache, publisher
from ember.runtime import loop as loop_mod
from ember.runtime.clock import FastClock, SimClock, sim_now_ms
from ember.runtime.loop import Loop, NcForcing, WeatherAsOf, load_scenario_dir, run_hash
from ember.runtime.model_host import landscape, make_host, model_config, model_grid, model_version, rust_backend
from ember.runtime.publisher import CycleInputs, build_bundle
from ember.runtime.sources import InboxSource, SyntheticSource
from ember.scripts.precompute import precompute
from ember.sync.cache_sink import CacheSink
from ember.sync.poller import Poller
from ember.sync.sink import HubSnapshot, apply_alerts

SCN = "synth"
MIN = 60_000
T0 = "2025-01-09T02:00:00Z"  # a day after Eaton, so no ID can collide with the fixture
G = {"crs": "EPSG:32611", "cell_m": 150, "nx": 60, "ny": 60, "x0": 390000, "y0": 3795000}
IGN_RC = (12, 48)
N_PHYS = 40
HEAD_DEG = 240.0
ZONES = [  # (zone_id, centre row/col, population, tier, evac default, egress)
    ("SYN-Z1", (12, 48), 400, 3, 45, 3),
    ("SYN-Z2", (19, 36), 1200, 1, 75, 1),
    ("SYN-Z3", (25, 25), 900, 2, 45, 2),
    ("SYN-Z4", (3, 43), 300, 3, 45, 2),
]


# ---------------------------------------------------------------- synthetic scenario package


def _cell_ring(r0: int, c0: int, r1: int, c1: int) -> list[list[float]]:
    pts = [(c0, r0), (c1, r0), (c1, r1), (c0, r1), (c0, r0)]
    xy = [(G["x0"] + c * G["cell_m"], G["y0"] - r * G["cell_m"]) for c, r in pts]
    return [[round(v, 6) for v in gridlib.xy_to_lonlat(x, y, G)] for x, y in xy]


def _centre_lonlat(r: int, c: int) -> tuple[float, float]:
    x, y = G["x0"] + (c + 0.5) * G["cell_m"], G["y0"] - (r + 0.5) * G["cell_m"]
    lon, lat = gridlib.xy_to_lonlat(x, y, G)
    return round(float(lon), 6), round(float(lat), 6)


def _tif(path: Path, a: np.ndarray, dtype: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tr = Affine(G["cell_m"], 0, G["x0"], 0, -G["cell_m"], G["y0"])
    with rasterio.open(path, "w", driver="GTiff", width=G["nx"], height=G["ny"], count=1, dtype=dtype,
                       crs=G["crs"], transform=tr) as dst:
        dst.write(a.astype(dtype), 1)


def write_scenario(root: Path, n_phys: int = N_PHYS) -> Path:
    d = root / SCN
    (d / "web").mkdir(parents=True)
    ign_lon, ign_lat = _centre_lonlat(*IGN_RC)
    t0 = int(np.datetime64(T0.rstrip("Z"), "ms").astype(np.int64))
    t_ign, end = t0 + 15 * MIN, t0 + 15 * MIN + (n_phys - 1) * 5 * MIN
    iso = lambda ms: str(np.datetime64(ms, "ms")) + "Z"
    cfg = {
        "scenario_id": SCN, "title": "SYNTHETIC TEST SCENARIO", "tz": "America/Los_Angeles",
        "t_ref_utc": iso(t0 - 120 * MIN), "t_ignition_utc": iso(t_ign), "replay_start_utc": iso(t0),
        "replay_end_utc": iso(end), "ignition": {"lon": ign_lon, "lat": ign_lat, "radius_m": 300}, "grid": G,
        "cycle_min": 5, "horizon_min": 360, "step_min": 30, "quantiles": [0.1, 0.3, 0.5, 0.7, 0.9], "seed": 7,
        "n_members": 16, "filter": "none", "assimilate_sensors": ["synthetic_goes"], "act_by_quantile": 0.3,
        "config_hash": None,
    }
    (d / "scenario.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    index = np.full((G["ny"], G["nx"]), -1, dtype=np.int16)
    feats = []
    for i, (zid, (r, c), pop, tier, evac, egress) in enumerate(ZONES):
        index[r - 3 : r + 3, c - 3 : c + 3] = i
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [_cell_ring(r - 3, c - 3, r + 3, c + 3)]},
                      "properties": {"zone_id": zid, "name": f"Synthetic zone {i + 1}", "population": pop,
                                     "housing": pop // 2, "n_critical": int(zid in ("SYN-Z2", "SYN-Z3")), "tier": tier,
                                     "evac_min_default": evac, "egress_roads": egress, "approximate": True}})
    _tif(d / "static" / "zone_index.tif", index, "int16")
    area = np.zeros((G["ny"], G["nx"]), dtype=np.int8)
    area[20:30, 20:30] = 1
    _tif(d / "static" / "area_x_index.tif", area, "int8")
    web = d / "web"
    (web / "zones.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    assets = [("syn-a1", "nursing_home", "SYN-Z2", ZONES[1][1]), ("syn-a2", "school", "SYN-Z3", ZONES[2][1])]
    (web / "assets.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": list(_centre_lonlat(*rc))},
         "properties": {"asset_id": aid, "type": t, "name": f"Synthetic {t}", "zone_id": z}}
        for aid, t, z, rc in assets]}), encoding="utf-8")
    sensors = [{"sensor_id": "synthetic_goes", "scenario_id": SCN, "kind": "goes_pixel", "name": "Synthetic GOES",
                "platform": "synthetic", "provenance": "simulated", "pixel_m": 2000.0}]
    (web / "sensors.json").write_text(json.dumps(sensors), encoding="utf-8")
    manifest = {"scenario_id": SCN, "title": "SYNTHETIC TEST SCENARIO", "tz": "America/Los_Angeles",
                "config_hash": None, "is_fixture": True, "t_ignition_sim_ms": t_ign, "replay_start_sim_ms": t0,
                "replay_end_sim_ms": end, "horizon_min": 360, "step_min": 30, "grid": G}
    (web / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    sp = _centre_lonlat(*ZONES[1][1])
    (web / "spawn_points.json").write_text(json.dumps(
        {"synth": [{"spawn_id": "syn_sp1", "lon": sp[0], "lat": sp[1], "zone_id": "SYN-Z2", "street": "Synthetic St"}]}),
        encoding="utf-8")
    return d


class FlatForcing:
    """One weather init, uniform 10 m/s wind toward 240° (from the NE), available before replay start."""

    def __init__(self, ctx, available_offset_ms: int = -60 * MIN) -> None:
        u, v = 10 * np.sin(np.radians(HEAD_DEG)), 10 * np.cos(np.radians(HEAD_DEG))
        shape = (1, G["ny"], G["nx"])
        self.w = Weather(t_min=np.array([0.0], dtype=np.float32), u10=np.full(shape, u, dtype=np.float32),
                         v10=np.full(shape, v, dtype=np.float32), temp_c=np.array([15.0], dtype=np.float32),
                         rh=np.array([0.1], dtype=np.float32))
        self.init_ms, self.avail = ctx.t_ref_ms, ctx.start_ms + available_offset_ms

    def weather_as_of(self, t_ms: int) -> WeatherAsOf:
        return WeatherAsOf(weather=self.w, init_ms=self.init_ms, available_ms=self.avail)


def write_hrrr(ctx, d: Path) -> list[tuple[int, Path]]:
    """Two SYNTHETIC HRRR inits in data/hrrr.py's file format, on the scenario grid:
    A at t_ref, every band out an hour before replay start (toward 240° at 6 m/s);
    B an hour later (toward 260° at 8 m/s): f00 out at k0+10 min, f01-f02 at k0+40 min
    (f02 is valid after that issue), f03-f09 never out during the replay."""
    from ember.data.hrrr import forcing_dataset, write_forcing

    xs, ys = gridlib.cell_centers(G)
    k0, never = ctx.k0_ms, ctx.end_ms + 600 * MIN
    out = []
    for init, avail, speed, head in [
        (ctx.t_ref_ms, [ctx.start_ms - 60 * MIN] * 10, 6.0, HEAD_DEG),
        (ctx.t_ref_ms + 60 * MIN, [k0 + 10 * MIN] + [k0 + 40 * MIN] * 2 + [never] * 7, 8.0, HEAD_DEG + 20),
    ]:
        shape = (10, G["ny"], G["nx"])
        u = np.full(shape, speed * np.sin(np.radians(head)), dtype=np.float32)
        v = np.full(shape, speed * np.cos(np.radians(head)), dtype=np.float32)
        ds = forcing_dataset(u, v, np.full(10, 15.0), np.full(10, 0.2), xs, ys, init, avail, "synthetic test forcing")
        out.append((init, write_forcing(ds, d / f"hrrr_{time.strftime('%Y%m%d%H', time.gmtime(init // 1000))}.nc")))
    return out


def synth_source(ctx) -> SyntheticSource:
    """A scan every 5 min: two hot pixels near ignition after it, one clear pixel always."""
    hot = _centre_lonlat(IGN_RC[0] + 2, IGN_RC[1] - 3)
    near = _centre_lonlat(*IGN_RC)
    clear = _centre_lonlat(50, 10)
    scans = []
    for k in range(ctx.start_ms - 3 * MIN, ctx.end_ms, 5 * MIN):
        pts = [(clear[0], clear[1], False)]
        if k >= ctx.t_ign_ms:
            pts = [(near[0], near[1], True), (hot[0], hot[1], True)] + pts
        scans.append((k, pts))
    return SyntheticSource(scans, scenario_id=SCN)


@pytest.fixture
def ctx(tmp_path):
    return load_scenario_dir(write_scenario(tmp_path / "scenarios"))


def run_precompute(ctx, root: Path, source=None, forcing=None) -> Loop:
    return asyncio.run(precompute(ctx, source or synth_source(ctx), forcing or FlatForcing(ctx),
                                  hazard_standin.FakeHazardModel, flat_landscape(ctx), root, "main",
                                  model_version=hazard_standin.MODEL_VERSION))


def physics_ks(ctx) -> list[int]:
    return [k for k in ctx.cycles() if k >= ctx.t_ign_ms]


# ---------------------------------------------------------------- in-memory hub


class FakeHub:
    """StateSink + hub clock/commands/rows with the 04 §2.2 rules the engine relies on."""

    TIME_COL: ClassVar[dict[str, str]] = {"cycle": "knowledge_sim_ms", "zone_risk": "knowledge_sim_ms", "observation": "available_sim_ms",
                "scan_event": "t_start_sim_ms"}

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        now = int(time.time() * 1000)
        self.clock = ClockState(scenario_id=ctx.scenario_id, mode="replay", playing=False, speed=240,
                                anchor_sim_ms=ctx.start_ms, anchor_wall_ms=now, epoch=0,
                                min_sim_ms=ctx.start_ms, max_sim_ms=ctx.end_ms)
        self.zones = {z["zone_id"]: {**z, "evac_min": z["evac_min_default"]} for z in ctx.zones}
        self.people: list = []
        self.alerts: dict = {}
        self.rows: dict[str, dict[str, int]] = {t: {} for t in self.TIME_COL}
        self.commands: list[dict] = []
        self.published: list[str] = []
        self.batches: list[int] = []
        self.bundles: list[CycleBundle] = []
        self.statuses: list[str] = []
        self.calls: list[tuple] = []
        self._n = 0

    # operator side
    def sim_now(self) -> int:
        return sim_now_ms(self.clock, int(time.time() * 1000))

    def _command(self, kind: str, args: dict) -> None:
        self._n += 1
        self.commands.append({"command_id": f"cmd_{self._n}", "scenario_id": self.ctx.scenario_id, "kind": kind,
                              "args_json": json.dumps(args), "status": "pending", "created_wall_ms": self._n})

    def _clock(self, **upd: Any) -> None:
        now = int(time.time() * 1000)
        base = {"anchor_sim_ms": self.sim_now(), "anchor_wall_ms": now}
        self.clock = self.clock.model_copy(update={**base, **upd})

    def play(self, speed: float) -> None:
        self._clock(playing=True, speed=speed)
        self._command("clock_play", {})

    def seek(self, sim_ms: int) -> None:
        frm = self.sim_now()
        self._clock(anchor_sim_ms=sim_ms, epoch=self.clock.epoch + 1)
        self._command("clock_seek", {"sim_ms": sim_ms, "from_sim_ms": frm})

    def set_evac(self, zone_id: str, minutes: float) -> None:
        self.zones[zone_id]["evac_min"] = minutes
        self._command("evac_min", {"zone_id": zone_id, "minutes": minutes})

    # StateSink
    async def publish(self, b: CycleBundle) -> None:
        self.published.append(b.bundle_id)
        self.bundles.append(b)
        if b.cycle is not None:
            self.rows["cycle"][b.cycle.cycle_id] = b.knowledge_sim_ms
        for r in b.zone_risk:
            self.rows["zone_risk"][f"{r.cycle_id}:{r.zone_id}"] = r.knowledge_sim_ms
        for o in b.observations:
            self.rows["observation"].setdefault(o.obs_id, o.available_sim_ms)
        for s in b.scan_events:
            self.rows["scan_event"].setdefault(s.scan_id, s.t_start_sim_ms)
        apply_alerts(self.alerts, b.alerts)

    async def publish_many(self, bs: list[CycleBundle]) -> None:
        assert len(bs) <= 10
        self.batches.append(len(bs))
        for b in bs:
            await self.publish(b)

    async def rewind(self, scn: str, branch: str, sim_ms: int) -> None:
        for table, rows in self.rows.items():
            self.rows[table] = {k: t for k, t in rows.items() if t <= sim_ms}
        self.alerts = {k: a for k, a in self.alerts.items() if a.triggered_sim_ms <= sim_ms}
        self.clock = self.clock.model_copy(update={"epoch": self.clock.epoch + 1})

    async def set_clock_status(self, scn: str, status: str) -> None:
        self.statuses.append(status)

    async def poll(self) -> HubSnapshot:
        return HubSnapshot(commands=[dict(c) for c in self.commands if c["status"] == "pending"], clock=self.clock,
                           zones={k: dict(v) for k, v in self.zones.items()}, people=list(self.people),
                           alerts=list(self.alerts.values()))

    async def mark_command(self, command_id: str, status: str, result: dict) -> None:
        for c in self.commands:
            if c["command_id"] == command_id:
                c["status"], c["result"] = status, result

    async def update_alert_call(self, alert_id, state, call_id, call_status) -> None:
        self.calls.append((alert_id, state, call_status))

    async def load_scenario(self, payload) -> None: ...
    async def create_branch(self, branch) -> None: ...
    async def upsert_alert_draft(self, *a) -> None: ...
    async def append_events(self, events) -> None: ...
    async def submit_report_engine(self, report, transcript) -> None: ...


class Engine:
    """Loop + poller over any sink; the test drives poller ticks every 20 ms instead of 500 ms."""

    def __init__(self, ctx, sink, root: Path, profile: str = "offline") -> None:
        self.clock = SimClock()
        host = make_host(ctx, hazard_standin.FakeHazardModel, flat_landscape(ctx), FlatForcing(ctx), root, None)
        self.loop = Loop(ctx, sink, self.clock, synth_source(ctx), host, root, "main", hazard_standin.MODEL_VERSION)
        self.poller = Poller(sink, self.clock, self.loop, profile)
        self.task: asyncio.Task | None = None

    async def until(self, cond: Callable[[], bool], timeout: float = 30.0) -> None:
        t_end = time.monotonic() + timeout
        while not cond():
            if self.task is not None and self.task.done():
                self.task.result()  # surface a crashed loop
            assert time.monotonic() < t_end, "timed out"
            await self.poller.tick()
            await asyncio.sleep(0.02)

    async def settle(self, n: int = 5) -> None:
        for _ in range(n):
            await self.poller.tick()
            await asyncio.sleep(0.02)


def with_engine(ctx, sink, root: Path, body, profile: str = "offline") -> None:
    async def main() -> None:
        eng = Engine(ctx, sink, root, profile)
        await eng.poller.tick()
        eng.task = asyncio.create_task(eng.loop.run())
        try:
            await body(eng)
        finally:
            eng.task.cancel()
            await asyncio.gather(eng.task, return_exceptions=True)

    asyncio.run(main())


def all_bundle_ids(ctx) -> list[str]:
    return [f"bnd_{SCN}_main_{k}" for k in ctx.cycles()]


# ---------------------------------------------------------------- 03 §14 test 1


def test_1_replay_publishes_each_cycle_once(ctx, tmp_path):
    root = tmp_path / "cache"
    run_precompute(ctx, root)
    hub = FakeHub(ctx)

    async def body(eng: Engine) -> None:
        hub.play(speed=20_000)  # ~0.7 s for the whole replay
        await eng.until(lambda: len(hub.published) >= len(ctx.cycles()))
        await eng.settle()

    with_engine(ctx, hub, root, body)
    assert hub.published == all_bundle_ids(ctx)
    assert len(hub.rows["cycle"]) == N_PHYS
    assert all(b.cycle is None for b in hub.bundles[:3]) and all(b.cycle for b in hub.bundles[3:])
    assert all(b.cycle.source == "cache" for b in hub.bundles[3:])  # precompute covered everything


def test_1_seek_forward_then_back(ctx, tmp_path):
    root = tmp_path / "cache"
    run_precompute(ctx, root)
    hub = FakeHub(ctx)
    plus3, plus1 = ctx.start_ms + 180 * MIN, ctx.start_ms + 60 * MIN
    upto3 = [b for b, k in zip(all_bundle_ids(ctx), ctx.cycles(), strict=True) if k <= plus3]

    async def body(eng: Engine) -> None:
        await eng.until(lambda: len(hub.published) == 1)  # replay_start bundle while paused
        hub.seek(plus3)
        await eng.until(lambda: len(hub.published) == len(upto3))
        await eng.settle()
        assert hub.published == upto3
        assert hub.batches and max(hub.batches) == 10  # catch-up in batches of <= 10
        assert hub.statuses[0] == "catching_up" and hub.statuses[-1] == "ok"
        hub.seek(plus1)
        await eng.until(lambda: eng.poller.handled.count(("clock_seek", "done")) == 2)
        await eng.settle()

    with_engine(ctx, hub, root, body)
    assert hub.published == upto3  # nothing published after the rewind while paused
    want = {f"cyc_{SCN}_main_{k}" for k in physics_ks(ctx) if k <= plus1}
    assert set(hub.rows["cycle"]) == want
    for table, rows in hub.rows.items():
        assert rows and max(rows.values()) <= plus1, table
    assert {r.split(":")[0] for r in hub.rows["zone_risk"]} == want


def test_1_seek_back_during_catch_up_publishes_the_gap(ctx, tmp_path):
    """Seek back to a time the catch-up has not published yet: every cycle <= target still gets published once."""
    root = tmp_path / "cache"
    run_precompute(ctx, root)

    class SlowHub(FakeHub):
        async def publish_many(self, bs):
            await asyncio.sleep(0.3)
            await super().publish_many(bs)

    hub = SlowHub(ctx)
    plus3, plus2 = ctx.start_ms + 180 * MIN, ctx.start_ms + 120 * MIN
    upto2 = [b for b, k in zip(all_bundle_ids(ctx), ctx.cycles(), strict=True) if k <= plus2]

    async def body(eng: Engine) -> None:
        await eng.until(lambda: len(hub.published) == 1)
        hub.seek(plus3)
        await eng.until(lambda: len(hub.batches) == 1)  # first catch-up batch landed, more still due
        hub.seek(plus2)
        await eng.until(lambda: eng.poller.handled.count(("clock_seek", "done")) == 2)
        await eng.until(lambda: len(hub.published) >= len(upto2))
        await eng.settle()

    with_engine(ctx, hub, root, body)
    assert hub.published == upto2
    assert set(hub.rows["cycle"]) == {f"cyc_{SCN}_main_{k}" for k in physics_ks(ctx) if k <= plus2}


def test_cache_coverage_stays_at_most_one(ctx, tmp_path):
    root = tmp_path / "cache"
    run_precompute(ctx, root)
    loop = asyncio.run(precompute(ctx, synth_source(ctx), FlatForcing(ctx), hazard_standin.FakeHazardModel,
                                  flat_landscape(ctx), root, "main", model_version="other"))  # every key stale
    assert loop.status["cache_coverage"] == 1.0


@pytest.mark.skipif(not os.environ.get("EMBER_HUB_TESTS"), reason="hub")
def test_1_hub_variant(ctx, tmp_path):
    """Replay at 900x into the local hub (scenario synth), then reset, seek +3 h, seek back to +1 h."""
    from ember.run import load_payload
    from ember.sync.stdb_http import StdbHttp
    from ember.sync.stdb_sink import StdbSink

    brf.load_env()
    pin = os.environ.get("OPERATOR_PIN", "")
    root = tmp_path / "cache"
    run_precompute(ctx, root)
    plus3, plus1 = ctx.start_ms + 180 * MIN, ctx.start_ms + 60 * MIN

    class CountingSink(StdbSink):
        published: ClassVar[list[str]] = []

        # Record only after the hub call returns, so "all published" means the rows are queryable.
        async def publish(self, b):
            await super().publish(b)
            self.published.append(b.bundle_id)

        async def publish_many(self, bs):
            await super().publish_many(bs)
            self.published.extend(b.bundle_id for b in bs)

    async def rows(hub, q):
        return (await hub.sql(q))[0]

    async def main() -> None:
        hub = StdbHttp(os.environ.get("STDB_URI", "http://localhost:3000"), os.environ.get("STDB_DB", "ember"),
                       os.environ["STDB_TOKEN"])
        sink = CountingSink(hub, SCN)
        sink.published = []
        try:
            await sink.load_scenario(load_payload(ctx.web_dir))
            await hub.call("reset_scenario", SCN, pin)
            await hub.call("set_pause_on_proposal", SCN, False, pin)
            eng = Engine(ctx, sink, root)
            await eng.poller.tick()
            task = eng.task = asyncio.create_task(eng.loop.run())
            try:
                await eng.until(lambda: ("reset", "done") in eng.poller.handled)
                await hub.call("clock_set_speed", SCN, 900, pin)
                await hub.call("clock_play", SCN, pin)
                await eng.until(lambda: set(all_bundle_ids(ctx)) <= set(sink.published), timeout=90)
                assert assert_once(sink.published, all_bundle_ids(ctx))
                cyc = await rows(hub, f"SELECT * FROM cycle WHERE scenario_id = '{SCN}'")
                assert len(cyc) == N_PHYS

                await hub.call("reset_scenario", SCN, pin)
                start_id = all_bundle_ids(ctx)[0]
                await eng.until(lambda: sink.published.count(start_id) == 2)  # reset republishes replay_start
                sink.published.clear()
                await hub.call("clock_seek", SCN, plus3, pin)
                ids3 = {b for b, k in zip(all_bundle_ids(ctx), ctx.cycles(), strict=True) if ctx.start_ms < k <= plus3}
                await eng.until(lambda: ids3 <= set(sink.published), timeout=60)
                await hub.call("clock_seek", SCN, plus1, pin)
                await eng.until(lambda: eng.poller.handled.count(("clock_seek", "done")) == 2)
                await eng.settle()
                checks = {"cycle": "knowledge_sim_ms", "zone_risk": "knowledge_sim_ms",
                          "observation": "available_sim_ms", "scan_event": "t_start_sim_ms", "alert": "triggered_sim_ms"}
                for table, col in checks.items():
                    got = await rows(hub, f"SELECT * FROM {table} WHERE scenario_id = '{SCN}'")
                    assert all(r[col] <= plus1 for r in got), table
                cyc = await rows(hub, f"SELECT * FROM cycle WHERE scenario_id = '{SCN}'")
                assert len(cyc) == sum(1 for k in physics_ks(ctx) if k <= plus1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            await hub.aclose()

    asyncio.run(main())


def assert_once(published: list[str], want: list[str]) -> bool:
    return all(published.count(b) == 1 for b in want)


# ---------------------------------------------------------------- 03 §14 test 2


def _arrival(root: Path, k: int) -> bytes:
    cycle = json.loads((cache.cycle_dir(root, SCN, "main", k) / "physics.json").read_text())["cycle"]
    return (root / Path(cycle["assets"]["arrival_q"]).relative_to("cache")).read_bytes()


def _key(root: Path, k: int) -> str:
    return json.loads((cache.cycle_dir(root, SCN, "main", k) / "physics.json").read_text(encoding="utf-8"))["key"]


def test_2_determinism_and_cache_miss_after_hits(ctx, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    run_precompute(ctx, a)
    run_precompute(ctx, b)
    ks = physics_ks(ctx)
    assert len(ks) == N_PHYS
    for k in ks:
        assert _key(a, k) == _key(b, k)
        assert _arrival(a, k) == _arrival(b, k)
        assert len(_arrival(a, k)) == 5 * G["nx"] * G["ny"]
    index = json.loads((a / SCN / "main" / "index.json").read_text(encoding="utf-8"))
    assert [c["knowledge_sim_ms"] for c in index["cycles"]] == ctx.cycles()  # pre-ignition bundles listed too

    k25 = ks[25]
    before = _arrival(a, k25)
    shutil.rmtree(cache.cycle_dir(a, SCN, "main", k25))
    sent: list = []
    monkeypatch.setattr(hazard_standin.FakeHazardModel, "update_weather", lambda self, w: sent.append(w))
    loop = run_precompute(ctx, a)
    assert loop.host.restored_from == [ks[24]]  # checkpoint 24, then exactly one step (load_state without init)
    phys = json.loads((cache.cycle_dir(a, SCN, "main", k25) / "physics.json").read_text(encoding="utf-8"))
    assert phys["identity"] == {"run_hash": loop.config_hash}  # the fake has no code identity
    assert sent == []  # the checkpoint's own weather is not re-sent (on Rust that adds a weather change)
    assert _arrival(a, k25) == before
    assert _key(a, k25) == _key(b, k25)


def test_checkpoint_from_another_lineage_is_never_restored(ctx, tmp_path):
    """A dev-profile checkpoint sits where the target run would look: the target run must not load it."""
    root = tmp_path / "cache"
    run_precompute(ctx, root)
    ks = physics_ks(ctx)
    before = _arrival(root, ks[25])
    shutil.rmtree(cache.cycle_dir(root, SCN, "main", ks[25]))
    ck = cache.latest_checkpoint(root, SCN, "main", ks[24])
    assert ck is not None and ck[0] == ks[24]
    cache.save_checkpoint(root, SCN, "main", ks[24], ck[1], key="other-lineage")
    loop = run_precompute(ctx, root)
    assert loop.host.restored_from == [ks[18]]  # skipped 24, rolled forward from 18
    assert _arrival(root, ks[25]) == before


def run_loop(ctx, root: Path, source=None, inbox=None, survive: bool = False) -> Loop:
    """precompute() with the live engine's knobs: an inbox and survive_physics_errors."""
    host = make_host(ctx, hazard_standin.FakeHazardModel, flat_landscape(ctx), FlatForcing(ctx), root)
    loop = Loop(ctx, CacheSink(root, ctx.zones), FastClock(), source or synth_source(ctx), host, root, "main",
                hazard_standin.MODEL_VERSION, inbox=inbox, stop_at_end=True, survive_physics_errors=survive)
    asyncio.run(loop.run())
    return loop


def bundle(root: Path, k: int) -> CycleBundle:
    return CycleBundle.model_validate_json((cache.cycle_dir(root, SCN, "main", k) / "bundle.json").read_text("utf-8"))


class DelayedSource:
    """The replay plus one hot pixel observed at `observed_ms` that only becomes available at `available_ms`."""

    def __init__(self, inner: SyntheticSource, observed_ms: int, available_ms: int) -> None:
        self.inner = inner
        o = inner._scan(observed_ms, available_ms, [(*_centre_lonlat(IGN_RC[0] + 1, IGN_RC[1] - 1), True)])[0][0]
        self.late = o.model_copy(update={"obs_id": "obs_synthetic_goes_late_0", "footprint_key": "synthetic_goes:late:0"})

    def collect(self, a_ms: int, b_ms: int):
        obs, scans = self.inner.collect(a_ms, b_ms)
        return (obs + [self.late] if a_ms < self.late.available_sim_ms <= b_ms else obs), scans


def check_delayed_report(ctx, base: Path, root: Path, run: Callable[[Path, Any], Any]) -> None:
    """Observed in cycle 3, available at cycle 6: assimilated at 6; cycles 0-5 identical to the run without it."""
    ks = physics_ks(ctx)
    src = DelayedSource(synth_source(ctx), ks[3] - 2 * MIN, ks[6])
    run(root, src)
    late = src.late.obs_id
    for k in ks[:6]:
        assert _key(root, k) == _key(base, k) and _arrival(root, k) == _arrival(base, k), k
        assert late not in {o.obs_id for o in bundle(root, k).observations}
    seen = {o.obs_id: o for o in bundle(root, ks[6]).observations}
    assert seen[late].status == "assimilated" and seen[late].observed_sim_ms < ks[4]
    assert _key(root, ks[6]) != _key(base, ks[6])  # the late evidence is in the chain from its availability on


def test_delayed_report_lands_in_its_availability_cycle(ctx, tmp_path):
    run_precompute(ctx, tmp_path / "base")
    check_delayed_report(ctx, tmp_path / "base", tmp_path / "late", lambda root, src: run_precompute(ctx, root, src))


def test_live_loop_survives_a_physics_failure(ctx, tmp_path, monkeypatch):
    ks = physics_ks(ctx)
    base = tmp_path / "base"
    run_precompute(ctx, base)
    real, failed = hazard_standin.FakeHazardModel.step, []

    def flaky(self, t_now_min, new_obs):  # fails once at ks[3], like a Rust timeout (nothing committed)
        if t_now_min == (ks[3] - ctx.t_ref_ms) / MIN and not failed:
            failed.append(t_now_min)
            raise TimeoutError("Rust forecast exceeded 120.0 seconds; model state was not committed")
        return real(self, t_now_min, new_obs)

    monkeypatch.setattr(hazard_standin.FakeHazardModel, "step", flaky)
    with pytest.raises(TimeoutError):
        run_precompute(ctx, tmp_path / "pre")  # precompute stays fail-fast
    failed.clear()
    root = tmp_path / "live"
    run_loop(ctx, root, survive=True)
    assert failed
    b = bundle(root, ks[3])
    assert b.cycle is None and [e.level for e in b.events if e.actor == "physics"] == ["error"]
    assert not (cache.cycle_dir(root, SCN, "main", ks[3]) / "physics.json").exists()
    for k in ks[4:]:  # k4 rolls through k3 again; the chain is the uninterrupted run's
        assert bundle(root, k).cycle is not None
        assert _key(root, k) == _key(base, k) and _arrival(root, k) == _arrival(base, k), k


def test_roll_forward_replays_inbox_reports(ctx, tmp_path, monkeypatch):
    """A restore that rolls through a cycle with an injected report steps it WITH the report its key hashes."""
    ks = physics_ks(ctx)
    o = synth_source(ctx)._scan(ks[20] - 2 * MIN, ks[20] - MIN, [(*_centre_lonlat(*IGN_RC), True)])[0][0]
    report = o.model_copy(update={"obs_id": "obs_inbox_report_0", "footprint_key": "inbox:0"})
    root = tmp_path / "cache"

    def run() -> Loop:
        inbox = InboxSource()
        inbox.add(report)
        return run_loop(ctx, root, inbox=inbox)

    run()
    shutil.rmtree(cache.cycle_dir(root, SCN, "main", ks[22]))
    real, stepped = hazard_standin.FakeHazardModel.step, []

    def spy(self, t_now_min, new_obs):
        stepped.extend(x.obs_id for x in new_obs)
        return real(self, t_now_min, new_obs)

    monkeypatch.setattr(hazard_standin.FakeHazardModel, "step", spy)
    loop = run()
    assert loop.host.restored_from == [ks[18]]  # rolled through k19-k21
    assert report.obs_id in stepped


def test_cache_key_binds_catalog_and_environment(ctx, tmp_path):
    """The key binds what Abby's checkpoint.load binds, so a catalog/environment change misses the cache."""
    _, identity = rust_backend()
    assert identity["catalog_sha256"] == hashlib.sha256(bridge.catalog_text().encode()).hexdigest()
    assert identity["environment"] == checkpoint.environment()
    host = make_host(ctx, hazard_standin.FakeHazardModel, flat_landscape(ctx), FlatForcing(ctx), tmp_path,
                     identity=identity)
    h = run_hash(ctx, host)
    host.identity = {**identity, "catalog_sha256": "0" * 64}
    assert run_hash(ctx, host) != h


# ---------------------------------------------------------------- 03 §14 test 3


class DoctoredSource:
    """Returns one record that is not yet available (bypasses SyntheticSource's own check)."""

    def __init__(self, inner: SyntheticSource) -> None:
        self.inner = inner

    def collect(self, a_ms: int, b_ms: int):
        obs, scans = self.inner.collect(a_ms, b_ms)
        if obs:
            obs[0] = obs[0].model_copy(update={"available_sim_ms": b_ms + MIN})
        return obs, scans


def test_3_no_future_data(ctx, tmp_path):
    with pytest.raises(AssertionError, match="future data"):
        run_precompute(ctx, tmp_path / "obs", source=DoctoredSource(synth_source(ctx)))
    with pytest.raises(AssertionError, match="future data: weather"):
        run_precompute(ctx, tmp_path / "wx", forcing=FlatForcing(ctx, available_offset_ms=10**9))


# ---------------------------------------------------------------- HRRR forcing, profiles, statics


def test_forcing_takes_released_bands_of_the_newest_init(ctx, tmp_path, monkeypatch):
    (a_init, a_path), (b_init, b_path) = write_hrrr(ctx, tmp_path / "forcing")
    forcing = NcForcing(tmp_path / "forcing", model_grid(ctx))
    k0 = ctx.k0_ms
    w = forcing.weather_as_of(k0)  # B has nothing out yet: fall back to A
    assert (w.audit["init_ms"], w.audit["selected_fxx"], w.audit["file"]) == (a_init, list(range(10)), a_path.name)
    assert w.audit["sha256"] == sha256_file(a_path)
    k = k0 + 40 * MIN
    w = forcing.weather_as_of(k)  # B f00-f02 out; f03+ not yet
    assert (w.audit["init_ms"], w.audit["selected_fxx"], w.audit["file"]) == (b_init, [0, 1, 2], b_path.name)
    assert max(w.audit["available_ms_by_fxx"]) == w.available_ms <= k  # available after the issue: never
    assert w.audit["valid_min_range"][1] > (k - ctx.t_ref_ms) / MIN  # valid after the issue: a forecast, legal
    assert w.weather.u10.shape == (3, G["ny"], G["nx"])

    real = loop_mod.weather_from_hrrr

    def doctored(ds, grid, issue_ms):
        weather, audit = real(ds, grid, issue_ms)
        return weather, {**audit, "available_ms_by_fxx": [issue_ms + MIN]}

    monkeypatch.setattr(loop_mod, "weather_from_hrrr", doctored)
    with pytest.raises(AssertionError, match="future data: weather"):
        NcForcing(tmp_path / "forcing", model_grid(ctx)).weather_as_of(k)


def test_hrrr_forcing_audit_cache_and_restore_across_a_weather_change(ctx, tmp_path, monkeypatch):
    """The orchestrator on real forcing files: audit in physics.json, weather updates, byte-identical restore."""
    write_hrrr(ctx, ctx.scn_dir / "forcing")
    root = tmp_path / "cache"

    def run():
        return run_precompute(ctx, root, forcing=NcForcing(ctx.scn_dir / "forcing", model_grid(ctx)))

    run()
    ks = physics_ks(ctx)
    phys = {k: json.loads((cache.cycle_dir(root, SCN, "main", k) / "physics.json").read_text(encoding="utf-8"))
            for k in ks[:10]}
    files = [phys[k]["forcing"]["file"] for k in ks[:10]]
    assert files[0] == files[1] != files[2] == files[9]  # B's f00 is out from k0+10 min
    assert [phys[k]["forcing"]["selected_fxx"] for k in ks[7:9]] == [[0], [0, 1, 2]]
    assert all(max(p["forcing"]["available_ms_by_fxx"]) <= k for k, p in phys.items())

    before = _arrival(root, ks[9])
    shutil.rmtree(cache.cycle_dir(root, SCN, "main", ks[9]))
    sent: list = []
    original = hazard_standin.FakeHazardModel.update_weather

    def spy(self, w):
        sent.append(len(w.t_min))
        original(self, w)

    monkeypatch.setattr(hazard_standin.FakeHazardModel, "update_weather", spy)
    loop = run()
    assert loop.host.restored_from == [ks[6]]  # checkpoint at k6 (B f00), rolled through k7, k8
    assert sent == [3]  # only the real change at k8 (B f00 -> f00-f02); the checkpoint's weather is not re-sent
    assert _arrival(root, ks[9]) == before


def test_members_profiles_and_spotting_off(ctx):
    ctx.cfg["n_members"] = 128
    dev, target = model_config(ctx, None, "dev"), model_config(ctx, None, "target")
    assert (dev.n_members, target.n_members) == (16, 128)
    assert not dev.spotting and not target.spotting
    assert model_version("target") == MODEL_VERSION and model_version("dev") == MODEL_VERSION + "+dev-n16"


def test_real_physics_stops_without_abby_statics(ctx):
    with pytest.raises(SystemExit, match=r"missing static/dem\.tif, static/fbfm40\.tif"):
        landscape(ctx)


def test_fake_model_keeps_the_real_hazard_protocol():
    for name in ("init", "update_weather", "step", "region_arrivals", "save_state", "load_state"):
        want = list(inspect.signature(getattr(HazardModel, name)).parameters)
        assert list(inspect.signature(getattr(hazard_standin.FakeHazardModel, name)).parameters) == want, name


# ---------------------------------------------------------------- 03 §14 test 4 (publisher level)


def _phys(ctx, k: int, z3_arrival_ms: int) -> cache.PhysicsOutput:
    n = 4
    zone_arr = np.full((n, len(ctx.zones)), np.inf, dtype=np.float32)
    zone_arr[:, 2] = (z3_arrival_ms - ctx.t_ref_ms) / MIN
    cycle = {
        "cycle_id": f"cyc_{SCN}_main_{k}", "scenario_id": SCN, "branch_id": "main", "knowledge_sim_ms": k,
        "data_through_sim_ms": k - 3 * MIN, "created_wall_ms": 0, "filter_mode": "none", "n_members": n, "ess": 4.0,
        "tempering_alpha": 1.0, "resampled": False, "n_obs_by_kind": {}, "horizon_min": 360, "step_min": 30,
        "assets": {"arrival_q": "a", "perimeter": "p", "isochrones": "i"}, "danger": {}, "burned_ha": 1.0,
        "front_km": 1.0, "growth_ha_per_h": 0.0, "head_bearing_deg": None, "params_summary": {},
        "model_version": "t", "config_hash": None, "duration_ms": 1,
    }
    return cache.PhysicsOutput(cycle=cycle, zone_arr=zone_arr, area_arr=np.zeros((n, 1), dtype=np.float32),
                               weights=np.full(n, 1 / n), obs_status=[], scans=[], events=[], key="k")


def test_4_publisher_proposes_once_and_supersedes(ctx, tmp_path):
    k1 = ctx.k0_ms + 10 * 5 * MIN
    arrival = k1 + 100 * MIN  # R1 (P by +2h = 1) at k1; R2 (P by +1h = 1) from k1 + 45 min
    snap, mirror, bundles = HubSnapshot(zones={}), {}, []
    for k in [k1 + i * 5 * MIN for i in range(12)]:
        inp = CycleInputs(k_ms=k, phys=_phys(ctx, k, arrival), new_obs=[], scans=[], scans_so_far=[], prev_p120=None)
        b = build_bundle(ctx, "main", inp, snap, list(mirror.values()))
        apply_alerts(mirror, b.alerts)
        bundles.append(b)
    proposed = [(i, a.rule_id) for i, b in enumerate(bundles) for a in b.alerts if a.state == "proposed"]
    assert proposed == [(0, "R1_WARNING"), (8, "R2_ORDER")]  # R2: P by +1h and act-by within 15 min, at +40 min
    (r1,) = [a for a in bundles[8].alerts if a.state == "superseded"]
    assert r1.alert_id == bundles[0].alerts[0].alert_id
    assert mirror[r1.alert_id].state == "superseded"
    assert {r.zone_id: r.status for r in bundles[8].zone_risk}["SYN-Z3"] == "order"
    assert any("crossed R2" in e.message for e in bundles[8].events)
    assert "school" in bundles[0].alerts[0].text and "Synthetic" not in bundles[0].alerts[0].text  # types, never names

    # An approved R1 is never superseded by the publisher.
    approved = {a.alert_id: a.model_copy(update={"state": "approved", "decided_by": "Tester"})
                for a in bundles[0].alerts}
    inp = CycleInputs(k_ms=k1 + 40 * MIN, phys=_phys(ctx, k1 + 40 * MIN, arrival), new_obs=[], scans=[],
                      scans_so_far=[], prev_p120=None)
    b = build_bundle(ctx, "main", inp, snap, list(approved.values()))
    assert [a.state for a in b.alerts] == ["proposed"]

    # A cached briefing (same input_hash as precompute) is reused, with Claude's text on the alert.
    first = bundles[0]
    inp0 = publisher.briefing_input(ctx, first.alerts[0], first.zone_risk, first.cycle, first.sensors)
    brief = {"briefing_id": "brf_x", "scenario_id": SCN, "branch_id": "main", "cycle_id": "c", "alert_id": "x",
             "model": "test", "prompt_version": "t", "input_hash": brf.input_hash(inp0), "headline": "H",
             "bullets": [], "text": "Claude text", "voice_script": "Claude voice", "grounded": True,
             "unmatched_tokens": [], "fallback_used": False, "latency_ms": 1, "created_wall_ms": 0}
    cache.save_briefing(tmp_path, SCN, brf.input_hash(inp0), brief)
    inp = CycleInputs(k_ms=k1, phys=_phys(ctx, k1, arrival), new_obs=[], scans=[], scans_so_far=[], prev_p120=None)
    b = build_bundle(ctx, "main", inp, snap, [], briefing_root=tmp_path)
    (alert,) = b.alerts
    assert alert.draft_source == "claude" and alert.text == "Claude text"
    assert b.briefings[0].alert_id == alert.alert_id and b.briefings[0].briefing_id == f"brf_{alert.alert_id}"


# ---------------------------------------------------------------- 03 §14 test 5


def test_5_evac_edit_survives_cached_replay(ctx, tmp_path):
    root = tmp_path / "cache"
    run_precompute(ctx, root)
    cached = {CycleBundle.model_validate_json(p.read_text(encoding="utf-8")).knowledge_sim_ms:
              CycleBundle.model_validate_json(p.read_text(encoding="utf-8"))
              for p in (root / SCN / "main").glob("*/bundle.json")}
    hub = FakeHub(ctx)
    k_a = ctx.start_ms + 60 * MIN
    after = [k_a + i * 5 * MIN for i in (1, 2, 3)]

    async def body(eng: Engine) -> None:
        hub.seek(k_a)
        await eng.until(lambda: f"bnd_{SCN}_main_{k_a}" in hub.published)
        n = len(hub.published)
        hub.set_evac("SYN-Z3", 100)
        await eng.until(lambda: len(hub.published) == n + 1)  # republish of the current cycle
        assert hub.published[-1] == f"bnd_{SCN}_main_{k_a}"
        hub.seek(after[-1])
        await eng.until(lambda: all(f"bnd_{SCN}_main_{k}" in hub.published for k in after))

    with_engine(ctx, hub, root, body)
    last = {b.knowledge_sim_ms: b for b in hub.bundles}
    for k in [k_a, *after]:
        z = {r.zone_id: r for r in last[k].zone_risk}["SYN-Z3"]
        z0 = {r.zone_id: r for r in cached[k].zone_risk}["SYN-Z3"]
        assert z.evac_min == 100 and z0.evac_min == 45
        assert z0.act_by_sim_ms is not None and z.act_by_sim_ms == z0.act_by_sim_ms - 55 * MIN
        assert z.act_by_basis == "q30 arrival − 100 min evac"


# ---------------------------------------------------------------- poller and file-mode extras


def test_poller_offline_call_and_report(ctx, tmp_path):
    root = tmp_path / "cache"
    run_precompute(ctx, root)
    hub = FakeHub(ctx)

    async def body(eng: Engine) -> None:
        hub.seek(ctx.start_ms + 150 * MIN)
        await eng.until(lambda: any(a.state == "proposed" for a in hub.alerts.values()))
        await eng.settle()
        aid = next(a.alert_id for a in hub.alerts.values() if a.state == "proposed")
        hub.alerts[aid] = hub.alerts[aid].model_copy(update={"state": "approved", "decided_by": "Tester"})
        hub._command("alert_approved", {"alert_id": aid, "operator": "Tester"})
        hub._command("report", {"report_id": "rep_test0001"})
        await eng.until(lambda: len(eng.poller.handled) >= 3)
        assert hub.calls == [(aid, "failed", "offline")]
        report = next(c for c in hub.commands if c["kind"] == "report")
        assert report["status"] == "failed" and "wave 3" in report["result"]["reason"]

    with_engine(ctx, hub, root, body)


def test_bundle_replay_of_committed_fixture():
    """`run --fixtures` path: the committed fixture bundles publish as-is, each once, in order."""
    from ember.run import FIXTURES, fixture_scenario
    from ember.runtime.loop import BundleReplayLoop

    fx = fixture_scenario(FIXTURES / "static" / "fixture")
    hub = FakeHub(fx)

    async def main() -> None:
        clock = SimClock()
        loop = BundleReplayLoop(fx, hub, clock, None, None, FIXTURES / "cache")
        poller = Poller(hub, clock, loop, "offline")
        await poller.tick()
        task = asyncio.create_task(loop.run())
        try:
            hub.seek(fx.end_ms)
            t_end = time.monotonic() + 20
            while len(hub.published) < len(loop.ks):
                assert time.monotonic() < t_end
                await poller.tick()
                await asyncio.sleep(0.02)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert hub.published == [f"bnd_fixture_main_{k}" for k in loop.ks]

    asyncio.run(main())


def test_scan_events_carry_sensor_status(ctx, tmp_path):
    loop = run_precompute(ctx, tmp_path / "cache")
    k1 = ctx.k0_ms + 5 * MIN  # the first scan that sees the fire
    b = CycleBundle.model_validate_json(
        (cache.cycle_dir(tmp_path / "cache", SCN, "main", k1) / "bundle.json").read_text(encoding="utf-8"))
    assert [s.sensor_id for s in b.sensors] == ["synthetic_goes"] and b.sensors[0].status == "ok"
    assert all(o.hot for o in b.observations) and len(b.observations) == 2  # clear pixels never published
    assert all(o.status == "assimilated" and o.cycle_id == b.cycle.cycle_id for o in b.observations)
    assert [e.event_id for e in b.events] == [f"ev_{b.cycle.cycle_id}_{i}" for i in range(len(b.events))]
    assert isinstance(b.scan_events[0], ScanEvent) and loop.status["cache_coverage"] == 1.0


def test_publication_failure_retries_exact_batch_without_advancing_or_rebuilding(ctx, tmp_path, monkeypatch):
    from ember.sync.sink import PublicationUnavailable
    sink = FakeHub(ctx)
    eng = Engine(ctx, sink, tmp_path / "cache")
    calls, built = [], []
    original_build = eng.loop._build
    original_publish = sink.publish_many
    async def build(k, snap, mirror):
        built.append(k)
        return await original_build(k, snap, mirror)
    async def publish(bs):
        calls.append([b.model_dump_json() for b in bs])
        if len(calls) == 1:
            raise PublicationUnavailable("simulated disconnected hub")
        await original_publish(bs)
    async def no_delay(_seconds):
        pass
    monkeypatch.setattr(eng.loop, "_build", build)
    monkeypatch.setattr(sink, "publish_many", publish)
    monkeypatch.setattr(loop_mod.asyncio, "sleep", no_delay)
    async def run():
        batch = eng.loop.ks[:2]
        await eng.loop._publish_batch(batch)
        assert eng.loop.i == 0 and eng.loop.status["publication_pending"] == 2
        await eng.loop._publish_batch(batch)
        assert eng.loop.i == 2 and eng.loop.status["publication_pending"] == 0
        assert built == batch
        assert calls[0] == calls[1]
    asyncio.run(run())


def test_file_index_keeps_previous_bundle_bytes_immutable(tmp_path):
    import json
    path = Path(__file__).resolve().parents[2] / "fixtures/cache/fixture/main/index.json"
    entry = json.loads(path.read_text())["cycles"][0]
    old = CycleBundle.model_validate_json((Path(__file__).resolve().parents[2] / "fixtures" / entry["bundle"]).read_text())
    root = tmp_path / "cache"
    sink = CacheSink(root)
    async def run():
        await sink.publish(old)
        index = root / "fixture/main/index.json"
        before = json.loads(index.read_text())["cycles"][0]["bundle"]
        original = (root.parent / before).read_bytes()
        await sink.publish(old.model_copy(update={"bundle_id": old.bundle_id + "_revision"}))
        after = json.loads(index.read_text())["cycles"][0]["bundle"]
        assert before != after
        assert (root.parent / before).read_bytes() == original
    asyncio.run(run())
