"""Export matched raw member forecasts through the existing causal orchestrator.

Outputs are predictions, not truth. Use a fresh output root; cached quantile files
cannot be converted into exact member probabilities. No shared contract changes.
"""

from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import numpy as np

from emberline.data.config import scenario_dir
from emberline.runtime.loop import NcForcing, load_scenario_dir
from emberline.runtime.model_host import landscape, model_grid, rust_backend
from emberline.runtime.sources import ReplaySource
from emberline.scripts.precompute import precompute
from .bridge import BackendOptions
from .historical import Grid, digest, write_prediction


class ForecastArchive:
    def __init__(self, root: Path, issues: set[int]):
        self.root, self.issues = root, set(issues)
        self.written = []

    def __call__(self, k, host, forecast, identity):
        if k not in self.issues:
            return
        g = host.land.grid
        grid = Grid(nx=g.W, ny=g.H, cell_m=g.cell_m, x0=g.x0, y0=g.y0, crs=g.crs)
        out = self.root / str(k)
        out.mkdir(parents=True, exist_ok=False)
        write_prediction(
            out / "prediction.npz",
            host.model._arrivals.copy(),
            forecast.weights.copy(),
            grid=grid,
            issue_sim_ms=k,
            t_ref_sim_ms=g.t_ref_s * 1000,
            horizon_min=host.cfg.horizon_min,
        )
        # Preserve the complete consumed evidence chain, not only the model's
        # rolling history (older evidence can still influence posterior parameters).
        evidence = identity.get("observations_by_issue")
        records = (
            [
                rec.model_dump(mode="json")
                for issue, batch in sorted(evidence.items())
                for rec in batch
            ]
            if evidence is not None
            else [rec.model_dump(mode="json") for rec in host.model._history]
        )
        if any(r["available_sim_ms"] > k for r in records):
            raise ValueError("Future evidence in raw-member archive")
        (out / "observations.json").write_text(
            json.dumps(records, sort_keys=True) + "\n"
        )
        wx = host.forcing.weather_as_of(k)
        metadata = dict(
            issue_sim_ms=k,
            config=asdict(host.cfg),
            identity={
                key: identity[key]
                for key in ("cycle_key", "run_hash")
                if key in identity
            },
            forcing_history=identity.get("forcing_by_issue", {}),
            backend=host.identity,
            forcing=wx.audit,
            grid=grid.model_dump(),
            prediction_sha256=digest(out / "prediction.npz"),
            observations_sha256=digest(out / "observations.json"),
            runtime_ms=forecast.duration_ms,
            weights_sum=float(np.sum(forecast.weights)),
            seed=host.cfg.seed,
            model_version=host.model.model_version,
            scientific_acceptance="not_established",
        )
        (out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        self.written.append(k)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenario", default="eaton")
    p.add_argument("--filter", choices=["none", "reinit", "pf"], required=True)
    p.add_argument("--profile", choices=["dev", "target"], default="dev")
    p.add_argument("--issue-ms", type=int, nargs="+", required=True)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise FileExistsError(a.out)
    ctx = load_scenario_dir(scenario_dir(a.scenario))
    issues = set(a.issue_ms)
    if not issues or not issues.issubset(set(ctx.cycles())) or min(issues) < ctx.k0_ms:
        raise ValueError(
            "Requested issues must be post-ignition configured cycle boundaries"
        )
    a.out.mkdir(parents=True)
    archive = ForecastArchive(a.out / "members", issues)
    make, identity = rust_backend(
        BackendOptions(
            workers=a.workers, timeout_s=900, memory_budget_bytes=2 * 1024**3
        )
    )
    n = ctx.cycles().index(max(issues)) + 1
    asyncio.run(
        precompute(
            ctx,
            ReplaySource(
                ctx.scn_dir,
                ctx.scenario_id,
                ctx.cfg["assimilate_sensors"],
                reports=bool(ctx.cfg.get("use_documented_reports")),
            ),
            NcForcing(ctx.scn_dir / "forcing", model_grid(ctx)),
            make,
            landscape(ctx),
            a.out / "cache",
            a.filter,
            a.filter,
            members_profile=a.profile,
            identity=identity,
            max_cycles=n,
            physics_observer=archive,
        )
    )
    if set(archive.written) != issues:
        raise RuntimeError("Raw-member exports incomplete")
    (a.out / "export.json").write_text(
        json.dumps(
            dict(
                issues=sorted(issues),
                profile=a.profile,
                mode=a.filter,
                limit="Prediction archive only; independently frozen sources/truth and manifest still required",
            ),
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
