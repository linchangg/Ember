"""Validate committed Eaton inputs and one real-data Rust PF forecast.

Run from repo root: PYTHONPATH=engine python scripts/validation/eaton_data_smoke.py
This is a two-member integration check, not historical skill or target performance.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np

from ember.data.config import scenario_dir
from ember.hazard.bridge import BackendOptions
from ember.runtime.loop import NcForcing, load_scenario_dir
from ember.runtime.model_host import ignition, landscape, model_config, model_grid, rust_backend
from ember.runtime.sources import ReplaySource


def main():
    ctx = load_scenario_dir(scenario_dir('eaton'))
    forcing_dir = ctx.scn_dir / 'forcing'
    receipts = (forcing_dir / 'forcing.sha256').read_text().splitlines()
    for line in receipts:
        digest, name = line.split(maxsplit=1)
        path = forcing_dir / name.lstrip('*')
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, path
    forcing = NcForcing(forcing_dir, model_grid(ctx))
    cycles = ctx.cycles()
    for k in cycles:
        w = forcing.weather_as_of(k)
        assert w.available_ms <= k
        assert np.isfinite(w.weather.u10).all()
    source = ReplaySource(ctx.scn_dir, ctx.scenario_id, ctx.cfg['assimilate_sensors'])
    # First actual delivered evidence after ignition; no synthetic observations.
    for k in cycles:
        if k < ctx.t_ign_ms:
            continue
        obs, _ = source.collect(ctx.k0_ms - ctx.cycle_ms, k)
        if any(o.observed_sim_ms >= ctx.t_ign_ms for o in obs):
            break
    assert obs, 'No delivered observations found'
    assert all(o.available_sim_ms <= k for o in obs)
    land = landscape(ctx)
    cfg = replace(model_config(ctx), n_members=2)
    make, identity = rust_backend(BackendOptions(workers=2, timeout_s=600, memory_budget_bytes=2 * 1024**3))
    model = make()
    model.init(land, forcing.weather_as_of(k).weather, cfg, ignition(ctx))
    result = model.step((k - ctx.t_ref_ms) / 60000, obs)
    assert result.t_q.shape == (5, land.grid.H, land.grid.W)
    assert np.isfinite(result.weights).all() and np.isclose(result.weights.sum(), 1)
    assert np.isfinite(result.p_burned_now).all()
    assert ((result.p_burned_now >= 0) & (result.p_burned_now <= 1)).all()
    used = sum(bool(o['used']) for o in result.obs_used)
    assert used > 0, result.obs_used
    print(json.dumps(dict(scenario='eaton', synthetic=False, members=2,
        forcing_files_verified=len(receipts), causal_cycles_checked=len(cycles),
        knowledge_sim_ms=k, observations_received=len(obs), observations_used=used,
        forecast_duration_ms=result.duration_ms, ess=result.ess,
        physics_sha256=identity['physics_sha256'],
        limits=['Two-member smoke check only', 'No independent perimeter truth or accuracy score']), indent=2))


if __name__ == '__main__':
    main()
