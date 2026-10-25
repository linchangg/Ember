"""Physics cycle loop + publish loop. spec/03 §4, §7 (seek/catch-up), §10.

Cycles are `k = replay_start + n·cycle_min`. Before ignition a cycle has no physics and
publishes a pre-ignition bundle (`cycle = None`). From the first k >= t_ignition the
chained cache key decides: a hit loads `physics.json` + `zone_arrivals.npz`, a miss
brings the model to k−5 (`ModelHost.ensure_state`) and steps it. Risk, alerts and
people are re-derived from live hub state at publish time (`publisher.build_bundle`).
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import xarray as xr
import yaml

from emberline.ai import briefing as brf
from emberline.contracts import LEADS_MIN, ActivityEvent, Alert, CycleBundle, ObservationRecord, ScanEvent, SensorInfo
from emberline.data.config import utc_ms
from emberline.risk import alerts as alerts_mod
from emberline.risk import risk
from emberline.runtime import cache, publisher, summarize
from emberline.runtime.model_host import ModelHost, burnable
from emberline.runtime.publisher import CycleInputs, build_bundle
from emberline.sync.sink import HubSnapshot, apply_alerts

REPO = Path(__file__).resolve().parents[3]
MAX_BATCH = 10
NEVER = -(2**62)


# ---------------------------------------------------------------- scenario package


@dataclass
class Scenario:
    scenario_id: str
    scn_dir: Path
    cfg: dict
    grid: dict
    t_ref_ms: int
    t_ign_ms: int
    start_ms: int
    end_ms: int
    cycle_ms: int
    zones: list[dict]  # ZoneFeatureProps, in zone_index order
    zone_index: np.ndarray  # (H, W) int32 zone position or -1
    area_index: np.ndarray  # (H, W) int32 0 inside area X, else -1
    sensors: list[SensorInfo]
    rules: dict
    critical: dict[str, list[str]] = field(default_factory=dict)  # zone_id -> readable facility TYPES

    @property
    def zone_names(self) -> dict[str, str]:
        return {z["zone_id"]: z["name"] for z in self.zones}

    @property
    def egress(self) -> dict[str, int]:
        return {z["zone_id"]: z["egress_roads"] for z in self.zones}

    @property
    def sensor_names(self) -> dict[str, str]:
        return {s.sensor_id: s.name for s in self.sensors}

    @property
    def horizon_min(self) -> int:
        return int(self.cfg.get("horizon_min", 360))

    @property
    def act_by_q(self) -> float:
        return float(self.cfg.get("act_by_quantile", 0.3))

    @property
    def web_dir(self) -> Path:
        return self.scn_dir / "web"

    def cycles(self) -> list[int]:
        return list(range(self.start_ms, self.end_ms + 1, self.cycle_ms))

    @property
    def k0_ms(self) -> int:
        """First physics cycle: the first k >= t_ignition."""
        return self.start_ms + max(0, math.ceil((self.t_ign_ms - self.start_ms) / self.cycle_ms)) * self.cycle_ms


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _raster(path: Path, g: dict) -> np.ndarray:
    with rasterio.open(path) as src:
        a = src.read(1).astype(np.int32)
    assert a.shape == (g["ny"], g["nx"]), f"{path.name} is {a.shape}, grid is {(g['ny'], g['nx'])}"
    return a


def critical_types(assets: list[dict]) -> dict[str, list[str]]:
    """Hospital, nursing home, assisted living, school: readable TYPES only, never names."""
    out: dict[str, list[str]] = {}
    for a in assets:
        if a.get("type") in brf.CRITICAL_TYPES and a.get("zone_id"):
            t = a["type"].replace("_", " ")
            if t not in out.setdefault(a["zone_id"], []):
                out[a["zone_id"]].append(t)
    return out


def load_scenario_dir(scn_dir: Path, rules_path: Path = REPO / "config" / "alert_rules.yaml") -> Scenario:
    """scenario.yaml + web/{zones,assets}.geojson + web/sensors.json + static/{zone,area_x}_index.tif.

    zone_index.tif value i means the i-th feature of web/zones.geojson (T9a); area_x_index.tif is non-zero inside.
    """
    scn_dir = Path(scn_dir)
    cfg = yaml.safe_load((scn_dir / "scenario.yaml").read_text(encoding="utf-8"))
    g = cfg["grid"]
    zones = [f["properties"] for f in _read_json(scn_dir / "web" / "zones.geojson")["features"]]
    zone_index = _raster(scn_dir / "static" / "zone_index.tif", g)
    assert zone_index.max() < len(zones), f"zone_index has id {zone_index.max()} but only {len(zones)} zones"
    area = _raster(scn_dir / "static" / "area_x_index.tif", g)
    assets_path = scn_dir / "web" / "assets.geojson"
    assets = [f["properties"] for f in _read_json(assets_path)["features"]] if assets_path.exists() else []
    return Scenario(
        scenario_id=cfg["scenario_id"], scn_dir=scn_dir, cfg=cfg, grid=g,
        t_ref_ms=utc_ms(cfg["t_ref_utc"]), t_ign_ms=utc_ms(cfg["t_ignition_utc"]),
        start_ms=utc_ms(cfg["replay_start_utc"]), end_ms=utc_ms(cfg["replay_end_utc"]),
        cycle_ms=int(cfg.get("cycle_min", 5)) * 60_000, zones=zones, zone_index=zone_index,
        area_index=np.where(area != 0, 0, -1).astype(np.int32),
        sensors=[SensorInfo.model_validate(s) for s in _read_json(scn_dir / "web" / "sensors.json")],
        rules=alerts_mod.load_rules(rules_path), critical=critical_types(assets),
    )


# ---------------------------------------------------------------- forcing (spec/03 §3)


@dataclass
class WeatherAsOf:
    weather: Any  # hazard.api.Weather
    init_ms: int
    available_ms: int


class NcForcing:
    """ForcingProvider over `forcing/hrrr_*.nc` (data/hrrr.py's format).

    weather_as_of(t): the newest init whose f00 is available by t, with only the fxx bands
    already available by t (no future data). Band start = init + fxx hours.
    """

    def __init__(self, forcing_dir: Path, t_ref_ms: int) -> None:
        self.t_ref_ms = t_ref_ms
        self.inits: list[tuple[int, list[int], Path]] = []
        for p in sorted(Path(forcing_dir).glob("hrrr_*.nc")):
            with xr.open_dataset(p) as ds:
                av = [int(a) for a in np.atleast_1d(ds.attrs["available_ms_by_fxx"])]
                self.inits.append((int(ds.attrs["init_ms"]), av, p))
        assert self.inits, f"no hrrr_*.nc in {forcing_dir}"
        self._loaded: dict[Path, xr.Dataset] = {}

    def weather_as_of(self, t_ms: int) -> WeatherAsOf:
        from emberline.hazard.api import Weather  # lazy: A0

        ready = [x for x in self.inits if x[1][0] <= t_ms]
        assert ready, f"no HRRR init available by {t_ms}"
        init_ms, av, path = max(ready, key=lambda x: x[0])
        # Contiguous prefix only: per-fxx availability is not monotone, and Weather.t_min must be hourly.
        n = next((f for f, a in enumerate(av) if a > t_ms), len(av))
        bands = list(range(n))
        if path not in self._loaded:
            self._loaded[path] = xr.load_dataset(path)
        ds = self._loaded[path].isel(fxx=bands)
        w = Weather(
            t_min=np.array([(init_ms + f * 3_600_000 - self.t_ref_ms) / 60_000 for f in bands], dtype=np.float32),
            u10=ds["u10"].values.astype(np.float32), v10=ds["v10"].values.astype(np.float32),
            temp_c=ds["temp_c"].values.astype(np.float32), rh=ds["rh"].values.astype(np.float32),
        )
        return WeatherAsOf(weather=w, init_ms=init_ms, available_ms=max(av[f] for f in bands))


# ---------------------------------------------------------------- loop


@dataclass
class LineageState:
    prev_key: str | None = None
    prev_burned_ha: float | None = None
    prev_p120: dict[str, float] | None = None
    data_through: int | None = None
    scans: list[ScanEvent] = field(default_factory=list)


def assert_no_future(obs: list[ObservationRecord], scans: list[ScanEvent], k_ms: int) -> None:
    for o in obs:
        assert o.available_sim_ms <= k_ms, f"future data: {o.obs_id} available {o.available_sim_ms} > k {k_ms}"
    for s in scans:
        assert s.available_sim_ms <= k_ms, f"future data: {s.scan_id} available {s.available_sim_ms} > k {k_ms}"


def p120(phys: cache.PhysicsOutput, ctx: Scenario) -> dict[str, float]:
    k_min = (phys.cycle["knowledge_sim_ms"] - ctx.t_ref_ms) / 60_000
    return {z["zone_id"]: risk.p_at(risk.zone_probs(phys.zone_arr[:, j], phys.weights, k_min), 120)
            for j, z in enumerate(ctx.zones)}


class Loop:
    """One task: wait for the clock, build bundles for every due cycle (<= 10 per call), publish.

    The poller calls retarget()/request_republish() and holds `lock` around rewinds, so a
    batch is never published across a seek.
    """

    def __init__(self, ctx: Scenario, sink: Any, clock: Any, source: Any, host: ModelHost | None,
                 cache_root: Path, branch: str = "main", model_version: str = "unknown",
                 filter_mode: str | None = None, briefings: str = "off", inbox: Any = None,
                 stop_at_end: bool = False) -> None:
        assert briefings in ("off", "background", "await")
        self.ctx, self.sink, self.clock, self.source, self.host = ctx, sink, clock, source, host
        self.root, self.branch, self.model_version = Path(cache_root), branch, model_version
        self.filter_mode = filter_mode or ctx.cfg.get("filter", "pf")
        self.briefings, self.inbox, self.stop_at_end = briefings, inbox, stop_at_end
        self.lock = asyncio.Lock()
        self.ks = self._cycle_ks()
        self.i = 0  # index into ks of the next cycle to publish
        self.state = LineageState()
        self._last: CycleInputs | None = None
        self._target: int | None = None
        self._republish = False
        self.catching = False
        self.tasks: set[asyncio.Task] = set()
        self.status: dict[str, Any] = {"last_cycle_sim_ms": None, "physics_ms": None,
                                       "cache_coverage": self._coverage()}

    # ------------------------------------------------------------ control (poller)

    def _cycle_ks(self) -> list[int]:
        return self.ctx.cycles()

    @property
    def next_k(self) -> int | None:
        return self.ks[self.i] if self.i < len(self.ks) else None

    def retarget(self, next_k_ms: int) -> None:
        """Continue from the first cycle >= next_k_ms (state is rebuilt silently from cache)."""
        self._target = next_k_ms
        self._wake()

    def request_republish(self) -> None:
        self._republish = True
        self._wake()

    def _wake(self) -> None:
        wake = getattr(self.clock, "wake", None)  # FastClock never waits
        if wake is not None:
            wake()

    def reset(self) -> None:
        if self.inbox is not None:
            self.inbox.clear()
        self.retarget(self.ctx.start_ms)

    # ------------------------------------------------------------ main loop

    async def run(self) -> None:
        try:
            while True:
                async with self.lock:
                    if self._target is not None:
                        target, self._target = self._target, None
                        # A seek back mid catch-up that lands at or past what was published is a no-op:
                        # the cycles up to the target are still due and get published by catch-up.
                        if self.next_k is None or self.next_k > target:
                            self._cancel_briefings(target)
                            await self._replay_silently(target)
                    if self._republish:
                        self._republish = False
                        await self._publish_last()
                    batch = self._due()
                    if batch:
                        await self._publish_batch(batch)
                        continue
                    if self.catching:
                        self.catching = False
                        await self.sink.set_clock_status(self.ctx.scenario_id, "ok")
                if self._target is not None or self._republish:
                    continue  # arrived while we awaited inside the lock; wait_until_sim would clear the wake
                if self.next_k is None and self.stop_at_end:
                    break
                await self.clock.wait_until_sim(self.next_k if self.next_k is not None else 2**62)
        finally:
            self._cancel_briefings(NEVER)  # background briefings die with the loop

    def _cancel_briefings(self, from_k: int) -> None:
        """Cancel background drafts for cycles >= from_k (rewound away; their alerts may be gone)."""
        for t in list(self.tasks):
            if int(t.get_name()) >= from_k:
                t.cancel()

    def _due(self) -> list[int]:
        now = self.clock.sim_now()
        return [k for k in self.ks[self.i : self.i + MAX_BATCH] if k <= now]

    async def _snapshot(self) -> tuple[HubSnapshot, dict[str, Alert]]:
        snap = await self.sink.poll()
        return snap, {a.alert_id: a for a in snap.alerts if a.branch_id == self.branch}

    async def _publish_batch(self, batch: list[int]) -> None:
        nxt = self.ks[self.i + len(batch)] if self.i + len(batch) < len(self.ks) else None
        if not self.catching and (len(batch) > 1 or (nxt is not None and nxt <= self.clock.sim_now())):
            self.catching = True
            await self.sink.set_clock_status(self.ctx.scenario_id, "catching_up")
        snap, mirror = await self._snapshot()
        bundles = []
        for k in batch:
            b = await self._build(k, snap, mirror)
            if b is None:
                continue
            if self.briefings == "await":
                b = await self._with_awaited_briefing(b)
            apply_alerts(mirror, b.alerts)
            bundles.append(b)
        if len(bundles) == 1:
            await self.sink.publish(bundles[0])
        elif bundles:
            await self.sink.publish_many(bundles)
        self.i += len(batch)
        self.status["last_cycle_sim_ms"] = batch[-1]
        if self.briefings == "background":
            for b in bundles:
                self._spawn_briefing(b)

    async def _publish_last(self) -> None:
        snap, mirror = await self._snapshot()
        b = await self._rebuild_last(snap, mirror)
        if b is not None:
            await self.sink.publish(b)

    # ------------------------------------------------------------ one cycle

    def _window(self, k: int) -> tuple[list[ObservationRecord], list[ScanEvent]]:
        a = k - self.ctx.cycle_ms if k > self.ctx.start_ms else NEVER
        obs, scans = self.source.collect(a, k)
        return list(obs), list(scans)

    def _obs_for(self, k: int) -> list[ObservationRecord]:
        # ponytail: roll-forward re-collects the replay source; injected reports (wave 3) need their own log.
        return self._window(k)[0]

    async def _advance(self, k: int) -> CycleInputs:
        """Collect cycle k's new data, then load or compute its physics; updates the lineage state."""
        ctx, st = self.ctx, self.state
        obs, scans = self._window(k)
        if self.inbox is not None and k >= ctx.k0_ms:
            obs += self.inbox.drain(k)
        assert_no_future(obs, scans, k)
        st.scans += scans
        prev_p120 = st.prev_p120
        # ponytail: data_through = newest observed_sim_ms of anything collected so far (clear pixels count: physics uses them).
        seen = [o.observed_sim_ms for o in obs] + ([st.data_through] if st.data_through is not None else [])
        st.data_through = max(seen, default=None)
        phys = None
        if k >= ctx.t_ign_ms:
            key = cache.cycle_key(st.prev_key, [o.obs_id for o in obs], self.model_version,
                                  ctx.cfg.get("config_hash"), int(ctx.cfg.get("seed", 7)), self.filter_mode)
            phys = cache.load_cycle(self.root, ctx.scenario_id, self.branch, k, key)
            if phys is None:
                phys = await asyncio.to_thread(self._physics_cycle, k, obs, scans, key)
            st.prev_key, st.prev_burned_ha = key, phys.cycle["burned_ha"]
            st.prev_p120 = p120(phys, ctx)
        return CycleInputs(k_ms=k, phys=phys, new_obs=obs, scans=scans, scans_so_far=list(st.scans),
                           prev_p120=prev_p120)

    def _physics_cycle(self, k: int, obs: list[ObservationRecord], scans: list[ScanEvent], key: str) -> cache.PhysicsOutput:
        """Cache miss: ensure_state(k−5) then physics_cycle (spec/03 §4)."""
        assert self.host is not None, f"cache miss at {k} and no model (fixture/bundle replay?)"
        ctx, host, st = self.ctx, self.host, self.state
        t0 = time.perf_counter()
        host.ensure_state(self.branch, k - ctx.cycle_ms, self._obs_for)
        arrays = host.step(self.branch, k, obs)
        rel = f"cache/{ctx.scenario_id}/{self.branch}/{k}"
        out_dir = cache.cycle_dir(self.root, ctx.scenario_id, self.branch, k)
        is_new = not (out_dir / "physics.json").exists()  # a stale key is already counted in coverage
        assets, danger, stats = summarize.write_assets(arrays, out_dir, rel, ctx.grid, burnable(host.land),
                                                       st.prev_burned_ha, ctx.cycle_ms // 60_000)
        zone_arr = np.asarray(host.model.region_arrivals(ctx.zone_index, len(ctx.zones)), dtype=np.float32)
        area_arr = np.asarray(host.model.region_arrivals(ctx.area_index, 1), dtype=np.float32)
        used = {u["obs_id"]: u for u in arrays.obs_used}
        obs_status = [
            {"obs_id": o.obs_id, "status": "assimilated" if used[o.obs_id]["used"] else "rejected",
             "influence": float(used[o.obs_id]["ll_spread"]) if used[o.obs_id]["used"] else None}
            for o in obs if publisher.publishable(o) and o.obs_id in used
        ]
        duration_ms = int((time.perf_counter() - t0) * 1000)
        n = len(arrays.weights)
        # spec/02 §1 has FakeHazardModel return "fake"; Cycle.filter_mode is pf|reinit|none (flagged to Abby/Cody).
        fm = arrays.filter_mode if arrays.filter_mode in ("pf", "reinit", "none") else "none"
        by_kind: dict[str, int] = {}
        for o in obs:
            by_kind[o.kind] = by_kind.get(o.kind, 0) + 1
        cycle = {
            "cycle_id": f"cyc_{ctx.scenario_id}_{self.branch}_{k}", "scenario_id": ctx.scenario_id,
            "branch_id": self.branch, "knowledge_sim_ms": k,
            "data_through_sim_ms": st.data_through if st.data_through is not None else ctx.start_ms,
            "created_wall_ms": int(time.time() * 1000), "filter_mode": fm, "n_members": n,
            "ess": float(arrays.ess), "tempering_alpha": float(arrays.tempering_alpha),
            "resampled": bool(arrays.resampled), "n_obs_by_kind": by_kind,
            "horizon_min": ctx.horizon_min, "step_min": int(ctx.cfg.get("step_min", 30)),
            "assets": assets, "danger": danger, **stats, "params_summary": arrays.params_summary,
            "model_version": self.model_version, "config_hash": ctx.cfg.get("config_hash"),
            "duration_ms": duration_ms,
        }
        if fm == "none":
            filt = "Filter: none (open loop)"
        else:
            filt = f"Filter: ESS {arrays.ess:.0f}/{n} (α {arrays.tempering_alpha:.2f})" + (
                ", resampled" if arrays.resampled else "")
        events = [
            {"stage": "filter", "actor": "physics", "message": filt},
            {"stage": "forecast", "actor": "physics", "duration_ms": duration_ms,
             "message": f"Forecast run {duration_ms / 1000:.1f} s → {len(LEADS_MIN)} leads"},
        ]
        out = cache.PhysicsOutput(cycle=cycle, zone_arr=zone_arr, area_arr=area_arr,
                                  weights=np.asarray(arrays.weights, dtype=np.float64), obs_status=obs_status,
                                  scans=[s.model_dump(mode="json") for s in scans], events=events, key=key)
        cache.save_cycle(self.root, ctx.scenario_id, self.branch, k, out)
        self.status["physics_ms"] = duration_ms
        self._n_cached += is_new
        self.status["cache_coverage"] = round(self._n_cached / max(1, len(self._physics_ks())), 4)
        return out

    def _physics_ks(self) -> list[int]:
        return [k for k in self.ks if k >= self.ctx.t_ign_ms]

    def _coverage(self) -> float:
        d = self.root / self.ctx.scenario_id / self.branch
        self._n_cached = sum(1 for k in self._physics_ks() if (d / str(k) / "physics.json").exists())
        return round(self._n_cached / max(1, len(self._physics_ks())), 4)

    async def _build(self, k: int, snap: HubSnapshot, mirror: dict[str, Alert]) -> CycleBundle | None:
        inp = await self._advance(k)
        self._last = inp
        return build_bundle(self.ctx, self.branch, inp, snap, list(mirror.values()), self.root)

    async def _rebuild_last(self, snap: HubSnapshot, mirror: dict[str, Alert]) -> CycleBundle | None:
        """The current cycle again with fresh hub state (evac_min edit, person joined)."""
        if self._last is None:
            return None
        return build_bundle(self.ctx, self.branch, self._last, snap, list(mirror.values()), self.root)

    async def _replay_silently(self, target_ms: int) -> None:
        """Rebuild the lineage state up to the first cycle >= target without publishing (rewind/reset)."""
        self.state, self._last = LineageState(), None
        self.i = 0
        while self.i < len(self.ks) and self.ks[self.i] < target_ms:
            self._last = await self._advance(self.ks[self.i])
            self.i += 1

    # ------------------------------------------------------------ briefings (spec/05 §1)

    def _briefing_request(self, b: CycleBundle) -> tuple[dict, Alert] | None:
        top = publisher.top_alert(b.alerts)
        if top is None or b.cycle is None or b.briefings or top.draft_source == "claude":
            return None
        return publisher.briefing_input(self.ctx, top, b.zone_risk, b.cycle, b.sensors), top

    async def _draft(self, inp: dict, alert: Alert, b: CycleBundle):
        out = await brf.draft(inp, alert, b.cycle)
        if out.grounded and not out.fallback_used:
            cache.save_briefing(self.root, self.ctx.scenario_id, out.input_hash, out.model_dump(mode="json"))
        return out

    async def _with_awaited_briefing(self, b: CycleBundle) -> CycleBundle:
        """Precompute: await Claude for the cycle's top new alert and bake the result into the bundle."""
        req = self._briefing_request(b)
        if req is None:
            return b
        out = await self._draft(*req, b)
        alerts = [publisher.with_briefing(a, out) if a.alert_id == req[1].alert_id else a for a in b.alerts]
        return b.model_copy(update={"alerts": alerts, "briefings": [out]})

    def _spawn_briefing(self, b: CycleBundle) -> None:
        req = self._briefing_request(b)
        if req is None:
            return
        task = asyncio.create_task(self._live_briefing(*req, b), name=str(b.knowledge_sim_ms))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _live_briefing(self, inp: dict, alert: Alert, b: CycleBundle) -> None:
        """Online only: draft in the background, then upsert_alert_draft (the hub keeps it only while proposed)."""
        out = await self._draft(inp, alert, b)
        ok = out.grounded and not out.fallback_used
        text, voice = (out.text, out.voice_script) if ok else (alert.text, alert.voice_script)
        await self.sink.upsert_alert_draft(alert.alert_id, text, voice, "claude" if ok else "template", out)
        msg = "Claude drafted briefing (validated ✓)" if ok else "Claude draft rejected → template"
        await self.sink.append_events([ActivityEvent(
            event_id=f"ev_{b.cycle.cycle_id}_brf", scenario_id=self.ctx.scenario_id, branch_id=self.branch,
            sim_ms=b.knowledge_sim_ms, wall_ms=int(time.time() * 1000), stage="briefing", actor="claude",
            level="info" if ok else "warn", message=msg, ref_kind="alert", ref_id=alert.alert_id,
            duration_ms=out.latency_ms,
        )])


class BundleReplayLoop(Loop):
    """`run --fixtures`: publish committed bundle.json files as they are; no physics, no re-derived risk.

    People still get their zone's values. Evac edits do not change these bundles.
    """

    def _cycle_ks(self) -> list[int]:
        index = _read_json(self.root / self.ctx.scenario_id / self.branch / "index.json")
        self.paths = {c["knowledge_sim_ms"]: self.root.parent / c["bundle"] for c in index["cycles"]}
        return sorted(self.paths)

    def _coverage(self) -> float:
        self._n_cached = len(self.paths)
        return 1.0

    def _load(self, k: int, snap: HubSnapshot) -> CycleBundle:
        b = CycleBundle.model_validate_json(self.paths[k].read_text(encoding="utf-8"))
        wall = int(time.time() * 1000)
        return b.model_copy(update={"people": risk.people(snap.people, b.zone_risk),
                                    "events": [e.model_copy(update={"wall_ms": wall}) for e in b.events]})

    async def _build(self, k: int, snap: HubSnapshot, mirror: dict[str, Alert]) -> CycleBundle | None:
        self._last_k = k
        return self._load(k, snap)

    async def _rebuild_last(self, snap: HubSnapshot, mirror: dict[str, Alert]) -> CycleBundle | None:
        k = getattr(self, "_last_k", None)
        return None if k is None else self._load(k, snap)

    async def _replay_silently(self, target_ms: int) -> None:
        self.i = sum(1 for k in self.ks if k < target_ms)
        self._last_k = self.ks[self.i - 1] if self.i > 0 else None
