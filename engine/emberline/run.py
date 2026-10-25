"""Engine entrypoint. spec/03 §1, §13.

    uv run python -m emberline.run --scenario eaton --profile online [--members-profile dev|target]
    uv run python -m emberline.run --profile offline --fixtures      # replay the committed fixture, no physics

Loads engine/.env, calls load_scenario on the hub, then runs the poller, the cycle loop
and the local API (:8787, T12) until interrupted. Physics (cache misses) is Abby's Rust
HazardModel on her statics; without static/dem.tif + static/fbfm40.tif + data.statics
this exits naming what is missing, unless --fixtures is given. `--members-profile` must
match the precompute's `--profile` for its cache to hit (default target).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

from emberline.ai.briefing import load_env
from emberline.contracts import SensorInfo
from emberline.data.config import scenario_dir
from emberline.risk import alerts as alerts_mod
from emberline.runtime import cache
from emberline.runtime.clock import SimClock
from emberline.runtime.loop import BundleReplayLoop, Loop, NcForcing, Scenario, load_scenario_dir
from emberline.runtime.model_host import PROFILES, landscape, make_host, model_grid, model_version, rust_backend
from emberline.sync.poller import Poller
from emberline.sync.stdb_http import StdbHttp
from emberline.sync.stdb_sink import StdbSink

log = logging.getLogger("emberline.run")
REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
LOCAL_HUB = "http://localhost:3000"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_payload(static_dir: Path) -> dict[str, Any]:
    """load_scenario payload (04 §2.2) from a web/static dir; same shape as tests/test_hub.py's load_payload."""
    m = _json(static_dir / "manifest.json")
    zones = _json(static_dir / "zones.geojson")["features"]
    spawns = _json(static_dir / "spawn_points.json") if (static_dir / "spawn_points.json").exists() else {}
    return {
        "scenario_id": m["scenario_id"], "title": m["title"], "tz": m["tz"], "config_hash": m["config_hash"],
        "manifest": m, "replay_start_sim_ms": m["replay_start_sim_ms"], "replay_end_sim_ms": m["replay_end_sim_ms"],
        "zones": [{**f["properties"], "evac_min": f["properties"]["evac_min_default"]} for f in zones],
        "sensors": _json(static_dir / "sensors.json"),
        "spawn_points": [{**sp, "join_code": code} for code, lst in spawns.items() for sp in lst],
    }


def fixture_scenario(static_dir: Path) -> Scenario:
    """Just enough Scenario for BundleReplayLoop (no rasters: bundles are published as committed)."""
    m = _json(static_dir / "manifest.json")
    empty = np.zeros((0, 0), dtype=np.int32)
    return Scenario(
        scenario_id=m["scenario_id"], scn_dir=static_dir, cfg={"filter": "none"}, grid=m["grid"],
        t_ref_ms=m["replay_start_sim_ms"], t_ign_ms=m["t_ignition_sim_ms"], start_ms=m["replay_start_sim_ms"],
        end_ms=m["replay_end_sim_ms"], cycle_ms=300_000,
        zones=[f["properties"] for f in _json(static_dir / "zones.geojson")["features"]],
        zone_index=empty, area_index=empty,
        sensors=[SensorInfo.model_validate(s) for s in _json(static_dir / "sensors.json")],
        rules=alerts_mod.load_rules(),
    )


def build_loop(a: argparse.Namespace, sink: StdbSink, clock: SimClock) -> tuple[Loop, Path]:
    if a.fixtures:
        static_dir = FIXTURES / "static" / a.scenario
        if not static_dir.is_dir():
            raise SystemExit(f"--fixtures: no {static_dir} (the committed fixture is --scenario fixture)")
        return BundleReplayLoop(fixture_scenario(static_dir), sink, clock, None, None, FIXTURES / "cache"), static_dir

    from emberline.runtime.sources import InboxSource, ReplaySource

    scn_dir = scenario_dir(a.scenario)
    ctx = load_scenario_dir(scn_dir)
    land = landscape(ctx)  # stops naming Abby's missing statics; never a fabricated landscape
    make, identity = rust_backend()
    host = make_host(ctx, make, land, NcForcing(scn_dir / "forcing", model_grid(ctx)), cache.DEFAULT_ROOT,
                     members_profile=a.members_profile, identity=identity)
    loop = Loop(ctx, sink, clock, ReplaySource(scn_dir, ctx.scenario_id, ctx.cfg["assimilate_sensors"],
                                                 reports=bool(ctx.cfg.get("use_documented_reports"))), host,
                cache.DEFAULT_ROOT, "main", model_version(a.members_profile),
                briefings="background" if a.profile == "online" else "off", inbox=InboxSource(),
                survive_physics_errors=True)
    return loop, ctx.web_dir


def api_server(a: argparse.Namespace, loop: Loop, hub: StdbHttp) -> tuple[Any, Any]:
    """T12's app on 127.0.0.1:ASSET_PORT, or (None, None) if api/local.py has not landed."""
    try:
        import uvicorn

        from emberline.api.local import LocalState, create_app
    except ImportError as e:
        log.warning("local API unavailable (%s); serving nothing on :%s", e, a.port)
        return None, None
    state = LocalState(scenario_id=loop.ctx.scenario_id, profile=a.profile, fixtures=a.fixtures,
                       seek_presets={"replay_start": loop.ctx.start_ms, "ignition": loop.ctx.t_ign_ms})
    server = uvicorn.Server(uvicorn.Config(create_app(state, hub), host="127.0.0.1", port=a.port, log_level="warning"))
    return state, server


async def main_async(a: argparse.Namespace) -> None:
    uri = os.environ.get("STDB_URI", LOCAL_HUB) if a.profile == "online" else LOCAL_HUB
    hub = StdbHttp(uri, os.environ.get("STDB_DB", "emberline"), os.environ.get("STDB_TOKEN"))
    clock = SimClock()
    sink = StdbSink(hub, a.scenario, sim_now=clock.sim_now)
    loop, static_dir = build_loop(a, sink, clock)
    sink.scenario_id = loop.ctx.scenario_id
    state, server = api_server(a, loop, hub)
    serving = asyncio.create_task(server.serve()) if server is not None else None  # files even if the hub is down
    await sink.load_scenario(load_payload(static_dir))
    poller = Poller(sink, clock, loop, a.profile, state)
    log.info("engine up: %s (%s%s) hub %s, %d cycles", loop.ctx.scenario_id, a.profile,
             ", fixtures" if a.fixtures else "", uri, len(loop.ks))
    try:
        # The loop waits (sim_now unknown) until the poller's first tick delivers the hub clock.
        await asyncio.gather(poller.run(), loop.run(), *([serving] if serving else []))
    finally:
        await hub.aclose()


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description="Emberline engine")
    ap.add_argument("--scenario", default=os.environ.get("SCENARIO_ID", "eaton"))
    ap.add_argument("--profile", choices=["online", "offline"], default=os.environ.get("EMBERLINE_PROFILE", "online"))
    ap.add_argument("--fixtures", action="store_true", help="replay fixtures/cache bundles as committed (no physics)")
    ap.add_argument("--members-profile", choices=PROFILES, default="target",
                    help="target = scenario.yaml n_members (reported run); dev = 16 members, labelled +dev-n16")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ASSET_PORT", "8787")))
    a = ap.parse_args()
    if a.fixtures and a.scenario == "eaton":
        a.scenario = "fixture"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main_async(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
