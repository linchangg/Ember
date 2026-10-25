"""Precompute a branch's physics cache (+ bundles for file mode). spec/03 §5.

    uv run python -m emberline.scripts.precompute --scenario eaton --branch main --filter pf
    uv run python -m emberline.scripts.precompute --scenario eaton --branch openloop --filter none

Writes cache/{scn}/{branch}/{k}/{physics.json, zone_arrivals.npz, arrival_q.bin, perimeter.geojson,
isochrones.geojson, bundle.json}, index.json and checkpoints/. By default it awaits Claude for
each cycle's top new alert and caches validated drafts in cache/{scn}/briefings/ (`--no-briefings` skips).
Needs `emberline.hazard` (A0); fails with a clear message without it.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import Any

from emberline.data.config import scenario_dir
from emberline.runtime import cache
from emberline.runtime.clock import FastClock
from emberline.runtime.loop import Loop, NcForcing, Scenario, load_scenario_dir
from emberline.runtime.model_host import (
    ModelHost,
    hazard_api,
    hazard_version,
    ignition,
    landscape,
    make_model,
    model_config,
)
from emberline.sync.cache_sink import CacheSink


def make_host(ctx: Scenario, make: Any, land: Any, forcing: Any, root: Path, filter_mode: str | None) -> ModelHost:
    return ModelHost(make, land, model_config(ctx, filter_mode), ignition(ctx), forcing, root, ctx.scenario_id,
                     ctx.t_ref_ms, ctx.k0_ms, ctx.cycle_ms)


async def precompute(ctx: Scenario, source: Any, forcing: Any, make: Any, land: Any, root: Path,
                     branch: str = "main", filter_mode: str | None = None, model_version: str | None = None,
                     briefings: bool = False) -> Loop:
    """Run every cycle of the replay through FastClock + CacheSink. Returns the finished loop."""
    host = make_host(ctx, make, land, forcing, root, filter_mode)
    loop = Loop(ctx, CacheSink(root, ctx.zones), FastClock(), source, host, root, branch,
                model_version or hazard_version(), filter_mode, "await" if briefings else "off", stop_at_end=True)
    await loop.run()
    return loop


def main() -> None:
    ap = argparse.ArgumentParser(description="Precompute one branch of the physics cache.")
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--filter", choices=["pf", "reinit", "none"], default=None, help="default: scenario.yaml")
    ap.add_argument("--model", choices=["mtt", "fake"], default="mtt")
    ap.add_argument("--no-briefings", action="store_true",
                    help="skip awaiting one Claude briefing per cycle with a new alert (spec/03 §5 default: await)")
    ap.add_argument("--cache-root", type=Path, default=cache.DEFAULT_ROOT)
    a = ap.parse_args()

    hazard_api()  # clear SystemExit if A0 has not landed
    from emberline.runtime.sources import ReplaySource

    scn_dir = scenario_dir(a.scenario)
    ctx = load_scenario_dir(scn_dir)
    source = ReplaySource(scn_dir, ctx.scenario_id, ctx.cfg["assimilate_sensors"])
    forcing = NcForcing(scn_dir / "forcing", ctx.t_ref_ms)
    t0 = time.perf_counter()
    loop = asyncio.run(precompute(ctx, source, forcing, make_model(a.model), landscape(ctx, a.model), a.cache_root,
                                  a.branch, a.filter, hazard_version(a.model), briefings=not a.no_briefings))
    sink: CacheSink = loop.sink
    print(f"{ctx.scenario_id}/{a.branch}: {len(sink.written)} bundles, {len(loop.ks)} cycles, "
          f"coverage {loop.status['cache_coverage']:.0%}, restores {loop.host.restored_from}, "
          f"{time.perf_counter() - t0:.1f} s -> {a.cache_root / ctx.scenario_id / a.branch}")


if __name__ == "__main__":
    main()
