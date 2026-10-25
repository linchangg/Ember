"""Owns the HazardModel instance: init, checkpoints, restore-and-roll-forward. spec/03 §4.

`emberline.hazard.api` (Abby, A0) is imported lazily, so this package imports without it.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from emberline.contracts import ObservationRecord
from emberline.data import grid as gridlib
from emberline.runtime import cache

if TYPE_CHECKING:
    from emberline.runtime.loop import Scenario

CHECKPOINT_MS = 30 * 60_000
NON_BURNABLE = (92, 93, 98, 99)
HAZARD_MISSING = (
    "emberline.hazard.api is missing: real physics waits on Abby's A0 (engine/emberline/hazard/). "
    "Until it lands, run `python -m emberline.run --fixtures` to replay the committed fixture."
)


def hazard_api() -> Any:
    """The HazardModel module, or SystemExit with a clear message (outside tests)."""
    try:
        return importlib.import_module("emberline.hazard.api")
    except ImportError as e:
        raise SystemExit(HAZARD_MISSING) from e


def hazard_version(model_name: str = "mtt") -> str:
    """The package's MODEL_VERSION; '+fake' for the fake model so its cache keys and labels never pass as real."""
    api = hazard_api()
    pkg = importlib.import_module("emberline.hazard")
    v = str(getattr(api, "MODEL_VERSION", None) or getattr(pkg, "MODEL_VERSION", None) or "unknown")
    return v + ("+fake" if model_name == "fake" else "")


def make_model(name: str) -> Callable[[], Any]:
    """'mtt' = Abby's EmberlineHazardModel, 'fake' = her FakeHazardModel (module names from spec/02 §0)."""
    hazard_api()
    mod, cls = {"mtt": ("emberline.hazard.model", "EmberlineHazardModel"),
                "fake": ("emberline.hazard.fake", "FakeHazardModel")}[name]
    try:
        return getattr(importlib.import_module(mod), cls)
    except (ImportError, AttributeError) as e:
        raise SystemExit(f"{mod}.{cls} is missing ({e}); check Abby's hazard package.") from e


def model_config(ctx: Scenario, filter_mode: str | None = None) -> Any:
    c = ctx.cfg
    return hazard_api().ModelConfig(
        n_members=int(c.get("n_members", 128)), horizon_min=int(c.get("horizon_min", 360)),
        out_step_min=int(c.get("step_min", 30)), cycle_min=int(c.get("cycle_min", 5)),
        filter=filter_mode or c.get("filter", "pf"), seed=int(c.get("seed", 7)),
        quantiles=tuple(c.get("quantiles", (0.1, 0.3, 0.5, 0.7, 0.9))),
    )


def model_grid(ctx: Scenario) -> Any:
    g = ctx.grid
    return hazard_api().Grid(crs=g["crs"], x0=float(g["x0"]), y0=float(g["y0"]), cell_m=float(g["cell_m"]),
                             H=int(g["ny"]), W=int(g["nx"]), t_ref_s=ctx.t_ref_ms // 1000)


def ignition(ctx: Scenario) -> Any:
    ign = ctx.cfg["ignition"]
    x, y = gridlib.lonlat_to_xy(ign["lon"], ign["lat"], ctx.grid)
    return hazard_api().Ignition(t_min=(ctx.t_ign_ms - ctx.t_ref_ms) / 60_000,
                                 points_xy=np.array([[x, y]], dtype=np.float64), radius_m=float(ign.get("radius_m", 300)))


def landscape(ctx: Scenario, model_name: str) -> Any:
    """Abby's loader if it exists; a flat, all-burnable landscape only for the fake model."""
    for mod in ("emberline.data.statics", "emberline.data"):
        fn = getattr(_try_import(mod), "load_landscape", None)
        if fn is not None:
            return fn(ctx.scenario_id)
    if model_name != "fake":
        raise SystemExit("load_landscape (spec/01 §6) is missing; only --model fake can run on a flat landscape.")
    return flat_landscape(ctx)


def flat_landscape(ctx: Scenario) -> Any:
    api, g = hazard_api(), ctx.grid
    shape = (int(g["ny"]), int(g["nx"]))
    zeros = np.zeros(shape, dtype=np.float32)
    # ponytail: flat stand-in landscape for the fake model only; never used for reported numbers.
    return api.Landscape(grid=model_grid(ctx), fuel=np.full(shape, 102, dtype=np.int16), slope=zeros,
                         aspect=zeros.copy(), elev=zeros.copy())


def _try_import(mod: str) -> Any:
    try:
        return importlib.import_module(mod)
    except ImportError:
        return None


def burnable(land: Any) -> np.ndarray:
    return ~np.isin(np.asarray(land.fuel), NON_BURNABLE)


class ModelHost:
    """In-memory model at `at_ms` on `branch`; checkpoints every 30 sim-min from the first cycle `k0_ms`."""

    def __init__(self, make: Callable[[], Any], land: Any, cfg: Any, ign: Any, forcing: Any,
                 root: Path, scenario_id: str, t_ref_ms: int, k0_ms: int, cycle_ms: int) -> None:
        self.make, self.land, self.cfg, self.ign, self.forcing = make, land, cfg, ign, forcing
        self.root, self.scenario_id = Path(root), scenario_id
        self.t_ref_ms, self.k0_ms, self.cycle_ms = t_ref_ms, k0_ms, cycle_ms
        self.model: Any = None
        self.at_ms: int | None = None
        self.branch: str | None = None
        self.weather_key: tuple[int, int] | None = None
        self.restored_from: list[int] = []  # checkpoint ks used by ensure_state (tests, /healthz)

    def weather(self, k_ms: int) -> Any:
        w = self.forcing.weather_as_of(k_ms)
        assert w.available_ms <= k_ms, f"future data: weather available {w.available_ms} > k {k_ms}"
        return w

    def _fresh(self) -> None:
        w = self.weather(self.k0_ms)
        self.model = self.make()
        self.model.init(self.land, w.weather, self.cfg, self.ign)
        self.weather_key = (w.init_ms, w.available_ms)

    def _set_weather(self, k_ms: int) -> None:
        w = self.weather(k_ms)
        if (w.init_ms, w.available_ms) != self.weather_key:
            self.model.update_weather(w.weather)
            self.weather_key = (w.init_ms, w.available_ms)

    def step(self, branch: str, k_ms: int, new_obs: list[ObservationRecord]) -> Any:
        assert self.model is not None and self.at_ms == k_ms - self.cycle_ms and self.branch == branch, (
            f"model at {self.at_ms} on {self.branch}, asked to step {branch} at {k_ms}")
        for o in new_obs:
            assert o.available_sim_ms <= k_ms, f"future data: {o.obs_id} available {o.available_sim_ms} > k {k_ms}"
        self._set_weather(k_ms)
        arrays = self.model.step((k_ms - self.t_ref_ms) / 60_000, new_obs)
        self.at_ms = k_ms
        if (k_ms - self.k0_ms) % CHECKPOINT_MS == 0:
            cache.save_checkpoint(self.root, self.scenario_id, branch, k_ms, self.model.save_state())
        return arrays

    def ensure_state(self, branch: str, k_prev_ms: int, obs_for: Callable[[int], list[ObservationRecord]]) -> None:
        """Bring the model to k_prev: in memory, else the newest checkpoint <= k_prev, else ignition; then roll forward."""
        if self.model is not None and self.branch == branch and self.at_ms == k_prev_ms:
            return
        ck = cache.latest_checkpoint(self.root, self.scenario_id, branch, k_prev_ms)
        ck = ck if ck is not None and ck[0] >= self.k0_ms else None
        in_memory = self.model is not None and self.branch == branch and self.at_ms is not None and self.at_ms < k_prev_ms
        if in_memory and (ck is None or self.at_ms >= ck[0]):
            at = self.at_ms
        elif ck is not None:
            self._fresh()
            self.model.load_state(ck[1])
            at = ck[0]
            self.weather_key = None
            self._set_weather(at)
            self.restored_from.append(at)
        else:
            self._fresh()
            at = self.k0_ms - self.cycle_ms
        self.branch, self.at_ms = branch, at
        for k in range(at + self.cycle_ms, k_prev_ms + 1, self.cycle_ms):
            self.step(branch, k, obs_for(k))
