"""Owns the HazardModel instance: init, checkpoints, restore-and-roll-forward. spec/03 §4.

The real model is Abby's Rust backend, created only through
`hazard.factory.create_hazard_model(backend="rust")` (docs/RUST_HAZARD_BACKEND.md).
Member profiles: "target" = scenario.yaml `n_members` (128, the reported configuration);
"dev" = 16 members, labelled in `model_version` so it can never pass as the reported run.
"""

from __future__ import annotations

import functools
import hashlib
import importlib
import importlib.util
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from emberline.contracts import ObservationRecord
from emberline.data import grid as gridlib
from emberline.hazard import api, bridge, checkpoint, factory
from emberline.runtime import cache

if TYPE_CHECKING:
    from emberline.runtime.loop import Scenario

log = logging.getLogger(__name__)
CHECKPOINT_MS = 30 * 60_000
NON_BURNABLE = (0, 92, 93, 98, 99)  # hazard/bridge.py's list
PROFILES = ("target", "dev")
DEV_MEMBERS = 16
STATICS = ("dem.tif", "fbfm40.tif")  # spec/01 §0, §6: Abby's data/statics.py writes them


def model_version(members_profile: str = "target") -> str:
    """Cycle.model_version: the backend's MODEL_VERSION, with the dev profile named in it."""
    assert members_profile in PROFILES, members_profile
    return api.MODEL_VERSION + (f"+dev-n{DEV_MEMBERS}" if members_profile == "dev" else "")


def rust_backend(options: bridge.BackendOptions | None = None) -> tuple[Callable[[], Any], dict]:
    """The model factory and its identity for the cache key: everything Abby's checkpoint.load binds to
    (Rust + Python source, fuel catalog, environment), so a cached cycle never outlives its checkpoints."""
    make = functools.partial(factory.create_hazard_model, backend="rust", options=options)
    identity = {"model_version": api.MODEL_VERSION, "physics_sha256": bridge.physics_fingerprint(),
                "python_sha256": bridge.python_fingerprint(),
                "catalog_sha256": hashlib.sha256(bridge.catalog_text().encode()).hexdigest(),
                "environment": checkpoint.environment()}
    return make, identity


def model_config(ctx: Scenario, filter_mode: str | None = None, members_profile: str = "target") -> api.ModelConfig:
    c = ctx.cfg
    assert members_profile in PROFILES, members_profile
    return api.ModelConfig(
        n_members=DEV_MEMBERS if members_profile == "dev" else int(c.get("n_members", 128)),
        horizon_min=int(c.get("horizon_min", 360)), out_step_min=int(c.get("step_min", 30)),
        cycle_min=int(c.get("cycle_min", 5)), filter=filter_mode or c.get("filter", "pf"), seed=int(c.get("seed", 7)),
        spotting=False,  # the Rust surface backend has no spotting model and rejects spotting=True
        quantiles=tuple(c.get("quantiles", (0.1, 0.3, 0.5, 0.7, 0.9))),
    )


def model_grid(ctx: Scenario) -> api.Grid:
    g = ctx.grid
    return api.Grid(crs=g["crs"], x0=float(g["x0"]), y0=float(g["y0"]), cell_m=float(g["cell_m"]),
                    H=int(g["ny"]), W=int(g["nx"]), t_ref_s=ctx.t_ref_ms // 1000)


def ignition(ctx: Scenario) -> api.Ignition:
    ign = ctx.cfg["ignition"]
    x, y = gridlib.lonlat_to_xy(ign["lon"], ign["lat"], ctx.grid)
    return api.Ignition(t_min=(ctx.t_ign_ms - ctx.t_ref_ms) / 60_000,
                        points_xy=np.array([[x, y]], dtype=np.float64), radius_m=float(ign.get("radius_m", 300)))


def landscape(ctx: Scenario) -> api.Landscape:
    """Abby's load_landscape over static/*.tif. Stops naming whatever is missing; never fabricates a landscape."""
    missing = [f"static/{n}" for n in STATICS if not (ctx.scn_dir / "static" / n).exists()]
    statics = importlib.import_module("emberline.data.statics") if importlib.util.find_spec("emberline.data.statics") else None
    if getattr(statics, "load_landscape", None) is None:
        missing.append("emberline.data.statics.load_landscape")
    if missing:
        raise SystemExit(f"{ctx.scenario_id}: real physics needs Abby's statics (spec/01 §6); missing "
                         f"{', '.join(missing)} (scenario dir {ctx.scn_dir}). No landscape is fabricated.")
    land = statics.load_landscape(ctx.scenario_id)
    assert land.grid == model_grid(ctx), f"landscape grid {land.grid} != scenario.yaml grid {model_grid(ctx)}"
    return land


def make_host(ctx: Scenario, make: Callable[[], Any], land: Any, forcing: Any, root: Path,
              filter_mode: str | None = None, members_profile: str = "target", identity: dict | None = None) -> ModelHost:
    return ModelHost(make, land, model_config(ctx, filter_mode, members_profile), ignition(ctx), forcing, root,
                     ctx.scenario_id, ctx.t_ref_ms, ctx.k0_ms, ctx.cycle_ms, identity)


def burnable(land: Any) -> np.ndarray:
    return ~np.isin(np.asarray(land.fuel), NON_BURNABLE)


class ModelHost:
    """In-memory model at `at_ms` on `branch`; checkpoints every 30 sim-min from the first cycle `k0_ms`.

    Each checkpoint is saved under the cycle key of its state; a restore only takes one whose key is
    the current lineage's, so another profile, filter, forcing or evidence chain never leaks in.
    """

    def __init__(self, make: Callable[[], Any], land: Any, cfg: Any, ign: Any, forcing: Any, root: Path,
                 scenario_id: str, t_ref_ms: int, k0_ms: int, cycle_ms: int, identity: dict | None = None) -> None:
        self.make, self.land, self.cfg, self.ign, self.forcing = make, land, cfg, ign, forcing
        self.root, self.scenario_id = Path(root), scenario_id
        self.t_ref_ms, self.k0_ms, self.cycle_ms = t_ref_ms, k0_ms, cycle_ms
        self.identity = identity or {}  # model code identity; part of the cache key's config hash
        self.model: Any = None
        self.at_ms: int | None = None
        self.branch: str | None = None
        self.key: str | None = None  # cycle key of the model's current state
        self.weather_key: tuple | None = None
        self.restored_from: list[int] = []  # checkpoint ks used by ensure_state (tests, /healthz)

    def weather(self, k_ms: int) -> Any:
        w = self.forcing.weather_as_of(k_ms)
        assert w.available_ms <= k_ms, f"future data: weather available {w.available_ms} > k {k_ms}"
        return w

    def _fresh(self) -> None:
        w = self.weather(self.k0_ms)
        self.model = self.make()
        self.model.init(self.land, w.weather, self.cfg, self.ign)
        self.weather_key = w.key

    def _set_weather(self, k_ms: int) -> None:
        w = self.weather(k_ms)
        if w.key != self.weather_key:
            self.model.update_weather(w.weather)
            self.weather_key = w.key

    def step(self, branch: str, k_ms: int, new_obs: list[ObservationRecord], key: str | None = None) -> Any:
        assert self.model is not None and self.at_ms == k_ms - self.cycle_ms and self.branch == branch, (
            f"model at {self.at_ms} on {self.branch}, asked to step {branch} at {k_ms}")
        for o in new_obs:
            assert o.available_sim_ms <= k_ms, f"future data: {o.obs_id} available {o.available_sim_ms} > k {k_ms}"
        self._set_weather(k_ms)
        arrays = self.model.step((k_ms - self.t_ref_ms) / 60_000, new_obs)
        self.at_ms, self.key = k_ms, key
        if (k_ms - self.k0_ms) % CHECKPOINT_MS == 0:
            cache.save_checkpoint(self.root, self.scenario_id, branch, k_ms, self.model.save_state(), key)
        return arrays

    def _restore(self, k_ms: int, blob: bytes) -> int:
        """load_state at k; returns k, or the pre-ignition cycle if the backend rejects the checkpoint."""
        self.model = self.make()  # load_state is self-contained: no init (docs/RUST_HAZARD_BACKEND.md)
        try:
            self.model.load_state(blob)
        except ValueError as e:  # another build/platform or a damaged file; Abby's load is atomic
            log.warning("checkpoint %s rejected (%s); replaying from ignition", k_ms, e)
            self._fresh()
            return self.k0_ms - self.cycle_ms
        # The checkpoint already holds the weather applied at k; re-sending it would add a weather change.
        self.weather_key = self.weather(k_ms).key
        self.restored_from.append(k_ms)
        return k_ms

    def ensure_state(self, branch: str, k_prev_ms: int, obs_for: Callable[[int], list[ObservationRecord]],
                     key_for: Callable[[int], str | None]) -> None:
        """Bring the model to k_prev: in memory, else the newest same-lineage checkpoint <= k_prev, else ignition;
        then roll forward with obs_for(k) under key_for(k)."""
        same = self.model is not None and self.branch == branch and self.at_ms is not None \
            and self.key == key_for(self.at_ms)
        if same and self.at_ms == k_prev_ms:
            return
        ck = cache.latest_checkpoint(self.root, self.scenario_id, branch, k_prev_ms, key_for)
        ck = ck if ck is not None and ck[0] >= self.k0_ms else None
        if same and self.at_ms < k_prev_ms and (ck is None or self.at_ms >= ck[0]):
            at = self.at_ms
        elif ck is not None:
            at = self._restore(*ck)
        else:
            self._fresh()
            at = self.k0_ms - self.cycle_ms
        self.branch, self.at_ms, self.key = branch, at, key_for(at)  # no key before the first physics cycle
        for k in range(at + self.cycle_ms, k_prev_ms + 1, self.cycle_ms):
            self.step(branch, k, obs_for(k), key_for(k))
