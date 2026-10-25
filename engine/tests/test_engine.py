"""Engine M1 acceptance tests, spec/03 §14 tests 1–5 (WAVE2 T10).

SyntheticSource + the FakeHazardModel stand-in + an in-memory fake hub, over a flat
synthetic scenario written to tmp_path. Test 1 also has a hub-gated variant on scenario
"synth" (EMBERLINE_HUB_TESTS=1, local `spacetime start`).
"""

from __future__ import annotations

import asyncio
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
from rasterio.transform import Affine

hazard_standin.install()

from emberline.ai import briefing as brf
from emberline.contracts import ClockState, CycleBundle, ScanEvent
from emberline.data import grid as gridlib
from emberline.runtime import cache, publisher
from emberline.runtime.clock import SimClock, sim_now_ms
from emberline.runtime.loop import Loop, WeatherAsOf, load_scenario_dir
from emberline.runtime.model_host import flat_landscape
from emberline.runtime.publisher import CycleInputs, build_bundle
from emberline.runtime.sources import SyntheticSource
from emberline.scripts.precompute import make_host, precompute
from emberline.sync.poller import Poller
from emberline.sync.sink import HubSnapshot, apply_alerts

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


def write_scenario(root: Path) -> Path:
    d = root / SCN
    (d / "web").mkdir(parents=True)
    ign_lon, ign_lat = _centre_lonlat(*IGN_RC)
    t0 = int(np.datetime64(T0.rstrip("Z"), "ms").astype(np.int64))
    t_ign, end = t0 + 15 * MIN, t0 + 15 * MIN + (N_PHYS - 1) * 5 * MIN
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
        api = hazard_standin.install()
        u, v = 10 * np.sin(np.radians(HEAD_DEG)), 10 * np.cos(np.radians(HEAD_DEG))
        shape = (1, G["ny"], G["nx"])
        self.w = api.Weather(t_min=np.array([0.0], dtype=np.float32), u10=np.full(shape, u, dtype=np.float32),
                             v10=np.full(shape, v, dtype=np.float32), temp_c=np.array([15.0], dtype=np.float32),
                             rh=np.array([0.1], dtype=np.float32))
        self.init_ms, self.avail = ctx.t_ref_ms, ctx.start_ms + available_offset_ms

    def weather_as_of(self, t_ms: int) -> WeatherAsOf:
        return WeatherAsOf(weather=self.w, init_ms=self.init_ms, available_ms=self.avail)


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


@pytest.mark.skipif(not os.environ.get("EMBERLINE_HUB_TESTS"), reason="hub")
def test_1_hub_variant(ctx, tmp_path):
    """Replay at 900x into the local hub (scenario synth), then reset, seek +3 h, seek back to +1 h."""
    from emberline.run import load_payload
    from emberline.sync.stdb_http import StdbHttp
    from emberline.sync.stdb_sink import StdbSink

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
        hub = StdbHttp(os.environ.get("STDB_URI", "http://localhost:3000"), os.environ.get("STDB_DB", "emberline"),
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
    return (cache.cycle_dir(root, SCN, "main", k) / "arrival_q.bin").read_bytes()


def _key(root: Path, k: int) -> str:
    return json.loads((cache.cycle_dir(root, SCN, "main", k) / "physics.json").read_text(encoding="utf-8"))["key"]


def test_2_determinism_and_cache_miss_after_hits(ctx, tmp_path):
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
    loop = run_precompute(ctx, a)
    assert loop.host.restored_from == [ks[24]]  # checkpoint 24, then exactly one step
    assert _arrival(a, k25) == before
    assert _key(a, k25) == _key(b, k25)


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
    from emberline.run import FIXTURES, fixture_scenario
    from emberline.runtime.loop import BundleReplayLoop

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
