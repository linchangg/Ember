"""Repeated sequential forecasts with sampled simultaneous process-tree RSS.

The first measurement is cold model propagation; later measurements reuse the
Python model but the current native bridge still replays. RSS sampling is a lower
bound, not a kernel-recorded exact peak. No performance claim follows from a pilot.
"""

from __future__ import annotations
import argparse
import json
import os
import platform
import subprocess
import threading
import time
from pathlib import Path
import numpy as np
from .api import Grid, Landscape, Weather, ModelConfig, Ignition
from .bridge import BackendOptions
from .model import RustHazardModel


def summarize(durations):
    a = np.asarray(durations, dtype=float)
    if a.ndim != 1 or len(a) == 0 or not np.isfinite(a).all() or (a < 0).any():
        raise ValueError("Expected nonnegative finite measurement list")
    return dict(
        n=len(a),
        p50_ms=float(np.percentile(a, 50)),
        p95_ms=float(np.percentile(a, 95)),
        min_ms=float(a.min()),
        max_ms=float(a.max()),
    )


def tree_rss(rows, root_pid):
    """Sum RSS at ONE process-table sample, traversing all descendants."""
    selected = {root_pid}
    while True:
        extra = {pid for pid, ppid, _ in rows if ppid in selected}
        if extra.issubset(selected):
            break
        selected.update(extra)
    return sum(rss for pid, _, rss in rows if pid in selected) * 1024


class MemorySampler:
    def __init__(self, interval=0.2):
        self.stop = threading.Event()
        self.interval = interval
        self.peak = None
        self.samples = 0
        self.error = None

    def __enter__(self):
        def sample():
            while not self.stop.is_set():
                try:
                    raw = subprocess.check_output(
                        ["ps", "-axo", "pid=,ppid=,rss="], text=True, timeout=3
                    )
                    rows = [
                        tuple(map(int, line.split()))
                        for line in raw.splitlines()
                        if line.strip()
                    ]
                    rss = tree_rss(rows, os.getpid())
                    self.peak = max(self.peak or 0, rss)
                    self.samples += 1
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    self.error = type(exc).__name__
                    break
                self.stop.wait(self.interval)

        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=4)


def synthetic(members, size, seed):
    g = Grid("EPSG:32611", 380000, 3803000, 90, size, size, 1736294400)
    shape = (size, size)
    zero = np.zeros(shape, np.float32)
    land = Landscape(g, np.full(shape, 102, np.int16), zero, zero.copy(), zero.copy())
    wx = Weather(
        np.array([0, 600], np.float32),
        np.full((2, *shape), 6, np.float32),
        np.zeros((2, *shape), np.float32),
        np.full(2, 22, np.float32),
        np.full(2, 0.25, np.float32),
    )
    cfg = ModelConfig(n_members=members, spotting=False, seed=seed, filter="pf")
    ign = Ignition(0, np.array([[g.x0 + size * 90 * 0.4, g.y0 - size * 90 * 0.5]]), 300)
    return land, wx, cfg, ign


def run(members=128, size=340, workers=2, samples=10, warmup=1, seed=7):
    if samples < 1 or warmup < 0:
        raise ValueError("Invalid sample/warmup counts")
    model = RustHazardModel(
        BackendOptions(workers=workers, memory_budget_bytes=2 * 1024**3, timeout_s=900)
    )
    values = synthetic(members, size, seed)
    started = time.perf_counter()
    model.init(*values)
    init_s = time.perf_counter() - started
    rows = []
    failure = None
    with MemorySampler() as memory:
        for index in range(warmup + samples):
            issue = (index + 1) * 5
            before = time.perf_counter()
            try:
                result = model.step(issue, [])
            except Exception as exc:
                failure = dict(
                    issue_min=issue,
                    wall_ms=(time.perf_counter() - before) * 1000,
                    error_type=type(exc).__name__,
                    message=str(exc)[:200],
                )
                break
            elapsed = (time.perf_counter() - before) * 1000
            row = dict(
                issue_min=issue,
                wall_ms=elapsed,
                reported_ms=result.duration_ms,
                warmup=index < warmup,
                cold=index == 0,
                ess=result.ess,
                resampled=result.resampled,
                native_steps=model.native_diagnostics["steps"],
            )
            rows.append(row)
            print(json.dumps(row), flush=True)
    measured = [r["wall_ms"] for r in rows if not r["warmup"]]
    return dict(
        synthetic=True,
        workload="homogeneous-no-observations",
        members=members,
        size=size,
        workers=workers,
        seed=seed,
        initialization_s=init_s,
        platform=platform.platform(),
        physics_sha256=model.backend_identity["physics_sha256"],
        raw=rows,
        status="failed" if failure else "completed",
        failure=failure,
        requested_samples=samples,
        completed_samples=len(measured),
        summary=summarize(measured) if measured else None,
        sampled_process_tree_peak_bytes=memory.peak,
        memory_samples=memory.samples,
        memory_sampling_error=memory.error,
        target_ms=500,
        target_met=bool(
            failure is None
            and len(measured) >= 10
            and np.percentile(measured, 95) <= 500
        ),
        limits=[
            "Sampled RSS is a lower bound and includes sampler overhead",
            "Python model reuse does not imply incremental native state",
            "No observations, resampling stress or historical accuracy assessment",
            "Machine may be contended; record concurrent workload before comparison",
        ],
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name, default in [
        ("members", 128),
        ("size", 340),
        ("workers", 2),
        ("samples", 10),
        ("warmup", 1),
        ("seed", 7),
    ]:
        p.add_argument("--" + name, type=int, default=default)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise FileExistsError(a.out)
    result = run(a.members, a.size, a.workers, a.samples, a.warmup, a.seed)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("x") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
