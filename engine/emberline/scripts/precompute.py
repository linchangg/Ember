"""Precompute a branch's physics cache (+ bundles for file mode). spec/03 §5.

    uv run python -m emberline.scripts.precompute --scenario eaton --branch main --filter pf
    uv run python -m emberline.scripts.precompute --scenario eaton --branch openloop --filter none
    uv run python -m emberline.scripts.precompute --scenario eaton --branch main --profile dev   # 16 members

Physics is Abby's Rust HazardModel (`hazard.factory.create_hazard_model`, docs/RUST_HAZARD_BACKEND.md)
on her statics (static/dem.tif, static/fbfm40.tif via data.statics.load_landscape) and the HRRR files
in forcing/. `--profile target` (default) is scenario.yaml's n_members, the reported configuration;
`--profile dev` runs 16 members and says `+dev-n16` in every cycle's model_version.

Writes cache/{scn}/{branch}/{k}/{physics.json, zone_arrivals.npz, arrival_q.bin, perimeter.geojson,
isochrones.geojson, bundle.json}, index.json and checkpoints/. By default it awaits Claude for
each cycle's top new alert and caches validated drafts in cache/{scn}/briefings/ (`--no-briefings` skips).
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import Any

from emberline.data.config import scenario_dir
from emberline.hazard.bridge import BackendOptions
from emberline.runtime import cache, model_host
from emberline.runtime.clock import FastClock
from emberline.runtime.loop import Loop, NcForcing, Scenario, load_scenario_dir
from emberline.runtime.model_host import PROFILES, landscape, make_host, model_grid, rust_backend
from emberline.sync.cache_sink import CacheSink


async def precompute(ctx: Scenario, source: Any, forcing: Any, make: Any, land: Any, root: Path,
                     branch: str = "main", filter_mode: str | None = None, model_version: str | None = None,
                     briefings: bool = False, members_profile: str = "target", identity: dict | None = None) -> Loop:
    """Run every cycle of the replay through FastClock + CacheSink. Returns the finished loop."""
    host = make_host(ctx, make, land, forcing, root, filter_mode, members_profile, identity)
    loop = Loop(ctx, CacheSink(root, ctx.zones), FastClock(), source, host, root, branch,
                model_version or model_host.model_version(members_profile), filter_mode,
                "await" if briefings else "off", stop_at_end=True)
    await loop.run()
    return loop


def main() -> None:
    ap = argparse.ArgumentParser(description="Precompute one branch of the physics cache.")
    ap.add_argument("--scenario", default="eaton")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--filter", choices=["pf", "reinit", "none"], default=None, help="default: scenario.yaml")
    ap.add_argument("--profile", "--members-profile", dest="members_profile", choices=PROFILES, default="target",
                    help="target = scenario.yaml n_members (reported run); dev = 16 members, labelled +dev-n16")
    ap.add_argument("--workers", type=int, default=2, help="Rust worker threads (BackendOptions)")
    ap.add_argument("--timeout-s", type=float, default=120.0, help="per Rust forecast (BackendOptions)")
    # fire-bridge silently caps workers at (budget - arrivals) / (cells * (640 + 56 * weather frames)) bytes,
    # ~0.2-0.4 GB per worker on Eaton, so the 1 GB default runs only 2-4 workers whatever --workers says.
    ap.add_argument("--memory-gb", type=float, default=1.0, help="Rust memory budget (BackendOptions)")
    ap.add_argument("--no-briefings", action="store_true",
                    help="skip awaiting one Claude briefing per cycle with a new alert (spec/03 §5 default: await)")
    ap.add_argument("--cache-root", type=Path, default=cache.DEFAULT_ROOT)
    a = ap.parse_args()

    from emberline.runtime.sources import ReplaySource

    scn_dir = scenario_dir(a.scenario)
    ctx = load_scenario_dir(scn_dir)
    land = landscape(ctx)  # stops naming Abby's missing statics; never a fabricated landscape
    source = ReplaySource(scn_dir, ctx.scenario_id, ctx.cfg["assimilate_sensors"])
    forcing = NcForcing(scn_dir / "forcing", model_grid(ctx))
    make, identity = rust_backend(BackendOptions(workers=a.workers, timeout_s=a.timeout_s,
                                                 memory_budget_bytes=int(a.memory_gb * 1024**3)))
    t0 = time.perf_counter()
    loop = asyncio.run(precompute(ctx, source, forcing, make, land, a.cache_root, a.branch, a.filter,
                                  briefings=not a.no_briefings, members_profile=a.members_profile, identity=identity))
    sink: CacheSink = loop.sink
    print(f"{ctx.scenario_id}/{a.branch} [{loop.model_version}]: {len(sink.written)} bundles, {len(loop.ks)} cycles, "
          f"coverage {loop.status['cache_coverage']:.0%}, restores {loop.host.restored_from}, "
          f"{time.perf_counter() - t0:.1f} s -> {a.cache_root / ctx.scenario_id / a.branch}")


if __name__ == "__main__":
    main()
