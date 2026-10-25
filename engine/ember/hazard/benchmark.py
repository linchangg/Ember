"""Measure the actual configured adapter workload; never assert a latency target."""

from __future__ import annotations
import argparse
import os
import platform
import resource
import time
from pathlib import Path
import numpy as np
from .api import Grid, Landscape, Weather, ModelConfig, Ignition
from .bridge import BackendOptions
from .export import write_json
from .model import RustHazardModel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--members", type=int, default=128)
    parser.add_argument("--size", type=int, default=340)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    g = Grid("EPSG:32611", 380000, 3803000, 90, args.size, args.size, 1736294400)
    shape = (g.H, g.W)
    zero = np.zeros(shape, np.float32)
    land = Landscape(g, np.full(shape, 102, np.int16), zero, zero.copy(), zero.copy())
    weather = Weather(
        np.array([0, 600], np.float32),
        np.full((2, *shape), 6, np.float32),
        np.zeros((2, *shape), np.float32),
        np.full(2, 22, np.float32),
        np.full(2, 0.25, np.float32),
    )
    cfg = ModelConfig(n_members=args.members, spotting=False, filter="pf")
    ign = Ignition(0, np.array([[g.x0 + g.W * 90 * 0.4, g.y0 - g.H * 90 * 0.5]]), 300)
    model = RustHazardModel(
        BackendOptions(
            workers=args.workers, memory_budget_bytes=2 * 1024**3, timeout_s=600
        )
    )
    started = time.perf_counter()
    model.init(land, weather, cfg, ign)
    initialization = time.perf_counter() - started
    result = model.step(5, [])
    # Darwin reports bytes; Linux reports KiB. These are individual process peaks,
    # not a fabricated simultaneous process-tree total.
    unit = 1 if platform.system() == "Darwin" else 1024
    report = {
        "synthetic": True,
        "model_version": model.model_version,
        "physics_sha256": model.backend_identity["physics_sha256"],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "members": args.members,
        "grid": [g.H, g.W],
        "cell_m": 90,
        "horizon_min": 360,
        "workers": args.workers,
        "initialization_s": initialization,
        "forecast_duration_ms": result.duration_ms,
        "target_ms": 500,
        "latency_target_met": bool(result.duration_ms <= 500),
        "python_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * unit,
        "child_peak_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        * unit,
        "native_steps": model.native_diagnostics["steps"],
        "limits": [
            "Single cold forecast, not a p95 latency benchmark",
            "No observations or reanchoring; assimilation can require another solve",
            "Entire-domain replay, not incremental member advancement",
        ],
    }
    write_json(args.out, report)
    print(report, flush=True)


if __name__ == "__main__":
    main()
